# /// script
# requires-python = ">=3.10"
# ///
"""Checks for module/overrides/: every override fits the BMAD version we pin (6.12.4) or our own skills.

tests/fixtures/bmad-6.12.4-customize.json lists, per stock skill, the customize.toml sections and the
type of each key. It was generated from a clean BMAD 6.12.4 install (Phase 1, T6). Regenerate it when
the BMAD pin changes.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / "module/overrides"
MODULE_SKILLS = ROOT / "module/skills"
FIXTURE = json.loads((ROOT / "tests/fixtures/bmad-6.12.4-customize.json").read_text())
STOCK = FIXTURE["skills"]
TOML_FILES = sorted(OVERRIDES.glob("*.toml"))

# Skills whose SKILL.md documents a list for on_complete (checked against 6.12.4 in T6).
ON_COMPLETE_LIST_OK = {"bmad-product-brief", "bmad-prd", "bmad-architecture", "bmad-ux"}
REMOVED_IN_6_12 = {"bmad-dev-story", "bmad-quick-dev", "bmad-create-story"}
PROJECT_SPECIFIC = ("/api/ideas", "ideaService", ".ai-context", "onrender.com", "token optimiz")
FILE_REF = re.compile(r"file:\{project-root\}/(_bmad/custom/[^\"\s]+)")

needs_toml = unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11")


def load(path: Path) -> dict:
    import tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))


def target_sections(skill: str) -> dict:
    """Sections and key types the target skill offers: stock 6.12.4 skill or one of ours."""
    if skill in STOCK:
        return STOCK[skill]
    own = MODULE_SKILLS / skill / "customize.toml"
    if own.is_file():
        data = load(own)
        return {sec: {k: type(v).__name__ for k, v in body.items()} for sec, body in data.items()}
    return {}


class OverrideTests(unittest.TestCase):
    def test_fixture_is_for_the_pinned_version(self):
        self.assertEqual(FIXTURE["bmad_version"], "6.12.4")
        self.assertIn("bmad-build", STOCK)

    def test_there_are_overrides(self):
        self.assertGreaterEqual(len(TOML_FILES), 9)

    def test_no_override_for_skills_removed_in_6_12(self):
        names = {p.stem for p in TOML_FILES}
        self.assertEqual(names & REMOVED_IN_6_12, set())

    @needs_toml
    def test_every_override_targets_a_real_skill_section_and_keys(self):
        for path in TOML_FILES:
            with self.subTest(override=path.name):
                offered = target_sections(path.stem)
                self.assertTrue(offered, f"no stock 6.12.4 skill or module skill named {path.stem}")
                for section, body in load(path).items():
                    self.assertIn(section, offered)
                    for key in body:
                        self.assertIn(key, offered[section], f"{path.stem} has no [{section}] {key}")

    @needs_toml
    def test_value_types_fit(self):
        for path in TOML_FILES:
            offered = target_sections(path.stem)
            for section, body in load(path).items():
                for key, value in body.items():
                    with self.subTest(override=path.name, key=key):
                        base = offered[section][key]
                        if key == "on_complete" and isinstance(value, list):
                            self.assertIn(path.stem, ON_COMPLETE_LIST_OK, "on_complete list not documented here")
                        elif isinstance(value, list):
                            self.assertEqual(base, "list")
                        elif isinstance(value, dict):
                            self.assertEqual(base, "dict")

    def test_file_references_point_to_shipped_files(self):
        for path in TOML_FILES:
            for ref in FILE_REF.findall(path.read_text(encoding="utf-8")):
                if "*" in ref:
                    continue  # a glob for project-owned files (the extension point), not shipped by the module
                with self.subTest(override=path.name, ref=ref):
                    shipped = OVERRIDES / Path(ref).relative_to("_bmad/custom")
                    self.assertTrue(shipped.is_file(), f"{ref} is not shipped in module/overrides")

    def test_every_workflow_override_has_a_project_extension_point(self):
        for path in TOML_FILES:
            text = path.read_text(encoding="utf-8")
            if "[workflow]" in text:
                with self.subTest(override=path.name):
                    self.assertIn(f"file:{{project-root}}/_bmad/custom/project/{path.stem}/*.md", text)

    def test_no_project_specific_content(self):
        for path in OVERRIDES.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                for word in PROJECT_SPECIFIC:
                    with self.subTest(file=str(path.relative_to(ROOT)), word=word):
                        self.assertNotIn(word, text)

    @needs_toml
    def test_bmad_build_runs_the_write_gate_check_before_coding(self):
        facts = load(OVERRIDES / "bmad-build.toml")["workflow"]["persistent_facts"]
        gate = [f for f in facts if f.startswith("Write gate (Guarded BMAD)")]
        self.assertEqual(len(gate), 1)
        self.assertIn('tool-guard/scripts/write_gate.py" --check', gate[0])
        self.assertTrue((MODULE_SKILLS / "tool-guard/scripts/write_gate.py").is_file())
        # it comes after the approval and Sentry step and before the scope rule
        order = [f.split(" (Guarded BMAD")[0] for f in facts if "(Guarded BMAD" in f]
        self.assertLess(order.index("Approval and gate"), order.index("Write gate"))
        self.assertLess(order.index("Write gate"), order.index("Scope"))
        for name in ("Ready-for-dev gate", "Plan-first hard gate", "Plan confidence", "Lifecycle log"):
            self.assertIn(name, order)

    @needs_toml
    def test_approval_uses_its_own_field(self):
        for name in ("agent-approval-gate-check", "agent-approval-grant"):
            self.assertEqual(load(OVERRIDES / f"{name}.toml")["agent"]["status_field"], "approval_status")

    def test_overrides_say_where_to_change_them(self):
        for path in TOML_FILES:
            with self.subTest(override=path.name):
                self.assertIn("bmad-guard team override", path.read_text(encoding="utf-8").splitlines()[0])


if __name__ == "__main__":
    unittest.main()
