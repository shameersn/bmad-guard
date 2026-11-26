# /// script
# requires-python = ">=3.10"
# ///
"""The session-start hook and the pre-push hook send the event queue (GUARD-3 T4 follow-up). Pre-commit does not.

Real hooks in a throwaway project, a fake collector on 127.0.0.1. Fake developer and key; HOME is a temp folder.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

from tests.hook_harness import HOOKS_REL, HookTestCase
from tests.test_guard_events import GOOD, FakeCollector

SESSION_START = ".claude/skills/tool-guard/scripts/session_start.py"
QUEUE = ".bmad-guard/events/queue.jsonl"


def wiring_json() -> str:
    path = Path(__file__).resolve().parents[1] / "module/skills/tool-guard/scripts/hook_wiring.py"
    spec = importlib.util.spec_from_file_location("hook_wiring_for_flush_tests", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up
    spec.loader.exec_module(module)
    return module.render(module.merge(None).settings)


class FlushCase(HookTestCase):
    def setUp(self):
        super().setUp()
        self.write(".claude/settings.json", wiring_json())  # a set-up project: session start has nothing to warn about
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", "wiring")

    def collector_env(self, url: str) -> dict[str, str]:
        return {"BMAD_GUARD_ENDPOINT": url, "BMAD_GUARD_USER": GOOD[0], "BMAD_GUARD_KEY": GOOD[1]}

    def queued(self) -> int:
        path = self.project / QUEUE
        return len([line for line in path.read_text().splitlines() if line.strip()]) if path.is_file() else 0

    def queue_one(self) -> None:
        """One queued event, as the pre-commit hook leaves it."""
        self.write("a.txt", "x\n")
        self.stage("a.txt")
        self.assertEqual(self.hook().returncode, 0)
        self.assertGreaterEqual(self.queued(), 1)

    def session_start(self, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.project / SESSION_START)],
            cwd=self.project,
            input="{}",
            capture_output=True,
            text=True,
            env={**self.env, **(extra_env or {})},
        )

    def pre_push(self, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.project / HOOKS_REL / "pre-push"), "origin", "none"],
            cwd=self.project,
            input="",
            capture_output=True,
            text=True,
            env={**self.env, **(extra_env or {})},
        )


class SessionStartFlushTests(FlushCase):
    def test_the_queue_is_sent_at_session_start(self):
        self.queue_one()
        with FakeCollector() as collector:
            proc = self.session_start(self.collector_env(collector.url))
            sent = collector.record_count()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertGreaterEqual(sent, 1)
        self.assertEqual(self.queued(), 0)

    def test_no_endpoint_means_no_network_no_message_and_the_queue_stays(self):
        self.queue_one()
        before = self.queued()
        proc = self.session_start()
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertNotIn("systemMessage", proc.stdout)
        self.assertEqual(self.queued(), before)

    def test_a_collector_that_is_down_does_not_stop_the_session_and_the_event_stays(self):
        self.queue_one()
        before = self.queued()
        start = time.monotonic()
        proc = self.session_start(self.collector_env("http://127.0.0.1:1"))
        self.assertEqual(proc.returncode, 0)
        self.assertLess(time.monotonic() - start, 8)
        self.assertGreaterEqual(self.queued(), before)

    def test_a_wrong_key_keeps_the_events_and_the_session_starts(self):
        self.queue_one()
        with FakeCollector() as collector:
            env = {**self.collector_env(collector.url), "BMAD_GUARD_KEY": "not-the-key"}
            proc = self.session_start(env)
        self.assertEqual(proc.returncode, 0)
        self.assertGreaterEqual(self.queued(), 1)

    def test_no_queue_file_means_nothing_is_created(self):
        with FakeCollector() as collector:
            proc = self.session_start(self.collector_env(collector.url))
            sent = collector.record_count()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(sent, 0)
        self.assertFalse((self.project / QUEUE).exists())


class PrePushFlushTests(FlushCase):
    def test_the_queue_and_the_push_event_are_sent_by_the_push_hook(self):
        self.queue_one()
        with FakeCollector() as collector:
            proc = self.pre_push(self.collector_env(collector.url))
            records = [r for req in collector.requests for r in req["resourceLogs"][0]["scopeLogs"][0]["logRecords"]]
        self.assertEqual(proc.returncode, 0, proc.stderr)
        codes = [r["body"]["stringValue"] for r in records]
        self.assertIn("COMMIT_ALLOWED", codes)
        self.assertIn("PUSH_NOTHING_TO_CHECK", codes)  # its own event goes out in the same call
        self.assertEqual(self.queued(), 0)

    def test_no_endpoint_means_no_network_and_no_message(self):
        self.queue_one()
        proc = self.pre_push()
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertGreaterEqual(self.queued(), 1)

    def test_a_collector_that_is_down_does_not_block_the_push_and_stays_within_the_timeout(self):
        self.queue_one()
        start = time.monotonic()
        proc = self.pre_push(self.collector_env("http://127.0.0.1:1"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLess(time.monotonic() - start, 6)
        self.assertGreaterEqual(self.queued(), 1)

    def test_the_backoff_after_a_failure_means_the_next_push_does_not_try_again(self):
        self.queue_one()
        env = self.collector_env("http://127.0.0.1:1")
        self.pre_push(env)
        start = time.monotonic()
        proc = self.pre_push(env)
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))  # backing off: silent and fast
        self.assertLess(time.monotonic() - start, 2.5)


class PreCommitStaysQueueOnlyTests(FlushCase):
    def test_pre_commit_never_sends(self):
        with FakeCollector() as collector:
            self.write("a.txt", "x\n")
            self.stage("a.txt")
            proc = self.hook(self.collector_env(collector.url))
            sent = collector.record_count()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(sent, 0)
        self.assertEqual(
            json.loads((self.project / QUEUE).read_text().splitlines()[-1])["reason_code"], "COMMIT_ALLOWED"
        )


if __name__ == "__main__":
    unittest.main()
