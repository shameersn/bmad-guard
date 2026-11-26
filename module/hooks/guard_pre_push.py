# /// script
# requires-python = ">=3.10"
# ///
"""The pre-push check (Phase 2, T7). `pre-push` is a thin launcher; this module holds the logic.

A push that sends source commits needs an independent review on record. The check reads git's pre-push input (one line
per ref: local ref, local sha, remote ref, remote sha) and, for each ref, works on the pushed commits only: the commits
that no ref of this remote has yet (`git rev-list <local> --not <remote sha> --remotes=<remote>`).

A **source commit** is a pushed commit that changes a file outside the write gate's allowed paths (`_bmad/`,
`_bmad-output/`, `docs/`, `.claude/`, `.agents/`, `.bmad-guard/` and the project's `write_gate_allowed_paths`; the
definition is write_gate.py's). A merge commit has no changes of its own and is not one. A push with no source commit,
a delete and an object that is not a commit are allowed.

For the source commits, the review records and the build records are read from the PUSHED tree (`git show <sha>:path`),
never from the work tree: what is not committed does not count.
  `_bmad-output/guard/reviews/<story>.json`  written by the tool-guard hook (review_record.py)
  `_bmad-output/guard/builds/<story>.json`   written by the tool-guard hook (the write gate)

A review record is **valid** when all of these hold:
  - its verdict is `pass`;
  - none of its sessions is a build session: the sessions of ALL build records in the pushed tree are compared, not only
    the record's own story, because the story name in a review record is typed by the model;
  - its `reviewed_sha` is an ancestor of the pushed commit (or the pushed commit itself), so it is not stale.
A source commit is **covered** when some valid record's `reviewed_sha` is that commit or a descendant of it. A source
commit that no valid record covers (it came after the reviewed commit, or no record exists) blocks the push. Commits
that do not change source (the commit of the record itself, documents) never need a record.

Rule ids, all blocks. One message lists every reason:
  PUSH_NO_RECORD            source commits, and no review record was committed in the push (and none covers them).
  PUSH_RECORD_UNREADABLE    a review record committed in the push is not in the expected format.
  PUSH_VERDICT_NOT_PASS     a review record committed in the push has a verdict other than `pass`.
  PUSH_SAME_SESSION         a review record committed in the push names a build session as its reviewer.
  PUSH_RECORD_STALE         a review record committed in the push points at a commit that is not in the pushed history.
  PUSH_COMMITS_AFTER_REVIEW source commits that no valid record covers, although a valid record exists.
  PUSH_BUILD_UNREADABLE     a build record in the pushed tree is not in the expected format (so no comparison is made).
  PUSH_ERROR                the hook could not run. The push is blocked.
Event only: PUSH_ALLOWED, PUSH_NOTHING_TO_CHECK.

Only records committed IN the push get a reason of their own: an old record for another story, left in the tree, is not
the push's business. A valid old record still counts as coverage.

Events go to the local queue (guard_hook_lib.emit); at the end the hook sends the queue once, best effort
(guard_hook_lib.flush: no endpoint, no send; a failure never changes the answer). They hold the rule id, the commit
counts and a detail (rule ids or an error type). No path, no ref name, no session id, no content.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import guard_hook_lib as lib  # noqa: E402 - sibling module, found through the line above

AGENT = "git-hooks/pre-push"
STAGE = "pre-push"
REVIEWS_REL = "_bmad-output/guard/reviews"
BUILDS_REL = "_bmad-output/guard/builds"
SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
LISTED = 5  # commits named in one message line


@dataclass
class Reason:
    code: str
    text: str = ""
    detail: str = ""


@dataclass
class Update:
    """One line of git's pre-push input."""

    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str

    @property
    def delete(self) -> bool:
        return set(self.local_sha) == {"0"}

    @property
    def new_ref(self) -> bool:
        return set(self.remote_sha) == {"0"}


@dataclass
class Record:
    story: str
    sha: str
    sessions: list[str]
    verdict: str


@dataclass
class Result:
    reasons: list[Reason] = field(default_factory=list)
    commits: int = 0  # pushed commits
    source: int = 0  # pushed commits that change source


def parse_updates(text: str) -> list[Update]:
    updates = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 4:
            raise lib.HookError("git gave a pre-push line that is not 'local-ref local-sha remote-ref remote-sha'")
        updates.append(Update(*parts))
    return updates


# --- the pushed commits ------------------------------------------------------------------------------------------


def commit_of(root: Path, sha: str) -> str | None:
    """The commit a pushed object stands for (an annotated tag is followed), or None when it is not a commit."""
    if not lib.git_ok(root, "cat-file", "-e", f"{sha}^{{commit}}"):
        return None
    return lib.git(root, "rev-parse", f"{sha}^{{commit}}").decode().strip()


def pushed_commits(root: Path, head: str, update: Update, remote: str) -> list[str]:
    """The commits of `head` that the remote does not have: not the remote's old tip, and not on any remote-tracking ref
    of this remote (or of any remote, when `remote` is not the name of one)."""
    args = ["rev-list", head, "--not"]
    if not update.new_ref and lib.git_ok(root, "cat-file", "-e", f"{update.remote_sha}^{{commit}}"):
        args.append(update.remote_sha)
    named = bool(remote) and lib.git_ok(root, "config", "--get", f"remote.{remote}.url")
    args.append(f"--remotes={remote}" if named else "--remotes")
    return lib.git(root, *args).decode().split()


def changed_files(root: Path, commit: str) -> list[str]:
    """The paths a commit changes (a merge commit has none of its own). Renames count as delete plus add."""
    out = lib.git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", "--no-renames", commit)
    return [p.decode("utf-8", errors="surrogateescape") for p in out.split(b"\0") if p]


def short(shas: list[str]) -> str:
    shown = ", ".join(s[:10] for s in shas[:LISTED])
    return shown + (f" (+{len(shas) - LISTED} more)" if len(shas) > LISTED else "")


# --- the records in the pushed tree ------------------------------------------------------------------------------


def tree_files(root: Path, head: str, folder: str) -> list[str]:
    out = lib.git(root, "ls-tree", "-r", "--name-only", "-z", head, "--", f"{folder}/")
    return [p.decode("utf-8", errors="surrogateescape") for p in out.split(b"\0") if p.endswith(b".json")]


def read_json(root: Path, head: str, rel: str):
    """The JSON of a file in the pushed tree, or None when it does not parse."""
    try:
        return json.loads(lib.git(root, "cat-file", "blob", f"{head}:{rel}").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def parse_record(data) -> Record | None:
    if not isinstance(data, dict):
        return None
    story, sha, sessions, verdict = (data.get(k) for k in ("story", "reviewed_sha", "sessions", "verdict"))
    if not (isinstance(story, str) and isinstance(sha, str) and SHA_RE.fullmatch(sha) and isinstance(verdict, str)):
        return None
    if not (isinstance(sessions, list) and sessions and all(isinstance(s, str) and s for s in sessions)):
        return None
    return Record(story, sha, sessions, verdict)


def build_sessions(root: Path, head: str) -> tuple[set[str], list[str]]:
    """(every session id in every build record of the pushed tree, the build records that cannot be read)."""
    sessions: set[str] = set()
    bad: list[str] = []
    for rel in tree_files(root, head, BUILDS_REL):
        data = read_json(root, head, rel)
        found = data.get("sessions") if isinstance(data, dict) else None
        if isinstance(found, list) and all(isinstance(s, str) for s in found):
            sessions.update(found)
        else:
            bad.append(Path(rel).stem)
    return sessions, bad


# --- the check for one ref ---------------------------------------------------------------------------------------


def check_update(root: Path, update: Update, remote: str, is_source) -> Result:
    result = Result()
    if update.delete:
        return result
    head = commit_of(root, update.local_sha)
    if head is None:
        return result  # a tag or ref that points at something that is not a commit
    commits = pushed_commits(root, head, update, remote)
    result.commits = len(commits)
    changes = {c: changed_files(root, c) for c in commits}
    source = [c for c in commits if any(is_source(f) for f in changes[c])]
    result.source = len(source)
    if not source:
        return result
    touched = {
        f for files in changes.values() for f in files if f.startswith(REVIEWS_REL + "/") and f.endswith(".json")
    }
    builds, bad_builds = build_sessions(root, head)
    valid: list[Record] = []
    reasons: list[Reason] = []
    for rel in tree_files(root, head, REVIEWS_REL):
        record = parse_record(read_json(root, head, rel))
        name = Path(rel).stem
        if record is None:
            if rel in touched:
                reasons.append(
                    Reason(
                        "PUSH_RECORD_UNREADABLE",
                        f"The review record for {name} is not in the expected format. Do not edit it: run the review "
                        "again in a fresh session.",
                    )
                )
            continue
        problems: list[Reason] = []
        if record.verdict != "pass":
            problems.append(
                Reason(
                    "PUSH_VERDICT_NOT_PASS",
                    f"The review of story {name} did not pass (verdict: {record.verdict[:20]}). Fix the findings, "
                    "commit, and review again in a fresh session.",
                )
            )
        if builds & set(record.sessions):
            problems.append(
                Reason(
                    "PUSH_SAME_SESSION",
                    f"The review of story {name} was recorded in a session that also wrote code (it is in "
                    f"{BUILDS_REL}). Review again in a fresh, top-level session.",
                )
            )
        if not lib.git_ok(root, "merge-base", "--is-ancestor", record.sha, head):
            problems.append(
                Reason(
                    "PUSH_RECORD_STALE",
                    f"The review of story {name} is for commit {record.sha[:10]}, which is not in the history being "
                    "pushed (the branch was rebased or amended, or the review is from another branch). "
                    "Review again.",
                )
            )
        if problems:
            reasons += problems if rel in touched else []
        else:
            valid.append(record)
    if valid:
        after = set(lib.git(root, "rev-list", head, "--not", *[r.sha for r in valid]).decode().split())
    else:
        after = set(commits)
    uncovered = [c for c in source if c in after]
    if uncovered and valid:
        reasons.append(
            Reason(
                "PUSH_COMMITS_AFTER_REVIEW",
                f"{len(uncovered)} commit(s) that change source come after the reviewed commit: {short(uncovered)}. "
                "Review again so the record covers them, and commit the new record.",
            )
        )
    elif uncovered and not reasons:
        reasons.append(
            Reason(
                "PUSH_NO_RECORD",
                f"{len(uncovered)} commit(s) change source ({short(uncovered)}), and the push has no review record. "
                "Run bmad-code-review in a fresh session, commit _bmad-output/guard/reviews/<story>.json on its own, "
                "then push again.",
            )
        )
    elif not uncovered:
        reasons = []  # every source commit is covered; a record with a problem does not matter
    # A build record that cannot be read means the reviewer cannot be compared with the builders: always a block.
    reasons += [
        Reason(
            "PUSH_BUILD_UNREADABLE",
            f"The build record for {name} is not in the expected format, so the reviewer cannot be compared with the "
            "builders. Ask the developer to restore it from git.",
        )
        for name in bad_builds
    ]
    result.reasons = reasons
    return result


# --- run ---------------------------------------------------------------------------------------------------------


def render(reasons: list[tuple[str, Reason]]) -> str:
    lines = [f"bmad-guard pre-push: the push is blocked ({len(reasons)} reason{'s' if len(reasons) > 1 else ''})."]
    lines += [f"- {r.code} ({ref}): {r.text}" for ref, r in reasons]
    lines.append("Fix this and push again. Do not use --no-verify.")
    return "\n".join(lines)


def record(root: Path, reasons: list[Reason], commits: int, source: int, nothing: bool) -> None:
    for r in reasons:
        lib.emit(
            root,
            STAGE,
            "block",
            r.code,
            AGENT,
            rule="review",
            detail=r.detail,
            commit_count=commits,
            source_count=source,
        )
    if not reasons:
        code = "PUSH_NOTHING_TO_CHECK" if nothing else "PUSH_ALLOWED"
        lib.emit(root, STAGE, "allow", code, AGENT, commit_count=commits, source_count=source)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    remote = args[0] if args else ""
    try:
        root = lib.project_root()
    except lib.HookError as err:
        print(f"bmad-guard pre-push: the push is blocked: {err}", file=sys.stderr)
        return 1
    blocked: list[tuple[str, Reason]] = []
    commits = source = 0
    try:
        updates = parse_updates(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
        if updates:
            write_gate = lib.load_skill_script(root, "tool-guard", "write_gate.py")
            is_source = write_gate.source_checker(root)
            for update in updates:
                result = check_update(root, update, remote, is_source)
                commits, source = commits + result.commits, source + result.source
                blocked += [(update.remote_ref, r) for r in result.reasons]
    except Exception as err:  # any failure blocks: a hook that cannot check must not wave a push through
        cause = str(err) if isinstance(err, lib.HookError) else type(err).__name__
        blocked = [
            (
                "-",
                Reason(
                    "PUSH_ERROR",
                    f"the check could not run ({cause}), so the push is blocked. Fix the cause (for example run "
                    "scripts/setup_guard.py again), then push again.",
                    type(err).__name__,
                ),
            )
        ]
    record(root, [r for _, r in blocked], commits, source, nothing=source == 0)
    lib.flush(root)  # best effort, after the push event is queued; never changes the answer
    if blocked:
        print(render(blocked), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
