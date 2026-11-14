"""Calls: a LiveKit room per call, the browser's join token, the dispatch of the worker, and the speech grant.

Call setup, for one user: a new call ends the older one, a
call lasts at most 30 minutes, and only the API can dispatch (it holds the LiveKit secret), so the worker trusts what
the dispatch says. The browser's token only lets it into its own room and only publish its microphone.
"""
import json
import logging
import secrets
import time
import uuid
from datetime import timedelta

from itsdangerous import BadData, URLSafeTimedSerializer

from api import library, settings

logger = logging.getLogger(__name__)

AGENT_NAME = "voice-demo"
ROOM_PREFIX = "demo-"
LANGUAGES = {"en": "English", "nl": "Dutch", "fr": "French", "de": "German"}
# The longest call; the grant lives as long.
MAX_CALL_SECONDS = 30 * 60
SESSION_SECONDS = MAX_CALL_SECONDS
# The browser's token only lets it enter the room: LiveKit checks its expiry when the browser joins and keeps a
# connected browser connected. It is not the length of the call.
JOIN_SECONDS = 10 * 60
# The user and the worker, plus one place while a replacement worker joins after a crash.
MAX_PARTICIPANTS = 3
# A room nobody joins closes after this long, and so does a room everybody has left.
EMPTY_ROOM_SECONDS = 120
DEPARTURE_SECONDS = 30
SPEECH_SALT = "voice-demo-speech"

# The live calls: id -> {"started": epoch seconds}.
_live = {}


class CallError(Exception):
    """No call could be made; the message is safe to show. `status`: the HTTP answer."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def enabled():
    return bool(settings.livekit_url() and settings.livekit_key() and settings.livekit_secret())


def get(call_id):
    """The live call's record, or None. A call that has run out counts as gone."""
    call = _live.get(call_id)
    if call is None:
        return None
    if time.time() - call["started"] > MAX_CALL_SECONDS:
        _live.pop(call_id, None)
        return None
    return call


def _api():
    from livekit import api

    return api.LiveKitAPI(settings.livekit_url(), api_key=settings.livekit_key(), api_secret=settings.livekit_secret())


def _signer():
    return URLSafeTimedSerializer(settings.speech_secret(), salt=SPEECH_SALT)


def speech_grant(language, call_id):
    """Lets the worker of call `call_id` have `language` spoken in the cloned voice, while that call is live."""
    return _signer().dumps({"language": language, "call": call_id})


def read_speech_grant(grant):
    """{"language", "call"} of a valid grant whose call is still live, else None."""
    try:
        found = _signer().loads(grant, max_age=SESSION_SECONDS)
    except (BadData, ValueError, TypeError):
        return None
    return found if get(found.get("call")) else None


def participant_token(room, identity):
    """The browser's pass into `room`. It is a plain JWT anyone can read, so it holds nothing about the person."""
    from livekit import api

    return (api.AccessToken(settings.livekit_key(), settings.livekit_secret())
            .with_identity(identity)
            .with_ttl(timedelta(seconds=JOIN_SECONDS))
            .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True,
                                         can_publish_data=True, can_publish_sources=["microphone"],
                                         can_update_own_metadata=False))
            .to_jwt())


async def create_session(language="en", use_clone=True):
    """A room with the worker in it: {"session_id", "server_url", "participant_token", "company_voice"}.

    `company_voice` is true when the call speaks in a cloned voice: an active clone exists for the language and
    `use_clone` (the page's switch) is on. Otherwise Gemini speaks with its own voice."""
    from livekit import api

    language = (language or "en").split("-")[0].lower()
    if language not in LANGUAGES:
        raise CallError("This language is not supported.", status=400)
    if not enabled():
        raise CallError("Voice is not set up.", status=503)
    # One live call: a new call ends the older one (a tab closed without hanging up).
    for older in list(_live):
        await end_session(older)
    session_id = uuid.uuid4().hex
    # Only promised when the voice will really speak: it is checked at Mistral now (else Gemini speaks from the start).
    grant = speech_grant(language, session_id) if use_clone and await library.verified_voice_id(language) else None
    _live[session_id] = {"started": time.time()}
    room = f"{ROOM_PREFIX}{session_id}"
    # One identity per call, random: LiveKit drops the older connection when a second joins with the same one, and
    # the identity is readable in the token, so it names nobody.
    identity = f"caller-{secrets.token_hex(8)}"
    token = participant_token(room, identity)
    metadata = json.dumps({"call_id": session_id, "language": language, "user": identity,
                           **({"speech": {"grant": grant}} if grant else {})})
    client = _api()
    try:
        # LiveKit's own room settings do the cleaning up: a cap on who can join, and the room closes itself when
        # nobody joins or everybody has left.
        await client.room.create_room(api.CreateRoomRequest(
            name=room, max_participants=MAX_PARTICIPANTS, empty_timeout=EMPTY_ROOM_SECONDS,
            departure_timeout=DEPARTURE_SECONDS))
        await client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room, metadata=metadata))
    except Exception as exc:
        logger.warning("The voice worker could not be dispatched: %s", exc)
        finish_call(session_id)
        raise CallError("The voice call could not start.", status=503) from exc
    finally:
        await client.aclose()
    return {"session_id": session_id, "server_url": settings.livekit_public_url(), "participant_token": token,
            "company_voice": bool(grant)}


def read_webhook(body, authorization):
    """The event LiveKit sent, if the signature in `authorization` is LiveKit's for exactly this `body`, else None."""
    from livekit import api

    if not authorization:
        return None
    try:
        return api.WebhookReceiver(api.TokenVerifier(settings.livekit_key(), settings.livekit_secret())).receive(
            body, authorization)
    except Exception as exc:
        logger.warning("A LiveKit webhook was refused: %s", type(exc).__name__)
        return None


def finished_call(event):
    """The id of the call whose room `event` says is finished, else None (another event, or not one of our rooms)."""
    name = event.room.name
    if event.event != "room_finished" or not name.startswith(ROOM_PREFIX):
        return None
    return name[len(ROOM_PREFIX):]


def finish_call(call_id):
    """The call is over, whatever ended it: it stops counting and its grant stops working."""
    _live.pop(call_id, None)


async def end_session(call_id):
    """Close the call's room, which ends the worker's job; False when there is no such live call."""
    from livekit import api

    if get(call_id) is None:
        return False
    client = _api()
    try:
        await client.room.delete_room(api.DeleteRoomRequest(room=f"{ROOM_PREFIX}{call_id}"))
    except Exception as exc:
        # Gone already when everyone left; the worker ends with the room either way.
        logger.info("Room %s not deleted: %s", call_id, exc)
    finally:
        await client.aclose()
    finish_call(call_id)
    return True
