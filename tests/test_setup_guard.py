# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for scripts/setup_guard.py. Fake module checkout and fake projects in temp folders."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        (tmp / "home").mkdir()  # `--check` looks at ~/.config/bmad-guard: never the real one
        patcher = mock.patch.dict(os.environ, {"HOME": str(tmp / "home")})
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_refuses_a_missing_project_folder_with_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "m").mkdir()
            mod = make_module(Path(tmp) / "m")
            for flags in ((), ("--check",), ("--dry-run",), ("--uninstall",)):
                with self.subTest(flags=flags):
                    code, out, err = run(Path(tmp) / "missing", mod, *flags)
                    self.assertEqual((code, out), (2, ""))
                    self.assertEqual(err.count("\n"), 1)
                    self.assertIn("not a folder", err)

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


class HookFileTests(Base):
    """T6: the hooks are copied with their library files, are executable, and --check notices when one is not."""

    def setUp(self):
        super().setUp()
        write(self.module / "module/hooks/guard_lib.py", "x = 1\n")
        write(self.module / "module/hooks/README.md", "folder readme, not shipped")
        git(self.module, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
        git(self.module, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "hooks")
        self.hooks = self.project / sg.HOOKS_REL

    def test_every_hook_file_is_copied_executable_and_recorded(self):
        run(self.project, self.module)
        self.assertEqual(sorted(p.name for p in self.hooks.iterdir()), ["guard_lib.py", "pre-commit"])
        for path in self.hooks.iterdir():
            self.assertTrue(path.stat().st_mode & 0o111, path.name)
        self.assertIn(f"{sg.HOOKS_REL}/pre-commit", self.manifest()["files"])

    def test_check_fails_when_a_hook_lost_its_executable_bit_and_a_run_repairs_it(self):
        run(self.project, self.module)
        (self.hooks / "pre-commit").chmod(0o644)
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 1)
        self.assertIn("update   _bmad/custom/guard/hooks/pre-commit (not executable)", out)
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 0, out)
        self.assertTrue((self.hooks / "pre-commit").stat().st_mode & 0o111)
        self.assertEqual(run(self.project, self.module, "--check")[0], 0)

    def test_a_hook_that_changed_locally_is_a_conflict(self):
        run(self.project, self.module)
        (self.hooks / "pre-commit").write_text("#!/bin/sh\nexit 0  # emptied\n")
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 1)
        self.assertIn("conflict _bmad/custom/guard/hooks/pre-commit (changed since install)", out)


class RealHookFilesTests(Base):
    """T7: the real hook folder of the module (with `pre-push`) is installed executable and covered by --check."""

    def setUp(self):
        super().setUp()
        shutil.copytree(ROOT / "module/hooks", self.module / "module/hooks", dirs_exist_ok=True)
        git(self.module, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
        git(self.module, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "real hooks")
        self.hooks = self.project / sg.HOOKS_REL

    def test_pre_push_and_its_check_are_installed_executable_and_recorded(self):
        run(self.project, self.module)
        for name in ("pre-push", "guard_pre_push.py", "pre-commit"):
            with self.subTest(name=name):
                self.assertTrue((self.hooks / name).stat().st_mode & 0o111)
                self.assertIn(f"{sg.HOOKS_REL}/{name}", self.manifest()["files"])
        self.assertEqual(run(self.project, self.module, "--check")[0], 0)

    def test_check_reports_a_pre_push_that_is_not_executable(self):
        run(self.project, self.module)
        (self.hooks / "pre-push").chmod(0o644)
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 1)
        self.assertIn("update   _bmad/custom/guard/hooks/pre-push (not executable)", out)

    def test_check_reports_a_missing_pre_push(self):
        run(self.project, self.module)
        (self.hooks / "pre-push").unlink()
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 1)
        self.assertIn("_bmad/custom/guard/hooks/pre-push", out)


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

    def test_check_counts_a_hooks_path_that_points_elsewhere_as_a_conflict(self):
        run(self.project, self.module)
        git(self.project, "config", "core.hooksPath", ".husky")
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 1)
        self.assertIn("conflict core.hooksPath is '.husky'", out)
        self.assertIn("0 change(s), 1 conflict(s)", out)
        self.assertNotIn(" 0 conflict(s)", out)
        self.assertIn("NOT up to date", out)

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
        self.assertIn('+        "matcher": "Bash|PowerShell|Read|Grep|Glob|Write|Edit|MultiEdit|NotebookEdit",', out)
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


def snapshot(project: Path) -> dict[str, str]:
    """Every file under the project (not .git) and its content: a project before setup against after uninstall."""
    out = {}
    for path in sorted(project.rglob("*")):
        rel = path.relative_to(project)
        if ".git" in rel.parts or not path.is_file():
            continue
        out[rel.as_posix()] = path.read_text(errors="replace")
    return out


class UninstallTests(Base):
    """`setup_guard.py --uninstall`: take out what setup added, using the manifest, and nothing else."""

    def setUp(self):
        super().setUp()
        shutil.copytree(ROOT / "module/skills/tool-guard", self.module / "module/skills/tool-guard")
        self.hw = sg.load_wiring(ROOT)
        # stock BMAD files and the project's own files: never touched
        self.stock = {
            "_bmad/core/config.yaml": "user_name: Fake\n",
            "_bmad/custom/my-own-note.md": "mine\n",
            ".claude/skills/bmad-help/SKILL.md": "stock skill\n",
            "src/app.py": "print('app')\n",
        }
        for rel, text in self.stock.items():
            write(self.project / rel, text)
        self.before = snapshot(self.project)

    def setup_project(self) -> None:
        code, out, _ = run(self.project, self.module)
        self.assertEqual(code, 0, out)

    def uninstall(self, *flags: str):
        return run(self.project, self.module, "--uninstall", *flags)

    def hooks_path(self) -> str:
        out = subprocess.run(
            ["git", "config", "--local", "core.hooksPath"], cwd=self.project, capture_output=True, text=True
        )
        return out.stdout.strip()

    def test_setup_then_uninstall_gives_back_the_project(self):
        self.setup_project()
        self.assertNotEqual(snapshot(self.project), self.before)
        code, out, err = self.uninstall()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(snapshot(self.project), self.before)
        self.assertEqual(self.hooks_path(), "")
        self.assertFalse((self.project / "_bmad/custom/guard").exists())
        self.assertIn("done", out)

    def test_the_project_is_the_same_down_to_its_folders(self):
        """Folders that setup made (the guard folder, the skill folders) are gone; the ones that were there stay."""
        folders = lambda: sorted(  # noqa: E731
            p.relative_to(self.project).as_posix()
            for p in self.project.rglob("*")
            if p.is_dir() and ".git" not in p.parts
        )
        before = folders()
        self.setup_project()
        self.uninstall()
        self.assertEqual(folders(), before)

    def test_dry_run_prints_the_plan_and_changes_nothing(self):
        self.setup_project()
        before, hooks = snapshot(self.project), self.hooks_path()
        code, out, _ = self.uninstall("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("remove   .claude/skills/guard-a/SKILL.md", out)
        self.assertIn("remove   _bmad/custom/guard/manifest.json", out)
        self.assertIn("remove   .claude/settings.json (3 hook entries and 21 deny rules", out)
        self.assertIn("unset    core.hooksPath", out)
        self.assertIn("dry run: nothing changed", out)
        self.assertEqual(snapshot(self.project), before)
        self.assertEqual(self.hooks_path(), hooks)

    def test_the_projects_own_settings_survive(self):
        own = {
            "model": "m",
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "./my-check.sh"}]}]},
            "permissions": {"allow": ["Bash(ls)"], "deny": ["Read(mine)"]},
        }
        write(self.project / sg.SETTINGS_REL, json.dumps(own, indent=2) + "\n")
        self.setup_project()
        self.assertEqual(self.uninstall()[0], 0)
        self.assertEqual(json.loads((self.project / sg.SETTINGS_REL).read_text()), own)

    def test_settings_that_setup_created_are_deleted_and_the_folder_stays(self):
        self.setup_project()
        self.assertTrue((self.project / sg.SETTINGS_REL).is_file())
        self.uninstall()
        self.assertFalse((self.project / sg.SETTINGS_REL).exists())
        self.assertTrue((self.project / ".claude").is_dir())

    def test_a_file_that_changed_since_install_stops_the_whole_uninstall(self):
        self.setup_project()
        edited = self.project / ".claude/skills/guard-a/scripts/run.py"
        edited.write_text("print('my edit')")
        before = snapshot(self.project)
        code, out, _ = self.uninstall()
        self.assertEqual(code, 1)
        self.assertIn("conflict .claude/skills/guard-a/scripts/run.py (changed since install)", out)
        self.assertIn("nothing was changed", out)
        self.assertEqual(snapshot(self.project), before)
        self.assertEqual(self.hooks_path(), sg.HOOKS_REL)

    def test_force_removes_the_changed_file_too(self):
        self.setup_project()
        (self.project / ".claude/skills/guard-a/scripts/run.py").write_text("print('my edit')")
        code, out, _ = self.uninstall("--force")
        self.assertEqual(code, 0, out)
        self.assertEqual(snapshot(self.project), self.before)

    def test_a_file_in_a_skill_folder_that_setup_did_not_install_stays(self):
        self.setup_project()
        mine = write(self.project / ".claude/skills/guard-a/notes.md", "my notes")
        code, out, _ = self.uninstall()
        self.assertEqual(code, 0, out)
        self.assertEqual(mine.read_text(), "my notes")
        self.assertFalse((self.project / ".claude/skills/guard-a/SKILL.md").exists())

    def test_the_guard_folder_with_a_file_of_the_projects_stays(self):
        self.setup_project()
        mine = write(self.project / "_bmad/custom/guard/my-rules.txt", "mine")
        self.assertEqual(self.uninstall()[0], 0)
        self.assertEqual(mine.read_text(), "mine")

    def test_stock_and_project_files_are_never_touched(self):
        self.setup_project()
        self.uninstall("--force")
        for rel, text in self.stock.items():
            self.assertEqual((self.project / rel).read_text(), text, rel)

    def test_a_file_setup_did_not_create_but_replaced_with_force_is_listed_so_it_goes(self):
        """With --force setup overwrote a file it did not create; the manifest lists it, so it goes with the rest."""
        write(self.project / ".claude/skills/guard-a/SKILL.md", "the project's own copy")
        self.assertEqual(run(self.project, self.module, "--force")[0], 0)
        self.assertEqual(self.uninstall()[0], 0)
        self.assertFalse((self.project / ".claude/skills/guard-a").exists())

    def test_a_doctored_manifest_cannot_delete_other_files(self):
        self.setup_project()
        manifest = json.loads((self.project / sg.MANIFEST_REL).read_text())
        digest = sg.sha256(self.project / "src/app.py")
        for rel in (
            "src/app.py",
            "../outside.txt",
            "/etc/hostname",
            ".claude/skills/../../src/app.py",
            "_bmad/core/config.yaml",
        ):
            manifest["files"][rel] = digest
        (self.project / sg.MANIFEST_REL).write_text(json.dumps(manifest))
        outside = write(self.project.parent / "outside.txt", "keep me")
        code, out, _ = self.uninstall("--force")
        self.assertEqual(code, 1, out)
        self.assertIn("not a path setup installs", out)
        self.assertEqual((self.project / "src/app.py").read_text(), "print('app')\n")
        self.assertEqual((self.project / "_bmad/core/config.yaml").read_text(), "user_name: Fake\n")
        self.assertEqual(outside.read_text(), "keep me")

    def test_a_link_in_place_of_an_installed_file_is_not_followed(self):
        self.setup_project()
        target = write(self.project / "src/keep.txt", "keep me")
        link = self.project / ".claude/skills/guard-a/SKILL.md"
        link.unlink()
        link.symlink_to(target)
        code, out, _ = self.uninstall("--force")
        self.assertEqual(code, 1, out)
        self.assertEqual(target.read_text(), "keep me")

    def test_core_hooks_path_that_is_not_ours_is_kept(self):
        self.setup_project()
        git(self.project, "config", "--local", "core.hooksPath", ".husky")
        code, out, _ = self.uninstall()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.hooks_path(), ".husky")
        self.assertIn("keep     core.hooksPath", out)

    def test_gitignore_keeps_the_projects_lines_and_removes_ours(self):
        write(self.project / ".gitignore", "node_modules/\n")
        self.setup_project()
        self.uninstall()
        self.assertEqual((self.project / ".gitignore").read_text(), "node_modules/\n")

    def test_a_gitignore_that_only_held_our_lines_is_deleted(self):
        self.setup_project()
        self.assertTrue((self.project / ".gitignore").is_file())
        self.uninstall()
        self.assertFalse((self.project / ".gitignore").exists())

    def test_a_gitignore_line_that_setup_did_not_write_stays_unless_forced(self):
        write(self.project / ".gitignore", ".bmad-guard/\n")  # the project already had it
        self.setup_project()
        self.assertEqual((self.project / ".gitignore").read_text(), ".bmad-guard/\n")  # setup added nothing
        code, out, _ = self.uninstall()
        self.assertEqual(code, 0, out)
        self.assertEqual((self.project / ".gitignore").read_text(), ".bmad-guard/\n")
        self.assertIn("keep     .gitignore", out)

    def test_the_local_event_queue_is_left_and_named(self):
        self.setup_project()
        write(self.project / ".bmad-guard/events/queue.jsonl", "{}\n")
        code, out, _ = self.uninstall()
        self.assertEqual(code, 0, out)
        self.assertTrue((self.project / ".bmad-guard/events/queue.jsonl").is_file())
        self.assertIn(".bmad-guard/", out)

    def test_a_lookalike_hook_entry_is_a_conflict_and_force_removes_it(self):
        self.setup_project()
        path = self.project / sg.SETTINGS_REL
        data = json.loads(path.read_text())
        data["hooks"]["PreToolUse"].append(
            {"hooks": [{"type": "command", "command": "python3 old/skills/tool-guard/scripts/pre_tool_use.py"}]}
        )
        path.write_text(json.dumps(data))
        code, out, _ = self.uninstall()
        self.assertEqual(code, 1)
        self.assertIn("looks like a bmad-guard hook", out)
        self.assertEqual(self.uninstall("--force")[0], 0)
        self.assertFalse(path.exists())

    def test_without_a_manifest_it_stops(self):
        code, _, err = self.uninstall()
        self.assertEqual(code, 2)
        self.assertIn("no manifest", err)
        self.assertEqual(snapshot(self.project), self.before)

    def test_a_broken_manifest_stops(self):
        write(self.project / sg.MANIFEST_REL, "{not json")
        self.assertEqual(self.uninstall()[0], 2)

    def test_a_second_uninstall_stops_cleanly_and_setup_works_again(self):
        self.setup_project()
        self.assertEqual(self.uninstall()[0], 0)
        self.assertEqual(self.uninstall()[0], 2)
        self.setup_project()
        self.assertEqual(self.hooks_path(), sg.HOOKS_REL)

    def test_a_run_that_stopped_half_way_can_be_run_again(self):
        """The manifest goes last, so a stop in the middle leaves it in place and the next run finishes."""
        self.setup_project()
        (self.project / ".claude/skills/guard-a/SKILL.md").unlink()
        (self.project / ".claude/skills/guard-a/scripts/run.py").unlink()
        self.assertEqual(self.uninstall()[0], 0)
        self.assertEqual(snapshot(self.project), self.before)

    def test_it_does_not_run_with_check_or_dry_run_together_wrongly(self):
        with self.assertRaises(SystemExit):
            run(self.project, self.module, "--uninstall", "--check")

    def test_a_module_that_does_not_ship_the_wiring_still_removes_files_and_the_hooks_path(self):
        shutil.rmtree(self.module / "module/skills/tool-guard")
        run(self.project, self.module)
        code, out, _ = self.uninstall()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.hooks_path(), "")

    def test_the_real_module_round_trip(self):
        """The real skills, hooks and overrides: setup, uninstall, and the project is as before."""
        code, out, _ = run(self.project, ROOT)
        self.assertEqual(code, 0, out)
        self.assertTrue((self.project / ".claude/skills/tool-guard/scripts/pre_tool_use.py").is_file())
        code, out, _ = run(self.project, ROOT, "--uninstall")
        self.assertEqual(code, 0, out)
        self.assertEqual(snapshot(self.project), self.before)
        self.assertEqual(self.hooks_path(), "")


class MachineCheckTests(Base):
    """ADR 0008, version skew: `--check` compares the project's manifest version with the machine folder's VERSION."""

    def setUp(self):
        super().setUp()
        run(self.project, self.module)
        self.machine = self.project.parent / "machine"
        self.machine.mkdir()

    def check(self, *flags: str):
        return run(self.project, self.module, "--check", "--machine-dir", str(self.machine), *flags)

    def test_no_machine_folder_is_a_note_and_not_a_failure(self):
        code, out, _ = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("note     machine scripts: none (fine unless managed settings point to them)", out)

    def test_the_same_version_says_nothing(self):
        (self.machine / "VERSION").write_text(self.manifest()["version"] + "\n")
        code, out, _ = self.check()
        self.assertEqual(code, 0, out)
        self.assertNotIn("machine scripts", out)

    def test_a_different_version_is_a_warning_and_does_not_change_the_exit_code(self):
        (self.machine / "VERSION").write_text("v9.9.9\n")
        code, out, _ = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("warning  machine scripts: v9.9.9; this project's module: v0.1.0. They differ", out)
        self.assertIn("up to date", out)

    def test_the_exit_code_still_follows_the_project(self):
        (self.project / sg.MANIFEST_REL).unlink()
        (self.machine / "VERSION").write_text("v9.9.9\n")
        self.assertEqual(self.check()[0], 1)

    def test_an_empty_version_file_counts_as_no_machine_folder(self):
        (self.machine / "VERSION").write_text("\n")
        self.assertIn("machine scripts: none", self.check()[1])

    def test_the_default_is_the_os_folder_and_windows_has_none(self):
        hw = sg.load_wiring(ROOT)
        shutil.copytree(ROOT / "module/skills/tool-guard", self.module / "module/skills/tool-guard")
        with mock.patch.object(sg.sys, "platform", "plan9"):
            self.assertEqual(sg.machine_report(self.module, "v1", None), [])
        with (
            mock.patch.object(sg.sys, "platform", "linux"),
            mock.patch.object(sg.Path, "read_text", side_effect=OSError),
        ):
            self.assertIn("none", sg.machine_report(self.module, "v1", None)[0])
        self.assertEqual(hw.machine_base("linux"), "/etc/claude-code/bmad-guard")

    def test_machine_dir_needs_check(self):
        with self.assertRaises(SystemExit):
            run(self.project, self.module, "--machine-dir", str(self.machine))


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


EVENTS_REL = "module/skills/agent-project-logger/scripts/guard_events.py"
FAKE_KEY = "fake-setup-secret-7"


class FakeHomeMixin:
    """HOME is a temp folder and no collector settings leak in, so a test never touches the real home."""

    def use_fake_home(self, tmp: Path) -> None:
        self.home = tmp / "home"
        self.home.mkdir(exist_ok=True)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("BMAD_GUARD_KEY", "BMAD_GUARD_ENDPOINT", "BMAD_GUARD_USER", "BMAD_GUARD_COLLECTOR"):
            os.environ.pop(name, None)
        self.folder = self.home / ".config" / "bmad-guard"
        self.cred = self.folder / "credentials"


class CredentialSetupTests(FakeHomeMixin, unittest.TestCase):
    """`--credentials`: writes the per-user file once (ADR 0007). Fake values only."""

    ARGS = ("--credentials", "--endpoint", "https://collector.example.invalid", "--user", "dev-a")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.use_fake_home(Path(tmp.name).resolve())

    def go(self, *args: str, key: str | None = FAKE_KEY, prompt: str | None = None):
        out, err = io.StringIO(), io.StringIO()
        env = {"BMAD_GUARD_KEY": key} if key is not None else {}
        with (
            mock.patch.dict(os.environ, env),
            mock.patch.object(sg.getpass, "getpass", return_value=prompt or "") as asked,
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = sg.main(list(args or self.ARGS))
        self.asked = asked
        return code, out.getvalue(), err.getvalue()

    def mode(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_writes_the_file_with_mode_600_in_a_700_folder(self):
        code, out, err = self.go()
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual((self.mode(self.folder), self.mode(self.cred)), (0o700, 0o600))
        text = self.cred.read_text()
        self.assertIn("[collector]", text)
        self.assertIn("endpoint = https://collector.example.invalid", text)
        self.assertIn("user = dev-a", text)
        self.assertIn(f"key = {FAKE_KEY}", text)
        self.assertNotIn(FAKE_KEY, out)
        self.assertTrue(out.rstrip().endswith("done"))

    def test_the_file_is_created_with_the_mode_not_changed_afterwards(self):
        with mock.patch.object(sg.os, "chmod", side_effect=AssertionError("chmod")):
            self.assertEqual(self.go()[0], 0)
        self.assertEqual(self.mode(self.cred), 0o600)

    def test_a_loose_umask_does_not_open_the_file(self):
        old = os.umask(0)
        self.addCleanup(os.umask, old)
        self.assertEqual(self.go()[0], 0)
        self.assertEqual((self.mode(self.folder), self.mode(self.cred)), (0o700, 0o600))

    def test_the_sender_accepts_what_setup_wrote(self):
        self.go()
        ge = sg.load_events(sg.DEFAULT_MODULE)
        values, problem = ge.read_credential_file(self.cred)
        self.assertEqual(problem, "")
        self.assertEqual(values, {"endpoint": "https://collector.example.invalid", "user": "dev-a", "key": FAKE_KEY})

    def test_the_second_run_with_the_same_values_changes_nothing(self):
        self.go()
        before = (self.cred.read_text(), self.cred.stat().st_ino, self.cred.stat().st_mtime_ns)
        code, out, _ = self.go()
        self.assertEqual(code, 0, out)
        self.assertIn("keep", out)
        self.assertEqual(before, (self.cred.read_text(), self.cred.stat().st_ino, self.cred.stat().st_mtime_ns))

    def test_different_values_are_a_conflict_without_force(self):
        self.go()
        before = self.cred.read_text()
        code, out, _ = self.go(key="fake-other-secret-8")
        self.assertEqual(code, 1)
        self.assertIn("conflict", out)
        self.assertIn("--force", out)
        self.assertIn("key", out)
        self.assertNotIn(FAKE_KEY, out)
        self.assertNotIn("fake-other-secret-8", out)
        self.assertEqual(self.cred.read_text(), before)

    def test_force_replaces_the_file_and_keeps_the_mode(self):
        self.go()
        code, out, _ = self.go(
            "--credentials", "--endpoint", "https://other.example.invalid", "--user", "dev-a", "--force"
        )
        self.assertEqual(code, 0, out)
        self.assertIn("endpoint = https://other.example.invalid", self.cred.read_text())
        self.assertEqual(self.mode(self.cred), 0o600)

    def test_force_repairs_a_file_that_is_too_open(self):
        self.go()
        self.cred.chmod(0o644)
        self.assertEqual(self.go()[0], 1)  # refused without --force, even with the same values
        self.assertEqual(self.go(*self.ARGS, "--force")[0], 0)
        self.assertEqual(self.mode(self.cred), 0o600)

    def test_the_key_comes_from_the_prompt_when_the_variable_is_not_set(self):
        code, out, _ = self.go(key=None, prompt=FAKE_KEY)
        self.assertEqual(code, 0, out)
        self.asked.assert_called_once()
        self.assertIn(f"key = {FAKE_KEY}", self.cred.read_text())

    def test_the_variable_is_used_without_asking(self):
        self.go()
        self.asked.assert_not_called()

    def test_there_is_no_key_option_on_the_command_line(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
            sg.main([*self.ARGS, "--key", FAKE_KEY])
        self.assertEqual(stop.exception.code, 2)
        self.assertFalse(self.cred.exists())

    def test_an_empty_key_is_refused(self):
        code, _, err = self.go(key=None, prompt="  ")
        self.assertEqual(code, 2)
        self.assertIn("key", err)
        self.assertFalse(self.cred.exists())

    def test_values_with_control_characters_are_refused_without_echoing_them(self):
        code, _, err = self.go(key="bad\nkey = injected")
        self.assertEqual(code, 2)
        self.assertNotIn("injected", err)
        self.assertFalse(self.cred.exists())

    def test_an_endpoint_the_sender_would_refuse_is_refused(self):
        code, _, err = self.go("--credentials", "--endpoint", "http://collector.example.invalid", "--user", "dev-a")
        self.assertEqual(code, 2)
        self.assertIn("https", err)

    def test_endpoint_and_user_are_required(self):
        for args in (
            ("--credentials", "--user", "dev-a"),
            ("--credentials", "--endpoint", "https://c.example.invalid"),
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                sg.main(list(args))

    def test_it_never_writes_into_a_project_and_refuses_project_flags(self):
        for extra in (("--project", "."), ("--check",), ("--dry-run",)):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                sg.main([*self.ARGS, *extra])

    def test_endpoint_and_user_need_credentials_mode(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            sg.main(["--project", ".", "--endpoint", "https://c.example.invalid"])

    def test_project_is_still_required_without_credentials_mode(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop:
            sg.main([])
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("--project", err.getvalue())

    def test_windows_is_refused(self):
        with mock.patch.object(sg.sys, "platform", "win32"):
            code, _, err = self.go()
        self.assertEqual(code, 2)
        self.assertIn("not supported yet", err)
        self.assertFalse(self.cred.exists())

    def test_a_folder_others_can_write_is_reported_as_a_problem(self):
        self.folder.mkdir(parents=True)
        self.folder.chmod(0o777)
        code, out, _ = self.go()
        self.assertEqual(code, 1)
        self.assertIn("chmod 700", out)


class CheckCredentialTests(FakeHomeMixin, Base):
    """`--check` in a project: a note for a missing credential file, a problem line for a bad one, a Windows warning."""

    def setUp(self):
        super().setUp()
        tmp = Path(self._tmp.name)
        self.home, self.folder = tmp / "home", tmp / "home" / ".config" / "bmad-guard"
        self.cred = self.folder / "credentials"
        shutil.copy2(ROOT / EVENTS_REL, write(self.module / EVENTS_REL, ""))
        git(self.module, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
        git(self.module, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "events")
        run(self.project, self.module)

    def test_a_missing_file_is_a_note_and_not_a_failure(self):
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("note", out)
        self.assertIn("no credential file", out)
        self.assertIn("up to date", out)

    def test_a_good_file_says_nothing(self):
        self.folder.mkdir(parents=True)
        self.folder.chmod(0o700)
        self.cred.write_text("[collector]\nendpoint = https://c.example.invalid\nuser = u\nkey = fake-secret\n")
        self.cred.chmod(0o600)
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 0, out)
        self.assertNotIn("credential", out)

    def test_a_file_that_is_too_open_is_a_problem_line_with_the_fix_and_no_content(self):
        self.folder.mkdir(parents=True)
        self.folder.chmod(0o700)
        self.cred.write_text("[collector]\nendpoint = https://c.example.invalid\nuser = u\nkey = fake-secret\n")
        self.cred.chmod(0o644)
        code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 0, "the exit code is about the project, not the machine's file")
        self.assertIn("problem", out)
        self.assertIn("chmod 600", out)
        self.assertNotIn("fake-secret", out)

    def test_windows_gets_a_warning(self):
        with mock.patch.object(sg.sys, "platform", "win32"):
            code, out, _ = run(self.project, self.module, "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("Windows is not supported yet", out)

    def test_other_platforms_get_no_windows_warning(self):
        self.assertNotIn("Windows", run(self.project, self.module, "--check")[1])


if __name__ == "__main__":
    unittest.main()
