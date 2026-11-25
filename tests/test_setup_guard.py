# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for scripts/setup_guard.py. Fake module checkout and fake projects in temp folders."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
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


class SettingsWiringTests(Base):
    """T1: hook entries and deny rules merged into .claude/settings.json."""

    STALE = "python3 .claude/skills/tool-guard/scripts/pre_tool_use.py"

    def setUp(self):
        super().setUp()
        shutil.copytree(ROOT / "module/skills/tool-guard", self.module / "module/skills/tool-guard")
        wiring = ROOT / "module/skills/tool-guard/scripts/hook_wiring.py"
        self.hw = sg.load_wiring(ROOT)
        self.assertEqual(Path(self.hw.__file__), wiring)
        self.settings_path = self.project / sg.SETTINGS_REL

    def settings(self) -> dict:
        return json.loads(self.settings_path.read_text())

    def pre_commands(self) -> list[str]:
        return [h["command"] for g in self.settings()["hooks"]["PreToolUse"] for h in g["hooks"]]

    def write_stale(self) -> None:
        stale = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": self.STALE}]}]}}
        write(self.settings_path, json.dumps(stale))

    def test_constants_match_the_wiring_module(self):
        self.assertEqual(sg.SETTINGS_REL, self.hw.SETTINGS_REL)
        self.assertEqual(sg.HOOKS_REL, self.hw.HOOKS_REL)
        self.assertIn(sg.WIRING_TRIGGER, self.hw.script_paths())

    def test_fresh_install_creates_settings_with_hooks_and_deny_rules(self):
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 0, out)
        self.assertIn("create   .claude/settings.json (add hook PreToolUse pre_tool_use.py)", out)
        self.assertEqual(self.settings()["hooks"], self.hw.desired_hooks())
        self.assertEqual(self.settings()["permissions"]["deny"], list(self.hw.DENY_RULES))
        self.assertTrue(self.settings_path.read_text().endswith("}\n"))
        self.assertTrue((self.project / ".claude/skills/tool-guard/scripts/pre_tool_use.py").is_file())

    def test_settings_file_is_not_recorded_in_the_manifest(self):
        run(self.project, self.module)
        self.assertNotIn(sg.SETTINGS_REL, self.manifest()["files"])

    def test_projects_own_entries_and_keys_are_kept(self):
        own = {"matcher": "Bash", "hooks": [{"type": "command", "command": "./my-check.sh"}]}
        existing = {
            "model": "m",
            "hooks": {"PreToolUse": [own]},
            "permissions": {"allow": ["Bash(ls)"], "deny": ["Read(x)"]},
        }
        write(self.settings_path, json.dumps(existing))
        write(self.project / ".claude/settings.local.json", '{"local": true}')
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 0, out)
        self.assertIn("update   .claude/settings.json", out)
        data = self.settings()
        self.assertEqual(data["model"], "m")
        self.assertEqual(data["hooks"]["PreToolUse"][0], own)
        self.assertEqual(len(data["hooks"]["PreToolUse"]), 2)
        self.assertEqual(data["permissions"]["allow"], ["Bash(ls)"])
        self.assertEqual(data["permissions"]["deny"][0], "Read(x)")
        self.assertEqual((self.project / ".claude/settings.local.json").read_text(), '{"local": true}')

    def test_second_run_changes_nothing_and_check_passes(self):
        run(self.project, self.module)
        before = self.settings_path.read_bytes()
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 0)
        self.assertIn("0 change(s), 0 conflict(s)", out)
        self.assertEqual(self.settings_path.read_bytes(), before)
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 0, out)

    def test_dry_run_shows_the_diff_and_writes_nothing(self):
        code, out, _ = run(self.project, self.module, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("+++ b/.claude/settings.json", out)
        self.assertIn('+        "matcher": "Bash|Read|Grep|Glob|Write|Edit|MultiEdit|NotebookEdit",', out)
        self.assertIn("dry run: nothing changed", out)
        self.assertFalse(self.settings_path.exists())

    def test_dry_run_on_existing_settings_diffs_against_them(self):
        write(self.settings_path, json.dumps({"model": "m"}, indent=2) + "\n")
        before = self.settings_path.read_text()
        _, out, _ = run(self.project, self.module, "--dry-run")
        self.assertIn("--- a/.claude/settings.json", out)
        self.assertIn('+  "hooks": {', out)
        self.assertIn('+  "model": "m",', out)  # only the comma changes on the project's own line
        self.assertEqual(self.settings_path.read_text(), before)

    def test_check_reports_missing_wiring_and_changes_nothing(self):
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 1)
        self.assertIn("add hook SessionStart session_start.py", out)
        self.assertIn("NOT up to date", out)
        self.assertFalse(self.settings_path.exists())

    def test_check_reports_a_hook_that_went_missing_after_install(self):
        run(self.project, self.module)
        data = self.settings()
        del data["hooks"]["PreToolUse"]
        self.settings_path.write_text(json.dumps(data))
        before = self.settings_path.read_bytes()
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 1)
        self.assertIn("add hook PreToolUse pre_tool_use.py", out)
        self.assertEqual(self.settings_path.read_bytes(), before)

    def test_check_reports_a_missing_deny_rule(self):
        run(self.project, self.module)
        data = self.settings()
        data["permissions"]["deny"].remove(self.hw.DENY_RULES[3])
        self.settings_path.write_text(json.dumps(data))
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 1)
        self.assertIn(f"add deny rule {self.hw.DENY_RULES[3]}", out)

    def test_edited_copy_of_our_hook_is_updated_without_force(self):
        run(self.project, self.module)
        data = self.settings()
        data["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 999
        self.settings_path.write_text(json.dumps(data))
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.settings()["hooks"]["PreToolUse"], self.hw.desired_hooks()["PreToolUse"])

    def test_lookalike_hook_is_a_conflict_and_left_alone(self):
        self.write_stale()
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 1)
        self.assertIn("conflict .claude/settings.json", out)
        self.assertIn("done with conflicts", out)
        self.assertIn(self.STALE, self.pre_commands())
        self.assertIn(self.hw.command("tool-guard", "pre_tool_use.py"), self.pre_commands())  # rest still added

    def test_check_fails_on_a_conflict(self):
        self.write_stale()
        run(self.project, self.module)
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 1)
        self.assertIn("conflict .claude/settings.json", out)

    def test_force_replaces_the_lookalike(self):
        self.write_stale()
        code, out, _ = run(self.project, self.module, "--force")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.pre_commands(), [self.hw.command("tool-guard", "pre_tool_use.py")])

    def test_disable_all_hooks_is_a_conflict_until_forced(self):
        write(self.settings_path, json.dumps({"disableAllHooks": True}))
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 1)
        self.assertIn("disableAllHooks is true", out)
        self.assertTrue(self.settings()["disableAllHooks"])
        code, _, _ = run(self.project, self.module, "--force")
        self.assertEqual(code, 0)
        self.assertNotIn("disableAllHooks", self.settings())

    def test_invalid_settings_json_stops_with_a_clear_error_and_changes_nothing(self):
        write(self.settings_path, "{not json")
        code, _, err = run(self.project, self.module)
        self.assertEqual(code, 2)
        self.assertIn(".claude/settings.json: settings.json is not valid JSON", err)
        self.assertEqual(self.settings_path.read_text(), "{not json")
        self.assertFalse((self.project / sg.MANIFEST_REL).exists())

    def test_no_claude_skills_folder_means_no_wiring(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = make_project(Path(tmp), tools=(".agents/skills",))
            code, out, _ = run(proj, self.module)
            self.assertEqual(code, 0, out)
            self.assertIn("skip     .claude/settings.json", out)
            self.assertFalse((proj / ".claude").exists())
            code, _, _ = run(proj, self.module, "--check")
            self.assertEqual(code, 0)

    def test_module_without_tool_guard_leaves_settings_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "m").mkdir()
            (Path(tmp) / "p").mkdir()
            mod = make_module(Path(tmp) / "m")
            proj = make_project(Path(tmp) / "p")
            code, out, _ = run(proj, mod)
            self.assertEqual(code, 0, out)
            self.assertFalse((proj / sg.SETTINGS_REL).exists())

    def test_wiring_survives_a_module_update(self):
        run(self.project, self.module)
        write(self.module / "module/skills/guard-a/SKILL.md", "skill a v2")
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.settings()["hooks"], self.hw.desired_hooks())


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
