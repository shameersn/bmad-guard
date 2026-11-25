# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for module/skills/tool-guard/scripts/path_guard.py: one block and one close-but-safe allow per rule.

Fake file names only. Nothing is read from disk by the guard (only its own settings), and no real secret appears here.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from tests.guard_harness import SCRIPTS, GuardTestCase, queue

hw = importlib.import_module("hook_wiring")
pg = importlib.import_module("path_guard")


class FileToolTests(GuardTestCase):
    def read(self, rel: str, tool: str = "Read") -> list[str]:
        return self.codes(tool, {"file_path": str(self.project / rel)})

    def test_env_files_block_but_the_example_is_readable(self):
        for name in (".env", ".env.local", ".env.production", ".env.development.local", "app/.env", "prod.env", ".ENV"):
            with self.subTest(name=name):
                self.assertEqual(self.read(name), ["PATH_ENV_FILE"], name)
        for name in (
            ".env.example",
            "app/.env.example",
            ".envrc.md",
            "environment.py",
            "env.md",
            "src/env.py",
            ".gitignore",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.read(name), [], name)

    def test_keys_and_certificates(self):
        for name in (
            "server.pem",
            "certs/ca.PEM",
            "tls.key",
            "keys/deploy.key",
            "id_rsa",
            "id_ed25519",
            "id_ecdsa",
            "id_dsa",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.read(name), ["PATH_PRIVATE_KEY"], name)
        for name in ("id_rsa.pub", "id_rsa_work", "keys/id_ed25519_sk"):
            with self.subTest(name=name):
                self.assertEqual(self.read(name), ["PATH_PRIVATE_KEY"], name)
        for name in (
            "id_test.py",
            "id_utils.py",
            "identity.md",
            "idle.txt",
            "src/id_generator.py",
            "keyboard.py",
            "monkey.txt",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.read(name), [], name)

    def test_key_stores(self):
        self.assertEqual(self.read("cert.p12"), ["PATH_KEY_STORE"])
        self.assertEqual(self.read("keys/Cert.P12"), ["PATH_KEY_STORE"])
        self.assertEqual(self.read("cert.p12.md"), [])
        self.assertEqual(self.read("cert.pdf"), [])

    def test_database_dumps(self):
        for name in ("dump.sql", "db_dump.sql", "backup/mysqldump-2024.sql", "prod-dump-1.sql", "DUMP.SQL"):
            with self.subTest(name=name):
                self.assertEqual(self.read(name), ["PATH_DB_DUMP"], name)
        for name in ("schema.sql", "migrations/001_init.sql", "seed.sql", "dumpster.txt", "dump.md", "dump.py"):
            with self.subTest(name=name):
                self.assertEqual(self.read(name), [], name)

    def test_database_backups(self):
        for name in ("prod.sql.bak", "app.db.bak", "x.sqlite.bak", "x.sqlite3.bak", "db-dump.bak", "database-2024.bak"):
            with self.subTest(name=name):
                self.assertEqual(self.read(name), ["PATH_DB_BACKUP"], name)
        for name in ("config.py.bak", "notes.bak", "feedback.bak", "app.sql", "x.bak.py"):
            with self.subTest(name=name):
                self.assertEqual(self.read(name), [], name)

    def test_home_folders(self):
        for rel, code in (
            (".aws/credentials", "PATH_AWS_DIR"),
            (".aws/config", "PATH_AWS_DIR"),
            (".aws", "PATH_AWS_DIR"),
            (".ssh/config", "PATH_SSH_DIR"),
            (".ssh/known_hosts", "PATH_SSH_DIR"),
            (".config/bmad-guard/credentials.json", "PATH_GUARD_CONFIG"),
            (".config/bmad-guard/sub/x", "PATH_GUARD_CONFIG"),
        ):
            with self.subTest(rel=rel):
                self.assertEqual(self.codes("Read", {"file_path": str(self.home / rel)}), [code], rel)
        for rel in (".config/other-tool/x", ".config/bmad-guard-notes", ".awsome/x", ".sshd/x", ".bashrc", "notes.txt"):
            with self.subTest(rel=rel):
                self.assertEqual(self.codes("Read", {"file_path": str(self.home / rel)}), [], rel)

    def test_a_project_folder_named_like_a_home_folder_is_fine(self):
        self.assertEqual(self.read(".ssh/config"), [])
        self.assertEqual(self.read(".aws/credentials"), [])

    def test_tilde_and_symlinks_are_resolved(self):
        self.assertEqual(self.codes("Read", {"file_path": "~/.ssh/config"}), ["PATH_SSH_DIR"])
        self.assertEqual(self.codes("Read", {"file_path": "~/.aws/credentials"}), ["PATH_AWS_DIR"])
        (self.home / ".ssh").mkdir()
        (self.home / ".ssh/config").write_text("fake")
        (self.project / "innocent.txt").symlink_to(self.home / ".ssh/config")
        self.assertEqual(self.read("innocent.txt"), ["PATH_SSH_DIR"])
        self.assertEqual(self.read("../project/./src/../innocent.txt"), ["PATH_SSH_DIR"])

    def test_write_edit_and_notebook_tools_use_the_same_list(self):
        for tool in ("Write", "Edit"):
            with self.subTest(tool=tool):
                self.assertEqual(self.read(".env", tool), ["PATH_ENV_FILE"])
                self.assertEqual(self.read(".env.example", tool), [])
        self.assertEqual(
            self.codes("NotebookEdit", {"notebook_path": str(self.project / "id_rsa")}), ["PATH_PRIVATE_KEY"]
        )
        self.assertEqual(self.codes("NotebookEdit", {"notebook_path": str(self.project / "a.ipynb")}), [])

    def test_glob_checks_pattern_and_path(self):
        for pattern in (
            "**/.env",
            "**/.env*",
            ".env.*",
            "**/*.pem",
            "*.key",
            "keys/*.p12",
            "**/*dump*.sql",
            "**/id_rsa*",
        ):
            with self.subTest(pattern=pattern):
                self.assertNotEqual(self.codes("Glob", {"pattern": pattern}), [], pattern)
        for pattern in ("**/*.py", "**/*.sql", "src/**", "*", "**/*.*", "**/.env.example", "**/*.md", "*.p*", "id_*"):
            with self.subTest(pattern=pattern):
                self.assertEqual(self.codes("Glob", {"pattern": pattern}), [], pattern)
        self.assertEqual(self.codes("Glob", {"pattern": "*", "path": str(self.home / ".ssh")}), ["PATH_SSH_DIR"])
        self.assertEqual(self.codes("Glob", {"pattern": "**/*", "path": "~/.aws"}), ["PATH_AWS_DIR"])
        self.assertEqual(self.codes("Glob", {"pattern": "~/.ssh/*"}), ["PATH_SSH_DIR"])

    def test_grep_checks_path_and_glob_not_the_search_text(self):
        self.assertEqual(
            self.codes("Grep", {"pattern": "TOKEN", "path": str(self.project / ".env")}), ["PATH_ENV_FILE"]
        )
        self.assertEqual(self.codes("Grep", {"pattern": "x", "path": str(self.home / ".ssh")}), ["PATH_SSH_DIR"])
        self.assertEqual(self.codes("Grep", {"pattern": "x", "glob": "*.pem"}), ["PATH_PRIVATE_KEY"])
        self.assertEqual(self.codes("Grep", {"pattern": "x", "glob": "**/.env*"}), ["PATH_ENV_FILE"])
        self.assertEqual(self.codes("Grep", {"pattern": ".env", "path": "src"}), [])
        self.assertEqual(self.codes("Grep", {"pattern": "id_rsa", "glob": "*.py"}), [])
        self.assertEqual(self.codes("Grep", {"pattern": "x"}), [])

    def test_missing_or_odd_path_arguments_are_not_a_path_hit(self):
        for tool_input in ({}, {"file_path": None}, {"file_path": 5}, {"file_path": ""}):
            with self.subTest(tool_input=tool_input):
                self.assertEqual(self.codes("Read", tool_input), [])

    def test_every_listed_pattern_is_blocked_by_the_hook(self):
        """The deny rules and the hook must be one list: build a sample file name from each pattern."""
        for code, patterns, carve_outs in hw.SENSITIVE_PATHS:
            for pattern in patterns:
                with self.subTest(pattern=pattern):
                    if pattern.startswith("~/"):
                        target = str(self.home / (pattern[2:-3] + "/some-file"))
                    else:
                        target = str(self.project / "sub" / pattern.replace("*", "x"))
                    self.assertEqual(self.codes("Read", {"file_path": target}), [code], target)
            for name in carve_outs:
                with self.subTest(carve_out=name):
                    self.assertEqual(self.codes("Read", {"file_path": str(self.project / name)}), [])


class BashPathTests(GuardTestCase):
    def test_commands_that_name_a_sensitive_path_are_blocked(self):
        for command, code in (
            ("cat .env", "PATH_ENV_FILE"),
            ("cat ./.env.local", "PATH_ENV_FILE"),
            ("cat 'config/.env'", "PATH_ENV_FILE"),
            ('cat ".env"', "PATH_ENV_FILE"),
            ("less prod.env", "PATH_ENV_FILE"),
            ("head -n 3 .env", "PATH_ENV_FILE"),
            ("cat .env*", "PATH_ENV_FILE"),
            ("cat < .env", "PATH_ENV_FILE"),
            ("echo SECRET > .env", "PATH_ENV_FILE"),
            ("echo SECRET >> .env.local", "PATH_ENV_FILE"),
            ("cat > .env <<EOF\nA=1\nEOF", "PATH_ENV_FILE"),
            ("cp .env /tmp/x", "PATH_ENV_FILE"),
            ("cp .env.example .env", "PATH_ENV_FILE"),
            ("mv x .env", "PATH_ENV_FILE"),
            ("source .env", "PATH_ENV_FILE"),
            ("grep KEY .env", "PATH_ENV_FILE"),
            ("grep -e KEY .env", "PATH_ENV_FILE"),
            ("git add .env", "PATH_ENV_FILE"),
            ("git diff -- .env", "PATH_ENV_FILE"),
            ("docker run --env-file .env img", "PATH_ENV_FILE"),
            ("docker run --env-file=.env img", "PATH_ENV_FILE"),
            ("tee prod.env", "PATH_ENV_FILE"),
            ("dd if=prod.env of=/tmp/x", "PATH_ENV_FILE"),
            ("scp server.pem host:", "PATH_PRIVATE_KEY"),
            ("scp -i deploy.key a host:b", "PATH_PRIVATE_KEY"),
            ("cp keys/tls.key /tmp/x", "PATH_PRIVATE_KEY"),
            ("openssl rsa -in server.pem -out x", "PATH_PRIVATE_KEY"),
            ("cat id_rsa", "PATH_PRIVATE_KEY"),
            ("ssh -i id_ed25519 host", "PATH_PRIVATE_KEY"),
            ("cat cert.p12", "PATH_KEY_STORE"),
            ("cat db_dump.sql", "PATH_DB_DUMP"),
            ("psql -h localhost app < dump.sql", "PATH_DB_DUMP"),
            ("gzip prod.sql.bak", "PATH_DB_BACKUP"),
            ("cat ~/.aws/credentials", "PATH_AWS_DIR"),
            ("ls ~/.aws", "PATH_AWS_DIR"),
            ("cat $HOME/.aws/config", "PATH_AWS_DIR"),
            ("cat ${HOME}/.aws/config", "PATH_AWS_DIR"),
            ("cat ~/.ssh/config", "PATH_SSH_DIR"),
            ("ls -la ~/.ssh", "PATH_SSH_DIR"),
            ("tar czf x.tgz ~/.ssh", "PATH_SSH_DIR"),
            ("cd ~/.ssh", "PATH_SSH_DIR"),
            ("cat ~/.ssh/*", "PATH_SSH_DIR"),
            ("cat ~/.config/bmad-guard/credentials.json", "PATH_GUARD_CONFIG"),
            ("ls ~/.config/bmad-guard", "PATH_GUARD_CONFIG"),
            ('cat "$HOME/.config/bmad-guard/x"', "PATH_GUARD_CONFIG"),
            (f"cat {self.home}/.ssh/id_rsa", "PATH_PRIVATE_KEY"),
        ):
            with self.subTest(command=command):
                self.assertIn(code, self.bash(command), command)

    def test_wrappers_pipes_and_nesting_do_not_hide_a_path(self):
        for command in (
            "sudo cat .env",
            "env A=1 cat .env",
            "cat .env | head",
            "ls && cat .env",
            "true || cat .env",
            "sh -c 'cat .env'",
            'bash -c "cd x && cat .env"',
            "echo $(cat .env)",
            "echo `cat .env`",
            "xargs cat .env",
            "find . -name .env",
            "find . -exec cat {} + -name .env",
            "cat <<EOF > .env\nA=1\nEOF",
            "eval 'cat .env'",
            "timeout 5 cat .env",
            "/bin/cat .env",
        ):
            with self.subTest(command=command):
                self.assertIn("PATH_ENV_FILE", self.bash(command), command)

    def test_close_but_safe_commands_are_allowed(self):
        self.assertAllows(
            "cat .env.example",
            "cp .env.example docs/env-sample.md",
            "git add .env.example",
            "git status",
            "ls -la",
            "cat id_test.py",
            "cat identity.md",
            "python id_utils.py",
            "cat schema.sql",
            "cat config.py.bak",
            "ls ~",
            "ls ~/.config",
            "cat ~/.bashrc",
            "cat ~/.config/other-tool/x",
            "cat .ssh/config",
            "cat src/.aws/x",
            "echo .env",
            "echo '.env is ignored' >> notes.txt",
            "echo .env >> .gitignore",
            "printf '%s\\n' .env >> .gitignore",
            "grep -rn '.env' src",
            "grep -rn .env src",
            "grep -e .env src",
            "grep --regexp=.env src",
            "rg '*.pem' src",
            "git commit -m 'remove .env from the repo'",
            "git log --oneline -- src",
            "cat <<EOF\n.env\nEOF",
            "cat .envelope",
            "cat env.sh",
            "cat .env.d/README.md",
            "node .env/bin/node",
            "ssh host",
            "aws s3 ls",
            "scp a.txt host:b",
        )

    def test_a_grep_pattern_is_not_a_path_but_the_files_it_reads_are(self):
        self.assertEqual(self.bash("grep -rn 'id_rsa' ."), [])
        self.assertEqual(self.bash("grep -rn 'id_rsa' id_rsa"), ["PATH_PRIVATE_KEY"])
        self.assertEqual(self.bash("grep -m 3 pattern .env"), ["PATH_ENV_FILE"])
        self.assertEqual(self.bash("grep -f .env x"), ["PATH_ENV_FILE"])

    def test_git_message_and_search_text_are_not_paths(self):
        self.assertAllows(
            "git log --grep=.env",
            "git log -S .env",
            "git log -G.env --oneline",
            "git log --author=.env",
            "git grep .env",
            "git grep -n -e .env -- src",
            "git commit -m .env",
            "git commit --message .env",
        )
        self.assertEqual(self.bash("git grep -f .env x"), ["PATH_ENV_FILE"])
        self.assertEqual(self.bash("git log -- .env"), ["PATH_ENV_FILE"])
        self.assertEqual(self.bash("git show HEAD:.env"), ["PATH_ENV_FILE"])

    def test_option_letters_of_other_commands_do_not_hide_a_path(self):
        for command in ("cat -e .env", "less -m .env", "head -S .env", "tail -G .env", "sed -e p .env", "cp -m .env x"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["PATH_ENV_FILE"], command)

    def test_a_link_to_a_sensitive_file_name_is_followed(self):
        (self.project / "link-one").symlink_to(self.project / "id_rsa")
        (self.project / "link-two").symlink_to(self.project / ".env.local")
        self.assertEqual(self.codes("Read", {"file_path": str(self.project / "link-one")}), ["PATH_PRIVATE_KEY"])
        self.assertEqual(self.bash("cat link-two"), ["PATH_ENV_FILE"])
        (self.project / "link-three").symlink_to(self.project / "readme.md")
        self.assertEqual(self.bash("cat link-three"), [])

    def test_relative_paths_are_resolved_from_the_call_directory(self):
        self.assertEqual(self.bash("cat .ssh/config", cwd=str(self.home)), ["PATH_SSH_DIR"])
        self.assertEqual(self.bash("cat ../home/.ssh/config"), ["PATH_SSH_DIR"])
        self.assertEqual(self.bash("cat .ssh/config"), [])

    def test_an_unreadable_command_is_reported_once_by_the_command_guard(self):
        self.assertEqual(self.bash("cat .env 'unclosed"), ["CMD_UNPARSABLE"])

    def test_one_reason_per_rule_however_many_words_match(self):
        self.assertEqual(self.bash("cat .env .env.local prod.env"), ["PATH_ENV_FILE"])
        self.assertEqual(
            sorted(self.bash("cat .env id_rsa ~/.ssh/config")), ["PATH_ENV_FILE", "PATH_PRIVATE_KEY", "PATH_SSH_DIR"]
        )


class EventTests(GuardTestCase):
    def test_events_and_messages_carry_the_rule_id_never_the_path(self):
        folder = "SECRET-FOLDER-NAME-987"
        raw = json.dumps(self.payload("Read", {"file_path": str(self.project / folder / ".env.local")}))
        code, out, err = self.entry(raw)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("PATH_ENV_FILE (path-guard)", err)
        self.assertNotIn(folder, err)
        events = queue(self.project)
        self.assertEqual([e["reason_code"] for e in events], ["PATH_ENV_FILE"])
        self.assertEqual(
            (events[0]["rule"], events[0]["tool"], events[0]["session_id"]), ("path-guard", "Read", "s-test")
        )
        self.assertEqual(events[0].get("path", ""), "")
        self.assertNotIn(folder, json.dumps(events))

    def test_a_bash_command_with_a_path_is_never_in_the_event(self):
        marker = "SECRET-COMMAND-MARKER-555"
        self.entry(json.dumps(self.payload("Bash", {"command": f"cat {marker}/.env && echo {marker}"})))
        events = queue(self.project)
        self.assertEqual([e["reason_code"] for e in events], ["PATH_ENV_FILE"])
        self.assertNotIn(marker, json.dumps(events))

    def test_an_allowed_read_records_nothing(self):
        raw = json.dumps(self.payload("Read", {"file_path": str(self.project / ".env.example")}))
        self.assertEqual(self.entry(raw), (0, "", ""))
        self.assertEqual(queue(self.project), [])

    def test_the_matcher_follows_the_home_variable(self):
        os.environ["HOME"] = str(self.project / "fakehome")
        self.assertEqual(
            self.codes("Read", {"file_path": str(self.project / "fakehome/.aws/credentials")}), ["PATH_AWS_DIR"]
        )


class MatcherTests(GuardTestCase):
    def matcher(self):
        return pg.Matcher(self.project, self.project)

    def test_wildcard_names_need_the_rule_specific_words(self):
        m = self.matcher()
        self.assertEqual(m.hits("*.pem"), ["PATH_PRIVATE_KEY"])
        self.assertEqual(m.hits(".env*"), ["PATH_ENV_FILE"])
        self.assertEqual(m.hits("*dump*.sql"), ["PATH_DB_DUMP"])
        for broad in ("*", "*.*", "*.sql", "*.p*", "dump*", "id_*", "*.bak"):
            with self.subTest(pattern=broad):
                self.assertEqual(m.hits(broad), [])

    def test_unknown_variables_do_not_hide_the_file_name(self):
        m = self.matcher()
        self.assertEqual(m.hits("$SOMEWHERE/.env"), ["PATH_ENV_FILE"])
        self.assertEqual(m.hits("$SOMEWHERE/readme.md"), [])
        self.assertEqual(m.hits("$ENVFILE"), [])

    def test_empty_text_has_no_hits(self):
        self.assertEqual(self.matcher().hits(""), [])
        self.assertEqual(self.matcher().hits("   "), [])


class ExtensionPathTests(GuardTestCase):
    def extension(self, data) -> None:
        self.write("_bmad/custom/project/guard/tool-rules.json", data if isinstance(data, str) else json.dumps(data))

    def test_a_project_path_rule_blocks_on_every_path_tool(self):
        self.extension(
            {
                "sensitive_paths": [
                    {"id": "payroll", "pattern": "payroll*.csv", "reason": "Payroll data."},
                    {"id": "exports", "pattern": "data/private/**"},
                ]
            }
        )
        self.assertEqual(
            self.codes("Read", {"file_path": str(self.project / "hr/payroll-2024.csv")}), ["PROJECT_PATH_PAYROLL"]
        )
        self.assertEqual(
            self.codes("Read", {"file_path": str(self.project / "data/private/x/y.txt")}), ["PROJECT_PATH_EXPORTS"]
        )
        self.assertEqual(self.codes("Glob", {"pattern": "**/payroll*.csv"}), ["PROJECT_PATH_PAYROLL"])
        self.assertBlocks("cat hr/payroll-2024.csv", "PROJECT_PATH_PAYROLL")
        self.assertBlocks("ls data/private", "PROJECT_PATH_EXPORTS")
        self.assertAllows("cat hr/payroll-policy.md", "ls data/public", "cat data/privates.txt")
        _, verdicts = self.call("Read", {"file_path": str(self.project / "payroll.csv")})
        self.assertEqual(verdicts[0].reason, "Payroll data.")
        _, verdicts = self.call("Read", {"file_path": str(self.project / "data/private/x")})
        self.assertEqual(verdicts[0].reason, "A project guard rule protects this path.")

    def test_a_bad_extension_file_blocks_every_path_tool_with_one_reason(self):
        self.extension("{ bad")
        for tool, tool_input in (
            ("Read", {"file_path": str(self.project / "x.py")}),
            ("Write", {"file_path": str(self.project / "x.py")}),
            ("Grep", {"pattern": "x"}),
            ("Glob", {"pattern": "**/*.py"}),
        ):
            with self.subTest(tool=tool):
                _, verdicts = self.call(tool, tool_input)
                self.assertEqual([(v.code, v.guard) for v in verdicts], [("EXT_BAD_FILE", "path-guard")])
                self.assertIn("tool-rules.json", verdicts[0].reason)
        _, verdicts = self.call("Bash", {"command": "cat x.txt"})
        self.assertEqual([(v.code, v.guard) for v in verdicts], [("EXT_BAD_FILE", "command-guard")])

    def test_a_project_path_rule_cannot_carve_out_a_built_in_one(self):
        self.extension({"sensitive_paths": [{"id": "allow-env", "pattern": ".env.example"}]})
        self.assertEqual(self.codes("Read", {"file_path": str(self.project / ".env")}), ["PATH_ENV_FILE"])
        self.assertEqual(
            self.codes("Read", {"file_path": str(self.project / ".env.example")}), ["PROJECT_PATH_ALLOW_ENV"]
        )


class CombinedTests(GuardTestCase):
    def test_command_and_path_reasons_come_in_one_message(self):
        raw = json.dumps(self.payload("Bash", {"command": "git push --force && rm -rf src && cat .env"}))
        code, _, err = self.entry(raw)
        self.assertEqual(code, 2)
        for reason in (
            "CMD_GIT_FORCE_PUSH (command-guard)",
            "CMD_RM_RECURSIVE (command-guard)",
            "PATH_ENV_FILE (path-guard)",
        ):
            self.assertIn(reason, err)
        self.assertEqual(err.count("[tool-guard] Blocked"), 1)
        self.assertEqual(
            sorted(e["reason_code"] for e in queue(self.project)),
            ["CMD_GIT_FORCE_PUSH", "CMD_RM_RECURSIVE", "PATH_ENV_FILE"],
        )

    def test_a_crash_in_the_path_guard_blocks_and_the_command_guard_still_reports(self):
        with mock.patch.object(pg.Matcher, "word_hits", side_effect=RuntimeError("boom-detail")):
            code, _, err = self.entry(json.dumps(self.payload("Bash", {"command": "git reset --hard"})))
        self.assertEqual(code, 2)
        self.assertIn("GUARD_ERROR (path-guard)", err)
        self.assertIn("CMD_GIT_RESET_HARD (command-guard)", err)
        self.assertNotIn("boom-detail", err)

    def test_the_real_script_blocks_and_allows_as_a_process(self):
        script = SCRIPTS / "pre_tool_use.py"
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.project)}

        def run(tool: str, tool_input: dict):
            return subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(self.payload(tool, tool_input)),
                capture_output=True,
                text=True,
                env=env,
                cwd=self.project,
            )

        blocked = run("Bash", {"command": "git push --force"})
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("CMD_GIT_FORCE_PUSH", blocked.stderr)
        blocked = run("Read", {"file_path": str(self.project / ".env")})
        self.assertEqual((blocked.returncode, blocked.stdout), (2, ""))
        self.assertIn("PATH_ENV_FILE", blocked.stderr)
        for tool, tool_input in (
            ("Bash", {"command": "git push"}),
            ("Read", {"file_path": str(self.project / ".env.example")}),
        ):
            allowed = run(tool, tool_input)
            self.assertEqual((allowed.returncode, allowed.stdout, allowed.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
