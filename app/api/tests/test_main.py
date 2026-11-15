"""The HTTP routes the page and the worker use."""
import base64
from unittest.mock import patch

from fastapi.testclient import TestClient

from api import calls, library, main
from api.tests.support import DemoCase, FakeMistral
from api.tests.test_calls import FakeLiveKit

WAV = b"RIFF" + b"\x00" * 64


class ApiCase(DemoCase):
    def setUp(self):
        super().setUp()
        calls._live.clear()
        self.addCleanup(calls._live.clear)
        self.livekit = FakeLiveKit()
        patcher = patch.object(calls, "_api", lambda: self.livekit)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mistral = FakeMistral().patch(self)
        self.client = TestClient(main.app)
        # The test client talks to the app through httpx too, so it must keep the real transport: it is built
        # before FakeMistral is consulted only for outgoing Mistral calls made inside the app.

    def make_clone(self, label="Sarah", language="en"):
        response = self.client.post(
            "/api/clones", data={"label": label, "language": language, "speaker_name": "Sarah Peeters",
                                 "consent": "true", "source": "imported"},
            files={"sample": ("a.wav", WAV, "audio/wav")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class CloneRouteTests(ApiCase):
    def test_a_clone_is_made_listed_activated_and_deleted(self):
        clone = self.make_clone()
        self.assertEqual(clone["status"], "ready")
        self.assertNotIn("mistral_voice_id", clone)
        self.assertEqual([c["id"] for c in self.client.get("/api/clones").json()], [clone["id"]])
        self.assertEqual(self.client.post(f"/api/clones/{clone['id']}/activate").status_code, 200)
        self.assertTrue(self.client.get("/api/clones").json()[0]["active"])
        self.assertEqual(self.client.post(f"/api/clones/{clone['id']}/deactivate").status_code, 200)
        self.assertFalse(self.client.get("/api/clones").json()[0]["active"])
        self.assertEqual(self.client.delete(f"/api/clones/{clone['id']}").status_code, 200)
        self.assertEqual(self.client.get("/api/clones").json(), [])

    def test_no_consent_is_a_400(self):
        response = self.client.post("/api/clones", data={"label": "S", "language": "en", "speaker_name": "S", "consent": "false"},
                                    files={"sample": ("a.wav", WAV, "audio/wav")})
        self.assertEqual(response.status_code, 400)

    def test_a_wrong_file_type_is_a_400(self):
        response = self.client.post("/api/clones", data={"label": "S", "language": "en", "speaker_name": "S", "consent": "true"},
                                    files={"sample": ("a.m4a", WAV, "audio/mp4")})
        self.assertEqual(response.status_code, 400)

    def test_a_voice_mistral_refuses_is_a_502(self):
        self.mistral.fail[("POST", "/v1/audio/voices")] = 400
        response = self.client.post("/api/clones", data={"label": "S", "language": "en", "speaker_name": "S", "consent": "true"},
                                    files={"sample": ("a.wav", WAV, "audio/wav")})
        self.assertEqual(response.status_code, 502)

    def test_a_file_over_15_mb_is_refused(self):
        big = b"\x00" * (15 * 1024 * 1024 + 1)
        response = self.client.post("/api/clones", data={"label": "S", "language": "en", "speaker_name": "S", "consent": "true"},
                                    files={"sample": ("a.wav", big, "audio/wav")})
        self.assertEqual(response.status_code, 413)

    def test_an_unknown_clone_is_a_404(self):
        self.assertEqual(self.client.post("/api/clones/nope/activate").status_code, 404)
        self.assertEqual(self.client.delete("/api/clones/nope").status_code, 404)

    def test_check_says_whether_mistral_has_the_voice(self):
        clone = self.make_clone()
        self.assertEqual(self.client.get(f"/api/clones/{clone['id']}/check").json(), {"works": True})
        self.mistral.voices.clear()
        self.assertEqual(self.client.get(f"/api/clones/{clone['id']}/check").json(), {"works": False})

    def test_a_delete_mistral_cannot_do_is_reported_and_kept_for_later(self):
        clone = self.make_clone()
        self.mistral.fail[("DELETE", "/v1/audio/voices")] = 500
        self.assertEqual(self.client.delete(f"/api/clones/{clone['id']}").status_code, 502)
        self.assertEqual(self.client.get("/api/clones").json()[0]["status"], "deleting")

    def test_the_config_names_the_languages_and_the_consent_version(self):
        config = self.client.get("/api/config").json()
        self.assertEqual([language["code"] for language in config["languages"]], ["en", "nl", "fr", "de"])
        self.assertEqual(config["consent_wording"], library.CONSENT_WORDING)
        self.assertTrue(config["cloning"])


class SessionRouteTests(ApiCase):
    def test_a_session_gives_the_browser_what_it_needs_to_join(self):
        response = self.client.post("/api/session", json={"language": "en"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"session_id", "server_url", "participant_token", "company_voice"})

    def test_a_session_error_carries_its_status(self):
        self.livekit.fail_dispatch = True
        self.assertEqual(self.client.post("/api/session", json={"language": "en"}).status_code, 503)

    def test_the_voice_switch_reaches_the_dispatch(self):
        clone = self.make_clone()
        self.client.post(f"/api/clones/{clone['id']}/activate")
        body = self.client.post("/api/session", json={"language": "en", "use_clone": False}).json()
        self.assertFalse(body["company_voice"])
        body = self.client.post("/api/session", json={"language": "en", "use_clone": True}).json()
        self.assertTrue(body["company_voice"])

    def test_ending_a_session(self):
        body = self.client.post("/api/session", json={"language": "en"}).json()
        self.assertEqual(self.client.post("/api/session/end", json={"session_id": body["session_id"]}).status_code, 200)
        self.assertEqual(self.client.post("/api/session/end", json={"session_id": body["session_id"]}).status_code, 404)

    def test_an_unsigned_webhook_is_refused(self):
        self.assertEqual(self.client.post("/api/livekit-webhook", content=b"{}").status_code, 401)


class SpeechRouteTests(ApiCase):
    def grant(self, language="en"):
        session = self.client.post("/api/session", json={"language": language}).json()
        return calls.speech_grant(language, session["session_id"])

    def test_a_valid_grant_gets_the_clone_speaking_as_mp3(self):
        clone = self.make_clone()
        self.client.post(f"/api/clones/{clone['id']}/activate")
        response = self.client.post("/api/speech", json={"grant": self.grant(), "text": "Hello there."})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/mpeg")
        self.assertEqual(response.content, b"MP3-AUDIO")

    def test_a_bad_grant_is_a_403(self):
        response = self.client.post("/api/speech", json={"grant": "nope", "text": "Hello."})
        self.assertEqual(response.status_code, 403)

    def test_no_active_clone_is_a_404_so_the_caller_can_fall_back(self):
        response = self.client.post("/api/speech", json={"grant": self.grant(), "text": "Hello."})
        self.assertEqual(response.status_code, 404)

    def test_text_that_cannot_be_spoken_is_a_400(self):
        clone = self.make_clone()
        self.client.post(f"/api/clones/{clone['id']}/activate")
        response = self.client.post("/api/speech", json={"grant": self.grant(), "text": "```code```"})
        self.assertEqual(response.status_code, 400)


class HealthTests(ApiCase):
    def test_health(self):
        self.assertEqual(self.client.get("/api/health").json(), {"ok": True})
