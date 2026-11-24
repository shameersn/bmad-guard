# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for make_keys.py (T3 spike)."""

import base64
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("make_keys", HERE.parent / "make_keys.py")
mk = importlib.util.module_from_spec(spec)
sys.modules["make_keys"] = mk
spec.loader.exec_module(mk)


class SshaTests(unittest.TestCase):
    def test_hash_verifies(self):
        value = mk.ssha("pw", salt=b"12345678")
        raw = base64.b64decode(value.removeprefix("{SSHA}"))
        digest, salt = raw[:20], raw[20:]
        self.assertEqual(digest, hashlib.sha1(b"pw" + salt).digest())

    def test_random_salt(self):
        self.assertNotEqual(mk.ssha("pw"), mk.ssha("pw"))


class MakeFilesTests(unittest.TestCase):
    def test_env_created_once_with_private_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "stack-passwords"
            self.assertTrue(mk.make_env(target))
            first = target.read_text()
            self.assertFalse(mk.make_env(target))
            self.assertEqual(target.read_text(), first)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            for name in mk.PASSWORD_VARS:
                self.assertIn(f"{name}=", first)

    def test_dev_keys_kept_and_htpasswd_lists_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            mk.make_dev_keys(["dev-a"], work)
            key_a = (work / "keys" / "dev-a.key").read_text()
            htpasswd = mk.make_dev_keys(["dev-a", "dev-b"], work)
            self.assertEqual((work / "keys" / "dev-a.key").read_text(), key_a)
            users = [line.split(":", 1)[0] for line in htpasswd.read_text().splitlines()]
            self.assertEqual(users, ["dev-a", "dev-b"])
            self.assertTrue(all("{SSHA}" in line for line in htpasswd.read_text().splitlines()))
            self.assertEqual((work / "keys" / "dev-b.key").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
