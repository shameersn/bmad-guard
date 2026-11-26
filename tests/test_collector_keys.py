"""Unit tests for deploy/collector/keys.py and the committed collector files. No Docker needed."""

import importlib.util
import io
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
COLLECTOR = ROOT / "deploy" / "collector"

spec = importlib.util.spec_from_file_location("collector_keys", COLLECTOR / "keys.py")
keys = importlib.util.module_from_spec(spec)
sys.modules["collector_keys"] = keys
spec.loader.exec_module(keys)

FAKE_HASH = "$2y$10$" + "F" * 53


def fake_hasher(key: str) -> str:
    return FAKE_HASH


class IssueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "local" / "devkeys.htpasswd"

    def issue(self, user, rotate=False, hasher=fake_hasher):
        out = io.StringIO()
        keys.issue(user, path=self.path, hasher=hasher, rotate=rotate, out=out)
        lines = out.getvalue().splitlines()
        key = lines[next(i for i, x in enumerate(lines) if x.startswith("KEY for ")) + 1]
        return key, out.getvalue()

    def test_add_writes_user_and_hash_line(self):
        self.issue("dev-a")
        self.assertEqual(self.path.read_text(), f"dev-a:{FAKE_HASH}\n")

    def test_key_is_printed_once_and_never_written(self):
        key, printed = self.issue("dev-a")
        self.assertGreaterEqual(len(key), 40)
        self.assertEqual(printed.count(key), 1)
        for file in Path(self.tmp.name).rglob("*"):
            if file.is_file():
                self.assertNotIn(key, file.read_text())

    def test_key_is_random(self):
        key_1, _ = self.issue("dev-a")
        key_2, _ = self.issue("dev-b")
        self.assertNotEqual(key_1, key_2)

    def test_key_goes_to_hasher_not_the_file(self):
        seen = []
        key, _ = self.issue("dev-a", hasher=lambda k: seen.append(k) or FAKE_HASH)
        self.assertEqual(seen, [key])

    def test_add_existing_user_is_refused(self):
        self.issue("dev-a")
        with self.assertRaises(keys.KeysError):
            self.issue("dev-a")

    def test_rotate_replaces_hash_and_keeps_others(self):
        self.issue("dev-a")
        self.issue("dev-b", hasher=lambda k: "$2y$10$other")
        self.issue("dev-a", rotate=True, hasher=lambda k: "$2y$10$rotated")
        self.assertEqual(keys.read_entries(self.path), {"dev-a": "$2y$10$rotated", "dev-b": "$2y$10$other"})

    def test_rotate_unknown_user_is_refused(self):
        with self.assertRaises(keys.KeysError):
            self.issue("nobody", rotate=True)

    def test_remove(self):
        self.issue("dev-a")
        self.issue("dev-b")
        out = io.StringIO()
        keys.remove("dev-a", path=self.path, out=out)
        self.assertEqual(list(keys.read_entries(self.path)), ["dev-b"])
        self.assertIn("docker compose restart otelcol", out.getvalue())
        with self.assertRaises(keys.KeysError):
            keys.remove("dev-a", path=self.path, out=out)

    def test_reload_reminder_is_printed(self):
        _, printed = self.issue("dev-a")
        self.assertIn("docker compose restart otelcol", printed)

    def test_hasher_failure_leaves_file_unchanged(self):
        self.issue("dev-a")
        before = self.path.read_text()

        def broken(key):
            raise keys.KeysError("docker not running")

        with self.assertRaises(keys.KeysError):
            self.issue("dev-b", hasher=broken)
        self.assertEqual(self.path.read_text(), before)

    def test_bad_user_ids_are_refused(self):
        for bad in ("a:b", "Dev-A", "x", "dev a", "dev\nx", "", "../x", "a" * 65):
            with self.subTest(bad=bad), self.assertRaises(keys.KeysError):
                self.issue(bad)
        self.assertFalse(self.path.exists())

    def test_file_is_readable_by_the_collector_container(self):
        self.issue("dev-a")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)

    def test_comments_and_blank_lines_are_ignored_on_read(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(f"# note\n\ndev-a:{FAKE_HASH}\n")
        self.assertEqual(keys.read_entries(self.path), {"dev-a": FAKE_HASH})


class BcryptHashTests(unittest.TestCase):
    def run_with(self, stdout="key:$2y$10$abc\n", returncode=0):
        done = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="boom")
        with mock.patch.object(keys.subprocess, "run", return_value=done) as run:
            result = keys.bcrypt_hash("secret-key-value") if returncode == 0 else None
        return result, run

    def test_key_goes_in_on_stdin_and_not_in_the_command_line(self):
        result, run = self.run_with()
        self.assertEqual(result, "$2y$10$abc")
        args, kwargs = run.call_args
        self.assertEqual(kwargs["input"], "secret-key-value\n")
        self.assertNotIn("secret-key-value", " ".join(args[0]))
        self.assertIn("-niB", args[0])  # -B = bcrypt, -i = password on stdin
        self.assertIn(keys.HTTPD_IMAGE, args[0])

    def test_non_bcrypt_output_is_refused(self):
        with mock.patch.object(
            keys.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="key:{SHA}xx\n", stderr="")
        ):
            with self.assertRaises(keys.KeysError):
                keys.bcrypt_hash("k")

    def test_docker_failure_is_a_clean_error(self):
        with mock.patch.object(keys.subprocess, "run", side_effect=FileNotFoundError("docker")):
            with self.assertRaises(keys.KeysError):
                keys.bcrypt_hash("k")


class InitSecretsTests(unittest.TestCase):
    def test_created_once_with_private_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "secrets.local.ini"
            self.assertTrue(keys.init_secrets(target, io.StringIO()))
            first = target.read_text()
            self.assertFalse(keys.init_secrets(target, io.StringIO()))
            self.assertEqual(target.read_text(), first)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            for name in keys.PASSWORD_VARS:
                self.assertRegex(first, rf"(?m)^{name}=[A-Za-z0-9]{{20,}}$")

    def test_values_are_not_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "secrets.local.ini"
            out = io.StringIO()
            keys.init_secrets(target, out)
            value = target.read_text().splitlines()[0].split("=", 1)[1]
            self.assertNotIn(value, out.getvalue())


class CommittedFilesTests(unittest.TestCase):
    def test_every_image_has_an_exact_version_tag(self):
        text = (COLLECTOR / "stack.yml").read_text()
        images = re.findall(r"(?m)^\s+image:\s*(\S+)", text)
        self.assertEqual(len(images), 3)
        for image in images:
            self.assertRegex(image, r":\d+\.\d+\.\d+", image)
            self.assertNotIn("latest", image)
        self.assertRegex(keys.HTTPD_IMAGE, r":\d+\.\d+\.\d+")

    def test_ports_bind_to_localhost_only(self):
        text = (COLLECTOR / "stack.yml").read_text()
        ports = re.findall(r'(?m)^\s+- "([^"]+:\d+)"\s*$', text)
        self.assertTrue(ports)
        for port in ports:
            self.assertTrue(port.startswith("127.0.0.1:"), port)

    def test_example_files_hold_fake_values_only(self):
        hashes = (COLLECTOR / "devkeys.htpasswd.example").read_text()
        self.assertIn("FAKE", hashes)
        secrets_example = (COLLECTOR / "secrets.local.ini.example").read_text()
        for line in secrets_example.splitlines():
            if "=" in line and not line.startswith("#"):
                self.assertTrue(line.split("=", 1)[1].startswith("change-me"), line)

    def test_real_files_are_git_ignored(self):
        for name in ("deploy/collector/secrets.local.ini", "deploy/collector/local/devkeys.htpasswd"):
            done = subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT)
            self.assertEqual(done.returncode, 0, name)

    def test_collector_uses_bcrypt_file_and_scrubs_personal_data(self):
        config = (COLLECTOR / "otelcol" / "config.yaml").read_text()
        for needle in (
            "basicauth/server",
            "from_context: auth.username",
            "user.email",
            "user.account_uuid",
            "user.account_id",
            "create_schema: false",
        ):
            self.assertIn(needle, config)

    def test_schema_has_ttl_and_least_privilege_users(self):
        sql = (COLLECTOR / "clickhouse" / "init" / "01-schema.sh").read_text()
        self.assertIn("INTERVAL 180 DAY", sql)
        self.assertIn("GRANT INSERT ON guard.guard_events TO otel_writer", sql)
        self.assertIn("readonly = 2", sql)
        self.assertNotRegex(sql, r"GRANT (ALL|SELECT)[^\n]*otel_writer")


if __name__ == "__main__":
    unittest.main()
