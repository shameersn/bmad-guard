# /// script
# requires-python = ">=3.10"
# ///
"""Tests for confidence-scorer: module/skills/confidence-scorer/scripts/confidence_score.py."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "module/skills/confidence-scorer/scripts/confidence_score.py"
spec = importlib.util.spec_from_file_location("confidence_score", SCRIPT)
cs = importlib.util.module_from_spec(spec)
sys.modules["confidence_score"] = cs
spec.loader.exec_module(cs)
ENV = {k: v for k, v in os.environ.items() if not k.startswith("BMAD_GUARD_")}


def make_args(**overrides):
    defaults = dict(
        file_path="src/example.py",
        tests_passed=0,
        tests_failed=0,
        no_tests_score=50,
        lint_errors=0,
        lint_warnings=0,
        error_penalty=20,
        warning_penalty=5,
        lines_changed=0,
        file_size=100,
        revision_cycles=0,
        doc_status=None,
        state_file=None,
        doc_type=None,
        test_weight=40,
        lint_weight=25,
        diff_weight=20,
        revision_weight=15,
        diff_ratio_cutoffs=cs.DEFAULT_DIFF_RATIO_CUTOFFS,
        revision_cycle_cutoffs=cs.DEFAULT_REVISION_CYCLE_CUTOFFS,
        label_cutoffs=cs.DEFAULT_LABEL_CUTOFFS,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def run(script: Path, cwd: Path, *args: str) -> tuple[int, dict | None, str]:
    proc = subprocess.run([sys.executable, str(script), *args], cwd=cwd, env=ENV, capture_output=True, text=True)
    out = proc.stdout.strip()
    return proc.returncode, (json.loads(out) if out else None), proc.stderr


def events(project: Path) -> list[dict]:
    q = project / ".bmad-guard/events/queue.jsonl"
    return [json.loads(line) for line in q.read_text().splitlines()] if q.exists() else []


class BucketTests(unittest.TestCase):
    def test_bucket_score_picks_first_matching_ascending_cutoff(self):
        cutoffs = [[0.1, 100], [0.3, 80], [999, 20]]
        self.assertEqual(cs.bucket_score(0.05, cutoffs), 100)
        self.assertEqual(cs.bucket_score(0.2, cutoffs), 80)
        self.assertEqual(cs.bucket_score(5, cutoffs), 20)

    def test_bucket_label_picks_first_matching_descending_threshold(self):
        cutoffs = [[80, "High"], [50, "Medium"], [0, "Low"]]
        self.assertEqual(cs.bucket_label(95, cutoffs), "High")
        self.assertEqual(cs.bucket_label(60, cutoffs), "Medium")
        self.assertEqual(cs.bucket_label(10, cutoffs), "Low")


class ComputeTests(unittest.TestCase):
    def test_all_signals_clean_scores_high_and_rationale_lists_every_signal(self):
        # The original test expected "No weak signals detected."; the documented behaviour is the full template.
        result = cs.compute(make_args(tests_passed=5, lines_changed=5, file_size=100))
        self.assertEqual((result["confidence"], result["confidence_label"]), (100, "High"))
        for part in ("test: 5/5 tests passed", "lint: no lint issues", "diff: 5 lines changed of 100", "total=100"):
            self.assertIn(part, result["confidence_rationale"])

    def test_test_failures_and_revision_cycles_drive_rationale(self):
        result = cs.compute(make_args(tests_passed=3, tests_failed=2, revision_cycles=3, lines_changed=5))
        self.assertIn("2 test failures", result["confidence_rationale"])
        self.assertIn("3 revision cycles", result["confidence_rationale"])
        self.assertLess(result["confidence"], 80)

    def test_no_tests_touching_file_uses_neutral_score(self):
        result = cs.compute(make_args(no_tests_score=50))
        self.assertEqual(result["components"]["test"]["score"], 50)
        self.assertIn("no tests touch this file", result["confidence_rationale"])

    def test_lint_errors_reduce_score_and_appear_in_rationale(self):
        result = cs.compute(make_args(tests_passed=5, lint_errors=2, lint_warnings=1))
        self.assertEqual(result["components"]["lint"]["score"], 100 - 2 * 20 - 1 * 5)
        self.assertIn("lint error", result["confidence_rationale"])

    def test_large_diff_ratio_lowers_diff_score(self):
        result = cs.compute(make_args(tests_passed=5, lines_changed=90, file_size=100))
        self.assertLess(result["components"]["diff"]["score"], 100)

    def test_confidence_clamped_between_0_and_100(self):
        args = make_args(tests_failed=10, lint_errors=20, lint_warnings=20, revision_cycles=10, lines_changed=1000)
        result = cs.compute(args)
        self.assertTrue(0 <= result["confidence"] <= 100)

    def test_same_inputs_same_output(self):
        args = dict(tests_passed=4, tests_failed=1, lint_warnings=2, lines_changed=30)
        self.assertEqual(cs.compute(make_args(**args)), cs.compute(make_args(**args)))

    def test_explicit_revision_cycles_skips_state_file(self):
        result = cs.compute(make_args(revision_cycles=1, state_file="/does/not/exist.json", doc_type="story-1"))
        self.assertEqual(result["components"]["revision"]["score"], 80)


class StateFileResolutionTests(unittest.TestCase):
    def test_missing_entry_is_unapproved(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "approval-state.json"
            state_file.write_text("{}")
            self.assertEqual(cs.resolve_from_state(str(state_file), "story-1"), (0, "unapproved"))

    def test_superseded_entry_is_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "story-1.md"
            doc.write_text("---\nstatus: draft\nversion: 2\n---\nbody\n")
            state_file = Path(tmp) / "approval-state.json"
            state_file.write_text(json.dumps({"story-1": {"doc_path": str(doc), "superseded": True}}))
            self.assertEqual(cs.resolve_from_state(str(state_file), "story-1"), (2, "draft"))

    def test_approved_entry_reads_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "story-1.md"
            doc.write_text("---\nstatus: approved\nversion: 1\n---\nbody\n")
            state_file = Path(tmp) / "approval-state.json"
            state_file.write_text(json.dumps({"story-1": {"doc_path": str(doc)}}))
            self.assertEqual(cs.resolve_from_state(str(state_file), "story-1"), (1, "approved"))

    def test_relative_doc_path_resolves_from_project_root(self):
        # Warden stores paths relative to the project root (T4). Run from another folder to prove it.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            git(project, "init", "-q")
            (project / "docs").mkdir()
            (project / "docs/story-1.md").write_text("---\nstatus: approved\nversion: 3\n---\n")
            (project / "_bmad/custom").mkdir(parents=True)
            state_file = project / "_bmad/custom/approval-state.json"
            state_file.write_text(json.dumps({"story-1": {"doc_path": "docs/story-1.md"}}))
            old = Path.cwd()
            try:
                os.chdir(tempfile.gettempdir())
                self.assertEqual(cs.resolve_from_state(str(state_file), "story-1"), (3, "approved"))
            finally:
                os.chdir(old)

    def test_broken_or_missing_state_is_unapproved(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "s.json"
            broken.write_text("{broken")
            self.assertEqual(cs.resolve_from_state(str(broken), "x"), (0, "unapproved"))
            self.assertEqual(cs.resolve_from_state(str(Path(tmp) / "none/s.json"), "x"), (0, "unapproved"))


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        git(self.project, "init", "-q")
        (self.project / "src").mkdir()
        (self.project / "src/app.py").write_text("print('hi')\n" * 10)

    def tearDown(self):
        self._tmp.cleanup()

    def test_bad_input_exits_2_and_records_nothing(self):
        for flags in (
            ["--tests-passed", "-1"],
            ["--lines-changed", "-5"],
            ["--file-size", "-1"],
            ["--test-weight", "-1"],
            ["--test-weight", "0", "--lint-weight", "0", "--diff-weight", "0", "--revision-weight", "0"],
        ):
            code, _, err = run(SCRIPT, self.project, "--file-path", "src/app.py", *flags)
            self.assertEqual(code, 2, flags)
            self.assertIn("error:", err)
        self.assertEqual(events(self.project), [])

    def test_score_is_recorded_as_event_with_hash_not_content(self):
        code, result, _ = run(
            SCRIPT, self.project, "--file-path", "src/app.py", "--tests-passed", "3", "--stage", "dev"
        )
        self.assertEqual(code, 0)
        (e,) = events(self.project)
        self.assertEqual(
            (e["guard"], e["reason_code"], e["verdict"], e["stage"]),
            ("confidence-scorer", "CODE_SCORED", "info", "dev"),
        )
        self.assertEqual(e["target_path"], "src/app.py")
        self.assertEqual(e["confidence"], str(result["confidence"]))
        self.assertEqual(len(e["target_sha256"]), 64)
        self.assertNotIn("print('hi')", json.dumps(e))

    def test_low_score_is_a_warning(self):
        run(
            SCRIPT,
            self.project,
            "--file-path",
            "src/app.py",
            "--tests-failed",
            "5",
            "--lint-errors",
            "5",
            "--lines-changed",
            "100",
            "--revision-cycles",
            "5",
        )
        self.assertEqual(events(self.project)[0]["verdict"], "warn")

    def test_no_event_flag(self):
        code, _, _ = run(SCRIPT, self.project, "--file-path", "src/app.py", "--no-event")
        self.assertEqual(code, 0)
        self.assertEqual(events(self.project), [])

    def test_scores_and_warns_without_ledger(self):
        lone = self.project / "skills/confidence-scorer"
        shutil.copytree(SCRIPT.parents[1], lone)
        code, result, err = run(lone / "scripts/confidence_score.py", self.project, "--file-path", "src/app.py")
        self.assertEqual(code, 0)
        self.assertIn("confidence", result)
        self.assertIn("Ledger (agent-project-logger) not installed", err)


if __name__ == "__main__":
    unittest.main()
