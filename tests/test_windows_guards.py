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
dg = importlib.import_module("document_guard")

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


class WindowsDocumentGuardTests(unittest.TestCase):
    """Zones were built with `/` and compared to `\\` paths, so on Windows every DOC_* zone was off."""

    def codes(self, tool: str, tool_input: dict, **extra) -> list[str]:
        with windows():
            return [v.code for v in dg.check(call(tool, tool_input, **extra), ROOT)]

    def write(self, path: str, content: str = "{}\n") -> list[str]:
        return self.codes("Write", {"file_path": path, "content": content})

    def test_the_approval_state_file_is_protected_in_every_spelling(self):
        for path in (
            "C:\\proj\\_bmad\\custom\\approval-state.json",
            "C:/proj/_bmad/custom/approval-state.json",
            "C:/proj\\_bmad/custom\\approval-state.json",  # mixed slashes
            "c:\\PROJ\\_BMAD\\custom\\Approval-State.json",  # case
            "C:\\proj\\_bmad\\custom\\approval-state.json::$DATA",  # NTFS stream
            "C:\\proj\\_bmad\\custom\\approval-state.json:stream",
            "C:\\proj\\_bmad\\custom\\approval-state.json.",  # trailing dot
            "C:\\proj\\_bmad\\custom\\approval-state.json ",  # trailing space
            "\\\\?\\C:\\proj\\_bmad\\custom\\approval-state.json",
            "/c/proj/_bmad/custom/approval-state.json",  # Git Bash
            "C:\\proj\\_bmad\\x\\..\\custom\\approval-state.json",
            "_bmad\\custom\\approval-state.json",  # relative to the project (cwd)
            "C:\\proj\\sub\\approval-state.json",  # the file name in any folder
        ):
            with self.subTest(path=path):
                self.assertEqual(self.write(path), ["DOC_APPROVAL_STATE"])

    def test_the_other_zones_are_protected_with_backslashes_and_any_case(self):
        for path, code in (
            ("C:\\proj\\_bmad-output\\guard\\reviews\\s.json", "DOC_GUARD_RECORD"),
            ("C:\\proj\\_BMAD-Output\\Guard\\builds\\s.json", "DOC_GUARD_RECORD"),
            ("C:\\proj\\_bmad\\custom\\project\\guard\\tool-rules.json", "DOC_GUARD_CONFIG"),
            ("C:\\proj\\_bmad\\custom\\guard\\manifest.json", "DOC_GUARD_SCRIPTS"),
            ("C:\\proj\\.claude\\settings.json", "DOC_GUARD_SCRIPTS"),
            ("C:\\proj\\.Claude\\Settings.JSON", "DOC_GUARD_SCRIPTS"),
            ("C:\\proj\\.claude.\\settings.json", "DOC_GUARD_SCRIPTS"),
            ("C:\\proj\\.claude\\settings.local.json", "DOC_GUARD_SCRIPTS"),
            ("C:\\proj\\.claude\\skills\\tool-guard\\scripts\\pre_tool_use.py", "DOC_GUARD_SCRIPTS"),
            ("C:\\proj\\.agents\\skills\\prompt-guard\\scripts\\prompt_check.py", "DOC_GUARD_SCRIPTS"),
            ("C:\\proj\\_bmad\\custom\\prompt-guard.toml", "DOC_GUARD_SCRIPTS"),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.write(path), [code])

    def test_edit_and_multiedit_and_notebook_use_the_same_zones(self):
        edit = {"file_path": "C:\\proj\\.claude\\settings.json", "old_string": "a", "new_string": "b"}
        self.assertEqual(self.codes("Edit", edit), ["DOC_GUARD_SCRIPTS"])
        multi = {"file_path": "C:\\proj\\.claude\\settings.json", "edits": [{"old_string": "a", "new_string": "b"}]}
        self.assertEqual(self.codes("MultiEdit", multi), ["DOC_GUARD_SCRIPTS"])
        notebook = {"notebook_path": "C:\\proj\\_bmad\\custom\\approval-state.json", "new_source": "x"}
        self.assertEqual(self.codes("NotebookEdit", notebook), ["DOC_APPROVAL_STATE"])

    def test_close_but_safe_writes_are_allowed(self):
        for path in (
            "C:\\proj\\src\\app.py",
            "C:\\proj\\_bmad-output\\prd-notes.json",
            "C:\\proj\\_bmad\\custom\\other.json",
            "C:\\proj\\.claude\\skills\\my-own-skill\\SKILL.txt",
            "C:\\proj\\.claude-notes\\settings.json",
            "C:\\proj2\\_bmad\\custom\\guard\\manifest.json",  # another folder with the same prefix
            "/c/proj/docs/notes.txt",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.write(path), [])

    def test_a_markdown_file_with_a_windows_trick_in_its_name_is_still_markdown(self):
        approved = "---\nstatus: approved\n---\n# PRD\n"
        for path in (
            "C:\\proj\\docs\\prd.md",
            "C:\\proj\\docs\\prd.md.",
            "C:\\proj\\docs\\prd.md ",
            "C:\\proj\\docs\\PRD.MD",
            "C:\\proj\\docs\\prd.markdown.",
            "C:\\proj\\docs\\prd.md::$DATA",
            "/c/proj/docs/prd.md.",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.write(path, approved), ["DOC_APPROVAL_FIELD"])
        self.assertEqual(self.write("C:\\proj\\docs\\prd.md.", "# PRD\n\nPlain text.\n"), [])

    def test_a_git_bash_cwd_is_made_native(self):
        found = self.codes("Write", {"file_path": "_bmad\\custom\\approval-state.json", "content": "{}"}, cwd="/c/proj")
        self.assertEqual(found, ["DOC_APPROVAL_STATE"])

    def test_the_message_names_the_guard_file_with_the_listed_name(self):
        with windows():
            verdicts = dg.check(call("Write", {"file_path": "C:\\PROJ\\.Claude\\settings.json", "content": "{}"}), ROOT)
        self.assertIn(".claude/settings.json", verdicts[0].reason)

    def test_the_same_texts_on_this_os_do_not_change(self):
        """Control: off Windows these are odd file names, not the protected files. Only the plain POSIX forms count."""
        for path in ("_bmad\\custom\\approval-state.json", "/c/proj/_bmad/custom/guard/manifest.json"):
            with self.subTest(path=path):
                self.assertEqual(
                    [v.code for v in dg.check(call("Write", {"file_path": path, "content": "{}"}, cwd="/work"), ROOT)],
                    [],
                )
        posix = call("Write", {"file_path": "/work/_bmad/custom/approval-state.json", "content": "{}"}, cwd="/work")
        self.assertEqual([v.code for v in dg.check(posix, Path("/work"))], ["DOC_APPROVAL_STATE"])
        spaced = call("Write", {"file_path": "/work/docs/prd.md ", "content": "status: approved\n"}, cwd="/work")
        self.assertEqual([v.code for v in dg.check(spaced, Path("/work"))], [])  # a name with a space is no .md file


if __name__ == "__main__":
    unittest.main()
