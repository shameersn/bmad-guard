# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for scripts/setup_guard.py. Fake module checkout and fake projects in temp folders."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_guard", ROOT / "scripts" / "setup_guard.py")
sg = importlib.util.module_from_spec(spec)
sys.modules["setup_guard"] = sg
spec.loader.exec_module(sg)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def make_module(root: Path, tag: str | None = "v0.1.0") -> Path:
    write(root / "module/skills/README.md", "folder readme, not shipped")
    write(root / "module/skills/guard-a/SKILL.md", "skill a")
    write(root / "module/skills/guard-a/scripts/run.py", "print('a')")
    write(root / "module/skills/guard-a/scripts/tests/test_run.py", "test, not shipped")
    write(root / "module/overrides/bmad-prd.toml", "[workflow]\n")
    write(root / "module/hooks/pre-commit", "#!/bin/sh\nexit 0\n")
    git(root, "init", "-q")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "m")
    if tag:
        git(root, "tag", tag)
    return root


def make_project(root: Path, tools=(".claude/skills", ".agents/skills")) -> Path:
    git(root, "init", "-q")
    (root / "_bmad/custom").mkdir(parents=True)
    for tool in tools:
        (root / tool).mkdir(parents=True)
    return root


def run(project: Path, module: Path, *flags: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = sg.main(["--project", str(project), "--module", str(module), *flags])
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        (tmp / "mod").mkdir()
        (tmp / "proj").mkdir()
        self.module = make_module(tmp / "mod")
        self.project = make_project(tmp / "proj")

    def tearDown(self):
        self._tmp.cleanup()

    def manifest(self) -> dict:
        return json.loads((self.project / sg.MANIFEST_REL).read_text())


class RefusalTests(unittest.TestCase):
    def test_refuses_non_git_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "m").mkdir()
            mod = make_module(Path(tmp) / "m")
            proj = Path(tmp) / "p"
            (proj / "_bmad").mkdir(parents=True)
            code, _, err = run(proj, mod)
            self.assertEqual(code, 2)
            self.assertIn("not a git repository", err)

    def test_refuses_project_without_bmad(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "m").mkdir()
            mod = make_module(Path(tmp) / "m")
            proj = Path(tmp) / "p"
            proj.mkdir()
            git(proj, "init", "-q")
            code, _, err = run(proj, mod)
            self.assertEqual(code, 2)
            self.assertIn("BMAD is not installed", err)

    def test_refuses_folder_that_is_not_a_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(Path(tmp))
            code, _, err = run(proj, Path(tmp) / "_bmad")
            self.assertEqual(code, 2)
            self.assertIn("not a bmad-guard checkout", err)


class InstallTests(Base):
    def test_fresh_install(self):
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 0, out)
        for tool in (".claude/skills", ".agents/skills"):
            self.assertEqual((self.project / tool / "guard-a/SKILL.md").read_text(), "skill a")
            self.assertTrue((self.project / tool / "guard-a/scripts/run.py").is_file())
        self.assertTrue((self.project / "_bmad/custom/bmad-prd.toml").is_file())
        hook = self.project / sg.HOOKS_REL / "pre-commit"
        self.assertTrue(hook.stat().st_mode & 0o111)
        self.assertEqual(git(self.project, "config", "core.hooksPath"), sg.HOOKS_REL)
        self.assertIn(".bmad-guard/", (self.project / ".gitignore").read_text())
        self.assertEqual(self.manifest()["version"], "v0.1.0")

    def test_tests_caches_and_folder_readmes_are_not_shipped(self):
        write(self.module / "module/skills/guard-a/scripts/__pycache__/run.cpython-313.pyc", "x")
        run(self.project, self.module)
        skill = self.project / ".claude/skills"
        self.assertFalse((skill / "README.md").exists())
        self.assertFalse((skill / "guard-a/scripts/tests").exists())
        self.assertFalse((skill / "guard-a/scripts/__pycache__").exists())

    def test_skips_tool_folder_the_project_does_not_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(Path(tmp), tools=(".claude/skills",))
            run(proj, self.module)
            self.assertFalse((proj / ".agents").exists())

    def test_second_run_changes_nothing_and_check_passes(self):
        run(self.project, self.module)
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 0)
        self.assertIn("0 change(s), 0 conflict(s)", out)
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("up to date", out)

    def test_check_fails_before_install(self):
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 1)
        self.assertIn("NOT up to date", out)
        self.assertFalse((self.project / sg.MANIFEST_REL).exists())

    def test_dry_run_changes_nothing(self):
        code, out, _ = run(self.project, self.module, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("create   .claude/skills/guard-a/SKILL.md", out)
        self.assertFalse((self.project / ".claude/skills/guard-a").exists())
        self.assertFalse((self.project / ".gitignore").exists())
        self.assertEqual(subprocess.run(["git", "config", "core.hooksPath"], cwd=self.project).returncode, 1)

    def test_gitignore_line_added_once_and_newline_kept(self):
        write(self.project / ".gitignore", "node_modules/")
        run(self.project, self.module)
        run(self.project, self.module)
        text = (self.project / ".gitignore").read_text()
        self.assertTrue(text.startswith("node_modules/\n"))
        self.assertEqual(text.count(".bmad-guard/"), 1)


class UpdateTests(Base):
    def commit_module(self, msg: str, tag: str | None = None):
        git(self.module, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
        git(self.module, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", msg)
        if tag:
            git(self.module, "tag", tag)

    def test_module_change_is_copied_and_version_updated(self):
        run(self.project, self.module)
        write(self.module / "module/skills/guard-a/SKILL.md", "skill a v2")
        self.commit_module("v2", "v0.2.0")
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 0, out)
        self.assertIn("update   .claude/skills/guard-a/SKILL.md", out)
        self.assertEqual((self.project / ".claude/skills/guard-a/SKILL.md").read_text(), "skill a v2")
        self.assertEqual(self.manifest()["version"], "v0.2.0")

    def test_local_edit_is_a_conflict_and_kept(self):
        run(self.project, self.module)
        write(self.project / "_bmad/custom/bmad-prd.toml", "[workflow]\n# team edit\n")
        write(self.module / "module/overrides/bmad-prd.toml", "[workflow]\n# v2\n")
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 1)
        self.assertIn("conflict _bmad/custom/bmad-prd.toml (changed since install)", out)
        self.assertIn("team edit", (self.project / "_bmad/custom/bmad-prd.toml").read_text())

    def test_force_overwrites_a_conflict(self):
        run(self.project, self.module)
        write(self.project / "_bmad/custom/bmad-prd.toml", "team edit")
        code, _, _ = run(self.project, self.module, "--force")
        self.assertEqual(code, 0)
        self.assertEqual((self.project / "_bmad/custom/bmad-prd.toml").read_text(), "[workflow]\n")

    def test_projects_own_file_with_same_name_is_a_conflict(self):
        write(self.project / "_bmad/custom/bmad-prd.toml", "project's own override")
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 1)
        self.assertIn("not installed by bmad-guard", out)
        self.assertEqual((self.project / "_bmad/custom/bmad-prd.toml").read_text(), "project's own override")

    def test_identical_existing_file_is_adopted(self):
        write(self.project / "_bmad/custom/bmad-prd.toml", "[workflow]\n")
        code, _, _ = run(self.project, self.module)
        self.assertEqual(code, 0)
        self.assertIn("_bmad/custom/bmad-prd.toml", self.manifest()["files"])

    def test_file_dropped_from_module_is_removed(self):
        run(self.project, self.module)
        (self.module / "module/skills/guard-a/scripts/run.py").unlink()
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 0, out)
        self.assertFalse((self.project / ".claude/skills/guard-a/scripts").exists())
        self.assertNotIn(".claude/skills/guard-a/scripts/run.py", self.manifest()["files"])

    def test_dropped_file_changed_locally_is_kept(self):
        run(self.project, self.module)
        write(self.project / ".claude/skills/guard-a/scripts/run.py", "local change")
        (self.module / "module/skills/guard-a/scripts/run.py").unlink()
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 1)
        self.assertIn("removed from the module but changed locally", out)
        self.assertTrue((self.project / ".claude/skills/guard-a/scripts/run.py").exists())


class HooksPathTests(Base):
    def test_existing_hooks_path_is_a_conflict(self):
        git(self.project, "config", "core.hooksPath", ".husky")
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 1)
        self.assertIn("core.hooksPath is '.husky'", out)
        self.assertEqual(git(self.project, "config", "core.hooksPath"), ".husky")

    def test_force_replaces_hooks_path(self):
        git(self.project, "config", "core.hooksPath", ".husky")
        code, _, _ = run(self.project, self.module, "--force")
        self.assertEqual(code, 0)
        self.assertEqual(git(self.project, "config", "core.hooksPath"), sg.HOOKS_REL)

    def test_check_fails_when_hooks_path_was_unset(self):
        run(self.project, self.module)
        git(self.project, "config", "--unset", "core.hooksPath")
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 1)
        self.assertIn("set      core.hooksPath", out)


class ManifestAndVersionTests(Base):
    def test_broken_manifest_stops_with_clear_error(self):
        write(self.project / sg.MANIFEST_REL, "{not json")
        code, _, err = run(self.project, self.module)
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)

    def test_untagged_module_uses_commit_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod = make_module(Path(tmp), tag=None)
            self.assertRegex(sg.module_version(mod), r"^[0-9a-f]{7,}$")

    def test_module_without_git_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sg.module_version(Path(tmp)), "unknown")


if __name__ == "__main__":
    unittest.main()
