# /// script
# requires-python = ">=3.10"
# ///
"""Tests for the guard rule list (customize.toml) and the project extension file (tool-rules.json). Fake data only."""

from __future__ import annotations

import importlib
import json
import sys
import unittest

from tests.guard_harness import GuardTestCase, queue

gc = importlib.import_module("guard_config")
cg = importlib.import_module("command_guard")

EXPECTED_RULE_IDS = [
    "CMD_RM_RECURSIVE",
    "CMD_FIND_DELETE",
    "CMD_GIT_FORCE_PUSH",
    "CMD_GIT_PUSH_DELETE",
    "CMD_GIT_RESET_HARD",
    "CMD_GIT_CLEAN",
    "CMD_GIT_BRANCH_DELETE",
    "CMD_GIT_NO_VERIFY",
    "CMD_GIT_COMMIT_SKIP_HOOKS",
    "CMD_GIT_HOOKS_PATH",
    "CMD_SQL_DESTRUCTIVE",
    "CMD_CHMOD_WORLD_WRITABLE",
    "EXPORT_DB_REMOTE",
    "EXPORT_DB_HOST_UNKNOWN",
    "EXPORT_S3_DOWNLOAD",
]


class TomlSubsetTests(unittest.TestCase):
    def test_tables_arrays_and_values(self):
        text = (
            "# comment\n"
            "[agent]\n"
            'code = "x"   # trailing comment\n'
            "flag = true\n"
            "n = 3\n"
            'list = ["a#b", "c"]\n'
            'nested = [["-a|--b"], ["-c", "-d"]]\n'
            "\n"
            "[[agent.rules]]\n"
            'id = "one"\n'
            "[[agent.rules]]\n"
            'id = "two"\n'
            "[other.table]\n"
            'k = "v \\"quoted\\" #not a comment"\n'
        )
        self.assertEqual(
            gc.parse_toml(text),
            {
                "agent": {
                    "code": "x",
                    "flag": True,
                    "n": 3,
                    "list": ["a#b", "c"],
                    "nested": [["-a|--b"], ["-c", "-d"]],
                    "rules": [{"id": "one"}, {"id": "two"}],
                },
                "other": {"table": {"k": 'v "quoted" #not a comment'}},
            },
        )

    def test_anything_else_raises(self):
        for text in (
            "key",
            "key = ",
            "key = bare",
            "key = 'literal'",
            "k = [1,\n2]",
            "[unclosed",
            "[[unclosed]",
            'a = "1"\na = "2"',
            "bad key = 1",
            "[a]\n[[a]]",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                gc.parse_toml(text)

    @unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11")
    def test_our_reader_agrees_with_tomllib_on_the_shipped_file(self):
        import tomllib

        text = gc.CUSTOMIZE.read_text(encoding="utf-8")
        self.assertEqual(gc.parse_toml(text), tomllib.loads(text))


class BuiltinRulesTests(unittest.TestCase):
    def test_the_rule_list_is_the_documented_one(self):
        rules, _, _ = gc.load_builtin()
        self.assertEqual([r["id"] for r in rules], EXPECTED_RULE_IDS)

    def test_every_rule_has_a_reason_and_a_known_check_or_a_command(self):
        rules, _, _ = gc.load_builtin()
        for rule in rules:
            with self.subTest(rule=rule["id"]):
                self.assertTrue(rule["reason"])
                self.assertLessEqual(len(rule["reason"]), gc.MAX_REASON)
                self.assertTrue(("check" in rule) != ("command" in rule))
                if "check" in rule:
                    self.assertIn(rule["check"], cg.CHECKS)

    def test_every_check_function_is_used_by_a_rule(self):
        rules, _, _ = gc.load_builtin()
        self.assertEqual(sorted(r["check"] for r in rules if "check" in r), sorted(cg.CHECKS))

    def test_scratch_folders_and_local_hosts(self):
        _, scratch, hosts = gc.load_builtin()
        self.assertIn("tmp", scratch)
        self.assertIn("**/node_modules", scratch)
        self.assertNotIn(".", scratch)
        self.assertEqual(hosts, ["localhost", "127.0.0.1", "::1"])


class DocsTests(unittest.TestCase):
    def test_the_skill_doc_names_every_rule_id(self):
        hw = importlib.import_module("hook_wiring")
        text = (gc.SKILL_ROOT / "references/guard-rules.md").read_text(encoding="utf-8")
        for rule_id in [*EXPECTED_RULE_IDS, *(rule_id for rule_id, _, _ in hw.SENSITIVE_PATHS)]:
            with self.subTest(rule=rule_id):
                self.assertIn(f"`{rule_id}`", text)

    def test_the_rollout_doc_lists_every_deny_pattern(self):
        hw = importlib.import_module("hook_wiring")
        text = (gc.SKILL_ROOT.parents[2] / "docs/managed-settings-rollout.md").read_text(encoding="utf-8")
        for rule_id, patterns, carve_outs in hw.SENSITIVE_PATHS:
            self.assertIn(f"`{rule_id}`", text)
            for pattern in (*patterns, *carve_outs):
                with self.subTest(pattern=pattern):
                    self.assertIn(f"`{pattern}`", text)


class ExtensionTests(GuardTestCase):
    def extension(self, data) -> None:
        text = data if isinstance(data, str) else json.dumps(data)
        self.write("_bmad/custom/project/guard/tool-rules.json", text)

    def test_no_extension_file_means_the_built_in_rules_only(self):
        rules = gc.load(self.project)
        self.assertEqual([r["code"] for r in rules.commands], EXPECTED_RULE_IDS)
        self.assertEqual(rules.project_paths, ())

    def test_a_project_command_rule_blocks_with_its_own_code(self):
        self.extension(
            {
                "command_rules": [
                    {
                        "id": "no-terraform-destroy",
                        "command": "terraform",
                        "words": ["destroy"],
                        "reason": "Destroy is run by the platform team.",
                    }
                ]
            }
        )
        self.assertBlocks("terraform destroy -auto-approve", "PROJECT_CMD_NO_TERRAFORM_DESTROY")
        self.assertBlocks("cd infra && terraform -chdir=x destroy", "PROJECT_CMD_NO_TERRAFORM_DESTROY")
        self.assertBlocks("sudo sh -c 'terraform destroy'", "PROJECT_CMD_NO_TERRAFORM_DESTROY")
        self.assertAllows("terraform plan", "terraform apply", "echo terraform destroy")
        _, verdicts = self.call("Bash", {"command": "terraform destroy"})
        self.assertEqual(verdicts[0].reason, "Destroy is run by the platform team.")

    def test_a_project_rule_with_flags_and_git_subcommand(self):
        self.extension(
            {
                "command_rules": [
                    {"id": "no-stash-drop", "command": "git", "subcommand": "stash", "words": ["drop"]},
                    {
                        "id": "no-docker-prune",
                        "command": "docker",
                        "words": ["system", "prune"],
                        "flag_sets": [["-a|--all"]],
                    },
                ]
            }
        )
        self.assertBlocks("git stash drop", "PROJECT_CMD_NO_STASH_DROP")
        self.assertBlocks("docker system prune -af", "PROJECT_CMD_NO_DOCKER_PRUNE")
        self.assertAllows("git stash", "git stash pop", "docker system prune", "docker system df -a")
        _, verdicts = self.call("Bash", {"command": "git stash drop"})
        self.assertEqual(verdicts[0].reason, "Blocked by a project guard rule.")

    def test_a_project_scratch_folder_widens_rm_only_for_that_folder(self):
        self.assertBlocks("rm -rf _work/cache", "CMD_RM_RECURSIVE")
        self.extension({"scratch_folders": ["_work/cache", "**/generated", "/var/cache/build"]})
        self.assertAllows(
            "rm -rf _work/cache", "rm -rf _work/cache/x", "rm -rf pkg/generated", "rm -rf /var/cache/build/x"
        )
        self.assertBlocks("rm -rf _work", "CMD_RM_RECURSIVE")
        self.assertBlocks("rm -rf /var/cache/build", "CMD_RM_RECURSIVE")
        self.assertBlocks("rm -rf src", "CMD_RM_RECURSIVE")

    def test_a_project_cannot_remove_or_replace_a_built_in_rule(self):
        for data in (
            {"disabled_rules": ["CMD_GIT_FORCE_PUSH"]},
            {"remove": ["CMD_GIT_FORCE_PUSH"]},
            {"command_rules": [{"id": "CMD_GIT_FORCE_PUSH", "command": "git", "words": ["never-matches"]}]},
            {"command_rules": [{"id": "cmd_git_force_push", "check": "rm_recursive"}]},
            {"scratch_folders": ["/"]},
        ):
            with self.subTest(data=data):
                self.extension(data)
                self.assertEqual(self.bash("git push --force"), ["EXT_BAD_FILE"])
        # an id that looks like a built-in one is only a new rule, with its own code; the built-in rule still applies
        self.extension({"command_rules": [{"id": "cmd-git-force-push", "command": "git", "words": ["never-matches"]}]})
        self.assertBlocks("git push --force", "CMD_GIT_FORCE_PUSH")

    def test_a_team_override_file_cannot_change_the_built_in_rules(self):
        self.write(
            "_bmad/custom/tool-guard.toml",
            '[[agent.command_rules]]\nid = "CMD_GIT_FORCE_PUSH"\ncommand = "git"\nwords = ["never-matches"]\n'
            'reason = "weakened"\n[agent]\nscratch_folders = ["src"]\n',
        )
        self.write("_bmad/custom/tool-guard.user.toml", '[agent]\nlocal_hosts = ["db.prod.example.com"]\n')
        self.assertBlocks("git push --force", "CMD_GIT_FORCE_PUSH")
        self.assertBlocks("rm -rf src", "CMD_RM_RECURSIVE")
        self.assertBlocks("psql -h db.prod.example.com app", "EXPORT_DB_REMOTE")

    def test_a_project_cannot_add_a_local_host(self):
        self.extension({"local_hosts": ["db.prod.example.com"]})
        self.assertEqual(self.bash("psql -h db.prod.example.com app"), ["EXT_BAD_FILE"])

    def test_bad_extension_files_block_bash_with_a_clear_reason(self):
        bad = {
            "not json": "{ this is not json",
            "not an object": "[]",
            "unknown key": {"command_rule": []},
            "rules not a list": {"command_rules": {}},
            "rule not an object": {"command_rules": ["x"]},
            "no id": {"command_rules": [{"command": "x"}]},
            "bad id": {"command_rules": [{"id": "Bad Id", "command": "x"}]},
            "no command": {"command_rules": [{"id": "a"}]},
            "command with a path": {"command_rules": [{"id": "a", "command": "/bin/x"}]},
            "unknown rule key": {"command_rules": [{"id": "a", "command": "x", "regex": "."}]},
            "check not allowed": {"command_rules": [{"id": "a", "check": "rm_recursive"}]},
            "bad flag": {"command_rules": [{"id": "a", "command": "x", "flag_sets": [["force"]]}]},
            "flag_sets shape": {"command_rules": [{"id": "a", "command": "x", "flag_sets": ["-f"]}]},
            "words not text": {"command_rules": [{"id": "a", "command": "x", "words": [1]}]},
            "subcommand on non-git": {"command_rules": [{"id": "a", "command": "x", "subcommand": "y"}]},
            "multi-line reason": {"command_rules": [{"id": "a", "command": "x", "reason": "a\nb"}]},
            "long reason": {"command_rules": [{"id": "a", "command": "x", "reason": "r" * 300}]},
            "duplicate id": {"command_rules": [{"id": "a", "command": "x"}, {"id": "a", "command": "y"}]},
            "path rule shape": {"sensitive_paths": ["x"]},
            "path rule no pattern": {"sensitive_paths": [{"id": "a"}]},
            "path pattern with dotdot": {"sensitive_paths": [{"id": "a", "pattern": "../x/**"}]},
            "path pattern with a bare slash": {"sensitive_paths": [{"id": "a", "pattern": "dir/file.txt"}]},
            "path pattern only wildcards": {"sensitive_paths": [{"id": "a", "pattern": "**"}]},
            "scratch not a list": {"scratch_folders": "tmp"},
            "scratch dot": {"scratch_folders": ["."]},
            "scratch dotdot": {"scratch_folders": ["../x"]},
            "scratch everything": {"scratch_folders": ["**"]},
            "scratch root": {"scratch_folders": ["/"]},
            "scratch short absolute": {"scratch_folders": ["/usr"]},
            "scratch home": {"scratch_folders": ["~/x"]},
        }
        for label, data in bad.items():
            with self.subTest(label=label):
                self.extension(data)
                _, verdicts = self.call("Bash", {"command": "echo hi"})
                self.assertEqual([v.code for v in verdicts], ["EXT_BAD_FILE"])
                self.assertIn("tool-rules.json", verdicts[0].reason)
                self.assertIn("Fix the file by hand", verdicts[0].reason)

    def test_a_bad_extension_is_one_reason_for_bash_not_two(self):
        self.extension("{ bad")
        self.assertEqual(self.bash("cat x.txt"), ["EXT_BAD_FILE"])

    def test_the_error_message_never_repeats_file_content(self):
        marker = "SECRET-FILE-CONTENT-777"
        self.extension({"command_rules": [{"id": marker, "command": "x"}]})
        _, verdicts = self.call("Bash", {"command": "ls"})
        self.assertNotIn(marker, verdicts[0].reason)
        self.extension("{ " + marker)
        _, verdicts = self.call("Bash", {"command": "ls"})
        self.assertNotIn(marker, verdicts[0].reason)

    def test_a_fixed_extension_file_works_again(self):
        self.extension("{ bad")
        self.assertEqual(self.bash("ls"), ["EXT_BAD_FILE"])
        self.extension({})
        self.assertEqual(self.bash("ls"), [])

    def test_extension_blocks_are_recorded_by_code_only(self):
        self.extension({"command_rules": [{"id": "no-terraform", "command": "terraform"}]})
        marker = "SECRET-ARG-MARKER-42"
        self.entry(json.dumps(self.payload("Bash", {"command": f"terraform destroy {marker}"})))
        events = queue(self.project)
        self.assertEqual([e["reason_code"] for e in events], ["PROJECT_CMD_NO_TERRAFORM"])
        self.assertNotIn(marker, json.dumps(events))

    def test_other_files_in_the_guard_folder_are_ignored(self):
        self.write("_bmad/custom/project/guard/prompt-patterns.json", "{ not for us")
        self.assertEqual(self.bash("git status"), [])


if __name__ == "__main__":
    unittest.main()
