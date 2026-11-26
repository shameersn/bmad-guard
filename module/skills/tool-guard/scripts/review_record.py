# /// script
# requires-python = ">=3.10"
# ///
"""Review record (Phase 2, T7): the HOOK writes the record, so the model cannot choose the reviewer session.

The review ends with one command, run through the Bash tool:

    python3 "<project>/.claude/skills/tool-guard/scripts/review_record.py" --story <story> --verdict pass|fail

Two parts live in this file:

1. The hook side (`check`, registered in the PreToolUse dispatcher). When a Bash call is exactly that command (one plain
   command, `python3`, this very script or the project's copy of it), the hook writes
   `_bmad-output/guard/reviews/<story>.json` itself:
   {"story", "reviewed_sha", "sessions", "verdict", "reviewed_at"}. The session id is the `session_id` of the hook
   input.
   The reviewed commit is `git rev-parse HEAD` of the project. The hook never reads `CLAUDE_CODE_SESSION_ID`: the model
   can set that variable in its own command. The script below writes nothing, so the model has nothing to feed an id to.
   The record is the only file the hook writes for a review; the agent's own writes to `_bmad-output/guard/` are
   blocked by DOC_GUARD_RECORD (document_guard.py, bash_write_guard.py).
2. The command line side (`main`). It is a receipt: it checks that the hook wrote a fresh record for this story and
   verdict and prints where it is. It writes no file. If the hook did not run (hooks off, a command that is not plain),
   there is no fresh record and it exits 1 with the reason.

Reason codes (they block the recorded call):
  REVIEW_IN_SUBAGENT     the hook input has `agent_id`: the call comes from a subagent. Run the review record from the
                         top-level session (a subagent may share the parent's session id; docs/hooks.md, open check 7).
  REVIEW_NO_SESSION      the hook input has no usable `session_id`.
  REVIEW_NO_COMMIT       the project has no commit to point at (`git rev-parse HEAD` fails).
  REVIEW_RECORD_FAILED   the record could not be written.

A call that mentions this script in any other shape (a pipe, `&&`, a copy of the script, bad arguments) is not
recorded and not blocked: the command line side then prints that there is no record. Known limits are in
references/guard-rules.md ("Review record").

The record holds ids, a commit SHA, a verdict and a time. No content, no path. The event carries the rule id, the
verdict, the session id, the record's path and the SHA-256 of the record file as written. The "review records with no
REVIEW_RECORDED event" report (deploy/collector/reports) compares that hash with the record file in the repository: a
record that was written by hand, or edited afterwards, has no event with its hash (ADR 0003, update of 2026-11-26).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import hook_common as hc  # noqa: E402 - sibling modules, found through the line above
import shell_parse as sp  # noqa: E402
import write_gate as wg  # noqa: E402

GUARD = "review-record"
REVIEWS_REL = "_bmad-output/guard/reviews"
VERDICTS = ("pass", "fail")
STORY_MAX = 300
FRESH_SECONDS = 120  # the receipt accepts a record the hook wrote this recently
SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
PYTHON_RE = re.compile(r"python(3(\.\d+)?)?")
USAGE = "usage: review_record.py --story <story id or file> --verdict pass|fail"
SCRIPT = Path(os.path.realpath(__file__))
ROOT_VARS = ("${CLAUDE_PROJECT_DIR}", "$CLAUDE_PROJECT_DIR")
PROJECT_COPIES = tuple(f"{d}/skills/tool-guard/scripts/review_record.py" for d in (".claude", ".agents"))
AGENT = f"{hc.GUARD}/PreToolUse"


class ArgError(ValueError):
    """The arguments are not `--story <x> --verdict pass|fail`. The message says what is wrong."""


def verdict_of(code: str, reason: str) -> hc.Verdict:
    return hc.Verdict(code, reason, GUARD)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- arguments and names -------------------------------------------------------------------------------


def parse_args(argv: list[str]) -> tuple[str, str]:
    """(story, verdict) from `--story X --verdict V` in either order (`--story=X` works too). Raises ArgError."""
    found: dict[str, str] = {}
    i = 0
    while i < len(argv):
        name, eq, value = argv[i].partition("=")
        if name not in ("--story", "--verdict") or name in found:
            raise ArgError(f"unknown or repeated argument. {USAGE}")
        if not eq:
            i += 1
            if i >= len(argv):
                raise ArgError(f"{name} needs a value. {USAGE}")
            value = argv[i]
        found[name] = value
        i += 1
    story, verdict = found.get("--story", "").strip(), found.get("--verdict", "")
    if not story or len(story) > STORY_MAX or "\0" in story:
        raise ArgError(f"--story needs the story id or the story file. {USAGE}")
    if verdict not in VERDICTS:
        raise ArgError(f"--verdict is pass or fail. {USAGE}")
    return story, verdict


def story_name(value: str) -> str:
    """The record name of a story, the same way the build record names it (write_gate.story_key): a plain id stays as
    it is, a path becomes the file name of the story."""
    path = Path(value)
    return wg.story_key(wg.Plan(path, value, "", {"story": value}), path)


def record_path(root: Path, key: str) -> Path:
    return root / REVIEWS_REL / f"{key}.json"


# --- the hook side -------------------------------------------------------------------------------------


def head_sha(root: Path) -> str | None:
    """The commit the review points at: HEAD of the project, or None when there is none."""
    try:
        out = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=root, capture_output=True, text=True)
    except OSError:
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and SHA_RE.fullmatch(sha) else None


def accepted_scripts(root: Path) -> set[str]:
    """The files a review command may name: this very script, and the project's copy of it (ADR 0008). With
    machine-level hooks this script is the machine copy, but the bmad-code-review override tells the model to run the
    project copy. The copy only prints a receipt (the hook writes the record), so accepting it adds no way to forge
    one. A copy that is not a file in this project (another folder, another project) is not accepted."""
    found = {str(SCRIPT)}
    for rel in PROJECT_COPIES:
        copy = root / rel
        if copy.is_file():
            found.add(os.path.realpath(copy))
    return found


def _script_arg(word: sp.Word, root: Path, cwd: str) -> bool:
    """True when the word is a path to an accepted script (a literal path, or one that starts with
    CLAUDE_PROJECT_DIR)."""
    text = word.text
    for var in ROOT_VARS:
        if text.startswith(var + "/"):
            text = str(root) + text[len(var) :]
            break
    else:
        if word.dynamic:
            return False
    return os.path.realpath(os.path.join(cwd, text)) in accepted_scripts(root)


def record_call(command: str, root: Path, cwd: str) -> list[str] | None:
    """The arguments of the call when `command` is exactly one plain `python3 <this script> ...` command, else None."""
    try:
        invocations = list(sp.walk(command))
    except sp.ParseError:
        return None  # command_guard reports a command it cannot read
    if len(invocations) != 1:
        return None
    inv = invocations[0]
    if not PYTHON_RE.fullmatch(inv.name) or not inv.args or inv.dynamic_tail or inv.redirects or inv.stdin_texts:
        return None
    if not _script_arg(inv.args[0], root, cwd):
        return None
    rest = inv.args[1:]
    return None if any(w.dynamic for w in rest) else [w.text for w in rest]


def write_record(root: Path, key: str, sha: str, session: str, verdict: str) -> str:
    """Write `_bmad-output/guard/reviews/<story>.json`, atomically. A later review of the story replaces the file.
    Returns the SHA-256 of the bytes written."""
    path = record_path(root, key)
    record = {"story": key, "reviewed_sha": sha, "sessions": [session], "verdict": verdict, "reviewed_at": now()}
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, indent=2) + "\n").encode("utf-8")
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{key}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(name, path)
        return hashlib.sha256(data).hexdigest()
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def check(payload: dict, root: Path) -> list[hc.Verdict]:
    cwd = payload.get("cwd")
    start = cwd if isinstance(cwd, str) and cwd and Path(cwd).is_absolute() else str(root)
    command = payload["tool_input"].get("command")
    real_root = Path(os.path.realpath(root))
    argv = record_call(command, real_root, start) if isinstance(command, str) else None
    if argv is None:
        return []
    try:
        story, verdict = parse_args(argv)
    except ArgError:
        return []  # the command line side reports it
    if payload.get("agent_id"):
        return [
            verdict_of(
                "REVIEW_IN_SUBAGENT",
                "The review record is written from the top-level session, not from a subagent (a subagent may share "
                "the build session's id). Run the review in a fresh session and record it there.",
            )
        ]
    session = payload.get("session_id")
    if not (isinstance(session, str) and wg.SESSION_RE.fullmatch(session)):
        return [
            verdict_of(
                "REVIEW_NO_SESSION",
                "The hook input has no usable session id, so the review cannot be recorded. "
                "Ask the developer to check the hook setup.",
            )
        ]
    sha = head_sha(real_root)
    if sha is None:
        return [
            verdict_of("REVIEW_NO_COMMIT", "The project has no commit yet, so there is nothing to record a review for.")
        ]
    key = story_name(story)
    try:
        digest = write_record(real_root, key, sha, session, verdict)
    except OSError as err:
        return [
            verdict_of(
                "REVIEW_RECORD_FAILED",
                f"The review record in {REVIEWS_REL} could not be written ({type(err).__name__}). "
                "Ask the developer to fix the folder.",
            )
        ]
    hc.emit(
        "tool",
        "allow",
        "REVIEW_RECORDED",
        root,
        AGENT,
        rule=GUARD,
        detail=verdict,
        tool="Bash",
        session_id=session,
        target_path=f"{REVIEWS_REL}/{key}.json",
        target_sha256=digest,
    )
    return []


GUARDS = [hc.Guard(GUARD, ("Bash",), check)]


# --- the command line side -----------------------------------------------------------------------------


def fresh_record(root: Path, key: str, verdict: str) -> dict | None:
    """The record when the hook wrote it just now for this story and verdict and HEAD has not moved, else None."""
    try:
        data = json.loads(record_path(root, key).read_text(encoding="utf-8"))
        written = datetime.strptime(data["reviewed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    age = (datetime.now(timezone.utc) - written).total_seconds()
    ok = (
        isinstance(data, dict)
        and data.get("story") == key
        and data.get("verdict") == verdict
        and data.get("reviewed_sha") == head_sha(root)
        and isinstance(data.get("sessions"), list)
        and -5 <= age <= FRESH_SECONDS
    )
    return data if ok else None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args in (["-h"], ["--help"]):
        print(f"{USAGE}\nThe tool-guard hook writes the review record. This command only checks that it did.")
        return 0
    try:
        story, verdict = parse_args(args)
    except ArgError as err:
        print(f"review_record: {err}", file=sys.stderr)
        return 2
    root = hc.project_root()
    key = story_name(story)
    if fresh_record(root, key, verdict) is None:
        print(
            "review_record: no record was written. The tool-guard hook writes it, and only when this is the whole "
            "Bash command (one plain `python3 <script> --story ... --verdict ...`, no pipe or `&&`) and the hooks "
            "are on. Run the command again on its own. If it still fails, ask the developer to run "
            "scripts/setup_guard.py --check.",
            file=sys.stderr,
        )
        return 1
    rel = f"{REVIEWS_REL}/{key}.json"
    print(
        f"Review record written for story {key}: verdict {verdict}, commit {head_sha(root)[:12]}. "
        f"Commit {rel} (it is not source code, so the commit does not need a plan)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
