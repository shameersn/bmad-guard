# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for module/skills/tool-guard/scripts/command_guard.py: one block and one close-but-safe allow per rule.

Fake commands and fake hosts only (example.com, example.invalid). Nothing is run.
"""

from __future__ import annotations

import importlib
import json
import os
import unittest
from unittest import mock

from tests.guard_harness import GuardTestCase, queue

cg = importlib.import_module("command_guard")  # the harness put the scripts folder on sys.path
gc = importlib.import_module("guard_config")


class DestructiveCommandTests(GuardTestCase):
    def test_rm_recursive_outside_scratch_is_blocked_inside_it_is_allowed(self):
        for command in (
            "rm -rf src",
            "rm -rf ./src/app",
            "rm -fr src",
            "rm -r -f src",
            "rm --recursive --force src",
            "rm -Rf src",
            "rm -r src",
            "rm -rf /usr/local/lib/thing",
            "rm -rf /",
            "rm -rf ~",
            "rm -rf .",
            "rm -rf ..",
            "rm -rf *",
            "rm -rf tmp*",
            "rm -rf tmp/..",
            "rm -rf $HOME/x",
            "rm -rf $(pwd)",
            "rm -rf tmp/x src",
            "rm -rf {tmp,src}",
            "rm -rf /tmp",
            "rm --no-preserve-root /",
            "/bin/rm -rf src",
            "sudo rm -rf src",
            "sudo -u root rm -rf src",
            "env X=1 rm -rf src",
            "nohup rm -rf src",
            "timeout 5 rm -rf src",
            "command rm -rf src",
            "\\rm -rf src",
            "ls && rm -rf src",
            "true || rm -rf src",
            "echo ok; rm -rf src",
            "echo ok | rm -rf src",
            "sh -c 'rm -rf src'",
            'bash -c "cd x && rm -rf src"',
            "bash -lc 'rm -rf src'",
            "echo $(rm -rf src)",
            "echo `rm -rf src`",
            "ls | xargs rm -rf",
            "find . -name x | xargs rm -rf",
            "find . -name x -exec rm -rf {} +",
            "xargs -I{} rm -rf {}",
            "echo src | xargs rm -rf",
            "eval 'rm -rf src'",
            "bash <<EOF\nrm -rf src\nEOF",
            "if true; then rm -rf src; fi",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["CMD_RM_RECURSIVE"], command)

    def test_rm_in_scratch_folders_or_without_recursion_is_allowed(self):
        self.assertAllows(
            "rm file.txt",
            "rm -f file.txt",
            "rm src/old.py",
            "rm -rf tmp",
            "rm -rf ./tmp/x",
            "rm -rf tmp/x/../y",
            "rm -rf tmp/*",
            "rm -rf build dist coverage",
            "rm -rf node_modules",
            "rm -rf packages/web/node_modules",
            "rm -rf src/__pycache__ .pytest_cache",
            "rm -rf .bmad-guard/tmp/queue",
            "rm -rf /tmp/some-build-dir",
            "rm -rf /var/tmp/x",
            "sudo rm -rf tmp/x",
            "sh -c 'rm -rf tmp/x'",
            "echo tmp/a tmp/b | xargs rm -rf",
            "find tmp -name '*.o' -exec rm -rf {} +",
            "rm -rf",
            "echo 'rm -rf src'",
            "git rm -r --cached src",
            "ls -R src",
        )

    def test_the_system_temp_folder_is_scratch_but_not_the_folder_itself(self):
        self.assertAllows(f"rm -rf {self.system_tmp}/build-1", f"rm -rf {self.system_tmp / 'a' / 'b'}")
        self.assertBlocks(f"rm -rf {self.system_tmp}", "CMD_RM_RECURSIVE")
        self.assertBlocks(f"rm -rf {self.system_tmp}/../project", "CMD_RM_RECURSIVE")

    def test_rm_cannot_escape_scratch_through_dotdot_or_a_symlink(self):
        (self.project / "tmp").mkdir()
        (self.project / "tmp/link").symlink_to(self.project / "src", target_is_directory=True)
        self.assertBlocks("rm -rf tmp/../src", "CMD_RM_RECURSIVE")
        self.assertBlocks("rm -rf tmp/link/x", "CMD_RM_RECURSIVE")

    def test_rm_resolves_relative_paths_from_the_call_directory(self):
        (self.project / "src").mkdir()
        self.assertEqual(self.bash("rm -rf tmp", cwd=str(self.project / "src")), ["CMD_RM_RECURSIVE"])
        self.assertEqual(self.bash("rm -rf ../tmp", cwd=str(self.project / "src")), [])

    def test_find_delete_outside_scratch_is_blocked(self):
        for command in ("find . -name '*.o' -delete", "find src -delete", "find -delete", "find / -name x -delete"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["CMD_FIND_DELETE"], command)
        self.assertAllows("find tmp -name '*.o' -delete", "find . -name '*.o'", "find src -type f")

    def test_git_force_push_all_forms(self):
        for command in (
            "git push --force",
            "git push -f",
            "git push -f origin main",
            "git push origin main --force",
            "git push --force-with-lease",
            "git push --force-with-lease=main:abc123",
            "git push --force-if-includes",
            "git push origin +main",
            "git push origin +HEAD:main",
            "git push -uf origin main",
            "git push --forc origin",
            "git -C other push --force",
            "git -c user.name=x push -f",
            "sudo git push -f",
            "cd x && git push -f",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["CMD_GIT_FORCE_PUSH"], command)

    def test_git_push_without_force_is_allowed(self):
        self.assertAllows(
            "git push",
            "git push origin main",
            "git push -u origin feature/GUARD-2-x",
            "git push -n",
            "git push --dry-run",
            "git push --tags",
            "git push origin HEAD:refs/heads/x",
            "git pull --force",
            "git fetch -f",
            "git log -f",
        )

    def test_git_push_that_deletes_remote_refs(self):
        for command in (
            "git push origin --delete old-branch",
            "git push -d origin old-branch",
            "git push origin :old-branch",
            "git push origin :refs/tags/v1",
            "git push --mirror",
            "git push --prune origin",
            "git -C other push origin --delete x",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["CMD_GIT_PUSH_DELETE"], command)

    def test_git_push_delete_lookalikes_are_allowed(self):
        self.assertAllows(
            "git push origin feature/a:feature/a",
            "git push origin HEAD:main",
            "git branch -d merged",
            "git fetch --prune",
            "git remote prune origin",
            "git tag -d v1",
        )

    def test_git_reset_hard(self):
        self.assertBlocks("git reset --hard", "CMD_GIT_RESET_HARD")
        self.assertBlocks("git reset --hard HEAD~1", "CMD_GIT_RESET_HARD")
        self.assertBlocks("git -C x reset --hard origin/main", "CMD_GIT_RESET_HARD")
        self.assertAllows("git reset", "git reset --soft HEAD~1", "git reset --mixed HEAD~1", "git reset HEAD file")

    def test_git_clean_force(self):
        for command in ("git clean -f", "git clean -fd", "git clean -fdx", "git clean -xdf", "git clean --force -d"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["CMD_GIT_CLEAN"], command)
        self.assertAllows("git clean -n", "git clean -nd", "git clean --dry-run", "git clean -fn", "git clean -d -n")

    def test_git_branch_force_delete(self):
        for command in (
            "git branch -D old",
            "git branch -d -f old",
            "git branch --delete --force old",
            "git branch -df old",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["CMD_GIT_BRANCH_DELETE"], command)
        self.assertAllows(
            "git branch -d old", "git branch --delete old", "git branch new", "git branch -a", "git branch -m a b"
        )

    def test_git_no_verify(self):
        for command in (
            "git commit --no-verify -m x",
            "git commit -m x --no-verify",
            "git push --no-verify",
            "git merge --no-verify topic",
            "git rebase --no-verify main",
            "git commit --no-verif -m x",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["CMD_GIT_NO_VERIFY"], command)
        for command in ("git commit -n -m x", "git commit -nm x", "git commit -anm x", "git commit -m x -n"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["CMD_GIT_COMMIT_SKIP_HOOKS"], command)
        self.assertAllows(
            "git commit -m x",
            "git commit -am x",
            "git commit -m 'skip -n and --no-verify in text'",
            'git commit -m "--no-verify"',
            "git commit -F msg.txt",
            "git commit --amend --no-edit",
            "git push -n",
            "git log -n 5",
            "git diff -n",
            "git verify-commit HEAD",
        )

    def test_git_commit_with_a_heredoc_message_is_allowed(self):
        self.assertAllows("git commit -m \"$(cat <<'EOF'\nFix: don't crash\n\nBody\nEOF\n)\"")

    def test_git_hooks_path_changes_are_blocked_reads_are_allowed(self):
        for command in (
            "git config core.hooksPath /dev/null",
            "git config --local core.hooksPath .git/hooks",
            "git config --global core.hooksPath x",
            "git config core.hookspath x",
            "git config --unset core.hooksPath",
            "git config --unset-all core.hooksPath",
            "git config --add core.hooksPath x",
            "git config --replace-all core.hooksPath x",
            "git -c core.hooksPath=/dev/null commit -m x",
            "git -ccore.hooksPath=/dev/null commit -m x",
            "git --config-env=core.hooksPath=X commit -m x",
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/x git commit -m x",
            "sh -c 'git config core.hooksPath x'",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["CMD_GIT_HOOKS_PATH"], command)
        self.assertAllows(
            "git config core.hooksPath",
            "git config --get core.hooksPath",
            "git config --local --get core.hooksPath",
            "git config --list",
            "git config user.name 'Dev'",
            "git config core.autocrlf true",
            "git -c user.name=x commit -m y",
        )

    def test_chmod_world_writable_recursive(self):
        for command in (
            "chmod -R 777 .",
            "chmod -R 0777 src",
            "chmod -R 666 src",
            "chmod --recursive 777 .",
            "chmod -Rv 777 .",
            "chmod -R a+rwx .",
            "chmod -R o+w .",
            "chmod -R ugo=rwx .",
            "sudo chmod -R 777 /var/www",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["CMD_CHMOD_WORLD_WRITABLE"], command)
        self.assertAllows(
            "chmod -R 755 .",
            "chmod -R 644 src",
            "chmod -R u+w .",
            "chmod -R go-w .",
            "chmod -R +x scripts",
            "chmod 777 one-file",
            "chmod +x run.sh",
        )


class SqlTests(GuardTestCase):
    def test_destructive_sql_in_clients_is_blocked(self):
        for command in (
            'psql -h localhost -c "DROP TABLE users"',
            "psql -c 'drop database app'",
            'psql -c "TRUNCATE users"',
            'psql -c "TRUNCATE TABLE users CASCADE"',
            'psql -c "DELETE FROM users"',
            'psql -c "delete from users;"',
            'psql -c "DELETE FROM users WHERE 1=1"',
            'psql -c "DELETE FROM users WHERE true"',
            'psql -c "SELECT 1; DROP TABLE users"',
            'psql -c "BEGIN; DELETE FROM users; COMMIT"',
            'psql -c "/* tidy */ DROP INDEX idx"',
            'psql -c "select 1" -c "drop table users"',
            "mysql -e 'DROP TABLE users' app",
            "mysql --execute='TRUNCATE users' app",
            'sqlite3 app.db "DELETE FROM users"',
            "echo 'DROP TABLE users;' | psql",
            "psql <<< 'DROP TABLE users'",
            "psql <<EOF\nSELECT 1;\nDELETE FROM users;\nEOF",
            "cat <<EOF | mysql app\nTRUNCATE users;\nEOF",
            "sh -c \"psql -c 'DROP TABLE users'\"",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["CMD_SQL_DESTRUCTIVE"], command)

    def test_safe_sql_is_allowed(self):
        self.assertAllows(
            'psql -h localhost -c "SELECT * FROM users"',
            'psql -c "DELETE FROM users WHERE id = 7"',
            "psql -c \"DELETE FROM users WHERE name = 'a' AND id > 3\"",
            "psql -c \"INSERT INTO notes VALUES ('drop table users')\"",
            "psql -c \"SELECT 'delete from users' AS text\"",
            "psql -c \"UPDATE users SET name = 'x' WHERE id = 1\"",
            'psql -c "CREATE TABLE drop_log (id int)"',
            'psql -c "-- DROP TABLE users\nSELECT 1"',
            "mysql -e 'SELECT 1' app",
            "echo 'SELECT 1;' | psql",
            "psql <<EOF\nSELECT 1;\nEOF",
            "echo 'drop table users' > notes.txt",
            "git commit -m 'drop table users'",
            "grep -r 'DROP TABLE' migrations",
        )

    def test_sql_script_files_are_read(self):
        self.write("cleanup.sql", "-- reset\nDROP TABLE users;\n")
        self.write("report.sql", "SELECT count(*) FROM users;\n")
        self.assertBlocks("psql -h localhost -f cleanup.sql", "CMD_SQL_DESTRUCTIVE")
        self.assertBlocks("psql --file=cleanup.sql", "CMD_SQL_DESTRUCTIVE")
        self.assertBlocks("mysql app < cleanup.sql", "CMD_SQL_DESTRUCTIVE")
        self.assertAllows("psql -f report.sql", "mysql app < report.sql", "psql -f missing.sql")

    def test_mysql_force_flag_is_not_a_script_file(self):
        self.write("cleanup.sql", "DROP TABLE users;\n")
        self.assertAllows("mysql -f app cleanup.sql")


class DataExportTests(GuardTestCase):
    def test_database_clients_against_a_remote_host_are_blocked(self):
        for command in (
            "psql -h db.prod.example.com -U app -c 'select 1'",
            "psql -hdb.prod.example.com app",
            "psql --host=db.prod.example.com app",
            "psql --host db.prod.example.com app",
            "psql postgres://app:pw@db.prod.example.com:5432/app",
            "psql postgresql://db.prod.example.com/app",
            "psql 'postgres://app:p@ss@db.prod.example.com/app'",
            "psql -d postgres://db.prod.example.com/app",
            "psql 'host=db.prod.example.com dbname=app'",
            "psql -h 10.1.2.3 app",
            "psql -h localhost,db.prod.example.com app",
            "PGHOST=db.prod.example.com psql app",
            "env PGHOST=db.prod.example.com psql app",
            "pg_dump -h db.prod.example.com app",
            "pg_dump postgres://db.prod.example.com/app",
            "pg_dumpall -h db.prod.example.com",
            "mysql -h db.prod.example.com -u app -p app",
            "mysql -hdb.prod.example.com app",
            "mysql --host=db.prod.example.com app",
            "mariadb -h db.prod.example.com app",
            "mysqldump -h db.prod.example.com app",
            "MYSQL_HOST=db.prod.example.com mysql app",
            "mongo --host db.prod.example.com",
            "mongosh --host=rs0/db1.example.com:27017,db2.example.com",
            "mongosh mongodb://db.prod.example.com:27017/app",
            "mongosh 'mongodb+srv://u:p@cluster0.example.com/app'",
            "mongosh db.prod.example.com:27017/app",
            "mongodump --host db.prod.example.com",
            "mongodump -h db.prod.example.com",
            "mongodump --uri='mongodb://db.prod.example.com/app'",
            "mongoexport --host=db.prod.example.com -d app -c users",
            "bash -c 'pg_dump -h db.prod.example.com app'",
            "pg_dump -h db.prod.example.com app | gzip > out.gz",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["EXPORT_DB_REMOTE"], command)

    def test_database_clients_against_a_local_host_are_allowed(self):
        self.assertAllows(
            "psql -h localhost -U app app",
            "psql -h 127.0.0.1 app",
            "psql -h 127.0.0.5 app",
            "psql -h ::1 app",
            "psql -h '[::1]' app",
            "psql -h /var/run/postgresql app",
            "psql app",
            "psql",
            "psql -U app -c 'select 1'",
            "psql postgres://app@localhost/app",
            "psql postgresql:///app",
            "psql 'postgresql:///app?host=/var/run/postgresql'",
            "psql 'host=localhost dbname=app'",
            "psql -d app -h localhost",
            "pg_dump app > out.sql",
            "pg_dump -h localhost app",
            "mysql -h localhost app",
            "mysql -u root -phunter2 app",
            "mysql -u root -p app",
            "mysql -S /tmp/mysql.sock app",
            "mysqldump -h 127.0.0.1 app",
            "mongosh",
            "mongosh app",
            "mongosh localhost/app",
            "mongosh mongodb://localhost:27017/app",
            "mongosh mongodb://127.0.0.1/app",
            "mongosh --host localhost",
            "mongosh --eval 'db.stats()' app",
            "mongosh script.js",
            "mongodump --host localhost --db app",
            "mongodump --uri=mongodb://localhost/app",
            "PGHOST=localhost psql app",
            "PGHOST=/var/run/postgresql psql app",
            "echo psql -h db.prod.example.com",
            "grep psql README.md",
        )

    def test_a_remote_host_from_the_environment_is_blocked(self):
        with mock.patch.dict(os.environ, {"PGHOST": "db.prod.example.com"}):
            self.assertBlocks("psql app", "EXPORT_DB_REMOTE")
            self.assertAllows("psql -h localhost app")
        with mock.patch.dict(os.environ, {"MYSQL_HOST": "db.prod.example.com"}):
            self.assertBlocks("mysqldump app", "EXPORT_DB_REMOTE")
        with mock.patch.dict(os.environ, {"PGHOST": "localhost"}):
            self.assertAllows("psql app")

    def test_a_host_that_cannot_be_known_is_blocked_with_its_own_code(self):
        for command in (
            'psql "$DATABASE_URL"',
            "psql -h $DB_HOST app",
            'mysql -h "$HOST" app',
            "psql --service=prod",
            "psql 'service=prod'",
            "mysql --defaults-file=client.cnf app",
            "mysql --defaults-extra-file=client.cnf app",
            "mysql --login-path=prod app",
            'mongosh "$MONGO_URL"',
            "mongodump --uri=$MONGO_URL",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["EXPORT_DB_HOST_UNKNOWN"], command)
        with mock.patch.dict(os.environ, {"PGSERVICE": "prod"}):
            self.assertBlocks("psql app", "EXPORT_DB_HOST_UNKNOWN")
            self.assertAllows("psql -h localhost app")
        self.assertAllows("psql -h localhost -c \"select '$X'\" app")

    def test_s3_download_is_blocked_upload_and_listing_are_allowed(self):
        for command in (
            "aws s3 cp s3://bucket/key .",
            "aws s3 cp s3://bucket/key ./out/file",
            "aws s3 cp s3://bucket/key -",
            "aws s3 cp s3://bucket/dir ./dir --recursive",
            "aws s3 sync s3://bucket/dir ./dir",
            "aws s3 sync s3://bucket/dir s3://other/dir",
            "aws s3 mv s3://bucket/key .",
            "aws --profile prod s3 cp s3://bucket/key .",
            "aws s3 cp s3://bucket/key . --profile prod --region eu-west-1",
            'aws s3 cp "$SRC" ./out',
            "sudo aws s3 cp s3://bucket/key .",
            "sh -c 'aws s3 sync s3://bucket/dir ./dir'",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["EXPORT_S3_DOWNLOAD"], command)
        self.assertAllows(
            "aws s3 cp ./file s3://bucket/key",
            "aws s3 cp - s3://bucket/key",
            "aws s3 sync ./dir s3://bucket/dir",
            "aws s3 cp ./file s3://bucket/key --profile prod",
            "aws --profile prod s3 cp ./file s3://bucket/key",
            'aws s3 cp "$SRC" s3://bucket/key',
            "aws s3 ls",
            "aws s3 ls s3://bucket/dir",
            "aws s3 presign s3://bucket/key",
            "aws sts get-caller-identity",
            "aws s3 cp ./a ./b",
        )


class ParsingTests(GuardTestCase):
    def test_unparsable_commands_are_blocked(self):
        for command in ("echo 'unclosed", 'echo "unclosed', "echo $(unclosed", "echo `unclosed", "echo ${unclosed"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), ["CMD_UNPARSABLE"], command)

    def test_deeply_nested_commands_are_blocked(self):
        command = "echo " + "$(echo " * 9 + "x" + ")" * 9
        self.assertEqual(self.bash(command), ["CMD_UNPARSABLE"])

    def test_missing_or_wrong_command_text_is_blocked(self):
        for tool_input in ({}, {"command": None}, {"command": 5}, {"command": ["rm", "-rf", "x"]}, {"cmd": "ls"}):
            with self.subTest(tool_input=tool_input):
                self.assertEqual(self.codes("Bash", tool_input), ["CMD_UNPARSABLE"])

    def test_empty_command_is_allowed(self):
        self.assertAllows("", "   ", "# only a comment")

    def test_comments_with_quotes_are_fine(self):
        self.assertAllows("git status # don't panic", "# it's fine\ngit status")

    def test_each_rule_reports_once_for_many_matches(self):
        self.assertBlocks("rm -rf src; rm -rf lib; rm -rf docs", "CMD_RM_RECURSIVE")

    def test_several_rules_are_all_reported_together(self):
        self.assertBlocks(
            "git push --force && git reset --hard && rm -rf src && psql -h db.example.com app",
            "CMD_GIT_FORCE_PUSH",
            "CMD_GIT_RESET_HARD",
            "CMD_RM_RECURSIVE",
            "EXPORT_DB_REMOTE",
        )

    def test_other_tools_are_not_checked_by_the_command_guard(self):
        verdicts = self.call("Read", {"file_path": "rm -rf src"})[1]
        self.assertEqual([v.guard for v in verdicts], [])

    def test_wrappers_and_odd_spellings_do_not_hide_a_rule(self):
        for command in (
            "$'\\x72m' -rf src",
            "'r'm -rf src",
            "r\\m -rf src",
            "/usr/bin/env -i /bin/rm -rf src",
            "sudo env A=1 nice -n 5 rm -rf src",
            "RM -RF src",
            "git\tpush\t--force",
            "git push \\\n --force",
            "(rm -rf src)",
            "{ rm -rf src; }",
        ):
            with self.subTest(command=command):
                self.assertNotEqual(self.bash(command), [], command)


class MessageAndEventTests(GuardTestCase):
    def test_block_message_lists_each_reason_code_once_and_never_the_command(self):
        marker = "SECRET-REMOTE-MARKER-123"
        code, out, err = self.entry(
            json.dumps(self.payload("Bash", {"command": f"git push --force {marker}; rm -rf src/{marker}"}))
        )
        self.assertEqual((code, out), (2, ""))
        self.assertIn("CMD_GIT_FORCE_PUSH (command-guard)", err)
        self.assertIn("CMD_RM_RECURSIVE (command-guard)", err)
        self.assertNotIn(marker, err)
        self.assertEqual(err.count("CMD_RM_RECURSIVE"), 1)

    def test_events_carry_rule_id_tool_and_session_never_the_command_text(self):
        marker = "SECRET-REMOTE-MARKER-123"
        command = f"git push --force {marker}; psql -h db.{marker}.example.com -c 'DROP TABLE {marker}'"
        self.entry(json.dumps(self.payload("Bash", {"command": command})))
        events = queue(self.project)
        self.assertEqual(
            sorted(e["reason_code"] for e in events), ["CMD_GIT_FORCE_PUSH", "CMD_SQL_DESTRUCTIVE", "EXPORT_DB_REMOTE"]
        )
        for event in events:
            self.assertEqual((event["guard"], event["verdict"], event["tool"]), ("tool-guard", "block", "Bash"))
            self.assertEqual((event["rule"], event["session_id"]), ("command-guard", "s-test"))
            self.assertEqual(event.get("path", ""), "")
        self.assertNotIn(marker, json.dumps(events))
        self.assertNotIn("psql", json.dumps(events))

    def test_an_allowed_command_records_nothing(self):
        code, out, err = self.entry(json.dumps(self.payload("Bash", {"command": "git push origin main"})))
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(queue(self.project), [])

    def test_malformed_hook_input_blocks_with_the_guards_registered(self):
        for raw in ("", "{not json", "[]", json.dumps({"tool_name": "Bash"}), json.dumps({"tool_input": {}})):
            with self.subTest(raw=raw):
                code, _, err = self.entry(raw)
                self.assertEqual(code, 2)
                self.assertIn("BAD_HOOK_INPUT", err)

    def test_a_guard_that_crashes_blocks_the_call(self):
        with mock.patch.object(cg, "check_rm_recursive", side_effect=RuntimeError("boom-detail")):
            with mock.patch.dict(cg.CHECKS, {"rm_recursive": cg.check_rm_recursive}):
                code, _, err = self.entry(json.dumps(self.payload("Bash", {"command": "rm -rf src"})))
        self.assertEqual(code, 2)
        self.assertIn("GUARD_ERROR (command-guard)", err)
        self.assertNotIn("boom-detail", err)
        self.assertEqual([e["reason_code"] for e in queue(self.project)], ["GUARD_ERROR"])

    def test_unreadable_builtin_rules_block(self):
        with mock.patch.object(gc, "load_builtin", side_effect=gc.ConfigError("x")):
            code, _, err = self.entry(json.dumps(self.payload("Bash", {"command": "ls"})))
        self.assertEqual(code, 2)
        self.assertIn("GUARD_ERROR", err)

    def test_a_built_in_rule_naming_a_missing_check_blocks(self):
        rules = [{"id": "CMD_X", "code": "CMD_X", "reason": "x", "check": "no_such_check"}]
        broken = gc.Rules(tuple(rules), (), ("localhost",), ())
        with mock.patch.object(gc, "load", return_value=broken):
            code, _, err = self.entry(json.dumps(self.payload("Bash", {"command": "ls"})))
        self.assertEqual(code, 2)
        self.assertIn("GUARD_ERROR (command-guard)", err)


if __name__ == "__main__":
    unittest.main()
