"""Shared by the API tests: a temporary database, a master key, and a fake Mistral."""
import base64
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx

MASTER = base64.b64encode(bytes(range(32))).decode()
OTHER_MASTER = base64.b64encode(bytes(range(32, 64))).decode()


class DemoCase(unittest.TestCase):
    """A fresh database file and environment for each test."""

    ENV = {}

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = {"DEMO_DB_PATH": os.path.join(folder.name, "demo.sqlite3"), "VOICE_MASTER_KEYS": f"k1:{MASTER}",
               "MISTRAL_API_KEY": "mistral-key", "TTS_API_BASE": "https://mistral.test",
               "LIVEKIT_URL": "ws://livekit:7880", "LIVEKIT_PUBLIC_URL": "ws://localhost:7880",
               "LIVEKIT_API_KEY": "devkey", "LIVEKIT_API_SECRET": "a-livekit-secret-long-enough-for-hs256",
               **self.ENV}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)


class FakeMistral:
    """Stands in for Mistral: records the requests, answers from `handlers` by (method, path)."""

    def __init__(self):
        self.requests = []
        self.created = 0
        self.voices = {}      # voice id -> body
        self.fail = {}        # (method, path prefix) -> status
        self.speech_audio = b"MP3-AUDIO"

    def handle(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, body))
        for (method, prefix), status in self.fail.items():
            if request.method == method and path.startswith(prefix):
                return httpx.Response(status, json={"message": "failed"})
        if request.method == "POST" and path == "/v1/audio/voices":
            self.created += 1
            voice_id = f"voice-{self.created}"
            self.voices[voice_id] = body
            return httpx.Response(200, json={"id": voice_id})
        if path.startswith("/v1/audio/voices/"):
            voice_id = path.rsplit("/", 1)[1]
            if voice_id not in self.voices:
                return httpx.Response(404, json={"message": "invalid_voice"})
            if request.method == "DELETE":
                del self.voices[voice_id]
            return httpx.Response(200, json={"id": voice_id})
        if request.method == "POST" and path == "/v1/audio/speech":
            if body["voice_id"] not in self.voices:
                return httpx.Response(404, json={"message": "invalid_voice"})
            return httpx.Response(200, json={"audio_data": base64.b64encode(self.speech_audio).decode()})
        return httpx.Response(500)

    def patch(self, test):
        transport = httpx.MockTransport(self.handle)
        real = httpx.AsyncClient

        def client(*args, **kwargs):
            return real(*args, transport=transport, **kwargs)

        patcher = patch("httpx.AsyncClient", client)
        patcher.start()
        test.addCleanup(patcher.stop)
        return self

    def calls(self, method, path_prefix):
        return [(m, p, b) for m, p, b in self.requests if m == method and p.startswith(path_prefix)]
