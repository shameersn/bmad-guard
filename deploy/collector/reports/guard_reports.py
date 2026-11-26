# /// script
# requires-python = ">=3.10"
# ///
"""Two audit reports that compare the guard events in ClickHouse with a git repository (ADR 0002, ADR 0003 update).

    python3 guard_reports.py commits --repo <dir> [--range origin/main..HEAD] [--project NAME] [--include-merges]
    python3 guard_reports.py reviews --repo <dir> [--ref HEAD]

commits   Commits in the range that no pre-commit hook saw. The pre-commit event carries `tree`, the id of the staged
          tree. A commit is "seen" when its tree has an ALLOW event from the pre-commit hook. A tree that only has
          BLOCK events was refused, and then committed some other way (`--no-verify`): reported as blocked-only.
reviews   Review records (`_bmad-output/guard/reviews/*.json`) in the repo that no REVIEW_RECORDED event matches. The
          hook's event carries the SHA-256 of the record file as written. A record written by hand, or edited after
          the hook wrote it, has no event with its hash.

Both exit 0 when nothing is reported, 1 when something is, 2 when a report cannot run (no query, bad repo).
Routine `commits` runs: `--range origin/main..<feature branch>`, BEFORE any rebase (a rebase makes new trees, which
would be reported as false alarms). What they cannot see is in the output footer and in
deploy/collector/README.md ("Audit reports").

The database is read through the ClickHouse HTTP interface as a READ-ONLY user (`grafana_reader` or one like it),
never as `otel_writer`. Settings come from the environment, never from the command line (a command line is visible in
the process list):
    GUARD_REPORT_URL        default http://127.0.0.1:8123
    GUARD_REPORT_USER       default grafana_reader
    GUARD_REPORT_PASSWORD   required
Standard library only. All queries are fixed text with typed parameters; nothing from the repo is put into SQL text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8123"
DEFAULT_USER = "grafana_reader"
REVIEWS_REL = "_bmad-output/guard/reviews"
TREE_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DAY = 86400
RETENTION_DAYS = 180  # TTL of the table (deploy/collector/clickhouse/init/01-schema.sh)
QueryFn = Callable[[str, dict[str, str]], list[dict]]


class ReportError(Exception):
    """The report cannot run. The message is safe to print (no key, no query result)."""


# --- ClickHouse (read only) ----------------------------------------------------------------------------------------


def make_query(url: str, user: str, secret: str, timeout: float = 20.0) -> QueryFn:
    """A function (sql, params) -> rows. Plain http is allowed for localhost only. Only SELECT is sent."""
    parts = urllib.parse.urlsplit(url)
    local = parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")
    if not (parts.scheme == "https" or local):
        raise ReportError("GUARD_REPORT_URL must be https (plain http only for localhost)")

    def query(sql: str, params: dict[str, str]) -> list[dict]:
        if not sql.lstrip().upper().startswith("SELECT"):
            raise ReportError("only SELECT queries are sent")
        args = {"default_format": "JSONEachRow", **{f"param_{k}": v for k, v in params.items()}}
        request = urllib.request.Request(  # noqa: S310 - scheme checked above
            f"{url.rstrip('/')}/?{urllib.parse.urlencode(args)}",
            data=sql.encode(),
            headers={"X-ClickHouse-User": user, "X-ClickHouse-Key": secret},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as err:  # the body may quote the query; print the status only
            raise ReportError(
                f"ClickHouse answered HTTP {err.code} (check the read-only user and its password)"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise ReportError(f"ClickHouse is not reachable ({type(err).__name__})") from None
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    return query


def query_from_env(env: dict[str, str] | None = None) -> QueryFn:
    env = os.environ if env is None else env
    secret = env.get("GUARD_REPORT_PASSWORD", "")
    if not secret:
        raise ReportError("set GUARD_REPORT_PASSWORD (the read-only user's password) in the environment")
    return make_query(env.get("GUARD_REPORT_URL", DEFAULT_URL), env.get("GUARD_REPORT_USER", DEFAULT_USER), secret)


# --- git -----------------------------------------------------------------------------------------------------------


def git(repo: Path, *args: str) -> bytes:
    try:
        proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)
    except OSError as err:
        raise ReportError(f"git cannot be run ({type(err).__name__})") from None
    if proc.returncode != 0:
        raise ReportError(f"git {args[0]} failed: {proc.stderr.decode(errors='replace').strip()[:200]}")
    return proc.stdout


@dataclass(frozen=True)
class Commit:
    sha: str
    tree: str
    authored: int  # Unix time; an amend or a rebase keeps it
    subject: str


def list_commits(repo: Path, rev_range: str, merges: bool = False) -> list[Commit]:
    """Commits in the range, newest first. Merge commits are skipped unless asked for (git runs no pre-commit hook
    for a clean merge). `--end-of-options` stops a range from being read as an option."""
    args = ["log", "--format=%H%x00%T%x00%at%x00%s%x1e"]
    if not merges:
        args.append("--no-merges")
    out = git(repo, *args, "--end-of-options", rev_range).decode("utf-8", errors="replace")
    commits = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, tree, at, subject = record.split("\x00", 3)
        commits.append(Commit(sha, tree, int(at), subject))
    return commits


def review_records(repo: Path, ref: str) -> list[tuple[str, bytes]]:
    """(path, bytes) of every `*.json` file directly in the reviews folder at `ref`."""
    out = git(repo, "ls-tree", "-r", "--name-only", "-z", "--end-of-options", ref, "--", REVIEWS_REL)
    records = []
    for raw in out.split(b"\0"):
        path = raw.decode("utf-8", errors="replace")
        if path.startswith(REVIEWS_REL + "/") and path.endswith(".json") and "/" not in path[len(REVIEWS_REL) + 1 :]:
            records.append((path, git(repo, "cat-file", "blob", f"{ref}:{path}")))
    return records


# --- report 1: commits with no guard events ------------------------------------------------------------------------


def commit_events(query: QueryFn, since: int, project: str = "") -> dict[str, dict[str, set[str]]]:
    """tree -> {"allow": {users}, "block": {users}} from the pre-commit events since `since` (Unix time)."""
    sql = (
        "SELECT LogAttributes['tree'] AS tree, Verdict AS verdict, User AS user "
        "FROM guard.guard_events "
        "WHERE Timestamp >= toDateTime({since:UInt32}) AND ServiceName = 'bmad-guard' "
        "AND LogAttributes['stage'] = 'pre-commit' AND LogAttributes['tree'] != '' "
        "AND ({project:String} = '' OR Project = {project:String}) "
        "GROUP BY tree, verdict, user"
    )
    found: dict[str, dict[str, set[str]]] = {}
    for row in query(sql, {"since": str(max(since, 0)), "project": project}):
        tree = str(row.get("tree", ""))
        if TREE_RE.fullmatch(tree):
            found.setdefault(tree, {"allow": set(), "block": set()}).setdefault(str(row.get("verdict")), set()).add(
                str(row.get("user", ""))
            )
    return found


def classify_commits(commits: list[Commit], events: dict[str, dict[str, set[str]]]) -> list[tuple[str, Commit]]:
    """(status, commit) for the commits to report. status: `no-event` or `blocked-only`. A commit is seen when an
    ALLOW event holds its tree. A BLOCK event alone does not count: it proves the hook refused that tree, and a later
    `--no-verify` commit of the same tree would otherwise look clean."""
    flagged = []
    for commit in commits:
        seen = events.get(commit.tree)
        if seen and seen.get("allow"):
            continue
        flagged.append(("blocked-only" if seen and seen.get("block") else "no-event", commit))
    return flagged


def run_commits(repo: Path, rev_range: str, query: QueryFn, project: str, merges: bool, out=sys.stdout) -> int:
    commits = list_commits(repo, rev_range, merges)
    if not commits:
        print(f"commits: no commits in {rev_range}.", file=out)
        return 0
    since = min(c.authored for c in commits) - DAY
    flagged = classify_commits(commits, commit_events(query, since, project))
    age = (datetime.now(timezone.utc).timestamp() - min(c.authored for c in commits)) / DAY
    print(f"commits: {len(commits)} in {rev_range}; {len(flagged)} without a guard event.", file=out)
    for status, c in flagged:
        when = datetime.fromtimestamp(c.authored, timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"  {status:<12} {c.sha[:12]} {when}Z  {c.subject[:60]}", file=out)
    if age > RETENTION_DAYS:
        print(f"note: the oldest commit is {age:.0f} days old; events are kept {RETENTION_DAYS} days.", file=out)
    print(
        "can see: a commit made without the pre-commit hook (--no-verify, setup not run, another git client).\n"
        "cannot see: a --no-verify commit whose tree equals one the hook allowed earlier; a rebased or cherry-picked "
        "commit (its tree is new, so it is a false alarm); a commit made on a machine that sends no events; a hook "
        "that was edited to emit events by itself.",
        file=out,
    )
    return 1 if flagged else 0


# --- report 2: review records with no REVIEW_RECORDED event ----------------------------------------------------------


def review_events(query: QueryFn) -> dict[str, set[str]]:
    """SHA-256 of a record file -> users, from the REVIEW_RECORDED events."""
    sql = (
        "SELECT LogAttributes['target_sha256'] AS sha, User AS user FROM guard.guard_events "
        "WHERE ServiceName = 'bmad-guard' AND LogAttributes['reason_code'] = 'REVIEW_RECORDED' "
        "AND LogAttributes['target_sha256'] != '' GROUP BY sha, user"
    )
    found: dict[str, set[str]] = {}
    for row in query(sql, {}):
        sha = str(row.get("sha", ""))
        if SHA256_RE.fullmatch(sha):
            found.setdefault(sha, set()).add(str(row.get("user", "")))
    return found


def classify_reviews(records: list[tuple[str, bytes]], events: dict[str, set[str]]) -> list[tuple[str, str, str]]:
    """(status, path, detail) for records to report. status: `no-event` (no event holds the file's hash: written by
    hand, or edited after the hook wrote it) or `story-mismatch` (the event exists, but the file's `story` is not its
    name: a valid record copied to another story)."""
    flagged = []
    for path, data in records:
        digest = hashlib.sha256(data).hexdigest()
        if digest not in events:
            flagged.append(("no-event", path, ""))
            continue
        try:
            story = json.loads(data).get("story")
        except (ValueError, AttributeError):
            story = None
        if story != Path(path).stem:
            flagged.append(("story-mismatch", path, ""))
    return flagged


def run_reviews(repo: Path, ref: str, query: QueryFn, out=sys.stdout) -> int:
    records = review_records(repo, ref)
    if not records:
        print(f"reviews: no review records at {ref}.", file=out)
        return 0
    flagged = classify_reviews(records, review_events(query))
    print(f"reviews: {len(records)} record(s) at {ref}; {len(flagged)} without a matching event.", file=out)
    for status, path, _ in flagged:
        print(f"  {status:<14} {path}", file=out)
    print(
        "can see: a record file written by hand or edited after the hook wrote it; a valid record copied to another "
        "story name.\n"
        "cannot see: a record the hook wrote for a review that was not a real review (the agent ran the command "
        "itself in the build session: the hook only knows the session id); a record whose bytes were copied from "
        "another project's event; records older than the 180-day retention.",
        file=out,
    )
    return 1 if flagged else 0


# --- command line --------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None, query: QueryFn | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit reports: guard events (ClickHouse) against a git repository.")
    sub = parser.add_subparsers(dest="report", required=True)
    commits = sub.add_parser("commits", help="commits that no pre-commit hook saw")
    commits.add_argument("--repo", type=Path, default=Path.cwd())
    commits.add_argument(
        "--range",
        default="origin/main..HEAD",
        dest="rev_range",
        help="a git revision range, e.g. origin/main..<feature branch>; run it before any rebase",
    )
    commits.add_argument("--project", default="", help="only events of this project name (as in project.json)")
    commits.add_argument("--include-merges", action="store_true")
    reviews = sub.add_parser("reviews", help="review records with no REVIEW_RECORDED event")
    reviews.add_argument("--repo", type=Path, default=Path.cwd())
    reviews.add_argument("--ref", default="HEAD", help="read the records from this commit (default HEAD)")
    args = parser.parse_args(argv)
    try:
        run_query = query or query_from_env()
        repo = args.repo.resolve()
        if args.report == "commits":
            return run_commits(repo, args.rev_range, run_query, args.project, args.include_merges)
        return run_reviews(repo, args.ref, run_query)
    except ReportError as err:
        print(f"guard_reports: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
