# /// script
# requires-python = ">=3.10"
# ///
"""Tests for the pre-commit hook (Phase 2, T6): scan, approval fields, tech stack, events, fail closed.

Every test uses a real, throwaway git repository (tests/hook_harness.py) and runs the installed hook on what is
staged. Warden's own scripts approve the fake documents, so the matching rules are tested against what Warden really
writes. Key-shaped fakes are built at run time; no literal token is committed. All data is fake.
"""

from __future__ import annotations

import contextlib
import io
import json
import runpy
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from tests.hook_harness import FAKE, HOOKS, HOOKS_REL, LIST_REL, MARKER, STATE_REL, HookTestCase
from tests.test_story_coverage import POINTER, ac, make_frd

KEY = FAKE["github-token"]
PHONE = FAKE["phone-us"]
AWS_EXAMPLE = "AK" + "IA" + "IOSFODNN7EXAMPLE"  # AWS's published documentation key, allow-listed
HIPAA = '[agent]\nphi = "block"\n'
HIPAA_REL = "_bmad/custom/prompt-guard.toml"
PATTERNS_REL = "_bmad/custom/project/guard/prompt-patterns.json"
BRIEF = "---\ntitle: Fake brief\nstatus: draft\n---\n\n# Fake brief\n\nPlain text.\n"
PLAN = "---\ntitle: Plan\nstatus: in-progress\napproval_status: pending-approval\n---\n\n# Plan\n\n- [ ] one\n\nBody.\n"
STORY = "---\nconfidence: 90\n---\n\n# Story\n\n## Open questions\n\n- Q-1: Which roles can export?\n"
RESOLUTION = "  Resolved: Admins only. — Priya BA, 2026-11-20\n"
LIST = '---\nstatus: draft\n---\n# Fake list\n[allowed]\nnpm = ["react", "@types/*"]\npython = ["django"]\n'
PACKAGE = '{"name": "app", "dependencies": {"react": "^18.0.0"}}\n'


def package(*names: str) -> str:
    return json.dumps({"name": "app", "dependencies": {name: "1.0.0" for name in names}}) + "\n"


class NothingToCheckTests(HookTestCase):
    def test_nothing_staged_exits_zero_with_one_allow_event(self):
        proc = self.hook()
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))
        self.assertEqual(self.codes(), ["COMMIT_NOTHING_TO_CHECK"])
        self.assertEqual(self.events()[0]["verdict"], "allow")

    def test_only_a_deleted_file_is_nothing_to_check(self):
        self.write("old.txt", f"token = '{KEY}'\n")
        self.commit_all("add")
        self.git("rm", "-q", "old.txt")
        proc = self.hook()
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertEqual(self.codes(), ["COMMIT_NOTHING_TO_CHECK"])

    def test_a_plain_file_is_allowed_with_one_allow_event(self):
        self.write("src/app.py", "print('hello')\n")
        self.stage("src/app.py")
        proc = self.hook()
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertEqual(self.codes(), ["COMMIT_ALLOWED"])
        self.assertEqual(self.events()[0]["file_count"], "1")


class SecretScanTests(HookTestCase):
    def test_a_fake_key_in_a_staged_file_blocks(self):
        self.write("src/app.py", f"token = '{KEY}'\n")
        self.stage("src/app.py")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_SECRET", proc.stderr)
        self.assertIn("src/app.py", proc.stderr)
        self.assertIn("secret.github-token", proc.stderr)
        self.assertNotIn(KEY, proc.stderr + proc.stdout)
        self.assertEqual(self.codes(), ["COMMIT_SECRET"])

    def test_the_documented_fake_key_is_allowed(self):
        self.write("docs/aws.md", f"Example key: {AWS_EXAMPLE}\n")
        self.stage("docs/aws.md")
        self.assertEqual(self.hook().returncode, 0)

    def test_it_reads_the_staged_blob_not_the_work_tree(self):
        self.write("src/app.py", f"token = '{KEY}'\n")
        self.stage("src/app.py")
        self.write("src/app.py", "token = 'from the environment'\n")  # cleaned, but not staged again
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_SECRET", proc.stderr)

    def test_a_secret_only_in_the_work_tree_is_not_the_commits_business(self):
        self.write("src/app.py", "token = 'from the environment'\n")
        self.stage("src/app.py")
        self.write("src/app.py", f"token = '{KEY}'\n")  # edited after staging, not staged
        self.assertEqual(self.hook().returncode, 0)

    def test_only_the_lines_a_commit_adds_are_scanned(self):
        self.write("src/old.py", f"token = '{KEY}'\nvalue = 1\n")
        self.commit_all("an old file that already holds a flagged value")
        self.write("src/old.py", f"token = '{KEY}'\nvalue = 2\n")
        self.stage("src/old.py")
        self.assertEqual(self.hook().returncode, 0)
        self.write("src/old.py", f"token = '{KEY}'\nvalue = 2\nother = '{FAKE['gitlab-token']}'\n")
        self.stage("src/old.py")
        self.assertEqual(self.hook().returncode, 1)

    def test_binary_files_and_lock_files_are_skipped(self):
        self.write_bytes("assets/blob.bin", b"\x00\x01" + f"token = '{KEY}'".encode())
        self.write("package-lock.json", f'{{"integrity": "{KEY}"}}\n')
        self.stage("assets/blob.bin", "package-lock.json")
        proc = self.hook()
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))

    def test_awkward_file_names_are_scanned_as_that_file(self):
        for name in ("src/a b[1].txt", "src/café.txt", "src/*.txt"):
            with self.subTest(name=name):
                self.write(name, f"token = '{KEY}'\n")
                self.stage(name)
                self.assertEqual(self.hook().returncode, 1)
                self.git("reset", "-q", "--", name)

    def test_a_crlf_file_is_scanned(self):
        self.write("src/win.py", f"token = '{KEY}'\r\n")
        self.stage("src/win.py")
        self.assertEqual(self.hook().returncode, 1)

    def test_a_huge_file_is_read_up_to_one_mib_and_the_hook_warns(self):
        filler = "# filler line of plain text\n" * 40000  # about 1.1 MiB
        self.write("data/early.txt", f"token = '{KEY}'\n" + filler)
        self.write("data/late.txt", filler + f"token = '{KEY}'\n")
        self.stage("data/early.txt", "data/late.txt")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)  # the key in the first MiB is found
        self.assertIn("COMMIT_SCAN_PARTIAL", proc.stderr)
        self.git("reset", "-q", "--", "data/early.txt")
        proc = self.hook()
        self.assertEqual(proc.returncode, 0)  # the key after the first MiB is not seen, and the hook says so
        self.assertIn("COMMIT_SCAN_PARTIAL", proc.stderr)
        self.assertIn("data/late.txt", proc.stderr)

    def test_a_project_allow_list_entry_is_honoured(self):
        self.write(PATTERNS_REL, json.dumps({"allow": [PHONE]}))
        self.write("docs/contact.md", f"Call {PHONE}\n")
        self.write(HIPAA_REL, HIPAA)
        self.stage("docs/contact.md")
        self.assertEqual(self.hook().returncode, 0)

    def test_a_bad_patterns_file_warns_and_the_built_in_rules_still_run(self):
        self.write(PATTERNS_REL, "{not json")
        self.write("src/app.py", f"token = '{KEY}'\n")
        self.stage("src/app.py")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_SCAN_CONFIG", proc.stderr)
        self.assertIn("COMMIT_SECRET", proc.stderr)


class PhiScanTests(HookTestCase):
    def test_phi_blocks_in_a_hipaa_project(self):
        self.write(HIPAA_REL, HIPAA)
        self.write("docs/note.md", f"Phone {PHONE}\n")
        self.stage("docs/note.md")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_PHI", proc.stderr)
        self.assertIn("HIPAA", proc.stderr)
        self.assertNotIn(PHONE, proc.stderr)
        self.assertEqual(self.events()[0]["verdict"], "block")

    def test_phi_warns_elsewhere_and_the_commit_goes_ahead(self):
        self.write("docs/note.md", f"Phone {PHONE}\n")
        self.stage("docs/note.md")
        proc = self.hook()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("COMMIT_PHI", proc.stderr)
        self.assertIn("warning", proc.stderr)
        self.assertNotIn(PHONE, proc.stderr)
        self.assertEqual(
            [(e["reason_code"], e["verdict"]) for e in self.events()],
            [("COMMIT_PHI", "warn"), ("COMMIT_ALLOWED", "allow")],
        )

    def test_phi_set_to_record_only_logs_an_event(self):
        self.write(HIPAA_REL, '[agent]\nphi = "record"\n')
        self.write("docs/note.md", f"Phone {PHONE}\n")
        self.stage("docs/note.md")
        proc = self.hook()
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertEqual(self.events()[0]["verdict"], "info")

    def test_secrets_and_phi_are_all_listed_in_one_message(self):
        self.write(HIPAA_REL, HIPAA)
        self.write("src/app.py", f"token = '{KEY}'\nphone = '{PHONE}'\n")
        self.stage("src/app.py")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_SECRET", proc.stderr)
        self.assertIn("COMMIT_PHI", proc.stderr)
        self.assertIn("2 reasons", proc.stderr)


class ApprovalTests(HookTestCase):
    def draft(self, rel="docs/brief.md", text=BRIEF):
        self.write(rel, text)
        self.commit_all("add " + rel)

    def approved_and_staged(self, rel="docs/brief.md", doc_type="brief", *extra):
        self.approve(rel, doc_type, "--no-commit", *extra)
        self.stage(rel, STATE_REL)

    def test_a_status_set_to_approved_by_hand_blocks(self):
        self.draft()
        self.write("docs/brief.md", BRIEF.replace("status: draft", "status: approved"))
        self.stage("docs/brief.md")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_APPROVAL_NO_ENTRY", proc.stderr)
        self.assertIn("docs/brief.md", proc.stderr)
        self.assertIn("Warden", proc.stderr)
        event = self.events()[0]
        self.assertEqual((event["target_path"], len(event["target_sha256"])), ("docs/brief.md", 64))

    def test_a_new_document_that_says_approved_blocks(self):
        self.write("docs/prd.md", "---\napproval_status: approved\n---\n# PRD\n")
        self.stage("docs/prd.md")
        self.assertIn("COMMIT_APPROVAL_NO_ENTRY", self.hook().stderr)

    def test_the_hook_changes_nothing(self):
        self.draft()
        text = BRIEF.replace("status: draft", "status: approved")
        self.write("docs/brief.md", text)
        self.stage("docs/brief.md")
        self.hook()
        self.assertEqual((self.project / "docs/brief.md").read_text(), text)
        self.assertEqual(self.git("diff", "--cached", "--name-only").stdout.split(), ["docs/brief.md"])

    def test_a_matching_warden_entry_allows(self):
        self.draft()
        self.approved_and_staged()
        proc = self.hook()
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))

    def test_warden_commit_goes_through_the_hook(self):
        self.draft()
        proc = self.approve("docs/brief.md")  # Warden commits the document and the state file itself
        self.assertTrue(json.loads(proc.stdout)["git_commit"]["ok"], proc.stdout)
        self.assertEqual(self.git("log", "-1", "--format=%s").stdout.strip(), "Approve brief v1")
        self.assertIn("COMMIT_ALLOWED", self.codes())

    def test_an_edit_after_approval_is_stale(self):
        self.draft()
        self.approve("docs/brief.md")
        self.write("docs/brief.md", (self.project / "docs/brief.md").read_text() + "\nA new paragraph.\n")
        self.stage("docs/brief.md")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_APPROVAL_STALE", proc.stderr)
        self.assertIn("edited after approval", proc.stderr)

    def test_a_wrong_hash_in_the_state_file_is_stale(self):
        self.draft()
        self.approved_and_staged()
        state = json.loads((self.project / STATE_REL).read_text())
        state["brief"]["approved_hash"] = "0" * 64
        self.write(STATE_REL, json.dumps(state))
        self.stage(STATE_REL)
        self.assertIn("COMMIT_APPROVAL_STALE", self.hook().stderr)

    def test_an_entry_for_another_file_does_not_count(self):
        self.draft()
        self.draft("docs/prd.md", BRIEF.replace("brief", "prd"))
        self.approve("docs/brief.md", "brief", "--no-commit")
        self.write("docs/prd.md", BRIEF.replace("status: draft", "status: approved"))
        self.stage("docs/prd.md", STATE_REL)
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_APPROVAL_NO_ENTRY", proc.stderr)

    def test_an_approval_that_was_not_staged_does_not_count(self):
        self.draft()
        self.approve("docs/brief.md", "brief", "--no-commit")
        self.stage("docs/brief.md")  # the state file stays out of the commit
        self.assertIn("COMMIT_APPROVAL_NO_ENTRY", self.hook().stderr)

    def test_a_reopened_approval_cannot_be_brought_back_by_hand(self):
        self.draft()
        self.approve("docs/brief.md")
        self.warden(
            "reopen_document.py", "--doc-path", "docs/brief.md", "--doc-type", "brief", "--state-file", STATE_REL
        )
        reopened = (self.project / "docs/brief.md").read_text()
        self.assertIn("status: draft", reopened)
        self.stage("docs/brief.md", STATE_REL)
        self.assertEqual(self.hook().returncode, 0)  # approved -> draft takes approval away: allowed
        self.commit_all("reopen")
        self.write("docs/brief.md", reopened.replace("status: draft", "status: approved"))
        self.stage("docs/brief.md")
        self.assertIn("COMMIT_APPROVAL_NO_ENTRY", self.hook().stderr)

    def test_an_approved_by_edit_needs_a_warden_entry_too(self):
        self.draft()
        self.write("docs/brief.md", BRIEF.replace("status: draft", "status: draft\napproved_by: Fake Name"))
        self.stage("docs/brief.md")
        self.assertIn("COMMIT_APPROVAL_NO_ENTRY", self.hook().stderr)

    def test_an_ordinary_edit_of_a_draft_is_allowed(self):
        self.draft()
        self.write("docs/brief.md", BRIEF + "\nMore text.\n")
        self.stage("docs/brief.md")
        self.assertQuiet(self.hook())

    def test_a_build_plan_may_change_its_progress_fields_after_approval(self):
        self.draft("_bmad-output/spec.md", PLAN)
        self.approve("_bmad-output/spec.md", "build:spec", "--status-field", "approval_status", "--hash-mode", "build")
        text = (self.project / "_bmad-output/spec.md").read_text()
        self.write("_bmad-output/spec.md", text.replace("- [ ] one", "- [x] one").replace("in-progress", "in-review"))
        self.stage("_bmad-output/spec.md")
        self.assertQuiet(self.hook())
        self.write("_bmad-output/spec.md", text.replace("Body.", "A different plan."))
        self.stage("_bmad-output/spec.md")
        self.assertIn("COMMIT_APPROVAL_STALE", self.hook().stderr)

    def test_crlf_in_the_staged_file_does_not_change_the_hash(self):
        self.draft()
        self.approve("docs/brief.md")
        text = (self.project / "docs/brief.md").read_text()
        self.write("docs/brief.md", text.replace("\n", "\r\n"))
        self.stage("docs/brief.md")
        self.assertQuiet(self.hook())

    def test_a_broken_state_file_blocks_only_when_an_approval_is_claimed(self):
        self.draft()
        self.write(STATE_REL, "{not json")
        self.write("docs/brief.md", BRIEF + "\nMore.\n")
        self.stage("docs/brief.md", STATE_REL)
        self.assertEqual(self.hook().returncode, 0)
        self.write("docs/brief.md", BRIEF.replace("status: draft", "status: approved"))
        self.stage("docs/brief.md")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_STATE_UNREADABLE", proc.stderr)

    def test_a_file_that_is_not_markdown_is_not_checked(self):
        self.write("config/app.yaml", "status: approved\n")
        self.stage("config/app.yaml")
        self.assertEqual(self.hook().returncode, 0)

    def test_gate_generic_warns(self):
        self.draft("docs/story.md", STORY)
        self.write("docs/story.md", STORY.replace("confidence: 90", "confidence: 90\ngate: generic"))
        self.stage("docs/story.md")
        proc = self.hook()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("COMMIT_GATE_GENERIC", proc.stderr)
        self.assertEqual(self.events()[0]["verdict"], "warn")

    def test_a_removed_question_warns_and_a_resolved_one_only_logs(self):
        self.draft("docs/story.md", STORY)
        self.write("docs/story.md", STORY.replace("- Q-1: Which roles can export?\n", ""))
        self.stage("docs/story.md")
        proc = self.hook()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("COMMIT_QUESTION_REMOVED", proc.stderr)
        self.write("docs/story.md", STORY + RESOLUTION)
        self.stage("docs/story.md")
        proc = self.hook()
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.assertIn("COMMIT_RESOLUTION_CHANGED", self.codes())

    def test_a_secret_and_a_bad_status_are_both_listed(self):
        self.write("docs/prd.md", f"---\nstatus: approved\n---\ntoken = '{KEY}'\n")
        self.stage("docs/prd.md")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_SECRET", proc.stderr)
        self.assertIn("COMMIT_APPROVAL_NO_ENTRY", proc.stderr)


class TechStackTests(HookTestCase):
    def listed(self, text=LIST):
        """Commit the list, approved by Warden (the architect), and a package.json that the list allows."""
        self.write(LIST_REL, text)
        self.approve(LIST_REL, "tech-stack")
        self.write("package.json", PACKAGE)
        self.commit_all("app")

    def test_no_list_means_one_info_event_and_no_message(self):
        self.write("package.json", package("react", "pg"))
        self.stage("package.json")
        proc = self.hook()
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        events = self.events()
        self.assertEqual([(e["reason_code"], e["verdict"]) for e in events][0], ("COMMIT_NO_TECH_STACK", "info"))

    def test_no_list_and_no_dependency_file_means_no_tech_stack_event(self):
        self.write("src/app.py", "print(1)\n")
        self.stage("src/app.py")
        self.hook()
        self.assertNotIn("COMMIT_NO_TECH_STACK", self.codes())

    def test_a_new_dependency_on_the_list_is_allowed(self):
        self.listed()
        self.write("package.json", package("react", "@types/node"))
        self.stage("package.json")
        proc = self.hook()
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))

    def test_a_new_dependency_not_on_the_list_blocks(self):
        self.listed()
        self.write("package.json", package("react", "pg"))
        self.stage("package.json")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_TECH_STACK_NOT_ALLOWED", proc.stderr)
        self.assertIn("pg", proc.stderr)
        self.assertIn("package.json", proc.stderr)
        event = [e for e in self.events() if e["reason_code"] == "COMMIT_TECH_STACK_NOT_ALLOWED"][0]
        self.assertNotIn("pg", json.dumps(event))  # the event holds a count only

    def test_python_files_are_read_too(self):
        self.listed()
        self.write("requirements.txt", "django==4.2\n")
        self.stage("requirements.txt")
        self.assertEqual(self.hook().returncode, 0)
        self.write("requirements.txt", "django==4.2\npsycopg2-binary>=2.9\n")
        self.stage("requirements.txt")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("psycopg2-binary", proc.stderr)
        self.write("pyproject.toml", '[project]\nname = "x"\ndependencies = [\n  "Django>=4",\n  "pymongo",\n]\n')
        self.stage("pyproject.toml")
        self.assertIn("pymongo", self.hook().stderr)

    def test_a_dependency_that_was_already_there_is_not_new(self):
        self.listed()
        self.write("package.json", package("react", "left-pad"))
        self.commit_all("an old unlisted dependency")
        self.write("package.json", package("react", "left-pad", "@types/node"))
        self.stage("package.json")
        self.assertEqual(self.hook().returncode, 0)

    def test_removing_dependencies_is_always_allowed(self):
        self.listed()
        self.write("package.json", package())
        self.stage("package.json")
        self.assertEqual(self.hook().returncode, 0)

    def test_a_list_that_was_never_approved_blocks_a_new_dependency(self):
        self.write(LIST_REL, LIST)
        self.write("package.json", PACKAGE)
        self.commit_all("list and app, no approval")
        self.write("package.json", package("react", "@types/node"))
        self.stage("package.json")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_TECH_STACK_UNAPPROVED", proc.stderr)
        self.assertIn("has not been approved", proc.stderr)

    def test_a_list_edited_after_approval_blocks(self):
        self.listed()
        self.write(LIST_REL, (self.project / LIST_REL).read_text().replace('"react"', '"react", "pg"'))
        self.commit_all("edit the list without Warden")
        self.write("package.json", package("react", "pg"))
        self.stage("package.json")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_TECH_STACK_UNAPPROVED", proc.stderr)
        self.assertIn("changed after it was approved", proc.stderr)

    def test_adding_a_dependency_and_a_draft_list_entry_in_one_commit_blocks(self):
        self.listed()
        self.write(
            LIST_REL,
            (self.project / LIST_REL).read_text().replace('"react"', '"react", "pg"').replace("approved", "draft"),
        )
        self.write("package.json", package("react", "pg"))
        self.stage(LIST_REL, "package.json")
        self.assertIn("COMMIT_TECH_STACK_UNAPPROVED", self.hook().stderr)

    def test_a_list_that_says_approved_without_warden_blocks(self):
        self.write(LIST_REL, LIST.replace("draft", "approved"))
        self.stage(LIST_REL)
        self.assertIn("COMMIT_APPROVAL_NO_ENTRY", self.hook().stderr)

    def test_an_architect_approval_of_the_list_commits_through_the_hook(self):
        self.write(LIST_REL, LIST)
        self.commit_all("draft list")
        proc = self.approve(LIST_REL, "tech-stack")
        self.assertTrue(json.loads(proc.stdout)["git_commit"]["ok"], proc.stdout)

    def test_deleting_an_approved_list_blocks_until_it_is_reopened(self):
        self.listed()
        self.git("rm", "-q", "--", LIST_REL)
        self.write("package.json", package("react", "pg"))
        self.stage("package.json")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_TECH_STACK_REMOVED", proc.stderr)
        self.git("checkout", "-q", "HEAD", "--", LIST_REL)
        self.git("checkout", "-q", "HEAD", "--", "package.json")
        self.warden("reopen_document.py", "--doc-path", LIST_REL, "--doc-type", "tech-stack", "--state-file", STATE_REL)
        self.stage(LIST_REL, STATE_REL)
        self.assertEqual(self.hook().returncode, 0)  # reopening is allowed
        self.commit_all("reopen the list")
        self.git("rm", "-q", "--", LIST_REL)
        self.assertQuiet(self.hook())  # a list that is not in force may be deleted (an info event, nothing else)

    def test_a_bad_list_blocks(self):
        for text in (
            '---\nstatus: draft\n---\n[allowed]\nrust = ["serde"]\n',
            "---\nstatus: draft\n---\n[allowed\n",
            "---\nstatus: draft\n---\n",
        ):
            with self.subTest(text=text):
                self.write(LIST_REL, text)
                self.write("package.json", package("react"))
                self.stage(LIST_REL, "package.json")
                proc = self.hook()
                self.assertEqual(proc.returncode, 1)
                self.assertIn("COMMIT_TECH_STACK_BAD_FILE", proc.stderr)

    def test_a_bad_dependency_file_blocks(self):
        self.listed()
        self.write("package.json", "{not json")
        self.stage("package.json")
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_TECH_STACK_BAD_FILE", proc.stderr)

    def test_other_dependency_files_and_ungoverned_ecosystems_warn(self):
        self.listed('---\nstatus: draft\n---\n[allowed]\nnpm = ["react"]\n')
        self.write("go.mod", "module example.invalid/app\n\nrequire example.invalid/x v1.0.0\n")
        self.write("requirements.txt", "flask\n")
        self.stage("go.mod", "requirements.txt")
        proc = self.hook()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("COMMIT_DEPENDENCY_UNCHECKED", proc.stderr)
        self.assertIn("go.mod", proc.stderr)
        self.assertIn("requirements.txt", proc.stderr)

    def test_a_list_with_nothing_unchecked_is_quiet_for_other_files(self):
        self.listed()
        self.write("src/app.py", "print(1)\n")
        self.stage("src/app.py")
        self.assertQuiet(self.hook())


class FailClosedTests(HookTestCase):
    def stage_plain(self):
        self.write("src/app.py", "print('hello')\n")
        self.stage("src/app.py")

    def test_a_crash_in_a_check_blocks_and_never_shows_the_error_text(self):
        self.stage_plain()
        self.write(".claude/skills/prompt-guard/scripts/prompt_check.py", f'raise RuntimeError("{MARKER}")\n')
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_ERROR", proc.stderr)
        self.assertIn("RuntimeError", proc.stderr)
        self.assertNotIn(MARKER, proc.stderr)
        self.assertEqual(self.codes(), ["COMMIT_ERROR"])
        self.assertNotIn(MARKER, json.dumps(self.events()))

    def test_a_missing_skill_blocks_and_says_what_to_install(self):
        self.stage_plain()
        (self.project / ".claude/skills/prompt-guard/scripts/prompt_check.py").unlink()
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("COMMIT_ERROR", proc.stderr)
        self.assertIn("prompt-guard skill is not installed", proc.stderr)
        self.assertIn("setup_guard.py", proc.stderr)

    def test_the_skills_are_also_found_in_the_agents_folder(self):
        self.stage_plain()
        (self.project / ".claude/skills").rename(self.project / ".claude/moved")
        (self.project / ".agents").mkdir()
        (self.project / ".claude/moved").rename(self.project / ".agents/skills")
        self.assertEqual(self.hook().returncode, 0)

    def test_a_missing_library_blocks(self):
        self.stage_plain()
        (self.project / HOOKS_REL / "guard_pre_commit.py").unlink()
        proc = self.hook()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("the checks cannot start", proc.stderr)

    def test_a_missing_ledger_warns_once_and_does_not_block(self):
        self.write("docs/note.md", f"Phone {PHONE}\n")  # a warning and the allow event: two events to record
        self.stage("docs/note.md")
        (self.project / ".claude/skills/agent-project-logger/scripts/guard_events.py").unlink()
        proc = self.hook()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stderr.count("event not recorded"), 1)

    def test_the_hook_does_not_send_events_even_when_a_collector_is_set(self):
        self.write("src/app.py", f"token = '{KEY}'\n")
        self.stage("src/app.py")
        for name in ("BMAD_GUARD_ENDPOINT", "BMAD_GUARD_COLLECTOR"):  # the new name and its deprecated alias
            with self.subTest(name=name):
                env = {
                    name: "https://collector.invalid",
                    "BMAD_GUARD_USER": "dev",
                    "BMAD_GUARD_KEY": "fake-key-for-the-test",
                }
                proc = self.hook(env)
                self.assertEqual(proc.returncode, 1)
                self.assertNotIn("could not send", proc.stderr)
                self.assertEqual(self.codes()[-1], "COMMIT_SECRET")  # queued, not sent

    def test_the_hook_leaves_no_pycache_in_the_project(self):
        self.stage_plain()
        self.hook()
        found = [p for p in self.project.rglob("__pycache__") if ".git" not in p.parts]
        self.assertEqual(found, [])


class LauncherTests(unittest.TestCase):
    def test_an_old_python_blocks_with_a_plain_message(self):
        err = io.StringIO()
        with (
            mock.patch.object(sys, "version_info", (3, 9, 6, "final", 0)),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as caught,
        ):
            runpy.run_path(str(HOOKS / "pre-commit"), run_name="__main__")
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("needs Python 3.10 or later", err.getvalue())
        self.assertIn("3.9", err.getvalue())


class EventTests(HookTestCase):
    def test_events_hold_codes_and_counts_never_content_or_scanned_paths(self):
        self.write(f"src/{MARKER}.py", f"# {MARKER}\ntoken = '{KEY}'\nphone = '{PHONE}'\n")
        self.stage(f"src/{MARKER}.py")
        self.hook()
        dump = json.dumps(self.events())
        for text in (MARKER, KEY, PHONE):
            self.assertNotIn(text, dump)
        event = self.events()[0]
        self.assertEqual(
            (
                event["guard"],
                event["stage"],
                event["verdict"],
                event["reason_code"],
                event["rule"],
                event["file_count"],
            ),
            ("git-hooks", "pre-commit", "block", "COMMIT_SECRET", "scan", "1"),
        )
        self.assertIn("secret.github-token", event["detail"])


class RealCommitTests(HookTestCase):
    def test_the_first_commit_of_a_new_repository_is_checked_too(self):
        fresh = self.project.parent / "fresh"
        shutil.copytree(self.project / ".claude", fresh / ".claude")
        shutil.copytree(self.project / HOOKS_REL, fresh / HOOKS_REL)
        run = lambda *a: subprocess.run(["git", *a], cwd=fresh, capture_output=True, text=True, env=self.env)  # noqa: E731
        run("init", "-q", "-b", "main")
        run("config", "core.hooksPath", HOOKS_REL)
        run("config", "user.name", "dev")
        run("config", "user.email", "dev@example.invalid")
        (fresh / "src").mkdir()
        (fresh / "src/app.py").write_text(f"token = '{KEY}'\n")
        run("add", "src/app.py")
        proc = run("commit", "-q", "-m", "first")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("COMMIT_SECRET", proc.stderr)
        (fresh / "src/app.py").write_text("print('hello')\n")
        run("add", "src/app.py")
        self.assertEqual(run("commit", "-q", "-m", "first").returncode, 0)

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.strip()

    def test_a_blocked_commit_is_not_made_and_shows_the_reason(self):
        before = self.head()
        self.write("src/app.py", f"token = '{KEY}'\n")
        self.stage("src/app.py")
        proc = self.commit("add the key")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("COMMIT_SECRET", proc.stderr)
        self.assertEqual(self.head(), before)

    def test_a_clean_commit_is_made(self):
        before = self.head()
        self.write("src/app.py", "print('hello')\n")
        self.stage("src/app.py")
        proc = self.commit("add the app")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotEqual(self.head(), before)

    def test_commit_a_reads_the_updated_index(self):
        self.write("src/app.py", "print('hello')\n")
        self.commit_all("app")
        self.write("src/app.py", f"token = '{KEY}'\n")
        proc = self.git("commit", "-q", "-a", "-m", "change", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("COMMIT_SECRET", proc.stderr)

    def test_commit_with_a_path_reads_that_paths_content(self):
        self.write("src/app.py", "print('hello')\n")
        self.write("src/other.py", "print('other')\n")
        self.commit_all("two files")
        self.write("src/app.py", "print('changed')\n")
        self.write("src/other.py", f"token = '{KEY}'\n")
        self.stage("src/other.py")  # staged, but this commit holds src/app.py only (git uses a temporary index)
        proc = self.git("commit", "-q", "-m", "only the app", "--", "src/app.py", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_installed_module_itself_can_be_committed(self):
        self.git("add", "--", ".claude/skills", HOOKS_REL)
        proc = self.hook()
        self.assertEqual(proc.returncode, 0, proc.stderr)


STORIES = "_bmad-output/planning-artifacts/stories"
FRD_REL = "_bmad-output/planning-artifacts/frds/F-001/frd.md"
SLICER_SCRIPT = ".claude/skills/experion-story-slicer/scripts/story_coverage.py"
ORDER = "- [F001-01](F-001/F001-01-slug.md) — FR-F001-01\n- [F001-02](F-001/F001-02-slug.md) — FR-F001-02\n"


def story(story_id: str, requirements: str, body: str = POINTER, deps: str = "[]") -> str:
    return (
        f"---\nstory: {story_id}\nfeature: F-001\nrequirements: {requirements}\ndepends_on: {deps}\n---\n"
        f"# {story_id} A fake story\n\n{body}\n"
    )


class StoryCoverageTests(HookTestCase):
    """A commit with staged story files gets a warning (never a block) when the stories do not match the FRD."""

    def setUp(self):
        super().setUp()
        self.write(FRD_REL, make_frd())  # FR-F001-01 and FR-F001-02, three criteria each
        self.commit_all("the approved FRD")

    def stage_stories(self, one: str = "[FR-F001-01]", two: str = "[FR-F001-02]", body: str = POINTER) -> None:
        self.write(f"{STORIES}/F-001/F001-01-slug.md", story("F001-01", one, body))
        self.write(f"{STORIES}/F-001/F001-02-slug.md", story("F001-02", two, deps="[F001-01]"))
        self.write(f"{STORIES}/stories.md", "# Build order\n\n" + ORDER)
        self.stage(STORIES)

    def stub(self, text: str) -> None:
        self.write(SLICER_SCRIPT, text)

    def warned(self, proc, *codes: str) -> None:
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("warning (the commit goes ahead)", proc.stderr)
        for code in codes:
            self.assertIn(code, proc.stderr)

    def test_stories_that_cover_the_frd_are_quiet(self):
        self.stage_stories()
        self.assertQuiet(self.hook())
        self.assertEqual(self.codes(), ["COMMIT_ALLOWED"])

    def test_an_uncovered_fr_warns_with_the_code_and_the_feature(self):
        self.stage_stories(two="[FR-F001-01]")  # FR-F001-02 is in no story, FR-F001-01 is in two
        proc = self.hook()
        self.warned(proc, "COMMIT_STORY_COVERAGE", "STORY_FR_UNCOVERED (F-001)", "STORY_FR_TWICE (F-001)")
        event = next(e for e in self.events() if e["reason_code"] == "COMMIT_STORY_COVERAGE")
        self.assertEqual((event["verdict"], event["rule"]), ("warn", "story-coverage"))
        self.assertEqual(event["detail"], "STORY_FR_TWICE,STORY_FR_UNCOVERED")

    def test_copied_frd_text_warns_with_the_story_id_and_never_repeats_the_text(self):
        copied = ac("FR-F001-01", 2)
        self.stage_stories(body=POINTER + "\n\n" + copied)
        proc = self.hook()
        self.warned(proc, "STORY_FRD_TEXT (F001-01)")
        self.assertNotIn(copied, proc.stderr + "".join(map(str, self.events())))

    def test_a_requirement_the_frd_does_not_have_warns(self):
        self.stage_stories(two="[FR-F001-09]")
        self.warned(self.hook(), "STORY_FR_UNKNOWN (F001-02)", "STORY_FR_UNCOVERED")

    def test_the_commit_itself_still_goes_through(self):
        self.stage_stories(two="[FR-F001-01]")
        proc = self.commit("some stories")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("COMMIT_STORY_COVERAGE", proc.stderr)
        self.assertEqual(self.git("log", "-1", "--format=%s").stdout.strip(), "some stories")

    def test_a_commit_without_story_files_does_not_run_the_coverage_check(self):
        marker = self.project / "coverage-ran.txt"
        self.stub(
            f"open({str(marker)!r}, 'w').close()\n\ndef evaluate(*args, **kwargs):\n    return {{'reasons': []}}\n"
        )
        self.write("src/app.py", "print('hello')\n")
        self.write("docs/notes.md", "# Notes\n")
        self.stage("src/app.py", "docs/notes.md")
        self.assertQuiet(self.hook())
        self.assertFalse(marker.exists())
        self.stage_stories()
        self.assertQuiet(self.hook())
        self.assertTrue(marker.exists(), "the stub shows the check runs when a story is staged")

    def test_a_project_without_the_slicer_gets_no_check_and_no_message(self):
        shutil.rmtree(self.project / ".claude/skills/experion-story-slicer")
        self.stage_stories(two="[FR-F001-01]")
        self.assertQuiet(self.hook())
        self.assertEqual(self.codes(), ["COMMIT_ALLOWED"])

    def test_it_reads_the_staged_stories_not_the_work_tree(self):
        self.stage_stories(two="[FR-F001-01]")
        self.write(f"{STORIES}/F-001/F001-02-slug.md", story("F001-02", "[FR-F001-02]", deps="[F001-01]"))
        self.warned(self.hook(), "STORY_FR_UNCOVERED")  # fixed in the work tree, but not staged
        self.stage_stories()
        self.assertQuiet(self.hook())

    def test_a_story_that_is_not_staged_is_not_counted(self):
        self.write(f"{STORIES}/F-001/F001-02-slug.md", story("F001-02", "[FR-F001-02]", deps="[F001-01]"))  # untracked
        self.write(f"{STORIES}/F-001/F001-01-slug.md", story("F001-01", "[FR-F001-01]"))
        self.write(f"{STORIES}/stories.md", "# Build order\n\n" + ORDER)
        self.stage(f"{STORIES}/F-001/F001-01-slug.md", f"{STORIES}/stories.md")
        self.warned(self.hook(), "STORY_FR_UNCOVERED", "STORY_ORDER")

    def test_the_order_file_alone_checks_the_whole_tree(self):
        self.stage_stories()
        self.commit_all("stories")
        self.write(
            f"{STORIES}/stories.md", "# Build order\n\n" + ORDER + "- [F001-09](F-001/F001-09-x.md) — FR-F001-09\n"
        )
        self.stage(f"{STORIES}/stories.md")
        self.warned(self.hook(), "STORY_ORDER (F001-09)")

    def test_a_technical_story_is_checked_too(self):
        self.stage_stories()
        self.commit_all("stories")
        text = "---\nstory: T-01\nfeature: none\ngate: technical\n---\n# T-01 Set up\n\n" + ac("FR-F001-01", 1) + "\n"
        self.write(f"{STORIES}/technical/T-01-set-up.md", text)
        self.stage(f"{STORIES}/technical/T-01-set-up.md")
        self.warned(self.hook(), "STORY_FRD_TEXT (T-01)", "STORY_ORDER")

    def test_the_stories_folder_comes_from_the_slicers_own_settings(self):
        toml = self.project / ".claude/skills/experion-story-slicer/customize.toml"
        text = toml.read_text(encoding="utf-8")
        self.assertIn('stories_dir = "{project-root}/' + STORIES + '"', text)
        toml.write_text(text.replace("/" + STORIES + '"', '/docs/stories"'), encoding="utf-8")
        self.write("docs/stories/F-001/F001-01-slug.md", story("F001-01", "[FR-F001-01]"))
        self.write("docs/stories/stories.md", "# Build order\n\n" + ORDER)
        self.stage("docs/stories")
        self.warned(self.hook(), "STORY_FR_UNCOVERED", "STORY_ORDER")

    def test_a_setting_outside_the_project_falls_back_to_the_default_folder(self):
        toml = self.project / ".claude/skills/experion-story-slicer/customize.toml"
        text = toml.read_text(encoding="utf-8")
        toml.write_text(text.replace("/" + STORIES + '"', '/../elsewhere"'), encoding="utf-8")
        self.stage_stories(two="[FR-F001-01]")
        self.warned(self.hook(), "STORY_FR_UNCOVERED")

    def test_a_check_that_cannot_run_warns_and_does_not_block(self):
        self.stub("raise ValueError('broken')\n")
        self.stage_stories()
        proc = self.hook()
        self.warned(proc, "COMMIT_STORY_COVERAGE", "could not run (ValueError)")
        self.stub("def evaluate(*args, **kwargs):\n    raise OSError('nope')\n")
        self.warned(self.hook(), "could not run (OSError)")


class TreeTests(HookTestCase):
    """Every event carries the staged tree, which the commit then holds (ADR 0003 update): the report's match key."""

    def tree_of(self, rev: str = "HEAD") -> str:
        return self.git("rev-parse", f"{rev}^{{tree}}").stdout.strip()

    def test_the_allow_event_holds_the_tree_of_the_commit_that_follows(self):
        self.write("src/app.py", "print('hello')\n")
        proc = self.git("add", "-A")
        self.assertEqual(proc.returncode, 0)
        proc = self.commit("add the app")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        events = [e for e in self.events() if e["reason_code"] == "COMMIT_ALLOWED"]
        self.assertEqual([e["tree"] for e in events][-1], self.tree_of())

    def test_a_blocked_attempt_has_the_tree_too_but_no_allow_event(self):
        self.write("src/app.py", f"token = '{KEY}'\n")
        self.stage("src/app.py")
        self.assertEqual(self.hook().returncode, 1)
        events = self.events()
        self.assertTrue(all(len(e["tree"]) in (40, 64) for e in events))
        self.assertEqual([e["verdict"] for e in events if e["stage"] == "pre-commit"], ["block"])

    def test_commit_a_and_commit_with_a_path_record_the_tree_of_what_is_committed(self):
        self.write("src/app.py", "print('hello')\n")
        self.write("src/other.py", "print('other')\n")
        self.commit_all("two files")
        self.write("src/app.py", "print('changed')\n")
        self.write("src/other.py", "print('staged only')\n")
        self.stage("src/other.py")
        proc = self.git("commit", "-q", "-m", "only the app", "--", "src/app.py", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.events()[-1]["tree"], self.tree_of())
        self.write("src/app.py", "print('again')\n")
        proc = self.git("commit", "-q", "-a", "-m", "all", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.events()[-1]["tree"], self.tree_of())

    def test_the_tree_is_a_hash_and_holds_no_content(self):
        self.write("src/app.py", "print('a very recognisable line')\n")
        self.stage("src/app.py")
        self.hook()
        text = json.dumps(self.events())
        self.assertNotIn("recognisable", text)


if __name__ == "__main__":
    unittest.main()
