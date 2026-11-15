"""Speech: the text is made speakable, and a clone's request carries the chosen pace and expressiveness."""
import os
import asyncio
from unittest.mock import patch

from api import speech
from api.tests.support import DemoCase, FakeMistral


class SpokenTextTests(DemoCase):
    def test_markdown_and_citations_are_removed(self):
        text = "## Title\n**Bold** and [a link](http://x.y) [S1]\n- item one\n```code```"
        self.assertEqual(speech.spoken_text(text), "Title. Bold and a link. item one.")

    def test_a_long_text_is_cut_at_a_sentence_end(self):
        text = "One two three. Four five six. Seven eight nine."
        self.assertEqual(speech.spoken_text(text, limit=32), "One two three. Four five six.")

    def test_empty_text_stays_empty(self):
        self.assertEqual(speech.spoken_text(""), "")
        self.assertEqual(speech.spoken_text(None), "")


class CloneRequestTests(DemoCase):
    def test_pace_and_expressiveness_are_sent(self):
        body = speech.clone_request("Hi.", "v1")
        self.assertEqual(body["input"], "[pace=moderate]Hi.")
        self.assertEqual(body["expressiveness"], 3.0)
        self.assertEqual(body["voice_id"], "v1")

    def test_an_empty_pace_and_zero_expressiveness_send_none(self):
        with patch.dict(os.environ, {"TTS_CLONE_PACE": "", "TTS_CLONE_EXPRESSIVENESS": "0"}):
            body = speech.clone_request("Hi.", "v1")
        self.assertEqual(body["input"], "Hi.")
        self.assertNotIn("expressiveness", body)

    def test_an_unknown_pace_is_not_sent(self):
        with patch.dict(os.environ, {"TTS_CLONE_PACE": "warp"}):
            self.assertEqual(speech.clone_request("Hi.", "v1")["input"], "Hi.")


class SpeakVoiceTests(DemoCase):
    def test_a_missing_voice_raises_voice_missing(self):
        FakeMistral().patch(self)
        with self.assertRaises(speech.VoiceMissing):
            asyncio.run(speech.speak_voice("Hi.", "unknown"))

    def test_a_failing_service_gives_none(self):
        mistral = FakeMistral().patch(self)
        mistral.voices["v1"] = {}
        mistral.fail[("POST", "/v1/audio/speech")] = 500
        self.assertIsNone(asyncio.run(speech.speak_voice("Hi.", "v1")))

    def test_nothing_to_say_gives_none(self):
        FakeMistral().patch(self)
        self.assertIsNone(asyncio.run(speech.speak_voice("   ", "v1")))
