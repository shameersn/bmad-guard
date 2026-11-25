# /// script
# requires-python = ">=3.10"
# ///
"""Tests for the ready-for-dev gate (module/skills/ready-for-dev-gate, ADR 0004).

The script runs as a real command-line program against throwaway git projects. All data is fake.
"""

from __future__ import annotations

import json
import os
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
