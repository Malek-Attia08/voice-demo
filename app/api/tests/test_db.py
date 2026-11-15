"""The database file: made on first use, with its folder."""
import os
import tempfile
from unittest.mock import patch

from api import db
from api.tests.support import DemoCase


class DbTests(DemoCase):
    def test_the_folder_of_the_database_is_made_when_it_is_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "not", "yet", "demo.sqlite3")
            with patch.dict(os.environ, {"DEMO_DB_PATH": path}):
                self.assertEqual(db.many("SELECT * FROM voice_clones"), [])
            self.assertTrue(os.path.exists(path))

    def test_deleting_a_clone_deletes_its_recording_with_it(self):
        db.run("INSERT INTO voice_clones (id, label, language, speaker_name, consented_at, consent_wording) "
               "VALUES ('c1', 'A', 'en', 'S', 'now', 'x')")
        db.run("INSERT INTO voice_recordings (clone_id, ciphertext, nonce, wrapped_key) VALUES ('c1', x'00', x'00', x'00')")
        db.run("DELETE FROM voice_clones WHERE id = 'c1'")
        self.assertEqual(db.many("SELECT * FROM voice_recordings"), [])

    def test_only_one_clone_per_language_can_be_active(self):
        for clone_id in ("a", "b"):
            db.run("INSERT INTO voice_clones (id, label, language, speaker_name, consented_at, consent_wording) "
                   "VALUES (?, 'X', 'en', 'S', 'now', 'x')", (clone_id,))
        db.run("UPDATE voice_clones SET active = 1 WHERE id = 'a'")
        import sqlite3

        with self.assertRaises(sqlite3.IntegrityError):
            db.run("UPDATE voice_clones SET active = 1 WHERE id = 'b'")
