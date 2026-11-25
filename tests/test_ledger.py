# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for Ledger: module/skills/agent-project-logger/scripts/log_action.py."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "module/skills/agent-project-logger/scripts"
spec = importlib.util.spec_from_file_location("log_action", SCRIPTS / "log_action.py")
la = importlib.util.module_from_spec(spec)
sys.modules["log_action"] = la
spec.loader.exec_module(la)

NO_COLLECTOR = {k: v for k, v in os.environ.items() if not k.startswith("BMAD_GUARD_")}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def make_repo(root: Path) -> Path:
    git(root, "init", "-q", "-b", "main")
    (root / "docs").mkdir()
    (root / "docs/prd.md").write_text("# PRD\nsecret product detail\n")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "init")
    return root


def log(project: Path, *flags: str) -> tuple[int, dict | None, str]:
    out, err = io.StringIO(), io.StringIO()
    base = ["--project-name", "demo-api", "--action", "PRD approved", "--user", "dev", "--project-root", str(project)]
    with mock.patch.dict(os.environ, NO_COLLECTOR, clear=True):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = la.main([*base, *flags])
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else None), err.getvalue()


def queued(project: Path) -> list[dict]:
    path = project / ".bmad-guard/events/queue.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()


class EventTests(Base):
    def test_one_action_one_event_in_queue(self):
        code, result, err = log(self.project, "--stage", "prd", "--agent", "agent-approval-grant/GRANT")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        events = queued(self.project)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual((e["guard"], e["stage"], e["verdict"], e["reason_code"]), ("ledger", "prd", "info", "ACTION"))
        self.assertEqual((e["project"], e["bmad_user"], e["agent"]), ("demo-api", "dev", "agent-approval-grant/GRANT"))
        self.assertEqual(result["status"], {"sent": 0, "queued": 1, "error": None})

    def test_append_only_across_calls(self):
        log(self.project, "--event", "A")
        first = queued(self.project)[0]
        log(self.project, "--event", "B")
        events = queued(self.project)
        self.assertEqual([e["reason_code"] for e in events], ["A", "B"])
        self.assertEqual(events[0], first)

    def test_git_context_without_remote_url(self):
        git(self.project, "remote", "add", "origin", "https://token@example.invalid/org/repo.git")
        log(self.project)
        e = queued(self.project)[0]
        self.assertEqual(e["repo"], self.project.name)
        self.assertEqual(e["branch"], "main")
        self.assertRegex(e["commit"], r"^[0-9a-f]{12}$")
        self.assertNotIn("token", json.dumps(e))

    def test_document_recorded_as_path_and_hash_only(self):
        log(self.project, "--doc-path", "docs/prd.md", "--doc-status", "approved")
        e = queued(self.project)[0]
        self.assertEqual(e["target_path"], "docs/prd.md")
        self.assertEqual(e["target_sha256"], hashlib.sha256((self.project / "docs/prd.md").read_bytes()).hexdigest())
        self.assertEqual(e["doc_status"], "approved")
        self.assertNotIn("secret product detail", json.dumps(e))

    def test_file_outside_project_is_not_read(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as outside:
            outside.write("x")
        try:
            log(self.project, "--doc-path", outside.name)
            e = queued(self.project)[0]
            self.assertEqual(e["target_path"], outside.name)
            self.assertNotIn("target_sha256", e)
        finally:
            os.unlink(outside.name)

    def test_long_text_is_cut_to_one_line(self):
        log(self.project, "--action", "line one\nline two " + "x" * 500, "--confidence-rationale", "y" * 900)
        e = queued(self.project)[0]
        self.assertTrue(e["action"].startswith("line one line two"))
        self.assertLessEqual(len(e["action"]), la.ACTION_MAX)
        self.assertLessEqual(len(e["confidence_rationale"]), la.RATIONALE_MAX)
        self.assertNotIn("\n", e["action"])

    def test_confidence_fields(self):
        log(self.project, "--confidence", "72", "--confidence-label", "Medium", "--event", "CONFIDENCE_SCORED")
        e = queued(self.project)[0]
        fields = (e["confidence"], e["confidence_label"], e["reason_code"])
        self.assertEqual(fields, ("72", "Medium", "CONFIDENCE_SCORED"))

    def test_empty_optional_fields_are_left_out(self):
        log(self.project)
        e = queued(self.project)[0]
        for key in ("agent", "doc_status", "confidence", "target_path", "run_id", "details"):
            self.assertNotIn(key, e)


class VerdictTests(Base):
    def test_result_maps_to_verdict(self):
        for result, verdict in (("blocked", "block"), ("failed", "warn"), ("completed", "info"), ("", "info")):
            log(self.project, "--result", result)
            self.assertEqual(queued(self.project)[-1]["verdict"], verdict, result)

    def test_explicit_verdict_wins(self):
        log(self.project, "--verdict", "allow", "--result", "blocked")
        self.assertEqual(queued(self.project)[0]["verdict"], "allow")


class CompatibilityTests(Base):
    def test_old_csv_flags_are_accepted_and_ignored(self):
        code, _, _ = log(self.project, "--pattern", "{project_name}-log.csv", "--output-dir", str(self.project / "out"))
        self.assertEqual(code, 0)
        self.assertFalse((self.project / "out").exists())
        self.assertEqual(len(queued(self.project)), 1)

    def test_lifecycle_fields_are_kept(self):
        log(self.project, "--run-id", "r1", "--duration-seconds", "3.5", "--result", "passed", "--details", "ok")
        e = queued(self.project)[0]
        self.assertEqual((e["run_id"], e["duration_seconds"], e["result"], e["details"]), ("r1", "3.5", "passed", "ok"))


class ErrorTests(Base):
    def test_bad_input_exits_2_and_records_nothing(self):
        for flags in (("--duration-seconds", "-1"), ("--duration-seconds", "x"), ("--confidence", "101")):
            code, _, err = log(self.project, *flags)
            self.assertEqual(code, 2, flags)
            self.assertIn("error:", err)
        self.assertEqual(queued(self.project), [])

    def test_unwritable_queue_still_exits_0(self):
        (self.project / ".bmad-guard").write_text("not a folder")
        code, result, err = log(self.project)
        self.assertEqual(code, 0)
        self.assertEqual(result["status"]["queued"], -1)
        self.assertIn("warning", err)

    def test_project_root_defaults_to_git_top_level(self):
        (self.project / "sub").mkdir()
        self.assertEqual(la.project_root_from(self.project / "sub").resolve(), self.project.resolve())


if __name__ == "__main__":
    unittest.main()
