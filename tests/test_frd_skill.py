# /// script
# requires-python = ">=3.10"
# ///
"""Checks for Fenn (module/skills/experion-frd) and Iris (module/skills/experion-frd-review): the FRD template
fits the guards, and the overrides wire Sentry, Warden, the reviewer and the ready-for-dev gate to the same
doc_type and file names (one FRD per PRD feature, doc_type frd-F-<nnn>)."""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "module/skills/experion-frd"
REVIEW = ROOT / "module/skills/experion-frd-review"
TEMPLATE = SKILL / "assets/frd-template.md"
OVERRIDES = ROOT / "module/overrides"
GATE = ROOT / "module/skills/ready-for-dev-gate/scripts/ready_for_dev.py"

SECTIONS = [
    "Objective",
    "Upstream & Downstream Processes",
    "Process Flow",
    "Requirement Details",
    "Screens & Attributes",
    "Figma Screens",
    "Dependent Features",
    "Non-Functional Requirements",
    "Data Conversion Requirements",
    "Out of Scope",
    "Dependencies and Constraints",
    "Open Questions",
    "Appendix",
]

needs_toml = unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11")


def gate_module():
    spec = importlib.util.spec_from_file_location("ready_for_dev_for_frd_tests", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def toml(path: Path) -> dict:
    import tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.text = TEMPLATE.read_text(encoding="utf-8")

    def test_has_the_13_sections_in_order(self):
        found = re.findall(r"^## (\d+)\. (.+)$", self.text, re.MULTILINE)
        self.assertEqual([int(n) for n, _ in found], list(range(1, 14)))
        self.assertEqual([title for _, title in found], SECTIONS)

    def test_frontmatter_has_the_guard_fields_and_starts_in_draft(self):
        rfd = gate_module()
        fields, _, error = rfd.parse_frontmatter(self.text)
        self.assertIsNone(error)
        self.assertEqual(fields["approval_status"], "draft")
        self.assertNotIn("epic", fields)
        keys = ("title", "frd_id", "feature", "prd_version", "ado_feature_id", "date", "version")
        for key in keys + ("confidence", "confidence_label", "confidence_rationale"):
            self.assertIn(key, fields)

    def test_the_gate_reads_section_12_as_open_questions(self):
        rfd = gate_module()
        _, body, _ = rfd.parse_frontmatter(self.text)
        questions = rfd.parse_questions(body)
        self.assertEqual([q["id"] for q in questions], ["Q-1", "Q-2"])
        self.assertEqual([q["resolved"] for q in questions], [False, True])

    def test_stable_requirement_ids_and_inherited_nfrs(self):
        self.assertIn("### 4.\\<n\\>. \\<FR-Fxxx-nn\\>", self.text)
        self.assertIn("### 8.1. Inherited NFRs (from PRD)", self.text)
        self.assertIn("FNFR-", self.text)
        self.assertIn("*Withdrawn*", self.text)

    def test_no_story_list_and_no_sofia(self):
        self.assertNotIn("User Stories", self.text)
        self.assertNotIn("Sofia", self.text)

    def test_figma_and_ado_are_optional(self):
        self.assertIn('"Not linked"', self.text)
        self.assertIn("is not a question and does not block anything", self.text)

    def test_process_flow_is_mermaid(self):
        section3 = self.text.split("## 3. Process Flow")[1].split("## 4.")[0]
        self.assertIn("```mermaid", section3)


class OverrideWiringTests(unittest.TestCase):
    @needs_toml
    def test_frd_override_checks_the_prd_and_hands_over_to_warden(self):
        agent = toml(OVERRIDES / "experion-frd.toml")["agent"]
        prepend = " ".join(agent["activation_steps_prepend"])
        self.assertIn("skill:agent-approval-gate-check with doc_type='prd'", prepend)
        warden = [s for s in agent["on_complete"] if "skill:agent-approval-grant" in s]
        self.assertEqual(len(warden), 1)
        defaults = toml(SKILL / "customize.toml")["agent"]
        for arg in (
            "doc_type='frd-<feature>'",
            "review_skill='experion-frd-review'",
            f"review_artifact_filename='{defaults['review_filename']}'",
        ):
            self.assertIn(arg, warden[0])

    @needs_toml
    def test_the_review_runs_before_warden(self):
        steps = toml(OVERRIDES / "experion-frd.toml")["agent"]["on_complete"]
        review = next(i for i, s in enumerate(steps) if s.startswith("skill:experion-frd-review"))
        warden = next(i for i, s in enumerate(steps) if "skill:agent-approval-grant" in s)
        self.assertLess(review, warden)

    @needs_toml
    def test_frd_lives_where_the_gate_looks(self):
        frd = toml(SKILL / "customize.toml")["agent"]
        gate = toml(ROOT / "module/skills/ready-for-dev-gate/customize.toml")["agent"]
        review = toml(REVIEW / "customize.toml")["agent"]
        self.assertEqual(frd["frds_dir"], gate["frds_dir"])
        self.assertEqual(frd["frd_filename"], gate_module().FRD_NAME)
        self.assertEqual(frd["review_filename"], review["review_filename"])
        self.assertTrue(frd["project_template"].endswith("_bmad/custom/templates/frd-template.md"))

    def test_review_writes_the_version_tag_warden_reads(self):
        # Warden reads `{doc_type}_version` from the review file; the doc_type is frd-<feature>.
        text = (REVIEW / "references/review-frd.md").read_text(encoding="utf-8")
        self.assertIn("frd-<feature>_version", text)
        self.assertIn("Disputed:", text)

    def test_fenn_has_no_validate_flow_left(self):
        self.assertFalse((SKILL / "references/validate-frd.md").exists())
        self.assertNotIn("validate-frd", (SKILL / "SKILL.md").read_text(encoding="utf-8"))

    @needs_toml
    def test_prd_override_asks_for_feature_and_nfr_ids(self):
        facts = " ".join(toml(OVERRIDES / "bmad-prd.toml")["workflow"]["persistent_facts"])
        self.assertIn("F-<nnn>", facts)
        self.assertIn("NFR-<nnn>", facts)
        self.assertIn("Applies to", facts)

    @needs_toml
    def test_bmad_build_checks_the_frd_with_sentry_before_the_plan(self):
        facts = toml(OVERRIDES / "bmad-build.toml")["workflow"]["persistent_facts"]
        order = [f.split(" (Guarded BMAD")[0] for f in facts if "(Guarded BMAD" in f]
        self.assertLess(order.index("Feature FRD"), order.index("Plan-first hard gate"))
        frd = next(f for f in facts if f.startswith("Feature FRD"))
        self.assertIn("skill:agent-approval-gate-check with doc_type='frd-<feature>'", frd)

    def test_skill_never_writes_approval_or_resolutions(self):
        for path in [*SKILL.rglob("*.md"), *REVIEW.rglob("*.md")]:
            if path == TEMPLATE:
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotRegex(text, r"(?i)set .{0,40}approval_status.{0,10} to .?approved")


if __name__ == "__main__":
    unittest.main()
