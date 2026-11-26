# /// script
# requires-python = ">=3.10"
# ///
"""The guards in a simulated Windows flavour (GUARD-3 T9, ADR 0009): `os.path` is ntpath, `winpaths.IS_WINDOWS` is on,
the home folder is `C:\\Users\\tester` and the project is `C:\\proj`. This checks our string logic on macOS and Linux.
It does not prove behaviour on Windows (docs/guides/windows-check.md). Fake paths only.

Each security case has a POSIX control: the same text on this OS, where nothing changes.
"""

from __future__ import annotations

import importlib
import unittest
from pathlib import Path

from tests.windows_flavour import HOME, windows

pg = importlib.import_module("path_guard")

PROJECT = "C:\\proj"
ROOT = Path(PROJECT)


def call(tool: str, tool_input: dict, **extra) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": PROJECT, **extra}


class WindowsPathGuardTests(unittest.TestCase):
    def codes(self, tool: str, tool_input: dict) -> list[str]:
        with windows():
            return [v.code for v in pg.check(call(tool, tool_input), ROOT)]

    def read(self, path: str) -> list[str]:
        return self.codes("Read", {"file_path": path})

    def bash(self, command: str) -> list[str]:
        return self.codes("Bash", {"command": command})

    def test_env_files_with_backslashes_block(self):
        for path in (
            "C:\\proj\\.env",
            "C:\\proj\\.env.local",
            "C:\\proj\\.env.production",
            "C:\\proj\\app\\prod.env",
            "C:/proj\\app/.env.local",  # both separators in one path
            "c:\\PROJ\\.ENV",
            "C:\\proj\\.Env.Local",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.read(path), ["PATH_ENV_FILE"])

    def test_env_files_with_windows_tricks_block(self):
        for path in (
            "C:\\proj\\.env ",  # trailing space
            "C:\\proj\\.env.",  # trailing dot
            "C:\\proj\\.env. .",
            "C:\\proj\\.env::$DATA",  # NTFS stream
            "C:\\proj\\.env:stream",
            "C:\\proj\\.env.local::$DATA",
            "\\\\?\\C:\\proj\\.env",
            "//?/C:/proj/.env",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.read(path), ["PATH_ENV_FILE"])

    def test_close_but_safe_names_are_allowed(self):
        for path in (
            "C:\\proj\\.env.example",
            "C:\\proj\\.env.example ",
            "C:\\proj\\README.md",
            "C:\\proj\\environment.py",
            "C:\\proj\\src\\env.py",
            "C:\\proj\\.envrc.md",
            "C:\\proj\\identity.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.read(path), [])

    def test_the_other_name_rules_use_both_separators_too(self):
        self.assertEqual(self.read("C:\\proj\\keys\\server.pem"), ["PATH_PRIVATE_KEY"])
        self.assertEqual(self.read("C:\\Users\\other\\.ssh\\id_ed25519"), ["PATH_PRIVATE_KEY"])
        self.assertEqual(self.read("C:\\proj\\db\\dump-2024.sql"), ["PATH_DB_DUMP"])
        self.assertEqual(self.read("C:\\proj\\a\\store.p12"), ["PATH_KEY_STORE"])

    def test_the_home_folders_block_with_every_spelling(self):
        for path in (
            f"{HOME}\\.aws\\config",
            f"{HOME}/.aws/config",
            "C:\\USERS\\TESTER\\.AWS\\config",
            "C:\\Users\\tester\\.aws.\\config",  # trailing dot in the folder name
            "C:\\Users\\tester\\.aws\\config::$DATA",
            "~\\.aws\\config",
            "~/.aws/config",
            "$HOME/.aws/config",
            "${HOME}/.aws/config",
            "$USERPROFILE\\.aws\\config",
            "$USERPROFILE/.aws/config",
            "${USERPROFILE}\\.aws\\config",
            "%USERPROFILE%\\.aws\\config",
            "%userprofile%\\.aws\\config",
            "$env:USERPROFILE\\.aws\\config",
            "/c/Users/tester/.aws/config",  # Git Bash
            "/cygdrive/c/Users/tester/.aws/config",
            "/mnt/c/Users/tester/.aws/config",
            "C:\\Users\\tester\\x\\..\\.aws\\config",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.read(path), ["PATH_AWS_DIR"])

    def test_ssh_and_the_guard_config_folder_block_with_git_bash_and_windows_forms(self):
        for path in ("/c/Users/tester/.ssh/known_hosts", "~\\.ssh\\config", "$USERPROFILE/.ssh/config"):
            with self.subTest(path=path):
                self.assertEqual(self.read(path), ["PATH_SSH_DIR"])
        for path in (
            "/c/Users/tester/.config/bmad-guard/credentials",
            "C:\\Users\\tester\\.config\\bmad-guard\\credentials",
            "$USERPROFILE\\.config\\bmad-guard\\credentials",
            "~\\.config\\bmad-guard\\credentials",
            "C:\\Users\\tester\\.config\\bmad-guard\\credentials::$DATA",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.read(path), ["PATH_GUARD_CONFIG"])

    def test_appdata_forms_expand_and_do_not_reach_other_folders(self):
        with windows(env={"APPDATA": "C:\\Users\\tester\\AppData\\Roaming"}):
            verdicts = pg.check(call("Read", {"file_path": "$APPDATA\\..\\..\\.ssh\\config"}), ROOT)
        self.assertEqual([v.code for v in verdicts], ["PATH_SSH_DIR"])

    def test_close_but_safe_folders_are_allowed(self):
        for path in (
            "C:\\Users\\tester\\.awsome\\x",
            "C:\\Users\\tester\\project\\.aws-notes.md",
            "C:\\Users\\tester\\.config\\other\\x",
            "C:\\Users\\other\\.aws\\config",  # another user's home is not this user's .aws
            "/c/Users/tester/Documents/notes.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.read(path), [])

    def test_bash_commands_with_git_bash_paths(self):
        self.assertEqual(self.bash("cat /c/Users/tester/.config/bmad-guard/credentials"), ["PATH_GUARD_CONFIG"])
        self.assertEqual(self.bash("cat /c/Users/tester/.ssh/id_rsa"), ["PATH_SSH_DIR", "PATH_PRIVATE_KEY"])
        self.assertEqual(self.bash("cat $USERPROFILE/.aws/credentials"), ["PATH_AWS_DIR"])
        self.assertEqual(self.bash("cat /c/proj/.env.local"), ["PATH_ENV_FILE"])
        self.assertEqual(self.bash("cat 'C:\\proj\\.env.local'"), ["PATH_ENV_FILE"])
        self.assertEqual(self.bash("cp ~/notes.txt out.txt > /c/Users/tester/.aws/config"), ["PATH_AWS_DIR"])
        self.assertEqual(self.bash("ls /c/Users/tester/Documents"), [])

    def test_grep_patterns_are_still_not_paths(self):
        self.assertEqual(self.bash("grep -rn '.env' src"), [])
        self.assertEqual(self.bash("echo /c/Users/tester/.aws/credentials"), [])

    def test_grep_and_glob_tools_use_the_same_rules(self):
        self.assertEqual(self.codes("Grep", {"pattern": "x", "path": "/c/Users/tester/.aws"}), ["PATH_AWS_DIR"])
        self.assertEqual(self.codes("Glob", {"pattern": "**/.env.", "path": "C:\\proj"}), ["PATH_ENV_FILE"])

    def test_a_device_path_cannot_be_checked_so_it_blocks(self):
        for path in ("\\\\.\\PhysicalDrive0", "//./C:", "\\\\.\\pipe\\x"):
            with self.subTest(path=path):
                self.assertEqual(self.read(path), ["PATH_DEVICE"])

    def test_a_git_bash_cwd_is_made_native(self):
        with windows():
            verdicts = pg.check(call("Read", {"file_path": ".aws\\config"}, cwd="/c/Users/tester"), ROOT)
        self.assertEqual([v.code for v in verdicts], ["PATH_AWS_DIR"])

    def test_the_same_texts_on_this_os_do_not_change(self):
        """Control: off Windows the Windows forms are not special. `C:\\proj\\.env.local` is one odd file name here."""
        for path in ("C:\\proj\\.env.local", "/c/Users/tester/.aws/config", "$USERPROFILE/.aws/config", "\\\\.\\x"):
            with self.subTest(path=path):
                self.assertEqual([v.code for v in pg.check(call("Read", {"file_path": path}, cwd="/work"), ROOT)], [])
        self.assertEqual(
            [v.code for v in pg.check(call("Read", {"file_path": "/p/.env"}, cwd="/work"), ROOT)], ["PATH_ENV_FILE"]
        )


if __name__ == "__main__":
    unittest.main()
