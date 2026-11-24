# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for setup_guard.py (T2 spike)."""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "setup_guard.py"
MODULE_DIR = HERE.parent / "test-module"

spec = importlib.util.spec_from_file_location("setup_guard", SCRIPT)
setup_guard = importlib.util.module_from_spec(spec)
sys.modules["setup_guard"] = setup_guard
spec.loader.exec_module(setup_guard)


def make_project(root: Path, *, git: bool = True, bmad: bool = True, tools=(".claude/skills", ".agents/skills")) -> Path:
    if git:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    if bmad:
        (root / "_bmad").mkdir()
    for rel in tools:
        (root / rel).mkdir(parents=True)
    return root


class InstallTests(unittest.TestCase):
    def test_copies_skill_into_each_tool_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp))
            setup_guard.install(MODULE_DIR, project)
            for rel in (".claude/skills", ".agents/skills"):
                self.assertTrue((project / rel / "guard-hello" / "SKILL.md").is_file())

    def test_skips_tool_folder_that_does_not_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), tools=(".claude/skills",))
            written = setup_guard.install(MODULE_DIR, project)
            self.assertFalse((project / ".agents").exists())
            self.assertIn(".claude/skills/guard-hello", written)

    def test_sets_hooks_path_and_hook_is_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp))
            setup_guard.install(MODULE_DIR, project)
            value = subprocess.run(
                ["git", "config", "core.hooksPath"], cwd=project, capture_output=True, text=True, check=True
            ).stdout.strip()
            self.assertEqual(value, setup_guard.HOOKS_REL)
            hook = project / setup_guard.HOOKS_REL / "pre-commit"
            self.assertTrue(hook.stat().st_mode & 0o111)

    def test_reinstall_replaces_old_skill_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp))
            stale = project / ".claude/skills/guard-hello/stale.md"
            stale.parent.mkdir(parents=True)
            stale.write_text("old")
            setup_guard.install(MODULE_DIR, project)
            self.assertFalse(stale.exists())

    def test_refuses_non_git_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), git=False)
            with self.assertRaises(SystemExit):
                setup_guard.install(MODULE_DIR, project)

    def test_refuses_project_without_bmad(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), bmad=False)
            with self.assertRaises(SystemExit):
                setup_guard.install(MODULE_DIR, project)


if __name__ == "__main__":
    unittest.main()
