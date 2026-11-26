# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for the write gate (Phase 2, T4): write_gate.py, as registered in the PreToolUse dispatcher.

Each test builds a fake project in a temp folder (a git repo with one commit, as the ready-for-dev baseline needs),
a fake bmad-build plan and, when needed, a fake approval in approval-state.json. Fake names and data only.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.guard_harness import ROOT, GuardTestCase, queue

wg = importlib.import_module("write_gate")
gc = importlib.import_module("guard_config")

SKILLS = ROOT / "module/skills"
spec = importlib.util.spec_from_file_location(
    "approval_core_for_tests", SKILLS / "agent-approval-gate-check/scripts/approval_core.py"
)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

PLANS = "_bmad-output/implementation-artifacts"
PLAN = f"{PLANS}/spec-export-report.md"
STORY = f"{PLANS}/2-3-export-report.md"
SUMMARY = "_bmad-output/planning-artifacts/epics/epic-2/epic-summary.md"
STATE = "_bmad/custom/approval-state.json"

PLAN_TEXT = """\
---
title: Export the monthly report
type: feature
created: '2026-11-25'
status: in-progress
route: dispatch
review_loop_iteration: 0
story: 2-3
approval_status: pending-approval
confidence: 88
---

# Plan: export the monthly report

## Scope Summary

One CSV export on the report page.

## Implementation Notes
"""
STORY_TEXT = """\
---
title: Export the monthly report
confidence: 90
epic: 2
---

# Story 2.3: Export the monthly report

The file has one row per member [ASSUMPTION-RESOLVED A-1: inactive members are left out — Ravi PO, 2026-11-21].

## Open questions

- Q-1: Which roles can export?
  Resolved: Managers and admins. — Priya BA, 2026-11-20
"""
SUMMARY_TEXT = "# Epic 2\n\n## Functional\nReports for managers.\n\n## Technical\nOne export service.\n"


def run(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


_folder: tempfile.TemporaryDirectory | None = None
_template: Path | None = None


def setUpModule():
    """A git repo with a plan, a story and an epic summary, built once and copied for every test (git is slow)."""
    global _folder, _template
    _folder = tempfile.TemporaryDirectory()
    _template = Path(os.path.realpath(_folder.name))
    for rel, text in ((SUMMARY, SUMMARY_TEXT), (STORY, STORY_TEXT), (PLAN, PLAN_TEXT)):
        (_template / rel).parent.mkdir(parents=True, exist_ok=True)
        (_template / rel).write_text(text, encoding="utf-8")
    run("init", "-q", cwd=_template)
    run("config", "user.email", "dev@example.invalid", cwd=_template)
    run("config", "user.name", "Dev", cwd=_template)
    run("add", "-A", cwd=_template)
    run("-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture", cwd=_template)


def tearDownModule():
    _folder.cleanup()


class GateCase(GuardTestCase):
    """A project that is a git repo with a plan, a story and an epic summary. Not approved yet."""

    with_write_gate = True

    def setUp(self):
        super().setUp()
        shutil.copytree(_template, self.project, dirs_exist_ok=True)

    def approve(self, rel: str = PLAN, text: str | None = None) -> None:
        """What Warden does for a plan: set approval_status and record the build hash."""
        path = self.project / rel
        approved = core.set_status(text if text is not None else path.read_text(), "approval_status", "approved")
        path.write_text(approved)
        state_path = self.project / STATE
        state = json.loads(state_path.read_text()) if state_path.is_file() else {}
        state[f"build:{rel}"] = {
            "doc_path": rel,
            "approved_hash": core.content_hash(approved, "build"),
            "hash_mode": "build",
            "approved_by": "Priya BA",
            "approved_at": "2026-11-25T10:00:00Z",
            "superseded": False,
        }
        self.write(STATE, json.dumps(state))

    def source(self, rel: str = "src/app.py", content: str = "print('fake')\n", **extra) -> list[str]:
        return self.codes("Write", {"file_path": str(self.project / rel), "content": content}, **extra)

    def builds(self) -> dict:
        return {p.stem: json.loads(p.read_text()) for p in (self.project / wg.RECORD_REL).glob("*.json")}

    def sub(self, rel: str, old: str, new: str) -> None:
        path = self.project / rel
        path.write_text(path.read_text().replace(old, new))


class PlanCheckTests(GateCase):
    def test_no_plan_blocks(self):
        (self.project / PLAN).unlink()
        self.assertEqual(self.source(), ["GATE_NO_ACTIVE_PLAN"])

    def test_a_plan_that_is_not_in_progress_is_not_active(self):
        for status in ("draft", "ready-for-dev", "done", "in-review-later"):
            with self.subTest(status=status):
                self.sub(PLAN, "status: in-progress", f"status: {status}")
                self.assertEqual(self.source(), ["GATE_NO_ACTIVE_PLAN"])
                self.sub(PLAN, f"status: {status}", "status: in-progress")

    def test_a_document_without_a_plan_marker_is_not_a_plan(self):
        self.write("_bmad-output/other.md", "---\nstatus: in-progress\ntitle: A note\n---\n# Note\n")
        (self.project / PLAN).unlink()
        self.assertEqual(self.source(), ["GATE_NO_ACTIVE_PLAN"])

    def test_the_template_comment_after_the_status_does_not_hide_the_plan(self):
        self.sub(PLAN, "status: in-progress", "status: 'in-progress' # draft | ready-for-dev | in-progress | done")
        self.assertEqual(self.source(), ["GATE_PLAN_NOT_APPROVED"])

    def test_an_unapproved_plan_blocks(self):
        self.assertEqual(self.source(), ["GATE_PLAN_NOT_APPROVED"])

    def test_an_approved_plan_and_a_ready_story_allow(self):
        self.approve()
        self.assertEqual(self.source(), [])

    def test_progress_edits_after_approval_do_not_count_as_a_change(self):
        self.approve()
        self.sub(PLAN, "status: in-progress", "status: in-review\nbaseline_commit: abc123")
        self.sub(PLAN, "## Implementation Notes\n", "## Implementation Notes\n\nTouched src/app.py.\n")
        self.assertEqual(self.source(), [])

    def test_a_plan_edited_after_approval_blocks_and_is_left_alone(self):
        self.approve()
        self.sub(PLAN, "One CSV export", "Two CSV exports")
        before = (self.project / PLAN).read_text()
        self.assertEqual(self.source(), ["GATE_PLAN_CHANGED"])
        self.assertEqual((self.project / PLAN).read_text(), before)  # the hook asks; Sentry's own run resets
        self.assertIn("approval_status: approved", before)

    def test_a_reopened_approval_blocks(self):
        self.approve()
        state = json.loads((self.project / STATE).read_text())
        state[f"build:{PLAN}"]["superseded"] = True
        self.write(STATE, json.dumps(state))
        self.assertEqual(self.source(), ["GATE_PLAN_NOT_APPROVED"])

    def test_an_approval_for_another_plan_does_not_count(self):
        self.approve()
        state = json.loads((self.project / STATE).read_text())
        state[f"build:{PLAN}"]["doc_path"] = "_bmad-output/other-plan.md"
        self.write(STATE, json.dumps(state))
        self.assertEqual(self.source(), ["GATE_PLAN_NOT_APPROVED"])

    def test_a_broken_state_file_blocks(self):
        self.approve()
        self.write(STATE, "{not json")
        self.assertEqual(self.source(), ["GATE_STATE_UNREADABLE"])

    def test_an_approved_status_without_a_record_blocks(self):
        self.sub(PLAN, "approval_status: pending-approval", "approval_status: approved")
        self.assertEqual(self.source(), ["GATE_PLAN_NOT_APPROVED"])

    def test_warden_approves_a_plan_and_the_gate_opens(self):
        # the real Warden script, with the arguments the bmad-build override gives it
        grant = SKILLS / "agent-approval-grant/scripts/grant_approval.py"
        done = subprocess.run(
            [
                sys.executable,
                str(grant),
                "--doc-path",
                PLAN,
                "--doc-type",
                f"build:{PLAN}",
                "--state-file",
                STATE,
                "--status-field",
                "approval_status",
                "--create-status-field",
                "--hash-mode",
                "build",
                "--no-commit",
                "--approved-by",
                "Priya BA",
            ],
            cwd=self.project,
            capture_output=True,
            text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.source(), [])

    def test_no_event_and_no_record_when_the_call_is_not_gated(self):
        (self.project / PLAN).unlink()
        self.assertEqual(self.source("docs/notes.txt"), [])
        self.assertEqual(self.builds(), {})


class ManyPlansTests(GateCase):
    def other_plan(self, status: str, name: str = "spec-other.md") -> str:
        rel = f"{PLANS}/{name}"
        self.write(rel, PLAN_TEXT.replace("status: in-progress", f"status: {status}").replace("story: 2-3\n", ""))
        return rel

    def test_two_plans_in_progress_block_with_one_reason_that_names_both(self):
        self.approve()
        self.other_plan("in-progress")
        codes = self.codes("Write", {"file_path": str(self.project / "src/a.py"), "content": "x"})
        self.assertEqual(codes, ["GATE_MANY_ACTIVE_PLANS"])
        verdicts = self.call("Write", {"file_path": str(self.project / "src/a.py"), "content": "x"})[1]
        self.assertIn("spec-other.md", verdicts[0].reason)
        self.assertIn("spec-export-report.md", verdicts[0].reason)

    def test_a_plan_waiting_for_review_does_not_hide_the_one_in_progress(self):
        self.approve()
        self.other_plan("in-review")
        self.assertEqual(self.source(), [])

    def test_a_plan_in_review_is_the_current_one_when_none_is_in_progress(self):
        self.approve()
        self.sub(PLAN, "status: in-progress", "status: in-review")
        self.assertEqual(self.source(), [])  # bmad-build patches code during review

    def test_two_plans_in_review_block(self):
        self.approve()
        self.sub(PLAN, "status: in-progress", "status: in-review")
        self.other_plan("in-review")
        self.assertEqual(self.source(), ["GATE_MANY_ACTIVE_PLANS"])

    def test_plans_are_found_in_any_folder_under_bmad_output(self):
        (self.project / PLAN).unlink()
        self.write("_bmad-output/specs/stories/2-3-x.md", PLAN_TEXT.replace("story: 2-3\n", ""))
        self.assertIn("GATE_PLAN_NOT_APPROVED", self.source())

    def test_a_plan_outside_bmad_output_is_not_looked_for(self):
        (self.project / PLAN).unlink()
        self.write("notes/spec-x.md", PLAN_TEXT)
        self.assertEqual(self.source(), ["GATE_NO_ACTIVE_PLAN"])


class StoryCheckTests(GateCase):
    def setUp(self):
        super().setUp()
        self.approve()

    def story_codes(self) -> list[str]:
        return self.source()

    def test_an_open_question_blocks(self):
        self.sub(STORY, "- Q-1: Which roles can export?", "- Q-1: Which roles can export?")
        self.write(STORY, STORY_TEXT.replace("  Resolved: Managers and admins. — Priya BA, 2026-11-20\n", ""))
        self.assertEqual(self.story_codes(), ["GATE_READY_OPEN_QUESTIONS"])

    def test_an_open_assumption_blocks(self):
        self.write(STORY, STORY_TEXT.replace("[ASSUMPTION-RESOLVED A-1", "[ASSUMPTION A-1"))
        self.assertEqual(self.story_codes(), ["GATE_READY_OPEN_ASSUMPTIONS"])

    def test_low_confidence_blocks(self):
        self.write(STORY, STORY_TEXT.replace("confidence: 90", "confidence: 79"))
        self.assertEqual(self.story_codes(), ["GATE_READY_LOW_CONFIDENCE"])
        self.write(STORY, STORY_TEXT.replace("confidence: 90", "confidence: 80"))
        self.assertEqual(self.story_codes(), [])

    def test_the_other_ready_for_dev_reasons_are_blocks_too(self):
        cases = {
            "GATE_READY_MISSING_CONFIDENCE": STORY_TEXT.replace("confidence: 90\n", ""),
            "GATE_READY_RESOLUTION_MISSING_WHO_OR_WHEN": STORY_TEXT.replace(" — Priya BA, 2026-11-20", ""),
            "GATE_READY_DELETED_ITEMS": STORY_TEXT.replace("- Q-1", "- Q-2"),
            "GATE_READY_MISSING_EPIC_SUMMARY": STORY_TEXT.replace("epic: 2\n", ""),
        }
        for code, text in cases.items():
            with self.subTest(code=code):
                self.write(STORY, text)
                self.assertIn(code, self.story_codes())

    def test_a_question_deleted_since_the_last_commit_counts(self):
        self.write(STORY, STORY_TEXT.replace("## Open questions", "## Notes").replace("- Q-1", "- note"))
        self.assertIn("GATE_READY_DELETED_ITEMS", self.story_codes())

    def test_a_missing_epic_summary_file_blocks_and_a_complete_one_passes(self):
        (self.project / SUMMARY).unlink()
        self.assertEqual(self.story_codes(), ["GATE_READY_MISSING_EPIC_SUMMARY"])
        self.write(SUMMARY, "# Epic 2\n\n## Functional\nText.\n")
        self.assertEqual(self.story_codes(), ["GATE_READY_EPIC_SUMMARY_INCOMPLETE"])

    def test_a_generic_story_skips_the_ready_check_but_not_the_plan_check(self):
        generic = "---\ngate: generic\n---\n\n# Chore\n\n## Open questions\n\n- Q-1: Anything?\n"
        self.write(STORY, generic)
        self.assertEqual(self.story_codes(), [])
        self.sub(PLAN, "approval_status: approved", "approval_status: pending-approval")
        self.assertEqual(self.story_codes(), ["GATE_PLAN_NOT_APPROVED"])

    def test_a_plan_without_a_story_field_is_its_own_story(self):
        plan = PLAN_TEXT.replace("story: 2-3\n", "")
        self.write(PLAN, plan)
        self.approve(text=plan)
        self.assertEqual(self.source(), ["GATE_READY_MISSING_EPIC_SUMMARY"])  # freeform work needs gate: generic
        generic = plan.replace("confidence: 88\n", "confidence: 88\ngate: generic\n")
        self.write(PLAN, generic)
        self.approve(text=generic)
        self.assertEqual(self.source(), [])

    def test_the_story_field_can_be_a_path_or_an_id(self):
        for value in (STORY, "2-3", "2-3-export-report"):
            with self.subTest(value=value):
                plan = PLAN_TEXT.replace("story: 2-3", f"story: '{value}'")
                self.write(PLAN, plan)
                self.approve(text=plan)
                self.assertEqual(self.source(), [])

    def test_a_story_that_cannot_be_found_blocks(self):
        for value in ("9-9", "docs/missing.md", "../outside.md", "2-4"):
            with self.subTest(value=value):
                plan = PLAN_TEXT.replace("story: 2-3", f"story: {value}")
                self.write(PLAN, plan)
                self.approve(text=plan)
                self.assertEqual(self.source(), ["GATE_STORY_NOT_FOUND"])

    def test_an_id_that_matches_two_files_blocks(self):
        self.write(f"{PLANS}/2-3-export-report-copy.md", STORY_TEXT)
        self.assertEqual(self.source(), ["GATE_STORY_NOT_FOUND"])

    def test_a_story_in_another_folder_is_found_by_its_id(self):
        (self.project / STORY).rename(self.project / "_bmad-output/2-3-moved.md")
        self.assertEqual(self.source(), [])

    def test_the_ready_for_dev_settings_come_from_its_own_customize_file(self):
        # a project file cannot lower the minimum confidence (it is not read)
        self.write("_bmad/custom/ready-for-dev-gate.toml", "[agent]\nmin_confidence = 10\nmode = 'advisory'\n")
        self.write(STORY, STORY_TEXT.replace("confidence: 90", "confidence: 50"))
        self.assertEqual(self.source(), ["GATE_READY_LOW_CONFIDENCE"])

    def test_an_unreadable_story_blocks(self):
        (self.project / STORY).write_bytes(b"\xff\xfe\x00bad")
        self.assertEqual(self.source(), ["GATE_READY_UNREADABLE_STORY"])


class OneMessageTests(GateCase):
    def test_every_reason_comes_in_one_message_with_the_next_step(self):
        self.write(
            STORY,
            STORY_TEXT.replace("[ASSUMPTION-RESOLVED A-1", "[ASSUMPTION A-1")
            .replace("confidence: 90", "confidence: 60")
            .replace("  Resolved: Managers and admins. — Priya BA, 2026-11-20\n", ""),
        )
        raw = json.dumps(self.payload("Write", {"file_path": str(self.project / "src/app.py"), "content": "x"}))
        code, out, err = self.entry(raw)
        self.assertEqual((code, out), (2, ""))
        for rule in (
            "GATE_PLAN_NOT_APPROVED",
            "GATE_READY_OPEN_QUESTIONS",
            "GATE_READY_OPEN_ASSUMPTIONS",
            "GATE_READY_LOW_CONFIDENCE",
        ):
            self.assertIn(rule, err)
        self.assertEqual(err.count("[tool-guard] Blocked"), 1)
        self.assertIn("Warden", err)
        self.assertIn("Q-1", err)
        self.assertIn("A-1", err)
        self.assertEqual(
            sorted(e["reason_code"] for e in queue(self.project)),
            sorted(
                [
                    "GATE_PLAN_NOT_APPROVED",
                    "GATE_READY_OPEN_QUESTIONS",
                    "GATE_READY_OPEN_ASSUMPTIONS",
                    "GATE_READY_LOW_CONFIDENCE",
                ]
            ),
        )

    def test_the_no_plan_message_names_the_next_step(self):
        (self.project / PLAN).unlink()
        raw = json.dumps(self.payload("Write", {"file_path": str(self.project / "src/app.py"), "content": "x"}))
        _, _, err = self.entry(raw)
        for word in ("bmad-build", "Warden", "in-progress"):
            self.assertIn(word, err)

    def test_other_guards_reasons_join_the_same_message(self):
        raw = json.dumps(self.payload("Write", {"file_path": str(self.project / ".env"), "content": "x"}))
        _, _, err = self.entry(raw)
        self.assertIn("PATH_ENV_FILE", err)
        self.assertIn("GATE_PLAN_NOT_APPROVED", err)
        self.assertEqual(err.count("[tool-guard] Blocked"), 1)


class PathTests(GateCase):
    def test_the_allowed_paths_are_not_gated(self):
        (self.project / PLAN).unlink()
        for rel in (
            "_bmad/custom/notes.toml",
            "_bmad-output/planning-artifacts/prd.md",
            "docs/api.md",
            "docs/deep/er/notes.txt",
            ".claude/commands/review.md",
            ".agents/notes.md",
            ".bmad-guard/tmp/x.json",
        ):
            with self.subTest(rel=rel):
                self.assertEqual(self.source(rel), [])

    def test_source_and_root_files_are_gated(self):
        (self.project / PLAN).unlink()
        for rel in ("src/app.py", "README.md", "package.json", "tests/test_app.py", "docsx/a.py", "_bmadx/a.py"):
            with self.subTest(rel=rel):
                self.assertEqual(self.source(rel), ["GATE_NO_ACTIVE_PLAN"])

    def test_every_writing_tool_is_gated(self):
        (self.project / PLAN).unlink()
        target = str(self.project / "src/app.py")
        self.write("src/app.py", "old\n")
        self.assertEqual(
            self.codes("Edit", {"file_path": target, "old_string": "old", "new_string": "new"}), ["GATE_NO_ACTIVE_PLAN"]
        )
        self.assertEqual(
            self.codes("MultiEdit", {"file_path": target, "edits": [{"old_string": "old", "new_string": "new"}]}),
            ["GATE_NO_ACTIVE_PLAN"],
        )
        self.assertEqual(
            self.codes("NotebookEdit", {"notebook_path": str(self.project / "src/n.ipynb"), "new_source": "x"}),
            ["GATE_NO_ACTIVE_PLAN"],
        )

    def test_reading_tools_are_not_gated(self):
        (self.project / PLAN).unlink()
        self.assertEqual(self.codes("Read", {"file_path": str(self.project / "src/app.py")}), [])
        self.assertEqual(self.codes("Grep", {"pattern": "x", "path": str(self.project / "src")}), [])

    def test_a_relative_path_is_resolved_from_the_working_folder(self):
        (self.project / PLAN).unlink()
        self.write("src/.keep")
        src = str(self.project / "src")
        self.assertEqual(self.codes("Write", {"file_path": "src/app.py", "content": "x"}), ["GATE_NO_ACTIVE_PLAN"])
        self.assertEqual(self.codes("Write", {"file_path": "app.py", "content": "x"}, cwd=src), ["GATE_NO_ACTIVE_PLAN"])
        self.assertEqual(self.codes("Write", {"file_path": "../docs/x.txt", "content": "x"}, cwd=src), [])
        self.write("docs/.keep")
        self.assertEqual(
            self.codes("Write", {"file_path": "../src/a.py", "content": "x"}, cwd=str(self.project / "docs")),
            ["GATE_NO_ACTIVE_PLAN"],
        )

    def test_a_path_outside_the_project_is_not_gated(self):
        (self.project / PLAN).unlink()
        for path in (self.project.parent / "elsewhere/x.py", self.home / "notes.md", "~/notes.md"):
            with self.subTest(path=str(path)):
                self.assertEqual(self.codes("Write", {"file_path": str(path), "content": "x"}), [])

    def test_the_other_guards_still_run_on_an_allowed_path(self):
        self.assertEqual(self.source("docs/.env"), ["PATH_ENV_FILE"])
        self.assertEqual(self.source(".claude/settings.json"), ["DOC_GUARD_SCRIPTS"])
        self.assertEqual(self.source("_bmad-output/guard/builds/x.json"), ["DOC_GUARD_RECORD"])

    def test_a_link_inside_an_allowed_folder_is_followed(self):
        (self.project / PLAN).unlink()
        (self.project / "src").mkdir()
        (self.project / "docs").mkdir()
        os.symlink(self.project / "src", self.project / "docs/linked")
        self.assertEqual(self.source("docs/linked/app.py"), ["GATE_NO_ACTIVE_PLAN"])

    def test_scratch_folders_are_not_gated(self):
        (self.project / PLAN).unlink()
        for rel in (
            "tmp/out.txt",
            "build/app.js",
            "dist/a.js",
            "node_modules/pkg/index.js",
            "a/__pycache__/x.pyc",
            "coverage/lcov.info",
        ):
            with self.subTest(rel=rel):
                self.assertEqual(self.source(rel), [])

    def test_a_project_scratch_folder_is_not_gated_either(self):
        (self.project / PLAN).unlink()
        self.write("_bmad/custom/project/guard/tool-rules.json", json.dumps({"scratch_folders": ["_work/cache"]}))
        self.assertEqual(self.source("_work/cache/x.bin"), [])
        self.assertEqual(self.source("_work/other.py"), ["GATE_NO_ACTIVE_PLAN"])

    def test_a_malformed_call_is_reported_by_the_document_guard_once(self):
        for tool_input in ({}, {"file_path": ""}, {"file_path": 7}):
            with self.subTest(tool_input=tool_input):
                self.assertEqual(self.codes("Write", tool_input), ["BAD_HOOK_INPUT"])


class ProjectPathTests(GateCase):
    def extension(self, data) -> None:
        self.write("_bmad/custom/project/guard/tool-rules.json", data if isinstance(data, str) else json.dumps(data))

    def test_a_project_adds_allowed_paths(self):
        (self.project / PLAN).unlink()
        self.extension({"write_gate_allowed_paths": ["tools/dev", "README.md", "**/fixtures"]})
        for rel in ("tools/dev/run.py", "README.md", "tests/fixtures/a.json", "a/b/fixtures/x.py", "fixtures/y.py"):
            with self.subTest(rel=rel):
                self.assertEqual(self.source(rel), [])
        for rel in ("tools/other.py", "README.md.bak", "src/app.py", "tests/fixture/a.json"):
            with self.subTest(rel=rel):
                self.assertEqual(self.source(rel), ["GATE_NO_ACTIVE_PLAN"])

    def test_a_project_path_does_not_remove_a_built_in_one(self):
        (self.project / PLAN).unlink()
        self.extension({"write_gate_allowed_paths": ["tools"]})
        self.assertEqual(self.source("docs/a.md"), [])

    def test_a_bad_extension_file_blocks_and_the_gate_stays_strict(self):
        (self.project / PLAN).unlink()
        for text in (
            "{not json",
            json.dumps({"write_gate_allowed_paths": "tools"}),
            json.dumps({"write_gate_allowed_paths": ["../up"]}),
            json.dumps({"write_gate_allowed_paths": ["/etc"]}),
            json.dumps({"write_gate_allowed_paths": ["src/*"]}),
            json.dumps({"write_gate_allowed_paths": ["**"]}),
            json.dumps({"write_gate_allowed_paths": ["."]}),
            json.dumps({"write_gate_allowed_paths": [""]}),
            json.dumps({"write_gate_allowed_paths": [7]}),
            json.dumps({"write_gate_paths": ["tools"]}),
        ):
            with self.subTest(text=text):
                self.extension(text)
                codes = self.source("src/app.py")
                self.assertIn("EXT_BAD_FILE", codes)
                self.assertIn("GATE_NO_ACTIVE_PLAN", codes)  # the file adds nothing while it is broken
                self.assertEqual(self.codes("Bash", {"command": "echo x > src/app.py"}).count("EXT_BAD_FILE"), 1)


class BuildRecordTests(GateCase):
    def setUp(self):
        super().setUp()
        self.approve()

    def test_a_passing_write_records_the_session_for_the_story(self):
        self.assertEqual(self.source(session_id="s-build-1"), [])
        record = self.builds()["2-3"]
        self.assertEqual(sorted(record), ["first_at", "last_at", "sessions", "story"])
        self.assertEqual((record["story"], record["sessions"]), ("2-3", ["s-build-1"]))
        self.assertRegex(record["first_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(record["first_at"], record["last_at"])

    def test_the_record_is_updated_not_duplicated(self):
        times = iter(["2026-11-25T10:00:00Z", "2026-11-25T11:00:00Z", "2026-11-25T12:00:00Z"])
        with mock.patch.object(wg, "now", side_effect=lambda: next(times)):
            self.source(session_id="s-1")
            self.source("src/b.py", session_id="s-1")
            self.source("src/c.py", session_id="s-2")
        self.assertEqual(
            self.builds()["2-3"],
            {
                "story": "2-3",
                "sessions": ["s-1", "s-2"],
                "first_at": "2026-11-25T10:00:00Z",
                "last_at": "2026-11-25T12:00:00Z",
            },
        )

    def test_the_session_id_comes_from_the_hook_input_never_from_the_environment(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "s-from-env"}):
            self.source(session_id="s-from-stdin")
        self.assertEqual(self.builds()["2-3"]["sessions"], ["s-from-stdin"])

    def test_the_record_holds_no_path_and_no_content(self):
        marker = "SECRET-CODE-MARKER-555"
        self.source("secret-folder-77/app.py", content=marker, session_id="s-1")
        text = (self.project / wg.RECORD_REL / "2-3.json").read_text()
        for word in (marker, "secret-folder-77", "app.py", "src/", "_bmad-output", "Priya", "spec-export"):
            self.assertNotIn(word, text)

    def test_the_record_name_follows_the_story(self):
        plan = PLAN_TEXT.replace("story: 2-3", f"story: {STORY}")
        self.write(PLAN, plan)
        self.approve(text=plan)
        self.source(session_id="s-1")
        self.assertEqual(list(self.builds()), ["2-3-export-report"])
        self.assertEqual(wg.story_key(wg.Plan(Path("x/Plan Name!.md"), "x", "in-progress", {}), None), "Plan-Name")

    def test_the_record_is_not_written_for_allowed_paths_or_blocked_calls(self):
        self.source("docs/a.md", session_id="s-1")
        self.assertEqual(self.builds(), {})
        self.sub(PLAN, "One CSV export", "Two")
        self.assertEqual(self.source(session_id="s-1"), ["GATE_PLAN_CHANGED"])
        self.assertEqual(self.builds(), {})

    def test_a_missing_or_bad_session_id_blocks(self):
        for session in (None, "", 7, "has space", "../x"):
            with self.subTest(session=session):
                payload = self.payload("Write", {"file_path": str(self.project / "src/a.py"), "content": "x"})
                payload["session_id"] = session
                _, verdicts = __import__("pre_tool_use").run(json.dumps(payload), self.guards(), root=self.project)
                self.assertEqual([v.code for v in verdicts], ["GATE_RECORD_FAILED"])
        self.assertEqual(self.builds(), {})

    def test_a_record_that_cannot_be_written_blocks(self):
        (self.project / "_bmad-output/guard").write_text("a file where the folder should be")
        self.assertEqual(self.source(session_id="s-1"), ["GATE_RECORD_FAILED"])

    def test_a_write_error_blocks_and_leaves_no_temp_file(self):
        with mock.patch.object(os, "replace", side_effect=OSError("disk full")):
            self.assertEqual(self.source(session_id="s-1"), ["GATE_RECORD_FAILED"])
        self.assertEqual(list((self.project / wg.RECORD_REL).iterdir()), [])

    def test_a_broken_record_blocks_instead_of_being_overwritten(self):
        for text in (
            "{not json",
            "[]",
            json.dumps({"sessions": "x", "first_at": "t"}),
            json.dumps({"sessions": [1], "first_at": "t"}),
        ):
            with self.subTest(text=text):
                self.write(f"{wg.RECORD_REL}/2-3.json", text)
                self.assertEqual(self.source(session_id="s-1"), ["GATE_RECORD_FAILED"])
                self.assertEqual((self.project / wg.RECORD_REL / "2-3.json").read_text(), text)

    def test_the_failure_message_names_the_next_step_and_leaks_nothing(self):
        (self.project / "_bmad-output/guard").write_text("x")
        raw = json.dumps(
            self.payload("Write", {"file_path": str(self.project / "src/a.py"), "content": "CODE-MARKER-9"})
        )
        code, _, err = self.entry(raw)
        self.assertEqual(code, 2)
        self.assertIn("GATE_RECORD_FAILED", err)
        self.assertIn("Ask the developer", err)
        self.assertNotIn("CODE-MARKER-9", err)

    def test_the_agent_cannot_write_the_record_itself(self):
        self.assertEqual(self.source(f"{wg.RECORD_REL}/2-3.json", content="{}"), ["DOC_GUARD_RECORD"])


class BashTests(GateCase):
    def blocked(self, *commands: str) -> None:
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["GATE_NO_ACTIVE_PLAN"], command)

    def allowed(self, *commands: str) -> None:
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), [], command)

    def setUp(self):
        super().setUp()
        (self.project / PLAN).unlink()  # no plan: every gated target blocks

    def test_explicit_write_targets_in_source_are_gated(self):
        self.blocked(
            "echo x > src/app.py",
            "echo x >> src/app.py",
            "cat <<EOF > src/app.py\nprint(1)\nEOF",
            "printf 'x' | tee src/app.py",
            "cp tmp/a.py src/app.py",
            "mv tmp/a.py src/app.py",
            "mv src/app.py tmp/old.py",
            "install -m 644 a.py src/",
            "sed -i 's/a/b/' src/app.py",
            "sed -i.bak 's/a/b/' src/app.py",
            "perl -pi -e 's/a/b/' src/app.py",
            "rm src/old.py",
            "curl -o src/vendor.js https://example.invalid/x.js",
            "dd if=/dev/zero of=src/blob bs=1 count=1",
            "git rm src/old.py",
            "echo x > README.md",
            f"echo x > {self.project}/src/app.py",
            "cd src && echo x > app.py",
            "cd src; cp ../a.py b.py",
            "ls && echo x > src/a.py",
            "sudo sh -c 'echo x > src/a.py'",
            "echo $(echo x > src/a.py)",
            "echo x > src/$name.py",
            "rm src/*.py",
            "echo x > *.py",
        )

    def test_scratch_tmp_and_dev_null_are_not_gated(self):
        self.allowed(
            "python3 -m unittest > /dev/null",
            "pytest 2>&1 | tail -5",
            "pytest > /dev/null 2>&1",
            "pytest 2>/dev/null",
            "echo x > /dev/stderr",
            "echo x > /tmp/out.txt",
            f"echo x > {self.system_tmp}/out.txt",
            "echo x > tmp/out.txt",
            "npm test | tee build/test.log",
            "cp a.py dist/a.py",
            "rm -rf build dist tmp node_modules",
            "mv tmp/a tmp/b > /dev/null",
            "cd build && cmake .. > log.txt",
            "cd /tmp && echo x > a.txt",
            "cd tmp; echo x > a.txt",
            "git diff > /tmp/x.diff",
            "git diff > $TMPDIR/x.diff",
            "echo x > $OUT",
            "echo x > $(mktemp)",
            "echo x > ~/notes.txt",
            "echo x > ../outside.txt",
            "echo x > /var/tmp/x",
        )

    def test_a_write_outside_the_project_is_not_gated_like_the_write_tool(self):
        outside = self.project.parent / "elsewhere"
        outside.mkdir()
        self.allowed(
            f"sed -i 's/a/b/' {outside}/file.py",
            f"sed -i '' 's/a/b/' {outside}/file.py",  # macOS sed: the empty word is the backup suffix
            f"sed -i.bak 's/a/b/' {outside}/file.py > /dev/null",
            f"sed -i -e 's/a/b/' {outside}/file.py",
            f"echo x > {outside}/file.py",
            f"cp src/app.py {outside}/file.py",
            f"cd {outside} && sed -i '' 's/a/b/' file.py",
            "sed -i '' 's/a/b/' ../elsewhere/file.py",
        )
        self.assertEqual(self.codes("Write", {"file_path": str(outside / "file.py"), "content": "x"}), [])

    def test_a_write_to_a_link_that_points_into_the_project_is_still_gated(self):
        outside = self.project.parent / "elsewhere"
        outside.mkdir()
        (self.project / "src").mkdir()
        os.symlink(self.project / "src/app.py", outside / "link.py")
        os.symlink(self.project / "src", outside / "linked-src")
        self.blocked(
            f"sed -i '' 's/a/b/' {outside}/link.py",
            f"echo x > {outside}/link.py",
            f"echo x > {outside}/linked-src/app.py",
        )

    def test_the_sed_script_is_not_a_target_and_a_redirect_still_is(self):
        self.blocked("sed 's/a/b/' src/in.py > src/out.py", "sed -n p src/in.py > src/out.py")
        self.allowed("sed 's/a/b/' src/in.py", "sed -i '' 's/a/b/' /tmp/x/file.py")

    def test_allowed_project_paths_are_not_gated(self):
        self.allowed(
            "echo x > docs/a.md",
            "cp a.md _bmad-output/b.md",
            "sed -i 's/a/b/' _bmad-output/implementation-artifacts/spec-x.md",
            "tee .claude/commands/x.md < a",
            "echo x > .bmad-guard/tmp/a",
        )

    def test_commands_without_a_write_target_are_not_gated(self):
        self.allowed(
            "ls src",
            "cat src/app.py",
            "grep -r todo src",
            "pytest tests/",
            "python3 -m unittest discover",
            "git status",
            "git commit -m 'x'",
            "git checkout main",
            "git checkout -b feature/x",
            "git restore --staged src/app.py",
            "find src -name '*.py' -exec grep -l x {} +",
            "npm install",
            "echo 'x > src/app.py'",
            "echo x 2>&1",
        )

    def test_a_cd_we_cannot_follow_leaves_only_absolute_targets_gated(self):
        self.allowed('cd "$DIR" && echo x > src/a.py')
        self.blocked(f'cd "$DIR" && echo x > {self.project}/src/a.py')

    def test_an_approved_plan_lets_the_write_through_and_records_the_session(self):
        self.write(PLAN, PLAN_TEXT)
        self.approve()
        self.assertEqual(self.bash("echo x > src/app.py", session_id="s-bash-1"), [])
        self.assertEqual(self.builds()["2-3"]["sessions"], ["s-bash-1"])

    def test_a_command_with_no_gated_target_records_nothing(self):
        self.write(PLAN, PLAN_TEXT)
        self.approve()
        self.assertEqual(self.bash("pytest > /dev/null", session_id="s-1"), [])
        self.assertEqual(self.builds(), {})

    def test_a_command_that_cannot_be_read_is_blocked_by_the_command_guard_only(self):
        self.assertEqual(self.bash("echo 'unclosed > src/a.py"), ["CMD_UNPARSABLE"])


class FailClosedTests(GateCase):
    def test_a_gate_that_crashes_blocks_the_call(self):
        with mock.patch.object(wg, "check_gate", side_effect=RuntimeError("boom-detail")):
            code, _, err = self.entry(
                json.dumps(self.payload("Write", {"file_path": str(self.project / "src/a.py"), "content": "x"}))
            )
        self.assertEqual(code, 2)
        self.assertIn("GUARD_ERROR (write-gate)", err)
        self.assertNotIn("boom-detail", err)
        self.assertEqual([e["reason_code"] for e in queue(self.project)], ["GUARD_ERROR"])

    def test_a_sibling_skill_that_is_missing_blocks_with_a_clear_reason(self):
        self.approve()
        with mock.patch.object(wg, "HERE", Path("/nonexistent/tool-guard/scripts")):
            with mock.patch.dict(sys.modules):
                sys.modules.pop("check_gate", None)
                self.assertEqual(self.source(), ["GATE_ERROR"])

    def test_broken_ready_for_dev_settings_block(self):
        self.approve()
        with mock.patch.object(wg, "READY_SKILL", "no-such-skill"):
            self.assertEqual(self.source(), ["GATE_ERROR"])

    def test_too_many_files_to_scan_block(self):
        with mock.patch.object(wg, "MAX_FILES", 1):
            self.assertEqual(self.source(), ["GATE_ERROR"])

    def test_malformed_hook_input_blocks(self):
        for raw in ("", "{not json", "[]", json.dumps({"tool_name": "Write"}), json.dumps({"tool_input": {}})):
            with self.subTest(raw=raw):
                code, _, err = self.entry(raw)
                self.assertEqual(code, 2)
                self.assertIn("BAD_HOOK_INPUT", err)

    def test_a_bash_call_with_no_command_text_is_left_to_the_command_guard(self):
        self.assertEqual(self.codes("Bash", {}), ["CMD_UNPARSABLE"])


class EventTests(GateCase):
    def test_events_and_messages_carry_the_rule_never_the_path_or_the_content(self):
        folder, marker = "SECRET-FOLDER-NAME-987", "SECRET-CODE-MARKER-321"
        self.write(
            f"_bmad-output/{folder}/spec-x.md", PLAN_TEXT.replace("story: 2-3\n", "").replace("# Plan", f"# {marker}")
        )
        (self.project / PLAN).unlink()
        raw = json.dumps(self.payload("Write", {"file_path": str(self.project / folder / "app.py"), "content": marker}))
        code, _, err = self.entry(raw)
        self.assertEqual(code, 2)
        events = queue(self.project)
        self.assertIn("GATE_PLAN_NOT_APPROVED", [e["reason_code"] for e in events])
        for text in (json.dumps(events), err.replace(f"_bmad-output/{folder}/spec-x.md", "<plan>")):
            self.assertNotIn(marker, text)
            self.assertNotIn(f"{folder}/app.py", text)
        for event in events:
            self.assertEqual(
                (event["guard"], event["verdict"], event["tool"], event["rule"], event["session_id"]),
                ("tool-guard", "block", "Write", "write-gate", "s-test"),
            )
            self.assertEqual(event.get("path", ""), "")
            self.assertNotIn(folder, json.dumps(event))

    def test_an_allowed_write_records_no_event(self):
        self.approve()
        raw = json.dumps(self.payload("Write", {"file_path": str(self.project / "src/a.py"), "content": "x"}))
        self.assertEqual(self.entry(raw), (0, "", ""))
        self.assertEqual(queue(self.project), [])


class CliTests(GateCase):
    def check(self, *extra: str):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.project)}), contextlib.redirect_stdout(out):
            code = wg.main(["--check", *extra])
        return code, out.getvalue()

    def test_check_prints_the_same_reasons_and_exits_1(self):
        code, text = self.check()
        self.assertEqual(code, 1)
        self.assertIn("GATE_PLAN_NOT_APPROVED (write-gate)", text)
        self.assertIn("Warden", text)
        raw = json.dumps(self.payload("Write", {"file_path": str(self.project / "src/a.py"), "content": "x"}))
        _, _, err = self.entry(raw)
        for line in text.splitlines()[1:]:
            self.assertIn(line, err)

    def test_check_lists_every_reason(self):
        self.write(STORY, STORY_TEXT.replace("confidence: 90", "confidence: 10"))
        _, text = self.check()
        self.assertIn("GATE_PLAN_NOT_APPROVED", text)
        self.assertIn("GATE_READY_LOW_CONFIDENCE", text)

    def test_check_passes_when_the_plan_is_approved_and_the_story_ready(self):
        self.approve()
        code, text = self.check()
        self.assertEqual(code, 0)
        self.assertIn("OK", text)

    def test_check_with_no_plan(self):
        (self.project / PLAN).unlink()
        code, text = self.check()
        self.assertEqual(code, 1)
        self.assertIn("GATE_NO_ACTIVE_PLAN", text)

    def test_check_does_not_record_a_session_or_change_the_plan(self):
        self.approve()
        self.sub(PLAN, "One CSV", "Two CSV")
        before = (self.project / PLAN).read_text()
        code, text = self.check()
        self.assertEqual(code, 1)
        self.assertIn("GATE_PLAN_CHANGED", text)
        self.assertEqual((self.project / PLAN).read_text(), before)
        self.assertEqual(self.builds(), {})

    def test_check_takes_the_root_from_the_option(self):
        with mock.patch.dict(os.environ, {}, clear=False), contextlib.redirect_stdout(io.StringIO()) as out:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            code = wg.main(["--check", "--root", str(self.project)])
        self.assertEqual(code, 1)
        self.assertIn("GATE_PLAN_NOT_APPROVED", out.getvalue())

    def test_check_reports_a_gate_that_cannot_run(self):
        with mock.patch.object(wg, "check_gate", side_effect=wg.GateError("a skill is missing")):
            code, text = self.check()
        self.assertEqual(code, 1)
        self.assertIn("GATE_ERROR", text)

    def test_the_script_runs_from_the_command_line(self):
        script = ROOT / "module/skills/tool-guard/scripts/write_gate.py"
        done = subprocess.run(
            [sys.executable, str(script), "--check", "--root", str(self.project)], capture_output=True, text=True
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("GATE_PLAN_NOT_APPROVED", done.stdout)
        self.approve()
        done = subprocess.run(
            [sys.executable, str(script), "--check", "--root", str(self.project)], capture_output=True, text=True
        )
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(subprocess.run([sys.executable, str(script)], capture_output=True, text=True).returncode, 2)


class DocsTests(unittest.TestCase):
    def test_the_rules_doc_names_every_gate_rule_id(self):
        text = (gc.SKILL_ROOT / "references/guard-rules.md").read_text(encoding="utf-8")
        ids = [
            "GATE_NO_ACTIVE_PLAN",
            "GATE_MANY_ACTIVE_PLANS",
            "GATE_PLAN_NOT_APPROVED",
            "GATE_PLAN_CHANGED",
            "GATE_STATE_UNREADABLE",
            "GATE_STORY_NOT_FOUND",
            "GATE_RECORD_FAILED",
            "GATE_ERROR",
        ]
        for rule_id in ids:
            with self.subTest(rule=rule_id):
                self.assertIn(f"`{rule_id}`", text)

    def test_every_ready_for_dev_reason_has_a_gate_rule_in_the_doc(self):
        text = (gc.SKILL_ROOT / "references/guard-rules.md").read_text(encoding="utf-8")
        script = (SKILLS / "ready-for-dev-gate/scripts/ready_for_dev.py").read_text(encoding="utf-8")
        import re

        codes = set(re.findall(r'"code": "([A-Z_]+)"', script)) | set(re.findall(r'GateError\("([A-Z_]+)"', script))
        self.assertTrue(codes)
        for code in codes:
            with self.subTest(code=code):
                self.assertIn(f"GATE_READY_{code}", text)


if __name__ == "__main__":
    unittest.main()


class SourceCheckerTests(unittest.TestCase):
    """The pre-push hook's view of "source": the write gate's allowed paths, plus the files BMAD and setup install."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.is_source = wg.source_checker(Path(tmp.name))

    def test_install_and_setup_files_are_not_source(self):
        for rel in (
            ".gitignore",
            ".github/agents/bmad-agent-dev.agent.md",
            ".github/agents/bmad-agent-ux-designer.agent.md",
        ):
            self.assertFalse(self.is_source(rel), rel)

    def test_other_files_stay_source(self):
        for rel in (
            "src/app.py",
            "README.md",
            ".github/workflows/ci.yml",
            ".github/agents/my-agent.agent.md",
            ".github/agents/bmad-agent-dev.md",
            ".github/agents/sub/bmad-agent-dev.agent.md",
            ".github/bmad-agent-dev.agent.md",
            "src/.gitignore",
        ):
            self.assertTrue(self.is_source(rel), rel)

    def test_the_write_gate_still_gates_install_files(self):
        """Only pre-push skips them: an agent write to .github/agents still needs an approved plan."""
        entries = wg.allowed_entries(())
        self.assertFalse(wg.in_allowed(entries, (".github", "agents", "bmad-agent-dev.agent.md")))
        self.assertFalse(wg.in_allowed(entries, (".gitignore",)))
