# /// script
# requires-python = ">=3.10"
# ///
"""Shared code for Warden (agent-approval-grant) and Sentry (agent-approval-gate-check).

This file is copied byte for byte into both skills' scripts/ folders, because each skill is
installed as its own folder. tests/test_approval.py fails if the two copies differ.

approval-state.json (shared in git, under _bmad/custom/):
    {"<doc_type>": {"doc_path": "<path relative to the project root>", "approved_hash": "...",
                    "hash_mode": "full" | "build", "approved_by": "...", "git_user": "...",
                    "approved_at": "...", "superseded": false, "history": [<older entries>]}}
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
VERSION_FIELD_RE = re.compile(r"^version\s*:\s*(.*)$", re.MULTILINE)

# bmad-build updates these while it works; they must not make an approved plan look edited.
MUTABLE_FRONTMATTER_FIELDS = {
    "status",
    "approval_status",
    "baseline_commit",
    "build_run_id",
    "build_started_at",
    "plan_created_at",
    "approval_requested_at",
    "coding_started_at",
    "coding_completed_at",
    "verification_started_at",
    "verification_completed_at",
    "review_started_at",
    "review_completed_at",
    "build_completed_at",
    "review_loop_iteration",
}
MUTABLE_SECTIONS = ("Implementation Notes", "Spec Change Log", "Review Triage Log")


# --- frontmatter -------------------------------------------------------------------------------


def get_status(text: str, status_field: str) -> str:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return ""
    field = re.search(rf"^{re.escape(status_field)}\s*:\s*(.*)$", match.group(1), re.MULTILINE)
    return field.group(1).strip().strip("'\"") if field else ""


def set_status(text: str, status_field: str, value: str, create_missing: bool = False) -> str:
    """Set the frontmatter field. Raises ValueError when there is no frontmatter or no field."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("no YAML frontmatter block found at the top of the file")
    frontmatter = match.group(1)
    field_re = re.compile(rf"^({re.escape(status_field)}\s*:\s*).*$", re.MULTILINE)
    if field_re.search(frontmatter):
        new_frontmatter = field_re.sub(lambda m: m.group(1) + value, frontmatter, count=1)
    elif create_missing:
        new_frontmatter = frontmatter.rstrip("\n") + f"\n{status_field}: {value}"
    else:
        raise ValueError(f"no '{status_field}:' field found in frontmatter")
    return text[: match.start(1)] + new_frontmatter + text[match.end(1) :]


def bump_version(text: str) -> tuple[str, int]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("no YAML frontmatter block found at the top of the file")
    frontmatter = match.group(1)
    existing = VERSION_FIELD_RE.search(frontmatter)
    try:
        current = int(existing.group(1).strip()) if existing else 0
    except ValueError:
        current = 0
    new_version = current + 1
    if existing:
        new_frontmatter = VERSION_FIELD_RE.sub(f"version: {new_version}", frontmatter, count=1)
    else:
        new_frontmatter = frontmatter.rstrip("\n") + f"\nversion: {new_version}"
    return text[: match.start(1)] + new_frontmatter + text[match.end(1) :], new_version


# --- hashing -------------------------------------------------------------------------------------


def build_hash(text: str) -> str:
    """Hash only the approval-relevant part of a bmad-build plan (progress fields and logs ignored)."""
    match = FRONTMATTER_RE.match(text)
    canonical = text
    if match:
        kept = [
            line
            for line in match.group(1).splitlines()
            if (line.split(":", 1)[0].strip() if ":" in line else "") not in MUTABLE_FRONTMATTER_FIELDS
        ]
        canonical = text[: match.start(1)] + "\n".join(kept) + text[match.end(1) :]
    for section in MUTABLE_SECTIONS:
        canonical = re.sub(rf"(?ms)^## {re.escape(section)}\s*\n.*?(?=^## |\Z)", f"## {section}\n", canonical)
    canonical = re.sub(r"(?m)^- \[[ xX]\]", "- [ ]", canonical)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def content_hash(text: str, mode: str) -> str:
    if mode == "build":
        return build_hash(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- project and state ---------------------------------------------------------------------------


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace")


def project_root_for(path: Path) -> Path:
    """Git top level of the folder holding `path`; the folder itself when it is not in git."""
    folder = path if path.is_dir() else path.parent
    out = git(["rev-parse", "--show-toplevel"], folder)
    return Path(out.stdout.strip()).resolve() if out.returncode == 0 and out.stdout.strip() else folder.resolve()


def path_key(doc_path: Path, project_root: Path) -> str:
    """Path stored in the shared state: relative to the project root, so it is the same on every machine."""
    resolved = doc_path.resolve()
    try:
        return resolved.relative_to(project_root).as_posix()
    except ValueError:
        return str(resolved)


def same_document(entry: dict, doc_path: Path, project_root: Path) -> bool:
    """True when the entry names this document (relative key, or an old absolute path from this machine)."""
    recorded = entry.get("doc_path")
    return recorded in (path_key(doc_path, project_root), str(doc_path.resolve()))


def load_state(state_file: Path) -> tuple[dict, bool]:
    """Return (state, readable). A missing file is readable and empty; a broken one is not readable."""
    if not state_file.exists():
        return {}, True
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}, False
    return (state, True) if isinstance(state, dict) else ({}, False)


def save_state(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def git_user_name(project_root: Path) -> str:
    """git user.name only. The email is personal data and is not recorded."""
    return git(["config", "user.name"], project_root).stdout.strip()


def project_name(project_root: Path) -> str:
    try:
        data = json.loads((project_root / "_bmad/custom/project.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("project_name", "")) if isinstance(data, dict) else ""


# --- events --------------------------------------------------------------------------------------


def _guard_events():
    """Ledger's sender, from the sibling skill folder. None when Ledger is not installed."""
    path = Path(__file__).resolve().parents[2] / "agent-project-logger" / "scripts" / "guard_events.py"
    if not path.is_file():
        return None
    if "guard_events" in sys.modules:
        return sys.modules["guard_events"]
    spec = importlib.util.spec_from_file_location("guard_events", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["guard_events"] = module
    spec.loader.exec_module(module)
    return module


def emit_event(guard: str, stage: str, verdict: str, reason_code: str, project_root: Path, **fields) -> dict | None:
    """Record one decision event through Ledger's queue. Never raises; warns on stderr if Ledger is missing."""
    try:
        ge = _guard_events()
        if ge is None:
            print(
                "bmad-guard: warning: Ledger (agent-project-logger) not installed; event not recorded", file=sys.stderr
            )
            return None
        event = ge.build_event(guard, stage, verdict, reason_code, project=project_name(project_root), **fields)
        ge.emit(event, ge.Config.for_project(project_root))
        return event
    except Exception as err:  # logging never blocks a guard decision
        print(f"bmad-guard: warning: event not recorded ({type(err).__name__})", file=sys.stderr)
        return None
