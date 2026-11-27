# /// script
# requires-python = ">=3.10"
# ///
"""Ledger: record one project action as a guard event (ADR 0003).

Replaces the old per-project CSV. The event goes to the local queue
(`.bmad-guard/events/queue.jsonl`) and on to the collector when one is configured.

Command-line flags stay compatible with the old CSV logger, so existing callers keep working:
  --pattern and --output-dir are accepted and ignored;
  --event becomes the event's reason_code; --result sets the verdict when --verdict is not given.

No document content goes into an event: the action text and the confidence rationale are cut
to one short line, and a document is recorded only as its path and SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import guard_events  # noqa: E402 - sibling module, found through the line above

GUARD = "ledger"
ACTION_MAX = 200
RATIONALE_MAX = 300
RESULT_TO_VERDICT = {"blocked": "block", "failed": "warn"}


def one_line(text: str, limit: int) -> str:
    """Collapse whitespace to one line and cut to `limit` characters."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def project_root_from(cwd: Path) -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace"
    )
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else cwd


def git_context(project_root: Path) -> dict[str, str]:
    """repo (folder name only, never the remote URL), branch and commit. Empty when not a git repo."""

    def run(*args: str) -> str:
        out = subprocess.run(["git", *args], cwd=project_root, capture_output=True, encoding="utf-8", errors="replace")
        return out.stdout.strip() if out.returncode == 0 else ""

    top = run("rev-parse", "--show-toplevel")
    return {
        "repo": Path(top).name if top else "",
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "commit": run("rev-parse", "--short=12", "HEAD"),
    }


def target_fields(doc_path: str, project_root: Path) -> dict[str, str]:
    """Path relative to the project and its SHA-256. Files outside the project are not read."""
    if not doc_path:
        return {}
    path = Path(doc_path)
    path = path if path.is_absolute() else project_root / path
    try:
        rel = path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return {"target_path": doc_path}
    fields = {"target_path": rel.as_posix()}
    if path.is_file():
        fields["target_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return fields


def validate_duration(value: str) -> str:
    if value == "":
        return ""
    try:
        duration = float(value)
    except ValueError as exc:
        raise ValueError("duration-seconds must be numeric") from exc
    if duration < 0:
        raise ValueError("duration-seconds must be non-negative")
    return value


def validate_confidence(value: str) -> str:
    if value == "":
        return ""
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError("confidence must be a number from 0 to 100") from exc
    if not 0 <= number <= 100:
        raise ValueError("confidence must be a number from 0 to 100")
    return value


def build_action_event(args: argparse.Namespace, project_root: Path) -> dict:
    verdict = args.verdict or RESULT_TO_VERDICT.get(args.result.strip().lower(), "info")
    fields = {
        "action": one_line(args.action, ACTION_MAX),
        "project": args.project_name,
        "bmad_user": args.user,
        "agent": args.agent,
        "doc_status": args.doc_status,
        "confidence": validate_confidence(args.confidence),
        "confidence_label": args.confidence_label,
        "confidence_rationale": one_line(args.confidence_rationale, RATIONALE_MAX),
        "run_id": args.run_id,
        "duration_seconds": validate_duration(args.duration_seconds),
        "result": args.result,
        "details": one_line(args.details, ACTION_MAX),
        "session_id": args.session_id,
        **git_context(project_root),
        **target_fields(args.doc_path, project_root),
    }
    return guard_events.build_event(GUARD, args.stage, verdict, args.event or "ACTION", **fields)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Record one project action as a guard event.")
    p.add_argument("--project-name", required=True, help="project name (from _bmad/custom/project.json)")
    p.add_argument("--action", required=True, help="short description of what happened (one line)")
    p.add_argument("--user", required=True, help="BMAD user_name; the collector adds the verified user")
    p.add_argument("--stage", default="", help="lifecycle stage, e.g. brief, prd, architecture, stories")
    p.add_argument("--agent", default="", help="calling skill or persona, e.g. agent-approval-grant/GRANT")
    p.add_argument("--doc-status", default="", help="document frontmatter status, e.g. draft, approved")
    p.add_argument("--doc-path", default="", help="document the action concerns (path; only its hash is stored)")
    p.add_argument("--confidence", default="", help="confidence score 0-100, for confidence-scoring actions")
    p.add_argument("--confidence-label", default="", help="Low, Medium or High")
    p.add_argument("--confidence-rationale", default="", help="1-2 sentences; cut to one short line")
    p.add_argument("--verdict", default="", choices=["", *sorted(guard_events.SEVERITY)], help="default: info")
    p.add_argument("--event", default="", help="stable event code; becomes reason_code (default ACTION)")
    p.add_argument("--run-id", default="", help="id that links the events of one workflow run")
    p.add_argument("--duration-seconds", default="", help="non-negative seconds for a finished phase")
    p.add_argument("--result", default="", help="started, passed, blocked, completed or failed")
    p.add_argument("--details", default="", help="short, non-sensitive context; cut to one line")
    p.add_argument("--session-id", default="", help="Claude Code session id, when known")
    p.add_argument("--project-root", type=Path, default=None, help="default: git top level of the current folder")
    p.add_argument("--pattern", default="", help=argparse.SUPPRESS)  # old CSV flag, ignored
    p.add_argument("--output-dir", default="", help=argparse.SUPPRESS)  # old CSV flag, ignored
    p.add_argument("-o", "--output", help="write the result JSON here instead of stdout")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = (args.project_root or project_root_from(Path.cwd())).resolve()
    try:
        event = build_action_event(args, project_root)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    status = guard_events.emit(event, guard_events.Config.for_project(project_root))
    result = {"queue": str(guard_events.QUEUE_REL), "status": status, "event": event}
    text = json.dumps(result)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0  # logging never blocks the caller


if __name__ == "__main__":
    sys.exit(main())
