# /// script
# requires-python = ">=3.10"
# ///
"""Tests for Sentry (agent-approval-gate-check) and Warden (agent-approval-grant).

Scripts run as real command-line programs against throwaway git projects.
"""

from __future__ import annotations

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
SKILLS = ROOT / "module/skills"
GATE = SKILLS / "agent-approval-gate-check/scripts/check_gate.py"
GRANT = SKILLS / "agent-approval-grant/scripts/grant_approval.py"
REOPEN = SKILLS / "agent-approval-grant/scripts/reopen_document.py"
ENV = {k: v for k, v in os.environ.items() if not k.startswith("BMAD_GUARD_")}

spec = importlib.util.spec_from_file_location("approval_core", SKILLS / "agent-approval-grant/scripts/approval_core.py")
core = importlib.util.module_from_spec(spec)
sys.modules["approval_core_under_test"] = core
spec.loader.exec_module(core)

BRIEF = "---\ntitle: Demo brief\nstatus: draft\n---\n\n# Brief\n\nThe product.\n"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def make_project(root: Path, brief: str = BRIEF) -> Path:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test.user@example.invalid")
    (root / "_bmad/custom").mkdir(parents=True)
    (root / "_bmad/custom/project.json").write_text('{"project_name": "demo-api"}')
    (root / "docs").mkdir()
    (root / "docs/brief.md").write_text(brief)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def run(script: Path, project: Path, *args: str, script_cwd: Path | None = None) -> tuple[int, dict | None, str]:
    proc = subprocess.run(
        [sys.executable, str(script), *args], cwd=script_cwd or project, env=ENV, capture_output=True, text=True
    )
    out = proc.stdout.strip()
    return proc.returncode, (json.loads(out) if out else None), proc.stderr


def doc_args(project: Path, doc: str = "docs/brief.md", doc_type: str = "brief") -> list[str]:
    return ["--doc-path", str(project / doc), "--doc-type", doc_type, "--state-file", str(state_path(project))]


def state_path(project: Path) -> Path:
    return project / "_bmad/custom/approval-state.json"


def state(project: Path) -> dict:
    return json.loads(state_path(project).read_text())


def events(project: Path) -> list[dict]:
    q = project / ".bmad-guard/events/queue.jsonl"
    return [json.loads(line) for line in q.read_text().splitlines()] if q.exists() else []


def gate(project: Path, *extra: str) -> dict:
    code, result, err = run(GATE, project, *doc_args(project), *extra)
    assert code == 0, err
    return result


def grant(project: Path, *extra: str) -> dict:
    code, result, err = run(GRANT, project, *doc_args(project), "--approved-by", "dev", *extra)
    assert code == 0, err
    return result


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        (self.tmp / "p").mkdir()
        self.project = make_project(self.tmp / "p")

    def tearDown(self):
        self._tmp.cleanup()

    def brief(self) -> str:
        return (self.project / "docs/brief.md").read_text()


# --- shared core -----------------------------------------------------------------------------------


class CoreTests(unittest.TestCase):
    def test_both_skill_copies_are_identical(self):
        a = (SKILLS / "agent-approval-grant/scripts/approval_core.py").read_bytes()
        b = (SKILLS / "agent-approval-gate-check/scripts/approval_core.py").read_bytes()
        self.assertEqual(a, b, "copy approval_core.py from agent-approval-grant to agent-approval-gate-check")

    def test_get_and_set_status(self):
        self.assertEqual(core.get_status(BRIEF, "status"), "draft")
        self.assertEqual(core.get_status(core.set_status(BRIEF, "status", "approved"), "status"), "approved")
        self.assertEqual(core.get_status("no frontmatter", "status"), "")

    def test_set_status_value_is_literal(self):
        text = core.set_status(BRIEF, "status", r"a\1b")
        self.assertEqual(core.get_status(text, "status"), r"a\1b")

    def test_set_status_errors_and_create(self):
        with self.assertRaises(ValueError):
            core.set_status("no frontmatter", "status", "x")
        with self.assertRaises(ValueError):
            core.set_status(BRIEF, "approval_status", "x")
        created = core.set_status(BRIEF, "approval_status", "approved", create_missing=True)
        self.assertEqual(core.get_status(created, "approval_status"), "approved")

    def test_bump_version(self):
        text, v = core.bump_version(BRIEF)
        self.assertEqual(v, 1)
        self.assertEqual(core.bump_version(text)[1], 2)
        self.assertEqual(core.bump_version("---\nversion: oops\n---\n")[1], 1)

    def test_build_hash_ignores_progress_fields_and_logs(self):
        plan = "---\ntitle: P\nbuild_started_at: 1\n---\n## Tasks\n- [ ] a\n## Implementation Notes\nx\n"
        moved = "---\ntitle: P\nbuild_started_at: 2\n---\n## Tasks\n- [x] a\n## Implementation Notes\ny\n"
        changed = "---\ntitle: P2\nbuild_started_at: 1\n---\n## Tasks\n- [ ] a\n## Implementation Notes\nx\n"
        self.assertEqual(core.build_hash(plan), core.build_hash(moved))
        self.assertNotEqual(core.build_hash(plan), core.build_hash(changed))
        self.assertNotEqual(core.content_hash(plan, "full"), core.content_hash(moved, "full"))

    def test_path_key_is_relative_and_legacy_absolute_still_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            doc = root / "docs/brief.md"
            self.assertEqual(core.path_key(doc, root), "docs/brief.md")
            self.assertTrue(core.same_document({"doc_path": "docs/brief.md"}, doc, root))
            self.assertTrue(core.same_document({"doc_path": str(doc)}, doc, root))
            self.assertFalse(core.same_document({"doc_path": "docs/other.md"}, doc, root))

    def test_load_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            self.assertEqual(core.load_state(path), ({}, True))
            path.write_text("{broken")
            self.assertEqual(core.load_state(path), ({}, False))
            path.write_text("[]")
            self.assertEqual(core.load_state(path), ({}, False))

    @unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11")
    def test_customize_files_are_valid_toml(self):
        import tomllib

        for skill in ("agent-approval-gate-check", "agent-approval-grant"):
            data = tomllib.loads((SKILLS / skill / "customize.toml").read_text())
            self.assertEqual(data["agent"]["state_file"], "{project-root}/_bmad/custom/approval-state.json")


# --- Sentry ----------------------------------------------------------------------------------------


class GateTests(Base):
    def test_missing_document(self):
        code, result, _ = run(GATE, self.project, *doc_args(self.project, "docs/none.md"))
        self.assertEqual((code, result), (0, {"result": "blocked", "reason": "missing"}))

    def test_never_approved(self):
        self.assertEqual(gate(self.project), {"result": "blocked", "reason": "unapproved"})

    def test_approved_passes_with_who_and_when(self):
        grant(self.project)
        result = gate(self.project)
        self.assertEqual(result["result"], "ok")
        self.assertEqual(result["approved_by"], "dev")
        self.assertTrue(result["approved_at"])

    def test_other_document_of_same_type_is_unapproved(self):
        grant(self.project)
        (self.project / "docs/brief2.md").write_text(BRIEF.replace("draft", "approved"))
        code, result, _ = run(GATE, self.project, *doc_args(self.project, "docs/brief2.md"))
        self.assertEqual(result, {"result": "blocked", "reason": "unapproved"})

    def test_status_not_approved_is_unapproved(self):
        grant(self.project)
        (self.project / "docs/brief.md").write_text(self.brief().replace("status: approved", "status: review"))
        self.assertEqual(gate(self.project)["reason"], "unapproved")

    def test_edit_after_approval_is_stale_and_reverts_to_draft(self):
        grant(self.project)
        (self.project / "docs/brief.md").write_text(self.brief() + "\nSneaky edit.\n")
        result = gate(self.project)
        self.assertEqual((result["result"], result["reason"]), ("blocked", "stale"))
        self.assertEqual(core.get_status(self.brief(), "status"), "draft")

    def test_check_can_leave_a_stale_document_alone(self):
        # the write gate hook (tool-guard) asks without resetting the plan
        spec = importlib.util.spec_from_file_location("check_gate_under_test", GATE)
        check_gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check_gate)
        grant(self.project)
        doc = self.project / "docs/brief.md"
        doc.write_text(self.brief() + "\nSneaky edit.\n")
        args = (doc, "brief", state_path(self.project), "status", "draft", "approved")
        result, _ = check_gate.check(*args, reset_stale=False)
        self.assertEqual((result["result"], result["reason"]), ("blocked", "stale"))
        self.assertEqual(core.get_status(doc.read_text(), "status"), "approved")
        result, _ = check_gate.check(*args)  # the default still resets, as the CLI does
        self.assertEqual(result["reason"], "stale")
        self.assertEqual(core.get_status(doc.read_text(), "status"), "draft")

    def test_reopened_approval_cannot_be_revived_by_hand(self):
        grant(self.project)
        approved_text = self.brief()
        run(REOPEN, self.project, *doc_args(self.project))
        (self.project / "docs/brief.md").write_text(approved_text)  # status: approved written back, same content
        self.assertEqual(gate(self.project), {"result": "blocked", "reason": "superseded"})

    def test_broken_state_file_blocks(self):
        grant(self.project)
        state_path(self.project).write_text("{not json")
        self.assertEqual(gate(self.project), {"result": "blocked", "reason": "state_unreadable"})

    def test_approval_works_in_a_clone_at_another_path(self):
        grant(self.project)
        clone = self.tmp / "teammate-checkout"
        git(self.tmp, "clone", "-q", str(self.project), str(clone))
        self.assertEqual(gate(clone)["result"], "ok")

    def test_build_mode_ignores_progress_updates(self):
        plan = (
            "---\ntitle: Plan\nstatus: planning\nbuild_run_id: r1\n---\n## Tasks\n- [ ] one\n## Implementation Notes\n"
        )
        (self.project / "docs/plan.md").write_text(plan)
        args = [*doc_args(self.project, "docs/plan.md", "build-plan"), "--status-field", "approval_status"]
        code, _, err = run(GRANT, self.project, *args, "--create-status-field", "--hash-mode", "build", "--no-commit")
        self.assertEqual(code, 0, err)
        text = (self.project / "docs/plan.md").read_text()
        text = text.replace("status: planning", "status: coding").replace("- [ ] one", "- [x] one")
        (self.project / "docs/plan.md").write_text(text + "Did it.\n")
        code, result, _ = run(GATE, self.project, *args)
        self.assertEqual(result["result"], "ok")
        (self.project / "docs/plan.md").write_text(text.replace("- [x] one", "- [x] one\n- [ ] two"))
        code, result, _ = run(GATE, self.project, *args)
        self.assertEqual(result["reason"], "stale")  # a real plan change still counts

    def test_every_result_is_an_event(self):
        gate(self.project)
        grant(self.project)
        gate(self.project, "--downstream", "PRD generation")
        gate_events = [e for e in events(self.project) if e["guard"] == "sentry"]
        self.assertEqual(
            [(e["verdict"], e["reason_code"]) for e in gate_events],
            [("block", "GATE_UNAPPROVED"), ("allow", "GATE_OK")],
        )
        self.assertEqual(gate_events[1]["downstream"], "PRD generation")
        self.assertEqual(gate_events[1]["target_path"], "docs/brief.md")
        self.assertEqual(gate_events[1]["project"], "demo-api")


# --- Warden ----------------------------------------------------------------------------------------


class GrantTests(Base):
    def test_grant_sets_status_bumps_version_and_records(self):
        result = grant(self.project, "--requested-at", "2026-11-25T10:00:00Z", "--approved-at", "2026-11-25T10:05:00Z")
        self.assertEqual(core.get_status(self.brief(), "status"), "approved")
        self.assertIn("version: 1", self.brief())
        entry = state(self.project)["brief"]
        self.assertEqual(entry["doc_path"], "docs/brief.md")
        self.assertEqual(entry["approved_by"], "dev")
        self.assertEqual(entry["git_user"], "Test User")
        self.assertEqual(entry["review_duration_seconds"], 300)
        self.assertFalse(entry["superseded"])
        self.assertEqual(result["version"], 1)
        self.assertNotIn("test.user@example.invalid", state_path(self.project).read_text())

    def test_grant_commits_document_and_state_together(self):
        result = grant(self.project)
        self.assertTrue(result["git_commit"]["ok"], result["git_commit"])
        files = git(self.project, "show", "--name-only", "--format=%s", "HEAD").splitlines()
        self.assertEqual(files[0], "Approve brief v1")
        self.assertEqual(set(files[1:]) - {""}, {"docs/brief.md", "_bmad/custom/approval-state.json"})

    def test_grant_commit_leaves_other_staged_work_alone(self):
        (self.project / "other.txt").write_text("unrelated")
        git(self.project, "add", "other.txt")
        grant(self.project)
        self.assertNotIn("other.txt", git(self.project, "show", "--name-only", "--format=", "HEAD"))
        self.assertIn("A  other.txt", git(self.project, "status", "--short"))

    def test_no_commit(self):
        result = grant(self.project, "--no-commit")
        self.assertTrue(result["git_commit"]["skipped"])
        self.assertEqual(git(self.project, "log", "--format=%s", "-1"), "init")

    def test_second_approval_keeps_first_in_history(self):
        grant(self.project)
        run(REOPEN, self.project, *doc_args(self.project))
        grant(self.project)
        entry = state(self.project)["brief"]
        self.assertEqual(entry["version"], 2)
        self.assertEqual(len(entry["history"]), 1)
        self.assertTrue(entry["history"][0]["superseded"])
        self.assertEqual(entry["history"][0]["version"], 1)

    def test_other_doc_types_untouched(self):
        state_path(self.project).write_text(json.dumps({"prd": {"doc_path": "docs/prd.md", "approved_hash": "x"}}))
        grant(self.project)
        self.assertEqual(state(self.project)["prd"], {"doc_path": "docs/prd.md", "approved_hash": "x"})

    def test_grant_event(self):
        grant(self.project)
        e = [e for e in events(self.project) if e["guard"] == "warden"][0]
        self.assertEqual(
            (e["verdict"], e["reason_code"], e["approved_by"], e["git_user"]), ("allow", "APPROVED", "dev", "Test User")
        )
        self.assertEqual(e["target_sha256"], state(self.project)["brief"]["approved_hash"])

    def test_errors_exit_2_and_change_nothing(self):
        cases = [
            doc_args(self.project, "docs/none.md"),
            [*doc_args(self.project), "--requested-at", "yesterday"],
            [*doc_args(self.project), "--status-field", "approval_status"],
        ]
        for args in cases:
            code, _, err = run(GRANT, self.project, *args)
            self.assertEqual(code, 2, args)
            self.assertIn("error:", err)
        self.assertEqual(self.brief(), BRIEF)

    def test_broken_state_file_is_not_overwritten(self):
        state_path(self.project).write_text("{not json")
        code, _, err = run(GRANT, self.project, *doc_args(self.project))
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)
        self.assertEqual(self.brief(), BRIEF)
        self.assertEqual(state_path(self.project).read_text(), "{not json")


class ReopenTests(Base):
    def test_reopen_marks_superseded_with_who_and_when(self):
        grant(self.project)
        code, result, _ = run(REOPEN, self.project, *doc_args(self.project), "--reopened-by", "lead")
        self.assertEqual(code, 0)
        self.assertTrue(result["superseded"])
        self.assertEqual(core.get_status(self.brief(), "status"), "draft")
        entry = state(self.project)["brief"]
        self.assertEqual((entry["superseded"], entry["superseded_by"]), (True, "lead"))
        self.assertTrue(entry["superseded_at"])
        self.assertIn("approved_hash", entry)  # kept, not deleted
        e = [e for e in events(self.project) if e["reason_code"] == "REOPENED"][0]
        self.assertEqual((e["guard"], e["superseded"]), ("warden", "true"))

    def test_reopen_without_matching_approval(self):
        code, result, _ = run(REOPEN, self.project, *doc_args(self.project))
        self.assertEqual((code, result["superseded"]), (0, False))
        self.assertFalse(state_path(self.project).exists())


class LedgerMissingTests(Base):
    def test_gate_still_decides_and_warns_without_ledger(self):
        lone = self.tmp / "skills"
        shutil.copytree(SKILLS / "agent-approval-gate-check", lone / "agent-approval-gate-check")
        code, result, err = run(
            lone / "agent-approval-gate-check/scripts/check_gate.py", self.project, *doc_args(self.project)
        )
        self.assertEqual((code, result["reason"]), (0, "unapproved"))
        self.assertIn("Ledger (agent-project-logger) not installed", err)
        self.assertEqual(events(self.project), [])


if __name__ == "__main__":
    unittest.main()
