"""The cloned voice's text-to-speech: it reports a failure so the call can fall back to Gemini's own voice."""
import asyncio
import unittest
from unittest.mock import MagicMock, patch

from livekit.agents import APIStatusError

from worker import company_voice


def run_speech(on_failure, fetch):
    async def scenario():
        voice = company_voice.CompanyVoiceTTS("http://api:8000", "grant", on_failure=on_failure)
        stream = voice.synthesize("Hello there.")
        emitter = MagicMock()
        with patch.object(company_voice, "fetch_speech", fetch):
            try:
                await stream._run(emitter)
            finally:
                await stream.aclose()
        return emitter

    return asyncio.run(scenario())


class FailureTests(unittest.TestCase):
    def test_a_service_error_is_reported_and_still_raised_for_livekit(self):
        callback = MagicMock()

        async def fetch(*args):
            raise APIStatusError("nope", status_code=404, body="", retryable=False)

        with self.assertRaises(APIStatusError):
            run_speech(callback, fetch)
        callback.assert_called_once()

    def test_a_timeout_and_a_broken_connection_are_reported_too(self):
        for error in (asyncio.TimeoutError(), OSError("down")):
            callback = MagicMock()

            async def fetch(*args, error=error):
                raise error

            with self.assertRaises(Exception):
                run_speech(callback, fetch)
            callback.assert_called_once()

    def test_a_success_reports_nothing_and_pushes_the_audio(self):
        callback = MagicMock()

        async def fetch(*args):
            return b"mp3"

        emitter = run_speech(callback, fetch)
        callback.assert_not_called()
        emitter.push.assert_called_once_with(b"mp3")

    def test_without_a_callback_a_failure_still_raises_normally(self):
        async def fetch(*args):
            raise OSError("down")

        with self.assertRaises(Exception):
            run_speech(None, fetch)


if __name__ == "__main__":
    unittest.main()
