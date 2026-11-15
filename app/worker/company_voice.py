"""The cloned voice for what the agent says.

When the call's dispatch carries a speech grant, the agent has each sentence spoken through the API
(POST /api/speech), which asks Mistral with the active clone. The grant belongs to this call and stops working when
the call ends. The Mistral key stays in the API; the worker never holds it.
"""
import asyncio
import logging
import time
import uuid

import aiohttp
from livekit.agents import APIConnectionError, APIStatusError, APITimeoutError, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

logger = logging.getLogger("voice-demo")

SAMPLE_RATE = 24000
SPEECH_PATH = "/api/speech"


async def fetch_speech(url, grant, text, timeout):
    """mp3 audio of `text` in the cloned voice, from the API."""
    started = time.monotonic()
    async with aiohttp.ClientSession() as http:
        async with http.post(url, json={"grant": grant, "text": text},
                             timeout=aiohttp.ClientTimeout(total=timeout)) as response:
            if response.status >= 400:
                logger.warning("Cloned voice answered HTTP %s after %.2f s", response.status, time.monotonic() - started)
                raise APIStatusError(f"cloned voice answered HTTP {response.status}", status_code=response.status,
                                     body=await response.text(), retryable=response.status >= 500)
            audio = await response.read()
    logger.info("Cloned voice spoke %d characters in %.2f s (%d bytes)", len(text), time.monotonic() - started, len(audio))
    return audio


class CompanyVoiceTTS(tts.TTS):
    def __init__(self, backend_url, grant, on_failure=None):
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False), sample_rate=SAMPLE_RATE, num_channels=1)
        self.url = backend_url.rstrip("/") + SPEECH_PATH
        self.grant = grant
        # Called when a sentence could not be spoken, so the call can fall back to Gemini's own voice.
        self.on_failure = on_failure

    def failed(self):
        if self.on_failure is not None:
            try:
                self.on_failure()
            except Exception as exc:
                logger.warning("The fallback to Gemini's voice failed: %s", type(exc).__name__)

    @property
    def model(self):
        return "cloned-voice"

    @property
    def provider(self):
        return "voice-demo-api"

    def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
        return _CompanySpeech(tts=self, input_text=text, conn_options=conn_options)


class _CompanySpeech(tts.ChunkedStream):
    def __init__(self, *, tts, input_text, conn_options):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._company_tts = tts

    async def _run(self, output_emitter):
        output_emitter.initialize(request_id=str(uuid.uuid4()), sample_rate=SAMPLE_RATE, num_channels=1,
                                  mime_type="audio/mpeg")
        try:
            audio = await fetch_speech(self._company_tts.url, self._company_tts.grant, self.input_text,
                                       self._conn_options.timeout)
        except APIStatusError:
            self._company_tts.failed()
            raise
        except asyncio.TimeoutError as exc:
            self._company_tts.failed()
            raise APITimeoutError() from exc
        except Exception as exc:
            self._company_tts.failed()
            raise APIConnectionError() from exc
        output_emitter.push(audio)
        output_emitter.flush()
