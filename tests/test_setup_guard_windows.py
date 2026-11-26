# /// script
# requires-python = ">=3.10"
# ///
"""Windows mode of scripts/setup_guard.py (GUARD-3 T9, ADR 0009), plus the .gitattributes lines (all OSes).

A simulation on this OS: `sys.platform` is patched to "win32", `icacls` and Git Bash are fakes. Nothing here has run on
Windows: docs/guides/windows-check.md lists what a real machine must confirm. Fake values only.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests.test_setup_guard as t
from tests.windows_flavour import POSIX_ONLY

sg = t.sg
HOOK = "_bmad/custom/guard/hooks/pre-commit"
FAKE_BASH = "/fake/Git/bin/bash.exe"


def windows():
    """sys.platform is win32 for the setup script and for the sender it loads."""
    return mock.patch.object(sg.sys, "platform", "win32")


def bash_ok(version: str = "(3, 12)"):
    """A runner that answers like Git Bash with a working python3."""
    return mock.Mock(return_value=mock.Mock(returncode=0, stdout=version + "\n"))


class WindowsPlanTests(t.Base):
    def go(self, *flags: str):
        with windows(), mock.patch.object(sg, "windows_report", return_value=(["ok       fake Git Bash"], True)):
            return t.run(self.project, self.module, *flags)

    def hooks_path(self) -> str:
        return subprocess.run(
            ["git", "config", "--local", "core.hooksPath"], cwd=self.project, capture_output=True, text=True
        ).stdout.strip()

    def test_no_git_hooks_are_copied_and_no_hooks_path_is_set(self):
        code, out, _ = self.go()
        self.assertEqual(code, 0, out)
        self.assertFalse((self.project / HOOK).exists())
        self.assertFalse((self.project / sg.HOOKS_REL).exists())
        self.assertEqual(self.hooks_path(), "")
        self.assertIn("L2 git hooks are not installed on Windows", out)
        self.assertNotIn("core.hooksPath", out.replace("L2 git hooks", ""))
        self.assertTrue(all(not rel.startswith(sg.HOOKS_REL) for rel in self.manifest()["files"]))

    def test_the_skills_and_the_wiring_are_still_installed(self):
        self.go()
        self.assertTrue((self.project / ".claude/skills/guard-a/SKILL.md").is_file())
        self.assertTrue((self.project / "_bmad/custom/bmad-prd.toml").is_file())
        self.assertIn("guard-a", " ".join(self.manifest()["files"]))

    def test_a_second_run_and_check_are_up_to_date_without_the_hooks_path(self):
        self.go()
        code, out, _ = self.go("--check")
        self.assertEqual(code, 0, out)
        self.assertIn("up to date", out)
        self.assertEqual(self.go()[1].count("create"), 0)

    @POSIX_ONLY
    def test_off_windows_the_hooks_are_still_installed(self):
        """Control: the same module on this OS copies the hook and sets core.hooksPath."""
        t.run(self.project, self.module)
        self.assertTrue((self.project / HOOK).is_file())
        self.assertEqual(self.hooks_path(), sg.HOOKS_REL)

    def test_a_project_set_up_on_another_os_keeps_its_git_hooks_when_windows_runs_setup(self):
        t.run(self.project, self.module)  # POSIX setup: hook file, manifest entry and core.hooksPath
        manifest = self.manifest()
        self.assertIn(HOOK, manifest["files"])
        code, out, _ = self.go("--check")
        self.assertEqual(code, 0, out)  # no "remove" action for the hook: Windows does not manage it
        self.assertNotIn("remove", out)
        code, out, _ = self.go()
        self.assertEqual(code, 0, out)
        self.assertTrue((self.project / HOOK).is_file())
        self.assertEqual(self.hooks_path(), sg.HOOKS_REL)
        self.assertEqual(self.manifest()["files"][HOOK], manifest["files"][HOOK])

    def test_uninstall_on_windows_removes_what_the_manifest_lists(self):
        self.go()
        code, out, _ = self.go("--uninstall")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.project / sg.MANIFEST_REL).exists())
        self.assertFalse((self.project / ".claude/skills/guard-a").exists())

    def test_the_matcher_with_powershell_is_written(self):
        shutil.copytree(t.ROOT / "module/skills/tool-guard", self.module / "module/skills/tool-guard")
        self.go()
        settings = json.loads((self.project / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertIn("PowerShell", settings["hooks"]["PreToolUse"][0]["matcher"].split("|"))

    def test_check_has_no_not_supported_line_on_windows(self):
        self.go()
        out = self.go("--check")[1]
        self.assertNotIn("not supported", out)
        self.assertNotIn("D10", out)


class WindowsReportTests(unittest.TestCase):
    """`--check` on Windows: Git Bash is there, and python3 in Git Bash is 3.10 or later."""

    def report(self, runner, bash: str | None = FAKE_BASH):
        return sg.windows_report(runner=runner, find=lambda: bash)

    def test_all_good(self):
        runner = bash_ok("(3, 12)")
        lines, ok = self.report(runner)
        self.assertTrue(ok)
        self.assertEqual(lines, [f"ok       Git Bash: {FAKE_BASH}", "ok       python3 in Git Bash: 3.12"])
        call = runner.call_args.args[0]
        self.assertEqual(call[:2], [FAKE_BASH, "-c"])
        self.assertEqual(call[2], 'python3 -c "import sys; print(sys.version_info[:2])"')

    def test_python_3_10_is_enough_and_3_9_is_not(self):
        self.assertTrue(self.report(bash_ok("(3, 10)"))[1])
        lines, ok = self.report(bash_ok("(3, 9)"))
        self.assertFalse(ok)
        self.assertIn("3.9", lines[-1])
        self.assertIn("3.10 or later", lines[-1])
        self.assertIn("python3.exe", lines[-1])

    def test_no_git_bash_is_a_clear_problem(self):
        runner = mock.Mock()
        lines, ok = self.report(runner, bash=None)
        self.assertFalse(ok)
        self.assertIn("Git for Windows", lines[0])
        self.assertIn("CLAUDE_CODE_GIT_BASH_PATH", lines[0])
        runner.assert_not_called()

    def test_python3_missing_or_the_store_stub(self):
        for code, text in (
            (127, ""),  # command not found
            (49, "Python was not found; run without arguments to install from the Microsoft Store"),
            (9009, ""),
            (0, ""),  # nothing printed
            (0, "something else\n"),
        ):
            with self.subTest(code=code):
                lines, ok = self.report(mock.Mock(return_value=mock.Mock(returncode=code, stdout=text)))
                self.assertFalse(ok)
                self.assertIn("python3 does not work in Git Bash", lines[-1])
                self.assertIn("python3.exe", lines[-1])
                self.assertIn("App execution aliases", lines[-1])

    def test_bash_that_cannot_run_is_a_problem_with_the_error_type_only(self):
        for error in (OSError("secret detail"), subprocess.TimeoutExpired("bash", 30)):
            with self.subTest(error=type(error).__name__):
                lines, ok = self.report(mock.Mock(side_effect=error))
                self.assertFalse(ok)
                self.assertIn(type(error).__name__, lines[-1])
                self.assertNotIn("secret detail", " ".join(lines))

    def test_the_check_exits_1_when_git_bash_is_missing_even_if_the_project_is_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "mod").mkdir()
            (base / "proj").mkdir()
            (base / "home").mkdir()
            module, project = t.make_module(base / "mod"), t.make_project(base / "proj")
            with mock.patch.dict(os.environ, {"HOME": str(base / "home")}), windows():
                with mock.patch.object(sg, "windows_report", return_value=(["ok       x"], True)):
                    self.assertEqual(t.run(project, module)[0], 0)
                broken = ["problem  Git for Windows (bash.exe) was not found."], False
                with mock.patch.object(sg, "windows_report", return_value=broken):
                    code, out, _ = t.run(project, module, "--check")
        self.assertEqual(code, 1)
        self.assertIn("problem  Git for Windows", out)
        self.assertIn("up to date", out)
        self.assertIn("Windows check failed", out)


class FindGitBashTests(unittest.TestCase):
    def find(self, existing: set[str], env: dict | None = None, git_exe: str | None = None):
        return sg.find_git_bash(env=env or {}, which=lambda name: git_exe, is_file=lambda p: p in existing)

    def test_the_variable_wins(self):
        env = {"CLAUDE_CODE_GIT_BASH_PATH": "/x/bash.exe"}
        self.assertEqual(self.find({"/x/bash.exe", "/g/Git/bin/bash.exe"}, env, "/g/Git/cmd/git.exe"), "/x/bash.exe")

    def test_it_is_found_next_to_git(self):
        self.assertEqual(self.find({"/g/Git/bin/bash.exe"}, git_exe="/g/Git/cmd/git.exe"), "/g/Git/bin/bash.exe")
        self.assertEqual(
            self.find({"/g/Git/bin/bash.exe"}, git_exe="/g/Git/mingw64/bin/git.exe"), "/g/Git/bin/bash.exe"
        )

    def test_it_is_found_in_the_usual_folders(self):
        env = {"ProgramFiles": "/pf"}
        self.assertEqual(self.find({"/pf/Git/bin/bash.exe"}, env), "/pf/Git/bin/bash.exe")
        env = {"LOCALAPPDATA": "/lad"}
        self.assertEqual(self.find({"/lad/Programs/Git/bin/bash.exe"}, env), "/lad/Programs/Git/bin/bash.exe")

    def test_the_wsl_bash_in_system32_is_never_picked(self):
        self.assertIsNone(self.find({"/Windows/System32/bash.exe"}, {"SystemRoot": "/Windows"}))

    def test_nothing_found(self):
        self.assertIsNone(self.find(set()))


class WindowsCredentialsTests(t.FakeHomeMixin, unittest.TestCase):
    """`--credentials` on Windows: the file is written, `icacls` is run on the folder and the file (best effort)."""

    ARGS = ("--credentials", "--endpoint", "https://collector.example.invalid", "--user", "dev-a")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.use_fake_home(Path(tmp.name).resolve())
        self.calls: list[list[str]] = []

    def go(self, *args: str, icacls_code: int | Exception = 0, user: str | None = "tester"):
        def fake_run(cmd, **kwargs):
            self.calls.append(cmd)
            if isinstance(icacls_code, Exception):
                raise icacls_code
            return mock.Mock(returncode=icacls_code, stdout="", stderr="")

        out, err = io.StringIO(), io.StringIO()
        env = {"BMAD_GUARD_KEY": t.FAKE_KEY}
        if user:
            env["USERNAME"] = user
        with (
            windows(),
            mock.patch.dict(os.environ, env),
            mock.patch.object(sg.subprocess, "run", fake_run),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            if not user:
                os.environ.pop("USERNAME", None)
            code = sg.main(list(args or self.ARGS))
        return code, out.getvalue(), err.getvalue()

    def test_it_is_not_refused_and_writes_the_file(self):
        code, out, err = self.go()
        self.assertEqual((code, err), (0, ""), out)
        text = self.cred.read_text(encoding="utf-8")
        self.assertIn("endpoint = https://collector.example.invalid", text)
        self.assertIn("user = dev-a", text)
        self.assertNotIn("\r", text)
        self.assertNotIn("not supported", out)
        self.assertTrue(out.rstrip().endswith("done"))

    def test_icacls_runs_on_the_folder_then_the_file_with_the_user_only(self):
        self.go()
        self.assertEqual(
            self.calls,
            [
                ["icacls", str(self.folder), "/inheritance:r", "/grant:r", "tester:(OI)(CI)F"],
                ["icacls", str(self.cred), "/inheritance:r", "/grant:r", "tester:F"],
            ],
        )

    def test_the_result_is_reported_and_the_key_is_never_shown(self):
        code, out, err = self.go()
        self.assertIn("acl      folder: only tester has access", out)
        self.assertIn("acl      file: only tester has access", out)
        self.assertNotIn(t.FAKE_KEY, out + err + json.dumps(self.calls))

    def test_a_failed_icacls_is_a_warning_and_the_exit_code_stays_0(self):
        for failure in (5, OSError("no icacls"), subprocess.TimeoutExpired("icacls", 30)):
            with self.subTest(failure=repr(failure)):
                self.calls.clear()
                code, out, _ = self.go(
                    "--credentials",
                    "--endpoint",
                    "https://collector.example.invalid",
                    "--user",
                    "dev-a",
                    "--force",
                    icacls_code=failure,
                )
                self.assertEqual(code, 0, out)
                self.assertEqual(out.count("warning  icacls failed"), 2)
                self.assertIn("readable by others", out)
                self.assertTrue(self.cred.is_file())

    def test_a_second_run_with_the_same_values_runs_icacls_again_so_it_can_repair_the_access(self):
        self.go()
        self.calls.clear()
        code, out, _ = self.go()
        self.assertEqual(code, 0, out)
        self.assertIn("keep", out)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("acl      folder", out)

    def test_no_username_skips_icacls_with_a_warning(self):
        code, out, _ = self.go(user=None)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls, [])
        self.assertIn("icacls skipped", out)

    def test_the_home_folder_is_the_user_profile_not_home(self):
        with (
            mock.patch.object(sg.Path, "home", return_value=self.home),
            mock.patch.dict(os.environ, {"HOME": "/c/Users/someone-else"}),
        ):
            with windows():
                self.assertEqual(sg.credentials_file(), self.cred)

    def test_the_sender_accepts_what_windows_setup_wrote(self):
        self.go()
        with windows():
            ge = sg.load_events(sg.DEFAULT_MODULE)
            values, problem = ge.read_credential_file(self.cred)
        self.assertEqual(problem, "")
        self.assertEqual(values["user"], "dev-a")

    def test_the_check_on_windows_reports_a_link_as_a_problem(self):
        self.go()
        real = os.lstat
        flagged = os.fspath(self.cred)

        def lstat(path, *args, **kwargs):
            if os.fspath(path) == flagged:
                return mock.Mock(st_file_attributes=1024)
            return real(path, *args, **kwargs)

        with windows(), mock.patch.object(os, "lstat", lstat):
            lines = sg.credential_report(sg.DEFAULT_MODULE)
        self.assertEqual(len(lines), 1)
        self.assertIn("link or junction", lines[0])
        self.assertTrue(lines[0].startswith("problem"))

    @POSIX_ONLY
    def test_off_windows_icacls_is_never_run(self):
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"BMAD_GUARD_KEY": t.FAKE_KEY}),
            mock.patch.object(sg.subprocess, "run", side_effect=AssertionError("icacls")),
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(sg.main(list(self.ARGS)), 0)
        self.assertNotIn("acl ", out.getvalue())


class GitattributesTests(t.Base):
    """Setup keeps the files it installs LF in a clone with core.autocrlf=true, so the manifest hashes match."""

    def attributes(self) -> str:
        return (self.project / ".gitattributes").read_text(encoding="utf-8")

    def setUp(self):
        super().setUp()
        # The fake skill files have no line end, so CRLF could not change them: give the module one that has.
        t.write(self.module / "module/skills/guard-a/references/notes.md", "line one\nline two\n")
        self.lines = sg.gitattributes_lines(sg.module_files(self.module, self.project))

    def test_the_lines_cover_only_what_setup_installs(self):
        self.assertEqual(
            self.lines,
            [
                ".agents/skills/guard-a/** text eol=lf",
                ".claude/skills/guard-a/** text eol=lf",
                "_bmad/custom/bmad-prd.toml text eol=lf",
                "_bmad/custom/guard/** text eol=lf",
            ],
        )

    def test_a_stock_file_next_to_the_guard_skills_is_not_touched(self):
        """Found by the install test: a broad `.claude/skills/**` line made git see BMAD's own CRLF files as modified.
        Real git: a CRLF stock file is committed first; setup must not change how git sees it."""
        stock = self.project / ".claude/skills/bmad-stock/assets/methods.csv"
        stock.parent.mkdir(parents=True)
        stock.write_bytes(b"a,b\r\n1,2\r\n")
        ident = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        t.git(self.project, *ident, "add", "-A")
        t.git(self.project, *ident, "commit", "-qm", "BMAD")
        t.run(self.project, self.module)
        status = t.git(self.project, "status", "--porcelain", "--", ".claude/skills/bmad-stock")
        self.assertEqual(status, "")
        attrs = t.git(self.project, "check-attr", "text", "eol", "--", ".claude/skills/bmad-stock/assets/methods.csv")
        self.assertNotIn(": lf", attrs)
        self.assertNotIn("text: set", attrs)
        ours = t.git(self.project, "check-attr", "text", "eol", "--", ".claude/skills/guard-a/SKILL.md")
        self.assertIn("text: set", ours)
        self.assertIn("eol: lf", ours)

    def commit_and_clone(self, drop: str | None = None) -> Path:
        """Commit the project (without the .gitattributes lines that contain `drop`; no .gitattributes at all for
        drop="") and clone it as a Windows user would: core.autocrlf=true."""
        if drop is not None:
            path = self.project / ".gitattributes"
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if drop not in line]
            if drop == "":
                path.unlink()
            else:
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ident = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        t.git(self.project, *ident, "add", "-A")
        t.git(self.project, *ident, "commit", "-qm", "setup")
        clone = Path(self._tmp.name) / "clone"
        subprocess.run(
            ["git", "clone", "-q", "-c", "core.autocrlf=true", str(self.project), str(clone)],
            check=True,
            capture_output=True,
        )
        t.git(clone, "config", "--local", "core.hooksPath", sg.HOOKS_REL)  # local config is not cloned
        return clone

    def test_setup_writes_the_lines_with_a_comment_and_a_second_run_changes_nothing(self):
        code, out, _ = t.run(self.project, self.module)
        self.assertEqual(code, 0, out)
        text = self.attributes()
        for line in self.lines:
            self.assertIn(line + "\n", text)
        self.assertEqual(text.splitlines()[0], sg.GITATTRIBUTES_COMMENT)
        self.assertNotIn("\r", text)
        self.assertEqual(t.run(self.project, self.module)[0], 0)
        self.assertEqual(self.attributes(), text)
        self.assertEqual(t.run(self.project, self.module, "--check")[0], 0)

    def test_existing_lines_stay_and_only_missing_lines_are_added(self):
        first = self.lines[0]
        (self.project / ".gitattributes").write_text("*.png binary\n" + first, encoding="utf-8")  # no final newline
        t.run(self.project, self.module)
        lines = self.attributes().splitlines()
        self.assertEqual(lines[:2], ["*.png binary", first])
        self.assertEqual([line for line in lines if line in self.lines], self.lines)  # each once
        self.assertEqual(lines.count(first), 1)

    def test_a_dry_run_names_it_and_writes_nothing(self):
        code, out, _ = t.run(self.project, self.module, "--dry-run")
        self.assertIn("append   .gitattributes", out)
        self.assertFalse((self.project / ".gitattributes").exists())

    def test_check_is_not_up_to_date_while_the_lines_are_missing(self):
        t.run(self.project, self.module)
        (self.project / ".gitattributes").unlink()
        code, out, _ = t.run(self.project, self.module, "--check")
        self.assertEqual(code, 1)
        self.assertIn("append   .gitattributes", out)

    def test_check_warns_about_crlf_only_when_autocrlf_is_true_and_the_lines_are_missing(self):
        t.run(self.project, self.module)
        (self.project / ".gitattributes").unlink()
        self.assertNotIn("autocrlf", t.run(self.project, self.module, "--check")[1])
        t.git(self.project, "config", "core.autocrlf", "true")
        out = t.run(self.project, self.module, "--check")[1]
        self.assertIn("core.autocrlf is true", out)
        self.assertIn("CRLF", out)
        t.run(self.project, self.module)
        self.assertNotIn("autocrlf", t.run(self.project, self.module, "--check")[1])

    def test_a_clone_with_autocrlf_true_is_up_to_date(self):
        """The case the lines are for: without them git writes CRLF and every hash differs."""
        t.run(self.project, self.module)
        clone = self.commit_and_clone()
        code, out, _ = t.run(clone, self.module, "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("up to date", out)

    def test_without_the_lines_a_clone_with_autocrlf_true_has_changed_files(self):
        """Control: the same clone with no .gitattributes lines gets CRLF files, so the check finds conflicts."""
        t.run(self.project, self.module)
        clone = self.commit_and_clone(drop="")
        code, out, _ = t.run(clone, self.module, "--check")
        self.assertEqual(code, 1, out)
        self.assertIn("changed since install", out)

    def test_the_skill_files_need_their_lines(self):
        t.run(self.project, self.module)
        clone = self.commit_and_clone(drop="skills")
        code, out, _ = t.run(clone, self.module, "--check")
        self.assertEqual(code, 1, out)
        self.assertIn("references/notes.md (changed since install)", out)

    def test_the_override_files_need_their_line_too(self):
        t.run(self.project, self.module)
        clone = self.commit_and_clone(drop="bmad-prd.toml")
        code, out, _ = t.run(clone, self.module, "--check")
        self.assertEqual(code, 1, out)
        self.assertIn("_bmad/custom/bmad-prd.toml (changed since install)", out)

    def test_uninstall_removes_our_block_and_keeps_the_rest(self):
        (self.project / ".gitattributes").write_text("*.png binary\n", encoding="utf-8")
        t.run(self.project, self.module)
        code, out, _ = t.run(self.project, self.module, "--uninstall")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.attributes(), "*.png binary\n")

    def test_uninstall_deletes_the_file_when_nothing_else_is_in_it(self):
        t.run(self.project, self.module)
        t.run(self.project, self.module, "--uninstall")
        self.assertFalse((self.project / ".gitattributes").exists())

    def test_uninstall_dry_run_lists_it_and_changes_nothing(self):
        t.run(self.project, self.module)
        before = self.attributes()
        out = t.run(self.project, self.module, "--uninstall", "--dry-run")[1]
        self.assertIn(".gitattributes", out)
        self.assertEqual(self.attributes(), before)

    def test_lines_without_our_comment_are_the_projects_own_until_force(self):
        first = self.lines[0]
        (self.project / ".gitattributes").write_text(first + "\n", encoding="utf-8")
        t.run(self.project, self.module)
        text = self.attributes()
        # Setup added the other lines (the first was already there), under its comment.
        self.assertEqual(text.count(first), 1)
        out = t.run(self.project, self.module, "--uninstall")[1]
        self.assertEqual(self.attributes(), first + "\n")
        self.assertIn("keep     .gitattributes", out)
        t.run(self.project, self.module)
        t.run(self.project, self.module, "--uninstall", "--force")
        self.assertFalse((self.project / ".gitattributes").exists())


class Utf8FilesTests(t.Base):
    def test_files_are_read_and_written_as_utf8_whatever_the_locale(self):
        """The default text encoding on Windows is cp1252. A non-ASCII name in .gitignore must survive a setup run."""
        (self.project / ".gitignore").write_text("café/\n", encoding="utf-8")
        with mock.patch("locale.getpreferredencoding", return_value="cp1252"):
            self.assertEqual(t.run(self.project, self.module)[0], 0)
            self.assertEqual(t.run(self.project, self.module, "--check")[0], 0)
        self.assertIn("café/", (self.project / ".gitignore").read_text(encoding="utf-8"))

    def test_the_manifest_is_written_with_lf(self):
        t.run(self.project, self.module)
        self.assertNotIn(b"\r", (self.project / sg.MANIFEST_REL).read_bytes())

    def test_git_output_is_read_as_utf8(self):
        with mock.patch.object(sg.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="x")
            sg.git(["status"], Path("."))
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "replace")


if __name__ == "__main__":
    unittest.main()
