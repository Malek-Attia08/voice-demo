"""make_master_key: makes the vault's master key in .env without ever printing it (port of make_voice_master_key)."""
import base64
import contextlib
import io
import os
import tempfile
import unittest

from api import make_master_key


def keys_in(path):
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("VOICE_MASTER_KEYS="):
                return [entry.partition(":") for entry in line.strip().partition("=")[2].split(",")]
    return []


class MakeMasterKeyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, ".env")

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_a_missing_env_file_is_created_with_a_valid_key(self):
        make_master_key.make_key(self.path)
        (key_id, _, encoded), = keys_in(self.path)
        self.assertTrue(key_id)
        self.assertEqual(len(base64.b64decode(encoded)), 32)

    def test_the_other_settings_stay_as_they_were(self):
        self.write("GOOGLE_API_KEY=abc\nVOICE_MASTER_KEYS=\nMISTRAL_API_KEY=def\n")
        make_master_key.make_key(self.path)
        text = open(self.path, encoding="utf-8").read()
        self.assertIn("GOOGLE_API_KEY=abc\n", text)
        self.assertIn("MISTRAL_API_KEY=def\n", text)
        self.assertEqual(len(keys_in(self.path)), 1)

    def test_an_existing_key_is_never_replaced_without_rotate(self):
        self.write("VOICE_MASTER_KEYS=k1:" + base64.b64encode(bytes(32)).decode() + "\n")
        with self.assertRaises(make_master_key.KeyExists):
            make_master_key.make_key(self.path)
        self.assertEqual([entry[0] for entry in keys_in(self.path)], ["k1"])

    def test_rotate_puts_the_new_key_first_and_keeps_the_old_one(self):
        self.write("VOICE_MASTER_KEYS=k1:" + base64.b64encode(bytes(32)).decode() + "\n")
        new_id = make_master_key.make_key(self.path, rotate=True)
        ids = [entry[0] for entry in keys_in(self.path)]
        self.assertEqual(ids, [new_id, "k1"])
        self.assertNotEqual(new_id, "k1")

    def test_the_key_is_never_printed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            make_master_key.main([self.path])
        (_, _, encoded), = keys_in(self.path)
        self.assertNotIn(encoded, out.getvalue())
        self.assertIn("VOICE_MASTER_KEYS", out.getvalue())

    def test_main_reports_an_existing_key_instead_of_crashing(self):
        make_master_key.make_key(self.path)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(make_master_key.main([self.path]), 1)
        self.assertIn("--rotate", out.getvalue())
