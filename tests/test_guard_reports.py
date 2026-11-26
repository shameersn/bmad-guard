"""Tests for deploy/collector/reports/guard_reports.py: a real throwaway git repo and a fake query. No Docker."""

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("guard_reports", ROOT / "deploy/collector/reports/guard_reports.py")
gr = importlib.util.module_from_spec(spec)
sys.modules["guard_reports"] = gr
spec.loader.exec_module(gr)

ID = ["-c", "user.name=Fake Dev", "-c", "user.email=fake@example.invalid", "-c", "commit.gpgsign=false"]
REVIEWS = gr.REVIEWS_REL


class RepoCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        out = subprocess.run(["git", *ID, *args], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def commit(self, name, text="x\n"):
        (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / name).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"add {name}")
        return self.git("rev-parse", "HEAD"), self.git("rev-parse", "HEAD^{tree}")


def fake_query(rows):
    seen = []

    def query(sql, params):
        seen.append((sql, params))
        return rows

    query.seen = seen
    return query


class CommitReportTests(RepoCase):
    def run_report(self, events, **kw):
        out = io.StringIO()
        code = gr.run_commits(self.repo, "HEAD", fake_query(events), kw.get("project", ""), False, out=out)
        return code, out.getvalue()

    def test_a_commit_with_an_allow_event_for_its_tree_is_clean(self):
        _, tree = self.commit("a.txt")
        code, text = self.run_report([{"tree": tree, "verdict": "allow", "user": "dev-a"}])
        self.assertEqual(code, 0)
        self.assertIn("1 in HEAD; 0 without", text)

    def test_a_commit_with_no_event_is_reported_and_exit_is_1(self):
        sha, _ = self.commit("a.txt")
        code, text = self.run_report([])
        self.assertEqual(code, 1)
        self.assertIn(f"no-event     {sha[:12]}", text)

    def test_a_blocked_only_tree_does_not_count_as_seen(self):
        sha, tree = self.commit("a.txt")
        code, text = self.run_report([{"tree": tree, "verdict": "block", "user": "dev-a"}])
        self.assertEqual(code, 1)
        self.assertIn(f"blocked-only {sha[:12]}", text)

    def test_only_the_unseen_commit_of_two_is_reported(self):
        _, tree1 = self.commit("a.txt")
        sha2, _ = self.commit("b.txt")
        code, text = self.run_report([{"tree": tree1, "verdict": "allow", "user": "dev-a"}])
        self.assertEqual(code, 1)
        self.assertIn(sha2[:12], text)
        self.assertIn("2 in HEAD; 1 without", text)

    def test_merge_commits_are_skipped_by_default(self):
        self.commit("a.txt")
        self.git("checkout", "-q", "-b", "side")
        self.commit("b.txt")
        self.git("checkout", "-q", "main")
        self.commit("c.txt")
        self.git("merge", "-q", "--no-ff", "-m", "merge", "side")
        merged = gr.list_commits(self.repo, "HEAD")
        self.assertEqual(len(merged), 3)  # a, b, c; not the merge
        self.assertEqual(len(gr.list_commits(self.repo, "HEAD", merges=True)), 4)

    def test_the_query_is_fixed_text_with_typed_parameters(self):
        self.commit("a.txt")
        query = fake_query([])
        gr.run_commits(self.repo, "HEAD", query, "my-project", False, out=io.StringIO())
        sql, params = query.seen[0]
        self.assertTrue(sql.startswith("SELECT"))
        self.assertNotIn("my-project", sql)
        self.assertEqual(params["project"], "my-project")

    def test_a_tree_value_that_is_not_a_hash_is_ignored(self):
        found = gr.commit_events(fake_query([{"tree": "'; DROP TABLE x", "verdict": "allow", "user": "u"}]), 0)
        self.assertEqual(found, {})

    def test_a_range_that_is_not_a_range_is_a_report_error(self):
        self.commit("a.txt")
        with self.assertRaises(gr.ReportError):
            gr.list_commits(self.repo, "no-such-branch..HEAD")


class ReviewReportTests(RepoCase):
    def record(self, story, **over):
        data = {"story": story, "reviewed_sha": "a" * 40, "sessions": ["s"], "verdict": "pass", **over}
        return (json.dumps(data, indent=2) + "\n").encode()

    def put(self, story, data):
        self.commit(f"{REVIEWS}/{story}.json", data.decode())

    def run_report(self, hashes):
        out = io.StringIO()
        events = [{"sha": h, "user": "dev-a"} for h in hashes]
        code = gr.run_reviews(self.repo, "HEAD", fake_query(events), out=out)
        return code, out.getvalue()

    def test_a_record_with_an_event_for_its_hash_is_clean(self):
        data = self.record("2-3")
        self.put("2-3", data)
        code, text = self.run_report([hashlib.sha256(data).hexdigest()])
        self.assertEqual(code, 0)
        self.assertIn("1 record(s) at HEAD; 0 without", text)

    def test_a_hand_made_record_is_reported(self):
        self.put("2-3", self.record("2-3"))
        code, text = self.run_report([hashlib.sha256(b"something else").hexdigest()])
        self.assertEqual(code, 1)
        self.assertIn(f"no-event       {REVIEWS}/2-3.json", text)

    def test_a_record_edited_after_the_hook_wrote_it_is_reported(self):
        original = self.record("2-3", verdict="fail")
        self.put("2-3", self.record("2-3", verdict="pass"))
        code, _ = self.run_report([hashlib.sha256(original).hexdigest()])
        self.assertEqual(code, 1)

    def test_a_valid_record_copied_to_another_story_is_reported(self):
        data = self.record("2-3")
        self.put("9-9", data)  # same bytes, so the hash has an event, but the story field is 2-3
        code, text = self.run_report([hashlib.sha256(data).hexdigest()])
        self.assertEqual(code, 1)
        self.assertIn("story-mismatch", text)

    def test_no_records_is_clean(self):
        self.commit("a.txt")
        code, text = self.run_report([])
        self.assertEqual(code, 0)
        self.assertIn("no review records", text)

    def test_files_outside_the_folder_or_in_subfolders_are_not_records(self):
        self.commit("other/2-3.json", "{}")
        self.commit(f"{REVIEWS}/sub/x.json", "{}")
        self.assertEqual(gr.review_records(self.repo, "HEAD"), [])

    def test_the_record_is_read_from_the_ref_not_the_work_tree(self):
        data = self.record("2-3")
        self.put("2-3", data)
        (self.repo / REVIEWS / "2-3.json").write_text("{}")  # edited, not committed
        self.assertEqual(gr.review_records(self.repo, "HEAD")[0][1], data)


class ClientTests(unittest.TestCase):
    def test_a_missing_secret_is_a_report_error_and_no_secret_in_the_message(self):
        with self.assertRaises(gr.ReportError) as ctx:
            gr.query_from_env({})
        self.assertIn("GUARD_REPORT_PASSWORD", str(ctx.exception))

    def test_plain_http_is_for_localhost_only(self):
        gr.make_query("http://127.0.0.1:8123", "u", "p")
        with self.assertRaises(gr.ReportError):
            gr.make_query("http://db.example.internal:8123", "u", "p")
        gr.make_query("https://db.example.internal", "u", "p")

    def test_only_select_is_sent(self):
        query = gr.make_query("http://127.0.0.1:1", "u", "p")
        with self.assertRaises(gr.ReportError) as ctx:
            query("INSERT INTO x VALUES (1)", {})
        self.assertIn("only SELECT", str(ctx.exception))

    def test_an_unreachable_server_is_a_report_error(self):
        query = gr.make_query("http://127.0.0.1:1", "u", "p", timeout=1)
        with self.assertRaises(gr.ReportError):
            query("SELECT 1", {})

    def test_the_cli_exits_2_when_the_report_cannot_run(self):
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as empty, unittest.mock.patch("sys.stderr", err):
            code = gr.main(["reviews", "--repo", empty], query=fake_query([]))
        self.assertEqual(code, 2)
        self.assertIn("guard_reports:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
