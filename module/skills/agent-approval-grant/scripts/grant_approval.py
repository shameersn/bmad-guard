# /// script
# requires-python = ">=3.10"
# ///
"""Warden: mark a document approved and record the approval.

Run only after the user gave both explicit confirmations (see references/grant-approval.md).

Sets the frontmatter status to the approved value, bumps `version`, saves, hashes the saved
content (full, or the approval-relevant part for bmad-build plans) and records the approval
under `doc_type` in the shared approval-state.json:
- the document path is stored relative to the project root, so the record works on every machine;
- an earlier approval of the same doc_type is kept in the entry's `history`, never thrown away;
- `approved_by` is the BMAD user; `git_user` is git's user.name (no email).

Then commits the document and the state file together (unless --no-commit). A failed commit is
reported in the output; the approval itself stands. Records a guard event (guard "warden").
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import approval_core as core  # noqa: E402 - sibling module, found through the line above

GUARD = "warden"
HISTORY_LIMIT = 20


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def commit(paths: list[Path], project_root: Path, message: str) -> dict:
    rel = [core.path_key(p, project_root) for p in paths]
    added = core.git(["add", "--", *rel], project_root)
    if added.returncode != 0:
        return {"ok": False, "message": message, "error": added.stderr.strip()}
    done = core.git(["commit", "-m", message, "--", *rel], project_root)
    if done.returncode != 0:
        return {"ok": False, "message": message, "error": (done.stderr or done.stdout).strip()}
    return {"ok": True, "message": message}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Warden: mark a document approved and record it.")
    p.add_argument("--doc-path", required=True, help="document to approve")
    p.add_argument("--doc-type", required=True, help="state-file key, e.g. brief, prd, architecture")
    p.add_argument("--state-file", required=True, help="shared approval-state.json")
    p.add_argument("--status-field", default="status", help="frontmatter field (default: %(default)s)")
    p.add_argument("--approved-value", default="approved", help="value to set (default: %(default)s)")
    p.add_argument("--create-status-field", action="store_true", help="add the status field when missing")
    p.add_argument("--approved-by", default="", help="BMAD user_name of the approver")
    p.add_argument("--requested-at", default="", help="UTC ISO-8601 time approval was requested")
    p.add_argument("--approved-at", default="", help="UTC ISO-8601 approval time (default: now)")
    p.add_argument("--no-commit", action="store_true", help="record the approval without a git commit")
    p.add_argument("--hash-mode", choices=("full", "build"), default="full", help="full document or bmad-build plan")
    args = p.parse_args(argv)

    approved_at = args.approved_at or datetime.now(timezone.utc).isoformat()
    review_duration_seconds = None
    try:
        approved_time = parse_time(approved_at)
        if args.requested_at:
            review_duration_seconds = max(0, int((approved_time - parse_time(args.requested_at)).total_seconds()))
    except ValueError:
        print("error: requested-at and approved-at must be ISO-8601 timestamps", file=sys.stderr)
        return 2

    doc_path = Path(args.doc_path)
    if not doc_path.is_file():
        print(f"error: doc_path does not exist: {doc_path}", file=sys.stderr)
        return 2
    state_file = Path(args.state_file)
    state, readable = core.load_state(state_file)
    if not readable:
        print(f"error: {state_file} is not valid JSON; fix it before approving", file=sys.stderr)
        return 2

    text = doc_path.read_text(encoding="utf-8")
    try:
        new_text = core.set_status(text, args.status_field, args.approved_value, args.create_status_field)
        new_text, new_version = core.bump_version(new_text)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    doc_path.write_text(new_text, encoding="utf-8")
    approved_hash = core.content_hash(doc_path.read_text(encoding="utf-8"), args.hash_mode)

    project_root = core.project_root_for(doc_path)
    key = core.path_key(doc_path, project_root)
    git_user = core.git_user_name(project_root)
    previous = state.get(args.doc_type)
    history = []
    if isinstance(previous, dict):
        history = list(previous.pop("history", []))
        history.append(previous)
    state[args.doc_type] = {
        "doc_path": key,
        "approved_hash": approved_hash,
        "hash_mode": args.hash_mode,
        "version": new_version,
        "approved_by": args.approved_by,
        "git_user": git_user,
        "requested_at": args.requested_at,
        "approved_at": approved_at,
        "review_duration_seconds": review_duration_seconds,
        "superseded": False,
        "history": history[-HISTORY_LIMIT:],
    }
    core.save_state(state_file, state)

    if args.no_commit:
        git_commit = {"ok": True, "message": "commit skipped by caller", "skipped": True}
    else:
        git_commit = commit([doc_path, state_file], project_root, f"Approve {args.doc_type} v{new_version}")

    core.emit_event(
        GUARD,
        args.doc_type,
        "allow",
        "APPROVED",
        project_root,
        agent="agent-approval-grant/GRANT",
        target_path=key,
        target_sha256=approved_hash,
        approved_by=args.approved_by,
        git_user=git_user,
        version=new_version,
        hash_mode=args.hash_mode,
    )
    print(
        json.dumps(
            {
                "doc_path": key,
                "doc_type": args.doc_type,
                "approved_hash": approved_hash,
                "state_file": str(state_file.resolve()),
                "version": new_version,
                "approved_by": args.approved_by,
                "git_user": git_user,
                "requested_at": args.requested_at,
                "approved_at": approved_at,
                "review_duration_seconds": review_duration_seconds,
                "hash_mode": args.hash_mode,
                "git_commit": git_commit,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
