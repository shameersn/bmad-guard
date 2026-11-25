# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for the document guard (Phase 2, T3): document_text.py and document_guard.py.

Fake documents only (fake names, fake dates). The guard reads the file on disk and compares; nothing is written.
"""

from __future__ import annotations

import importlib
import json
import os
import unittest
from unittest import mock

from tests.guard_harness import GuardTestCase, queue

dt = importlib.import_module("document_text")
dg = importlib.import_module("document_guard")

STORY = """\
---
title: Export the monthly report
approval_status: pending-approval
status: draft
confidence: 85
---

# Story 2.3: Export the monthly report

The export runs nightly [ASSUMPTION A-1: the data team accepts a 24-hour delay].
The file has one row per member [ASSUMPTION-RESOLVED A-2: inactive members are left out — Ravi PO, 2026-11-21].

## Open questions

- Q-1: Which roles can export?
- Q-2: Should the file name include the month?
  Resolved: Yes. — Priya BA, 2026-11-20
"""
APPROVED = STORY.replace("approval_status: pending-approval", "approval_status: approved").replace(
    "status: draft", "status: approved"
)


class TextGuardCase(GuardTestCase):
    """Helpers: put a document on disk, then run an Edit, Write or MultiEdit call through the dispatcher."""

    def put(self, rel: str, text: str):
        self.write(rel, text)
        return str(self.project / rel)

    def edit(self, rel: str, old: str, new: str, **extra) -> list[str]:
        return self.codes("Edit", {"file_path": str(self.project / rel), "old_string": old, "new_string": new, **extra})

    def save(self, rel: str, content: str) -> list[str]:
        return self.codes("Write", {"file_path": str(self.project / rel), "content": content})

    def multi(self, rel: str, *edits: tuple[str, str]) -> list[str]:
        items = [{"old_string": o, "new_string": n} for o, n in edits]
        return self.codes("MultiEdit", {"file_path": str(self.project / rel), "edits": items})


class ApprovalFieldTests(TextGuardCase):
    def test_each_approval_field_blocks_when_an_edit_changes_it(self):
        self.put("docs/story.md", STORY)
        for old, new in (
            ("approval_status: pending-approval", "approval_status: approved"),
            ("status: draft", "status: approved"),
            ("status: draft", "status: Approved"),
            ("status: draft", 'status: "approved"'),
            ("status: draft", "status: approved # signed off"),
            ("confidence: 85", "confidence: 85\napproved_by: Priya BA"),
            ("confidence: 85", "confidence: 85\napproved_at: 2026-11-25T10:00:00Z"),
        ):
            with self.subTest(new=new):
                self.assertEqual(self.edit("docs/story.md", old, new), ["DOC_APPROVAL_FIELD"])

    def test_removing_or_changing_an_existing_approval_blocks(self):
        signed = APPROVED.replace("confidence: 85", "confidence: 85\napproved_by: Priya BA\napproved_at: 2026-11-25")
        self.put("docs/story.md", signed)
        for old, new in (
            ("approval_status: approved\n", ""),  # remove the line
            ("approval_status: approved", "approval_status: pending-approval"),  # reopen
            ("status: approved", "status: draft"),
            ("approved_by: Priya BA", "approved_by: Someone Else"),
            ("approved_by: Priya BA\n", ""),
            ("approved_at: 2026-11-25", "approved_at: 2026-12-01"),
        ):
            with self.subTest(old=old, new=new):
                self.assertEqual(self.edit("docs/story.md", old, new), ["DOC_APPROVAL_FIELD"])

    def test_an_edit_elsewhere_in_an_approved_document_is_allowed(self):
        self.put("docs/story.md", APPROVED)
        self.assertEqual(self.edit("docs/story.md", "# Story 2.3", "# Story 2.3 (final)"), [])
        self.assertEqual(self.edit("docs/story.md", "confidence: 85", "confidence: 90"), [])
        self.assertEqual(self.edit("docs/story.md", "title: Export the monthly report", "title: Export report"), [])

    def test_other_status_values_are_ordinary_edits(self):
        self.put("docs/story.md", STORY)
        for old, new in (
            ("status: draft", "status: ready-for-dev"),
            ("status: draft", "status: in-progress"),
            ("approval_status: pending-approval", "approval_status: draft"),
            ("approval_status: pending-approval", "approval_status: pending-review"),
        ):
            with self.subTest(new=new):
                self.assertEqual(self.edit("docs/story.md", old, new), [])

    def test_a_status_line_in_the_body_is_not_an_approval_field(self):
        self.put("docs/notes.md", "# Notes\n\nExample: status: approved\n")
        self.assertEqual(self.edit("docs/notes.md", "Example: status: approved", "Example: status: draft"), [])
        self.assertEqual(self.save("docs/notes.md", "# Notes\n\nstatus: approved\napproval_status: approved\n"), [])

    def test_write_compares_with_the_file_on_disk(self):
        self.put("docs/story.md", APPROVED)
        self.assertEqual(self.save("docs/story.md", APPROVED), [])  # same content
        self.assertEqual(self.save("docs/story.md", APPROVED.replace("# Story 2.3", "# Story 2.3 final")), [])
        self.assertEqual(self.save("docs/story.md", STORY), ["DOC_APPROVAL_FIELD"])  # approval dropped
        self.put("docs/draft.md", STORY)
        self.assertEqual(self.save("docs/draft.md", APPROVED), ["DOC_APPROVAL_FIELD"])

    def test_a_new_file_that_already_says_approved_blocks(self):
        self.assertEqual(self.save("docs/new.md", APPROVED), ["DOC_APPROVAL_FIELD"])
        self.assertEqual(
            self.save("_bmad-output/planning-artifacts/prd.md", "---\nstatus: approved\n---\n# PRD\n"),
            ["DOC_APPROVAL_FIELD"],
        )
        self.assertEqual(
            self.save("docs/new.md", STORY.replace("[ASSUMPTION A-1", "[ASSUMPTION-RESOLVED A-1")),
            ["DOC_APPROVAL_FIELD"],
        )

    def test_a_new_plan_that_waits_for_approval_is_allowed(self):
        # bmad-build writes its plan with approval_status: pending-approval; Warden approves it later.
        plan = "---\nstory: 2-3\nstatus: draft\napproval_status: pending-approval\nconfidence: 70\n---\n# Plan\n"
        self.assertEqual(self.save("_bmad-output/implementation-artifacts/spec-2-3.md", plan), [])

    def test_the_rule_applies_to_markdown_anywhere_in_the_project(self):
        for rel in ("specs/prd.md", "notes/brief.markdown", "x/Y.MD", "_bmad-output/planning-artifacts/prd.md"):
            with self.subTest(rel=rel):
                self.assertEqual(self.save(rel, "---\napproval_status: approved\n---\n"), ["DOC_APPROVAL_FIELD"])

    def test_other_file_types_are_not_documents(self):
        for rel in ("docs/story.txt", "docs/story.json", "src/app.py", "docs/story.md.bak"):
            with self.subTest(rel=rel):
                self.assertEqual(self.save(rel, "---\napproval_status: approved\n---\n"), [])

    def test_edit_with_replace_all(self):
        self.put("docs/story.md", "---\nstatus: draft\n---\nstatus: draft\n")
        self.assertEqual(
            self.edit("docs/story.md", "status: draft", "status: approved", replace_all=True), ["DOC_APPROVAL_FIELD"]
        )
        self.assertEqual(self.edit("docs/story.md", "draft", "in-progress", replace_all=True), [])

    def test_multiedit_runs_the_edits_in_order(self):
        self.put("docs/story.md", STORY)
        self.assertEqual(
            self.multi("docs/story.md", ("# Story 2.3", "# Story"), ("confidence: 85", "confidence: 90")), []
        )
        self.assertEqual(
            self.multi("docs/story.md", ("# Story 2.3", "# Story"), ("status: draft", "status: approved")),
            ["DOC_APPROVAL_FIELD"],
        )
        # the second edit works on the text the first one made
        self.assertEqual(
            self.multi("docs/story.md", ("status: draft", "state: draft"), ("state: draft", "status: approved")),
            ["DOC_APPROVAL_FIELD"],
        )
        # an approval that is added and taken back inside one call leaves the file as it was
        self.assertEqual(
            self.multi("docs/story.md", ("status: draft", "status: approved"), ("status: approved", "status: draft")),
            [],
        )

    def test_a_crlf_file_is_compared_like_any_other(self):
        self.put("docs/story.md", STORY.replace("\n", "\r\n"))
        self.assertEqual(self.edit("docs/story.md", "status: draft\n", "status: approved\n"), ["DOC_APPROVAL_FIELD"])
        self.assertEqual(self.edit("docs/story.md", "# Story 2.3\n\nThe", "# Story 2.3 final\n\nThe"), [])

    def test_a_relative_path_is_read_from_the_working_directory(self):
        self.put("docs/story.md", STORY)
        codes = self.codes(
            "Edit", {"file_path": "docs/story.md", "old_string": "status: draft", "new_string": "status: approved"}
        )
        self.assertEqual(codes, ["DOC_APPROVAL_FIELD"])

    def test_an_edit_claude_code_would_fail_is_not_blocked(self):
        self.assertEqual(self.edit("docs/missing.md", "status: draft", "status: approved"), [])  # no such file
        self.put("docs/story.md", STORY)
        self.assertEqual(self.edit("docs/story.md", "no such text", "other text"), [])  # not in the file
        self.assertEqual(self.edit("docs/story.md", "", "anything"), [])  # empty old_string on a file with text

    def test_an_edit_with_an_empty_old_string_can_create_a_file(self):
        self.assertEqual(self.edit("docs/new.md", "", APPROVED), ["DOC_APPROVAL_FIELD"])
        self.assertEqual(self.edit("docs/new2.md", "", "---\nstatus: draft\n---\n# New\n"), [])

    def test_when_the_old_string_is_not_found_as_typed_the_lines_are_compared_on_their_own(self):
        self.put("docs/story.md", STORY)
        # two spaces: not in the file as typed, but an approval line is added all the same
        self.assertEqual(self.edit("docs/story.md", "status:  draft", "status: approved"), ["DOC_APPROVAL_FIELD"])
        self.assertEqual(self.edit("docs/story.md", "title:  Export", "title: Export all"), [])

    def test_a_file_that_cannot_be_read_counts_as_empty(self):
        self.put("docs/story.md", STORY)
        with mock.patch("pathlib.Path.read_text", side_effect=PermissionError):
            self.assertEqual(self.save("docs/story.md", APPROVED), ["DOC_APPROVAL_FIELD"])
            self.assertEqual(self.save("docs/story.md", "# plain\n"), [])


class ResolutionTests(TextGuardCase):
    def test_adding_changing_or_removing_a_resolution_blocks(self):
        self.put("docs/story.md", STORY)
        for old, new in (
            (
                "- Q-1: Which roles can export?",
                "- Q-1: Which roles can export?\n  Resolved: Managers. — Priya BA, 2026-11-26",
            ),
            ("Resolved: Yes.", "Resolved: No."),
            ("  Resolved: Yes. — Priya BA, 2026-11-20\n", ""),
            (
                "[ASSUMPTION A-1: the data team accepts a 24-hour delay]",
                "[ASSUMPTION-RESOLVED A-1: ok — Ravi PO, 2026-11-21]",
            ),
            ("inactive members are left out", "inactive members stay in"),
            ("[ASSUMPTION-RESOLVED A-2: inactive members are left out — Ravi PO, 2026-11-21]", "nothing here"),
        ):
            with self.subTest(old=old):
                self.assertIn("DOC_APPROVAL_FIELD", self.edit("docs/story.md", old, new))

    def test_editing_other_text_rewording_and_adding_are_allowed(self):
        self.put("docs/story.md", STORY)
        for old, new in (
            ("# Story 2.3: Export the monthly report", "# Story 2.3: Export the report"),
            ("- Q-1: Which roles can export?", "- Q-1: Which user roles are allowed to export?"),  # still open
            ("- Q-1: Which roles can export?", "- Q-1: Which roles can export?\n- Q-3: Is a header row needed?"),
            ("accepts a 24-hour delay]", "accepts a 24-hour delay and no more]"),  # still open
            ("The export runs nightly", "The export runs every night"),
            ("  Resolved: Yes.", "    Resolved:  Yes."),  # only the spaces change
        ):
            with self.subTest(new=new):
                self.assertEqual(self.edit("docs/story.md", old, new), [], new)

    def test_moving_a_resolution_inside_one_write_is_not_a_change(self):
        self.put("docs/story.md", STORY)
        moved = STORY.replace("The export runs nightly", "A new first line.\nThe export runs nightly")
        self.assertEqual(self.save("docs/story.md", moved), [])

    def test_question_markup_in_a_file_without_it_needs_no_parser(self):
        # a document with none of the markup words never loads the ready-for-dev parser
        self.put("docs/plain.md", "# Plain\n\nText.\n")
        with mock.patch.object(dt, "ready_for_dev", side_effect=AssertionError("loaded")):
            self.assertEqual(self.edit("docs/plain.md", "Text.", "More text."), [])


class QuestionRemovedTests(TextGuardCase):
    def test_deleting_an_open_question_blocks(self):
        self.put("docs/story.md", STORY)
        self.assertEqual(self.edit("docs/story.md", "- Q-1: Which roles can export?\n", ""), ["DOC_QUESTION_REMOVED"])

    def test_deleting_an_open_assumption_blocks(self):
        self.put("docs/story.md", STORY)
        old = "The export runs nightly [ASSUMPTION A-1: the data team accepts a 24-hour delay]."
        self.assertEqual(self.edit("docs/story.md", old, "The export runs nightly."), ["DOC_QUESTION_REMOVED"])

    def test_removing_the_whole_section_or_writing_none_blocks(self):
        self.put("docs/story.md", STORY)
        for new in (
            STORY.split("## Open questions")[0],
            STORY.replace("- Q-1: Which roles can export?\n", "None.\n"),
            STORY.replace("## Open questions", "## Notes"),  # the items are no longer questions
        ):
            with self.subTest(new=new[-40:]):
                self.assertIn("DOC_QUESTION_REMOVED", self.save("docs/story.md", new))

    def test_changing_the_id_is_a_delete_and_an_add(self):
        self.put("docs/story.md", STORY)
        self.assertEqual(self.edit("docs/story.md", "- Q-1:", "- Q-7:"), ["DOC_QUESTION_REMOVED"])

    def test_a_question_that_stays_open_may_move(self):
        self.put("docs/story.md", STORY)
        moved = STORY.replace("- Q-1: Which roles can export?\n", "") + "- Q-1: Which roles can export?\n"
        self.assertEqual(self.save("docs/story.md", moved), [])

    def test_the_delete_check_with_and_without_a_resolution(self):
        old = STORY
        gone = STORY.replace("- Q-1: Which roles can export?\n", "")
        answered = STORY.replace(
            "- Q-1: Which roles can export?",
            "- Q-1: Which roles can export?\n  Resolved: Managers. — Priya BA, 2026-11-26",
        )
        self.assertEqual(dt.compare(old, gone), ["DOC_QUESTION_REMOVED"])
        # the item is still there, now with a resolution record: nothing removed. Who may write that record is the
        # approval-field rule, which blocks an AI edit.
        self.assertEqual(dt.compare(old, answered), ["DOC_APPROVAL_FIELD"])
        self.assertFalse(dt.items_removed(old, answered))
        assumed = STORY.replace(
            "[ASSUMPTION A-1: the data team accepts a 24-hour delay]",
            "[ASSUMPTION-RESOLVED A-1: the data team accepts a 24-hour delay — Ravi PO, 2026-11-26]",
        )
        self.assertFalse(dt.items_removed(old, assumed))
        self.put("docs/story.md", STORY)
        self.assertEqual(self.save("docs/story.md", answered), ["DOC_APPROVAL_FIELD"])

    def test_items_without_an_id_are_followed_by_count(self):
        old = "## Open questions\n\n- Which roles can export?\n- Is a header needed?\n\nText [ASSUMPTION: no delay]\n"
        self.assertFalse(dt.items_removed(old, old.replace("Which roles", "Which user roles")))
        self.assertTrue(dt.items_removed(old, old.replace("- Is a header needed?\n", "")))
        self.assertTrue(dt.items_removed(old, old.replace("Text [ASSUMPTION: no delay]", "Text")))
        self.assertFalse(dt.items_removed(old, old.replace("- Is a header needed?", "- Q-1: Is a header needed?")))
        malformed = old.replace("[ASSUMPTION: no delay]", "[ASSUMPTION no delay]")
        self.assertFalse(dt.items_removed(old, malformed))  # a tag that is not well formed still counts as open

    def test_resolved_items_may_not_be_deleted_either(self):
        self.put("docs/story.md", STORY)
        gone = STORY.replace(
            "- Q-2: Should the file name include the month?\n  Resolved: Yes. — Priya BA, 2026-11-20\n", ""
        )
        self.assertEqual(self.save("docs/story.md", gone), ["DOC_APPROVAL_FIELD"])  # the Resolved: line is gone

    def test_the_ready_for_dev_parser_is_missing(self):
        self.put("docs/story.md", STORY)
        with mock.patch.object(dt, "ready_for_dev", side_effect=ImportError("not installed")):
            codes = self.codes(
                "Edit", {"file_path": str(self.project / "docs/story.md"), "old_string": "# Story", "new_string": "# S"}
            )
        self.assertEqual(codes, ["GUARD_ERROR"])


class TextUnitTests(unittest.TestCase):
    def test_frontmatter_reading(self):
        front, body = dt.split_frontmatter("---\na: 1\n---\nbody\n")
        self.assertEqual((front, body), (["a: 1"], "body\n"))
        self.assertEqual(dt.split_frontmatter("no frontmatter\n"), ([], "no frontmatter\n"))
        self.assertEqual(dt.split_frontmatter("﻿---\na: 1\n---\nx")[0], ["a: 1"])
        self.assertEqual(dt.split_frontmatter("---\r\na: 1\r\n---\r\nx")[0], ["a: 1"])
        self.assertEqual(dt.split_frontmatter("---\na: 1\n...\nx")[1], "x")
        self.assertEqual(dt.split_frontmatter("---\na: 1\nstatus: approved\n")[0][:2], ["a: 1", "status: approved"])

    def test_an_unclosed_block_cannot_hide_an_approval_line(self):
        self.assertEqual(dt.compare("---\nstatus: draft\n", "---\nstatus: approved\n"), ["DOC_APPROVAL_FIELD"])

    def test_values_are_read_without_quotes_comments_or_case(self):
        values = dt.field_values(["Status:  'Approved' # done", 'approval_status: "APPROVED"', "approved_by: Priya"])
        self.assertEqual(values, {"status": ["approved"], "approval_status": ["approved"], "approved_by": ["priya"]})

    def test_a_key_with_a_prefix_is_not_an_approval_key(self):
        self.assertEqual(
            dt.compare("---\nx: 1\n---\n", "---\nx: 1\nmy_status: approved\nstatus_note: approved\n---\n"), []
        )

    def test_indented_lines_are_not_top_level_fields(self):
        self.assertEqual(dt.compare("---\nx: 1\n---\n", "---\nx: 1\nmeta:\n  status: approved\n---\n"), [])

    def test_fragments_look_at_every_line(self):
        self.assertEqual(dt.compare("a", "status: approved", exact=False), ["DOC_APPROVAL_FIELD"])
        self.assertEqual(dt.compare("status: approved", "status: approved", exact=False), [])
        self.assertEqual(dt.compare("a", "b", exact=False), [])


class ProtectedPlaceTests(TextGuardCase):
    def test_the_approval_state_file_is_blocked_for_every_writing_tool(self):
        state = str(self.project / "_bmad/custom/approval-state.json")
        self.assertEqual(self.codes("Write", {"file_path": state, "content": "{}"}), ["DOC_APPROVAL_STATE"])
        self.assertEqual(
            self.codes("Edit", {"file_path": state, "old_string": "a", "new_string": "b"}), ["DOC_APPROVAL_STATE"]
        )
        self.assertEqual(
            self.codes("MultiEdit", {"file_path": state, "edits": [{"old_string": "a", "new_string": "b"}]}),
            ["DOC_APPROVAL_STATE"],
        )
        self.assertEqual(
            self.codes("NotebookEdit", {"notebook_path": state, "new_source": "x"}), ["DOC_APPROVAL_STATE"]
        )

    def test_the_state_file_is_found_by_name_in_any_folder_and_spelling(self):
        for rel in (
            "_bmad/custom/Approval-State.JSON",
            "elsewhere/approval-state.json",
            "approval-state.json",
            "docs/../_bmad/custom/approval-state.json",
        ):
            with self.subTest(rel=rel):
                self.assertEqual(
                    self.codes("Write", {"file_path": str(self.project / rel), "content": "{}"}), ["DOC_APPROVAL_STATE"]
                )
        self.assertEqual(
            self.codes("Write", {"file_path": "_bmad/custom/approval-state.json", "content": "{}"}),
            ["DOC_APPROVAL_STATE"],
        )
        for rel in ("_bmad/custom/project.json", "_bmad/custom/approval-state.md", "docs/approval-state-notes.json"):
            with self.subTest(rel=rel):
                self.assertEqual(self.codes("Write", {"file_path": str(self.project / rel), "content": "{}"}), [])

    def test_a_link_to_the_state_file_is_blocked(self):
        self.write("_bmad/custom/approval-state.json", "{}")
        os.symlink(self.project / "_bmad/custom/approval-state.json", self.project / "state.json")
        self.assertEqual(self.save("state.json", "{}"), ["DOC_APPROVAL_STATE"])

    def test_guard_records_are_blocked_inside_the_folder_only(self):
        for rel in ("_bmad-output/guard/reviews/2-3.json", "_bmad-output/guard/builds.json", "_bmad-output/guard/x.md"):
            with self.subTest(rel=rel):
                self.assertEqual(self.save(rel, "{}"), ["DOC_GUARD_RECORD"])
        for rel in (
            "_bmad-output/guard.md",
            "_bmad-output/guardian/x.json",
            "_bmad-output/planning-artifacts/prd.md",
            "docs/_bmad-output/guard/x.json",
            "_bmad-output/implementation-artifacts/x.json",
        ):
            with self.subTest(rel=rel):
                self.assertEqual(self.save(rel, "{}"), [])

    def test_guard_config_is_blocked_inside_the_folder_only(self):
        for rel in (
            "_bmad/custom/project/guard/tool-rules.json",
            "_bmad/custom/project/guard/tech-stack.toml",
            "_bmad/custom/project/guard/new-file.txt",
            "_bmad/custom/project/guard/sub/x",
        ):
            with self.subTest(rel=rel):
                self.assertEqual(self.save(rel, "{}"), ["DOC_GUARD_CONFIG"])
        for rel in (
            "_bmad/custom/project/guard-notes.md",
            "_bmad/custom/project/bmad-build/context.md",
            "_bmad/custom/tool-guard.toml",
            "_bmad/custom/project.json",
        ):
            with self.subTest(rel=rel):
                self.assertEqual(self.save(rel, "text"), [])

    def test_all_four_tools_are_checked_for_a_zone(self):
        for tool, key in (("Write", "file_path"), ("Edit", "file_path"), ("MultiEdit", "file_path")):
            with self.subTest(tool=tool):
                extra = {"content": "x"} if tool == "Write" else {"old_string": "a", "new_string": "b"}
                if tool == "MultiEdit":
                    extra = {"edits": [{"old_string": "a", "new_string": "b"}]}
                codes = self.codes(
                    tool, {key: str(self.project / "_bmad/custom/project/guard/tool-rules.json"), **extra}
                )
                self.assertEqual(codes, ["DOC_GUARD_CONFIG"])
        nb = {"notebook_path": str(self.project / "_bmad-output/guard/a.ipynb"), "new_source": "x"}
        self.assertEqual(self.codes("NotebookEdit", nb), ["DOC_GUARD_RECORD"])
        nb = {"notebook_path": str(self.project / "analysis/a.ipynb"), "new_source": "status: approved"}
        self.assertEqual(self.codes("NotebookEdit", nb), [])

    def test_case_dots_and_links_do_not_get_past_a_folder(self):
        os.makedirs(self.project / "_bmad-output/guard")
        os.symlink(self.project / "_bmad-output/guard", self.project / "notes")
        for rel in ("_BMAD-Output/GUARD/x.json", "docs/../_bmad-output/guard/x.json", "notes/x.json"):
            with self.subTest(rel=rel):
                self.assertEqual(self.save(rel, "{}"), ["DOC_GUARD_RECORD"])

    def test_the_home_folder_form_and_the_working_directory_are_followed(self):
        self.write("_bmad/custom/project/guard/x", "")
        sub = self.project / "docs"
        sub.mkdir()
        codes = self.codes("Write", {"file_path": "../_bmad/custom/project/guard/x", "content": "{}"}, cwd=str(sub))
        self.assertEqual(codes, ["DOC_GUARD_CONFIG"])

    def test_a_markdown_file_in_a_protected_folder_gets_the_folder_reason_only(self):
        self.assertEqual(self.save("_bmad-output/guard/x.md", APPROVED), ["DOC_GUARD_RECORD"])


class FailClosedTests(TextGuardCase):
    def test_malformed_input_for_each_tool_blocks(self):
        good = str(self.project / "docs/a.md")
        bad_calls = [
            ("Write", {"content": "x"}),
            ("Write", {"file_path": good}),
            ("Write", {"file_path": good, "content": 5}),
            ("Write", {"file_path": 5, "content": "x"}),
            ("Write", {"file_path": "  ", "content": "x"}),
            ("Edit", {"file_path": good, "old_string": "a"}),
            ("Edit", {"file_path": good, "old_string": 1, "new_string": "b"}),
            ("MultiEdit", {"file_path": good}),
            ("MultiEdit", {"file_path": good, "edits": []}),
            ("MultiEdit", {"file_path": good, "edits": "x"}),
            ("MultiEdit", {"file_path": good, "edits": [{"old_string": "a"}]}),
            ("MultiEdit", {"file_path": good, "edits": ["x"]}),
            ("NotebookEdit", {"new_source": "x"}),
            ("NotebookEdit", {"notebook_path": None}),
        ]
        for tool, tool_input in bad_calls:
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertEqual(self.codes(tool, tool_input), ["BAD_HOOK_INPUT"])

    def test_malformed_text_is_not_checked_for_a_file_that_is_not_markdown(self):
        self.assertEqual(self.codes("Write", {"file_path": str(self.project / "a.py"), "content": 5}), [])

    def test_a_guard_that_crashes_blocks(self):
        with mock.patch.object(dg.dt, "compare", side_effect=RuntimeError("boom")):
            self.assertEqual(self.save("docs/a.md", "text"), ["GUARD_ERROR"])
        with mock.patch.object(dg, "Zones", side_effect=OSError("boom")):
            code, out, err = self.entry(json.dumps(self.payload("Write", {"file_path": "x.md", "content": "x"})))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("GUARD_ERROR", err)
        self.assertNotIn("boom", err)

    def test_the_hook_blocks_with_exit_2_and_a_message_that_names_the_human_path(self):
        self.put("docs/story.md", STORY)
        raw = json.dumps(
            self.payload(
                "Edit",
                {
                    "file_path": str(self.project / "docs/story.md"),
                    "old_string": "status: draft",
                    "new_string": "status: approved",
                },
            )
        )
        code, out, err = self.entry(raw)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("DOC_APPROVAL_FIELD (document-guard)", err)
        self.assertIn("Warden", err)

    def test_every_rule_message_says_what_to_do_instead(self):
        for code, text in dg.REASONS.items():
            with self.subTest(code=code):
                self.assertTrue(text.endswith("."), code)
                self.assertTrue(
                    any(
                        w in text for w in ("Ask the developer", "Tell the developer", "Keep it open", "Run the review")
                    )
                )
                self.assertLess(len(text), 260)

    def test_the_guard_is_registered_for_the_writing_tools_only(self):
        guards = {g.name: g for g in dg.GUARDS}
        self.assertEqual(guards["document-guard"].tools, ("Write", "Edit", "MultiEdit", "NotebookEdit"))
        self.assertEqual(
            self.call("Read", {"file_path": str(self.project / "_bmad/custom/approval-state.json")})[1], []
        )


class EventTests(TextGuardCase):
    def test_events_and_messages_never_carry_the_path_or_the_text(self):
        folder, marker = "SECRET-FOLDER-NAME-987", "SECRET-TEXT-MARKER-321"
        self.put(f"{folder}/story.md", STORY.replace("Export the monthly report", marker))
        path = str(self.project / folder / "story.md")
        raw = json.dumps(
            self.payload(
                "Edit",
                {"file_path": path, "old_string": "status: draft", "new_string": f"status: approved\nx: {marker}"},
            )
        )
        code, out, err = self.entry(raw)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("DOC_APPROVAL_FIELD", err)
        for text in (err, json.dumps(queue(self.project))):
            self.assertNotIn(folder, text)
            self.assertNotIn(marker, text)
        events = queue(self.project)
        self.assertEqual([e["reason_code"] for e in events], ["DOC_APPROVAL_FIELD"])
        event = events[0]
        self.assertEqual(
            (event["guard"], event["verdict"], event["tool"], event["rule"], event["session_id"]),
            ("tool-guard", "block", "Edit", "document-guard", "s-test"),
        )
        self.assertEqual(event.get("path", ""), "")

    def test_a_write_to_a_protected_folder_records_the_rule_never_the_content(self):
        marker = "SECRET-RECORD-MARKER-654"
        raw = json.dumps(
            self.payload("Write", {"file_path": str(self.project / "_bmad-output/guard/x.json"), "content": marker})
        )
        code, _, err = self.entry(raw)
        self.assertEqual(code, 2)
        events = queue(self.project)
        self.assertEqual([e["reason_code"] for e in events], ["DOC_GUARD_RECORD"])
        self.assertNotIn(marker, err + json.dumps(events))
        self.assertNotIn("x.json", err + json.dumps(events))

    def test_an_allowed_edit_records_nothing(self):
        self.put("docs/story.md", STORY)
        raw = json.dumps(
            self.payload(
                "Edit", {"file_path": str(self.project / "docs/story.md"), "old_string": "# Story", "new_string": "# S"}
            )
        )
        self.assertEqual(self.entry(raw), (0, "", ""))
        self.assertEqual(queue(self.project), [])


if __name__ == "__main__":
    unittest.main()
