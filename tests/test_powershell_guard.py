# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for module/skills/tool-guard/scripts/powershell_guard.py (GUARD-3 T9, ADR 0009).

The guard blocks every PowerShell tool call, on every OS. These tests run on the real dispatcher, with the real
registered guards, on this machine (not a Windows simulation). Fake commands only.
"""

from __future__ import annotations

import json

from tests.guard_harness import GuardTestCase, queue


class PowerShellToolTests(GuardTestCase):
    def test_a_powershell_call_is_blocked_with_the_rule_id(self):
        for command in ("Get-ChildItem", "Set-Content -Path notes.txt -Value x", "Remove-Item -Recurse -Force src"):
            with self.subTest(command=command):
                self.assertEqual(self.codes("PowerShell", {"command": command}), ["PS_TOOL_UNSUPPORTED"])

    def test_it_blocks_a_call_that_no_other_guard_would_see(self):
        """The case that was open: writing the approval state through PowerShell."""
        command = 'Set-Content "_bmad\\custom\\approval-state.json" "{}"'
        self.assertEqual(self.codes("PowerShell", {"command": command}), ["PS_TOOL_UNSUPPORTED"])

    def test_it_blocks_a_call_with_missing_or_odd_input(self):
        for tool_input in ({}, {"command": 5}, {"command": ""}):
            with self.subTest(tool_input=tool_input):
                self.assertEqual(self.codes("PowerShell", tool_input), ["PS_TOOL_UNSUPPORTED"])

    def test_other_tools_are_not_touched(self):
        self.assertEqual(self.bash("git status"), [])
        self.assertEqual(self.codes("Read", {"file_path": str(self.project / "README.md")}), [])

    def test_the_hook_entry_exits_2_names_the_code_and_the_fix_and_leaves_the_command_out(self):
        command = "Get-Content secret-marker-text.txt"
        raw = json.dumps(self.payload("PowerShell", {"command": command}))
        code, out, err = self.entry(raw)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("PS_TOOL_UNSUPPORTED", err)
        self.assertIn("Bash tool", err)
        self.assertNotIn("secret-marker-text", err)
        events = queue(self.project)
        self.assertEqual([(e["reason_code"], e["verdict"]) for e in events], [("PS_TOOL_UNSUPPORTED", "block")])
        self.assertNotIn("secret-marker-text", json.dumps(events))

    def test_the_hook_entry_allows_a_powershell_call_in_a_project_that_does_not_use_the_guard(self):
        (self.project / "_bmad/custom/guard/manifest.json").unlink()
        code, out, err = self.entry(json.dumps(self.payload("PowerShell", {"command": "Get-ChildItem"})))
        self.assertEqual((code, out, err), (0, "", ""))
