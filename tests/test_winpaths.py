# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for module/skills/tool-guard/scripts/winpaths.py, in a simulated Windows flavour (ntpath) and on POSIX.

Fake names only. A simulation checks our string logic; it does not prove behaviour on Windows.
"""

from __future__ import annotations

import unittest

from tests.windows_flavour import HOME, TEMP, windows, winpaths


class NativeTests(unittest.TestCase):
    def test_git_bash_cygwin_and_wsl_paths_become_drive_paths(self):
        with windows():
            for text, want in (
                ("/c/Users/x/.aws/credentials", "C:\\Users\\x\\.aws\\credentials"),
                ("/cygdrive/c/Users/x", "C:\\Users\\x"),
                ("/mnt/d/proj/.env", "D:\\proj\\.env"),
                ("/c", "C:\\"),
                ("/c/", "C:\\"),
            ):
                with self.subTest(text=text):
                    self.assertEqual(winpaths.native(text), want)

    def test_other_paths_are_left_alone(self):
        with windows():
            for text in ("/usr/bin/env", "/dev/null", "C:\\proj\\x", "proj/x", "~/x", "/cache/x", ""):
                with self.subTest(text=text):
                    self.assertEqual(winpaths.native(text), text)

    def test_tmp_is_the_temp_folder(self):
        with windows():
            self.assertEqual(winpaths.native("/tmp/a/b"), TEMP + "\\a\\b")
            self.assertEqual(winpaths.native("/tmp"), TEMP + "\\")
            self.assertEqual(winpaths.native("/tmpx/a"), "/tmpx/a")

    def test_device_paths(self):
        with windows():
            self.assertTrue(winpaths.is_device("\\\\.\\PhysicalDrive0"))
            self.assertTrue(winpaths.is_device("//./C:"))
            self.assertFalse(winpaths.is_device("\\\\?\\C:\\x"))
            self.assertFalse(winpaths.is_device("C:\\x"))

    def test_posix_is_unchanged(self):
        for text in ("/c/Users/x", "/cygdrive/c/x", "/tmp/a", "\\\\.\\PhysicalDrive0"):
            with self.subTest(text=text):
                self.assertEqual(winpaths.native(text), text)
        self.assertFalse(winpaths.is_device("\\\\.\\PhysicalDrive0"))


class StripTests(unittest.TestCase):
    def test_tricks_are_removed(self):
        with windows():
            for text, want in (
                ("C:\\proj\\_bmad\\custom\\approval-state.json::$DATA", "C:\\proj\\_bmad\\custom\\approval-state.json"),
                ("C:\\proj\\approval-state.json:stream", "C:\\proj\\approval-state.json"),
                ("C:\\proj\\approval-state.json.", "C:\\proj\\approval-state.json"),
                ("C:\\proj\\.env ", "C:\\proj\\.env"),
                ("C:\\proj\\.env. .", "C:\\proj\\.env"),
                ("C:\\proj\\.claude.\\settings.json", "C:\\proj\\.claude\\settings.json"),
                ("\\\\?\\C:\\proj\\x", "C:\\proj\\x"),
                ("C:/proj/x.", "C:\\proj\\x"),
                ("C:\\proj\\..\\x", "C:\\proj\\..\\x"),
                ("C:\\proj\\.\\x", "C:\\proj\\.\\x"),
            ):
                with self.subTest(text=text):
                    self.assertEqual(winpaths.strip_win_tricks(text), want)

    def test_posix_is_unchanged(self):
        for text in ("/p/x.", "/p/x::$DATA", "/p/.env ", "\\\\?\\x"):
            with self.subTest(text=text):
                self.assertEqual(winpaths.strip_win_tricks(text), text)


class KeyTests(unittest.TestCase):
    def test_one_form_for_every_spelling(self):
        with windows():
            want = "c:\\proj\\_bmad\\custom\\approval-state.json"
            for text in (
                "C:\\proj\\_bmad\\custom\\approval-state.json",
                "c:\\PROJ\\_BMAD\\custom\\Approval-State.json",
                "C:/proj/_bmad/custom/approval-state.json",
                "C:\\proj\\_bmad\\custom\\approval-state.json::$DATA",
                "C:\\proj\\_bmad\\custom\\approval-state.json.",
                "\\\\?\\C:\\proj\\_bmad\\custom\\approval-state.json",
                "C:\\proj\\_bmad\\x\\..\\custom\\approval-state.json",
                "/c/proj/_bmad/custom/approval-state.json",
            ):
                with self.subTest(text=text):
                    self.assertEqual(winpaths.key(text), want)

    def test_posix_key_keeps_case(self):
        self.assertEqual(winpaths.key("/proj/./Docs/../X"), "/proj/X")


class NameTests(unittest.TestCase):
    def test_last_name_on_both_separators(self):
        with windows():
            for text, want in (
                ("C:\\proj\\.env.local", ".env.local"),
                ("C:/proj\\app/.env", ".env"),
                ("C:\\proj\\.env\\", ".env"),
                ("C:\\proj\\.env ", ".env"),
                ("C:\\proj\\.env::$DATA", ".env"),
                ("~\\.ssh\\id_rsa", "id_rsa"),
                ("id_rsa", "id_rsa"),
            ):
                with self.subTest(text=text):
                    self.assertEqual(winpaths.name_of(text), want)

    def test_posix_splits_on_slash_only(self):
        self.assertEqual(winpaths.name_of("/p/.env/"), ".env")
        self.assertEqual(winpaths.name_of("a\\.env"), "a\\.env")
        self.assertEqual(winpaths.name_of("/p/x. "), "x. ")


class StartDirTests(unittest.TestCase):
    def test_windows_uses_a_native_absolute_cwd_and_falls_back_to_the_root(self):
        with windows():
            self.assertEqual(winpaths.start_dir("C:\\proj\\sub", "ROOT"), "C:\\proj\\sub")
            self.assertEqual(winpaths.start_dir("/c/proj/sub", "ROOT"), "C:\\proj\\sub")
            for bad in ("", None, 5, "sub\\x", "relative"):
                with self.subTest(cwd=bad):
                    self.assertEqual(winpaths.start_dir(bad, "ROOT"), "ROOT")

    def test_posix_is_what_the_guards_did_before(self):
        self.assertEqual(winpaths.start_dir("/p/sub", "ROOT"), "/p/sub")
        for bad in ("", None, 5, "sub", "C:\\x"):
            with self.subTest(cwd=bad):
                self.assertEqual(winpaths.start_dir(bad, "ROOT"), "ROOT")


class ExpandHomeTests(unittest.TestCase):
    def test_windows_forms(self):
        env = {"APPDATA": "C:\\Users\\tester\\AppData\\Roaming", "LOCALAPPDATA": "C:\\Users\\tester\\AppData\\Local"}
        with windows(env=env):
            for text, want in (
                ("~", HOME),
                ("~/.ssh/id_rsa", HOME + "/.ssh/id_rsa"),
                ("~\\.ssh\\id_rsa", HOME + "\\.ssh\\id_rsa"),
                ("$HOME/.aws", HOME + "/.aws"),
                ("${HOME}/.aws", HOME + "/.aws"),
                ("$USERPROFILE/.aws", HOME + "/.aws"),
                ("${USERPROFILE}\\.aws", HOME + "\\.aws"),
                ("%USERPROFILE%\\.aws", HOME + "\\.aws"),
                ("%userprofile%\\.aws", HOME + "\\.aws"),
                ("$env:USERPROFILE\\.aws", HOME + "\\.aws"),
                ("$APPDATA\\x", env["APPDATA"] + "\\x"),
                ("$env:LOCALAPPDATA\\x", env["LOCALAPPDATA"] + "\\x"),
                ("$USERPROFILEX/x", "$USERPROFILEX/x"),
                ("~user/x", "~user/x"),
                ("proj/x", "proj/x"),
            ):
                with self.subTest(text=text):
                    self.assertEqual(winpaths.expand_home(text, HOME), want)

    def test_an_unset_variable_stays_as_it_is(self):
        with windows(env={"APPDATA": ""}):
            self.assertEqual(winpaths.expand_home("$APPDATA\\x", HOME), "$APPDATA\\x")

    def test_posix_expands_only_the_old_forms(self):
        for text, want in (
            ("~", "/h"),
            ("~/x", "/h/x"),
            ("$HOME/x", "/h/x"),
            ("${HOME}/x", "/h/x"),
            ("~\\x", "~\\x"),
            ("$USERPROFILE/x", "$USERPROFILE/x"),
            ("%USERPROFILE%/x", "%USERPROFILE%/x"),
        ):
            with self.subTest(text=text):
                self.assertEqual(winpaths.expand_home(text, "/h"), want)


if __name__ == "__main__":
    unittest.main()
