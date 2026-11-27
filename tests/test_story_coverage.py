# /// script
# requires-python = ">=3.10"
# ///
"""Tests for the story coverage check (module/skills/experion-story-slicer, ADR 0010).

The checks run as a library function on throwaway story trees; the command line and the guard event run as a real
program. All data is fake.
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
SKILL = ROOT / "module/skills/experion-story-slicer"
GATE_SKILL = ROOT / "module/skills/ready-for-dev-gate"
SCRIPT = SKILL / "scripts/story_coverage.py"
FIXTURE = ROOT / "tests/fixtures/frd-clinic-f001.md"
ENV = {k: v for k, v in os.environ.items() if not k.startswith("BMAD_GUARD_")}

_spec = importlib.util.spec_from_file_location("story_coverage", SCRIPT)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

TRIGGER = "The fake patient opens the test page while free slots exist today"
SCREEN_SENTENCE = "This fake page lists every free slot for the next seven days and has no filter at all."
POINTER = "Delivers one requirement. Acceptance criteria: the FRD, Section 4."
DEFAULT_FRS = [("01", "View the list of free slots", 3, False), ("02", "List the free slots through the API", 3, False)]


def ac(fr: str, number: int) -> str:
    """A long, unique, fake acceptance criterion."""
    return (
        f"When the test patient performs action number {number} for {fr}, the service shows the matching fake result."
    )


def make_frd(feature: str = "F-001", frs=DEFAULT_FRS, approval: str = "approved", section_4: bool = True) -> str:
    key = feature.replace("-", "")
    lines = ["---", f"frd_id: FRD-TEST-{key}", f"feature: {feature}", f"approval_status: {approval}", "---", ""]
    if section_4:
        lines += ["## 4. Requirement Details", ""]
    for index, (number, title, count, withdrawn) in enumerate(frs, start=1):
        fr = f"FR-{key}-{number}"
        mark = " *Withdrawn*" if withdrawn else ""
        lines += [f"### 4.{index}. {fr} — {title}{mark}", "", "Screen: Section 5.1. Figma: Not linked", ""]
        lines += [f"#### 4.{index}.1. Acceptance Criteria", ""]
        lines += [f"{n}. {ac(fr, n)}" for n in range(1, count + 1)]
        lines += ["", f"#### 4.{index}.2. Key Transactions", "", "| # | Trigger | What Happens | User Sees |"]
        lines += ["| --- | --- | --- | --- |", f"| 1 | {TRIGGER} | Works | Fine |", ""]
    lines += ["## 5. Screens & Attributes", "", "### 5.1. Test page", "", SCREEN_SENTENCE, ""]
    return "\n".join(lines)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def codes(result: dict) -> list[str]:
    return [r["code"] for r in result["reasons"]]


class Base(unittest.TestCase):
    """A project folder with an approved F-001 FRD (FR-F001-01 and -02, three criteria each) and two stories."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.stories = self.root / "stories"
        self.frds = self.root / "frds"
        self.stories.mkdir()
        self.order: list[str] = []
        self.write_frd("F-001", make_frd())
        self.add_story("F001-01", "[FR-F001-01]")
        self.add_story("F001-02", "[FR-F001-02]", deps="[F001-01]")

    def tearDown(self):
        self._tmp.cleanup()

    def write_frd(self, feature: str, text: str) -> None:
        (self.frds / feature).mkdir(parents=True, exist_ok=True)
        (self.frds / feature / "frd.md").write_text(text, encoding="utf-8")

    def add_story(
        self, story_id, requirements, deps="[]", body=POINTER, folder=None, name=None, feature=None, listed=True
    ):
        technical = story_id.startswith("T-")
        folder = folder or ("technical" if technical else f"F-{story_id[1 : story_id.index('-')]}")
        feature = feature or ("none" if technical else folder)
        text = f"---\nstory: {story_id}\nfeature: {feature}\n"
        if requirements is not None:
            text += f"requirements: {requirements}\n"
        text += f"depends_on: {deps}\n---\n# {story_id} A fake story\n\n{body}\n"
        path = self.stories / folder / (name or f"{story_id}-slug.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if listed:
            self.order.append(story_id)
        return path

    def check(self, feature=None, write_order=True) -> dict:
        if write_order:
            (self.stories / "stories.md").write_text(
                "\n".join(f"- [{s}](x/{s}.md) — fake" for s in self.order) + "\n", encoding="utf-8"
            )
        return sc.evaluate(self.stories, self.frds, feature)

    def blocked_with(self, code: str, **kw) -> dict:
        result = self.check(**kw)
        self.assertEqual(result["result"], "block", result)
        self.assertIn(code, codes(result), result)
        return next(r for r in result["reasons"] if r["code"] == code)


class CoverageTests(Base):
    def test_full_coverage_allows(self):
        result = self.check()
        self.assertEqual((result["result"], result["reasons"]), ("allow", []))
        self.assertEqual(
            result["counts"], {"features": 1, "stories": 2, "technical_stories": 0, "frs": 2, "frs_covered": 2}
        )

    def test_the_clinic_fixture_with_thin_stories_allows(self):
        self.write_frd("F-001", FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(self.check()["reasons"], [])

    def test_an_fr_in_no_story_is_uncovered(self):
        (self.stories / "F-001/F001-02-slug.md").unlink()
        self.order.remove("F001-02")
        reason = self.blocked_with("STORY_FR_UNCOVERED")
        self.assertIn("FR-F001-02", reason["detail"])
        self.assertEqual(reason["feature"], "F-001")

    def test_a_withdrawn_fr_needs_no_story(self):
        self.write_frd("F-001", make_frd(frs=[*DEFAULT_FRS, ("03", "Old idea", 0, True)]))
        self.assertEqual(self.check()["reasons"], [])
        self.assertEqual(self.check()["counts"]["frs"], 2)

    def test_split_fr_partly_covered_is_uncovered(self):
        self.add_story("F001-03", "[FR-F001-03#1-2]")
        self.write_frd("F-001", make_frd(frs=[*DEFAULT_FRS, ("03", "Three parts", 4, False)]))
        reason = self.blocked_with("STORY_FR_UNCOVERED")
        self.assertIn("FR-F001-03 acceptance criteria 3, 4", reason["detail"])

    def test_split_fr_fully_covered_allows(self):
        self.write_frd("F-001", make_frd(frs=[*DEFAULT_FRS, ("03", "Three parts", 4, False)]))
        self.add_story("F001-03", "[FR-F001-03#1-2]")
        self.add_story("F001-04", "[FR-F001-03#3,4]")
        result = self.check()
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["counts"]["frs_covered"], 3)

    def test_whole_fr_in_two_stories_is_twice(self):
        self.add_story("F001-03", "[FR-F001-01]")
        reason = self.blocked_with("STORY_FR_TWICE")
        self.assertIn("FR-F001-01", reason["detail"])
        self.assertIn("F001-03", reason["detail"])

    def test_an_ac_number_in_two_stories_is_twice(self):
        self.write_frd("F-001", make_frd(frs=[*DEFAULT_FRS, ("03", "Three parts", 4, False)]))
        self.add_story("F001-03", "[FR-F001-03#1-2]")
        self.add_story("F001-04", "[FR-F001-03#2-4]")
        reason = self.blocked_with("STORY_FR_TWICE")
        self.assertIn("criteria 2 ", reason["detail"])

    def test_whole_fr_plus_a_split_of_it_is_twice(self):
        self.add_story("F001-03", "[FR-F001-01#1]")
        self.blocked_with("STORY_FR_TWICE")

    def test_one_story_may_merge_two_frs(self):
        (self.stories / "F-001/F001-02-slug.md").unlink()
        self.order.remove("F001-02")
        self.add_story("F001-01", "[FR-F001-01, FR-F001-02]")
        self.order.remove("F001-01")  # the rewrite above listed it a second time
        result = self.check()
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["counts"]["frs_covered"], 2)

    def test_an_fr_of_another_feature_is_blocked(self):
        self.add_story("F001-03", "[FR-F002-01]")
        reason = self.blocked_with("STORY_FR_OTHER_FEATURE")
        self.assertEqual(reason["story"], "F001-03")

    def test_an_unknown_fr_is_blocked(self):
        self.add_story("F001-03", "[FR-F001-09]")
        self.assertIn("FR-F001-09", self.blocked_with("STORY_FR_UNKNOWN")["detail"])

    def test_a_withdrawn_fr_in_a_story_is_blocked(self):
        self.write_frd("F-001", make_frd(frs=[*DEFAULT_FRS, ("03", "Old idea", 0, True)]))
        self.add_story("F001-03", "[FR-F001-03]")
        self.assertIn("withdrawn", self.blocked_with("STORY_FR_UNKNOWN")["detail"])

    def test_an_ac_number_the_fr_does_not_have_is_blocked(self):
        self.add_story("F001-03", "[FR-F001-01#9]")
        self.assertIn("criterion 9", self.blocked_with("STORY_FR_UNKNOWN")["detail"])

    def test_a_malformed_id_is_blocked(self):
        self.add_story("F001-03", "[FR-1, banana]")
        result = self.check()
        self.assertEqual(codes(result).count("STORY_FR_UNKNOWN"), 2)

    def test_a_feature_story_without_requirements_is_blocked(self):
        for requirements in (None, "[]"):
            with self.subTest(requirements=requirements):
                self.add_story("F001-03", requirements)
                self.assertIn("no requirements", self.blocked_with("STORY_FR_UNKNOWN")["detail"])
                (self.stories / "F-001/F001-03-slug.md").unlink()
                self.order.remove("F001-03")

    def test_a_technical_story_needs_no_requirements(self):
        self.add_story("T-01", None)
        result = self.check()
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["counts"]["technical_stories"], 1)

    def test_the_technical_summary_is_not_a_story(self):
        (self.stories / "F-001/technical-summary.md").write_text("## Technical\nA fake note.\n", encoding="utf-8")
        self.assertEqual(self.check()["reasons"], [])


class CopiedTextTests(Base):
    def blocked_text(self, body: str) -> dict:
        self.add_story("F001-03", "[]", body=body)  # no requirements: also checked, the text rule still fires
        result = self.check()
        return next(r for r in result["reasons"] if r["code"] == "STORY_FRD_TEXT")

    def test_a_copied_criterion_is_blocked_whatever_the_formatting(self):
        text = ac("FR-F001-01", 2)
        for body in (text, f"- **{text}**", f"2. `{text.upper()}`", f"   {text}   "):
            with self.subTest(body=body[:20]):
                reason = self.blocked_text(body)
                self.assertEqual(reason["story"], "F001-03")
                self.assertNotIn("test patient", reason["detail"])  # line numbers and sections only
                self.assertIn("4.1", reason["detail"])
                (self.stories / "F-001/F001-03-slug.md").unlink()
                self.order.remove("F001-03")

    def test_a_story_line_with_extra_words_around_an_frd_line_is_blocked(self):
        self.blocked_text(f"Must hold: {ac('FR-F001-01', 1)} Also true.")

    def test_a_story_line_that_is_part_of_an_frd_line_is_blocked(self):
        self.blocked_text("the test patient performs action number 3 for FR-F001-02, the service shows")

    def test_a_copied_screen_line_is_blocked(self):
        self.assertIn("5.1", self.blocked_text(SCREEN_SENTENCE)["detail"])

    def test_a_copied_table_cell_is_blocked(self):
        self.assertIn("4.1", self.blocked_text(f"| {TRIGGER} | x |")["detail"])

    def test_a_short_shared_phrase_is_not_flagged(self):
        words = "the service shows the matching fake result"  # 7 words, inside every criterion
        self.assertEqual(len(words.split()), 7)
        self.add_story("F001-03", "[]", body=words)
        self.assertNotIn("STORY_FRD_TEXT", codes(self.check()))

    def test_a_story_heading_that_repeats_an_fr_title_is_not_flagged(self):
        title = "View the list of free slots for the next seven days"  # FRD heading side is left out
        self.write_frd("F-001", make_frd(frs=[("01", title, 3, False), DEFAULT_FRS[1]]))
        (self.stories / "F-001/F001-01-slug.md").write_text(
            f"---\nstory: F001-01\nfeature: F-001\nrequirements: [FR-F001-01]\ndepends_on: []\n---\n# {title}\n",
            encoding="utf-8",
        )
        self.assertEqual(self.check()["reasons"], [])

    def test_the_pointer_line_of_a_thin_story_is_not_flagged(self):
        self.assertNotIn("STORY_FRD_TEXT", codes(self.check()))

    def test_a_technical_story_is_checked_against_every_frd(self):
        self.add_story("T-01", None, body=ac("FR-F001-02", 3))
        reason = self.blocked_with("STORY_FRD_TEXT")
        self.assertEqual((reason["story"], reason["feature"]), ("T-01", "none"))

    def test_the_line_number_is_the_file_line(self):
        self.add_story("F001-03", "[FR-F001-01#1]", body=f"a\n\n{ac('FR-F001-01', 3)}")
        reason = self.blocked_with("STORY_FRD_TEXT")
        self.assertIn("line 11 ", reason["detail"])  # 6 front matter lines, then heading, blank, a, blank, text


class OrderTests(Base):
    def test_a_dependency_on_a_later_story_is_blocked(self):
        self.add_story("F001-02", "[FR-F001-02]", listed=False)  # no longer depends on F001-01
        self.add_story("F001-01", "[FR-F001-01]", deps="[F001-02]", listed=False)
        self.assertIn("comes later", self.blocked_with("STORY_ORDER")["detail"])

    def test_a_dependency_on_a_missing_story_is_blocked(self):
        self.add_story("F001-02", "[FR-F001-02]", deps="[F001-77]", listed=False)
        self.assertIn("F001-77", self.blocked_with("STORY_ORDER")["detail"])

    def test_a_cycle_is_blocked_once_per_story_as_a_cycle(self):
        self.add_story("F001-01", "[FR-F001-01]", deps="[F001-02]", listed=False)
        result = self.check()
        self.assertEqual(codes(result), ["STORY_ORDER", "STORY_ORDER"])
        self.assertTrue(all("cycle" in r["detail"] for r in result["reasons"]))

    def test_a_story_that_depends_on_itself_is_a_cycle(self):
        self.add_story("F001-02", "[FR-F001-02]", deps="[F001-02]", listed=False)
        self.assertIn("cycle", self.blocked_with("STORY_ORDER")["detail"])

    def test_a_story_file_not_in_stories_md_is_blocked(self):
        self.add_story("F001-03", "[]", listed=False)
        self.assertIn("not listed", self.blocked_with("STORY_ORDER")["detail"])

    def test_a_listed_story_without_a_file_is_blocked(self):
        self.order.append("F001-09")
        reason = self.blocked_with("STORY_ORDER")
        self.assertIn("no story file", reason["detail"])
        self.assertEqual(reason["feature"], "F-001")

    def test_a_missing_stories_md_is_blocked(self):
        self.assertIn("stories.md", self.blocked_with("STORY_ORDER", write_order=False)["detail"])

    def test_a_story_listed_twice_is_blocked(self):
        self.order.append("F001-01")
        self.assertIn("twice", self.blocked_with("STORY_ORDER")["detail"])

    def test_a_technical_story_before_a_feature_story_is_fine(self):
        self.add_story("T-01", None, listed=False)
        self.order.insert(0, "T-01")
        self.add_story("F001-01", "[FR-F001-01]", deps="[T-01]", listed=False)
        self.assertEqual(self.check()["reasons"], [])

    def test_numbered_list_items_in_stories_md_count(self):
        (self.stories / "stories.md").write_text("1. [F001-01](a.md)\n2. [F001-02](b.md)\n", encoding="utf-8")
        self.assertEqual(self.check(write_order=False)["reasons"], [])


class FrdTests(Base):
    def test_a_missing_frd_is_unreadable_and_skips_coverage(self):
        (self.frds / "F-001/frd.md").unlink()
        result = self.check()
        self.assertEqual(codes(result), ["STORY_FRD_UNREADABLE"])
        self.assertEqual(result["reasons"][0]["feature"], "F-001")

    def test_an_frd_with_parse_problems_is_unreadable_and_names_the_codes(self):
        self.write_frd("F-001", make_frd(section_4=False))
        reason = self.blocked_with("STORY_FRD_UNREADABLE")
        self.assertIn("FRD_NO_SECTION_4", reason["detail"])
        self.assertEqual(codes(self.check()), ["STORY_FRD_UNREADABLE"])

    def test_an_frd_whose_feature_differs_from_its_folder_is_unreadable(self):
        self.write_frd("F-001", make_frd(feature="F-002"))
        self.assertIn("FRD_FEATURE_MISMATCH", self.blocked_with("STORY_FRD_UNREADABLE")["detail"])

    def test_an_approved_frd_nobody_sliced_is_uncovered(self):
        self.write_frd("F-002", make_frd(feature="F-002"))
        result = self.check()
        self.assertEqual([r["feature"] for r in result["reasons"]], ["F-002", "F-002"])
        self.assertEqual(codes(result), ["STORY_FR_UNCOVERED"] * 2)

    def test_a_draft_or_broken_frd_nobody_sliced_is_ignored(self):
        self.write_frd("F-002", make_frd(feature="F-002", approval="draft"))
        self.write_frd("F-003", make_frd(feature="F-003", section_4=False))
        (self.frds / "F-004").mkdir()
        self.assertEqual(self.check()["reasons"], [])


class FeatureFilterTests(Base):
    def setUp(self):
        super().setUp()
        self.write_frd("F-002", make_frd(feature="F-002"))
        self.add_story("F002-01", "[FR-F002-01, FR-F002-01]")  # F-002 has a gap: FR-F002-02
        self.add_story("T-01", None, body=ac("FR-F001-01", 1))  # a technical story with copied text

    def test_without_the_filter_everything_is_reported(self):
        self.assertEqual(
            sorted(set(codes(self.check()))),
            ["STORY_FRD_TEXT", "STORY_FR_UNCOVERED"],
        )

    def test_the_filter_keeps_only_that_feature(self):
        result = self.check(feature="F-001")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["counts"]["features"], 1)
        only_two = self.check(feature="F-002")
        self.assertEqual(codes(only_two), ["STORY_FR_UNCOVERED"])

    def test_the_filter_names_a_feature_even_when_it_has_no_stories(self):
        self.write_frd("F-005", make_frd(feature="F-005", approval="draft"))
        self.assertEqual(codes(self.check(feature="F-005")), ["STORY_FR_UNCOVERED"] * 2)

    def test_a_broken_frd_of_another_feature_does_not_matter_under_the_filter(self):
        (self.frds / "F-002/frd.md").unlink()
        self.assertEqual(self.check(feature="F-001")["reasons"], [])
        self.assertIn("STORY_FRD_UNREADABLE", codes(self.check()))


class StoryFileTests(Base):
    def invalid(self) -> list[dict]:
        result = self.check()
        return [r for r in result["reasons"] if r["code"] == "STORY_FILE_INVALID"]

    def test_a_file_without_an_id_is_invalid_and_not_used(self):
        (self.stories / "F-001/notes.md").write_text("# Just notes\n", encoding="utf-8")
        [reason] = self.invalid()
        self.assertIn("notes.md", reason["detail"])
        self.assertEqual(reason["feature"], "F-001")

    def test_front_matter_without_a_closing_line_is_invalid(self):
        (self.stories / "F-001/F001-05-slug.md").write_text("---\nstory: F001-05\nno end\n", encoding="utf-8")
        self.assertTrue(any("closing" in r["detail"] for r in self.invalid()))

    def test_a_file_name_that_does_not_start_with_the_id_is_invalid(self):
        self.add_story("F001-03", "[]", name="other-name.md")
        [reason] = self.invalid()
        self.assertEqual(reason["story"], "F001-03")
        self.assertIn("file name", reason["detail"])

    def test_a_feature_that_differs_from_the_folder_is_invalid(self):
        self.add_story("F001-03", "[]", feature="F-002")
        [reason] = self.invalid()
        self.assertIn("does not match its folder", reason["detail"])

    def test_a_technical_story_in_a_feature_folder_is_invalid(self):
        self.add_story("T-01", None, folder="F-001")
        [reason] = self.invalid()
        self.assertIn("does not match its folder", reason["detail"])

    def test_an_id_used_in_two_files_is_invalid(self):
        self.add_story("F001-01", "[FR-F001-01]", name="F001-01-copy.md", listed=False)
        [reason] = self.invalid()
        self.assertIn("used twice", reason["detail"])

    def test_a_file_that_is_not_utf8_is_invalid(self):
        (self.stories / "F-001/F001-06-slug.md").write_bytes(b"\xff\xfe\x00")
        [reason] = self.invalid()
        self.assertIn("cannot read", reason["detail"])


class CannotRunTests(Base):
    def test_a_missing_stories_folder_raises(self):
        with self.assertRaises(sc.CoverageError) as ctx:
            sc.evaluate(self.root / "nope", self.frds)
        self.assertEqual(ctx.exception.code, "STORIES_DIR_UNREADABLE")

    def test_a_missing_frds_folder_raises(self):
        with self.assertRaises(sc.CoverageError) as ctx:
            sc.evaluate(self.stories, self.root / "nope")
        self.assertEqual(ctx.exception.code, "FRDS_DIR_UNREADABLE")


def run(cwd: Path, *args: str, script: Path = SCRIPT) -> tuple[int, dict | None, str]:
    proc = subprocess.run([sys.executable, str(script), *args], cwd=cwd, env=ENV, capture_output=True, text=True)
    out = proc.stdout.strip()
    return proc.returncode, (json.loads(out) if out else None), proc.stderr


def events(project: Path) -> list[dict]:
    queue = project / ".bmad-guard/events/queue.jsonl"
    return [json.loads(line) for line in queue.read_text().splitlines()] if queue.exists() else []


class CliTests(Base):
    def setUp(self):
        super().setUp()
        git(self.root, "init", "-q", "-b", "main")
        (self.root / "_bmad/custom").mkdir(parents=True)
        (self.root / "_bmad/custom/project.json").write_text('{"project_name": "demo-api"}')
        self.args = ["--stories-dir", str(self.stories), "--frds-dir", str(self.frds)]
        self.check()  # writes stories.md

    def test_allow_prints_json_and_exits_0(self):
        code, result, _ = run(self.root, *self.args, "--no-event")
        self.assertEqual((code, result["result"], result["reasons"]), (0, "allow", []))
        self.assertEqual(result["counts"]["frs_covered"], 2)

    def test_block_prints_json_and_exits_0(self):
        self.add_story("F001-03", "[FR-F001-01]")
        self.check()
        code, result, _ = run(self.root, *self.args, "--no-event")
        self.assertEqual((code, result["result"], codes(result)), (0, "block", ["STORY_FR_TWICE"]))

    def test_the_default_folders_are_used(self):
        base = self.root / "_bmad-output/planning-artifacts"
        base.mkdir(parents=True)
        shutil.copytree(self.stories, base / "stories")
        shutil.copytree(self.frds, base / "frds")
        code, result, _ = run(self.root, "--no-event")
        self.assertEqual((code, result["result"]), (0, "allow"))

    def test_the_feature_option_limits_the_check(self):
        self.write_frd("F-002", make_frd(feature="F-002"))
        code, result, _ = run(self.root, *self.args, "--feature", "F-001", "--no-event")
        self.assertEqual((code, result["result"]), (0, "allow"))
        code, result, _ = run(self.root, *self.args, "--no-event")
        self.assertEqual((code, result["result"]), (0, "block"))

    def test_a_bad_feature_option_exits_2(self):
        code, _, err = run(self.root, *self.args, "--feature", "banana", "--no-event")
        self.assertEqual(code, 2)
        self.assertIn("F-001", err)

    def test_a_missing_folder_exits_2_with_a_block(self):
        code, result, _ = run(self.root, "--stories-dir", str(self.root / "nope"), "--frds-dir", str(self.frds))
        self.assertEqual((code, result["result"], codes(result)), (2, "block", ["STORIES_DIR_UNREADABLE"]))
        [event] = events(self.root)
        self.assertEqual((event["verdict"], event["reasons"]), ("block", "STORIES_DIR_UNREADABLE"))

    def test_a_missing_frd_parser_exits_2_with_a_block(self):
        lone = self.root / "lone"
        shutil.copytree(SKILL, lone / "experion-story-slicer")
        code, result, _ = run(
            self.root, *self.args, "--no-event", script=lone / "experion-story-slicer/scripts/story_coverage.py"
        )
        self.assertEqual((code, result["result"], codes(result)), (2, "block", ["FRD_PARSE_MISSING"]))

    def test_one_event_with_counts_and_codes_but_no_text(self):
        (self.stories / "F-001/F001-02-slug.md").write_text(
            "---\nstory: F001-02\nfeature: F-001\nrequirements: [FR-F001-02]\ndepends_on: []\n---\n"
            + ac("FR-F001-02", 2),
            encoding="utf-8",
        )
        code, result, _ = run(self.root, *self.args)
        self.assertEqual((code, result["result"]), (0, "block"))
        [event] = events(self.root)
        self.assertEqual(
            (event["guard"], event["stage"], event["verdict"]), ("story-coverage", "story-coverage", "block")
        )
        self.assertEqual((event["reason_code"], event["reasons"]), ("STORIES_CHECKED", "STORY_FRD_TEXT"))
        self.assertEqual(json.loads(event["counts"])["frs"], 2)
        self.assertEqual((event["project"], event["target_path"]), ("demo-api", "stories"))
        raw = (self.root / ".bmad-guard/events/queue.jsonl").read_text()
        self.assertNotIn("test patient", raw)

    def test_an_allow_event(self):
        run(self.root, *self.args)
        [event] = events(self.root)
        self.assertEqual((event["verdict"], event["reason_code"]), ("allow", "STORIES_CHECKED"))
        self.assertNotIn("reasons", event)  # an empty field is not recorded

    def test_no_event_flag_writes_nothing(self):
        run(self.root, *self.args, "--no-event")
        self.assertEqual(events(self.root), [])

    def test_works_without_ledger(self):
        lone = self.root / "lone"
        shutil.copytree(SKILL, lone / "experion-story-slicer")
        shutil.copytree(GATE_SKILL, lone / "ready-for-dev-gate")
        code, result, err = run(self.root, *self.args, script=lone / "experion-story-slicer/scripts/story_coverage.py")
        self.assertEqual((code, result["result"]), (0, "allow"))
        self.assertIn("Ledger (agent-project-logger) not installed", err)
        self.assertEqual(events(self.root), [])


if __name__ == "__main__":
    unittest.main()
