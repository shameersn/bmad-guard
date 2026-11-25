# /// script
# requires-python = ">=3.10"
# ///
"""Sentry: check that a document is approved and has not been edited since.

Reads the shared approval-state.json, looks up `doc_type`, and compares the recorded hash with a
fresh hash of the document (full content, or the approval-relevant part for bmad-build plans).
A content hash is used, not a file time, because git does not keep file times across clones.

If the content changed since approval, the document's status is set back to draft as part of
this check, and the result is "stale".

Prints one JSON object:
  {"result": "ok", "approved_hash": ..., "approved_by": ..., "approved_at": ...}
  {"result": "blocked", "reason": "missing"}       the document does not exist
  {"result": "blocked", "reason": "unapproved"}    no approval on record for this document, or status not approved
  {"result": "blocked", "reason": "superseded"}    the approval was reopened; it must be approved again
  {"result": "blocked", "reason": "state_unreadable"}   approval-state.json is broken (fail closed)
  {"result": "blocked", "reason": "stale", "approved_hash": ..., "current_hash": ...}

Every result is also recorded as a guard event (guard "sentry").
Exit code is 0 for every result above; 2 only for bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import approval_core as core  # noqa: E402 - sibling module, found through the line above

GUARD = "sentry"


def check(
    doc_path: Path,
    doc_type: str,
    state_file: Path,
    status_field: str,
    draft_value: str,
    approved_value: str,
    reset_stale: bool = True,
):
    """Return (result dict, project_root). `reset_stale=False` leaves the document alone: the write gate hook
    (tool-guard, Phase 2 T4) only asks, it does not change a plan."""
    project_root = core.project_root_for(doc_path if doc_path.exists() else doc_path.parent)
    if not doc_path.exists():
        return {"result": "blocked", "reason": "missing"}, project_root

    state, readable = core.load_state(state_file)
    if not readable:
        return {"result": "blocked", "reason": "state_unreadable"}, project_root

    entry = state.get(doc_type)
    if not isinstance(entry, dict) or not core.same_document(entry, doc_path, project_root):
        return {"result": "blocked", "reason": "unapproved"}, project_root
    if entry.get("superseded"):
        return {"result": "blocked", "reason": "superseded"}, project_root

    text = doc_path.read_text(encoding="utf-8")
    if core.get_status(text, status_field) != approved_value:
        return {"result": "blocked", "reason": "unapproved"}, project_root

    approved_hash = entry.get("approved_hash")
    current_hash = core.content_hash(text, entry.get("hash_mode", "full"))
    who = {"approved_by": entry.get("approved_by", ""), "approved_at": entry.get("approved_at", "")}
    if approved_hash is None or current_hash != approved_hash:
        try:
            new_text = core.set_status(text, status_field, draft_value)
        except ValueError:
            new_text = text
        if reset_stale and new_text != text:
            doc_path.write_text(new_text, encoding="utf-8")
        return {
            "result": "blocked",
            "reason": "stale",
            "approved_hash": approved_hash,
            "current_hash": current_hash,
            **who,
        }, project_root

    return {
        "result": "ok",
        "approved_hash": approved_hash,
        **who,
        "review_duration_seconds": entry.get("review_duration_seconds"),
    }, project_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sentry: check a document is approved and unchanged.")
    parser.add_argument("--doc-path", required=True, help="document to check")
    parser.add_argument("--doc-type", required=True, help="state-file key, e.g. brief, prd, architecture")
    parser.add_argument("--state-file", required=True, help="shared approval-state.json")
    parser.add_argument("--status-field", default="status", help="frontmatter field (default: %(default)s)")
    parser.add_argument("--draft-value", default="draft", help="value written when stale (default: %(default)s)")
    parser.add_argument("--approved-value", default="approved", help="required status (default: %(default)s)")
    parser.add_argument("--downstream", default="", help="work being gated, e.g. 'PRD generation' (for the event)")
    args = parser.parse_args(argv)

    doc_path = Path(args.doc_path)
    result, project_root = check(
        doc_path, args.doc_type, Path(args.state_file), args.status_field, args.draft_value, args.approved_value
    )
    reason = result.get("reason", "ok")
    core.emit_event(
        GUARD,
        args.doc_type,
        "allow" if result["result"] == "ok" else "block",
        f"GATE_{reason.upper()}",
        project_root,
        agent="agent-approval-gate-check/GATE",
        target_path=core.path_key(doc_path, project_root),
        downstream=args.downstream,
        approved_by=result.get("approved_by", ""),
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
