"""Calls: the room, the browser's token, the dispatch to the worker, the speech grant and the call's lifetime."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import jwt as pyjwt  # installed with livekit-api

from api import calls, library
from api.tests.support import DemoCase, FakeMistral


def run(coroutine):
    return asyncio.run(coroutine)


class FakeLiveKit:
    def __init__(self, fail_dispatch=False):
        self.rooms, self.dispatches, self.deleted, self.closed = [], [], [], 0
        self.fail_dispatch = fail_dispatch

        async def create_room(request):
            self.rooms.append(request)

        async def create_dispatch(request):
            if self.fail_dispatch:
                raise RuntimeError("no worker")
            self.dispatches.append(request)

        async def delete_room(request):
            self.deleted.append(request.room)

        self.room = SimpleNamespace(create_room=create_room, delete_room=delete_room)
        self.agent_dispatch = SimpleNamespace(create_dispatch=create_dispatch)

    async def aclose(self):
        self.closed += 1


class CallCase(DemoCase):
    def setUp(self):
        super().setUp()
        calls._live.clear()
        self.addCleanup(calls._live.clear)
        self.livekit = FakeLiveKit()
        patcher = patch.object(calls, "_api", lambda: self.livekit)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mistral = FakeMistral().patch(self)

    def clone_active(self, language="en"):
        clone = run(library.add("Sarah", b"sound", "a.wav", language=language, speaker_name="Sarah", consent=True))
        library.activate(clone["id"])
        return clone


class GrantTests(CallCase):
    def test_a_grant_is_readable_while_its_call_is_live(self):
        session = run(calls.create_session("en"))
        grant = calls.speech_grant("en", session["session_id"])
        self.assertEqual(calls.read_speech_grant(grant), {"language": "en", "call": session["session_id"]})

    def test_a_grant_for_a_call_that_is_not_live_is_refused(self):
        self.assertIsNone(calls.read_speech_grant(calls.speech_grant("en", "ghost")))

    def test_a_forged_grant_is_refused(self):
        session = run(calls.create_session("en"))
        self.assertIsNone(calls.read_speech_grant(calls.speech_grant("en", session["session_id"]) + "x"))
        self.assertIsNone(calls.read_speech_grant("garbage"))

    def test_an_expired_grant_is_refused(self):
        session = run(calls.create_session("en"))
        grant = calls.speech_grant("en", session["session_id"])
        with patch.object(calls, "SESSION_SECONDS", -1):
            self.assertIsNone(calls.read_speech_grant(grant))


class CreateSessionTests(CallCase):
    def test_a_room_is_made_and_the_worker_dispatched_with_the_call_id_and_language(self):
        session = run(calls.create_session("nl"))
        self.assertEqual(len(self.livekit.rooms), 1)
        room = self.livekit.rooms[0]
        self.assertEqual(room.name, f"demo-{session['session_id']}")
        self.assertEqual(room.max_participants, 3)
        dispatch = self.livekit.dispatches[0]
        self.assertEqual(dispatch.agent_name, "voice-demo")
        self.assertEqual(dispatch.room, room.name)
        metadata = json.loads(dispatch.metadata)
        self.assertEqual(metadata["call_id"], session["session_id"])
        self.assertEqual(metadata["language"], "nl")
        self.assertTrue(metadata["user"].startswith("caller-"))
        self.assertEqual(self.livekit.closed, 1)

    def test_the_browser_gets_a_token_for_its_room_only_and_the_public_address(self):
        session = run(calls.create_session("en"))
        self.assertEqual(session["server_url"], "ws://localhost:7880")
        claims = pyjwt.decode(session["participant_token"], options={"verify_signature": False})
        self.assertEqual(claims["video"]["room"], f"demo-{session['session_id']}")
        self.assertTrue(claims["video"]["roomJoin"])
        self.assertEqual(claims["sub"], json.loads(self.livekit.dispatches[0].metadata)["user"])
        self.assertNotIn("name", claims)

    def test_without_a_clone_there_is_no_grant_and_gemini_speaks(self):
        session = run(calls.create_session("en"))
        self.assertFalse(session["company_voice"])
        self.assertNotIn("speech", json.loads(self.livekit.dispatches[0].metadata))

    def test_with_an_active_clone_the_dispatch_carries_a_grant_for_this_call(self):
        self.clone_active("en")
        session = run(calls.create_session("en"))
        self.assertTrue(session["company_voice"])
        grant = json.loads(self.livekit.dispatches[0].metadata)["speech"]["grant"]
        self.assertEqual(calls.read_speech_grant(grant)["call"], session["session_id"])

    def test_a_clone_mistral_has_lost_means_gemini_speaks_from_the_start(self):
        self.clone_active("en")
        self.mistral.voices.clear()
        session = run(calls.create_session("en"))
        self.assertFalse(session["company_voice"])
        self.assertNotIn("speech", json.loads(self.livekit.dispatches[0].metadata))

    def test_a_mistral_that_cannot_be_reached_means_gemini_speaks_from_the_start(self):
        self.clone_active("en")
        self.mistral.fail[("GET", "/v1/audio/voices")] = 500
        session = run(calls.create_session("en"))
        self.assertFalse(session["company_voice"])

    def test_the_switch_can_force_gemini_s_own_voice(self):
        self.clone_active("en")
        session = run(calls.create_session("en", use_clone=False))
        self.assertFalse(session["company_voice"])
        self.assertNotIn("speech", json.loads(self.livekit.dispatches[0].metadata))

    def test_a_new_call_ends_the_older_one(self):
        first = run(calls.create_session("en"))
        second = run(calls.create_session("en"))
        self.assertEqual(self.livekit.deleted, [f"demo-{first['session_id']}"])
        self.assertIsNone(calls.get(first["session_id"]))
        self.assertIsNotNone(calls.get(second["session_id"]))

    def test_a_worker_that_cannot_be_dispatched_leaves_no_live_call(self):
        self.livekit.fail_dispatch = True
        with self.assertRaises(calls.CallError) as raised:
            run(calls.create_session("en"))
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(calls._live, {})

    def test_an_unsupported_language_is_refused(self):
        with self.assertRaises(calls.CallError) as raised:
            run(calls.create_session("xx"))
        self.assertEqual(raised.exception.status, 400)

    def test_without_livekit_settings_voice_is_not_set_up(self):
        with patch.dict("os.environ", {"LIVEKIT_URL": ""}):
            with self.assertRaises(calls.CallError) as raised:
                run(calls.create_session("en"))
        self.assertEqual(raised.exception.status, 503)


class LifetimeTests(CallCase):
    def test_ending_a_call_closes_its_room_and_forgets_it(self):
        session = run(calls.create_session("en"))
        self.assertTrue(run(calls.end_session(session["session_id"])))
        self.assertEqual(self.livekit.deleted, [f"demo-{session['session_id']}"])
        self.assertIsNone(calls.get(session["session_id"]))

    def test_ending_an_unknown_call_says_so(self):
        self.assertFalse(run(calls.end_session("ghost")))

    def test_a_call_past_the_longest_length_counts_as_gone(self):
        session = run(calls.create_session("en"))
        calls._live[session["session_id"]]["started"] -= calls.MAX_CALL_SECONDS + 1
        self.assertIsNone(calls.get(session["session_id"]))

    def test_a_finished_room_event_ends_the_call(self):
        session = run(calls.create_session("en"))
        event = SimpleNamespace(event="room_finished", room=SimpleNamespace(name=f"demo-{session['session_id']}"))
        self.assertEqual(calls.finished_call(event), session["session_id"])
        self.assertIsNone(calls.finished_call(SimpleNamespace(event="room_started", room=event.room)))
        self.assertIsNone(calls.finished_call(SimpleNamespace(event="room_finished", room=SimpleNamespace(name="other-1"))))
