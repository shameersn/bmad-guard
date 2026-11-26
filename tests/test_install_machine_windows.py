# /// script
# requires-python = ">=3.10"
# ///
"""Windows branch of scripts/install_machine.py (GUARD-3 T9, ADR 0009). Built and unit-tested, not run on a machine.

A simulation on this OS: `sys.platform` is patched to "win32"; `icacls`, `IsUserAnAdmin` and Git Bash are fakes; the
folders are temp folders. Fake data only.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

import tests.test_install_machine as tim

im, wiring, ROOT = tim.im, tim.wiring, tim.ROOT
VERSION = tim.VERSION
WIN_BASE = "C:/Program Files/ClaudeCode/bmad-guard"

# What `icacls "C:\Program Files\ClaudeCode\bmad-guard"` prints on a default Windows 11 (English), trimmed.
FOLDER = "C:\\Program Files\\ClaudeCode\\bmad-guard"
GOOD = f"""{FOLDER} NT SERVICE\\TrustedInstaller:(CI)(F)
{" " * (len(FOLDER) + 1)}NT AUTHORITY\\SYSTEM:(OI)(CI)(F)
{" " * (len(FOLDER) + 1)}BUILTIN\\Administrators:(OI)(CI)(F)
{" " * (len(FOLDER) + 1)}BUILTIN\\Users:(OI)(CI)(RX)
{" " * (len(FOLDER) + 1)}CREATOR OWNER:(OI)(CI)(IO)(F)

Successfully processed 1 files; Failed processing 0 files
"""


def with_users(rights: str, who: str = "BUILTIN\\Users") -> str:
    return GOOD.replace("BUILTIN\\Users:(OI)(CI)(RX)", f"{who}:{rights}")


def windows():
    return mock.patch.object(im.sg.sys, "platform", "win32")


def icacls_says(text: str, code: int = 0):
    return mock.patch.object(im.subprocess, "run", return_value=mock.Mock(returncode=code, stdout=text, stderr=""))


class AclTests(unittest.TestCase):
    def test_the_default_access_list_is_fine(self):
        self.assertEqual(im.acl_write_problems(GOOD, FOLDER), [])

    def test_write_rights_for_users_everyone_or_authenticated_users_are_found(self):
        for rights, who in (
            ("(OI)(CI)(M)", "BUILTIN\\Users"),
            ("(OI)(CI)(F)", "BUILTIN\\Users"),
            ("(CI)(WD,AD,WEA,WA)", "BUILTIN\\Users"),
            ("(OI)(CI)(W)", "BUILTIN\\Users"),
            ("(OI)(CI)(AD)", "BUILTIN\\Users"),
            ("(I)(OI)(CI)(M)", "NT AUTHORITY\\Authenticated Users"),
            ("(OI)(CI)(F)", "Everyone"),
        ):
            with self.subTest(who=who, rights=rights):
                found = im.acl_write_problems(with_users(rights, who), FOLDER)
                self.assertEqual(len(found), 1, found)
                self.assertIn(who, found[0])

    def test_read_rights_and_deny_entries_and_other_principals_are_not_found(self):
        for rights, who in (
            ("(OI)(CI)(RX)", "BUILTIN\\Users"),
            ("(OI)(CI)(R)", "Everyone"),
            ("(OI)(CI)(DENY)(W)", "BUILTIN\\Users"),
            ("(OI)(CI)(M)", "BUILTIN\\Administrators"),
            ("(OI)(CI)(M)", "CONTOSO\\Domain Users"),
            ("(OI)(CI)(F)", "CREATOR OWNER"),
        ):
            with self.subTest(who=who, rights=rights):
                self.assertEqual(im.acl_write_problems(with_users(rights, who), FOLDER), [])

    def test_text_with_no_entry_cannot_be_read(self):
        for text in ("", "Successfully processed 1 files; Failed processing 0 files\n", "Zugriff verweigert\n"):
            with self.subTest(text=text):
                self.assertIsNone(im.acl_write_problems(text, FOLDER))

    def test_the_folder_name_with_a_space_does_not_confuse_the_first_line(self):
        self.assertEqual(
            im.acl_write_problems(f"{FOLDER} BUILTIN\\Users:(OI)(CI)(M)\n", FOLDER), ["BUILTIN\\Users (OI)(CI)(M)"]
        )

    def test_the_check_uses_icacls_and_reports(self):
        target = Path(FOLDER)  # a plain path object; icacls is a fake
        with windows(), icacls_says(with_users("(OI)(CI)(M)")) as run:
            problems = im.safety_problems(target, 0)
        self.assertEqual(run.call_args.args[0], ["icacls", str(target)])
        self.assertEqual(len(problems), 1)
        self.assertIn("write access for BUILTIN\\Users", problems[0])
        with windows(), icacls_says(GOOD):
            self.assertEqual(im.safety_problems(target, 0), [])

    def test_an_access_list_that_cannot_be_read_is_a_note_to_check_by_hand(self):
        for patch in (
            icacls_says("garbage"),
            icacls_says("", 5),
            mock.patch.object(im.subprocess, "run", side_effect=OSError),
        ):
            with self.subTest(patch=repr(patch)), windows(), patch:
                problems, notes = im.windows_safety_problems(Path(FOLDER))
            self.assertEqual(problems, [])
            self.assertIn("check it by hand", notes[0])


class WindowsInstallTests(tim.Base):
    """The same install, check and uninstall, with the Windows branch: no chmod, no chown, renames retried."""

    def setUp(self):
        super().setUp()
        self.patches = [
            windows(),
            mock.patch.object(im, "is_admin", return_value=True),
            mock.patch.object(im.sg, "windows_report", return_value=(["ok       python3 in Git Bash: 3.12"], True)),
            mock.patch.object(im.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=GOOD)),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_install_makes_the_folder_without_chmod_or_chown(self):
        boom = AssertionError("not on Windows")
        with (
            mock.patch.object(os, "chmod", side_effect=boom),
            mock.patch.object(os, "lchown", side_effect=boom, create=True),
        ):
            code, out, err = self.install()
        self.assertEqual(code, 0, out + err)
        self.assertTrue((self.target / "skills/tool-guard/scripts/pre_tool_use.py").is_file())
        self.assertTrue((self.target / "skills/tool-guard/scripts/winpaths.py").is_file())
        self.assertEqual((self.target / "VERSION").read_text(encoding="utf-8").strip(), VERSION)
        self.assertNotIn("warning: not running as", out)

    def test_a_second_install_changes_nothing(self):
        self.installed()
        code, out, _ = self.install()
        self.assertEqual(code, 0, out)
        self.assertIn("unchanged", out)

    def test_not_elevated_gets_a_clear_warning(self):
        with mock.patch.object(im, "is_admin", return_value=False):
            code, out, _ = self.install("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("elevated terminal (Administrator)", out)
        self.assertNotIn("root", out)

    def test_the_rename_is_retried_when_a_scanner_holds_the_files(self):
        self.installed()
        module = self.module_copy()
        (module / "module/skills/tool-guard/scripts/extra.py").write_text("X = 1\n", encoding="utf-8")
        real, calls = os.rename, {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError("held by another process")
            real(src, dst)

        with mock.patch.object(os, "rename", flaky), mock.patch.object(im.time, "sleep"):
            code, out, err = self.install(module=module)
        self.assertEqual(code, 0, out + err)
        self.assertGreater(calls["n"], 2)
        self.assertTrue((self.target / "skills/tool-guard/scripts/extra.py").is_file())

    def test_a_rename_that_never_works_is_an_error_and_the_old_folder_is_put_back(self):
        self.installed()
        module = self.module_copy()
        (module / "module/skills/tool-guard/scripts/extra.py").write_text("X = 1\n", encoding="utf-8")
        before = (self.target / "VERSION").read_text(encoding="utf-8")
        real = os.rename

        def never(src, dst):
            if Path(src).name == "bmad-guard.new":
                raise PermissionError("held")
            real(src, dst)

        with mock.patch.object(os, "rename", never), mock.patch.object(im.time, "sleep"):
            code, _, err = self.install(module=module)
        self.assertEqual(code, 2)
        self.assertIn("PermissionError", err)
        self.assertEqual((self.target / "VERSION").read_text(encoding="utf-8"), before)

    def test_off_windows_a_rename_is_not_retried(self):
        with (
            mock.patch.object(im, "on_windows", return_value=False),
            mock.patch.object(os, "rename", side_effect=PermissionError) as rn,
        ):
            with self.assertRaises(PermissionError):
                im.rename_retry(Path("a"), Path("b"))
        self.assertEqual(rn.call_count, 1)

    def test_check_passes_with_a_good_access_list_and_a_working_python3(self):
        self.installed()
        code, out, err = self.check()
        self.assertEqual(code, 0, out + err)
        self.assertIn("Users, Everyone and Authenticated Users have no write access (icacls)", out)
        self.assertIn("python3 is 3.10 or later", out)
        self.assertNotIn("owned by uid", out)

    def test_check_fails_when_users_can_write(self):
        self.installed()
        with icacls_says(with_users("(OI)(CI)(M)")):
            code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("problem  write access for BUILTIN\\Users", out)

    def test_check_says_check_by_hand_when_the_access_list_is_unreadable_and_does_not_fail(self):
        self.installed()
        with icacls_says("garbage"):
            code, out, _ = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("check it by hand", out)

    def test_check_fails_when_python3_does_not_work_in_git_bash(self):
        self.installed()
        broken = (["ok       Git Bash: x", "problem  python3 does not work in Git Bash. Fix: copy python.exe"], False)
        with mock.patch.object(im.sg, "windows_report", return_value=broken):
            code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("problem  python3 does not work in Git Bash", out)

    def test_check_fails_when_the_files_changed(self):
        self.installed()
        (self.target / "skills/tool-guard/scripts/pre_tool_use.py").write_text("# edited\n", encoding="utf-8")
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("changed since the install", out)

    def test_uninstall_removes_the_folder(self):
        self.installed()
        code, out, err = self.run_main("--target", str(self.target), "--uninstall")
        self.assertEqual(code, 0, out + err)
        self.assertFalse(self.target.exists())

    def test_no_rights_is_a_clear_error_for_a_folder_we_cannot_write_to(self):
        with mock.patch.object(im.tempfile, "TemporaryFile", side_effect=PermissionError):
            code, _, err = self.install()
        self.assertEqual(code, 2)
        self.assertIn("elevated terminal", err)
        self.assertIn("Run as administrator", err)

    def test_the_rights_probe_leaves_no_file_behind(self):
        self.target.parent.mkdir(parents=True)
        self.assertIsNone(im.need_rights(self.target))
        self.assertEqual(list(self.target.parent.iterdir()), [])


class WindowsManagedFileTests(unittest.TestCase):
    """The managed file points into `C:/Program Files/...`; the target is a Windows path with backslashes."""

    class WinTarget(PureWindowsPath):
        def is_dir(self) -> bool:
            return True

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.managed = Path(tmp.name)
        self.target = self.WinTarget("C:\\Program Files\\ClaudeCode\\bmad-guard")

    def put(self, data: dict) -> None:
        (self.managed / "managed-settings.json").write_text(json.dumps(data), encoding="utf-8")

    def test_the_windows_template_points_here_even_though_the_path_has_backslashes(self):
        """Before: the base had backslashes, the base check refused it, and every Windows folder looked wrong."""
        self.put(json.loads((ROOT / "module/managed-settings/managed-settings.windows.json").read_text("utf-8")))
        problems, points_here = im.managed_problems(self.managed, self.target, wiring)
        self.assertEqual(problems, [])
        self.assertTrue(points_here)

    def test_another_folder_in_the_file_is_a_problem(self):
        self.put(wiring.managed_settings("C:/Other/bmad-guard", windows=True))
        problems, _ = im.managed_problems(self.managed, self.target, wiring)
        self.assertEqual(len(problems), 2)  # the hooks and the helper
        self.assertIn("does not run all three hooks", problems[0])


class WindowsMainTests(tim.Base):
    def test_print_template_windows_is_the_shipped_file_and_has_forward_slashes(self):
        code, out, _ = self.run_main("--print-template", "windows")
        self.assertEqual(code, 0)
        self.assertEqual(out, (ROOT / "module/managed-settings/managed-settings.windows.json").read_text("utf-8"))
        self.assertIn("C:/Program Files/ClaudeCode/bmad-guard/skills/tool-guard/scripts/pre_tool_use.py", out)
        self.assertIn('"CLAUDE_CODE_USE_POWERSHELL_TOOL": "0"', out)

    def test_print_template_current_on_windows_is_the_windows_file(self):
        with windows():
            code, out, _ = self.run_main("--print-template")
        self.assertEqual(code, 0)
        self.assertIn("C:/Program Files/ClaudeCode/bmad-guard", out)

    def test_print_template_with_a_target_uses_forward_slashes(self):
        code, out, _ = self.run_main("--print-template", "windows", "--target", str(self.target))
        self.assertEqual(code, 0)
        self.assertNotIn("\\\\", json.loads(out)["hooks"]["PreToolUse"][0]["hooks"][0]["command"])

    def test_windows_is_no_longer_refused(self):
        with windows(), mock.patch.object(im, "is_admin", return_value=True):
            code, out, err = self.install("--dry-run")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("not supported", out + err)

    def test_the_default_folder_on_windows_is_program_files(self):
        self.assertEqual(im.default_target(wiring, "win32").as_posix(), WIN_BASE)

    def test_is_admin_is_false_when_it_cannot_be_told(self):
        self.assertFalse(im.is_admin())  # no ctypes.windll on this OS
        fake = mock.Mock()
        fake.windll.shell32.IsUserAnAdmin.return_value = 1
        with mock.patch.dict(sys.modules, {"ctypes": fake}):
            self.assertTrue(im.is_admin())
        fake.windll.shell32.IsUserAnAdmin.return_value = 0
        with mock.patch.dict(sys.modules, {"ctypes": fake}):
            self.assertFalse(im.is_admin())

    def test_privileged_and_euid_off_windows_are_the_posix_ones(self):
        self.assertEqual(im.euid(), os.geteuid())
        self.assertEqual(im.privileged(), os.geteuid() == 0)
        with windows():
            self.assertEqual(im.euid(), 0)


if __name__ == "__main__":
    unittest.main()
