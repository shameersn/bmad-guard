# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for guard_events.py (T3 spike). Uses a fake local collector; no Docker needed."""

import base64
import importlib.util
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("guard_events", HERE.parent / "guard_events.py")
ge = importlib.util.module_from_spec(spec)
sys.modules["guard_events"] = ge
spec.loader.exec_module(ge)

GOOD = ("dev-a", "fake-key-a")


class FakeCollector:
    """Accepts POST /v1/logs with the GOOD basic-auth pair, else 401."""

    def __init__(self):
        self.requests = []
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
                self.send_header("Content-Type", "application/json")
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

    def record_count(self):
        return sum(len(r["resourceLogs"][0]["scopeLogs"][0]["logRecords"]) for r in self.requests)


def config(tmp, endpoint, user=GOOD[0], key=GOOD[1]):
    return ge.Config(endpoint=endpoint, user=user, key=key, queue=Path(tmp) / "q" / "queue.jsonl", timeout=1)


def event(n=0):
    return ge.build_event("sentry", "prd", "block", "BRIEF_NOT_APPROVED", project="demo-api", n=str(n))


def unused_port_url():
    server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_port
    server.server_close()
    return f"http://127.0.0.1:{port}"


class BuildEventTests(unittest.TestCase):
    def test_has_required_fields(self):
        e = event()
        for key in ("event_id", "ts_ns", "guard", "stage", "verdict", "reason_code"):
            self.assertIn(key, e)
        self.assertEqual(e["project"], "demo-api")

    def test_rejects_unknown_verdict(self):
        with self.assertRaises(ValueError):
            ge.build_event("sentry", "prd", "maybe", "X")

    def test_event_ids_are_unique(self):
        self.assertNotEqual(event()["event_id"], event()["event_id"])


class ToOtlpTests(unittest.TestCase):
    def test_shape_and_severity(self):
        body = ge.to_otlp([event()])
        rec = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        self.assertEqual(rec["severityText"], "block")
        self.assertEqual(rec["severityNumber"], 17)
        self.assertEqual(rec["body"], {"stringValue": "BRIEF_NOT_APPROVED"})
        keys = {a["key"] for a in rec["attributes"]}
        self.assertIn("event_id", keys)
        self.assertNotIn("ts_ns", keys)
        service = body["resourceLogs"][0]["resource"]["attributes"][0]
        self.assertEqual(service["value"]["stringValue"], "bmad-guard")


class EmitTests(unittest.TestCase):
    def test_sends_and_empties_queue(self):
        with tempfile.TemporaryDirectory() as tmp, FakeCollector() as fc:
            cfg = config(tmp, fc.url)
            result = ge.emit(event(), cfg, stderr=io.StringIO())
            self.assertEqual(result, {"sent": 1, "queued": 0, "error": None})
            self.assertEqual(fc.record_count(), 1)
            self.assertEqual(cfg.queue.read_text(), "")

    def test_collector_down_queues_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp, unused_port_url())
            err = io.StringIO()
            for i in range(3):
                result = ge.emit(event(i), cfg, stderr=err)
            self.assertEqual(result["queued"], 3)
            self.assertIn("warning", err.getvalue())
            self.assertEqual(len(cfg.queue.read_text().splitlines()), 3)

    def test_queue_drains_when_collector_is_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            down = config(tmp, unused_port_url())
            for i in range(3):
                ge.emit(event(i), down, stderr=io.StringIO())
            with FakeCollector() as fc:
                up = config(tmp, fc.url)
                result = ge.emit(None, up, stderr=io.StringIO())
                self.assertEqual(result["sent"], 3)
                self.assertEqual(fc.record_count(), 3)
            self.assertEqual(up.queue.read_text(), "")

    def test_bad_key_keeps_events_and_hints(self):
        with tempfile.TemporaryDirectory() as tmp, FakeCollector() as fc:
            cfg = config(tmp, fc.url, key="wrong")
            err = io.StringIO()
            result = ge.emit(event(), cfg, stderr=err)
            self.assertEqual(result["queued"], 1)
            self.assertIn("HTTP 401", err.getvalue())
            self.assertIn("BMAD_GUARD_KEY", err.getvalue())
            self.assertNotIn("wrong", err.getvalue())  # never echo the key

    def test_not_configured_queues_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp, "")
            result = ge.emit(event(), cfg, stderr=io.StringIO())
            self.assertEqual(result["queued"], 1)

    def test_unwritable_queue_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "file"
            blocker.write_text("x")
            cfg = ge.Config(endpoint="", user="", key="", queue=blocker / "sub" / "queue.jsonl")
            result = ge.emit(event(), cfg, stderr=io.StringIO())
            self.assertEqual(result["queued"], -1)

    def test_torn_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp, FakeCollector() as fc:
            cfg = config(tmp, fc.url)
            cfg.queue.parent.mkdir(parents=True)
            cfg.queue.write_text('{"broken\n')
            result = ge.emit(event(), cfg, stderr=io.StringIO())
            self.assertEqual(result["sent"], 1)


if __name__ == "__main__":
    unittest.main()
