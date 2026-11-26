# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for module/skills/agent-project-logger/scripts/guard_events.py. Fake collector, no Docker."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
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


if __name__ == "__main__":
    unittest.main()
