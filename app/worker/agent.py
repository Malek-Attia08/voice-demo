"""The CareVoice voice worker: Gemini Live hears and thinks, and either speaks itself or has the cloned voice speak.

- Gemini Live is the call's brain: it hears, decides turns and thinks (no STT or VAD of ours). Long calls are
  compressed and a dropped connection can resume.
- When the dispatch carries a speech grant (an active cloned voice exists for the call's language and the page's
  switch is on), Gemini's messages are turned into text-only ones (text_only): LiveKit speaks those with the cloned
  voice, sentence by sentence as the text streams in, and keeps its own turn-taking, interruptions and transcript.
  Gemini's audio is thrown away. Without a grant, Gemini speaks with its own voice.
- Each sentence of the cloned voice is fetched from the API (POST /api/speech) with the call's grant: the Mistral key
  never reaches the worker.
- Events go to the page on a LiveKit data topic ("transcription.segment", "agent.state.changed", "error",
  "call.ended").

The assistant's prompt is worker/prompts.py. It has five tools with hardcoded data (worker/tools.py: rag_answer,
history, medical_info, get_time, find_appointment_slots) that run inside the worker, plus end_call, and stop_speaking when the
cloned voice speaks.

Run it from C:\\voice-demo\\app:

    python -m worker.agent start
"""
import asyncio
import datetime
import json
import logging
import os
import time

from google.genai import types
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, RunContext, cli
from livekit.agents import llm
from livekit.agents.llm import function_tool
# LiveKit plugins must be imported (registered) on the main thread, so here and not inside a call's job thread.
from livekit.plugins import google  # noqa: F401

from worker import tools
from worker.prompts import LANGUAGE_NAMES, greeting, voice_instructions

logger = logging.getLogger("voice-demo")

AGENT_NAME = "voice-demo"
EVENTS_TOPIC = "demo.voice_events"

DEFAULT_GEMINI_MODEL = "gemini-3.8-live"
DEFAULT_GEMINI_VOICE = "Kore"
# google-genai opens its Live WebSocket on a default API version that answers 404; v1beta has the endpoint on the
# Gemini Developer API.
GEMINI_API_VERSION = "v1beta"

# The languages the demo supports, told to the transcription so Gemini does not guess one outside them. The call's
# language is only listed first: the user may speak another one.
TRANSCRIPTION_LOCALES = {"en": "en-US", "nl": "nl-NL", "fr": "fr-FR", "de": "de-DE"}

# How long the job waits for the user to join the room before it gives up and frees its place.
WAIT_FOR_USER_S = 120
# The longest call. LiveKit has no maximum room length of its own, so the job closes the room (the API stops
# counting the call at the same length).
MAX_CALL_S = 30 * 60


def _code(language):
    return (language or "en").split("-")[0].lower()


def call_settings(metadata):
    """The dispatch's language, caller and cloned-voice grant, or None when the call cannot run here."""
    try:
        call = json.loads(metadata or "{}")
    except ValueError:
        return None
    if not isinstance(call, dict):
        return None
    return call.get("language") or "en", call.get("user"), (call.get("speech") or {}).get("grant")


def gemini_options(language):
    """The options the Gemini Live session is built with (livekit's Google RealtimeModel).

    Gemini only hears and speaks: it gets the demo prompt and the tools. Only options that the Gemini Developer API
    and Vertex AI both have are used, so moving between them is a change of settings. The model takes no language
    code, so the prompt names the language."""
    code = _code(language)
    options = {
        "model": os.getenv("GEMINI_LIVE_MODEL") or DEFAULT_GEMINI_MODEL,
        "voice": os.getenv("GEMINI_LIVE_VOICE") or DEFAULT_GEMINI_VOICE,
        "instructions": voice_instructions(language),
        "api_key": os.getenv("GOOGLE_API_KEY"),
        "api_version": GEMINI_API_VERSION,
        # Without compression an audio session ends after about 15 minutes, and a full context cannot be recovered.
        "context_window_compression": types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
        "session_resumption": types.SessionResumptionConfig(),
    }
    if code in TRANSCRIPTION_LOCALES:
        first = TRANSCRIPTION_LOCALES[code]
        options["input_audio_transcription"] = types.AudioTranscriptionConfig(
            language_codes=[first, *[locale for locale in TRANSCRIPTION_LOCALES.values() if locale != first]])
    return options


class Transcript:
    """What was said in this call, for the history tool: [(who, words)], who being "user" or "agent"."""

    def __init__(self):
        self.turns = []

    def on_conversation_item_added(self, event):
        item = event.item
        role = {"user": "user", "assistant": "agent"}.get(getattr(item, "role", None))
        text = (getattr(item, "text_content", None) or "").strip()
        if role and text:
            self.turns.append((role, text))


def demo_tools(transcript, announce=None):
    """The fake tools as Gemini's function tools. `announce(name, arguments, answer)` tells the page about each call."""

    def make(spec):
        async def handler(raw_arguments: dict[str, object], context: RunContext):
            answer = tools.call(spec["name"], raw_arguments, turns=transcript.turns)
            logger.info("tool %s called", spec["name"])
            if announce:
                announce(spec["name"], raw_arguments, answer)
            return answer

        return function_tool(handler, raw_schema=spec)

    return [make(spec) for spec in tools.TOOLS]


END_CALL = {
    "name": "end_call",
    "description": "Hang up the call. Call it only right after you have said goodbye because the user said "
                   "goodbye or clearly wants to stop.",
    "parameters": {"type": "object", "properties": {}},
}


def end_call_tool(ctx):
    """end_call: once the goodbye has been spoken, tell the page the call is over and close the room."""

    async def end_call(raw_arguments: dict[str, object], context: RunContext):
        await context.wait_for_playout()
        logger.info("Call %s: Gemini hung up after the goodbye", ctx.room.name)
        await ctx.room.local_participant.publish_data(
            json.dumps({"type": "call.ended", "reason": "goodbye"}).encode(), reliable=True, topic=EVENTS_TOPIC)
        await ctx.delete_room()
        return "The call has ended."

    return function_tool(end_call, raw_schema=END_CALL)


STOP_SPEAKING = {
    "name": "stop_speaking",
    "description": "Cut off what you are saying to the user right now, for example when you notice you are wrong "
                   "or the user needs something else.",
    "parameters": {"type": "object", "properties": {}},
}


def stop_speaking_tool():
    async def stop_speaking(raw_arguments: dict[str, object], context: RunContext):
        context.session.interrupt()
        return "Stopped."

    return function_tool(stop_speaking, raw_schema=STOP_SPEAKING)


class VoiceMode:
    """Who speaks the call's words right now: the cloned voice (`cloned`) or Gemini itself.

    It is read for every message Gemini announces, so a switch takes effect from the next message."""

    def __init__(self, cloned):
        self.cloned = cloned


# What Gemini is asked when the cloned voice failed: the reply it just gave was never heard.
REPEAT_REPLY = ("The last thing you said could not be played to the user. Say it again now, in your own words and "
                "briefly, and do not mention any problem.")


class VoiceFallback:
    """When the cloned voice fails, Gemini's own voice takes over for the rest of the call.

    The first failure switches the mode, asks Gemini to say the reply again (the one that failed was never heard)
    and tells the page. Later failures do nothing: from then on Gemini's audio is played, not text."""

    def __init__(self, mode, on_switch=None):
        self.mode = mode
        self.on_switch = on_switch
        self.session = None  # set once the session exists

    def on_failure(self):
        if not self.mode.cloned:
            return
        self.mode.cloned = False
        logger.warning("The cloned voice failed: Gemini speaks with its own voice for the rest of the call")
        if self.on_switch:
            self.on_switch()
        if self.session is not None:
            try:
                self.session.generate_reply(instructions=REPEAT_REPLY)
            except Exception as exc:
                logger.warning("Gemini could not be asked to repeat its reply: %s", type(exc).__name__)


def cloned_voice_tts(speech_grant, on_failure=None):
    """The cloned voice as the call's TTS, or None when the call has no grant or the worker does not know the API.

    The cloned voice answers a whole text at once, so it is cut into sentences as Gemini's words arrive and each is
    fetched while the one before it plays. A sentence that cannot be spoken calls `on_failure` (the fallback to
    Gemini's own voice): the worker has no Mistral key, so there is no other cloned or preset voice behind it."""
    from livekit.agents import tts

    from worker.company_voice import CompanyVoiceTTS

    backend = os.getenv("DEMO_API_URL")
    if not speech_grant:
        return None
    if not backend:
        logger.warning("DEMO_API_URL is not set: the cloned voice cannot be used, Gemini's own is")
        return None
    return tts.StreamAdapter(tts=CompanyVoiceTTS(backend, speech_grant, on_failure=on_failure))


async def _drain(frames):
    async for _ in frames:
        pass


async def _timed(text, started):
    """Gemini's text, logging when the first of it arrives (how long the clone waits for it) but not what it says."""
    first = True
    async for chunk in text:
        if first and chunk:
            first = False
            logger.info("Gemini's first words reached the cloned voice after %.2f s", time.monotonic() - started)
        yield chunk


async def text_only(messages, mode=None):
    """Gemini's messages as LiveKit's text-only ones: LiveKit speaks those with the session's TTS, sentence by
    sentence as the text streams in, and keeps its own turn-taking, interruptions and transcript. Gemini's audio is
    read and thrown away (its words come with it as the output transcription).

    When `mode` says Gemini is to speak (the cloned voice failed), the message goes through as it is, audio included."""
    async for message in messages:
        if mode is not None and not mode.cloned:
            yield message
            continue
        modalities = asyncio.get_running_loop().create_future()
        modalities.set_result(["text"])
        task = asyncio.ensure_future(_drain(message.audio_stream))
        task.add_done_callback(lambda done: done.cancelled() or done.exception())
        yield llm.MessageGeneration(message_id=message.message_id,
                                    text_stream=_timed(message.text_stream, time.monotonic()),
                                    audio_stream=message.audio_stream, modalities=modalities)


def text_only_session(base, mode=None):
    """`base` (Gemini's realtime session) with every generation it announces turned into a text-only one (while
    `mode` says the cloned voice speaks)."""

    class TextOnlySession(base):
        def emit(self, event, *args):
            if event == "generation_created":
                args[0].message_stream = text_only(args[0].message_stream, mode)
            return super().emit(event, *args)

    return TextOnlySession


def gemini_realtime_model(options, cloned, mode=None):
    """Gemini Live's realtime model. When the call speaks in the cloned voice, its sessions announce text-only
    generations, so LiveKit reads Gemini's words with the cloned voice instead of playing Gemini's audio."""
    from livekit.plugins import google
    from livekit.plugins.google.realtime import realtime_api

    model = google.realtime.RealtimeModel(**options)
    if cloned:
        session_class = text_only_session(realtime_api.RealtimeSession, mode)

        def session(*, turn_detection_disabled=False):
            sess = session_class(model)
            model._sessions.add(sess)
            return sess

        model.session = session
    return model


def build_session(language, company_tts=None, mode=None):
    """Gemini Live is the call's brain: it hears, decides turns and thinks (no STT or VAD of ours).

    With `company_tts` its words are not played in its own voice: they stream to that voice (text_only), until
    `mode` says the cloned voice failed and Gemini speaks itself."""
    options = gemini_options(language)
    return AgentSession(llm=gemini_realtime_model(options, company_tts is not None, mode),
                        **({"tts": company_tts} if company_tts is not None else {}))


def welcome(session, language):
    """The call opens with the assistant talking: Gemini introduces itself in its own words, in the call's language."""
    session.generate_reply(instructions=greeting(language))


class EventForwarder:
    """The session's events to the page, in the shapes the page expects."""

    def __init__(self, room):
        self.room = room
        self.user_segment = None
        self.segments = 0
        self.tasks = set()

    def publish(self, event):
        task = asyncio.ensure_future(self.room.local_participant.publish_data(
            json.dumps(event).encode(), reliable=True, topic=EVENTS_TOPIC))
        self.tasks.add(task)
        # Who the event is about, never its words.
        label = f"{event['type']} ({event['role']})" if event.get("role") else event["type"]

        def sent(done):
            self.tasks.discard(done)
            if done.cancelled():
                return
            if done.exception() is not None:
                logger.warning("Event %s could not be sent to the page: %s", label, type(done.exception()).__name__)

        task.add_done_callback(sent)

    def on_user_input_transcribed(self, event):
        if self.user_segment is None:
            self.segments += 1
            self.user_segment = f"user-{self.segments}"
        # What was sent, without the words: the id, whether it is final, how long it is.
        logger.info("user transcript %s final=%s chars=%d", self.user_segment, event.is_final, len(event.transcript))
        self.publish({"type": "transcription.segment", "id": self.user_segment, "role": "user",
                      "content": event.transcript, "final": event.is_final})
        if event.is_final:
            self.user_segment = None

    def on_conversation_item_added(self, event):
        item = event.item
        # What the agent said, cut to what was heard when the user interrupted.
        if getattr(item, "role", None) == "assistant" and (item.text_content or "").strip():
            logger.info("The agent said %d characters%s", len(item.text_content),
                        " (cut off by the user)" if item.interrupted else "")
            self.publish({"type": "transcription.segment", "id": item.id, "role": "assistant",
                          "content": item.text_content, "final": True, "interrupted": item.interrupted})

    def on_agent_state_changed(self, event):
        logger.info("agent state %s -> %s", event.old_state, event.new_state)
        self.publish({"type": "agent.state.changed", "old_state": event.old_state, "state": event.new_state})

    def on_voice_fallback(self):
        self.publish({"type": "voice.fallback", "to": "gemini"})

    def on_tool_called(self, name, arguments, answer):
        self.publish({"type": "tool.called", "name": name, "arguments": arguments, "answer": answer})

    def on_error(self, event):
        # The kind of error, not its text: a connection error can carry the URL of Google's session, key included.
        logger.warning("Voice error: %s", type(event.error).__name__)
        self.publish({"type": "error", "source": "worker", "code": type(event.error).__name__,
                      "message": "The voice connection had a problem.",
                      "fatal": not getattr(event.error, "recoverable", True)})

    def attach(self, session):
        for name in ("user_input_transcribed", "conversation_item_added", "agent_state_changed", "error"):
            session.on(name, getattr(self, f"on_{name}"))


def server_options():
    """How this worker shares load and shuts down (LiveKit's server options)."""
    return {
        # A stopping worker lets calls in progress end (the longest call is 30 minutes) before it exits.
        "drain_timeout": 31 * 60,
        # Above this share of its CPU a worker takes no new call and LiveKit gives it to another worker.
        "load_threshold": 0.7,
        # A warm job process, so a call does not wait for one to start.
        "num_idle_processes": 1,
    }


server = AgentServer(**server_options())


def seconds_left(room):
    """How long the call may still last: the longest call, counted from the room's creation. A replacement worker
    that joins after a crash therefore ends the call at the same moment as the one before it would have."""
    age = (datetime.datetime.now(datetime.timezone.utc) - room.creation_time).total_seconds()
    return max(MAX_CALL_S - age, 0)


async def end_after_longest_call(ctx: JobContext):
    await asyncio.sleep(seconds_left(ctx.room))
    logger.info("Call %s reached the longest call length", ctx.room.name)
    await ctx.delete_room()


async def run_call(ctx: JobContext):
    """One call: join the room, wait for the user, and run Gemini Live with the tools and (maybe) the cloned voice."""
    settings = call_settings(ctx.job.metadata)
    if settings is None:
        # The dispatch is not logged: it can carry a grant.
        logger.error("Call %s: a dispatch this worker cannot run", ctx.room.name)
        return
    language, user_identity, speech_grant = settings
    await ctx.connect()
    try:
        waiting = ctx.wait_for_participant(identity=user_identity) if user_identity else ctx.wait_for_participant()
        await asyncio.wait_for(waiting, WAIT_FOR_USER_S)
    except asyncio.TimeoutError:
        logger.warning("Call %s: the user never joined", ctx.room.name)
        ctx.shutdown()
        return
    # The cloned voice, when the call has a grant for it: Gemini then thinks and its words stream to that voice.
    forwarder = EventForwarder(ctx.room)
    # If the cloned voice fails, Gemini's own voice takes over for the rest of the call (VoiceFallback).
    fallback = VoiceFallback(VoiceMode(False), on_switch=forwarder.on_voice_fallback)
    company_tts = cloned_voice_tts(speech_grant, on_failure=fallback.on_failure)
    cloned = company_tts is not None
    fallback.mode.cloned = cloned
    logger.info("Call %s: Gemini speaks in %s", ctx.room.name,
                "the cloned voice" if cloned else "its own voice" + (" (no cloned voice for this call)" if not speech_grant else ""))
    session = build_session(language, company_tts=company_tts, mode=fallback.mode)
    fallback.session = session
    transcript = Transcript()
    session.on("conversation_item_added", transcript.on_conversation_item_added)
    forwarder.attach(session)
    demo = Agent(instructions=voice_instructions(language),
                 tools=[*demo_tools(transcript, forwarder.on_tool_called), end_call_tool(ctx),
                        *([stop_speaking_tool()] if cloned else [])])
    # record=False: nothing of the call goes to LiveKit Cloud's observability.
    await session.start(agent=demo, room=ctx.room, record=False)
    welcome(session, language)
    timer = asyncio.create_task(end_after_longest_call(ctx))

    async def stop_timer():
        timer.cancel()

    ctx.add_shutdown_callback(stop_timer)


@server.rtc_session(agent_name=AGENT_NAME)
async def voice_demo(ctx: JobContext):
    await run_call(ctx)


if __name__ == "__main__":
    cli.run_app(server)
