# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for module/skills/tool-guard/scripts/hook_wiring.py and the managed-settings template."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "module/skills/tool-guard/scripts"
spec = importlib.util.spec_from_file_location("hook_wiring", SCRIPTS / "hook_wiring.py")
hw = importlib.util.module_from_spec(spec)
sys.modules["hook_wiring"] = hw
spec.loader.exec_module(hw)

PRE = hw.command("tool-guard", "pre_tool_use.py")
START = hw.command("tool-guard", "session_start.py")
PROMPT = hw.command("prompt-guard", "prompt_guard_hook.py")
OWN_HOOK = {"matcher": "Bash", "hooks": [{"type": "command", "command": "./my-own-check.sh"}]}


def full() -> dict:
    return hw.merge(None).settings


class FreshMergeTests(unittest.TestCase):
    def test_empty_settings_get_all_hooks_and_deny_rules(self):
        result = hw.merge(None)
        self.assertEqual(result.conflicts, [])
        self.assertEqual(result.settings["hooks"], hw.desired_hooks())
        self.assertEqual(result.settings["permissions"]["deny"], list(hw.DENY_RULES))
        self.assertEqual(len(result.changes), 3 + len(hw.DENY_RULES))
        self.assertFalse(result.ok)

    def test_command_is_exact_and_uses_project_dir_with_quotes(self):
        self.assertEqual(PRE, 'python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/tool-guard/scripts/pre_tool_use.py"')
        hooks = hw.desired_hooks()
        self.assertEqual(hooks["PreToolUse"][0]["hooks"][0]["command"], PRE)
        self.assertEqual(hooks["PreToolUse"][0]["hooks"][0]["onFailure"], "block")
        self.assertEqual(hooks["PreToolUse"][0]["matcher"], hw.PRE_TOOL_MATCHER)
        self.assertEqual(hooks["UserPromptSubmit"][0]["hooks"][0]["command"], PROMPT)
        self.assertNotIn("matcher", hooks["SessionStart"][0])

    def test_empty_dict_and_none_are_the_same(self):
        self.assertEqual(hw.merge({}).settings, hw.merge(None).settings)

    def test_second_run_changes_nothing(self):
        again = hw.merge(full())
        self.assertTrue(again.ok, again.changes)
        self.assertEqual(again.settings, full())

    def test_input_is_not_changed(self):
        original = {"hooks": {"PreToolUse": [copy.deepcopy(OWN_HOOK)]}, "permissions": {"deny": ["Read(x)"]}}
        before = copy.deepcopy(original)
        hw.merge(original)
        self.assertEqual(original, before)

    def test_script_paths(self):
        self.assertEqual(
            hw.script_paths(),
            [
                ".claude/skills/tool-guard/scripts/session_start.py",
                ".claude/skills/prompt-guard/scripts/prompt_guard_hook.py",
                ".claude/skills/tool-guard/scripts/pre_tool_use.py",
            ],
        )


class ExistingSettingsTests(unittest.TestCase):
    def test_project_entries_and_other_keys_are_kept(self):
        settings = {
            "model": "x",
            "env": {"A": "1"},
            "hooks": {
                "PreToolUse": [copy.deepcopy(OWN_HOOK)],
                "Stop": [{"hooks": [{"type": "command", "command": "s"}]}],
            },
            "permissions": {"allow": ["Bash(ls)"], "deny": ["Read(secrets/**)"]},
        }
        out = hw.merge(settings)
        self.assertEqual(out.conflicts, [])
        self.assertEqual(out.settings["model"], "x")
        self.assertEqual(out.settings["env"], {"A": "1"})
        self.assertEqual(out.settings["hooks"]["Stop"], settings["hooks"]["Stop"])
        self.assertEqual(out.settings["hooks"]["PreToolUse"][0], OWN_HOOK)
        self.assertEqual(out.settings["hooks"]["PreToolUse"][1]["hooks"][0]["command"], PRE)
        self.assertEqual(out.settings["permissions"]["allow"], ["Bash(ls)"])
        self.assertEqual(out.settings["permissions"]["deny"], ["Read(secrets/**)", *hw.DENY_RULES])

    def test_deny_rules_already_present_are_not_duplicated(self):
        settings = {"permissions": {"deny": ["Read(.env)", "Read(*.pem)"]}}
        out = hw.merge(settings)
        deny = out.settings["permissions"]["deny"]
        self.assertEqual(len(deny), len(set(deny)))
        self.assertEqual(deny[:2], ["Read(.env)", "Read(*.pem)"])
        self.assertEqual(sum(c.startswith("add deny rule") for c in out.changes), len(hw.DENY_RULES) - 2)

    def test_carve_out_comes_after_the_rule_it_carves_from(self):
        deny = full()["permissions"]["deny"]
        self.assertLess(deny.index("Read(.env.*)"), deny.index("Read(!.env.example)"))

    def test_wrong_timeout_is_updated_to_one_copy(self):
        settings = full()
        settings["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 99
        out = hw.merge(settings)
        self.assertEqual(out.changes, ["update hook PreToolUse pre_tool_use.py"])
        self.assertEqual(out.settings["hooks"], hw.desired_hooks())

    def test_wrong_matcher_is_updated(self):
        settings = full()
        settings["hooks"]["PreToolUse"][0]["matcher"] = "Bash"
        out = hw.merge(settings)
        self.assertEqual(out.settings["hooks"]["PreToolUse"][0]["matcher"], hw.PRE_TOOL_MATCHER)
        self.assertEqual(len(out.settings["hooks"]["PreToolUse"]), 1)

    def test_duplicate_copies_of_our_hook_become_one(self):
        settings = full()
        settings["hooks"]["SessionStart"].append(copy.deepcopy(settings["hooks"]["SessionStart"][0]))
        out = hw.merge(settings)
        self.assertEqual(out.changes, ["update hook SessionStart session_start.py"])
        self.assertEqual(len(out.settings["hooks"]["SessionStart"]), 1)

    def test_our_hook_sharing_a_group_with_a_project_hook_keeps_the_project_hook(self):
        settings = full()
        shared = {"matcher": "Bash", "hooks": [OWN_HOOK["hooks"][0], settings["hooks"]["PreToolUse"][0]["hooks"][0]]}
        settings["hooks"]["PreToolUse"] = [shared]
        out = hw.merge(settings)
        commands = [h["command"] for g in out.settings["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertEqual(commands, ["./my-own-check.sh", PRE])  # matcher differs: old copy moved to a fresh group
        self.assertEqual(out.settings["hooks"]["PreToolUse"][0]["hooks"], [OWN_HOOK["hooks"][0]])

    def test_project_hook_with_same_matcher_is_not_a_conflict(self):
        out = hw.merge({"hooks": {"PreToolUse": [{"matcher": hw.PRE_TOOL_MATCHER, "hooks": OWN_HOOK["hooks"]}]}})
        self.assertEqual(out.conflicts, [])
        self.assertEqual(len(out.settings["hooks"]["PreToolUse"]), 2)

    def test_disable_all_hooks_false_is_left_alone(self):
        out = hw.merge({"disableAllHooks": False})
        self.assertEqual(out.conflicts, [])
        self.assertIs(out.settings["disableAllHooks"], False)


class ConflictTests(unittest.TestCase):
    STALE = "python3 .claude/skills/tool-guard/scripts/pre_tool_use.py"

    def stale_settings(self) -> dict:
        return {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": self.STALE}]}]}}

    def test_lookalike_command_is_a_conflict_and_kept(self):
        out = hw.merge(self.stale_settings())
        self.assertEqual(len(out.conflicts), 1)
        self.assertIn(self.STALE, out.conflicts[0])
        commands = [h["command"] for g in out.settings["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertIn(self.STALE, commands)
        self.assertIn(PRE, commands)  # the rest of the wiring is still added

    def test_force_replaces_the_lookalike(self):
        out = hw.merge(self.stale_settings(), force=True)
        self.assertEqual(out.conflicts, [])
        commands = [h["command"] for g in out.settings["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertEqual(commands, [PRE])
        self.assertTrue(any(c.startswith("remove stale hook") for c in out.changes))

    def test_our_script_under_the_wrong_event_is_a_conflict(self):
        settings = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": PRE}]}]}}
        self.assertEqual(len(hw.merge(settings).conflicts), 1)

    def test_prompt_hook_with_another_path_is_a_conflict(self):
        settings = {
            "hooks": {
                "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 x/prompt_guard_hook.py"}]}]
            }
        }
        self.assertEqual(len(hw.merge(settings).conflicts), 1)

    def test_hooks_not_an_object(self):
        out = hw.merge({"hooks": []})
        self.assertEqual(out.conflicts, ["hooks is not an object"])
        self.assertEqual(out.settings["hooks"], [])
        forced = hw.merge({"hooks": []}, force=True)
        self.assertEqual(forced.settings["hooks"], hw.desired_hooks())

    def test_event_value_not_a_list(self):
        out = hw.merge({"hooks": {"PreToolUse": {"a": 1}}})
        self.assertEqual(out.conflicts, ["hooks.PreToolUse is not a list"])
        self.assertEqual(out.settings["hooks"]["PreToolUse"], {"a": 1})
        forced = hw.merge({"hooks": {"PreToolUse": {"a": 1}}}, force=True)
        self.assertEqual(forced.conflicts, [])
        self.assertEqual(forced.settings["hooks"]["PreToolUse"], hw.desired_hooks()["PreToolUse"])

    def test_permissions_shapes(self):
        self.assertEqual(hw.merge({"permissions": "x"}).conflicts, ["permissions is not an object"])
        self.assertEqual(hw.merge({"permissions": {"deny": "x"}}).conflicts, ["permissions.deny is not a list"])
        forced = hw.merge({"permissions": {"deny": "x", "allow": ["a"]}}, force=True)
        self.assertEqual(forced.settings["permissions"], {"deny": list(hw.DENY_RULES), "allow": ["a"]})

    def test_disable_all_hooks_true_is_a_conflict(self):
        out = hw.merge({"disableAllHooks": True})
        self.assertEqual(out.conflicts, ["disableAllHooks is true: no hook would run"])
        self.assertIs(out.settings["disableAllHooks"], True)
        forced = hw.merge({"disableAllHooks": True}, force=True)
        self.assertNotIn("disableAllHooks", forced.settings)
        self.assertEqual(forced.conflicts, [])

    def test_malformed_entries_inside_a_valid_list_are_ignored_not_crashed(self):
        settings = {"hooks": {"PreToolUse": ["junk", {"hooks": "no"}, {"hooks": [1, {"command": 5}]}]}}
        out = hw.merge(settings)
        self.assertEqual(out.conflicts, [])
        self.assertEqual(out.settings["hooks"]["PreToolUse"][:3], settings["hooks"]["PreToolUse"])


class LoadSettingsTests(unittest.TestCase):
    def test_missing_file_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(hw.load_settings(Path(tmp) / "settings.json"))

    def test_invalid_json_and_non_object_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{not json")
            with self.assertRaisesRegex(ValueError, "not valid JSON"):
                hw.load_settings(path)
            path.write_text("[]")
            with self.assertRaisesRegex(ValueError, "not a JSON object"):
                hw.load_settings(path)

    def test_valid_file_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(hw.render(full()))
            self.assertEqual(hw.load_settings(path), full())
            self.assertTrue(hw.render({}).endswith("}\n"))


class ManagedTemplateTests(unittest.TestCase):
    """module/managed-settings/managed-settings.json must carry the same wiring as the project merge."""

    @classmethod
    def setUpClass(cls):
        cls.template = json.loads((ROOT / "module/managed-settings/managed-settings.json").read_text())

    def test_hooks_and_deny_rules_match_the_wiring(self):
        self.assertEqual(self.template["hooks"], hw.desired_hooks())
        self.assertEqual(self.template["permissions"]["deny"], list(hw.DENY_RULES))

    def test_hooks_are_locked_to_managed(self):
        self.assertIs(self.template["allowManagedHooksOnly"], True)

    def test_hooks_cannot_be_switched_off_and_no_allow_rules_are_set(self):
        self.assertNotIn("disableAllHooks", self.template)
        self.assertNotIn("allow", self.template["permissions"])

    def test_no_secrets_or_real_paths(self):
        text = (ROOT / "module/managed-settings/managed-settings.json").read_text()
        self.assertNotIn("/Users/", text)
        self.assertNotRegex(text, r"(?i)api[_-]?key|token|password")


if __name__ == "__main__":
    unittest.main()
