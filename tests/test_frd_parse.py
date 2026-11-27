# /// script
# requires-python = ">=3.10"
# ///
"""Tests for the shared FRD parser (module/skills/ready-for-dev-gate/scripts/frd_parse.py, ADR 0010).

The fixture is a copy of the clinic demo FRD for F-001 (fake data only). Edits are made on its text.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "module/skills/ready-for-dev-gate/scripts/frd_parse.py"
FIXTURE = ROOT / "tests/fixtures/frd-clinic-f001.md"
GATE = ROOT / "module/skills/ready-for-dev-gate/scripts/ready_for_dev.py"

HEADING_2 = "### 4.2. FR-F001-02 — List Free Slots through the API"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fp = load("frd_parse_under_test", SCRIPT)
TEXT = FIXTURE.read_text(encoding="utf-8")


def codes(problems: list[dict]) -> list[str]:
    return [p["code"] for p in problems]


def numbers(block: dict) -> list[str]:
    return [c["number"] for c in block["criteria"]]


def edit(old: str, new: str, text: str = TEXT) -> str:
    assert old in text, old
    return text.replace(old, new)


def select(ids: list[str], text: str = TEXT) -> dict:
    return fp.select_blocks(fp.parse_frd(text), ids)


class ClinicShapeTests(unittest.TestCase):
    """The F-001 FRD as Fenn writes it."""

    def setUp(self):
        self.frd = fp.parse_frd(TEXT)
        self.first, self.second = self.frd["blocks"]

    def test_front_matter_and_feature(self):
        self.assertEqual((self.frd["feature"], self.frd["frd_id"]), ("F-001", "FRD-CLINIC-F001"))
        self.assertEqual(self.frd["fields"]["approval_status"], "draft")
        self.assertIsNone(self.frd["frontmatter_error"])
        self.assertTrue(self.frd["body"].startswith("\n## Product Overview"))

    def test_the_fixture_has_no_problems(self):
        self.assertEqual(self.frd["problems"], [])

    def test_fr_blocks(self):
        self.assertEqual([b["id"] for b in self.frd["blocks"]], ["FR-F001-01", "FR-F001-02"])
        self.assertEqual((self.first["section"], self.first["title"]), ("4.1", "View Free Slots on the Web UI"))
        self.assertEqual((self.second["section"], self.second["title"]), ("4.2", "List Free Slots through the API"))
        self.assertFalse(self.first["withdrawn"] or self.second["withdrawn"])
        self.assertTrue(self.first["text"].startswith("### 4.1. FR-F001-01"))
        self.assertTrue(self.first["text"].rstrip().endswith("| Nothing |"))
        self.assertNotIn("FR-F001-02 —", self.first["text"])

    def test_the_line_is_the_line_in_the_file(self):
        lines = TEXT.splitlines()
        for block in self.frd["blocks"]:
            self.assertTrue(lines[block["line"] - 1].startswith(f"### {block['section']}. {block['id']}"))

    def test_screen_reference(self):
        self.assertEqual(self.first["screen"], "Screen: Section 5.1. Figma: Not linked")
        self.assertEqual(self.first["screen_ids"], ["5.1"])
        self.assertTrue(self.second["screen"].startswith("Screen: none."))
        self.assertEqual(self.second["screen_ids"], [])

    def test_criteria_are_numbered_items(self):
        self.assertEqual(numbers(self.first), [str(n) for n in range(1, 14)])
        first = self.first["criteria"][0]
        self.assertTrue(first["text"].startswith("When the Patient opens the Web UI"))
        self.assertIn("[ASSUMPTION A-1:", first["text"])  # the whole line, tags included

    def test_sub_criteria_are_numbered_under_their_item(self):
        self.assertEqual(numbers(self.second), ["1", "2", "3", "4", "5", "6", "7", "7.1", "7.2", "7.3"])
        sub = {c["number"]: c["text"] for c in self.second["criteria"]}
        self.assertTrue(sub["7.1"].startswith("A Host header other than"))
        self.assertTrue(sub["7.3"].startswith("Anything unexpected"))

    def test_key_transactions_table(self):
        table = self.first["transactions"]
        self.assertEqual(table["header"], ["#", "Trigger", "What Happens", "User Sees"])
        self.assertEqual(len(table["rows"]), 5)
        self.assertEqual(table["rows"][1][1], "Patient opens the Web UI and no Free Slot exists")
        self.assertEqual(len(self.second["transactions"]["rows"]), 4)

    def test_screens_of_section_5(self):
        [screen] = self.frd["screens"]
        self.assertEqual((screen["id"], screen["title"]), ("5.1", "Booking page - See Free Slots"))
        self.assertIn("| 5 | Load error message |", screen["text"])
        self.assertNotIn("## 6.", screen["text"])

    def test_inherited_nfr_rows(self):
        rows = self.frd["nfrs"]
        self.assertEqual([r["id"] for r in rows], ["NFR-001", "NFR-002", "NFR-004", "NFR-005", "NFR-006"])
        self.assertEqual(
            (rows[0]["category"], rows[0]["target"]),
            ("Local only", "The service accepts connections from the same machine only."),
        )
        self.assertIn("FR-F001-01 and FR-F001-02", rows[0]["applies_to"])

    def test_windows_line_endings_read_the_same(self):
        crlf = fp.parse_frd(TEXT.replace("\n", "\r\n"))
        self.assertEqual(crlf["problems"], [])
        self.assertEqual([numbers(b) for b in crlf["blocks"]], [numbers(self.first), numbers(self.second)])
        self.assertEqual(crlf["feature"], "F-001")
        self.assertEqual(crlf["blocks"][1]["line"], self.second["line"])


class WithdrawnTests(unittest.TestCase):
    def test_marker_in_the_heading(self):
        frd = fp.parse_frd(edit(HEADING_2, HEADING_2 + " *Withdrawn*"))
        self.assertTrue(frd["blocks"][1]["withdrawn"])
        self.assertFalse(frd["blocks"][0]["withdrawn"])
        self.assertEqual(frd["blocks"][1]["title"], "List Free Slots through the API")
        self.assertEqual(frd["problems"], [])

    def test_marker_on_the_first_line_under_the_heading(self):
        frd = fp.parse_frd(
            edit(HEADING_2 + "\n\nScreen: none.", HEADING_2 + "\n\n**Withdrawn** in 1.2.\n\nScreen: none.")
        )
        self.assertTrue(frd["blocks"][1]["withdrawn"])

    def test_the_word_inside_a_criterion_is_not_a_marker(self):
        text = edit(
            "5. When there is no Free Slot,", "5. A *Withdrawn* booking is not listed. When there is no Free Slot,"
        )
        self.assertFalse(fp.parse_frd(text)["blocks"][1]["withdrawn"])

    def test_a_withdrawn_fr_without_criteria_is_not_a_defect(self):
        withdrawn = "### 4.3. FR-F001-03 — Old idea *Withdrawn*\n\nDropped in 1.2.\n\n"
        frd = fp.parse_frd(edit("## 5. Screens", withdrawn + "## 5. Screens"))
        self.assertTrue(frd["blocks"][2]["withdrawn"])
        self.assertEqual(frd["problems"], [])

    def test_selecting_a_withdrawn_fr_is_a_problem_and_returns_no_block(self):
        picked = select(["FR-F001-02"], edit(HEADING_2, HEADING_2 + " *Withdrawn*"))
        self.assertEqual(codes(picked["problems"]), ["FR_WITHDRAWN"])
        self.assertEqual(picked["blocks"], [])


class SelectTests(unittest.TestCase):
    def test_a_whole_fr_comes_with_its_screen_and_nfr_rows(self):
        picked = select(["FR-F001-01"])
        self.assertEqual(picked["problems"], [])
        [block] = picked["blocks"]
        self.assertEqual((block["id"], len(block["criteria"]), block["ac_selected"]), ("FR-F001-01", 13, None))
        self.assertEqual([s["id"] for s in picked["screens"]], ["5.1"])
        # NFR-006 names only FR-F001-02, so it is not an NFR of FR-F001-01
        self.assertEqual([r["id"] for r in picked["nfrs"]], ["NFR-001", "NFR-002", "NFR-004", "NFR-005"])
        self.assertEqual(len(select(["FR-F001-02"])["nfrs"]), 5)

    def test_blocks_come_in_the_order_asked(self):
        picked = select(["FR-F001-02", "FR-F001-01"])
        self.assertEqual([b["id"] for b in picked["blocks"]], ["FR-F001-02", "FR-F001-01"])
        self.assertEqual([s["id"] for s in picked["screens"]], ["5.1"])  # FR-F001-02 has none

    def test_an_nfr_row_that_names_only_other_frs_is_left_out(self):
        text = TEXT.replace("Test data for FR-F001-01 and FR-F001-02 is fake", "Test data for FR-F001-01 is fake")
        self.assertEqual(
            [r["id"] for r in select(["FR-F001-02"], text)["nfrs"]], ["NFR-001", "NFR-004", "NFR-005", "NFR-006"]
        )
        self.assertEqual(
            [r["id"] for r in select(["FR-F001-01"], text)["nfrs"]], ["NFR-001", "NFR-002", "NFR-004", "NFR-005"]
        )

    def test_an_nfr_row_that_names_no_fr_applies_to_all(self):
        text = TEXT.replace("Test data for FR-F001-01 and FR-F001-02 is fake", "Test data is fake")
        self.assertIn("NFR-002", [r["id"] for r in select(["FR-F001-02"], text)["nfrs"]])

    def test_range_split_returns_only_those_criteria(self):
        [block] = select(["FR-F001-01#1-4"])["blocks"]
        self.assertEqual(numbers(block), ["1", "2", "3", "4"])
        self.assertEqual(block["ac_selected"], [1, 2, 3, 4])
        self.assertIn("4. The service creates the Slots automatically.", block["text"])
        self.assertNotIn("5. A Slot whose start time is now", block["text"])
        self.assertIn("#### 4.1.2. Key Transactions", block["text"])  # the table stays whole
        self.assertEqual(len(block["transactions"]["rows"]), 5)

    def test_list_split_returns_only_those_criteria(self):
        picked = select(["FR-F001-01#2,5"])
        self.assertEqual(picked["problems"], [])
        self.assertEqual(numbers(picked["blocks"][0]), ["2", "5"])

    def test_a_split_keeps_the_sub_criteria_of_a_chosen_item(self):
        [block] = select(["FR-F001-02#7"])["blocks"]
        self.assertEqual(numbers(block), ["7", "7.1", "7.2", "7.3"])
        self.assertIn("    1. A Host header", block["text"])
        [block] = select(["FR-F001-02#1-6"])["blocks"]
        self.assertNotIn("7.1", numbers(block))
        self.assertNotIn("Host header", block["text"])

    def test_two_splits_of_one_fr_are_joined_and_a_whole_fr_wins(self):
        [block] = select(["FR-F001-01#1-2", "FR-F001-01#4"])["blocks"]
        self.assertEqual(numbers(block), ["1", "2", "4"])
        [block] = select(["FR-F001-01#1-2", "FR-F001-01"])["blocks"]
        self.assertEqual((len(block["criteria"]), block["ac_selected"]), (13, None))

    def test_unknown_id(self):
        picked = select(["FR-F001-99"])
        self.assertEqual(codes(picked["problems"]), ["FR_UNKNOWN"])
        self.assertIn("FR-F001-99", picked["problems"][0]["detail"])
        self.assertEqual(picked["blocks"], [])

    def test_id_of_another_feature(self):
        picked = select(["FR-F002-01", "FR-F001-01"])
        self.assertEqual(codes(picked["problems"]), ["FR_OTHER_FEATURE"])
        self.assertEqual([b["id"] for b in picked["blocks"]], ["FR-F001-01"])  # the good id still comes back

    def test_ac_number_that_does_not_exist(self):
        picked = select(["FR-F001-02#6-9"])
        self.assertEqual(codes(picked["problems"]), ["FR_AC_UNKNOWN"])
        self.assertIn("criterion 8, 9", picked["problems"][0]["detail"])
        self.assertEqual(numbers(picked["blocks"][0]), ["6", "7", "7.1", "7.2", "7.3"])

    def test_a_sub_number_is_not_an_ac_number(self):
        self.assertEqual(codes(select(["FR-F001-02#7.1"])["problems"]), ["FR_ID_MALFORMED"])

    def test_malformed_ids(self):
        for spec in (
            "FR-1",
            "F001-02",
            "",
            "FR-F001-02#",
            "FR-F001-02#0",
            "FR-F001-02#4-1",
            "FR-F001-02#a",
            "FR-F001-02#1-99999",
        ):
            with self.subTest(spec=spec):
                picked = select([spec])
                self.assertEqual(codes(picked["problems"]), ["FR_ID_MALFORMED"])
                self.assertEqual(picked["blocks"], [])

    def test_fr_that_names_a_missing_screen(self):
        picked = select(["FR-F001-01"], edit("Screen: Section 5.1.", "Screen: Section 5.1 and 5.4."))
        self.assertEqual(codes(picked["problems"]), ["FR_SCREEN_UNKNOWN"])
        self.assertIn("5.4", picked["problems"][0]["detail"])

    def test_without_a_feature_in_the_frd_another_feature_id_is_just_unknown(self):
        picked = select(["FR-F002-01"], edit("feature: F-001\n", ""))
        self.assertEqual(codes(picked["problems"]), ["FR_UNKNOWN"])


class SplitIdsTests(unittest.TestCase):
    def test_a_number_list_after_a_hash_stays_with_its_id(self):
        self.assertEqual(
            fp.split_ids("FR-F001-02,FR-F001-04#2,5,FR-F001-05"), ["FR-F001-02", "FR-F001-04#2,5", "FR-F001-05"]
        )
        self.assertEqual(fp.split_ids("FR-F001-04#1-2,4"), ["FR-F001-04#1-2,4"])

    def test_brackets_quotes_and_spaces_are_dropped(self):
        self.assertEqual(fp.split_ids("[FR-F001-02, 'FR-F001-04#1-4']"), ["FR-F001-02", "FR-F001-04#1-4"])
        self.assertEqual(fp.split_ids(" , "), [])

    def test_a_bare_number_without_a_hash_id_is_kept_as_an_item(self):
        self.assertEqual(fp.split_ids("FR-F001-02,5"), ["FR-F001-02", "5"])  # select then reports it as malformed


class ProblemTests(unittest.TestCase):
    def test_missing_section_4(self):
        text = TEXT.replace("## 4. Requirement Details", "## 4x. Requirement Details")
        frd = fp.parse_frd(text)
        self.assertEqual(codes(frd["problems"]), ["FRD_NO_SECTION_4"])
        self.assertEqual(frd["blocks"], [])
        self.assertEqual(codes(fp.select_blocks(frd, ["FR-F001-01"])["problems"]), ["FR_UNKNOWN"])

    def test_section_4_without_fr_headings(self):
        frd = fp.parse_frd("---\nfeature: F-001\n---\n\n## 4. Requirement Details\n\nNothing yet.\n")
        self.assertEqual(codes(frd["problems"]), ["FRD_NO_FR_BLOCKS"])

    def test_duplicate_fr_id(self):
        text = edit("### 4.2. FR-F001-02 —", "### 4.2. FR-F001-01 —")
        frd = fp.parse_frd(text)
        self.assertEqual(codes(frd["problems"]), ["FRD_FR_DUPLICATE"])
        self.assertIn("FR-F001-01", frd["problems"][0]["detail"])
        picked = fp.select_blocks(frd, ["FR-F001-01", "FR-F001-02"])
        self.assertEqual(codes(picked["problems"]), ["FR_DUPLICATE", "FR_UNKNOWN"])
        self.assertEqual(picked["blocks"], [])  # an ambiguous id returns nothing

    def test_malformed_headings(self):
        cases = {
            "id has one digit": "### 4.2. FR-F001-2 — List Free Slots through the API",
            "no id": "### 4.2. List Free Slots through the API",
            "no dash": "### 4.2. FR-F001-02 List Free Slots through the API",
            "no title": "### 4.2. FR-F001-02 —",
            "wrong section number": "### 3.2. FR-F001-02 — List Free Slots through the API",
        }
        for name, heading in cases.items():
            with self.subTest(name):
                frd = fp.parse_frd(edit(HEADING_2, heading))
                self.assertEqual(codes(frd["problems"]), ["FRD_FR_HEADING_MALFORMED"])
                self.assertIn(f"line {TEXT.splitlines().index(HEADING_2) + 1}:", frd["problems"][0]["detail"])
                self.assertEqual([b["id"] for b in frd["blocks"]], ["FR-F001-01"])  # the good block is still read

    def test_a_malformed_heading_does_not_swallow_the_block_before_it(self):
        frd = fp.parse_frd(edit(HEADING_2, "### 4.2. Oops"))
        self.assertNotIn("Oops", frd["blocks"][0]["text"])

    def test_fr_without_criteria(self):
        text = edit("#### 4.2.1. Acceptance Criteria", "#### 4.2.1. Rules")
        frd = fp.parse_frd(text)
        self.assertEqual(codes(frd["problems"]), ["FRD_FR_NO_CRITERIA"])
        self.assertEqual(frd["blocks"][1]["criteria"], [])

    def test_frontmatter_problems(self):
        frd = fp.parse_frd("---\nfeature: F-001\ntitle: no end\n\n## 4. Requirement Details\n")
        self.assertEqual(frd["frontmatter_error"], "frontmatter starts with '---' but has no closing '---' line")
        self.assertEqual(codes(frd["problems"]), ["FRD_FRONTMATTER", "FRD_NO_FEATURE", "FRD_NO_FR_BLOCKS"])

    def test_missing_or_bad_feature(self):
        for text in (edit("feature: F-001\n", ""), edit("feature: F-001\n", "feature: Feature 1\n")):
            frd = fp.parse_frd(text)
            self.assertEqual(codes(frd["problems"]), ["FRD_NO_FEATURE"])
            self.assertEqual(frd["feature"], "")

    def test_headings_inside_a_code_fence_are_not_headings(self):
        fenced = "```\n### 4.9. FR-F001-09 — Not a block\n## 5. Not a section\n```\n\n"
        frd = fp.parse_frd(edit("#### 4.2.1. Acceptance Criteria", fenced + "#### 4.2.1. Acceptance Criteria"))
        self.assertEqual([b["id"] for b in frd["blocks"]], ["FR-F001-01", "FR-F001-02"])
        self.assertEqual(frd["problems"], [])
        self.assertEqual(len(frd["screens"]), 1)

    def test_odd_input_never_raises(self):
        odd = [
            "",
            "\n",
            "---\n",
            "---\n---\n",
            "# only a title\n",
            "## 4. Requirement Details\n### 4.1. FR-F001-01 — x\n#### 4.1.1. Acceptance Criteria\n    1. orphan\n",
            "## 4.\n### \n####\n| a | b\n|\n||\n",
            "## 8. NFR\n### 8.1. Inherited\n| # | NFR |\n| 1 |\n",
            "\x00\x01﻿---\nfeature: F-001\n---\n",
            "## 5. Screens\n### 5.x. Bad\n### 5.2\n",
        ]
        for text in odd:
            with self.subTest(text=text[:30]):
                frd = fp.parse_frd(text)
                self.assertIsInstance(frd["problems"], list)
                fp.select_blocks(frd, ["FR-F001-01#1", "#", "FR-F001-01"])

    def test_the_orphan_sub_item_is_not_a_criterion(self):
        text = "## 4. Requirement Details\n### 4.1. FR-F001-01 — x\n#### 4.1.1. Acceptance Criteria\n    1. orphan\n"
        self.assertEqual(fp.parse_frd(text)["blocks"][0]["criteria"], [])

    def test_a_wrapped_criterion_line_joins_its_item(self):
        text = edit("2. The system shows only Slots for today", "2. The system shows only Slots\n   for today")
        criterion = fp.parse_frd(text)["blocks"][0]["criteria"][1]
        self.assertEqual(criterion["number"], "2")
        self.assertTrue(criterion["text"].startswith("The system shows only Slots for today and the 6 days"))

    def test_nfr_row_with_an_escaped_pipe_and_fnfr_ids(self):
        text = edit("| 5 | NFR-006 | Input checks |", "| 5 | NFR-006 | Input a \\| b checks |")
        self.assertEqual(fp.parse_frd(text)["nfrs"][4]["category"], "Input a | b checks")
        row = {"applies_to": "see FNFR-F001-09"}
        self.assertTrue(fp._applies(row, {"FR-F001-01"}))  # FNFR-... is not an FR id


def run(*args: str) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
    return proc.returncode, (json.loads(proc.stdout) if proc.stdout.strip() else {})


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.frd = Path(self.tmp.name) / "frd.md"
        self.frd.write_text(TEXT, encoding="utf-8")

    def test_ids_print_one_json_object_and_exit_0(self):
        code, out = run("--frd", str(self.frd), "--ids", "FR-F001-02,FR-F001-01#1-4", "--json")
        self.assertEqual(code, 0)
        self.assertEqual((out["result"], out["problems"]), ("ok", []))
        self.assertEqual((out["feature"], out["frd_id"]), ("F-001", "FRD-CLINIC-F001"))
        self.assertEqual([b["id"] for b in out["blocks"]], ["FR-F001-02", "FR-F001-01"])
        self.assertEqual(numbers(out["blocks"][1]), ["1", "2", "3", "4"])
        self.assertEqual([s["id"] for s in out["screens"]], ["5.1"])
        self.assertEqual(len(out["nfrs"]), 5)

    def test_json_is_printed_without_the_flag_too(self):
        self.assertEqual(run("--frd", str(self.frd), "--ids", "FR-F001-01")[1]["result"], "ok")

    def test_a_list_split_with_commas_survives_the_command_line(self):
        code, out = run("--frd", str(self.frd), "--ids", "FR-F001-01#2,5,FR-F001-02")
        self.assertEqual((code, out["problems"]), (0, []))
        self.assertEqual([numbers(b) for b in out["blocks"]][0], ["2", "5"])
        self.assertEqual(len(out["blocks"]), 2)

    def test_no_ids_prints_every_block(self):
        code, out = run("--frd", str(self.frd))
        self.assertEqual((code, out["result"]), (0, "ok"))
        self.assertEqual([b["id"] for b in out["blocks"]], ["FR-F001-01", "FR-F001-02"])

    def test_problems_are_in_the_json_and_the_exit_code_is_0(self):
        code, out = run("--frd", str(self.frd), "--ids", "FR-F001-99,FR-F002-01,FR-F001-02#9")
        self.assertEqual((code, out["result"]), (0, "problems"))
        self.assertEqual(codes(out["problems"]), ["FR_UNKNOWN", "FR_OTHER_FEATURE", "FR_AC_UNKNOWN"])

    def test_problems_of_the_frd_itself_are_in_the_json(self):
        self.frd.write_text(edit(HEADING_2, "### 4.2. Oops"), encoding="utf-8")
        code, out = run("--frd", str(self.frd), "--ids", "FR-F001-01")
        self.assertEqual((code, out["result"]), (0, "problems"))
        self.assertEqual(codes(out["problems"]), ["FRD_FR_HEADING_MALFORMED"])
        self.assertEqual([b["id"] for b in out["blocks"]], ["FR-F001-01"])

    def test_unreadable_file_exits_2_with_json(self):
        code, out = run("--frd", str(Path(self.tmp.name) / "missing.md"), "--ids", "FR-F001-01")
        self.assertEqual((code, out["result"], out["blocks"]), (2, "problems", []))
        self.assertEqual(codes(out["problems"]), ["FRD_UNREADABLE"])
        self.assertNotIn(self.tmp.name, out["problems"][0]["detail"])

    def test_a_file_that_is_not_utf8_exits_2(self):
        self.frd.write_bytes(b"---\nfeature: F-001\n---\n\xff\xfe\x00")
        code, out = run("--frd", str(self.frd))
        self.assertEqual((code, codes(out["problems"])), (2, ["FRD_UNREADABLE"]))

    def test_missing_argument_exits_2(self):
        self.assertEqual(run()[0], 2)


if __name__ == "__main__":
    unittest.main()
