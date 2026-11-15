"""The clone library: consent, one active clone per language, Mistral kept in step, lost voices made again."""
import asyncio
import os
from unittest.mock import patch

from api import db, library, vault
from api.tests.support import DemoCase, FakeMistral

CONSENT = dict(speaker_name="Sarah Peeters", consent=True)


def run(coroutine):
    return asyncio.run(coroutine)


class LibraryCase(DemoCase):
    def setUp(self):
        super().setUp()
        self.mistral = FakeMistral().patch(self)

    def add(self, label="Sarah", language="en", filename="sample.wav", sample=b"RIFF-sound", **extra):
        return run(library.add(label, sample, filename, language=language, **{**CONSENT, **extra}))


class AddTests(LibraryCase):
    def test_a_clone_is_made_at_mistral_and_kept_ready_with_its_consent(self):
        clone = self.add(language="nl-BE", reading_check="heard")
        self.assertEqual(clone["status"], "ready")
        self.assertEqual(clone["mistral_voice_id"], "voice-1")
        self.assertEqual(clone["speaker_name"], "Sarah Peeters")
        self.assertEqual(clone["consent_wording"], library.CONSENT_WORDING)
        self.assertEqual(clone["language"], "nl-be")
        self.assertEqual(clone["reading_check"], "heard")

    def test_mistral_is_told_the_base_language_the_name_and_the_retention(self):
        clone = self.add(language="nl-BE")
        (_, _, body), = self.mistral.calls("POST", "/v1/audio/voices")
        self.assertEqual(body["languages"], ["nl"])
        self.assertEqual(body["name"], f"voicedemo:{clone['id']}")
        self.assertEqual(body["retention_notice"], 365)
        self.assertEqual(body["sample_filename"], "sample.wav")

    def test_the_recording_is_kept_encrypted(self):
        clone = self.add()
        self.assertEqual(vault.load(clone["id"]), b"RIFF-sound")

    def test_no_consent_makes_nothing(self):
        with self.assertRaises(library.ConsentRequired):
            run(library.add("Sarah", b"x", "a.wav", language="en", speaker_name="Sarah", consent=False))
        with self.assertRaises(library.ConsentRequired):
            run(library.add("Sarah", b"x", "a.wav", language="en", speaker_name="  ", consent=True))
        self.assertEqual(self.mistral.created, 0)
        self.assertEqual(library.list_clones(), [])

    def test_only_mp3_and_wav_are_accepted(self):
        with self.assertRaises(library.UnsupportedSample):
            self.add(filename="sample.m4a")

    def test_an_empty_recording_and_a_missing_language_are_refused(self):
        with self.assertRaises(library.CloneError):
            self.add(sample=b"")
        with self.assertRaises(library.VoiceLibraryError):
            self.add(language="")

    def test_a_voice_mistral_refuses_leaves_no_row_and_no_recording(self):
        self.mistral.fail[("POST", "/v1/audio/voices")] = 400
        with self.assertRaises(library.CloneError):
            self.add()
        self.assertEqual(library.list_clones(), [])
        self.assertEqual(db.many("SELECT * FROM voice_recordings"), [])

    def test_without_a_master_key_no_voice_is_made(self):
        with patch.dict(os.environ, {"VOICE_MASTER_KEYS": ""}):
            with self.assertRaises(library.VoiceLibraryError):
                self.add()
        self.assertEqual(library.list_clones(), [])

    def test_without_a_mistral_key_cloning_is_not_set_up(self):
        with patch.dict(os.environ, {"MISTRAL_API_KEY": ""}):
            with self.assertRaises(library.CloneError):
                self.add()


class ActiveVoiceTests(LibraryCase):
    def test_nothing_is_active_until_activated(self):
        self.add()
        self.assertIsNone(library.active_voice_id("en"))

    def test_the_clone_of_the_exact_language_wins(self):
        nl, be = self.add("NL", "nl-nl"), self.add("BE", "be-nl")
        library.activate(nl["id"])
        library.activate(be["id"])
        self.assertEqual(library.active_voice_id("be-nl"), be["mistral_voice_id"])
        self.assertEqual(library.active_voice_id("nl-nl"), nl["mistral_voice_id"])

    def test_a_clone_of_the_same_base_language_serves_a_language_without_its_own(self):
        be = self.add("BE", "be-nl")
        library.activate(be["id"])
        self.assertEqual(library.active_voice_id("nl-nl"), be["mistral_voice_id"])

    def test_a_language_without_a_clone_gets_the_oldest_active_one(self):
        en, fr = self.add("EN", "en"), self.add("FR", "fr")
        library.activate(fr["id"])
        library.activate(en["id"])
        self.assertEqual(library.active_voice_id("de"), en["mistral_voice_id"])
        self.assertEqual(library.active_voice_id(None), en["mistral_voice_id"])

    def test_activating_a_clone_stops_the_one_speaking_its_language(self):
        first, second = self.add("A", "en"), self.add("B", "en")
        library.activate(first["id"])
        library.activate(second["id"])
        active = [c for c in library.list_clones() if c["active"]]
        self.assertEqual([c["id"] for c in active], [second["id"]])

    def test_several_languages_can_speak_at_once(self):
        en, fr = self.add("EN", "en"), self.add("FR", "fr")
        library.activate(en["id"])
        library.activate(fr["id"])
        self.assertEqual(len([c for c in library.list_clones() if c["active"]]), 2)

    def test_deactivating_stops_it_speaking(self):
        clone = self.add()
        library.activate(clone["id"])
        library.deactivate(clone["id"])
        self.assertIsNone(library.active_voice_id("en"))

    def test_a_clone_that_is_not_ready_cannot_be_activated(self):
        clone = self.add()
        db.run("UPDATE voice_clones SET status = 'gone' WHERE id = ?", (clone["id"],))
        with self.assertRaises(library.VoiceLibraryError):
            library.activate(clone["id"])

    def test_an_unknown_clone_is_not_found(self):
        with self.assertRaises(library.NotFound):
            library.activate("nope")


class DeleteTests(LibraryCase):
    def test_a_clone_is_deleted_at_mistral_first_then_here_with_its_recording(self):
        clone = self.add()
        run(library.delete(clone["id"]))
        self.assertEqual(self.mistral.calls("DELETE", "/v1/audio/voices/voice-1")[0][1], "/v1/audio/voices/voice-1")
        self.assertEqual(library.list_clones(), [])
        self.assertEqual(db.many("SELECT * FROM voice_recordings"), [])

    def test_a_voice_mistral_does_not_have_counts_as_deleted(self):
        clone = self.add()
        self.mistral.voices.clear()
        run(library.delete(clone["id"]))
        self.assertEqual(library.list_clones(), [])

    def test_when_mistral_cannot_delete_the_row_is_kept_as_deleting_and_retried(self):
        clone = self.add()
        library.activate(clone["id"])
        self.mistral.fail[("DELETE", "/v1/audio/voices")] = 500
        with self.assertRaises(library.MistralUnavailable):
            run(library.delete(clone["id"]))
        kept = library.list_clones()
        self.assertEqual([c["status"] for c in kept], ["deleting"])
        self.assertIsNone(library.active_voice_id("en"))
        self.mistral.fail.clear()
        self.assertEqual(run(library.retry_pending_deletes()), 1)
        self.assertEqual(library.list_clones(), [])


class ReplaceTests(LibraryCase):
    def test_replace_makes_the_new_one_speak_and_deletes_the_old(self):
        old = self.add("Old", "en")
        library.activate(old["id"])
        new = run(library.replace(old["id"], b"new-sound", "new.wav", **CONSENT))
        self.assertEqual([c["id"] for c in library.list_clones()], [new["id"]])
        self.assertEqual(library.active_voice_id("en"), new["mistral_voice_id"])
        self.assertEqual(new["label"], "Old")


class HealTests(LibraryCase):
    def test_check_reports_a_present_voice(self):
        clone = self.add()
        self.assertTrue(run(library.check(clone["id"])))

    def test_a_voice_mistral_lost_is_marked_gone_and_stops_speaking(self):
        clone = self.add()
        library.activate(clone["id"])
        self.mistral.voices.clear()
        self.assertFalse(run(library.check(clone["id"])))
        self.assertEqual(library.list_clones()[0]["status"], "gone")
        self.assertIsNone(library.active_voice_id("en"))

    def test_a_gone_voice_is_made_again_from_the_kept_recording(self):
        clone = self.add()
        self.mistral.voices.clear()
        run(library.check(clone["id"]))
        healed = run(library.heal(clone["id"]))
        self.assertEqual(healed["status"], "ready")
        self.assertEqual(healed["mistral_voice_id"], "voice-2")
        self.assertEqual(self.mistral.voices["voice-2"]["sample_filename"], "sample.wav")

    def test_a_voice_that_cannot_be_made_again_goes_back_to_gone(self):
        clone = self.add()
        db.run("UPDATE voice_clones SET status = 'gone' WHERE id = ?", (clone["id"],))
        self.mistral.fail[("POST", "/v1/audio/voices")] = 500
        with self.assertRaises(library.CloneError):
            run(library.heal(clone["id"]))
        self.assertEqual(library.list_clones()[0]["status"], "gone")

    def test_a_voice_without_a_kept_recording_asks_to_record_again(self):
        clone = self.add()
        db.run("UPDATE voice_clones SET status = 'gone' WHERE id = ?", (clone["id"],))
        vault.destroy(clone["id"])
        with self.assertRaises(library.VoiceLibraryError):
            run(library.heal(clone["id"]))
        self.assertEqual(library.list_clones()[0]["status"], "gone")

    def test_the_daily_check_makes_lost_voices_again(self):
        keep, lose = self.add("Keep", "en"), self.add("Lose", "fr")
        del self.mistral.voices[lose["mistral_voice_id"]]
        report = run(library.check_all())
        self.assertEqual(report, {"checked": 2, "lost": 1, "healed": 1})
        self.assertEqual({c["status"] for c in library.list_clones()}, {"ready"})
        self.assertEqual(keep["status"], "ready")

    def test_renew_retention_tells_mistral_again(self):
        self.add()
        self.assertEqual(run(library.renew_retention()), 1)
        self.assertEqual(self.mistral.calls("PATCH", "/v1/audio/voices/voice-1")[0][2], {"retention_notice": 365})

    def test_retention_days_must_be_a_whole_number_from_1_to_365(self):
        clone = self.add()
        for bad in (0, 366, "30", True, 1.5):
            with self.assertRaises(ValueError):
                run(library.set_retention(clone["id"], bad))


class SpeakTests(LibraryCase):
    def test_no_active_clone_means_no_audio(self):
        self.add()
        self.assertIsNone(run(library.speak("Hello there.", "en")))

    def test_an_active_clone_speaks_the_text_as_mp3(self):
        clone = self.add()
        library.activate(clone["id"])
        self.assertEqual(run(library.speak("Hello there.", "en")), b"MP3-AUDIO")
        (_, _, body), = self.mistral.calls("POST", "/v1/audio/speech")
        self.assertEqual(body["voice_id"], "voice-1")
        self.assertEqual(body["response_format"], "mp3")
        self.assertEqual(body["input"], "[pace=moderate]Hello there.")
        self.assertEqual(body["expressiveness"], 3.0)

    def test_a_voice_mistral_lost_makes_the_call_fall_back_and_is_made_again(self):
        clone = self.add()
        library.activate(clone["id"])
        self.mistral.voices.clear()

        async def scenario():
            audio = await library.speak("Hello.", "en")
            await asyncio.gather(*library.background_tasks())
            return audio

        self.assertIsNone(run(scenario()))
        self.assertEqual(library.list_clones()[0]["status"], "ready")
        self.assertEqual(library.list_clones()[0]["mistral_voice_id"], "voice-2")


class VerifiedVoiceTests(LibraryCase):
    """The voice a call may be promised: the active one, checked at Mistral just now."""

    def test_an_active_voice_mistral_still_has_is_returned(self):
        clone = self.add()
        library.activate(clone["id"])
        self.assertEqual(run(library.verified_voice_id("en")), clone["mistral_voice_id"])

    def test_no_active_voice_gives_none_without_asking_mistral(self):
        self.add()
        self.assertIsNone(run(library.verified_voice_id("en")))
        self.assertEqual(self.mistral.calls("GET", "/v1/audio/voices"), [])

    def test_a_voice_mistral_lost_gives_none_is_marked_gone_and_is_made_again_in_the_background(self):
        clone = self.add()
        library.activate(clone["id"])
        self.mistral.voices.clear()

        async def scenario():
            found = await library.verified_voice_id("en")
            await asyncio.gather(*library.background_tasks())
            return found

        self.assertIsNone(run(scenario()))
        self.assertEqual(library.list_clones()[0]["status"], "ready")
        self.assertEqual(library.list_clones()[0]["mistral_voice_id"], "voice-2")

    def test_a_mistral_that_cannot_say_gives_none_and_leaves_the_voice_alone(self):
        clone = self.add()
        library.activate(clone["id"])
        self.mistral.fail[("GET", "/v1/audio/voices")] = 500
        self.assertIsNone(run(library.verified_voice_id("en")))
        self.assertEqual(library.list_clones()[0]["status"], "ready")
