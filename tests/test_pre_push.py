# /// script
# requires-python = ">=3.10"
# ///
"""Tests for the pre-push hook (Phase 2, T7): real throwaway git repositories and a real bare remote.

Every push here is a real `git push` to a bare repository in the temp folder, so git starts the installed hook through
core.hooksPath. Records are written as the hooks would write them (the same JSON); one test makes the review record
with the real tool-guard hook and the real command, end to end. Fake data only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from tests.hook_harness import HOOKS_REL, MARKER, HookTestCase

REVIEWS = "_bmad-output/guard/reviews"
BUILDS = "_bmad-output/guard/builds"
ZERO = "0" * 40


class PushCase(HookTestCase):
    def setUp(self):
        super().setUp()
        self.remote = self.project.parent / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True, env=self.env)
        self.git("remote", "add", "origin", str(self.remote))
        # The baseline commit holds a source file (README.md): it goes up without the hook, as set-up.
        self.git("push", "-q", "--no-verify", "origin", "main")
        self.git("checkout", "-q", "-b", "feature/GUARD-9-export")

    # --- building a branch --------------------------------------------------------------------------------------

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def code(self, name: str = "app", text: str = "x = 1\n") -> str:
        """A commit that changes source. Returns its sha."""
        self.write(f"src/{name}.py", text)
        self.commit_all(f"code {name}")
        return self.head()

    def docs(self, name: str = "notes") -> str:
        self.write(f"docs/{name}.md", "# Notes\n")
        self.commit_all(f"docs {name}")
        return self.head()

    def build(self, story: str = "2-3", sessions: tuple[str, ...] = ("s-build",)) -> None:
        text = {"story": story, "sessions": list(sessions), "first_at": "2026-11-25T10:00:00Z", "last_at": "x"}
        self.write(f"{BUILDS}/{story}.json", json.dumps(text))

    def review(self, sha: str, story: str = "2-3", session: str = "s-review", verdict: str = "pass") -> None:
        text = {
            "story": story,
            "reviewed_sha": sha,
            "sessions": [session],
            "verdict": verdict,
            "reviewed_at": "2026-11-25T11:00:00Z",
        }
        self.write(f"{REVIEWS}/{story}.json", json.dumps(text, indent=2) + "\n")

    def commit_records(self) -> str:
        self.commit_all("record")
        return self.head()

    def reviewed_branch(self) -> str:
        """code, then a review of it, then the record commit. Returns the reviewed sha."""
        sha = self.code()
        self.build()
        self.review(sha)
        self.commit_records()
        return sha

    # --- the push ------------------------------------------------------------------------------------------------

    def push(self, *refs: str, check: bool = False) -> subprocess.CompletedProcess:
        return self.git("push", "origin", *(refs or ("HEAD",)), check=check)

    def on_remote(self, ref: str) -> bool:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", ref], cwd=self.remote, capture_output=True, text=True, env=self.env
        )
        return proc.returncode == 0

    def assertBlocked(self, proc: subprocess.CompletedProcess, *codes: str) -> None:
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("the push is blocked", proc.stderr)
        for code in codes:
            self.assertIn(code, proc.stderr)
        self.assertFalse(self.on_remote("feature/GUARD-9-export"), "a blocked push sends nothing")

    def assertSent(self, proc: subprocess.CompletedProcess) -> None:
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.on_remote("feature/GUARD-9-export"))

    def hook(self, lines: str = "", *args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        """The installed hook run by hand with git's input."""
        return subprocess.run(
            [sys.executable, str(self.project / HOOKS_REL / "pre-push"), *(args or ("origin", str(self.remote)))],
            cwd=self.project,
            input=lines,
            capture_output=True,
            text=True,
            env={**self.env, **(extra_env or {})},
        )


class AllowTests(PushCase):
    def test_a_valid_record_lets_the_push_through(self):
        self.reviewed_branch()
        proc = self.push()
        self.assertSent(proc)
        self.assertEqual(proc.stderr.count("bmad-guard pre-push"), 0)
        self.assertIn("PUSH_ALLOWED", self.codes())

    def test_a_valid_record_for_a_branch_with_several_source_commits(self):
        self.code("one")
        sha = self.code("two")
        self.build()
        self.review(sha)
        self.commit_records()
        self.assertSent(self.push())

    def test_commits_without_source_changes_need_no_record(self):
        self.docs()
        self.write("_bmad-output/planning-artifacts/prd.md", "# PRD\n")
        self.write(".claude/settings.json", "{}\n")
        self.commit_all("planning")
        self.assertSent(self.push())
        self.assertEqual(self.codes(), ["PUSH_NOTHING_TO_CHECK"])

    def test_a_branch_delete_is_allowed(self):
        self.reviewed_branch()
        self.assertSent(self.push())
        self.git("checkout", "-q", "main")
        proc = self.git("push", "origin", "--delete", "feature/GUARD-9-export", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.on_remote("feature/GUARD-9-export"))
        self.assertEqual(self.codes()[-1], "PUSH_NOTHING_TO_CHECK")

    def test_a_delete_line_by_hand_is_allowed(self):
        proc = self.hook(f"(delete) {ZERO} refs/heads/old {'a' * 40}\n")
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))

    def test_a_push_of_nothing_is_allowed(self):
        proc = self.hook("")
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))

    def test_a_new_branch_is_checked_against_the_remote_tracking_refs(self):
        """Zero remote sha: only the commits that no ref of the remote has are looked at."""
        self.docs()
        self.assertSent(self.push())
        lines = f"refs/heads/other {self.head()} refs/heads/other {ZERO}\n"
        proc = self.hook(lines)
        self.assertEqual((proc.returncode, proc.stderr), (0, ""), "the baseline source commit is already on origin")

    def test_an_existing_branch_checks_only_the_new_commits(self):
        self.reviewed_branch()
        self.assertSent(self.push())
        self.docs()
        proc = self.push()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.codes()[-1], "PUSH_NOTHING_TO_CHECK")

    def test_a_second_branch_with_commits_the_remote_already_has_is_allowed(self):
        self.reviewed_branch()
        self.assertSent(self.push())
        proc = self.git("push", "origin", "HEAD:refs/heads/copy", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_project_allowed_path_is_not_source(self):
        self.write(
            "_bmad/custom/project/guard/tool-rules.json",
            json.dumps({"write_gate_allowed_paths": ["scripts/dev-tools"]}),
        )
        self.write("scripts/dev-tools/run.sh", "echo hi\n")
        self.commit_all("tool")
        self.assertSent(self.push())

    def test_a_tag_on_a_commit_the_remote_has_is_allowed(self):
        self.git("tag", "v0", "main")
        proc = self.git("push", "origin", "v0", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_ref_that_is_not_a_commit_is_allowed(self):
        tree = self.git("rev-parse", "HEAD^{tree}").stdout.strip()
        proc = self.hook(f"refs/tags/t {tree} refs/tags/t {ZERO}\n")
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))

    def test_the_records_are_read_from_the_pushed_commit_not_the_work_tree(self):
        self.reviewed_branch()
        self.review("b" * 40, verdict="fail", session="s-build")  # edited after the commit, not committed
        self.assertSent(self.push())


class BlockTests(PushCase):
    def test_a_missing_record_blocks(self):
        self.code()
        proc = self.push()
        self.assertBlocked(proc, "PUSH_NO_RECORD")
        self.assertIn("bmad-code-review", proc.stderr)
        self.assertIn("Do not use --no-verify", proc.stderr)

    def test_a_record_that_is_not_committed_does_not_count(self):
        sha = self.code()
        self.review(sha)  # in the work tree only
        self.assertBlocked(self.push(), "PUSH_NO_RECORD")

    def test_the_same_session_blocks(self):
        sha = self.code()
        self.build(sessions=("s-build", "s-same"))
        self.review(sha, session="s-same")
        self.commit_records()
        proc = self.push()
        self.assertBlocked(proc, "PUSH_SAME_SESSION")
        self.assertNotIn("PUSH_NO_RECORD", proc.stderr)
        self.assertNotIn("s-same", proc.stderr, "no session id in the message")

    def test_a_session_that_built_any_story_cannot_review_another(self):
        """The story name in a review record is typed by the model, so every build record counts."""
        sha = self.code()
        self.build("1-1", ("s-old-build",))
        self.build("2-3", ("s-build",))
        self.review(sha, story="9-9", session="s-old-build")
        self.commit_records()
        self.assertBlocked(self.push(), "PUSH_SAME_SESSION")

    def test_a_stale_sha_blocks(self):
        """The record points at a commit that is not in the pushed history (amended, rebased, another branch)."""
        self.git("checkout", "-q", "-b", "elsewhere", "main")
        other = self.code("other")
        self.git("checkout", "-q", "feature/GUARD-9-export")
        self.code()
        self.build()
        self.review(other)
        self.commit_records()
        proc = self.push()
        self.assertBlocked(proc, "PUSH_RECORD_STALE")
        self.assertIn(other[:10], proc.stderr)

    def test_a_sha_that_is_not_in_the_repository_is_stale(self):
        self.code()
        self.review("c" * 40)
        self.commit_records()
        self.assertBlocked(self.push(), "PUSH_RECORD_STALE")

    def test_an_amended_commit_makes_the_record_stale(self):
        sha = self.reviewed_branch()
        self.git("checkout", "-q", sha)
        self.git("commit", "-q", "--amend", "--no-verify", "-m", "code, amended")
        amended = self.head()
        self.git("checkout", "-q", "-B", "feature/GUARD-9-export", amended)
        self.assertNotEqual(sha, amended)
        self.review(sha)
        self.commit_records()
        self.assertBlocked(self.push(), "PUSH_RECORD_STALE")

    def test_commits_after_the_reviewed_sha_block(self):
        self.reviewed_branch()
        late = self.code("late")
        proc = self.push()
        self.assertBlocked(proc, "PUSH_COMMITS_AFTER_REVIEW")
        self.assertIn(late[:10], proc.stderr)
        self.assertIn("1 commit(s)", proc.stderr)

    def test_a_source_change_inside_the_record_commit_is_not_covered(self):
        sha = self.code()
        self.review(sha)
        self.write("src/sneak.py", "y = 2\n")
        self.commit_all("record and code together")
        self.assertBlocked(self.push(), "PUSH_COMMITS_AFTER_REVIEW")

    def test_a_commit_after_the_review_that_changes_no_source_is_fine(self):
        self.reviewed_branch()
        self.docs()
        self.assertSent(self.push())

    def test_a_failing_verdict_blocks(self):
        sha = self.code()
        self.review(sha, verdict="fail")
        self.commit_records()
        proc = self.push()
        self.assertBlocked(proc, "PUSH_VERDICT_NOT_PASS")
        self.assertNotIn("PUSH_NO_RECORD", proc.stderr)

    def test_only_the_verdict_pass_passes(self):
        sha = self.code()
        for verdict in ("PASS", "passed", "ok", "", "pass-with-notes"):
            with self.subTest(verdict=verdict):
                self.review(sha, verdict=verdict)
                self.commit_records()
                self.assertBlocked(self.push(), "PUSH_VERDICT_NOT_PASS")

    def test_every_reason_comes_in_one_message(self):
        self.git("checkout", "-q", "-b", "elsewhere", "main")
        other = self.code("other")
        self.git("checkout", "-q", "feature/GUARD-9-export")
        self.code()
        self.build(sessions=("s-build",))
        self.review(other, session="s-build", verdict="fail")
        self.commit_records()
        proc = self.push()
        self.assertBlocked(proc, "PUSH_VERDICT_NOT_PASS", "PUSH_SAME_SESSION", "PUSH_RECORD_STALE")
        self.assertEqual(proc.stderr.count("the push is blocked (3 reasons)"), 1)
        self.assertEqual(proc.stderr.count("bmad-guard pre-push"), 1)

    def test_a_record_in_a_wrong_format_blocks(self):
        self.code()
        for text in (
            "not json",
            "[]",
            "{}",
            json.dumps({"story": "x", "reviewed_sha": "zz", "sessions": ["s"], "verdict": "pass"}),
        ):
            with self.subTest(text=text):
                self.write(f"{REVIEWS}/2-3.json", text)
                self.commit_records()
                self.assertBlocked(self.push(), "PUSH_RECORD_UNREADABLE")

    def test_a_build_record_that_cannot_be_read_blocks(self):
        sha = self.code()
        self.review(sha)
        self.write(f"{BUILDS}/2-3.json", "not json")
        self.commit_records()
        self.assertBlocked(self.push(), "PUSH_BUILD_UNREADABLE")

    def test_an_old_record_with_a_problem_is_not_this_pushes_business(self):
        """A stale record for another story, left in the tree by an earlier push, adds no reason."""
        self.git("checkout", "-q", "-b", "elsewhere", "main")
        other = self.code("other")
        self.git("checkout", "-q", "feature/GUARD-9-export")
        self.review(other, story="1-1")
        self.commit_all("old stale record")
        self.git("push", "-q", "--no-verify", "origin", "HEAD:refs/heads/old")
        sha = self.code()
        self.review(sha)
        self.commit_records()
        self.assertSent(self.push())

    def test_an_annotated_tag_on_unreviewed_source_blocks(self):
        self.code()
        self.git("tag", "-a", "-m", "release", "v1")
        proc = self.git("push", "origin", "v1", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("PUSH_NO_RECORD", proc.stderr)

    def test_one_bad_ref_blocks_the_whole_push(self):
        self.reviewed_branch()
        self.git("branch", "good")
        self.git("checkout", "-q", "-b", "bad", "main")
        self.code("unreviewed")
        proc = self.git("push", "origin", "good", "bad", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("refs/heads/bad", proc.stderr)
        self.assertNotIn("refs/heads/good", proc.stderr)
        self.assertFalse(self.on_remote("good"), "git sends nothing when the hook blocks")


class CrashTests(PushCase):
    def test_a_missing_guard_skill_blocks_with_a_clear_message(self):
        self.code()
        shutil.rmtree(self.project / ".claude/skills/tool-guard")
        proc = self.push()
        self.assertBlocked(proc, "PUSH_ERROR")
        self.assertIn("setup_guard.py", proc.stderr)
        self.assertEqual(self.codes(), ["PUSH_ERROR"])

    def test_a_check_that_cannot_load_blocks(self):
        (self.project / HOOKS_REL / "guard_pre_push.py").write_text("raise RuntimeError('broken')\n")
        proc = self.hook("")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("the push is blocked: the check cannot start (RuntimeError)", proc.stderr)

    def test_bad_input_from_git_blocks(self):
        for text in ("garbage\n", "a b c\n", "a b c d e\n"):
            with self.subTest(text=text):
                proc = self.hook(text)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("PUSH_ERROR", proc.stderr)

    def test_a_sha_that_git_does_not_know_blocks(self):
        proc = self.hook(f"refs/heads/x {'d' * 40} refs/heads/x {ZERO}\n")
        self.assertEqual(proc.returncode, 0, "an object that is not there is not a commit: nothing to review")

    def test_a_broken_git_blocks(self):
        proc = self.hook(f"refs/heads/x {self.head()} refs/heads/x {ZERO}\n", extra_env={"PATH": "/nonexistent"})
        self.assertEqual(proc.returncode, 1)


class EventTests(PushCase):
    def test_events_hold_rule_ids_and_counts_never_content(self):
        sha = self.code(MARKER, f"secret_value = '{MARKER}'\n")
        self.build(sessions=(f"s-{MARKER}",))
        self.review(sha, session=f"s-{MARKER}")
        self.git("checkout", "-q", "-b", f"feature/{MARKER}")
        self.commit_records()
        self.git("push", "origin", "HEAD", check=False)
        events = self.events()
        self.assertEqual([e["reason_code"] for e in events], ["PUSH_SAME_SESSION"])
        event = events[0]
        self.assertEqual((event["guard"], event["stage"], event["verdict"]), ("git-hooks", "pre-push", "block"))
        self.assertEqual((event["commit_count"], event["source_count"]), ("2", "1"))
        queue = (self.project / ".bmad-guard/events/queue.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(MARKER, queue)
        self.assertNotIn("2-3", queue)
        self.assertNotIn("s-review", queue)

    def test_an_allowed_push_leaves_one_allow_event_with_counts(self):
        self.reviewed_branch()
        self.assertSent(self.push())
        events = self.events()
        self.assertEqual([e["reason_code"] for e in events], ["PUSH_ALLOWED"])
        self.assertEqual((events[0]["verdict"], events[0]["commit_count"]), ("allow", "2"))

    def test_the_hook_never_sends_events(self):
        self.reviewed_branch()
        proc = subprocess.run(
            ["git", "push", "origin", "HEAD"],
            cwd=self.project,
            capture_output=True,
            text=True,
            env={
                **self.env,
                "BMAD_GUARD_ENDPOINT": "http://127.0.0.1:9",
                "BMAD_GUARD_USER": "u",
                "BMAD_GUARD_KEY": "k",
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("event", proc.stderr)


class EndToEndTests(PushCase):
    """The review record is made by the real tool-guard hook, from the real command, as Claude Code would run it."""

    def hook_call(self, command: str, session: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": session,
            "cwd": str(cwd or self.project),
        }
        script = self.project / ".claude/skills/tool-guard/scripts/pre_tool_use.py"
        env = {**self.env, "CLAUDE_PROJECT_DIR": str(self.project), "CLAUDE_CODE_SESSION_ID": "s-forged-env"}
        return subprocess.run(
            [sys.executable, str(script)], input=json.dumps(payload), capture_output=True, text=True, env=env
        )

    def record_command(self, verdict: str = "pass") -> str:
        script = self.project / ".claude/skills/tool-guard/scripts/review_record.py"
        return f'python3 "{script}" --story 2-3 --verdict {verdict}'

    def test_review_in_a_fresh_session_then_push(self):
        self.code()
        self.build(sessions=("s-build",))
        proc = self.hook_call(self.record_command(), "s-fresh-review")
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
        self.commit_records()
        self.assertSent(self.push())
        recorded = json.loads((self.project / f"{REVIEWS}/2-3.json").read_text(encoding="utf-8"))
        self.assertEqual(recorded["sessions"], ["s-fresh-review"])

    def test_the_build_session_recording_its_own_review_is_stopped_at_the_push(self):
        self.code()
        self.build(sessions=("s-build",))
        self.hook_call(self.record_command(), "s-build")
        self.commit_records()
        self.assertBlocked(self.push(), "PUSH_SAME_SESSION")

    def test_typing_another_session_id_does_not_help_the_build_session(self):
        """The environment says s-forged-env, the command s-fresh, the hook input s-build: the record says s-build."""
        self.code()
        self.build(sessions=("s-build",))
        command = f"CLAUDE_CODE_SESSION_ID=s-fresh {self.record_command()}"
        self.hook_call(command, "s-build")
        self.assertEqual(json.loads((self.project / f"{REVIEWS}/2-3.json").read_text())["sessions"], ["s-build"])
        self.commit_records()
        self.assertBlocked(self.push(), "PUSH_SAME_SESSION")

    def test_the_review_record_commit_passes_the_pre_commit_hook(self):
        """The record is committed with a real `git commit`, so the pre-commit hook reads it (no false block)."""
        self.code()
        self.build()
        self.hook_call(self.record_command(), "s-fresh-review")
        self.git("add", "--", REVIEWS, BUILDS)
        proc = self.commit("GUARD-9: review record")
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))
