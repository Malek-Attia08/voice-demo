"""The nightly care: check, repair, retry deletes, renew, rewrap."""
import asyncio
import os
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from api import care, library, vault
from api.tests.support import MASTER, OTHER_MASTER, DemoCase, FakeMistral


def run(coroutine):
    return asyncio.run(coroutine)


class CareTests(DemoCase):
    def setUp(self):
        super().setUp()
        self.mistral = FakeMistral().patch(self)

    def add(self, label, language):
        return run(library.add(label, b"sound", "a.wav", language=language, speaker_name="S", consent=True))

    def test_one_round_repairs_a_lost_voice_retries_a_delete_and_renews_retention(self):
        lost, stuck, fine = self.add("Lost", "en"), self.add("Stuck", "fr"), self.add("Fine", "nl")
        del self.mistral.voices[lost["mistral_voice_id"]]
        self.mistral.fail[("DELETE", "/v1/audio/voices")] = 500
        with self.assertRaises(library.MistralUnavailable):
            run(library.delete(stuck["id"]))
        self.mistral.fail.clear()

        report = run(care.run_once())

        self.assertEqual(report["checked"], 2)
        self.assertEqual(report["healed"], 1)
        self.assertEqual(report["deleted"], 1)
        self.assertEqual(report["renewed"], 2)
        self.assertEqual({c["label"] for c in library.list_clones()}, {"Lost", "Fine"})
        self.assertEqual({c["status"] for c in library.list_clones()}, {"ready"})
        self.assertEqual(fine["status"], "ready")

    def test_one_round_locks_recordings_with_the_newest_key(self):
        clone = self.add("A", "en")
        with patch.dict(os.environ, {"VOICE_MASTER_KEYS": f"k2:{OTHER_MASTER},k1:{MASTER}"}):
            self.assertEqual(run(care.run_once())["rewrapped"], 1)
        with patch.dict(os.environ, {"VOICE_MASTER_KEYS": f"k2:{OTHER_MASTER}"}):
            self.assertEqual(vault.load(clone["id"]), b"sound")

    def test_a_round_without_a_master_key_still_runs_the_rest(self):
        self.add("A", "en")
        with patch.dict(os.environ, {"VOICE_MASTER_KEYS": ""}):
            report = run(care.run_once())
        self.assertEqual(report["rewrapped"], 0)
        self.assertEqual(report["checked"], 1)

    def test_the_next_run_is_the_coming_midnight_in_brussels(self):
        zone = ZoneInfo("Europe/Brussels")
        now = datetime(2026, 11, 14, 13, 30, tzinfo=zone)
        self.assertEqual(care.seconds_until_next(now, "00:00"), 10 * 3600 + 30 * 60)

    def test_just_after_the_run_time_waits_for_tomorrow(self):
        zone = ZoneInfo("Europe/Brussels")
        now = datetime(2026, 11, 14, 0, 0, 1, tzinfo=zone)
        self.assertEqual(care.seconds_until_next(now, "00:00"), 24 * 3600 - 1)
