# /// script
# requires-python = ">=3.10"
# ///
"""Checks for Sloane (module/skills/experion-story-slicer): the story and technical summary templates fit the
coverage script and the ready-for-dev gate, the skill text carries the rules of ADR 0010, and the team override wires
Sentry, the logger and the coverage run. All data is fake."""

from __future__ import annotations

import importlib
import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

import tests.guard_harness  # noqa: F401 - puts the guard scripts on sys.path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "module/skills/experion-story-slicer"
TEMPLATE = SKILL / "assets/story-template.md"
SUMMARY_TEMPLATE = SKILL / "assets/technical-summary-template.md"
REFERENCE = SKILL / "references/slice-stories.md"
OVERRIDE = ROOT / "module/overrides/experion-story-slicer.toml"
FIXTURE = ROOT / "tests/fixtures/frd-clinic-f001.md"

needs_toml = unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11")
dt = importlib.import_module("document_text")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sc = load("story_coverage_for_slicer_tests", SKILL / "scripts/story_coverage.py")
rfd = load("ready_for_dev_for_slicer_tests", ROOT / "module/skills/ready-for-dev-gate/scripts/ready_for_dev.py")


def toml(path: Path) -> dict:
    import tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))


def fill_story(text: str, values: dict[str, str]) -> str:
    """The template as a model should leave it: the guidance section removed, each <placeholder> replaced.

    `values` maps the start of a placeholder's text to its fake value; a placeholder nobody mapped fails the test.
    """
    text = text.split("\n## How to use this template")[0].rstrip() + "\n"

    def replace(match: re.Match) -> str:
        for start, value in values.items():
            if match.group(1).startswith(start):
                return value
        raise AssertionError(f"no value for the placeholder {match.group(0)}")

    return re.sub(r"<([^<>]*)>", replace, text)


def feature_values(story_id: str, requirements: str, depends: str) -> dict[str, str]:
    """Fake values for every placeholder of the feature story template."""
    return {
        "F001-02": story_id,
        "F-001": "F-001",
        "FR-F001-02": requirements,
        "F001-01": depends,
        "FRD-ABC": "FRD-CLINIC-F001",
        "4.2": "4.2",
        "0-100": "85",
        "Low |": "High",
        "1-2 sentences": "The FR maps to one story and the architecture names the module.",
        "story id": story_id,
        "Story title": "List free slots",
        "Pointers": "- Route module: see the architecture, section 'API layer'.",
    }


class StoryTemplateTests(unittest.TestCase):
    def setUp(self):
        self.text = TEMPLATE.read_text(encoding="utf-8")
        self.fields, self.body, self.error = rfd.parse_frontmatter(self.text)

    def test_front_matter_has_the_keys_the_check_and_the_gate_read(self):
        self.assertIsNone(self.error)
        for key in (
            "story",
            "feature",
            "requirements",
            "depends_on",
            "epic_summary",
            "confidence",
            "confidence_label",
            "confidence_rationale",
        ):
            self.assertIn(key, self.fields)

    def test_lists_are_one_line_in_square_brackets(self):
        front = self.text.split("---")[1]
        for key in ("requirements", "depends_on"):
            self.assertRegex(front, rf"(?m)^{key}: \[[^\]\n]*\]$")
        self.assertNotRegex(front, r"(?m)^\s+- ")  # no YAML block lists

    def test_the_story_has_no_functional_sections_and_no_approval(self):
        headings = re.findall(r"(?m)^#{1,6} (.+)$", self.text.split("\n## How to use")[0])
        self.assertEqual(headings[1:], ["Technical notes", "Open questions"])
        self.assertNotRegex(self.text, r"(?im)^#+ .*(acceptance|criteria|requirement)")
        for word in ("approval_status", "approved_by", "approved_at", "gate: generic"):
            self.assertNotIn(word, self.text.split("\n## How to use")[0])

    def test_open_questions_start_as_none(self):
        part = self.text.split("## Open questions")[1].split("## How to use")[0]
        self.assertEqual(part.strip(), "- None.")

    def test_the_note_names_the_technical_story_changes(self):
        for must in (
            "T-<nn>",
            "feature: none",
            "gate: technical",
            "requirements",
            "epic_summary",
            "stories/technical/",
        ):
            self.assertIn(must, self.text.split("\n## How to use")[1])

    def test_the_template_does_not_trip_the_document_guard(self):
        for path in (TEMPLATE, SUMMARY_TEMPLATE, REFERENCE, SKILL / "SKILL.md"):
            with self.subTest(path=path.name):
                self.assertEqual(dt.compare("", path.read_text(encoding="utf-8")), [])
        self.assertEqual(dt.open_items(self.text)[1], 0)


class FilledTemplateTests(unittest.TestCase):
    """Stories written from the template, with fake values, against the clinic FRD fixture."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.stories, self.frds = root / "stories", root / "frds"
        (self.frds / "F-001").mkdir(parents=True)
        (self.frds / "F-001/frd.md").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        (self.stories / "F-001").mkdir(parents=True)
        (self.stories / "technical").mkdir()
        self.template = TEMPLATE.read_text(encoding="utf-8")

    def feature_story(self, story_id: str, requirements: str, depends: str, name: str) -> str:
        """`requirements` and `depends` are the list contents; depends "none" writes `depends_on: none`."""
        text = self.template.replace("[<F001-01>]", "none") if depends == "none" else self.template
        text = fill_story(text, feature_values(story_id, requirements, depends))
        (self.stories / "F-001" / f"{story_id}-{name}.md").write_text(text, encoding="utf-8")
        return text

    def technical_story(self) -> str:
        """The template with the changes its note lists for a technical story."""
        text = re.sub(r"(?m)^(requirements|epic_summary): .*\n", "", self.template)
        text = text.replace("confidence: <0-100>", "gate: technical\nconfidence: <0-100>")
        text = text.replace("[<F001-01>]", "none")
        text = text.replace(
            "Delivers <FR-F001-02> of <FRD-ABC-F001>. Acceptance criteria: the FRD, Section <4.2>.",
            "Delivers <what>. Source: the approved architecture, section 'Starter'.",
        )
        values = {
            **feature_values("T-01", "", ""),
            "story id": "T-01",
            "F-001": "none",
            "what": "the starter the architecture asks for",
            "Pointers": "- Use the starter the architecture names.",
        }
        text = fill_story(text, values)
        (self.stories / "technical/T-01-set-up-the-project.md").write_text(text, encoding="utf-8")
        return text

    def summary(self) -> str:
        values = {
            "F-001": "F-001",
            "Feature name": "List free slots",
            "architecture file name": "ARCHITECTURE-SPINE.md",
            "x": "1",
            "the languages": "the stack the architecture sets",
            "the API": "the API style the architecture sets",
            "storage": "the storage the architecture names",
            "security": "the rules the architecture sets",
        }
        text = fill_story(SUMMARY_TEMPLATE.read_text(encoding="utf-8"), values)
        (self.stories / "F-001/technical-summary.md").write_text(text, encoding="utf-8")
        return text

    def order(self, *lines: str) -> None:
        (self.stories / "stories.md").write_text("# Build order\n\n" + "\n".join(lines) + "\n", encoding="utf-8")

    def test_stories_from_the_template_pass_the_coverage_check(self):
        self.technical_story()
        self.summary()
        self.feature_story("F001-01", "FR-F001-01", "T-01", "view-free-slots")
        self.feature_story("F001-02", "FR-F001-02", "F001-01", "list-free-slots-through-the-api")
        self.order(
            "- [T-01](technical/T-01-set-up-the-project.md) — technical",
            "- [F001-01](F-001/F001-01-view-free-slots.md) — FR-F001-01",
            "- [F001-02](F-001/F001-02-list-free-slots-through-the-api.md) — FR-F001-02",
        )
        result = sc.evaluate(self.stories, self.frds)
        self.assertEqual((result["result"], result["reasons"]), ("allow", []))
        self.assertEqual(
            result["counts"], {"features": 1, "stories": 2, "technical_stories": 1, "frs": 2, "frs_covered": 2}
        )

    def test_a_merged_story_and_a_split_form_from_the_note_are_accepted(self):
        self.summary()
        text = self.feature_story("F001-01", "FR-F001-01, FR-F001-02", "none", "both-slot-lists")
        text = text.replace("Section 4.2.", "Section 4.1 and 4.2.\n\nMerged: both lists share one query.")
        (self.stories / "F-001/F001-01-both-slot-lists.md").write_text(text, encoding="utf-8")
        self.order("- [F001-01](F-001/F001-01-both-slot-lists.md) — FR-F001-01, FR-F001-02")
        self.assertEqual(sc.evaluate(self.stories, self.frds)["reasons"], [])
        self.assertEqual(sc.id_list("[FR-F001-04#1-4]"), ["FR-F001-04#1-4"])

    def test_a_split_fr_written_as_the_note_says_is_covered_once(self):
        self.summary()
        for story_id, requirements, name in (
            ("F001-01", "FR-F001-01#1-6", "view-first-part"),
            ("F001-02", "FR-F001-01#7-13", "view-second-part"),
            ("F001-03", "FR-F001-02", "list-through-the-api"),
        ):
            text = self.feature_story(story_id, requirements, "none", name)
            self.assertIn(f"requirements: [{requirements}]", text)
        self.order(
            "- [F001-01](F-001/F001-01-view-first-part.md) — FR-F001-01#1-6",
            "- [F001-02](F-001/F001-02-view-second-part.md) — FR-F001-01#7-13",
            "- [F001-03](F-001/F001-03-list-through-the-api.md) — FR-F001-02",
        )
        result = sc.evaluate(self.stories, self.frds)
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["counts"]["frs_covered"], 2)

    def test_a_story_with_copied_frd_text_is_blocked(self):
        self.summary()
        frd = sc.load_frd_parse().parse_frd(FIXTURE.read_text(encoding="utf-8"))
        copied = frd["blocks"][0]["criteria"][0]["text"].split(" [ASSUMPTION")[0]
        text = self.feature_story("F001-01", "FR-F001-01, FR-F001-02", "none", "both")
        path = self.stories / "F-001/F001-01-both.md"
        path.write_text(text + "\n" + copied + "\n", encoding="utf-8")
        self.order("- [F001-01](F-001/F001-01-both.md) — FR-F001-01, FR-F001-02")
        codes = [r["code"] for r in sc.evaluate(self.stories, self.frds)["reasons"]]
        self.assertIn("STORY_FRD_TEXT", codes)

    def test_the_filled_stories_do_not_trip_the_document_guard(self):
        technical = self.technical_story()
        feature = self.feature_story("F001-01", "FR-F001-01", "T-01", "view-free-slots")
        for text in (technical, feature, self.summary()):
            self.assertEqual(dt.compare("", text), [])

    def test_the_technical_summary_has_the_part_the_gate_wants(self):
        self.summary()
        path = self.stories / "F-001/technical-summary.md"
        self.assertEqual(rfd.check_epic_summary(path, has_frd=True), [])
        self.assertEqual(rfd.epic_summary_missing_parts(path.read_text(encoding="utf-8"), ("technical",)), [])
        self.assertEqual(path.name, toml(SKILL / "customize.toml")["agent"]["technical_summary_filename"])

    def test_a_feature_story_points_to_its_summary_where_the_gate_looks(self):
        self.summary()
        text = self.feature_story("F001-01", "[FR-F001-01]", "none", "view-free-slots")
        fields, _, _ = rfd.parse_frontmatter(text)
        self.assertEqual(fields["epic_summary"], "_bmad-output/planning-artifacts/stories/F-001/technical-summary.md")

    def test_an_unfilled_technical_part_is_what_the_gate_rejects(self):
        path = self.stories / "F-001/technical-summary.md"
        path.write_text("# F-001 summary\n\n## Technical\n\n", encoding="utf-8")
        self.assertEqual(rfd.check_epic_summary(path, has_frd=True)[0]["code"], "EPIC_SUMMARY_INCOMPLETE")


class SummaryTemplateTests(unittest.TestCase):
    def test_the_title_line_is_not_read_as_a_summary_part(self):
        text = SUMMARY_TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(rfd.epic_summary_missing_parts(text.replace("<F-001>", "F-001"), ("technical",)), [])
        headings = re.findall(r"(?m)^#{2,6} (.+)$", text.split("\n## How to use")[0])
        self.assertEqual(headings, ["Technical"])  # no sub-headings: the gate stops a part at the next heading

    def test_it_is_written_from_the_architecture_only(self):
        text = SUMMARY_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("approved architecture only", text)
        self.assertNotIn("## Functional", text)


class SkillTextTests(unittest.TestCase):
    def setUp(self):
        self.skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.reference = REFERENCE.read_text(encoding="utf-8")

    def test_every_capability_route_exists(self):
        routes = re.findall(r"Load `(references/[^`]+)`", self.skill)
        self.assertEqual(routes, ["references/slice-stories.md"])
        for route in routes:
            self.assertTrue((SKILL / route).is_file())

    def test_the_skill_forbids_copying_the_frd_and_keeps_functional_text_in_it(self):
        self.assertIn("The FRD is the only place functional text lives", self.skill)
        self.assertIn("no copied FRD lines", self.skill)
        self.assertIn("no acceptance criteria", self.skill)
        self.assertIn("Never write functional text in a story", self.reference)
        self.assertIn("STORY_FRD_TEXT", self.reference)

    def test_the_skill_never_writes_approval_resolutions_or_generic(self):
        self.assertIn("never writes `approval_status`", self.skill)
        self.assertIn("Never write `approval_status`, `approved_by`, `approved_at`, a `Resolved:` line", self.reference)
        self.assertIn("`gate: generic`", self.reference)
        self.assertIn("Never raise a confidence score", self.reference)
        for path in SKILL.rglob("*.md"):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"(?i)set .{0,40}approval_status.{0,10} to .?approved")

    def test_slicing_rules_of_the_plan_are_in_the_flow(self):
        for must in (
            "1 FR = 1 story",
            "one-line reason",
            "FR-F001-04#1-4",
            "`feature: none`, `gate: technical`",
            "reopened with Fenn",
            "Never renumber or reuse a story id",
            "agent-approval-gate-check with doc_type='frd-<feature>'",
            "frd_parse.py",
            "story_coverage.py",
            "STORY_FR_UNCOVERED",
            '--stage "stories"',
            "experion-story-slicer/SS",
        ):
            self.assertIn(must, self.reference + self.skill)

    def test_a_functional_question_is_not_a_story_question(self):
        self.assertIn("A new functional question found while slicing is not a story question", self.skill)
        self.assertIn("never a story question", self.reference)

    def test_the_commands_in_the_flow_use_flags_the_scripts_have(self):
        for flag in ("--stories-dir", "--frds-dir", "--feature", "--no-event"):
            self.assertIn(flag, self.reference)
            self.assertIn(flag, (SKILL / "scripts/story_coverage.py").read_text(encoding="utf-8"))
        self.assertIn("{skill-root}/../ready-for-dev-gate/scripts/frd_parse.py", self.reference)
        self.assertTrue((SKILL / "../ready-for-dev-gate/scripts/frd_parse.py").resolve().is_file())

    def test_the_order_example_is_read_by_the_script(self):
        block = re.search(r"```markdown\n(# Build order.*?)```", self.reference, re.DOTALL).group(1)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / sc.ORDER_FILE).write_text(block, encoding="utf-8")
            self.assertEqual(sc.read_order(Path(tmp)), ["T-01", "F001-01", "F001-02"])

    def test_the_flow_names_the_withdrawn_story_open_point(self):
        self.assertIn('no "withdrawn story" mark', self.reference)


class CustomizeTests(unittest.TestCase):
    @needs_toml
    def test_defaults_match_the_script_the_gate_and_the_other_skills(self):
        agent = toml(SKILL / "customize.toml")["agent"]
        gate = toml(ROOT / "module/skills/ready-for-dev-gate/customize.toml")["agent"]
        frd = toml(ROOT / "module/skills/experion-frd/customize.toml")["agent"]
        self.assertEqual(agent["frds_dir"], gate["frds_dir"])
        self.assertEqual(agent["frds_dir"], frd["frds_dir"])
        self.assertTrue(agent["stories_dir"].endswith("/" + sc.DEFAULT_STORIES_DIR))
        self.assertTrue(agent["frds_dir"].endswith("/" + sc.DEFAULT_FRDS_DIR))
        self.assertEqual(agent["order_filename"], sc.ORDER_FILE)
        self.assertEqual(agent["technical_summary_filename"], sc.SUMMARY_NAME)
        self.assertEqual(agent["code"], "experion-story-slicer")
        self.assertEqual(agent["name"], "Sloane")

    @needs_toml
    def test_templates_exist_and_the_team_template_has_its_own_path(self):
        agent = toml(SKILL / "customize.toml")["agent"]
        self.assertTrue((SKILL / agent["story_template"]).is_file())
        self.assertTrue((SKILL / agent["technical_summary_template"]).is_file())
        self.assertTrue(agent["project_template"].endswith("_bmad/custom/templates/story-template.md"))
        for key in ("activation_steps_prepend", "activation_steps_append", "persistent_facts", "on_complete"):
            self.assertIsInstance(agent[key], list)


class OverrideTests(unittest.TestCase):
    @needs_toml
    def test_sentry_checks_the_architecture_and_the_logger_starts(self):
        agent = toml(OVERRIDE)["agent"]
        prepend = agent["activation_steps_prepend"]
        self.assertIn("skill:agent-approval-gate-check with doc_type='architecture'", prepend[0])
        self.assertIn("downstream_label='story slicing'", prepend[0])
        self.assertIn("event='STORIES_STARTED', stage='stories'", prepend[1])

    @needs_toml
    def test_on_complete_logs_scores_and_runs_the_coverage_check(self):
        steps = toml(OVERRIDE)["agent"]["on_complete"]
        self.assertIn("event='STORIES_SLICED', stage='stories'", steps[0])
        self.assertIn("event='CONFIDENCE_SCORED', stage='stories'", steps[1])
        self.assertIn("confidence=", steps[1])
        self.assertIn("story_coverage.py", steps[2])
        self.assertIn("stop", steps[2])
        self.assertIn("'block'", steps[2])

    @needs_toml
    def test_no_warden_step_and_the_facts_load_the_shared_files(self):
        agent = toml(OVERRIDE)["agent"]
        self.assertNotIn("agent-approval-grant", " ".join(agent["on_complete"] + agent["activation_steps_prepend"]))
        facts = agent["persistent_facts"]
        self.assertIn("file:{project-root}/_bmad/custom/guard/references/confidence-scoring.md", facts)
        self.assertIn("file:{project-root}/_bmad/custom/project/experion-story-slicer/*.md", facts)
        joined = " ".join(facts)
        self.assertIn("'frd-<feature>'", joined)
        self.assertIn("'architecture'", joined)
        self.assertTrue((ROOT / "module/overrides/guard/references/confidence-scoring.md").is_file())


if __name__ == "__main__":
    unittest.main()
