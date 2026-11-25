# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for the pure helpers in scripts/install_test.py. Fake data; no network, no npx.

The end-to-end run itself needs network and npx: `python3 scripts/install_test.py` (see scripts/README.md).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_test", ROOT / "scripts" / "install_test.py")
it = importlib.util.module_from_spec(spec)
sys.modules["install_test"] = it
spec.loader.exec_module(it)

GUARDS = {"agent-approval-grant", "ready-for-dev-gate"}


class GeneratedFileTests(unittest.TestCase):
    def test_generated_files(self):
        for path in (
            "_bmad/config.toml",
            "_bmad/core/config.yaml",
            "_bmad/bmm/config.yaml",
            "_bmad/_config/manifest.yaml",
            "_bmad/_config/nested/files-manifest.csv",
        ):
            with self.subTest(path=path):
                self.assertTrue(it.is_generated(path))

    def test_not_generated(self):
        for path in (
            "_bmad/bmm/agents/pm.md",
            "_bmad/config.toml.bak",
            "_bmad/_configx/manifest.yaml",
            "x/_bmad/config.toml",
            ".claude/skills/bmad-prd/SKILL.md",
        ):
            with self.subTest(path=path):
                self.assertFalse(it.is_generated(path))


class ClassifyTests(unittest.TestCase):
    def test_guard_skill_folders_in_every_tool_dir(self):
        self.assertEqual(it.classify(".claude/skills/agent-approval-grant/SKILL.md", GUARDS), "guard")
        self.assertEqual(it.classify(".agents/skills/ready-for-dev-gate/scripts/ready_for_dev.py", GUARDS), "guard")

    def test_custom_folder_is_guard(self):
        self.assertEqual(it.classify("_bmad/custom/bmad-prd.toml", GUARDS), "guard")
        self.assertEqual(it.classify("_bmad/custom/guard/manifest.json", GUARDS), "guard")

    def test_stock_skill_and_similar_names_are_stock(self):
        self.assertEqual(it.classify(".claude/skills/bmad-prd/SKILL.md", GUARDS), "stock")
        self.assertEqual(it.classify(".claude/skills/agent-approval-grant-old/SKILL.md", GUARDS), "stock")
        self.assertEqual(it.classify(".github/agents/bmad-agent-pm.agent.md", GUARDS), "stock")
        self.assertEqual(it.classify("_bmad/customx/a.toml", GUARDS), "stock")

    def test_generated_wins(self):
        self.assertEqual(it.classify("_bmad/_config/manifest.yaml", GUARDS), "generated")

    def test_group_changes_sorts_and_dedupes(self):
        groups = it.group_changes(
            [
                "_bmad/core/config.yaml",
                ".claude/skills/bmad-prd/a.md",
                "_bmad/_config/m.yaml",
                "_bmad/core/config.yaml",
            ],
            GUARDS,
        )
        self.assertEqual(
            groups,
            {
                "generated": ["_bmad/_config/m.yaml", "_bmad/core/config.yaml"],
                "guard": [],
                "stock": [".claude/skills/bmad-prd/a.md"],
            },
        )

    def test_group_changes_empty(self):
        self.assertEqual(it.group_changes([], GUARDS), {"generated": [], "guard": [], "stock": []})

    def test_split_z(self):
        self.assertEqual(it.split_z("a b.md\0_bmad/x\0"), ["a b.md", "_bmad/x"])
        self.assertEqual(it.split_z(""), [])


class ResolverJsonTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(it.parse_resolver_json('{"workflow": {"a": 1}}\n'), {"workflow": {"a": 1}})

    def test_notes_before_and_after(self):
        out = 'note: using cached env\n{\n  "agent": {"name": "Warden"}\n}\ndone\n'
        self.assertEqual(it.parse_resolver_json(out), {"agent": {"name": "Warden"}})

    def test_no_json(self):
        with self.assertRaises(ValueError):
            it.parse_resolver_json("error: skill not found\n")
        with self.assertRaises(ValueError):
            it.parse_resolver_json("")

    def test_broken_json(self):
        with self.assertRaises(ValueError):
            it.parse_resolver_json('{"workflow": ')


class MissingKeysTests(unittest.TestCase):
    def test_all_present_with_appended_list_and_extra_keys(self):
        expected = {"persistent_facts": ["ours"], "status_field": "approval_status", "map": {"prd": "bmad-prd"}}
        resolved = {
            "persistent_facts": ["stock", "ours"],
            "status_field": "approval_status",
            "map": {"prd": "bmad-prd", "brief": "x"},
            "name": "Warden",
        }
        self.assertEqual(it.missing_keys(expected, resolved, "agent"), [])

    def test_reports_each_gap(self):
        expected = {"facts": ["a", "b"], "field": "approval_status", "map": {"prd": "bmad-prd"}, "gone": 1}
        resolved = {"facts": ["a"], "field": "status", "map": {"prd": "other"}}
        self.assertEqual(
            it.missing_keys(expected, resolved, "workflow"),
            ["workflow.facts[1]", "workflow.field", "workflow.map.prd", "workflow.gone"],
        )

    def test_wrong_types(self):
        self.assertEqual(it.missing_keys({"a": 1}, None, "agent"), ["agent"])
        self.assertEqual(it.missing_keys({"a": ["x"]}, {"a": "x"}, "w"), ["w.a"])
        self.assertEqual(it.missing_keys({"a": 1}, None), ["<root>"])


class ReportTests(unittest.TestCase):
    CHECKS = [
        it.Check("Fresh", "install BMAD", True, "", 12.34),
        it.Check("Fresh", "only generated files changed", False, "a|b.md\nnext"),
        it.Check("Upgrade", "overrides resolve", True, "3 key(s) present"),
    ]

    def test_summary(self):
        self.assertEqual(it.summary(self.CHECKS), (2, 1))
        self.assertEqual(it.summary([]), (0, 0))

    def test_failed_report(self):
        text = it.render_report(self.CHECKS, {"BMAD": "6.12.4"})
        self.assertIn("Result: **FAIL**: 2 passed, 1 failed.", text)
        self.assertIn("| BMAD | 6.12.4 |", text)
        self.assertEqual(text.count("## Fresh"), 1)
        self.assertIn("## Upgrade", text)
        self.assertIn("| install BMAD | PASS | 12.3s |  |", text)
        self.assertIn("| only generated files changed | FAIL |  | a\\|b.md next |", text)
        self.assertLess(text.index("## Fresh"), text.index("## Upgrade"))

    def test_passed_report(self):
        self.assertIn("Result: **PASS**: 1 passed, 0 failed.", it.render_report(self.CHECKS[:1], {}))

    def test_short_list(self):
        self.assertEqual(it.short_list(["a", "b"]), "a, b")
        self.assertEqual(it.short_list(["a", "b", "c"], 2), "a, b (+1 more)")
        self.assertEqual(it.short_list([]), "")


class ScriptCoverageTests(unittest.TestCase):
    def test_every_guard_script_has_help_or_a_smoke_call(self):
        """A new script without argparse needs an entry in NO_ARGPARSE_SMOKE, or the e2e run fails."""
        for script in sorted((ROOT / "module/skills").glob("*/scripts/*.py")):
            with self.subTest(script=script.name):
                has_help = "ArgumentParser" in script.read_text(encoding="utf-8")
                self.assertTrue(has_help or script.name in it.NO_ARGPARSE_SMOKE)


class MainTests(unittest.TestCase):
    def test_missing_tool_exits_2_without_creating_sandboxes(self):
        err = io.StringIO()
        with (
            mock.patch.object(it.shutil, "which", return_value=None),
            mock.patch.object(it.tempfile, "mkdtemp") as mkdtemp,
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(it.main([]), 2)
        mkdtemp.assert_not_called()
        self.assertIn("missing tool(s): npx, uv, git", err.getvalue())


if __name__ == "__main__":
    unittest.main()
