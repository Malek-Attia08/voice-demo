"""The demo worker, without a LiveKit server or Google.

Run in the worker's environment, from C:\\voice-demo\\app:
    python -m unittest discover -s worker/tests -t .
"""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from worker import agent


def in_loop(make):
    """Build what needs a running event loop, as in the worker's entrypoint."""
    async def build():
        return make()
    return asyncio.run(build())


class CallSettingsTests(unittest.TestCase):
    def test_the_dispatch_gives_the_language_the_caller_and_the_grant(self):
        metadata = json.dumps({"call_id": "c1", "language": "nl", "user": "caller-1", "speech": {"grant": "g"}})
        self.assertEqual(agent.call_settings(metadata), ("nl", "caller-1", "g"))

    def test_a_dispatch_without_a_grant_has_none(self):
        self.assertEqual(agent.call_settings(json.dumps({"language": "en", "user": "u"})), ("en", "u", None))

    def test_the_language_defaults_to_english(self):
        self.assertEqual(agent.call_settings(json.dumps({"user": "u"}))[0], "en")

    def test_a_dispatch_that_is_not_json_cannot_run(self):
        self.assertIsNone(agent.call_settings("not json"))
        self.assertIsNone(agent.call_settings("[1, 2]"))

    def test_an_empty_dispatch_runs_with_the_defaults(self):
        self.assertEqual(agent.call_settings(None), ("en", None, None))


class GeminiOptionsTests(unittest.TestCase):
    ENV = {"GOOGLE_API_KEY": "google-key"}

    def options(self, language="en", **env):
        with patch.dict("os.environ", {**self.ENV, **env}):
            return agent.gemini_options(language)

    def test_the_model_voice_and_key_come_from_the_environment(self):
        options = self.options(GEMINI_LIVE_MODEL="a-model", GEMINI_LIVE_VOICE="Puck")
        self.assertEqual((options["model"], options["voice"], options["api_key"]), ("a-model", "Puck", "google-key"))

    def test_the_defaults_are_the_default_model_and_voice(self):
        options = self.options()
        self.assertEqual((options["model"], options["voice"]), ("gemini-3.8-live", "Kore"))

    def test_long_calls_are_compressed_and_a_dropped_connection_can_resume(self):
        from google.genai import types

        options = self.options()
        self.assertIsNotNone(options["context_window_compression"].sliding_window)
        self.assertIsInstance(options["session_resumption"], types.SessionResumptionConfig)

    def test_the_session_names_the_api_version_because_googles_default_has_no_live_endpoint(self):
        self.assertEqual(self.options()["api_version"], "v1beta")

    def test_the_transcription_may_be_any_supported_language_with_the_calls_first(self):
        def hint(language):
            return self.options(language)["input_audio_transcription"].language_codes

        self.assertEqual(hint("nl"), ["nl-NL", "en-US", "fr-FR", "de-DE"])
        self.assertEqual(hint("fr-BE"), ["fr-FR", "en-US", "nl-NL", "de-DE"])
        self.assertNotIn("input_audio_transcription", self.options("xx"))

    def test_the_prompt_names_the_language_and_lists_the_tools(self):
        instructions = self.options("nl")["instructions"]
        self.assertIn("Dutch", instructions)
        for name in ("rag_answer", "history", "medical_info", "get_time", "find_appointment_slots", "end_call"):
            self.assertIn(name, instructions)

    def test_only_options_both_google_products_accept_are_used(self):
        common = {"model", "voice", "instructions", "api_key", "context_window_compression", "session_resumption",
                  "language", "modalities", "input_audio_transcription", "output_audio_transcription",
                  "temperature", "vertexai", "project", "location", "api_version"}
        self.assertLessEqual(set(self.options("nl")), common)


class TranscriptTests(unittest.TestCase):
    def event(self, role, text):
        return SimpleNamespace(item=SimpleNamespace(role=role, text_content=text))

    def test_the_turns_are_kept_in_order_without_empty_ones(self):
        transcript = agent.Transcript()
        for role, text in (("user", "hi"), ("assistant", "hello"), ("assistant", "  "), ("system", "x"), ("user", "bye")):
            transcript.on_conversation_item_added(self.event(role, text))
        self.assertEqual(transcript.turns, [("user", "hi"), ("agent", "hello"), ("user", "bye")])


class ToolTests(unittest.TestCase):
    def build(self, announce=None):
        transcript = agent.Transcript()
        transcript.turns.extend([("user", "appointment?"), ("agent", "Let me check")])
        return transcript, agent.demo_tools(transcript, announce)

    def test_every_tool_is_offered_to_gemini(self):
        _, built = self.build()
        self.assertEqual([tool.info.name for tool in built], ["rag_answer", "history", "medical_info", "get_time", "find_appointment_slots"])

    def test_a_tool_answers_with_the_demo_data(self):
        _, built = self.build()
        rag = next(tool for tool in built if tool.info.name == "rag_answer")
        answer = asyncio.run(rag(raw_arguments={"question": "cancel an appointment"}, context=SimpleNamespace()))
        self.assertIn("24 hours", answer)

    def test_the_history_tool_reads_the_calls_own_turns(self):
        _, built = self.build()
        history = next(tool for tool in built if tool.info.name == "history")
        answer = asyncio.run(history(raw_arguments={"count": 2}, context=SimpleNamespace()))
        self.assertEqual(answer, "user: appointment?\nagent: Let me check")

    def test_each_call_is_announced_to_the_page_by_name_and_arguments(self):
        seen = []
        _, built = self.build(announce=lambda name, arguments, answer: seen.append((name, arguments, answer)))
        clock = next(tool for tool in built if tool.info.name == "get_time")
        asyncio.run(clock(raw_arguments={}, context=SimpleNamespace()))
        self.assertEqual(seen[0][0], "get_time")
        self.assertEqual(seen[0][1], {})
        self.assertTrue(seen[0][2].startswith("It is "))


class ClonedVoiceTests(unittest.TestCase):
    def test_no_grant_means_gemini_speaks(self):
        with patch.dict("os.environ", {"DEMO_API_URL": "http://api:8000"}):
            self.assertIsNone(agent.cloned_voice_tts(None))

    def test_a_grant_without_the_api_address_means_gemini_speaks(self):
        with patch.dict("os.environ", {"DEMO_API_URL": ""}):
            self.assertIsNone(agent.cloned_voice_tts("grant"))

    def test_a_grant_makes_the_cloned_voice_the_text_to_speech_cut_into_sentences(self):
        from livekit.agents import tts

        with patch.dict("os.environ", {"DEMO_API_URL": "http://api:8000"}):
            made = in_loop(lambda: agent.cloned_voice_tts("grant"))
        self.assertIsInstance(made, tts.StreamAdapter)
        self.assertEqual(made._wrapped_tts.url, "http://api:8000/api/speech")
        self.assertEqual(made._wrapped_tts.grant, "grant")


class TextOnlyTests(unittest.TestCase):
    def test_gemini_audio_is_thrown_away_and_its_words_are_kept_as_a_text_message(self):
        drained = []

        async def text():
            yield "Hello "
            yield "there."

        async def audio():
            for frame in ("a", "b"):
                drained.append(frame)
                yield frame

        async def messages():
            yield SimpleNamespace(message_id="m1", text_stream=text(), audio_stream=audio())

        async def scenario():
            out = []
            async for generation in agent.text_only(messages()):
                words = [chunk async for chunk in generation.text_stream]
                out.append((generation.message_id, words, await generation.modalities))
            await asyncio.sleep(0.05)
            return out

        self.assertEqual(asyncio.run(scenario()), [("m1", ["Hello ", "there."], ["text"])])
        self.assertEqual(drained, ["a", "b"])

    def test_the_session_turns_every_announced_generation_into_a_text_only_one(self):
        class Base:
            def __init__(self):
                self.emitted = []

            def emit(self, event, *args):
                self.emitted.append((event, args))

        session = agent.text_only_session(Base)()
        generation = SimpleNamespace(message_stream="original")
        with patch.object(agent, "text_only", lambda stream, mode=None: f"text-only({stream})"):
            session.emit("generation_created", generation)
            session.emit("other", "x")
        self.assertEqual(generation.message_stream, "text-only(original)")
        self.assertEqual(session.emitted[1], ("other", ("x",)))


class VoiceFallbackTests(unittest.TestCase):
    """When the cloned voice fails, Gemini's own voice takes over for the rest of the call."""

    def test_a_call_with_a_cloned_voice_starts_cloned(self):
        self.assertTrue(agent.VoiceMode(True).cloned)
        self.assertFalse(agent.VoiceMode(False).cloned)

    def test_when_gemini_is_to_speak_its_message_goes_through_with_its_audio_untouched(self):
        consumed = []

        async def audio():
            consumed.append("frame")
            yield "frame"

        message = SimpleNamespace(message_id="m1", text_stream=None, audio_stream=audio())

        async def messages():
            yield message

        async def scenario():
            return [generation async for generation in agent.text_only(messages(), agent.VoiceMode(False))]

        out = asyncio.run(scenario())
        self.assertEqual(len(out), 1)
        self.assertIs(out[0], message)
        self.assertEqual(consumed, [])

    def test_the_mode_is_read_for_every_message_so_a_switch_takes_effect_mid_call(self):
        mode = agent.VoiceMode(True)
        second_message = SimpleNamespace(message_id="second", text_stream=None, audio_stream=None)

        async def text():
            yield "Hi."

        async def audio():
            yield "frame"

        async def messages():
            yield SimpleNamespace(message_id="first", text_stream=text(), audio_stream=audio())
            mode.cloned = False
            yield second_message

        async def scenario():
            out = [generation async for generation in agent.text_only(messages(), mode)]
            return out, await out[0].modalities

        (first, second), modalities = asyncio.run(scenario())
        self.assertEqual(modalities, ["text"])
        self.assertIs(second, second_message)

    def test_the_session_passes_its_mode_to_text_only(self):
        class Base:
            def emit(self, event, *args):
                pass

        seen = []
        mode = agent.VoiceMode(True)
        session = agent.text_only_session(Base, mode)()
        with patch.object(agent, "text_only", lambda stream, mode=None: seen.append(mode) or "x"):
            session.emit("generation_created", SimpleNamespace(message_stream="s"))
        self.assertEqual(seen, [mode])

    def test_the_first_failure_switches_to_gemini_asks_it_to_say_the_reply_again_and_tells_the_page(self):
        mode = agent.VoiceMode(True)
        told = []
        fallback = agent.VoiceFallback(mode, on_switch=lambda: told.append("page"))
        fallback.session = MagicMock()
        fallback.on_failure()
        self.assertFalse(mode.cloned)
        instructions = fallback.session.generate_reply.call_args.kwargs["instructions"]
        self.assertIn("could not be played", instructions)
        self.assertEqual(told, ["page"])

    def test_only_the_first_failure_acts(self):
        mode = agent.VoiceMode(True)
        fallback = agent.VoiceFallback(mode)
        fallback.session = MagicMock()
        fallback.on_failure()
        fallback.on_failure()
        fallback.session.generate_reply.assert_called_once()

    def test_a_failure_before_the_session_exists_still_switches(self):
        mode = agent.VoiceMode(True)
        agent.VoiceFallback(mode).on_failure()
        self.assertFalse(mode.cloned)

    def test_the_cloned_voice_reports_its_failures_to_the_fallback(self):
        callback = MagicMock()
        with patch.dict("os.environ", {"DEMO_API_URL": "http://api:8000"}):
            made = in_loop(lambda: agent.cloned_voice_tts("grant", on_failure=callback))
        self.assertIs(made._wrapped_tts.on_failure, callback)

    def test_a_cloned_call_gets_a_mode_that_starts_cloned_and_a_plain_one_does_not(self):
        for grant, cloned in (("g", True), (None, False)):
            speech = {"speech": {"grant": grant}} if grant else {}
            ctx = RunCallTests().job(json.dumps({"language": "en", "user": "caller-1", **speech}),
                                     participant=SimpleNamespace(identity="caller-1"))
            session = MagicMock()
            session.start = AsyncMock()
            with patch.dict("os.environ", {"DEMO_API_URL": "http://api:8000", "GOOGLE_API_KEY": "k"}), \
                    patch.object(agent, "build_session", return_value=session) as build, patch.object(agent, "Agent"):
                asyncio.run(RunCallTests().start_call(ctx))
            self.assertEqual(build.call_args.kwargs["mode"].cloned, cloned)


class SessionTests(unittest.TestCase):
    OPTIONS = {"model": "gemini-3.8-live", "voice": "Kore", "instructions": "x", "api_key": "k"}

    def test_the_session_is_gemini_alone_with_no_separate_mouth(self):
        from livekit.plugins import google

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "k"}):
            session = in_loop(lambda: agent.build_session("en"))
        self.assertIsInstance(session.llm, google.realtime.RealtimeModel)
        self.assertIsNone(session.stt)
        self.assertIsNone(session.tts)

    def test_with_a_cloned_voice_gemini_speaks_through_it(self):
        voice = MagicMock()
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "k"}):
            session = in_loop(lambda: agent.build_session("en", company_tts=voice))
        self.assertIs(session.tts, voice)

    def test_a_cloned_call_announces_text_only_generations(self):
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "k"}):
            model = in_loop(lambda: agent.gemini_realtime_model(agent.gemini_options("en"), cloned=True))
            plain = in_loop(lambda: agent.gemini_realtime_model(agent.gemini_options("en"), cloned=False))
        self.assertNotEqual(model.session.__qualname__, plain.session.__qualname__)


class WelcomeTests(unittest.TestCase):
    def test_gemini_opens_the_call_by_greeting_in_the_calls_language(self):
        session = MagicMock()
        agent.welcome(session, "nl")
        instructions = session.generate_reply.call_args.kwargs["instructions"]
        self.assertIn("Dutch", instructions)
        self.assertIn("Introduce yourself as Mira", instructions)


class EventTests(unittest.TestCase):
    def forwarder(self):
        published = []

        async def publish_data(data, reliable=True, topic=""):
            published.append((topic, json.loads(data)))

        room = SimpleNamespace(local_participant=SimpleNamespace(publish_data=publish_data))
        return agent.EventForwarder(room), published

    def run_events(self, act):
        forwarder, published = self.forwarder()

        async def scenario():
            act(forwarder)
            await asyncio.sleep(0.01)

        asyncio.run(scenario())
        return published

    def test_the_users_words_go_to_the_page_as_segments_that_end_when_final(self):
        def act(forwarder):
            forwarder.on_user_input_transcribed(SimpleNamespace(transcript="hel", is_final=False))
            forwarder.on_user_input_transcribed(SimpleNamespace(transcript="hello", is_final=True))
            forwarder.on_user_input_transcribed(SimpleNamespace(transcript="next", is_final=True))

        published = self.run_events(act)
        self.assertEqual({topic for topic, _ in published}, {agent.EVENTS_TOPIC})
        ids = [event["id"] for _, event in published]
        self.assertEqual(ids, ["user-1", "user-1", "user-2"])
        self.assertEqual(published[1][1]["content"], "hello")
        self.assertTrue(published[1][1]["final"])

    def test_what_the_agent_said_goes_to_the_page_with_whether_it_was_cut_off(self):
        item = SimpleNamespace(role="assistant", id="a1", text_content="Hi there", interrupted=True)
        published = self.run_events(lambda f: f.on_conversation_item_added(SimpleNamespace(item=item)))
        self.assertEqual(published[0][1], {"type": "transcription.segment", "id": "a1", "role": "assistant",
                                           "content": "Hi there", "final": True, "interrupted": True})

    def test_a_fallback_to_gemini_goes_to_the_page(self):
        published = self.run_events(lambda f: f.on_voice_fallback())
        self.assertEqual(published[0][1], {"type": "voice.fallback", "to": "gemini"})

    def test_a_tool_call_goes_to_the_page(self):
        published = self.run_events(lambda f: f.on_tool_called("rag_answer", {"question": "appointment"}, "24 hours"))
        self.assertEqual(published[0][1], {"type": "tool.called", "name": "rag_answer",
                                           "arguments": {"question": "appointment"}, "answer": "24 hours"})

    def test_an_error_says_its_kind_but_not_its_text(self):
        error = SimpleNamespace(error=RuntimeError("wss://google/session?key=SECRET"))
        published = self.run_events(lambda f: f.on_error(error))
        self.assertEqual(published[0][1]["code"], "RuntimeError")
        self.assertNotIn("SECRET", json.dumps(published))

    def test_agent_state_changes_go_to_the_page(self):
        event = SimpleNamespace(old_state="listening", new_state="speaking")
        published = self.run_events(lambda f: f.on_agent_state_changed(event))
        self.assertEqual(published[0][1], {"type": "agent.state.changed", "old_state": "listening", "state": "speaking"})


class HangUpTests(unittest.TestCase):
    def test_after_the_goodbye_is_spoken_the_page_is_told_and_the_room_closes(self):
        published = []

        async def publish_data(data, reliable=True, topic=""):
            published.append((topic, json.loads(data)))

        ctx = SimpleNamespace(room=SimpleNamespace(name="demo-1", local_participant=SimpleNamespace(publish_data=publish_data)),
                              delete_room=AsyncMock())
        tool = agent.end_call_tool(ctx)
        context = SimpleNamespace(wait_for_playout=AsyncMock())
        self.assertEqual(asyncio.run(tool(raw_arguments={}, context=context)), "The call has ended.")
        context.wait_for_playout.assert_awaited_once()
        self.assertEqual(published, [(agent.EVENTS_TOPIC, {"type": "call.ended", "reason": "goodbye"})])
        ctx.delete_room.assert_awaited_once()

    def test_stop_speaking_interrupts_the_session(self):
        session = MagicMock()
        tool = agent.stop_speaking_tool()
        self.assertEqual(asyncio.run(tool(raw_arguments={}, context=SimpleNamespace(session=session))), "Stopped.")
        session.interrupt.assert_called_once()


class CallClockTests(unittest.TestCase):
    def room(self, age):
        import datetime

        return SimpleNamespace(creation_time=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age))

    def test_a_new_room_has_the_whole_call_left(self):
        self.assertAlmostEqual(agent.seconds_left(self.room(0)), agent.MAX_CALL_S, delta=2)

    def test_a_replacement_worker_ends_the_call_at_the_rooms_deadline(self):
        self.assertAlmostEqual(agent.seconds_left(self.room(600)), agent.MAX_CALL_S - 600, delta=2)

    def test_a_room_older_than_the_longest_call_ends_at_once(self):
        self.assertEqual(agent.seconds_left(self.room(agent.MAX_CALL_S + 100)), 0)


class RunCallTests(unittest.TestCase):
    def job(self, metadata, participant=None, wait_error=None):
        waiting = AsyncMock(side_effect=wait_error) if wait_error else AsyncMock(return_value=participant)
        import datetime

        room = SimpleNamespace(name="demo-1", on=MagicMock(), local_participant=SimpleNamespace(publish_data=AsyncMock()),
                               creation_time=datetime.datetime.now(datetime.timezone.utc))
        return SimpleNamespace(job=SimpleNamespace(metadata=metadata), room=room, connect=AsyncMock(),
                               wait_for_participant=lambda identity=None: waiting(), shutdown=MagicMock(),
                               add_shutdown_callback=MagicMock(), delete_room=AsyncMock())

    def test_a_dispatch_the_worker_cannot_run_ends_without_connecting(self):
        ctx = self.job("not json")
        asyncio.run(agent.run_call(ctx))
        ctx.connect.assert_not_awaited()

    def test_a_user_who_never_joins_ends_the_job(self):
        ctx = self.job(json.dumps({"language": "en", "user": "u"}), wait_error=asyncio.TimeoutError())
        asyncio.run(agent.run_call(ctx))
        ctx.shutdown.assert_called_once()

    def test_a_call_with_a_grant_starts_gemini_with_the_cloned_voice_the_tools_and_a_greeting(self):
        ctx = self.job(json.dumps({"language": "nl", "user": "caller-1", "speech": {"grant": "g"}}),
                       participant=SimpleNamespace(identity="caller-1"))
        session = MagicMock()
        session.start = AsyncMock()
        with patch.dict("os.environ", {"DEMO_API_URL": "http://api:8000", "GOOGLE_API_KEY": "k"}), \
                patch.object(agent, "build_session", return_value=session) as build, \
                patch.object(agent, "Agent") as agent_class:
            asyncio.run(self.start_call(ctx))
        company_tts = build.call_args.kwargs["company_tts"]
        self.assertIsNotNone(company_tts)
        tools = agent_class.call_args.kwargs["tools"]
        names = [tool.info.name for tool in tools]
        self.assertEqual(names, ["rag_answer", "history", "medical_info", "get_time", "find_appointment_slots", "end_call", "stop_speaking"])
        session.start.assert_awaited_once()
        session.generate_reply.assert_called_once()

    def test_a_call_without_a_grant_speaks_with_gemini_s_own_voice_and_has_no_stop_tool(self):
        ctx = self.job(json.dumps({"language": "en", "user": "caller-1"}), participant=SimpleNamespace(identity="caller-1"))
        session = MagicMock()
        session.start = AsyncMock()
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "k"}), \
                patch.object(agent, "build_session", return_value=session) as build, \
                patch.object(agent, "Agent") as agent_class:
            asyncio.run(self.start_call(ctx))
        self.assertIsNone(build.call_args.kwargs["company_tts"])
        names = [tool.info.name for tool in agent_class.call_args.kwargs["tools"]]
        self.assertEqual(names, ["rag_answer", "history", "medical_info", "get_time", "find_appointment_slots", "end_call"])

    async def start_call(self, ctx):
        # run_call starts a timer task for the longest call; it is cancelled with the loop.
        await agent.run_call(ctx)


if __name__ == "__main__":
    unittest.main()
