# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for the review record (Phase 2, T7): review_record.py, as registered in the PreToolUse dispatcher.

The hook writes the record. The tests check that the session id comes from the hook input and never from the
environment or the command, that only one plain command is recognised, that the agent's own writes to the record are
blocked, and that the command line side is only a receipt. Each test uses a fake project with one git commit.
Fake names and data only.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tests.guard_harness import ROOT, GuardTestCase, pt, queue

rr = importlib.import_module("review_record")
wg = importlib.import_module("write_gate")

SCRIPT = str(rr.SCRIPT)
CALL = f'python3 "{SCRIPT}" --story 2-3 --verdict pass'
REVIEW = "_bmad-output/guard/reviews/2-3.json"
GIT_ID = ("-c", "user.name=dev", "-c", "user.email=dev@example.invalid")


class ReviewCase(GuardTestCase):
    def setUp(self):
        super().setUp()
        self.git("init", "-q", "-b", "main")
        self.write("README.md", "# Fake project\n")
        self.git("add", "-A")
        self.git(*GIT_ID, "commit", "-q", "-m", "baseline")

    def git(self, *args: str) -> str:
        out = subprocess.run(["git", *args], cwd=self.project, capture_output=True, text=True)
        if out.returncode != 0:
            raise AssertionError(f"git {' '.join(args)}: {out.stderr}")
        return out.stdout.strip()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def record(self, rel: str = REVIEW) -> dict:
        return json.loads((self.project / rel).read_text(encoding="utf-8"))

    def run_record(self, command: str = CALL, session: str = "s-review", **extra):
        """(payload, verdicts) of one Bash call with the review record guard only."""
        raw = json.dumps(self.payload("Bash", {"command": command}, session_id=session, **extra))
        return self.run_raw(raw)

    def run_raw(self, raw: str, root: Path | None = None):
        return pt.run(raw, self.guards(("review_record",)), root=root or self.project)


class ArgumentTests(GuardTestCase):
    def test_both_orders_and_the_equals_form(self):
        self.assertEqual(rr.parse_args(["--story", "2-3", "--verdict", "pass"]), ("2-3", "pass"))
        self.assertEqual(rr.parse_args(["--verdict=fail", "--story=2-3"]), ("2-3", "fail"))

    def test_bad_arguments_are_refused(self):
        for argv in (
            [],
            ["--story", "2-3"],
            ["--verdict", "pass"],
            ["--story", "2-3", "--verdict", "maybe"],
            ["--story", "2-3", "--verdict", "PASS"],
            ["--story", "", "--verdict", "pass"],
            ["--story", "2-3", "--verdict", "pass", "--story", "9-9"],
            ["--story", "2-3", "--verdict", "pass", "--session", "s-other"],
            ["--story", "2-3", "--verdict", "pass", "extra"],
            ["--story", "2-3", "--verdict"],
            ["--story", "x" * 400, "--verdict", "pass"],
        ):
            with self.subTest(argv=argv), self.assertRaises(rr.ArgError):
                rr.parse_args(argv)

    def test_the_story_name_is_the_one_of_the_build_record(self):
        plan = wg.Plan(Path("plan.md"), "plan.md", "in-progress", {"story": "2-3"})
        self.assertEqual(rr.story_name("2-3"), wg.story_key(plan, None))
        path = "_bmad-output/implementation-artifacts/2-3-export-report.md"
        plan = wg.Plan(Path("p.md"), "p.md", "in-progress", {"story": path})
        self.assertEqual(rr.story_name(path), wg.story_key(plan, Path(path)))
        self.assertEqual(rr.story_name(path), "2-3-export-report")

    def test_a_story_value_cannot_leave_the_folder(self):
        for value in ("../../etc/passwd", "a/b/../../../x.md", ".hidden", "x y z", "a;b"):
            with self.subTest(value=value):
                name = rr.story_name(value)
                self.assertRegex(name, r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RecordTests(ReviewCase):
    def test_the_exact_command_writes_the_record(self):
        _, verdicts = self.run_record()
        self.assertEqual(verdicts, [])
        data = self.record()
        self.assertEqual(
            set(data), {"story", "reviewed_sha", "sessions", "verdict", "reviewed_at"}, "no content, no path"
        )
        self.assertEqual(data["story"], "2-3")
        self.assertEqual(data["reviewed_sha"], self.head())
        self.assertEqual(data["sessions"], ["s-review"])
        self.assertEqual(data["verdict"], "pass")
        datetime.strptime(data["reviewed_at"], "%Y-%m-%dT%H:%M:%SZ")

    def test_a_fail_verdict_is_recorded_too(self):
        self.run_record(f'python3 "{SCRIPT}" --story 2-3 --verdict fail')
        self.assertEqual(self.record()["verdict"], "fail")

    def test_the_session_id_comes_from_the_hook_input_never_from_the_environment(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "s-forged-env"}):
            self.run_record(session="s-from-stdin")
        self.assertEqual(self.record()["sessions"], ["s-from-stdin"])

    def test_a_session_id_typed_in_the_command_changes_nothing(self):
        self.run_record(f'CLAUDE_CODE_SESSION_ID=s-forged python3 "{SCRIPT}" --story 2-3 --verdict pass')
        self.assertEqual(self.record()["sessions"], ["s-review"])
        (self.project / REVIEW).unlink()
        _, verdicts = self.run_record(f'python3 "{SCRIPT}" --story 2-3 --verdict pass --session s-forged')
        self.assertEqual(verdicts, [])
        self.assertFalse((self.project / REVIEW).exists(), "bad arguments write nothing")

    def test_the_commit_is_head_at_the_time_of_the_call(self):
        first = self.head()
        self.run_record()
        self.assertEqual(self.record()["reviewed_sha"], first)
        self.write("src/app.py", "x = 1\n")
        self.git("add", "-A")
        self.git(*GIT_ID, "commit", "-q", "-m", "code")
        self.run_record()
        self.assertEqual(self.record()["reviewed_sha"], self.head())
        self.assertNotEqual(self.head(), first)

    def test_a_new_review_replaces_the_record_and_leaves_no_temp_file(self):
        self.run_record(session="s-one")
        self.run_record(f'python3 "{SCRIPT}" --story 2-3 --verdict fail', session="s-two")
        data = self.record()
        self.assertEqual((data["sessions"], data["verdict"]), (["s-two"], "fail"))
        self.assertEqual(sorted(p.name for p in (self.project / REVIEW).parent.iterdir()), ["2-3.json"])

    def test_a_story_path_names_the_record_like_the_build_record(self):
        path = "_bmad-output/implementation-artifacts/2-3-export-report.md"
        self.run_record(f'python3 "{SCRIPT}" --story {path} --verdict pass')
        self.assertTrue((self.project / "_bmad-output/guard/reviews/2-3-export-report.json").is_file())

    def test_the_variable_form_and_a_relative_path_are_recognised(self):
        skills = self.project / ".claude/skills"
        skills.parent.mkdir(parents=True)
        os.symlink(ROOT / "module/skills", skills)
        scripts = ".claude/skills/tool-guard/scripts/review_record.py"
        forms = (
            f'python3 "${{CLAUDE_PROJECT_DIR}}/{scripts}" --story 2-3 --verdict pass',
            f'python3 "$CLAUDE_PROJECT_DIR/{scripts}" --story 2-3 --verdict pass',
            f"python3 {scripts} --story 2-3 --verdict pass",
            f"python3.13 {self.project}/{scripts} --story 2-3 --verdict pass",
            f"python {scripts} --story 2-3 --verdict pass",
        )
        for command in forms:
            with self.subTest(command=command):
                path = self.project / REVIEW
                path.unlink(missing_ok=True)
                self.run_record(command)
                self.assertTrue(path.is_file())

    def put_project_copy(self, tool_dir: str) -> str:
        """A real copy (not a link) of the script in the project, as the setup script installs it."""
        dest = self.project / tool_dir / "tool-guard/scripts/review_record.py"
        dest.parent.mkdir(parents=True)
        dest.write_text((ROOT / "module/skills/tool-guard/scripts/review_record.py").read_text(encoding="utf-8"))
        return str(dest)

    def test_the_project_copy_is_recognised_when_the_hook_runs_from_elsewhere(self):
        """ADR 0008, break 1: with machine-level hooks `rr.SCRIPT` is the machine copy, but the review command names
        the project copy (the bmad-code-review override). MACH-3."""
        for tool_dir in (".claude/skills", ".agents/skills"):
            with self.subTest(tool_dir=tool_dir):
                copy = self.put_project_copy(tool_dir)
                rel = f"{tool_dir}/tool-guard/scripts/review_record.py"
                forms = (
                    f'python3 "{copy}" --story 2-3 --verdict pass',
                    f'python3 "${{CLAUDE_PROJECT_DIR}}/{rel}" --story 2-3 --verdict pass',
                    f"python3 {rel} --story 2-3 --verdict pass",
                )
                for command in forms:
                    path = self.project / REVIEW
                    path.unlink(missing_ok=True)
                    _, verdicts = self.run_record(command)
                    self.assertEqual(verdicts, [], command)
                    self.assertEqual(self.record()["sessions"], ["s-review"], command)
                shutil.rmtree(self.project / tool_dir.split("/")[0])

    def test_a_project_copy_that_is_not_there_or_sits_elsewhere_is_not_recognised(self):
        missing = self.project / ".claude/skills/tool-guard/scripts/review_record.py"
        self.put_project_copy(".claude/skills/other-skill")  # right file name, wrong skill folder
        elsewhere = self.put_project_copy("docs/skills")
        for command in (
            f'python3 "{missing}" --story 2-3 --verdict pass',
            f'python3 "{self.project}/.claude/skills/other-skill/tool-guard/scripts/review_record.py" '
            "--story 2-3 --verdict pass",
            f'python3 "{elsewhere}" --story 2-3 --verdict pass',
        ):
            with self.subTest(command=command):
                self.run_record(command)
                self.assertFalse((self.project / REVIEW).exists())

    def test_another_projects_copy_is_not_recognised(self):
        other = self.project.parent / "other-project/.claude/skills/tool-guard/scripts/review_record.py"
        other.parent.mkdir(parents=True)
        other.write_text("print('x')\n")
        self.run_record(f'python3 "{other}" --story 2-3 --verdict pass')
        self.assertFalse((self.project / REVIEW).exists())

    def test_a_command_that_is_not_the_one_plain_call_writes_nothing(self):
        other = self.project / "copy/review_record.py"
        other.parent.mkdir()
        other.write_text((ROOT / "module/skills/tool-guard/scripts/review_record.py").read_text(encoding="utf-8"))
        for command in (
            f'python3 "{SCRIPT}" --story 2-3 --verdict pass | cat',
            f'cd /tmp && python3 "{SCRIPT}" --story 2-3 --verdict pass',
            f'python3 "{SCRIPT}" --story 2-3 --verdict pass && echo done',
            f'python3 "{SCRIPT}" --story 2-3 --verdict pass; echo done',
            f'python3 "{SCRIPT}" --story 2-3 --verdict pass > out.txt',
            f'python3 "{SCRIPT}" --story "$(echo 2-3)" --verdict pass',
            f'python3 "{SCRIPT}" --story $STORY --verdict pass',
            f'S="{SCRIPT}"; python3 "$S" --story 2-3 --verdict pass',
            'python3 "$SCRIPT" --story 2-3 --verdict pass',
            f'python3 -I "{SCRIPT}" --story 2-3 --verdict pass',
            f"bash -c 'python3 \"{SCRIPT}\" --story 2-3 --verdict pass' x",
            f"python3 {other} --story 2-3 --verdict pass",
            f'node "{SCRIPT}" --story 2-3 --verdict pass',
            f'cat "{SCRIPT}"',
            "python3 -c 'print(1)'",
            f'python3 "{SCRIPT}.bak" --story 2-3 --verdict pass',
        ):
            with self.subTest(command=command):
                self.run_record(command)
                self.assertFalse((self.project / REVIEW).exists())

    def test_other_tools_and_other_commands_are_not_looked_at(self):
        raw = json.dumps(self.payload("Write", {"file_path": "x.txt", "content": CALL}))
        self.assertEqual(self.run_raw(raw)[1], [])
        self.assertEqual(self.run_record("git status")[1], [])
        self.assertFalse((self.project / "_bmad-output").exists())

    def test_a_call_from_a_subagent_is_blocked_and_writes_nothing(self):
        _, verdicts = self.run_record(agent_id="agent-1", agent_type="reviewer")
        self.assertEqual([v.code for v in verdicts], ["REVIEW_IN_SUBAGENT"])
        self.assertFalse((self.project / REVIEW).exists())

    def test_a_missing_or_bad_session_id_blocks(self):
        for session in (None, "", 7, "has space", "x" * 200):
            with self.subTest(session=session):
                payload = self.payload("Bash", {"command": CALL})
                payload["session_id"] = session
                _, verdicts = self.run_raw(json.dumps(payload))
                self.assertEqual([v.code for v in verdicts], ["REVIEW_NO_SESSION"])
        self.assertFalse((self.project / REVIEW).exists())

    def test_a_project_without_a_commit_blocks(self):
        fresh = self.project.parent / "fresh"
        fresh.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=fresh, check=True)
        raw = json.dumps({**self.payload("Bash", {"command": CALL}), "cwd": str(fresh)})
        _, verdicts = self.run_raw(raw, fresh)
        self.assertEqual([v.code for v in verdicts], ["REVIEW_NO_COMMIT"])

    def test_a_record_that_cannot_be_written_blocks_and_leaks_nothing(self):
        (self.project / "_bmad-output").mkdir()
        (self.project / "_bmad-output/guard").write_text("a file where the folder should be")
        _, verdicts = self.run_record()
        self.assertEqual([v.code for v in verdicts], ["REVIEW_RECORD_FAILED"])
        self.assertNotIn(str(self.project), verdicts[0].reason)

    def test_the_hook_blocks_with_exit_2_and_the_reason_on_stderr(self):
        raw = json.dumps(self.payload("Bash", {"command": CALL}, agent_id="agent-1"))
        code, out, err = self.entry(raw)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("REVIEW_IN_SUBAGENT (review-record)", err)

    def test_the_event_has_the_rule_id_the_verdict_and_no_content(self):
        self.run_record()
        events = [e for e in queue(self.project) if e["reason_code"] == "REVIEW_RECORDED"]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event["verdict"], event["detail"], event["rule"]), ("allow", "pass", "review-record"))
        text = json.dumps(event)
        for leak in (self.head(), str(self.project)):
            self.assertNotIn(leak, text)

    def test_the_event_carries_the_record_path_and_the_hash_of_the_record_file(self):
        self.run_record()
        event = [e for e in queue(self.project) if e["reason_code"] == "REVIEW_RECORDED"][0]
        file = self.project / REVIEW
        self.assertEqual(event["target_path"], REVIEW)
        self.assertEqual(event["target_sha256"], hashlib.sha256(file.read_bytes()).hexdigest())
        file.write_text(file.read_text().replace("pass", "fail"))  # an edit afterwards no longer matches the event
        self.assertNotEqual(event["target_sha256"], hashlib.sha256(file.read_bytes()).hexdigest())

    def test_the_whole_dispatcher_lets_the_call_through_and_writes_the_record(self):
        code, out, err = self.entry(json.dumps(self.payload("Bash", {"command": CALL}, session_id="s-whole")))
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(self.record()["sessions"], ["s-whole"])


class ForgeryTests(ReviewCase):
    """The agent cannot write the record itself: the guards block tool writes and (best effort) shell writes."""

    FORGED = json.dumps({"story": "2-3", "reviewed_sha": "a" * 40, "sessions": ["s-fresh"], "verdict": "pass"})

    def test_file_tools_are_blocked(self):
        path = str(self.project / REVIEW)
        for tool, tool_input in (
            ("Write", {"file_path": path, "content": self.FORGED}),
            ("Edit", {"file_path": path, "old_string": "a", "new_string": "b"}),
            ("MultiEdit", {"file_path": path, "edits": [{"old_string": "a", "new_string": "b"}]}),
            ("NotebookEdit", {"notebook_path": path, "new_source": "x"}),
            ("Write", {"file_path": "_bmad-output/guard/reviews/new-story.json", "content": self.FORGED}),
        ):
            with self.subTest(tool=tool):
                self.assertEqual(self.codes(tool, tool_input), ["DOC_GUARD_RECORD"])

    def test_shell_writes_are_blocked(self):
        for command in (
            f"echo '{self.FORGED}' > {REVIEW}",
            f"echo x >> {REVIEW}",
            f"tee {REVIEW} < /dev/null",
            f"cp /tmp/x.json {REVIEW}",
            f"mv /tmp/x.json {REVIEW}",
            f"sed -i 's/fail/pass/' {REVIEW}",
            f"rm {REVIEW}",
            "rm -r _bmad-output/guard",
            f"git checkout HEAD~1 -- {REVIEW}",
            f"cat <<EOF > {REVIEW}\nx\nEOF",
        ):
            with self.subTest(command=command):
                self.assertIn("DOC_GUARD_RECORD", self.bash(command))

    def test_the_record_command_with_a_redirect_into_the_record_is_blocked_and_writes_nothing(self):
        command = f'python3 "{SCRIPT}" --story 2-3 --verdict pass > {REVIEW}'
        self.assertIn("DOC_GUARD_RECORD", self.bash(command))
        self.assertFalse((self.project / REVIEW).exists())

    def test_the_script_itself_writes_no_record(self):
        """Run by the model without the hook, the command line side has nothing to forge."""
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(
                os.environ, {"CLAUDE_PROJECT_DIR": str(self.project), "CLAUDE_CODE_SESSION_ID": "s-forged"}
            ),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = rr.main(["--story", "2-3", "--verdict", "pass"])
        self.assertEqual(code, 1)
        self.assertFalse((self.project / "_bmad-output").exists())
        self.assertIn("no record was written", err.getvalue())

    def test_the_real_script_run_by_the_shell_writes_no_record_either(self):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.project), "CLAUDE_CODE_SESSION_ID": "s-forged"}
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--story", "2-3", "--verdict", "pass"],
            cwd=self.project,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse((self.project / "_bmad-output").exists())


class ReceiptTests(ReviewCase):
    def receipt(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.project)}),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = rr.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_after_the_hook_the_receipt_confirms_and_names_the_file(self):
        self.run_record()
        code, out, err = self.receipt("--story", "2-3", "--verdict", "pass")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Review record written for story 2-3", out)
        self.assertIn(self.head()[:12], out)
        self.assertIn("_bmad-output/guard/reviews/2-3.json", out)

    def test_without_the_hook_there_is_no_record_and_exit_1(self):
        code, out, err = self.receipt("--story", "2-3", "--verdict", "pass")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no record was written", err)

    def test_an_old_record_is_not_a_receipt(self):
        self.run_record()
        data = self.record()
        data["reviewed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        (self.project / REVIEW).write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.receipt("--story", "2-3", "--verdict", "pass")[0], 1)

    def test_a_record_for_another_verdict_or_commit_is_not_a_receipt(self):
        self.run_record()
        self.assertEqual(self.receipt("--story", "2-3", "--verdict", "fail")[0], 1)
        self.write("src/app.py", "x = 1\n")
        self.git("add", "-A")
        self.git(*GIT_ID, "commit", "-q", "-m", "code")
        self.assertEqual(self.receipt("--story", "2-3", "--verdict", "pass")[0], 1)

    def test_a_broken_record_is_not_a_receipt(self):
        for text in ("not json", "[]", '{"reviewed_at": 5}', "{}"):
            with self.subTest(text=text):
                self.write(REVIEW, text)
                self.assertEqual(self.receipt("--story", "2-3", "--verdict", "pass")[0], 1)

    def test_bad_arguments_exit_2_and_help_exits_0(self):
        code, _, err = self.receipt("--story", "2-3")
        self.assertEqual(code, 2)
        self.assertIn("usage", err)
        code, out, _ = self.receipt("--help")
        self.assertEqual(code, 0)
        self.assertIn("usage", out)
