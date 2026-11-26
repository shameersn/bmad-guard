# /// script
# requires-python = ">=3.10"
# ///
"""The unguarded-project rule (ADR 0008): the three hook entries decide first whether the project uses the guard.

  no `_bmad/`                                  -> allow silently: no output, no event, no file written
  `_bmad/` but no `_bmad/custom/guard/manifest.json` -> allow; SessionStart prints one line
  manifest present                             -> all guards, fail closed, as before

`hook_common.guard_state` (tool-guard) and `prompt_check.guard_state` (prompt-guard) must agree. The hard part is
the other direction: a crafted or odd input must never make a guarded project look unguarded. Real scripts run in
new processes with a clean environment. Fake data only.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.fake_home import isolated_env

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "module/skills"
TOOL = SKILLS / "tool-guard/scripts"
PROMPT = SKILLS / "prompt-guard/scripts"
for folder in (TOOL, PROMPT):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import hook_common as hc  # noqa: E402 - found through the sys.path lines above
import prompt_check as pc  # noqa: E402

MANIFEST = "_bmad/custom/guard/manifest.json"
FAKE_KEY = "gh" + "p_" + "b7" * 18  # built at run time: no key-shaped literal in the file
PUSH = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}
WRITE_SRC = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Write",
    "tool_input": {"file_path": "src/app.py", "content": "x"},
}
HOOKS = {
    "pre": TOOL / "pre_tool_use.py",
    "prompt": PROMPT / "prompt_guard_hook.py",
    "start": TOOL / "session_start.py",
}


def tree(path: Path) -> list[str]:
    """Every file and folder under `path`, as relative names (to see that nothing was written)."""
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(os.path.realpath(tmp.name))
        (self.base / "home").mkdir()
        self.env = {**isolated_env(), "HOME": str(self.base / "home")}
        self.env.pop("CLAUDE_PROJECT_DIR", None)

    def project(self, name: str, bmad: bool = False, manifest: bool = False) -> Path:
        path = self.base / name
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)
        if bmad:
            (path / "_bmad").mkdir()
        if manifest:
            (path / MANIFEST).parent.mkdir(parents=True)
            (path / MANIFEST).write_text('{"version": "v1", "files": {}}')
        return path

    def hook(self, which: str, payload: dict | str, project: Path | None, cwd: Path | None = None, env_dir=True):
        """Run one real hook script. `project` is CLAUDE_PROJECT_DIR (when env_dir), `cwd` the process folder; the
        hook input says `cwd` is the process folder, unless the payload sets its own."""
        env = dict(self.env)
        if project is not None and env_dir:
            env["CLAUDE_PROJECT_DIR"] = str(project)
        where = cwd or project or self.base
        if isinstance(payload, dict):
            payload = {"cwd": str(where), "session_id": "s-1", **payload}
            payload = json.dumps(payload)
        return subprocess.run(
            [sys.executable, str(HOOKS[which])], input=payload, capture_output=True, text=True, env=env, cwd=where
        )


class StateTableTests(Base):
    """guard_state, with the two copies side by side."""

    def setUp(self):
        super().setUp()
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.base / "home")  # the process folder is one of the places the rule looks at

    def states(self, root, cwd=None) -> tuple[str, str]:
        return hc.guard_state(root, cwd), pc.guard_state(root, cwd)

    def check(self, expected: str, root, cwd=None, msg: str = ""):
        a, b = self.states(root, cwd)
        self.assertEqual(a, expected, f"hook_common {msg}")
        self.assertEqual(b, expected, f"prompt_check {msg}")

    def test_the_three_states(self):
        self.check(hc.UNGUARDED, self.project("plain"))
        self.check(hc.NO_MANIFEST, self.project("bmad", bmad=True))
        self.check(hc.GUARDED, self.project("guarded", bmad=True, manifest=True))

    def test_the_constants_match_in_both_copies(self):
        for name in ("GUARDED", "NO_MANIFEST", "UNGUARDED", "GUARD_MANIFEST_REL"):
            self.assertEqual(getattr(hc, name), getattr(pc, name), name)
        self.assertEqual(hc.GUARD_MANIFEST_REL, MANIFEST)

    def test_the_two_copies_have_the_same_code(self):
        for name in ("_exists", "guard_state"):
            self.assertEqual(inspect.getsource(getattr(hc, name)), inspect.getsource(getattr(pc, name)), name)

    def test_a_bmad_file_instead_of_a_folder_is_bmad_without_manifest(self):
        root = self.project("odd")
        (root / "_bmad").write_text("not a folder")
        self.check(hc.NO_MANIFEST, root)

    def test_a_manifest_in_any_form_means_guarded(self):
        """A folder, an empty file, a dangling link: something is there, so the project is guarded."""
        for name, make in (
            ("folder", lambda p: p.mkdir(parents=True)),
            ("empty", lambda p: (p.parent.mkdir(parents=True), p.write_text(""))),
            ("dangling", lambda p: (p.parent.mkdir(parents=True), p.symlink_to(self.base / "nowhere"))),
        ):
            with self.subTest(form=name):
                root = self.project(f"m-{name}")
                make(root / MANIFEST)
                self.check(hc.GUARDED, root, msg=name)

    def test_a_linked_bmad_folder_with_a_manifest_is_guarded(self):
        real = self.project("real", bmad=True, manifest=True)
        root = self.project("linked")
        (root / "_bmad").symlink_to(real / "_bmad")
        self.check(hc.GUARDED, root)

    def test_a_link_to_a_guarded_project_is_guarded(self):
        real = self.project("real", bmad=True, manifest=True)
        link = self.base / "link"
        link.symlink_to(real)
        self.check(hc.GUARDED, link)
        self.check(hc.GUARDED, self.project("plain"), cwd=link)

    def test_a_folder_inside_a_guarded_project_is_guarded(self):
        """No CLAUDE_PROJECT_DIR and no git repository: the root comes out as the sub-folder."""
        real = self.project("real", bmad=True, manifest=True)
        sub = real / "apps/web"
        sub.mkdir(parents=True)
        self.check(hc.GUARDED, sub)
        self.check(hc.GUARDED, self.project("plain"), cwd=sub)

    def test_a_guarded_root_wins_over_an_unguarded_cwd_and_the_other_way_round(self):
        guarded = self.project("guarded", bmad=True, manifest=True)
        plain = self.project("plain")
        self.check(hc.GUARDED, guarded, cwd=plain)
        self.check(hc.GUARDED, plain, cwd=guarded)
        self.check(hc.UNGUARDED, plain, cwd=plain)

    def test_a_cwd_of_the_wrong_type_is_guarded(self):
        plain = self.project("plain")
        for cwd in (5, 0, ["x"], {}, b"/tmp", True):
            with self.subTest(cwd=cwd):
                self.check(hc.GUARDED, plain, cwd=cwd)

    def test_empty_or_missing_cwd_is_ignored(self):
        plain = self.project("plain")
        for cwd in (None, ""):
            self.check(hc.UNGUARDED, plain, cwd=cwd)

    def test_a_cwd_that_does_not_exist_is_not_a_reason_to_guard(self):
        self.check(hc.UNGUARDED, self.project("plain"), cwd=self.base / "gone")

    def test_a_null_byte_in_a_path_is_guarded(self):
        self.check(hc.GUARDED, self.project("plain"), cwd="/nowhere/x\0y")
        self.check(hc.GUARDED, "/nowhere/x\0y")

    def test_a_relative_root_is_resolved_against_the_process_folder(self):
        guarded = self.project("guarded", bmad=True, manifest=True)
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(guarded)
        self.check(hc.GUARDED, ".")
        os.chdir(self.base / "home")
        self.check(hc.UNGUARDED, ".")

    @unittest.skipIf(os.geteuid() == 0, "root can read everything")
    def test_an_unreadable_folder_on_the_way_is_guarded(self):
        """lstat fails with a permission error: not 'absent', so the project counts as guarded."""
        root = self.project("locked", bmad=True)
        locked = root / "_bmad"
        locked.chmod(0o000)
        self.addCleanup(locked.chmod, 0o755)
        self.check(hc.GUARDED, root)

    def test_any_error_while_deciding_is_guarded(self):
        plain = self.project("plain")
        with mock.patch("os.path.abspath", side_effect=RuntimeError("boom")):
            self.assertEqual(hc.guard_state(plain), hc.GUARDED)
            self.assertEqual(pc.guard_state(plain), hc.GUARDED)

    def test_project_root_in_both_copies_agree(self):
        """The rule must look at the same folder in both hooks (ADR 0008)."""
        plain = self.project("plain")
        sub = plain / "a/b"
        sub.mkdir(parents=True)
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}):
            self.assertEqual(hc.project_root(sub), pc.project_root(sub))
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(plain)}):
            self.assertEqual(hc.project_root(sub), pc.project_root(sub))


class UnguardedProjectTests(Base):
    """No `_bmad/`: allow, silent, nothing written (M-I5, MACH-C1)."""

    def test_every_hook_is_silent_and_writes_nothing(self):
        root = self.project("plain")
        (root / "README.md").write_text("x")
        before = tree(root)
        cases = (
            ("pre", PUSH),  # blocked in a guarded project
            ("pre", WRITE_SRC),  # the write gate would block a source write with no plan
            ("prompt", {"hook_event_name": "UserPromptSubmit", "prompt": f"use this token {FAKE_KEY}"}),
            ("start", {"hook_event_name": "SessionStart", "source": "startup"}),
        )
        for which, payload in cases:
            with self.subTest(hook=which, tool=payload.get("tool_name", "")):
                proc = self.hook(which, payload, root)
                self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))
        self.assertEqual(tree(root), before, "a file or folder was created in the project")
        self.assertFalse((root / ".bmad-guard").exists())

    def test_bad_input_is_allowed_too(self):
        root = self.project("plain")
        for which in ("pre", "prompt", "start"):
            with self.subTest(hook=which):
                proc = self.hook(which, "{not json", root)
                self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))

    def test_a_folder_that_is_not_a_git_repository_is_allowed_too(self):
        root = self.base / "no-git"
        root.mkdir()
        proc = self.hook("pre", PUSH, root)
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))
        self.assertEqual(tree(root), [])

    def test_a_broken_guard_module_cannot_block_an_unguarded_project(self):
        """The rule runs before the guards are imported (ADR 0008), so an import error cannot block."""
        copy = self.base / "scripts-copy"
        shutil.copytree(SKILLS, copy / "skills", ignore=shutil.ignore_patterns("__pycache__"))
        (copy / "skills/tool-guard/scripts/command_guard.py").write_text("this is not python (\n")
        script = copy / "skills/tool-guard/scripts/pre_tool_use.py"
        env = {**self.env, "CLAUDE_PROJECT_DIR": str(self.project("plain"))}
        run = lambda: subprocess.run(  # noqa: E731
            [sys.executable, str(script)], input=json.dumps(PUSH), capture_output=True, text=True, env=env
        )
        self.assertEqual(run().returncode, 0)
        guarded = self.project("guarded", bmad=True, manifest=True)
        env["CLAUDE_PROJECT_DIR"] = str(guarded)
        proc = run()
        self.assertEqual(proc.returncode, 2)  # a guarded project still fails closed
        self.assertIn("GUARD_ERROR", proc.stderr)


class BmadWithoutManifestTests(Base):
    """`_bmad/` but no manifest: allow, with one line at session start (M-I6, MACH-C2)."""

    def test_tool_and_prompt_hooks_allow_silently(self):
        root = self.project("bmad", bmad=True)
        before = tree(root)
        for which, payload in (
            ("pre", PUSH),
            ("pre", WRITE_SRC),
            ("prompt", {"hook_event_name": "UserPromptSubmit", "prompt": f"use this token {FAKE_KEY}"}),
        ):
            with self.subTest(hook=which):
                proc = self.hook(which, payload, root)
                self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))
        self.assertEqual(tree(root), before)

    def test_session_start_prints_one_line_and_writes_nothing(self):
        root = self.project("bmad", bmad=True)
        before = tree(root)
        proc = self.hook("start", {"hook_event_name": "SessionStart", "source": "startup"}, root)
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        message = json.loads(proc.stdout)["systemMessage"]
        self.assertNotIn("\n", message)
        self.assertIn("bmad-guard is not installed", message)
        self.assertIn("guards are off", message)
        self.assertIn("setup_guard.py", message)
        self.assertEqual(tree(root), before)
        self.assertFalse((root / ".bmad-guard").exists())


class GuardedProjectTests(Base):
    """The manifest is there: everything works as before, and it fails closed (M-I7)."""

    def test_the_guards_run(self):
        root = self.project("guarded", bmad=True, manifest=True)
        proc = self.hook("pre", PUSH, root)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("CMD_GIT_FORCE_PUSH", proc.stderr)
        proc = self.hook(
            "prompt", {"hook_event_name": "UserPromptSubmit", "prompt": f"use this token {FAKE_KEY}"}, root
        )
        self.assertEqual(json.loads(proc.stdout)["decision"], "block")
        proc = self.hook("start", {"hook_event_name": "SessionStart", "source": "startup"}, root)
        self.assertIn("core.hooksPath", json.loads(proc.stdout)["systemMessage"])

    def test_bad_input_blocks(self):
        root = self.project("guarded", bmad=True, manifest=True)
        proc = self.hook("pre", "{not json", root)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("BAD_HOOK_INPUT", proc.stderr)


class CraftedInputTests(Base):
    """A guarded project must never look unguarded, whatever the hook input and environment say."""

    def setUp(self):
        super().setUp()
        self.guarded = self.project("guarded", bmad=True, manifest=True)
        self.plain = self.project("plain")

    def blocked(self, proc: subprocess.CompletedProcess) -> None:
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("CMD_GIT_FORCE_PUSH", proc.stderr)

    def test_no_project_dir_variable_and_the_input_cwd_in_the_project(self):
        self.blocked(self.hook("pre", PUSH, self.guarded, env_dir=False))

    def test_no_project_dir_variable_and_the_input_cwd_in_a_sub_folder(self):
        sub = self.guarded / "src/deep"
        sub.mkdir(parents=True)
        self.blocked(self.hook("pre", {**PUSH, "cwd": str(sub)}, self.guarded, cwd=sub, env_dir=False))

    def test_no_project_dir_variable_and_a_sub_folder_of_a_project_that_is_not_a_git_repo(self):
        root = self.base / "no-git-guarded"
        (root / MANIFEST).parent.mkdir(parents=True)
        (root / MANIFEST).write_text("{}")
        sub = root / "src"
        sub.mkdir()
        self.blocked(self.hook("pre", {**PUSH, "cwd": str(sub)}, root, cwd=sub, env_dir=False))

    def test_the_input_cwd_outside_the_project(self):
        """CLAUDE_PROJECT_DIR is the guarded project; the input says the call runs somewhere else."""
        self.blocked(self.hook("pre", {**PUSH, "cwd": str(self.plain)}, self.guarded, cwd=self.guarded))
        self.blocked(self.hook("pre", {**PUSH, "cwd": "/"}, self.guarded, cwd=self.guarded))
        self.blocked(self.hook("pre", {**PUSH, "cwd": str(self.base / "gone")}, self.guarded, cwd=self.guarded))

    def test_the_project_dir_variable_pointing_at_an_unguarded_folder(self):
        """The input cwd (and the process folder) are in the guarded project."""
        proc = self.hook("pre", {**PUSH, "cwd": str(self.guarded)}, self.plain, cwd=self.guarded)
        self.blocked(proc)

    def test_the_process_folder_in_the_guarded_project_and_no_other_hint(self):
        """No variable, and an input cwd outside: the process folder still says guarded."""
        self.blocked(self.hook("pre", {**PUSH, "cwd": str(self.plain)}, self.guarded, cwd=self.guarded, env_dir=False))

    def test_a_link_to_the_guarded_project(self):
        link = self.base / "link"
        link.symlink_to(self.guarded)
        self.blocked(self.hook("pre", {**PUSH, "cwd": str(link)}, link, cwd=link))
        self.blocked(self.hook("pre", {**PUSH, "cwd": str(link)}, self.plain, cwd=self.plain))

    def test_a_cwd_of_the_wrong_type(self):
        for cwd in (5, ["x"], {"a": 1}, True):
            with self.subTest(cwd=cwd):
                payload = json.dumps({**PUSH, "cwd": cwd, "session_id": "s-1"})
                proc = self.hook("pre", payload, self.guarded)
                self.assertEqual(proc.returncode, 2, proc.stderr)

    def test_a_cwd_with_a_null_byte(self):
        payload = json.dumps({**PUSH, "cwd": "/nowhere/x\0y", "session_id": "s-1"})
        self.assertEqual(self.hook("pre", payload, self.guarded).returncode, 2)

    def test_the_prompt_hook_in_the_same_odd_places(self):
        prompt = {"hook_event_name": "UserPromptSubmit", "prompt": f"use this token {FAKE_KEY}"}
        for kwargs in (
            {"env_dir": False},
            {"cwd": self.guarded},
        ):
            with self.subTest(**kwargs):
                proc = self.hook("prompt", {**prompt, "cwd": str(self.plain)}, self.guarded, **kwargs)
                self.assertEqual(json.loads(proc.stdout)["decision"], "block")
        payload = json.dumps({**prompt, "cwd": 5})
        self.assertEqual(json.loads(self.hook("prompt", payload, self.guarded).stdout)["decision"], "block")

    def test_session_start_in_the_same_odd_places(self):
        start = {"hook_event_name": "SessionStart", "source": "startup"}
        proc = self.hook("start", {**start, "cwd": str(self.plain)}, self.guarded, cwd=self.guarded)
        self.assertIn("core.hooksPath", json.loads(proc.stdout)["systemMessage"])


if __name__ == "__main__":
    unittest.main()
