# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for module/skills/agent-project-logger/scripts/guard_events.py. Fake collector, no Docker."""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "module/skills/agent-project-logger/scripts"
spec = importlib.util.spec_from_file_location("guard_events", SCRIPTS / "guard_events.py")
ge = importlib.util.module_from_spec(spec)
sys.modules["guard_events"] = ge
spec.loader.exec_module(ge)

GOOD = ("dev-a", "fake-key-a")


class FakeCollector:
    """Accepts POST /v1/logs with the GOOD basic-auth pair, else 401."""

    def __init__(self):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                expected = "Basic " + base64.b64encode(f"{GOOD[0]}:{GOOD[1]}".encode()).decode()
                if self.path != "/v1/logs" or self.headers.get("Authorization") != expected:
                    self.send_response(401)
                    self.end_headers()
                    return
                outer.requests.append(json.loads(body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def record_count(self) -> int:
        return sum(len(r["resourceLogs"][0]["scopeLogs"][0]["logRecords"]) for r in self.requests)


def unused_port_url() -> str:
    server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_port
    server.server_close()
    return f"http://127.0.0.1:{port}"


def config(tmp: str, endpoint: str = "", user: str = GOOD[0], key: str = GOOD[1]) -> ge.Config:
    """backoff=0: each call tries, as the older tests expect. BackoffTests set it."""
    return ge.Config(queue=Path(tmp) / ge.QUEUE_REL, endpoint=endpoint, user=user, key=key, timeout=1, backoff=0)


def event(n: int = 0) -> dict:
    return ge.build_event("sentry", "prd", "block", "BRIEF_NOT_APPROVED", project="demo-api", n=n)


class BuildEventTests(unittest.TestCase):
    def test_required_fields_and_extras(self):
        e = event()
        for key in ("event_id", "ts_ns", "guard", "stage", "verdict", "reason_code"):
            self.assertIn(key, e)
        self.assertEqual(e["project"], "demo-api")
        self.assertEqual(e["n"], "0")

    def test_empty_extras_are_dropped(self):
        e = ge.build_event("ledger", "", "info", "ACTION", agent="", doc_status=None, project="p")
        self.assertNotIn("agent", e)
        self.assertNotIn("doc_status", e)

    def test_rejects_unknown_verdict(self):
        with self.assertRaises(ValueError):
            ge.build_event("sentry", "prd", "maybe", "X")

    def test_event_ids_are_unique(self):
        self.assertNotEqual(event()["event_id"], event()["event_id"])


class ToOtlpTests(unittest.TestCase):
    def test_shape_and_severity(self):
        body = ge.to_otlp([event(), ge.build_event("ledger", "", "info", "ACTION")])
        recs = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        self.assertEqual([r["severityNumber"] for r in recs], [17, 9])
        self.assertEqual(recs[0]["body"], {"stringValue": "BRIEF_NOT_APPROVED"})
        self.assertEqual(recs[0]["eventName"], "guard.decision")
        self.assertNotIn("ts_ns", {a["key"] for a in recs[0]["attributes"]})
        service = body["resourceLogs"][0]["resource"]["attributes"][0]["value"]["stringValue"]
        self.assertEqual(service, "bmad-guard")


class EmitTests(unittest.TestCase):
    def test_no_collector_configured_only_queues_quietly(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            result = ge.emit(event(), config(tmp), stderr=err)
            self.assertEqual(result, {"sent": 0, "queued": 1, "error": None})
            self.assertEqual(err.getvalue(), "")
            self.assertEqual(len(config(tmp).queue.read_text().splitlines()), 1)

    def test_sends_and_empties_queue(self):
        with tempfile.TemporaryDirectory() as tmp, FakeCollector() as fc:
            result = ge.emit(event(), config(tmp, fc.url), stderr=io.StringIO())
            self.assertEqual(result, {"sent": 1, "queued": 0, "error": None})
            self.assertEqual(fc.record_count(), 1)
            self.assertEqual(config(tmp).queue.read_text(), "")

    def test_collector_down_queues_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, err = config(tmp, unused_port_url()), io.StringIO()
            for i in range(3):
                result = ge.emit(event(i), cfg, stderr=err)
            self.assertEqual(result["queued"], 3)
            self.assertIn("warning", err.getvalue())

    def test_queue_drains_when_collector_is_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(3):
                ge.emit(event(i), config(tmp, unused_port_url()), stderr=io.StringIO())
            with FakeCollector() as fc:
                result = ge.emit(None, config(tmp, fc.url), stderr=io.StringIO())
                self.assertEqual(result["sent"], 3)
                self.assertEqual(fc.record_count(), 3)

    def test_events_queued_before_collector_was_configured_are_sent_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            ge.emit(event(), config(tmp), stderr=io.StringIO())
            with FakeCollector() as fc:
                self.assertEqual(ge.emit(None, config(tmp, fc.url), stderr=io.StringIO())["sent"], 1)

    def test_bad_key_keeps_events_hints_and_never_prints_key(self):
        with tempfile.TemporaryDirectory() as tmp, FakeCollector() as fc:
            err = io.StringIO()
            result = ge.emit(event(), config(tmp, fc.url, key="wrong-key-value"), stderr=err)
            self.assertEqual(result["queued"], 1)
            self.assertIn("HTTP 401", err.getvalue())
            self.assertIn("BMAD_GUARD_KEY", err.getvalue())
            self.assertNotIn("wrong-key-value", err.getvalue())

    def test_plain_http_to_remote_host_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            result = ge.emit(event(), config(tmp, "http://collector.example.invalid"), stderr=err)
            self.assertEqual(result["queued"], 1)
            self.assertIn("must use https", err.getvalue())

    def test_unwritable_queue_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "file"
            blocker.write_text("x")
            cfg = ge.Config(queue=blocker / "sub" / "queue.jsonl")
            self.assertEqual(ge.emit(event(), cfg, stderr=io.StringIO())["queued"], -1)

    def test_torn_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp, FakeCollector() as fc:
            cfg = config(tmp, fc.url)
            cfg.queue.parent.mkdir(parents=True)
            cfg.queue.write_text('{"broken\n')
            self.assertEqual(ge.emit(event(), cfg, stderr=io.StringIO())["sent"], 1)


class BackoffTests(unittest.TestCase):
    def cfg(self, tmp: str, endpoint: str) -> ge.Config:
        return ge.Config(queue=Path(tmp) / ge.QUEUE_REL, endpoint=endpoint, user=GOOD[0], key=GOOD[1], timeout=1)

    def test_default_backoff_is_five_minutes(self):
        self.assertEqual(ge.Config(queue=Path("/q")).backoff, 300.0)

    def test_after_a_failure_later_calls_only_queue_without_trying_or_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            first = ge.emit(event(0), self.cfg(tmp, unused_port_url()), stderr=err)
            self.assertIsNotNone(first["error"])
            self.assertEqual(err.getvalue().count("warning"), 1)
            with FakeCollector() as fc:  # the collector is back, but the backoff still runs
                later = ge.emit(event(1), self.cfg(tmp, fc.url), stderr=err)
                self.assertEqual(later, {"sent": 0, "queued": 2, "error": None})
                self.assertEqual(fc.requests, [])
            self.assertEqual(err.getvalue().count("warning"), 1, "no second warning while backing off")

    def test_sending_resumes_after_the_backoff_and_clears_the_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            ge.emit(event(0), self.cfg(tmp, unused_port_url()), stderr=io.StringIO())
            marker = self.cfg(tmp, "").send_after_file
            self.assertTrue(marker.is_file())
            with FakeCollector() as fc, mock.patch.object(ge.time, "time", return_value=time.time() + 301):
                result = ge.emit(event(1), self.cfg(tmp, fc.url), stderr=io.StringIO())
            self.assertEqual(result["sent"], 2)
            self.assertFalse(marker.exists())

    def test_flush_tries_during_the_backoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            ge.emit(event(0), self.cfg(tmp, unused_port_url()), stderr=io.StringIO())
            with FakeCollector() as fc:
                result = ge.emit(None, self.cfg(tmp, fc.url), stderr=io.StringIO(), force=True)
                self.assertEqual(result["sent"], 1)

    def test_zero_backoff_writes_no_marker(self):
        """Regression: the marker time was rounded to whole seconds, so backoff=0 could still back off."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp, unused_port_url())
            with mock.patch.object(ge.time, "time", return_value=1000.6):
                ge.emit(event(), cfg, stderr=io.StringIO())
            self.assertFalse(cfg.send_after_file.exists())

    def test_the_marker_keeps_sub_second_precision(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.cfg(tmp, unused_port_url())
            with mock.patch.object(ge.time, "time", return_value=1000.6):
                ge.emit(event(), cfg, stderr=io.StringIO())
            self.assertEqual(float(cfg.send_after_file.read_text()), 1300.6)

    def test_a_broken_marker_means_no_backoff(self):
        with tempfile.TemporaryDirectory() as tmp, FakeCollector() as fc:
            cfg = self.cfg(tmp, fc.url)
            cfg.queue.parent.mkdir(parents=True)
            cfg.send_after_file.write_text("not a number\n")
            self.assertEqual(ge.emit(event(), cfg, stderr=io.StringIO())["sent"], 1)

    def test_a_failed_attempt_waits_at_most_one_timeout_for_many_events(self):
        """The point of the backoff: ten hook calls against a dead collector try once."""
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            real_send = ge.send

            def counting_send(events, config):
                calls.append(len(events))
                real_send(events, config)

            with mock.patch.object(ge, "send", counting_send):
                for i in range(10):
                    ge.emit(event(i), self.cfg(tmp, unused_port_url()), stderr=io.StringIO())
            self.assertEqual(calls, [1])


class ConfigTests(unittest.TestCase):
    def test_for_project_reads_env_and_places_queue(self):
        cfg = ge.Config.for_project(Path("/p"), {"BMAD_GUARD_ENDPOINT": "https://c", "BMAD_GUARD_USER": "u"})
        self.assertEqual(cfg.queue, Path("/p/.bmad-guard/events/queue.jsonl"))
        self.assertEqual(cfg.endpoint, "https://c")
        self.assertTrue(cfg.sending_enabled)
        self.assertFalse(ge.Config.for_project(Path("/p"), {}).sending_enabled)

    def test_old_collector_name_is_a_deprecated_alias(self):
        self.assertEqual(
            ge.Config.for_project(Path("/p"), {"BMAD_GUARD_COLLECTOR": "https://old"}).endpoint, "https://old"
        )

    def test_new_name_wins_over_the_alias(self):
        env = {"BMAD_GUARD_ENDPOINT": "https://new", "BMAD_GUARD_COLLECTOR": "https://old"}
        self.assertEqual(ge.Config.for_project(Path("/p"), env).endpoint, "https://new")


class CredentialFileTests(unittest.TestCase):
    """The credential file (ADR 0007): read order, the permission rules, and what is never printed."""

    FILE_KEY = "fake-file-secret-1%x"  # a % must survive (no interpolation)
    TEXT = f"[collector]\nendpoint = https://file.example.invalid\nuser = file-user\nkey = {FILE_KEY}\n"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))
        self.home = self.base / "home"
        self.project = self.base / "project"
        self.folder = self.home / ".config" / "bmad-guard"
        self.path = self.folder / "credentials"

    def put(self, text: str | None = None, mode: int = 0o600, folder_mode: int = 0o700) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, folder_mode)
        self.path.write_text(self.TEXT if text is None else text, encoding="utf-8")
        os.chmod(self.path, mode)
        return self.path

    def cfg(self, **env: str) -> ge.Config:
        cfg = ge.Config.for_project(self.project, {"HOME": str(self.home), **env})
        cfg.queue, cfg.timeout, cfg.backoff = self.base / "q" / "queue.jsonl", 1, 0
        return cfg

    def test_env_only(self):
        env = {"BMAD_GUARD_ENDPOINT": "https://e", "BMAD_GUARD_USER": "u", "BMAD_GUARD_KEY": "k"}
        cfg = self.cfg(**env)
        self.assertEqual((cfg.endpoint, cfg.user, cfg.key, cfg.key_source), ("https://e", "u", "k", "env"))
        self.assertEqual(cfg.credential_problem, "")

    def test_the_file_alone_gives_all_three_values(self):
        self.put()
        cfg = self.cfg()
        self.assertEqual(
            (cfg.endpoint, cfg.user, cfg.key), ("https://file.example.invalid", "file-user", self.FILE_KEY)
        )
        self.assertEqual((cfg.key_source, cfg.credential_problem), ("file", ""))
        self.assertTrue(cfg.sending_enabled)

    def test_each_value_comes_from_the_environment_first_then_the_file(self):
        self.put()
        cfg = self.cfg(BMAD_GUARD_USER="env-user")
        self.assertEqual((cfg.endpoint, cfg.user, cfg.key), ("https://file.example.invalid", "env-user", self.FILE_KEY))
        cfg = self.cfg(BMAD_GUARD_KEY="env-secret")
        self.assertEqual((cfg.user, cfg.key, cfg.key_source), ("file-user", "env-secret", "env"))
        cfg = self.cfg(BMAD_GUARD_COLLECTOR="https://old")  # the deprecated alias still wins over the file
        self.assertEqual(cfg.endpoint, "https://old")

    def test_the_file_is_not_read_when_the_environment_has_everything(self):
        env = {"BMAD_GUARD_ENDPOINT": "https://e", "BMAD_GUARD_USER": "u", "BMAD_GUARD_KEY": "k"}
        with mock.patch.object(ge, "read_credential_file", side_effect=AssertionError("read"), create=True):
            self.cfg(**env)

    def test_neither_env_nor_file_only_queues_without_a_message(self):
        cfg, err = self.cfg(), io.StringIO()
        self.assertEqual((cfg.endpoint, cfg.credential_problem), ("", ""))
        self.assertEqual(ge.emit(event(), cfg, stderr=err), {"sent": 0, "queued": 1, "error": None})
        self.assertEqual(err.getvalue(), "")

    def test_env_passed_without_home_skips_the_file(self):
        self.put()
        self.assertFalse(ge.Config.for_project(self.project, {}).sending_enabled)

    def test_no_env_argument_uses_the_process_environment(self):
        self.put()
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            self.assertEqual(ge.Config.for_project(self.project).user, "file-user")

    def test_an_explicit_file_path_wins_over_home(self):
        self.put()
        cfg = ge.Config.for_project(self.project, {}, credentials_file=self.path)
        self.assertEqual(cfg.user, "file-user")

    def test_a_file_open_to_group_or_others_is_refused_with_the_fix(self):
        for mode in (0o640, 0o604, 0o644, 0o660):
            with self.subTest(mode=oct(mode)):
                self.put(mode=mode)
                cfg = self.cfg()
                self.assertEqual((cfg.endpoint, cfg.user, cfg.key), ("", "", ""))
                self.assertFalse(cfg.sending_enabled)
                self.assertIn(f"{mode:03o}", cfg.credential_problem)
                self.assertIn("chmod 600", cfg.credential_problem)
                self.assertNotIn(self.FILE_KEY, cfg.credential_problem)

    def test_a_folder_that_group_or_others_can_write_is_refused(self):
        self.put(folder_mode=0o770)
        cfg = self.cfg()
        self.assertFalse(cfg.sending_enabled)
        self.assertIn("chmod 700", cfg.credential_problem)

    def test_a_symbolic_link_is_refused(self):
        self.put()
        real = self.base / "real"
        self.path.rename(real)
        self.path.symlink_to(real)
        cfg = self.cfg()
        self.assertFalse(cfg.sending_enabled)
        self.assertIn("symbolic link", cfg.credential_problem)

    def test_a_file_owned_by_someone_else_is_refused(self):
        self.put()  # a real chown needs root, so the current user id is faked instead
        with mock.patch.object(ge.os, "geteuid", return_value=os.stat(self.path).st_uid + 1):
            cfg = self.cfg()
        self.assertFalse(cfg.sending_enabled)
        self.assertIn("not owned by you", cfg.credential_problem)

    def test_something_that_is_not_a_regular_file_is_refused(self):
        self.folder.mkdir(parents=True)
        os.chmod(self.folder, 0o700)
        self.path.mkdir()
        self.assertIn("not a regular file", self.cfg().credential_problem)

    def test_a_missing_file_is_not_a_problem(self):
        self.assertEqual(ge.read_credential_file(self.path), ({}, ""))
        self.assertEqual(self.cfg().credential_problem, "")

    def test_a_parse_error_names_the_error_type_and_never_the_content(self):
        self.put("key = SECRET-LINE-VALUE\n")  # no section header: the library's message quotes this line
        cfg = self.cfg()
        self.assertFalse(cfg.sending_enabled)
        self.assertIn("MissingSectionHeaderError", cfg.credential_problem)
        self.assertNotIn("SECRET-LINE-VALUE", cfg.credential_problem)

    def test_unknown_keys_and_a_missing_section_are_ignored(self):
        self.put("[other]\nkey = x\n[collector]\nendpoint = https://e\nextra = 1\n")
        cfg = self.cfg()
        self.assertEqual((cfg.endpoint, cfg.user, cfg.key, cfg.credential_problem), ("https://e", "", "", ""))
        self.put("[other]\nkey = x\n")
        self.assertFalse(self.cfg().sending_enabled)

    def test_the_401_hint_names_the_file_when_the_key_came_from_it(self):
        with FakeCollector() as fc:  # the file's key is not the collector's key, so the answer is 401
            self.put(self.TEXT.replace("https://file.example.invalid", fc.url))
            cfg, err = self.cfg(), io.StringIO()
            result = ge.emit(event(), cfg, stderr=err)
        self.assertEqual(result["queued"], 1)
        self.assertIn("HTTP 401", err.getvalue())
        self.assertIn("the key is from the credential file", err.getvalue())
        self.assertNotIn(self.FILE_KEY, err.getvalue())

    def test_the_401_hint_names_the_variable_when_the_key_came_from_the_environment(self):
        with FakeCollector() as fc:
            cfg = self.cfg(BMAD_GUARD_ENDPOINT=fc.url, BMAD_GUARD_USER="u", BMAD_GUARD_KEY="env-wrong-secret")
            err = io.StringIO()
            ge.emit(event(), cfg, stderr=err)
        self.assertIn("the key is from BMAD_GUARD_KEY", err.getvalue())
        self.assertNotIn("env-wrong-secret", err.getvalue())

    def test_flush_puts_the_reason_in_the_error_field(self):
        self.put(mode=0o644)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), contextlib.redirect_stdout(out):
            self.assertEqual(ge.main(["flush", "--project-root", str(self.project)]), 0)
        result = json.loads(out.getvalue())
        self.assertIn("644", result["error"])
        self.assertNotIn(self.FILE_KEY, out.getvalue())

    def test_an_endpoint_from_the_environment_with_a_refused_file_says_why_it_cannot_send(self):
        self.put(mode=0o644)
        cfg, err = self.cfg(BMAD_GUARD_ENDPOINT="https://e.example.invalid"), io.StringIO()
        result = ge.emit(event(), cfg, stderr=err)
        self.assertEqual(result["queued"], 1)
        self.assertIn("chmod 600", err.getvalue())


REPARSE = ge.stat.FILE_ATTRIBUTE_REPARSE_POINT


class WindowsCredentialFileTests(CredentialFileTests):
    """ADR 0009: on Windows there are no mode bits, so the sender checks the type and links instead. A simulation on
    this OS (`IS_WINDOWS` set, a fake `st_file_attributes`); not run on Windows. Inherits the setUp and helpers."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(ge, "IS_WINDOWS", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cfg(self, **env: str) -> ge.Config:
        return super().cfg(USERPROFILE=str(self.home), **env)  # on Windows the home folder is USERPROFILE

    def reparse(self, *paths: Path):
        """os.lstat answers with the reparse-point flag for these paths (a link or junction on Windows)."""
        real = os.lstat
        flagged = {os.fspath(p) for p in paths}

        def lstat(path, *args, **kwargs):
            if os.fspath(path) in flagged:
                return mock.Mock(st_file_attributes=REPARSE)
            return real(path, *args, **kwargs)

        return mock.patch.object(ge.os, "lstat", lstat)

    def test_a_file_that_posix_would_refuse_is_used_because_windows_has_no_mode_bits(self):
        self.put(mode=0o666, folder_mode=0o777)  # what a Windows file looks like to os.stat
        cfg = self.cfg()
        self.assertEqual(
            (cfg.endpoint, cfg.user, cfg.key, cfg.credential_problem),
            (
                "https://file.example.invalid",
                "file-user",
                self.FILE_KEY,
                "",
            ),
        )
        self.assertTrue(cfg.sending_enabled)

    def test_a_file_owned_by_someone_else_is_not_checked_here(self):
        self.put()
        with mock.patch.object(ge.os, "geteuid", return_value=os.stat(self.path).st_uid + 1, create=True):
            self.assertEqual(self.cfg().credential_problem, "")

    def test_a_link_or_junction_file_is_refused(self):
        self.put()
        with self.reparse(self.path):
            cfg = self.cfg()
        self.assertFalse(cfg.sending_enabled)
        self.assertIn("link or junction", cfg.credential_problem)

    def test_a_link_or_junction_folder_is_refused(self):
        self.put()
        with self.reparse(self.folder):
            cfg = self.cfg()
        self.assertFalse(cfg.sending_enabled)
        self.assertIn("folder", cfg.credential_problem)
        self.assertIn("link or junction", cfg.credential_problem)

    def test_a_file_that_cannot_be_checked_is_refused_with_the_error_type_only(self):
        self.put()

        def lstat(path, *args, **kwargs):
            raise PermissionError(f"secret detail about {path}")

        with mock.patch.object(ge.os, "lstat", lstat):
            problem = ge.read_credential_file(self.path)[1]
        self.assertIn("PermissionError", problem)
        self.assertNotIn("secret detail", problem)

    def test_the_content_never_appears_in_a_problem(self):
        self.put("key = SECRET-LINE-VALUE\n")
        self.assertNotIn("SECRET-LINE-VALUE", self.cfg().credential_problem)

    def test_the_home_folder_is_userprofile_not_home(self):
        env = {"HOME": "/c/Users/other", "USERPROFILE": str(self.home)}
        self.assertEqual(ge.credentials_path(env), self.path)
        self.assertIsNone(ge.credentials_path({"HOME": "/c/Users/other"}))
        with (
            mock.patch.dict(os.environ, {"HOME": "/c/Users/other"}),
            mock.patch.object(ge.Path, "home", return_value=self.home),
        ):
            self.assertEqual(ge.credentials_path(), self.path)

    # The POSIX-only rules do not apply on Windows: skip their tests here.
    test_a_file_open_to_group_or_others_is_refused_with_the_fix = None
    test_a_folder_that_group_or_others_can_write_is_refused = None
    test_a_file_owned_by_someone_else_is_refused = None
    test_flush_puts_the_reason_in_the_error_field = None
    test_an_endpoint_from_the_environment_with_a_refused_file_says_why_it_cannot_send = None
    test_a_symbolic_link_is_refused = None  # on Windows a link is a reparse point: the tests above


class PosixCredentialPathTests(unittest.TestCase):
    def test_home_wins_and_userprofile_is_ignored_off_windows(self):
        env = {"HOME": "/h", "USERPROFILE": "/u"}
        self.assertEqual(ge.credentials_path(env), Path("/h") / ge.CREDENTIALS_REL)
        self.assertEqual(ge.credentials_path({"USERPROFILE": "/u"}), None)


class NoLockQueueTests(unittest.TestCase):
    """ADR 0009: with no `fcntl` (Windows) `emit()` only appends one line; `flush` sends. Simulated: `fcntl = None`."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        patcher = mock.patch.object(ge, "fcntl", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def lines(self, cfg: ge.Config) -> list[dict]:
        return [json.loads(line) for line in cfg.queue.read_text(encoding="utf-8").splitlines()]

    def test_emit_with_an_event_only_appends_and_never_sends(self):
        with FakeCollector() as fc:
            cfg = config(self.tmp, fc.url)
            for n in range(3):
                result = ge.emit(event(n), cfg)
                self.assertEqual((result["sent"], result["error"]), (0, None))
            self.assertEqual(fc.record_count(), 0)
        self.assertEqual([e["n"] for e in self.lines(cfg)], ["0", "1", "2"])

    def test_emit_with_an_event_does_not_even_read_the_queue(self):
        cfg = config(self.tmp, "http://127.0.0.1:1")
        ge.emit(event(), cfg)
        with (
            mock.patch.object(ge, "_read_queue", side_effect=AssertionError("read")),
            mock.patch.object(ge, "send", side_effect=AssertionError("sent")),
        ):
            ge.emit(event(1), cfg)
        self.assertEqual(len(self.lines(cfg)), 2)

    def test_flush_sends_the_queue_and_empties_it(self):
        with FakeCollector() as fc:
            cfg = config(self.tmp, fc.url)
            ge.emit(event(0), cfg)
            ge.emit(event(1), cfg)
            result = ge.emit(None, cfg)
            self.assertEqual((result["sent"], result["queued"], result["error"]), (2, 0, None))
            self.assertEqual(fc.record_count(), 2)
        self.assertEqual(cfg.queue.read_text(encoding="utf-8"), "")

    def test_a_line_appended_while_flush_sends_stays_in_the_queue(self):
        cfg = config(self.tmp, "http://127.0.0.1:1")
        ge.emit(event(0), cfg)
        late = event(9)

        def send_and_meanwhile_append(events, _config):
            with open(cfg.queue, "a", encoding="utf-8") as handle:  # another hook process, no lock
                handle.write(json.dumps(late) + "\n")

        with mock.patch.object(ge, "send", send_and_meanwhile_append):
            result = ge.emit(None, cfg)
        self.assertEqual(result["sent"], 1)
        self.assertEqual([e["n"] for e in self.lines(cfg)], ["9"])

    def test_a_failed_flush_keeps_everything(self):
        cfg = config(self.tmp, unused_port_url())
        ge.emit(event(0), cfg)
        err = io.StringIO()
        result = ge.emit(None, cfg, stderr=err, force=True)
        self.assertEqual((result["sent"], result["queued"]), (0, 1))
        self.assertEqual(len(self.lines(cfg)), 1)

    def test_the_cli_flush_sends_without_a_lock(self):
        with FakeCollector() as fc:
            cfg = config(self.tmp, fc.url)
            ge.emit(event(), cfg)
            env = {"BMAD_GUARD_ENDPOINT": fc.url, "BMAD_GUARD_USER": GOOD[0], "BMAD_GUARD_KEY": GOOD[1]}
            out = io.StringIO()
            project = Path(self.tmp)
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
                self.assertEqual(ge.main(["flush", "--project-root", str(project)]), 0)
            self.assertEqual(json.loads(out.getvalue())["sent"], 1)
            self.assertEqual(fc.record_count(), 1)


if __name__ == "__main__":
    unittest.main()
