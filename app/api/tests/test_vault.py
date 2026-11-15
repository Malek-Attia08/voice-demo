"""The vault keeps recordings encrypted, bound to their clone, under rotating master keys."""
import os
from unittest.mock import patch

from api import db, vault
from api.tests.support import MASTER, OTHER_MASTER, DemoCase


def make_clone(clone_id="c1"):
    db.run("INSERT INTO voice_clones (id, label, language, speaker_name, consented_at, consent_wording) "
           "VALUES (?, 'Sarah', 'en', 'Sarah', 'now', 'v3')", (clone_id,))


class VaultTests(DemoCase):
    def test_a_stored_recording_comes_back_unchanged(self):
        make_clone()
        vault.store("c1", b"the sound", "sample.wav")
        self.assertEqual(vault.load("c1"), b"the sound")

    def test_the_database_never_holds_the_sound_in_the_clear(self):
        make_clone()
        vault.store("c1", b"the sound of my voice", "sample.wav")
        row = db.one("SELECT * FROM voice_recordings WHERE clone_id = 'c1'")
        self.assertNotIn(b"the sound", bytes(row["ciphertext"]))
        self.assertNotIn(b"the sound", bytes(row["wrapped_key"]))

    def test_a_recording_moved_to_another_clone_does_not_open(self):
        make_clone("c1")
        make_clone("c2")
        vault.store("c1", b"secret", "a.wav")
        row = db.one("SELECT * FROM voice_recordings WHERE clone_id = 'c1'")
        db.run("INSERT INTO voice_recordings (clone_id, ciphertext, nonce, wrapped_key, filename) VALUES ('c2', ?, ?, ?, 'a.wav')",
               (row["ciphertext"], row["nonce"], row["wrapped_key"]))
        with self.assertRaises(vault.VaultError):
            vault.load("c2")

    def test_a_changed_recording_is_refused(self):
        make_clone()
        vault.store("c1", b"secret", "a.wav")
        db.run("UPDATE voice_recordings SET ciphertext = ? WHERE clone_id = 'c1'", (b"x" * 40,))
        with self.assertRaises(vault.VaultError):
            vault.load("c1")

    def test_a_missing_recording_is_reported(self):
        make_clone()
        with self.assertRaises(vault.VaultError):
            vault.load("c1")

    def test_without_a_master_key_nothing_is_kept(self):
        make_clone()
        with patch.dict(os.environ, {"VOICE_MASTER_KEYS": ""}):
            with self.assertRaises(vault.VaultNotConfigured):
                vault.store("c1", b"secret", "a.wav")

    def test_a_malformed_master_key_is_refused(self):
        make_clone()
        with patch.dict(os.environ, {"VOICE_MASTER_KEYS": "k1:AAAA"}):
            with self.assertRaises(vault.VaultNotConfigured):
                vault.store("c1", b"secret", "a.wav")

    def test_destroy_removes_the_recording(self):
        make_clone()
        vault.store("c1", b"secret", "a.wav")
        vault.destroy("c1")
        with self.assertRaises(vault.VaultError):
            vault.load("c1")

    def test_rotation_locks_old_recordings_with_the_newest_key_and_they_still_open(self):
        make_clone()
        vault.store("c1", b"secret", "a.wav")
        both = f"k2:{OTHER_MASTER},k1:{MASTER}"
        with patch.dict(os.environ, {"VOICE_MASTER_KEYS": both}):
            self.assertEqual(vault.rewrap_all(), 1)
            self.assertEqual(vault.rewrap_all(), 0)
        with patch.dict(os.environ, {"VOICE_MASTER_KEYS": f"k2:{OTHER_MASTER}"}):
            self.assertEqual(vault.load("c1"), b"secret")

    def test_a_retired_key_opens_nothing(self):
        make_clone()
        vault.store("c1", b"secret", "a.wav")
        with patch.dict(os.environ, {"VOICE_MASTER_KEYS": f"k2:{OTHER_MASTER}"}):
            with self.assertRaises(vault.VaultError):
                vault.load("c1")
