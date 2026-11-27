# /// script
# requires-python = ">=3.10"
# ///
"""Windows behaviour of the three hook entries (GUARD-3 T9, ADR 0009): SessionStart, the console encoding, and how the
project folder is found. A simulation on this OS (`sys.platform`, `IS_WINDOWS`, a cp1252 stream); not run on Windows.

Fake data only.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import sys
import unittest
from pathlib import PureWindowsPath
from unittest import mock

from tests.guard_harness import ROOT, GuardTestCase, queue
from tests.test_tool_guard import hc, hw, pt, ss
from tests.windows_flavour import POSIX_ONLY

PROMPT_SCRIPTS = ROOT / "module/skills/prompt-guard/scripts"
if str(PROMPT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PROMPT_SCRIPTS))
import prompt_check as pc  # noqa: E402 - found through the sys.path lines above
import prompt_guard_hook as pgh  # noqa: E402

WIN_BASE = "C:/Program Files/ClaudeCode/bmad-guard"


def cp1252_streams() -> tuple[io.TextIOWrapper, io.TextIOWrapper]:
    """stdout and stderr as a Windows console with code page cp1252 gives them: strict, so ✓ cannot be written."""
    return (
        io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", write_through=True),
        io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", write_through=True),
    )


class SessionStartOnWindowsTests(GuardTestCase):
    def start(self, wire_settings: bool = True):
        if wire_settings:
            path = self.project / hw.SETTINGS_REL
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(hw.render(hw.merge(None).settings), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        stdin = mock.Mock(buffer=io.BytesIO(json.dumps({"cwd": str(self.project)}).encode()))
        with (
            mock.patch.object(ss, "managed_dir", return_value=self.project / "managed"),
            mock.patch.object(sys, "stdin", stdin),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = ss.entry()
        return code, out.getvalue(), err.getvalue()

    @POSIX_ONLY
    def test_a_project_with_no_git_hooks_path_warns_off_windows(self):
        """Control: the same project warns on macOS and Linux (core.hooksPath is not set)."""
        self.assertIn("core.hooksPath", json.loads(self.start()[1])["systemMessage"])

    def test_setup_installs_no_git_hooks_on_windows_so_the_session_is_silent(self):
        with mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(self.start(), (0, "", ""))
        self.assertEqual(queue(self.project), [])

    def test_missing_wiring_still_warns_on_windows(self):
        with mock.patch.object(sys, "platform", "win32"):
            code, out, _ = self.start(wire_settings=False)
        message = json.loads(out)["systemMessage"]
        self.assertEqual(code, 0)
        self.assertIn("Claude Code hooks will not run", message)
        self.assertNotIn("core.hooksPath", message)
        self.assertEqual(queue(self.project)[0]["missing"], "hook_wiring")

    def test_hooks_path_ok_is_true_on_windows(self):
        self.assertFalse(ss.hooks_path_ok(self.project))
        with mock.patch.object(sys, "platform", "win32"):
            self.assertTrue(ss.hooks_path_ok(self.project))

    def test_the_windows_managed_file_with_machine_commands_counts_as_wired(self):
        """Before: the managed folder came out as `C:\\Program Files\\...`, the base check refused it, and every
        session warned SETUP_INCOMPLETE."""
        template = json.loads((ROOT / "module/managed-settings/managed-settings.windows.json").read_text("utf-8"))
        managed = PureWindowsPath("C:\\Program Files\\ClaudeCode")
        with (
            mock.patch.object(ss, "managed_dir", return_value=managed),
            mock.patch.object(ss, "managed_settings", return_value=template),
        ):
            self.assertTrue(ss.wiring_ok(self.project))
        self.assertEqual((managed / hw.MACHINE_DIR_NAME).as_posix(), WIN_BASE)

    def test_a_wrong_windows_managed_file_does_not_count(self):
        template = json.loads((ROOT / "module/managed-settings/managed-settings.macos.json").read_text("utf-8"))
        with (
            mock.patch.object(ss, "managed_dir", return_value=PureWindowsPath("C:\\Program Files\\ClaudeCode")),
            mock.patch.object(ss, "managed_settings", return_value=template),
        ):
            self.assertFalse(ss.wiring_ok(self.project))

    def test_the_credential_warning_on_windows_names_icacls_free_fixes_not_chmod(self):
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(ss, "credential_ok", return_value=False),
        ):
            message = json.loads(self.start()[1])["systemMessage"]
        self.assertIn("link or junction", message)
        self.assertIn("setup_guard.py --credentials", message)
        self.assertNotIn("chmod", message)


class ConsoleEncodingTests(GuardTestCase):
    """A message with a character that cp1252 cannot write must still come out, with its own rule id."""

    def run_entry(self, entry, raw: str):
        out, err = cp1252_streams()
        stdin = mock.Mock(buffer=io.BytesIO(raw.encode()))
        env = mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.project)})
        with env, mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", out):
            with mock.patch.object(sys, "stderr", err):
                code = entry()
        return code, out.buffer.getvalue().decode("utf-8"), err.buffer.getvalue().decode("utf-8")

    def test_pre_tool_use_blocks_with_the_real_reason_when_the_message_has_non_cp1252_text(self):
        guard = hc.Guard("g", ("Bash",), lambda payload, root: [hc.Verdict("X_FANCY", "caf\u00e9 \u2713 reason", "g")])
        raw = json.dumps(self.payload("Bash", {"command": "echo hi"}))
        with mock.patch.object(pt, "load_guards", return_value=[guard]):
            code, _, err = self.run_entry(pt.entry, raw)
        self.assertEqual(code, 2)
        self.assertIn("X_FANCY", err)
        self.assertIn("\u2713", err)
        self.assertNotIn("GUARD_ERROR", err)

    def test_utf8_output_reconfigures_both_streams_and_leaves_others_alone(self):
        out, err = cp1252_streams()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            hc.utf8_output()
        self.assertEqual((out.encoding, err.encoding), ("utf-8", "utf-8"))
        for stream in (io.StringIO(), None):
            with mock.patch.object(sys, "stdout", stream), mock.patch.object(sys, "stderr", stream):
                hc.utf8_output()  # no reconfigure on a StringIO, no stream at all: nothing raised

    def test_the_prompt_hook_and_session_start_reconfigure_too(self):
        for name, entry in (("prompt_guard_hook", pgh.entry), ("session_start", ss.entry)):
            with self.subTest(hook=name):
                out, err = cp1252_streams()
                stdin = mock.Mock(buffer=io.BytesIO(b"{}"))
                with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", out):
                    with mock.patch.object(sys, "stderr", err):
                        entry()
                self.assertEqual((out.encoding, err.encoding), ("utf-8", "utf-8"))


class ProjectRootOnWindowsTests(GuardTestCase):
    def test_a_git_bash_project_dir_becomes_a_drive_path_in_both_hooks(self):
        for copy in (hc, pc):
            with self.subTest(copy=copy.__name__):
                for text, want in (
                    ("/c/Users/x/proj", "C:\\Users\\x\\proj"),
                    ("/cygdrive/d/work", "D:\\work"),
                    ("/mnt/c/a", "C:\\a"),
                    ("C:\\Users\\x\\proj", "C:\\Users\\x\\proj"),
                    ("/home/x/proj", "/home/x/proj"),
                ):
                    with (
                        mock.patch.object(copy, "IS_WINDOWS", True),
                        mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": text}),
                    ):
                        self.assertEqual(str(copy.project_root()), want, text)

    @POSIX_ONLY
    def test_off_windows_the_project_dir_is_used_as_it_is(self):
        for copy in (hc, pc):
            with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/c/Users/x/proj"}):
                self.assertEqual(str(copy.project_root()), "/c/Users/x/proj")

    def test_the_copies_have_the_same_code(self):
        for name in ("_native", "utf8_output"):
            self.assertEqual(
                inspect.getsource(getattr(hc, name)).split('"""')[2],
                inspect.getsource(getattr(pc, name)).split('"""')[2],
                name,
            )

    def test_the_copy_in_winpaths_agrees(self):
        from tests.windows_flavour import windows, winpaths

        samples = ("/c/Users/x", "/cygdrive/c/x", "/mnt/d/a/b", "/c", "/c/", "/usr/bin", "C:\\x", "proj/x", "")
        with windows(), mock.patch.object(hc, "IS_WINDOWS", True), mock.patch.object(pc, "IS_WINDOWS", True):
            for text in samples:
                with self.subTest(text=text):
                    self.assertEqual(hc._native(text), pc._native(text))
                    self.assertEqual(hc._native(text), winpaths.native(text))

    def test_git_output_is_read_as_utf8(self):
        with mock.patch.object(hc.subprocess, "run") as run, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            run.return_value = mock.Mock(returncode=0, stdout="/x\n")
            hc.project_root(self.project)
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "replace")
        self.assertNotIn("text", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
