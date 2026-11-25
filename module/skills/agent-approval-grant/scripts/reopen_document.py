# /// script
# requires-python = ">=3.10"
# ///
"""Warden: reopen an approved document for revision.

Run only after the user's explicit confirmation (see references/reopen-document.md).

Sets the frontmatter status back to the reopen value and marks the matching approval in the
shared approval-state.json as superseded (with who and when). The entry is kept, never deleted:
it is the audit record of the approval that came before. Sentry blocks a superseded approval
even if someone writes `status: approved` back by hand. Records a guard event (guard "warden").
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Warden: reopen an approved document for revision.")
    p.add_argument("--doc-path", required=True, help="document to reopen")
    p.add_argument("--doc-type", required=True, help="state-file key, e.g. brief, prd, architecture")
    p.add_argument("--state-file", required=True, help="shared approval-state.json")
    p.add_argument("--status-field", default="status", help="frontmatter field (default: %(default)s)")
    p.add_argument("--reopen-value", default="draft", help="value to set (default: %(default)s)")
    p.add_argument("--reopened-by", default="", help="BMAD user_name of the person reopening")
    args = p.parse_args(argv)

    doc_path = Path(args.doc_path)
    if not doc_path.is_file():
        print(f"error: doc_path does not exist: {doc_path}", file=sys.stderr)
        return 2
    state_file = Path(args.state_file)
    state, readable = core.load_state(state_file)
    if not readable:
        print(f"error: {state_file} is not valid JSON; fix it before reopening", file=sys.stderr)
        return 2

    text = doc_path.read_text(encoding="utf-8")
    try:
        new_text = core.set_status(text, args.status_field, args.reopen_value)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    doc_path.write_text(new_text, encoding="utf-8")

    project_root = core.project_root_for(doc_path)
    key = core.path_key(doc_path, project_root)
    entry = state.get(args.doc_type)
    superseded = False
    if isinstance(entry, dict) and core.same_document(entry, doc_path, project_root):
        entry.update(
            superseded=True,
            superseded_at=datetime.now(timezone.utc).isoformat(),
            superseded_by=args.reopened_by,
        )
        core.save_state(state_file, state)
        superseded = True

    core.emit_event(
        GUARD,
        args.doc_type,
        "info",
        "REOPENED",
        project_root,
        agent="agent-approval-grant/REOPEN",
        target_path=key,
        reopened_by=args.reopened_by,
        superseded=str(superseded).lower(),
    )
    print(
        json.dumps(
            {
                "doc_path": key,
                "doc_type": args.doc_type,
                "reopen_value": args.reopen_value,
                "state_file": str(state_file.resolve()),
                "superseded": superseded,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
