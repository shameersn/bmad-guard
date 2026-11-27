# /// script
# requires-python = ">=3.10"
# ///
"""Checks for the thin-story team overrides (ADR 0010, T5): the build and code-review overrides fetch the story's
requirements from the FRD with frd_parse.py, and the epics-and-stories override redirects to the slicer and stops.
The command in the instruction is run once, exactly as the override words it. All data is fake."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / "module/overrides"
SKILLS = ROOT / "module/skills"
FIXTURE = ROOT / "tests/fixtures/frd-clinic-f001.md"
COMMAND = re.compile(r'`(python3 "\{project-root\}/\.claude/skills/ready-for-dev-gate/scripts/frd_parse\.py" [^`]+)`')

needs_toml = unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11")


def facts(name: str) -> list[str]:
    import tomllib

    return tomllib.loads((OVERRIDES / f"{name}.toml").read_text(encoding="utf-8"))["workflow"]["persistent_facts"]


def fact(name: str, start: str) -> str:
    found = [f for f in facts(name) if f.startswith(start)]
    assert len(found) == 1, (name, start, len(found))
    return found[0]


class BuildOverrideTests(unittest.TestCase):
    @needs_toml
    def test_build_reads_requirements_from_the_frd(self):
        text = fact("bmad-build", "Requirements from the FRD")
        for word in ("frd_parse.py", "--ids", "--frd", "requirements", "Section 4", "Section 5", "Section 8.1"):
            self.assertIn(word, text)
        self.assertIn("never invent acceptance criteria", text.lower())
        self.assertIn(".agents/skills", text)

    @needs_toml
    def test_build_names_the_technical_story_path_and_the_old_path(self):
        text = fact("bmad-build", "Requirements from the FRD")
        self.assertIn("gate: technical", text)
        self.assertIn("approved architecture", text)
        self.assertIn("without `requirements`", text)

    @needs_toml
    def test_build_fact_order_and_old_facts_stay(self):
        order = [f.split(" (Guarded BMAD")[0] for f in facts("bmad-build") if "(Guarded BMAD" in f]
        for name in (
            "Ready-for-dev gate",
            "Feature FRD",
            "Requirements from the FRD",
            "Plan-first hard gate",
            "Plan confidence",
            "Approval and gate",
            "Write gate",
            "Scope",
            "Lifecycle log",
        ):
            self.assertIn(name, order)
        self.assertEqual(order.index("Feature FRD") + 1, order.index("Requirements from the FRD"))
        self.assertLess(order.index("Requirements from the FRD"), order.index("Plan-first hard gate"))

    @needs_toml
    def test_plan_confidence_counts_the_frd_blocks_as_confirmed_input(self):
        text = fact("bmad-build", "Plan confidence")
        self.assertIn("frd_parse.py", text)
        self.assertIn("confirmed input", text)

    @needs_toml
    def test_the_command_in_the_instruction_runs_and_returns_ok(self):
        text = fact("bmad-build", "Requirements from the FRD")
        match = COMMAND.search(text)
        self.assertIsNotNone(match, "the override no longer words the frd_parse.py command as this test expects")
        command = match.group(1)
        self.assertTrue(command.endswith("--frd <the FRD path above> --ids <the requirements value>"))
        with tempfile.TemporaryDirectory() as tmp:
            # a project whose .claude/skills holds the module skills, as setup_guard installs them
            (Path(tmp) / ".claude").mkdir()
            (Path(tmp) / ".claude/skills").symlink_to(SKILLS, target_is_directory=True)
            command = command.replace("{project-root}", tmp)
            command = command.replace("<the FRD path above>", str(FIXTURE))
            command = command.replace("<the requirements value>", "FR-F001-02")
            argv = shlex.split(command)
            argv[0] = sys.executable
            proc = subprocess.run(argv, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["result"], "ok")
        self.assertEqual([b["id"] for b in out["blocks"]], ["FR-F001-02"])
        self.assertTrue(out["blocks"][0]["criteria"])


class CodeReviewOverrideTests(unittest.TestCase):
    @needs_toml
    def test_review_checks_the_diff_against_the_frd(self):
        text = fact("bmad-code-review", "FRD check")
        for word in ("frd_parse.py", "--ids", "criterion", "key transaction", "FR id", "is a finding"):
            self.assertIn(word, text)
        self.assertIn("gate: technical", text)

    @needs_toml
    def test_review_keeps_its_old_facts_and_record(self):
        order = [f.split(" (Guarded BMAD")[0] for f in facts("bmad-code-review") if "(Guarded BMAD" in f]
        for name in ("Independent review", "Severity levels", "Diff-to-plan check", "Review confidence"):
            self.assertIn(name, order)
        self.assertLess(order.index("Diff-to-plan check"), order.index("Review confidence"))
        import tomllib

        data = tomllib.loads((OVERRIDES / "bmad-code-review.toml").read_text(encoding="utf-8"))
        self.assertIn("review_record.py", data["workflow"]["on_complete"])


class EpicsOverrideTests(unittest.TestCase):
    @needs_toml
    def test_epics_skill_redirects_to_the_slicer_and_stops(self):
        import tomllib

        data = tomllib.loads((OVERRIDES / "bmad-create-epics-and-stories.toml").read_text(encoding="utf-8"))
        steps = data["workflow"]["activation_steps_prepend"]
        self.assertEqual(len(steps), 1)
        self.assertIn("experion-story-slicer", steps[0])
        self.assertIn("stop", steps[0].lower())
        self.assertIn("ADR 0010", steps[0])

    @needs_toml
    def test_epics_override_has_no_story_writing_facts_left(self):
        import tomllib

        data = tomllib.loads((OVERRIDES / "bmad-create-epics-and-stories.toml").read_text(encoding="utf-8"))
        self.assertNotIn("on_complete", data["workflow"])
        # the only fact left is the project extension point the override tests require
        self.assertEqual(
            data["workflow"]["persistent_facts"],
            ["file:{project-root}/_bmad/custom/project/bmad-create-epics-and-stories/*.md"],
        )
        text = (OVERRIDES / "bmad-create-epics-and-stories.toml").read_text(encoding="utf-8")
        for gone in ("Per-story file mirror", "FRDs first", "Story traceability", "confidence-scoring"):
            self.assertNotIn(gone, text)


if __name__ == "__main__":
    unittest.main()
