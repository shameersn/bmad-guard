# /// script
# requires-python = ">=3.10"
# ///
"""Tests for the ready-for-dev gate (module/skills/ready-for-dev-gate, ADR 0004).

The script runs as a real command-line program against throwaway git projects. All data is fake.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "module/skills/ready-for-dev-gate"
SCRIPT = SKILL / "scripts/ready_for_dev.py"
ENV = {k: v for k, v in os.environ.items() if not k.startswith("BMAD_GUARD_")}

FIXTURE = (
    ROOT / "tests/fixtures/frd-clinic-f001.md"
)  # a fake clinic FRD with FR-F001-01 (13 criteria) and FR-F001-02 (7)
STORY_ONLY_WORD = "zebra-pineapple"  # appears only in story text; must never reach an event

STORY = f"""---
title: Export the monthly report
confidence: 84
epic_summary: epics/epic-2/epic-summary.md
---

# Story 2.3: Export the monthly report {STORY_ONLY_WORD}

1. One row per member [ASSUMPTION-RESOLVED A-1: inactive members are left out — Test Owner, 2026-11-21].
2. Runs nightly [ASSUMPTION-RESOLVED A-2: a 24-hour delay is fine -- Test Analyst, 2026-11-22].

## Open questions

- Q-1: Which roles can export?
  Resolved: Managers and admins. — Test Analyst, 2026-11-20
- Q-2: Should the file name include the month?
  - Resolved: Yes. -- Test Owner, 2026-11-21

## Notes

- Not a question, outside the section.
"""

EPIC_SUMMARY = "# Epic 2\n\n## Functional\nReports for managers.\n\n## Technical\nA nightly export job.\n"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def make_project(root: Path, story: str = STORY) -> Path:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test.user@example.invalid")
    (root / "_bmad/custom").mkdir(parents=True)
    (root / "_bmad/custom/project.json").write_text('{"project_name": "demo-api"}')
    (root / "epics/epic-2").mkdir(parents=True)
    (root / "epics/epic-2/epic-summary.md").write_text(EPIC_SUMMARY)
    (root / "stories").mkdir()
    (root / "stories/2-3.md").write_text(story)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def run(project: Path, *args: str, script: Path = SCRIPT) -> tuple[int, dict | None, str]:
    proc = subprocess.run([sys.executable, str(script), *args], cwd=project, env=ENV, capture_output=True, text=True)
    out = proc.stdout.strip()
    return proc.returncode, (json.loads(out) if out else None), proc.stderr


def events(project: Path) -> list[dict]:
    q = project / ".bmad-guard/events/queue.jsonl"
    return [json.loads(line) for line in q.read_text().splitlines()] if q.exists() else []


def codes(result: dict) -> list[str]:
    return [r["code"] for r in result["reasons"]]


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        (self.tmp / "p").mkdir()
        self.project = make_project(self.tmp / "p")
        self.story = self.project / "stories/2-3.md"

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, text: str) -> None:
        self.story.write_text(text)

    def edit(self, old: str, new: str) -> None:
        text = self.story.read_text()
        self.assertIn(old, text)
        self.write(text.replace(old, new))

    def gate(self, *extra: str) -> dict:
        code, result, err = run(self.project, str(self.story), *extra)
        self.assertEqual(code, 0, err)
        return result

    def blocked_with(self, code: str, *extra: str) -> dict:
        result = self.gate(*extra)
        self.assertEqual(result["result"], "block")
        self.assertIn(code, codes(result))
        return result


class PassTests(Base):
    def test_ready_story_is_allowed(self):
        result = self.gate()
        self.assertEqual(result["result"], "allow", result)
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["counts"]["resolved_questions"], 2)
        self.assertEqual(result["counts"]["resolved_assumptions"], 2)
        self.assertEqual(result["counts"]["open_questions"], 0)
        self.assertEqual(result["counts"]["confidence"], 84)

    def test_none_item_is_not_a_question(self):
        self.edit("- Q-1: Which", "- None.\n- Q-1: Which")
        self.assertEqual(self.gate()["result"], "allow")

    def test_confidence_boundary(self):
        self.edit("confidence: 84", "confidence: 80")
        self.assertEqual(self.gate()["result"], "allow")
        self.edit("confidence: 80", "confidence: 79")
        result = self.blocked_with("LOW_CONFIDENCE")
        self.assertEqual(codes(result), ["LOW_CONFIDENCE"])

    def test_min_confidence_option(self):
        self.assertEqual(self.gate("--min-confidence", "85")["result"], "block")
        self.assertEqual(self.gate("--min-confidence", "84")["result"], "allow")

    def test_generic_story_skips_the_gate_but_records_an_event(self):
        self.write("---\ngate: generic\n---\n# Set up CI\n\n## Open questions\n\n- Q-1: Which runner?\n")
        result = self.gate()
        self.assertEqual(result["result"], "allow")
        self.assertEqual(result["gate"], "generic")
        [event] = events(self.project)
        self.assertEqual((event["verdict"], event["reason_code"], event["gate"]), ("allow", "READY_ALLOW", "generic"))


class BlockReasonTests(Base):
    def test_open_question(self):
        self.edit("- Q-2: Should", "- Q-3: Anything else?\n- Q-2: Should")
        result = self.blocked_with("OPEN_QUESTIONS")
        self.assertIn("Q-3", result["reasons"][0]["detail"])
        self.assertEqual(result["counts"]["open_questions"], 1)

    def test_list_item_without_id_is_an_open_question(self):
        self.edit("- Q-2: Should", "- What about exports on weekends?\n- Q-2: Should")
        self.assertIn("without an id", self.blocked_with("OPEN_QUESTIONS")["reasons"][0]["detail"])

    def test_open_assumption(self):
        self.edit("## Open questions", "Uses CSV [ASSUMPTION A-3: nobody needs Excel].\n\n## Open questions")
        result = self.blocked_with("OPEN_ASSUMPTIONS")
        self.assertIn("A-3", result["reasons"][0]["detail"])

    def test_assumption_without_id_is_open(self):
        self.edit("## Open questions", "Uses CSV [ASSUMPTION: nobody needs Excel].\n\n## Open questions")
        self.assertEqual(self.blocked_with("OPEN_ASSUMPTIONS")["counts"]["open_assumptions"], 1)

    def test_resolution_without_who_or_when(self):
        for bad in (
            "Resolved: Managers and admins.",
            "Resolved: Managers and admins. — Test Analyst",
            "Resolved: Managers and admins. — , 2026-11-20",
            "Resolved: Managers. — Test Analyst, 2026-13-45",
        ):
            with self.subTest(line=bad):
                self.write(STORY.replace("Resolved: Managers and admins. — Test Analyst, 2026-11-20", bad))
                self.assertIn("Q-1", self.blocked_with("RESOLUTION_MISSING_WHO_OR_WHEN")["reasons"][0]["detail"])

    def test_assumption_resolution_without_who_or_when(self):
        self.edit(" -- Test Analyst, 2026-11-22", "")
        self.assertIn("A-2", self.blocked_with("RESOLUTION_MISSING_WHO_OR_WHEN")["reasons"][0]["detail"])

    def test_missing_confidence(self):
        self.edit("confidence: 84\n", "")
        self.blocked_with("MISSING_CONFIDENCE")

    def test_confidence_not_a_number(self):
        for value in ("high", "101", "80.5", "-1"):
            with self.subTest(value=value):
                self.write(STORY.replace("confidence: 84", f"confidence: {value}"))
                self.blocked_with("MISSING_CONFIDENCE")

    def test_no_frontmatter(self):
        self.write("# A story\n")
        result = self.blocked_with("MISSING_CONFIDENCE")
        self.assertIn("MISSING_EPIC_SUMMARY", codes(result))

    def test_duplicate_ids(self):
        self.edit("- Q-2: Should", "- Q-1: Again?\n  Resolved: Yes. — Test Owner, 2026-11-21\n- Q-2: Should")
        self.blocked_with("DUPLICATE_IDS")


class MalformedTests(Base):
    def test_unclosed_frontmatter_fails_closed(self):
        self.write("---\nconfidence: 90\ngate: generic\n# Story\n")
        result = self.blocked_with("MALFORMED_FRONTMATTER")
        self.assertNotIn("gate", result)

    def test_bad_frontmatter_line_fails_closed(self):
        self.edit("title: Export", "this is not yaml\ntitle: Export")
        self.blocked_with("MALFORMED_FRONTMATTER")

    def test_duplicate_frontmatter_key_fails_closed(self):
        self.edit("confidence: 84", "confidence: 40\nconfidence: 90")
        self.blocked_with("MALFORMED_FRONTMATTER")

    def test_nested_frontmatter_values_are_fine(self):
        self.edit("title: Export", "tags:\n  - reports\n  - csv\ntitle: Export")
        self.assertEqual(self.gate()["result"], "allow")

    def test_unreadable_story_exits_2_and_blocks(self):
        for path in (self.project / "stories/missing.md", self.project / "stories"):
            with self.subTest(path=path.name):
                code, result, _ = run(self.project, str(path))
                self.assertEqual(code, 2)
                self.assertEqual((result["result"], codes(result)), ("block", ["UNREADABLE_STORY"]))

    def test_non_utf8_story_exits_2(self):
        self.story.write_bytes(b"---\nconfidence: 90\n---\n\xff\xfe")
        code, result, _ = run(self.project, str(self.story))
        self.assertEqual((code, codes(result)), (2, ["UNREADABLE_STORY"]))

    def test_bad_min_confidence_is_a_usage_error(self):
        code, result, err = run(self.project, str(self.story), "--min-confidence", "120")
        self.assertEqual((code, result), (2, None))
        self.assertIn("0 to 100", err)


class DeletedItemTests(Base):
    def test_id_gap_counts_as_deleted(self):
        self.edit("- Q-1: Which roles can export?\n  Resolved: Managers and admins. — Test Analyst, 2026-11-20\n", "")
        self.write(self.story.read_text().replace("Q-2", "Q-3"))
        result = self.blocked_with("DELETED_ITEMS")
        self.assertEqual(codes(result), ["DELETED_ITEMS"])
        self.assertIn("Q-1, Q-2", result["reasons"][0]["detail"])

    def test_assumption_id_gap(self):
        self.edit("[ASSUMPTION-RESOLVED A-1: inactive members are left out — Test Owner, 2026-11-21]", "")
        self.assertIn("A-1", self.blocked_with("DELETED_ITEMS")["reasons"][0]["detail"])

    def test_baseline_catches_deleting_the_last_question(self):
        self.edit("- Q-2: Should the file name include the month?\n  - Resolved: Yes. -- Test Owner, 2026-11-21\n", "")
        self.assertEqual(self.gate()["result"], "allow")  # no gap: only the baseline can see it
        result = self.blocked_with("DELETED_ITEMS", "--baseline", "HEAD")
        self.assertIn("Q-2", result["reasons"][0]["detail"])
        self.assertEqual(result["counts"]["deleted_items"], 1)

    def test_baseline_catches_a_deletion_already_committed(self):
        first = git(self.project, "rev-parse", "HEAD")
        self.write(STORY.split("## Open questions")[0])
        git(self.project, "commit", "-qam", "drop questions")
        self.assertEqual(self.gate("--baseline", "HEAD")["result"], "allow")
        result = self.blocked_with("DELETED_ITEMS", "--baseline", first)
        self.assertIn("Q-1, Q-2", result["reasons"][0]["detail"])

    def test_baseline_without_the_file_is_fine(self):
        new = self.project / "stories/2-4.md"
        new.write_text(STORY)
        code, result, err = run(self.project, str(new), "--baseline", "HEAD")
        self.assertEqual((code, result["result"]), (0, "allow"), err)

    def test_unknown_baseline_ref_exits_2(self):
        for ref in ("no-such-branch", "--output=x"):
            with self.subTest(ref=ref):
                code, result, _ = run(self.project, str(self.story), f"--baseline={ref}")
                self.assertEqual((code, codes(result)), (2, ["BASELINE_UNAVAILABLE"]))

    def test_baseline_outside_git_exits_2(self):
        lone = self.tmp / "nogit"
        lone.mkdir()
        (lone / "story.md").write_text(STORY)
        code, result, _ = run(lone, str(lone / "story.md"), "--baseline", "HEAD", "--no-event")
        self.assertEqual((code, codes(result)), (2, ["BASELINE_UNAVAILABLE"]))


class EpicSummaryTests(Base):
    def test_missing_link(self):
        self.edit("epic_summary: epics/epic-2/epic-summary.md\n", "")
        self.blocked_with("MISSING_EPIC_SUMMARY")

    def test_missing_file(self):
        (self.project / "epics/epic-2/epic-summary.md").unlink()
        self.blocked_with("MISSING_EPIC_SUMMARY")

    def test_explicit_path_wins(self):
        other = self.project / "other-summary.md"
        other.write_text("## Functional\nOnly this.\n")
        result = self.blocked_with("EPIC_SUMMARY_INCOMPLETE", "--epic-summary", str(other))
        self.assertIn("Technical", result["reasons"][0]["detail"])

    def test_epic_number_with_epics_dir(self):
        self.edit("epic_summary: epics/epic-2/epic-summary.md", "epic: 2")
        self.assertEqual(self.gate("--epics-dir", str(self.project / "epics"))["result"], "allow")
        self.blocked_with("MISSING_EPIC_SUMMARY")  # no --epics-dir: nowhere to look

    def test_incomplete_summaries(self):
        summary = self.project / "epics/epic-2/epic-summary.md"
        for text in ("## Functional\nUsers.\n", "## Technical\nJobs.\n", "## Functional\n\n## Technical\nJobs.\n", ""):
            with self.subTest(text=text):
                summary.write_text(text)
                self.blocked_with("EPIC_SUMMARY_INCOMPLETE")

    def test_part_headings_may_have_more_words(self):
        (self.project / "epics/epic-2/epic-summary.md").write_text(
            "## Functional summary\nUsers.\n### Technical: design\nJobs.\n"
        )
        self.assertEqual(self.gate()["result"], "allow")


FRD = """---
title: FRD - F-001 - Monthly report
feature: F-001
version: 1
approval_status: approved
---

# FRD - F-001 - Monthly report

## 4. Requirement Details

Export runs nightly [ASSUMPTION-RESOLVED A-1: a 24-hour delay is fine — Test Owner, 2026-11-22].

## 12. Open Questions

- Q-1: Which roles can export? (raised by Test Analyst)
  Resolved: Managers and admins. — Test Analyst, 2026-11-20

## 13. Appendix

- Not a question, outside the section.
"""


class FrdTests(Base):
    """A story with `feature: F-<nnn>` needs that feature's FRD: approved and settled; it is the functional part."""

    def setUp(self):
        super().setUp()
        self.frds = self.project / "frds"
        self.frd = self.frds / "F-001/frd.md"
        self.frd.parent.mkdir(parents=True)
        self.summary = self.project / "epics/epic-2/epic-summary.md"
        self.frd.write_text(FRD)
        self.edit("confidence: 84\n", "confidence: 84\nfeature: F-001\n")

    def gate(self, *extra: str) -> dict:
        return super().gate("--frds-dir", str(self.frds), *extra)

    def edit_frd(self, old: str, new: str) -> None:
        text = self.frd.read_text()
        self.assertIn(old, text)
        self.frd.write_text(text.replace(old, new))

    def test_approved_settled_frd_passes(self):
        result = self.gate()
        self.assertEqual(result["result"], "allow", result)
        self.assertEqual(result["counts"]["frd_open_items"], 0)

    def test_story_without_a_feature_keeps_the_old_rule(self):
        self.edit("feature: F-001\n", "")
        self.edit_frd("approval_status: approved", "approval_status: draft")
        result = self.gate()
        self.assertEqual(result["result"], "allow", result)
        self.assertIsNone(result["counts"]["frd_open_items"])

    def test_missing_frd_blocks(self):
        self.frd.unlink()
        result = self.blocked_with("FRD_UNAPPROVED")
        self.assertIn("no frd.md", result["reasons"][0]["detail"])

    def test_feature_not_in_the_id_format_blocks(self):
        for bad in ("../F-001", "F-1a", "f-001", "F-001/../../x"):
            with self.subTest(feature=bad):
                self.write(self.story.read_text().replace("feature: F-001", f"feature: {bad}"))
                self.assertIn("F-<nnn>", self.blocked_with("FRD_UNAPPROVED")["reasons"][0]["detail"])
                self.write(self.story.read_text().replace(f"feature: {bad}", "feature: F-001"))

    def test_no_frds_dir_blocks_a_story_with_a_feature(self):
        result = Base.gate(self)
        self.assertEqual(codes(result), ["FRD_UNAPPROVED"])
        self.assertIn("no FRD folder", result["reasons"][0]["detail"])

    def test_quoted_feature_is_read(self):
        self.edit("feature: F-001", 'feature: "F-001"')
        self.assertEqual(self.gate()["result"], "allow")

    def test_frd_replaces_the_functional_part_only(self):
        self.summary.write_text("## Technical\nA nightly export job.\n")
        self.assertEqual(self.gate()["result"], "allow")
        self.summary.write_text("## Functional\nReports.\n")
        result = self.blocked_with("EPIC_SUMMARY_INCOMPLETE")
        self.assertIn("Technical", result["reasons"][0]["detail"])

    def test_frd_without_a_summary_still_needs_the_summary(self):
        self.summary.unlink()
        self.blocked_with("MISSING_EPIC_SUMMARY")

    def test_unapproved_frd_blocks(self):
        for status in ("draft", "review", ""):
            with self.subTest(status=status):
                self.frd.write_text(FRD.replace("approval_status: approved", f"approval_status: {status}"))
                result = self.blocked_with("FRD_UNAPPROVED")
                self.assertEqual(codes(result), ["FRD_UNAPPROVED"])
        self.frd.write_text(FRD.replace("approval_status: approved\n", ""))
        self.assertIn("missing", self.blocked_with("FRD_UNAPPROVED")["reasons"][0]["detail"])

    def test_broken_frd_frontmatter_blocks(self):
        self.frd.write_text("---\napproval_status: approved\n# no closing line\n")
        self.blocked_with("FRD_UNAPPROVED")

    def test_open_frd_question_blocks_with_its_id(self):
        self.edit_frd("2026-11-20\n", "2026-11-20\n- Q-2: How long is a slot? (raised by Test Owner)\n")
        result = self.blocked_with("FRD_OPEN_ITEMS")
        self.assertIn("Q-2", result["reasons"][0]["detail"])
        self.assertEqual(result["counts"]["frd_open_items"], 1)
        self.assertEqual(result["counts"]["open_questions"], 0)  # the story's own counts stay separate

    def test_open_frd_assumption_blocks(self):
        self.edit_frd("## 13.", "Slots are 30 minutes [ASSUMPTION A-2: 30 minutes].\n\n## 13.")
        self.assertIn("A-2", self.blocked_with("FRD_OPEN_ITEMS")["reasons"][0]["detail"])

    def test_frd_resolution_without_who_or_when_blocks(self):
        self.edit_frd("— Test Analyst, 2026-11-20", "")
        self.assertIn("Q-1", self.blocked_with("FRD_OPEN_ITEMS")["reasons"][0]["detail"])

    def test_frd_id_gap_and_duplicate_block(self):
        self.edit_frd("- Q-1: Which", "- Q-3: Which")
        self.assertIn("Q-1, Q-2", self.blocked_with("FRD_OPEN_ITEMS")["reasons"][0]["detail"])
        self.frd.write_text(FRD)
        self.edit_frd("## 13.", "Again [ASSUMPTION-RESOLVED A-1: same id — Test Owner, 2026-11-22].\n\n## 13.")
        self.assertIn("used more than once", self.blocked_with("FRD_OPEN_ITEMS")["reasons"][0]["detail"])

    def test_an_frd_in_the_epic_folder_is_not_used(self):
        self.frd.unlink()
        (self.project / "epics/epic-2/frd.md").write_text(FRD)
        self.blocked_with("FRD_UNAPPROVED")

    def test_numbered_open_questions_heading_counts_in_a_story(self):
        self.edit("## Open questions", "## 7. Open Questions\n\n- Q-3: Is CSV enough?\n\n## 7.1. Open questions")
        self.assertIn("Q-3", self.blocked_with("OPEN_QUESTIONS")["reasons"][0]["detail"])

    def test_frd_text_never_reaches_the_event(self):
        self.edit_frd("Which roles can export?", "Which roles can export? giraffe-mango")
        self.edit_frd("approval_status: approved", "approval_status: draft")
        self.gate()
        raw = (self.project / ".bmad-guard/events/queue.jsonl").read_text()
        self.assertIn("FRD_UNAPPROVED", raw)
        self.assertNotIn("giraffe-mango", raw)


def settled_clinic_frd() -> str:
    """The clinic fixture, approved and with no open question or assumption (the fixture is a draft with both)."""
    text = FIXTURE.read_text(encoding="utf-8").replace("approval_status: draft", "approval_status: approved")
    text = re.sub(r"\s*\[ASSUMPTION[^\]]*\]", "", text)
    return re.sub(r"(## 12\. Open Questions\n\n).*?(\n## 13\.)", r"\1- None.\n\2", text, flags=re.DOTALL)


class RequirementsTests(Base):
    """A feature story's `requirements` must be in its feature's FRD (ADR 0010). Uncovered FRs never block here."""

    def setUp(self):
        super().setUp()
        self.frds = self.project / "frds"
        self.frd = self.frds / "F-001/frd.md"
        self.frd.parent.mkdir(parents=True)
        self.frd.write_text(settled_clinic_frd(), encoding="utf-8")
        self.edit("confidence: 84\n", "confidence: 84\nfeature: F-001\nrequirements: [FR-F001-02]\n")

    def gate(self, *extra: str) -> dict:
        return super().gate("--frds-dir", str(self.frds), *extra)

    def requirements(self, value: str | None) -> None:
        text = re.sub(r"requirements: .*\n", "", self.story.read_text())
        if value is not None:
            text = text.replace("feature: F-001\n", f"feature: F-001\nrequirements: {value}\n")
        self.write(text)

    def blocked_only(self, code: str) -> dict:
        result = self.blocked_with(code)
        self.assertEqual(codes(result), [code], result)
        return result

    def test_the_settled_fixture_is_clean(self):
        self.assertEqual(self.gate()["result"], "allow", self.gate())

    def test_known_ids_pass_whole_split_and_merged(self):
        for value in ("[FR-F001-02]", "[FR-F001-01#1-4, FR-F001-01#6]", "[FR-F001-01, FR-F001-02]", "[FR-F001-02#7]"):
            with self.subTest(requirements=value):
                self.requirements(value)
                self.assertEqual(self.gate()["result"], "allow", value)

    def test_an_unknown_id_blocks_with_the_id_in_the_detail(self):
        self.requirements("[FR-F001-09]")
        result = self.blocked_only("STORY_FR_UNKNOWN")
        self.assertIn("FR-F001-09", result["reasons"][0]["detail"])

    def test_one_unknown_id_among_known_ones_blocks(self):
        self.requirements("[FR-F001-01, FR-F001-09]")
        self.blocked_only("STORY_FR_UNKNOWN")

    def test_an_acceptance_criterion_the_fr_does_not_have_blocks(self):
        self.requirements("[FR-F001-02#8]")
        self.assertIn("criterion 8", self.blocked_only("STORY_FR_UNKNOWN")["reasons"][0]["detail"])

    def test_a_withdrawn_fr_blocks(self):
        text = self.frd.read_text(encoding="utf-8")
        heading = "### 4.2. FR-F001-02 — List Free Slots through the API"
        self.assertIn(heading, text)
        self.frd.write_text(text.replace(heading, heading + " *Withdrawn*"), encoding="utf-8")
        self.assertIn("withdrawn", self.blocked_only("STORY_FR_UNKNOWN")["reasons"][0]["detail"])

    def test_a_malformed_id_blocks(self):
        for value in ("[FR-F001]", "[FR-F001-02#x]", "[fr-f001-02]"):
            with self.subTest(requirements=value):
                self.requirements(value)
                self.blocked_only("STORY_FR_UNKNOWN")

    def test_an_fr_heading_used_twice_in_the_frd_blocks(self):
        text = self.frd.read_text(encoding="utf-8")
        self.frd.write_text(text.replace("### 4.1. FR-F001-01", "### 4.1. FR-F001-02"), encoding="utf-8")
        self.assertIn("more than one block", self.blocked_only("STORY_FR_UNKNOWN")["reasons"][0]["detail"])

    def test_an_id_of_another_feature_blocks(self):
        self.requirements("[FR-F002-01]")
        self.assertIn("another feature", self.blocked_only("STORY_FR_OTHER_FEATURE")["reasons"][0]["detail"])

    def test_both_reasons_come_together(self):
        self.requirements("[FR-F002-01, FR-F001-09]")
        self.assertEqual(
            sorted(codes(self.blocked_with("STORY_FR_UNKNOWN"))), ["STORY_FR_OTHER_FEATURE", "STORY_FR_UNKNOWN"]
        )

    def test_a_feature_story_without_requirements_keeps_the_old_behaviour(self):
        for value in (None, "[]", "none", ""):
            with self.subTest(requirements=value):
                self.requirements(value)
                result = self.gate()
                self.assertEqual(result["result"], "allow", result)

    def test_an_uncovered_fr_never_blocks_the_gate(self):
        self.requirements("[FR-F001-01]")  # FR-F001-02 is in no story: the slicer and the commit warning care, not this
        self.assertEqual(self.gate()["result"], "allow")

    def test_ids_are_not_read_when_the_frd_cannot_be_read(self):
        self.requirements("[FR-F001-09]")
        self.frd.write_text("---\napproval_status: approved\n# no closing line\n")
        self.blocked_only("FRD_UNAPPROVED")
        self.frd.unlink()
        self.blocked_only("FRD_UNAPPROVED")

    def test_an_unapproved_frd_and_an_unknown_id_both_block(self):
        self.frd.write_text(settled_clinic_frd().replace("approval_status: approved", "approval_status: draft"))
        self.requirements("[FR-F001-09]")
        self.assertEqual(codes(self.blocked_with("FRD_UNAPPROVED")), ["FRD_UNAPPROVED", "STORY_FR_UNKNOWN"])

    def test_a_story_without_a_feature_ignores_requirements(self):
        self.write(self.story.read_text().replace("feature: F-001\n", "").replace("[FR-F001-02]", "[FR-F001-09]"))
        self.assertEqual(self.gate()["result"], "allow")

    def test_a_generic_story_is_not_checked(self):
        self.requirements("[FR-F001-09]")
        self.edit("feature: F-001\n", "feature: F-001\ngate: generic\n")
        self.assertEqual(self.gate()["result"], "allow")

    def test_the_event_holds_the_code_and_not_the_id_text(self):
        self.requirements("[FR-F001-09]")
        self.gate()
        raw = (self.project / ".bmad-guard/events/queue.jsonl").read_text()
        self.assertIn("STORY_FR_UNKNOWN", raw)
        self.assertNotIn("FR-F001-09", raw)


ARCHITECTURE = (
    "---\ntitle: Fake architecture\napproval_status: approved\n---\n\n# Fake architecture\n\nOne fake service.\n"
)
ARCH_REL = "arch/ARCHITECTURE-SPINE.md"
TECHNICAL_STORY = """---
story: T-01
feature: none
gate: technical
confidence: 90
---
# T-01 Set up the fake project

Builds what the architecture asks for.

## Open questions

- None.
"""


class TechnicalStoryTests(Base):
    """`gate: technical` is not a skip: the story's own checks run, no epic summary is needed, and the approved,
    unchanged architecture must be on record (Sentry's rules)."""

    def setUp(self):
        super().setUp()
        self.arch = self.project / ARCH_REL
        self.state = self.project / "_bmad/custom/approval-state.json"
        self.arch.parent.mkdir()
        self.approve_architecture()
        self.write(TECHNICAL_STORY)

    def approve_architecture(self, text: str = ARCHITECTURE, doc_type: str = "architecture", **entry) -> None:
        self.arch.write_text(text, encoding="utf-8")
        record = {
            "doc_path": ARCH_REL,
            "approved_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "hash_mode": "full",
            "approved_by": "Test Owner",
            "approved_at": "2026-11-26T10:00:00Z",
            "superseded": False,
            **entry,
        }
        state = json.loads(self.state.read_text()) if self.state.is_file() else {}
        state[doc_type] = record
        self.state.write_text(json.dumps(state), encoding="utf-8")

    def arch_reason(self, why: str, *extra: str) -> None:
        result = self.blocked_with("ARCH_UNAPPROVED", *extra)
        self.assertEqual(codes(result), ["ARCH_UNAPPROVED"], result)
        self.assertTrue(result["reasons"][0]["detail"].endswith(f": {why}"), result["reasons"][0]["detail"])

    def test_approved_architecture_and_no_epic_summary_is_allowed(self):
        result = self.gate()
        self.assertEqual(result["result"], "allow", result)
        self.assertEqual(result["gate"], "technical")
        self.assertIsNone(result["counts"]["frd_open_items"])
        [event] = events(self.project)
        self.assertEqual((event["verdict"], event["gate"]), ("allow", "technical"))

    def test_no_epic_summary_reason_even_when_the_story_links_none(self):
        (self.project / "epics/epic-2/epic-summary.md").unlink()
        self.assertEqual(self.gate()["result"], "allow")

    def test_a_missing_architecture_file_blocks(self):
        self.arch.unlink()
        self.arch_reason("missing")

    def test_nothing_on_record_blocks_as_unapproved(self):
        self.state.unlink()
        self.arch_reason("unapproved")
        self.state.write_text("{}", encoding="utf-8")
        self.arch_reason("unapproved")

    def test_an_architecture_whose_status_is_not_approved_blocks(self):
        self.arch.write_text(ARCHITECTURE.replace("approval_status: approved", "approval_status: draft"))
        self.arch_reason("unapproved")

    def test_a_reopened_approval_blocks_as_superseded(self):
        self.approve_architecture(superseded=True)
        self.arch_reason("superseded")

    def test_an_architecture_edited_after_approval_blocks_as_stale_and_is_left_alone(self):
        edited = ARCHITECTURE + "\nA new fake rule.\n"
        self.arch.write_text(edited, encoding="utf-8")
        self.arch_reason("stale")
        self.assertEqual(self.arch.read_text(encoding="utf-8"), edited, "the gate only asks; Sentry's own run resets")

    def test_an_unreadable_approval_state_blocks(self):
        self.state.write_text("{not json", encoding="utf-8")
        self.arch_reason("state_unreadable")

    def test_an_approval_that_names_a_file_that_is_gone_blocks(self):
        self.approve_architecture(doc_path="arch/OTHER.md")
        self.arch_reason("missing")

    def test_the_document_type_is_a_setting(self):
        self.state.unlink()
        self.approve_architecture(doc_type="architecture-v2")
        self.arch_reason("unapproved")
        self.assertEqual(self.gate("--architecture-doc-type", "architecture-v2")["result"], "allow")

    def test_missing_sentry_blocks_instead_of_waving_the_story_through(self):
        spec = importlib.util.spec_from_file_location("ready_for_dev_no_sentry", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._sentry = lambda: None
        result = module.evaluate(TECHNICAL_STORY, self.story, 80)
        self.assertEqual(codes(result), ["ARCH_UNAPPROVED"])
        self.assertIn("not installed", result["reasons"][0]["detail"])

    def test_a_feature_story_cannot_be_relabelled_technical(self):
        for feature in ("F-001", '"F-001"', "F-1a"):
            with self.subTest(feature=feature):
                self.write(TECHNICAL_STORY.replace("feature: none", f"feature: {feature}"))
                result = self.blocked_with("TECHNICAL_STORY_INVALID")
                self.assertEqual(codes(result), ["TECHNICAL_STORY_INVALID"], "no FRD or epic summary reasons")

    def test_a_technical_story_with_requirements_is_invalid(self):
        self.write(TECHNICAL_STORY.replace("feature: none", "feature: none\nrequirements: [FR-F001-01]"))
        self.assertEqual(codes(self.blocked_with("TECHNICAL_STORY_INVALID")), ["TECHNICAL_STORY_INVALID"])

    def test_no_feature_line_and_empty_requirements_are_valid(self):
        self.write(TECHNICAL_STORY.replace("feature: none\n", ""))
        self.assertEqual(self.gate()["result"], "allow")
        self.write(TECHNICAL_STORY.replace("feature: none", "feature: 'none'\nrequirements: []"))
        self.assertEqual(self.gate()["result"], "allow")

    def test_an_invalid_shape_and_an_unapproved_architecture_both_show(self):
        self.write(TECHNICAL_STORY.replace("feature: none", "feature: F-001"))
        self.arch.unlink()
        self.assertEqual(
            codes(self.blocked_with("TECHNICAL_STORY_INVALID")), ["TECHNICAL_STORY_INVALID", "ARCH_UNAPPROVED"]
        )

    def test_the_story_own_open_question_still_blocks(self):
        self.write(TECHNICAL_STORY.replace("- None.", "- Q-1: Which fake runner?"))
        self.assertEqual(codes(self.blocked_with("OPEN_QUESTIONS")), ["OPEN_QUESTIONS"])

    def test_the_story_own_open_assumption_still_blocks(self):
        self.write(TECHNICAL_STORY.replace("Builds what", "Builds [ASSUMPTION A-1: a fake runner] what"))
        self.assertEqual(codes(self.blocked_with("OPEN_ASSUMPTIONS")), ["OPEN_ASSUMPTIONS"])

    def test_low_and_missing_confidence_still_block(self):
        self.write(TECHNICAL_STORY.replace("confidence: 90", "confidence: 79"))
        self.assertEqual(codes(self.blocked_with("LOW_CONFIDENCE")), ["LOW_CONFIDENCE"])
        self.write(TECHNICAL_STORY.replace("confidence: 90\n", ""))
        self.assertEqual(codes(self.blocked_with("MISSING_CONFIDENCE")), ["MISSING_CONFIDENCE"])

    def test_deleted_items_and_duplicate_ids_still_block(self):
        gap = TECHNICAL_STORY.replace(
            "- None.", "- Q-2: Which fake runner?\n  Resolved: Fake. — Test Owner, 2026-11-26"
        )
        self.write(gap)
        self.assertEqual(codes(self.blocked_with("DELETED_ITEMS")), ["DELETED_ITEMS"])
        twice = TECHNICAL_STORY.replace(
            "- None.",
            "- Q-1: One?\n  Resolved: Yes. — Test Owner, 2026-11-26\n"
            "- Q-1: Two?\n  Resolved: Yes. — Test Owner, 2026-11-26",
        )
        self.write(twice)
        self.assertIn("DUPLICATE_IDS", codes(self.blocked_with("DUPLICATE_IDS")))

    def test_a_resolution_without_who_and_when_still_blocks(self):
        self.write(TECHNICAL_STORY.replace("- None.", "- Q-1: Which fake runner?\n  Resolved: Fake runner."))
        self.assertEqual(codes(self.blocked_with("RESOLUTION_MISSING_WHO_OR_WHEN")), ["RESOLUTION_MISSING_WHO_OR_WHEN"])

    def test_broken_front_matter_is_not_treated_as_technical(self):
        self.write("---\nstory: T-01\ngate: technical\n# no closing line\n\n# T-01\n")
        result = self.blocked_with("MALFORMED_FRONTMATTER")
        self.assertNotIn("ARCH_UNAPPROVED", codes(result))

    def test_another_gate_value_keeps_the_ordinary_checks(self):
        ordinary = TECHNICAL_STORY.replace("feature: none\n", "")
        self.write(ordinary.replace("gate: technical", "gate: strict"))
        self.assertEqual(codes(self.blocked_with("MISSING_EPIC_SUMMARY")), ["MISSING_EPIC_SUMMARY"])
        self.write(ordinary.replace("gate: technical", "gate: strict\nepic: 2"))
        self.assertEqual(self.gate("--epics-dir", str(self.project / "epics"))["result"], "allow")

    def test_a_generic_story_is_still_allowed_without_the_architecture(self):
        self.arch.unlink()
        self.write(TECHNICAL_STORY.replace("gate: technical", "gate: generic"))
        result = self.gate()
        self.assertEqual((result["result"], result["gate"]), ("allow", "generic"))

    def test_the_event_holds_the_code_and_not_the_architecture_text(self):
        self.arch.write_text(ARCHITECTURE.replace("One fake service.", "giraffe-mango"))
        self.gate()
        raw = (self.project / ".bmad-guard/events/queue.jsonl").read_text()
        self.assertIn("ARCH_UNAPPROVED", raw)
        self.assertNotIn("giraffe-mango", raw)


class EventTests(Base):
    def test_event_has_counts_and_codes_but_no_document_text(self):
        self.edit("confidence: 84", "confidence: 70")
        self.gate()
        [event] = events(self.project)
        self.assertEqual(event["guard"], "ready-for-dev")
        self.assertEqual((event["verdict"], event["reason_code"]), ("block", "READY_BLOCK"))
        self.assertEqual(event["target_path"], "stories/2-3.md")
        self.assertEqual(len(event["target_sha256"]), 64)
        self.assertEqual(event["reasons"], "LOW_CONFIDENCE")
        self.assertEqual(json.loads(event["counts"])["confidence"], 70)
        self.assertEqual(event["project"], "demo-api")
        raw = (self.project / ".bmad-guard/events/queue.jsonl").read_text()
        for text in (STORY_ONLY_WORD, "Managers and admins", "Test Analyst", "inactive members"):
            self.assertNotIn(text, raw)

    def test_allow_event(self):
        self.gate()
        [event] = events(self.project)
        self.assertEqual((event["verdict"], event["reason_code"]), ("allow", "READY_ALLOW"))

    def test_unreadable_story_still_records_a_block(self):
        run(self.project, str(self.project / "stories/missing.md"))
        [event] = events(self.project)
        self.assertEqual((event["reason_code"], event["reasons"]), ("READY_BLOCK", "UNREADABLE_STORY"))

    def test_no_event_flag(self):
        self.gate("--no-event")
        self.assertEqual(events(self.project), [])

    def test_works_without_ledger(self):
        lone = self.tmp / "lone"
        shutil.copytree(SKILL, lone / "ready-for-dev-gate")
        code, result, err = run(
            self.project, str(self.story), script=lone / "ready-for-dev-gate/scripts/ready_for_dev.py"
        )
        self.assertEqual((code, result["result"]), (0, "allow"))
        self.assertIn("Ledger (agent-project-logger) not installed", err)
        self.assertEqual(events(self.project), [])


class SkillTests(unittest.TestCase):
    @unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11")
    def test_customize_defaults(self):
        import tomllib

        agent = tomllib.loads((SKILL / "customize.toml").read_text(encoding="utf-8"))["agent"]
        self.assertEqual((agent["code"], agent["mode"], agent["min_confidence"]), ("ready-for-dev-gate", "enforce", 80))
        self.assertTrue(agent["epics_dir"])
        self.assertTrue(agent["frds_dir"])
        self.assertEqual(agent["architecture_doc_type"], "architecture")

    def test_markup_doc_example_blocks_for_the_documented_reasons(self):
        doc = (ROOT / "docs/ready-for-dev-markup.md").read_text(encoding="utf-8")
        example = doc.split("## Worked example")[1].split("```markdown\n")[1].split("\n```")[0]
        with tempfile.TemporaryDirectory() as tmp:
            story = Path(tmp) / "story.md"
            story.write_text(example + "\n")
            summary = Path(tmp) / "summary.md"
            summary.write_text(EPIC_SUMMARY)
            code, result, err = run(Path(tmp), str(story), "--epic-summary", str(summary), "--no-event")
        self.assertEqual(code, 0, err)
        self.assertEqual(codes(result), ["OPEN_QUESTIONS", "OPEN_ASSUMPTIONS", "DELETED_ITEMS"])


if __name__ == "__main__":
    unittest.main()
