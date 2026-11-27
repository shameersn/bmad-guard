# /// script
# requires-python = ">=3.10"
# ///
"""Ready-for-dev gate (ADR 0004): a story moves to development only on counted facts.

Blocks unless the story has zero open questions, zero open assumptions, confidence at or above the
minimum, a who-and-when record on every resolution, no deleted question or assumption ids, and an
epic summary with a functional and a technical part. When the story names its PRD feature (frontmatter
`feature: F-<nnn>`), the feature's FRD (`<frds-dir>/F-<nnn>/frd.md`, written by experion-frd) is the
functional part: it must exist, be approved and have no open question or assumption, or every story of
that feature is blocked. Markup: docs/ready-for-dev-markup.md.

Usage:
  python3 ready_for_dev.py <story> [--epic-summary FILE] [--epics-dir DIR] [--frds-dir DIR]
                           [--min-confidence 80] [--baseline GIT_REF] [--no-event]

Prints one JSON object:
  {"result": "allow" | "block", "reasons": [{"code": ..., "detail": ...}], "counts": {...}}

Exit code 0 for allow and block; 2 for bad arguments, an unreadable story or an unusable baseline
(the JSON then says "block" too, so a caller that only reads the JSON still fails closed).
Every run records one guard event (guard "ready-for-dev") unless --no-event is given. The event
holds the path, the SHA-256, the counts and the reason codes, never document text.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

GUARD = "ready-for-dev"
STAGE = "ready-for-dev"
AGENT = "ready-for-dev-gate/READY"

FRONTMATTER_OPEN_RE = re.compile(r"\A---\r?\n")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n?---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
OPEN_QUESTIONS_RE = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)?open questions$", re.IGNORECASE)  # "12. Open Questions" too
ITEM_RE = re.compile(r"^ ?[-*+]\s+(.*)$")  # top-level only; indented items belong to the item above
QUESTION_ID_RE = re.compile(r"^(?:\*\*)?(Q-(\d+))\b")
RESOLVED_RE = re.compile(r"^\s*(?:[-*+]\s+)?(?:\*\*)?resolved:(?:\*\*)?\s*(.*)$", re.IGNORECASE)
NONE_ITEM_RE = re.compile(r"^(none|n/a)\.?$", re.IGNORECASE)
ASSUMPTION_RE = re.compile(r"\[ASSUMPTION(?P<resolved>-RESOLVED)?(?:\s+(?P<id>A-(?P<num>\d+)))?\s*:(?P<body>[^\]]*)\]")
ANY_ASSUMPTION_RE = re.compile(r"\[ASSUMPTION")
WHO_WHEN_RE = re.compile(r"(?:—|--)\s*(?P<who>[^,—]+?)\s*,\s*(?P<when>\d{4}-\d{2}-\d{2})\s*$")
EPIC_PARTS = ("functional", "technical")
FRD_NAME = "frd.md"  # the feature's FRD, in <frds-dir>/F-<nnn>/
FEATURE_RE = re.compile(r"^F-\d{1,4}$")  # also keeps the path inside frds-dir
APPROVED = "approved"


class GateError(Exception):
    """A problem that stops the gate from running at all (exit 2)."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


# --- parsing ---------------------------------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict[str, str], str, str | None]:
    """Return (fields, body, error). Only top-level `key: value` lines are read; nested lines are skipped."""
    if not FRONTMATTER_OPEN_RE.match(text):
        return {}, text, None
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text, "frontmatter starts with '---' but has no closing '---' line"
    fields: dict[str, str] = {}
    for number, line in enumerate(match.group(1).splitlines(), start=2):
        if not line.strip() or line.lstrip().startswith("#") or line[:1] in (" ", "\t", "-"):
            continue  # blank, comment, or part of a nested value
        key_match = KEY_RE.match(line)
        if not key_match:
            return {}, text[match.end() :], f"frontmatter line {number} is not 'key: value'"
        key = key_match.group(1).lower()
        if key in fields:
            return {}, text[match.end() :], f"frontmatter key '{key}' appears twice"
        fields[key] = key_match.group(2).strip().strip("'\"")
    return fields, text[match.end() :], None


def has_who_and_when(text: str) -> bool:
    match = WHO_WHEN_RE.search(text.strip())
    if not match or not match.group("who").strip():
        return False
    try:
        dt.date.fromisoformat(match.group("when"))
    except ValueError:
        return False
    return True


def open_question_sections(lines: list[str]) -> list[list[str]]:
    """Lines under every `## Open questions` heading, up to the next heading of the same or a higher level."""
    sections, current, level = [], None, 0
    for line in lines:
        heading = HEADING_RE.match(line)
        if heading:
            depth = len(heading.group(1))
            if current is not None and depth <= level:
                sections.append(current)
                current = None
            if current is None and OPEN_QUESTIONS_RE.match(heading.group(2).strip()):
                current, level = [], depth
                continue
        if current is not None:
            current.append(line)
    if current is not None:
        sections.append(current)
    return sections


def parse_questions(body: str) -> list[dict]:
    """Each top-level list item under `## Open questions`: id (or None), resolved, who/when recorded."""
    items: list[dict] = []
    for section in open_question_sections(body.splitlines()):
        current = None
        for line in section:
            item = ITEM_RE.match(line)
            if item:
                text = item.group(1).strip()
                if NONE_ITEM_RE.match(text):
                    current = None
                    continue
                qid = QUESTION_ID_RE.match(text)
                current = {
                    "id": qid.group(1) if qid else None,
                    "num": int(qid.group(2)) if qid else None,
                    "resolved": False,
                    "recorded": False,
                }
                items.append(current)
                continue
            if current is None:
                continue
            resolved = RESOLVED_RE.match(line)
            if resolved:
                current["resolved"] = True
                current["recorded"] = has_who_and_when(resolved.group(1))
    return items


def parse_assumptions(body: str) -> tuple[list[dict], int]:
    """Return (tagged assumptions, count of `[ASSUMPTION` markers that are not well formed)."""
    found = []
    for match in ASSUMPTION_RE.finditer(body):
        resolved = bool(match.group("resolved"))
        found.append(
            {
                "id": match.group("id"),
                "num": int(match.group("num")) if match.group("num") else None,
                "resolved": resolved,
                "recorded": resolved and has_who_and_when(match.group("body")),
            }
        )
    malformed = len(ANY_ASSUMPTION_RE.findall(body)) - len(found)
    return found, malformed


def id_gaps(items: list[dict], prefix: str) -> list[str]:
    """Ids missing from 1..highest. Ids are never reused or removed, so a gap is a deleted item."""
    numbers = {i["num"] for i in items if i["num"] is not None}
    if not numbers:
        return []
    return [f"{prefix}-{n}" for n in range(1, max(numbers) + 1) if n not in numbers]


def all_ids(body: str) -> set[str]:
    questions = parse_questions(body)
    assumptions, _ = parse_assumptions(body)
    return {i["id"] for i in questions + assumptions if i["id"]}


def sort_ids(ids) -> list[str]:
    return sorted(ids, key=lambda i: (i[0], int(i.split("-")[1])))


# --- git -------------------------------------------------------------------------------------------


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def project_root_for(path: Path) -> Path:
    """Git top level of the folder holding `path`; the folder itself when it is not in git."""
    folder = path if path.is_dir() else path.parent
    try:
        out = git(["rev-parse", "--show-toplevel"], folder)
    except OSError:
        return folder.resolve()
    return Path(out.stdout.strip()).resolve() if out.returncode == 0 and out.stdout.strip() else folder.resolve()


def baseline_text(story: Path, ref: str) -> str | None:
    """The story as it was at `ref`; None when the file did not exist there. GateError when git cannot answer."""
    if ref.startswith("-"):
        raise GateError("BASELINE_UNAVAILABLE", "baseline ref must not start with '-'")
    folder = story.parent
    try:
        top = git(["rev-parse", "--show-toplevel"], folder)
    except OSError as err:
        raise GateError("BASELINE_UNAVAILABLE", f"git not available ({type(err).__name__})") from None
    if top.returncode != 0:
        raise GateError("BASELINE_UNAVAILABLE", "the story is not in a git repository")
    if git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], folder).returncode != 0:
        raise GateError("BASELINE_UNAVAILABLE", f"git ref '{ref}' not found")
    rel = story.resolve().relative_to(Path(top.stdout.strip()).resolve()).as_posix()
    shown = git(["show", f"{ref}:{rel}"], folder)
    return shown.stdout if shown.returncode == 0 else None


# --- epic summary ----------------------------------------------------------------------------------


def epic_summary_path(story: Path, fields: dict, explicit: str | None, epics_dir: str | None) -> Path | None:
    """--epic-summary, else frontmatter `epic_summary` (from the project root), else <epics-dir>/epic-<epic>/."""
    if explicit:
        return Path(explicit)
    if fields.get("epic_summary"):
        return project_root_for(story) / fields["epic_summary"]
    if epics_dir and fields.get("epic"):
        return Path(epics_dir) / f"epic-{fields['epic']}" / "epic-summary.md"
    return None


def epic_summary_missing_parts(text: str, parts: tuple[str, ...] = EPIC_PARTS) -> list[str]:
    """Parts (of 'functional', 'technical') that have no heading or no text under the heading."""
    _, body, _ = parse_frontmatter(text)
    filled: dict[str, bool] = {}
    current = None
    for line in body.splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            first = heading.group(2).strip().split(" ")[0].lower().strip(":")
            current = first if first in EPIC_PARTS else None
            continue
        if current and line.strip():
            filled[current] = True
    return [part for part in parts if not filled.get(part)]


def check_epic_summary(path: Path | None, has_frd: bool = False) -> list[dict]:
    """The summary's parts. With an FRD in the epic folder, the FRD is the functional part."""
    if path is None:
        return [{"code": "MISSING_EPIC_SUMMARY", "detail": "no epic summary given or linked (epic_summary or epic)"}]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [{"code": "MISSING_EPIC_SUMMARY", "detail": f"cannot read {path.name} at the given path"}]
    missing = epic_summary_missing_parts(text, ("technical",) if has_frd else EPIC_PARTS)
    if missing:
        detail = "no " + " and no ".join(f"'## {p.capitalize()}' part with text" for p in missing)
        return [{"code": "EPIC_SUMMARY_INCOMPLETE", "detail": detail}]
    return []


def check_frd(path: Path) -> tuple[list[dict], int | None]:
    """Reasons from the feature's FRD, and its open item count. A missing FRD blocks."""
    if not path.is_file():
        return [{"code": "FRD_UNAPPROVED", "detail": f"no {path.name} for this feature at {path}"}], 0
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        return [{"code": "FRD_UNAPPROVED", "detail": f"cannot read {path.name} ({type(err).__name__})"}], 0
    fields, body, fm_error = parse_frontmatter(text)
    reasons = []
    status = fields.get("approval_status", "")
    if fm_error:
        reasons.append({"code": "FRD_UNAPPROVED", "detail": f"{path.name}: {fm_error}"})
    elif status.lower() != APPROVED:
        detail = f"{path.name} approval_status is '{status or 'missing'}', not approved"
        reasons.append({"code": "FRD_UNAPPROVED", "detail": detail})
    questions = parse_questions(body)
    assumptions, malformed = parse_assumptions(body)
    items = questions + assumptions
    open_ids = [i["id"] for i in items if not i["resolved"] and i["id"]]
    no_id = sum(1 for i in items if not i["resolved"] and not i["id"]) + malformed
    unrecorded = [i["id"] or "an item without an id" for i in items if i["resolved"] and not i["recorded"]]
    gaps = id_gaps(questions, "Q") + id_gaps(assumptions, "A")
    seen, dupes = set(), set()
    for item in items:
        if item["id"] in seen:
            dupes.add(item["id"])
        if item["id"]:
            seen.add(item["id"])
    parts = []
    if open_ids or no_id:
        extra = f" (+{no_id} without an id or not in the agreed format)" if no_id else ""
        listed = ", ".join(sort_ids(open_ids)) + extra
        parts.append(f"open: {listed.strip()}")
    if unrecorded:
        parts.append("no '— <name>, <YYYY-MM-DD>' on: " + ", ".join(unrecorded))
    if gaps:
        parts.append("ids missing (deleted items count as open): " + ", ".join(sort_ids(gaps)))
    if dupes:
        parts.append("ids used more than once: " + ", ".join(sort_ids(dupes)))
    total = len(open_ids) + no_id + len(unrecorded) + len(gaps) + len(dupes)
    if parts:
        reasons.append({"code": "FRD_OPEN_ITEMS", "detail": f"{path.name}: " + "; ".join(parts)})
    return reasons, total


def check_feature_frd(fields: dict, frds_dir: str | None) -> tuple[list[dict], int | None]:
    """The FRD of the story's `feature` (None as count when the story names no feature)."""
    feature = fields.get("feature", "").strip().strip("'\"")
    if not feature:
        return [], None
    if not FEATURE_RE.match(feature):
        return [{"code": "FRD_UNAPPROVED", "detail": f"feature '{feature}' is not in the F-<nnn> format"}], 0
    if not frds_dir:
        return [{"code": "FRD_UNAPPROVED", "detail": f"no FRD folder configured to find the FRD of {feature}"}], 0
    return check_frd(Path(frds_dir) / feature / FRD_NAME)


# --- the gate --------------------------------------------------------------------------------------


def evaluate(
    text: str,
    story: Path,
    min_confidence: int,
    epic_summary: str | None = None,
    epics_dir: str | None = None,
    baseline: str | None = None,
    frds_dir: str | None = None,
) -> dict:
    """Return the result dict for one story text."""
    reasons: list[dict] = []
    fields, body, fm_error = parse_frontmatter(text)
    if fm_error:
        reasons.append({"code": "MALFORMED_FRONTMATTER", "detail": fm_error})

    if not fm_error and fields.get("gate", "").lower() == "generic":
        return {"result": "allow", "reasons": [], "counts": {}, "gate": "generic"}

    questions = parse_questions(body)
    assumptions, malformed_tags = parse_assumptions(body)
    open_q = [q for q in questions if not q["resolved"]]
    open_a = [a for a in assumptions if not a["resolved"]]
    unrecorded = [i for i in questions + assumptions if i["resolved"] and not i["recorded"]]

    deleted = set(id_gaps(questions, "Q")) | set(id_gaps(assumptions, "A"))
    if baseline:
        old = baseline_text(story, baseline)
        if old is not None:
            deleted |= all_ids(old) - all_ids(body)
    seen, duplicates = set(), set()
    for item in questions + assumptions:
        if item["id"] in seen:
            duplicates.add(item["id"])
        if item["id"]:
            seen.add(item["id"])

    if open_q:
        no_id = sum(1 for q in open_q if not q["id"])
        ids = ", ".join(sort_ids(q["id"] for q in open_q if q["id"]))
        detail = f"{len(open_q)} open question(s)" + (f": {ids}" if ids else "")
        reasons.append({"code": "OPEN_QUESTIONS", "detail": detail + (f" ({no_id} without an id)" if no_id else "")})
    if open_a or malformed_tags:
        ids = ", ".join(sort_ids(a["id"] for a in open_a if a["id"]))
        detail = f"{len(open_a) + malformed_tags} open assumption(s)" + (f": {ids}" if ids else "")
        if malformed_tags:
            detail += f" ({malformed_tags} [ASSUMPTION tag(s) not in the agreed format)"
        reasons.append({"code": "OPEN_ASSUMPTIONS", "detail": detail})
    if unrecorded:
        ids = ", ".join(sort_ids(i["id"] for i in unrecorded if i["id"])) or "items without an id"
        reasons.append({"code": "RESOLUTION_MISSING_WHO_OR_WHEN", "detail": f"no '— <name>, <YYYY-MM-DD>' on: {ids}"})
    if deleted:
        reasons.append(
            {
                "code": "DELETED_ITEMS",
                "detail": "ids missing (deleted items count as open): " + ", ".join(sort_ids(deleted)),
            }
        )
    if duplicates:
        reasons.append(
            {"code": "DUPLICATE_IDS", "detail": "ids used more than once: " + ", ".join(sort_ids(duplicates))}
        )

    confidence = None
    raw = fields.get("confidence", "")
    if not fm_error:
        if raw == "":
            reasons.append({"code": "MISSING_CONFIDENCE", "detail": "no 'confidence' in the frontmatter"})
        elif not re.fullmatch(r"\d{1,3}", raw) or int(raw) > 100:
            reasons.append({"code": "MISSING_CONFIDENCE", "detail": "'confidence' is not a whole number from 0 to 100"})
        else:
            confidence = int(raw)
            if confidence < min_confidence:
                reasons.append(
                    {"code": "LOW_CONFIDENCE", "detail": f"confidence {confidence} is below {min_confidence}"}
                )

    summary_path = epic_summary_path(story, fields, epic_summary, epics_dir)
    frd_reasons, frd_open = check_feature_frd(fields, frds_dir) if not fm_error else ([], None)
    reasons.extend(check_epic_summary(summary_path, has_frd=frd_open is not None))
    reasons.extend(frd_reasons)

    counts = {
        "open_questions": len(open_q),
        "resolved_questions": len(questions) - len(open_q),
        "open_assumptions": len(open_a) + malformed_tags,
        "resolved_assumptions": len(assumptions) - len(open_a),
        "resolutions_missing_who_or_when": len(unrecorded),
        "deleted_items": len(deleted),
        "confidence": confidence,
        "min_confidence": min_confidence,
        "frd_open_items": frd_open,
    }
    return {"result": "block" if reasons else "allow", "reasons": reasons, "counts": counts}


# --- events ----------------------------------------------------------------------------------------


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


def project_name(project_root: Path) -> str:
    try:
        data = json.loads((project_root / "_bmad/custom/project.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("project_name", "")) if isinstance(data, dict) else ""


def emit_event(result: dict, story: Path, sha256: str) -> None:
    """Record the decision as one guard event. Never raises; warns on stderr when it cannot."""
    try:
        ge = _guard_events()
        if ge is None:
            print(
                "bmad-guard: warning: Ledger (agent-project-logger) not installed; event not recorded", file=sys.stderr
            )
            return
        root = project_root_for(story if story.parent.is_dir() else Path.cwd())
        try:
            target = story.resolve().relative_to(root).as_posix()
        except ValueError:
            target = story.name
        allow = result["result"] == "allow"
        event = ge.build_event(
            GUARD,
            STAGE,
            "allow" if allow else "block",
            "READY_ALLOW" if allow else "READY_BLOCK",
            project=project_name(root),
            agent=AGENT,
            target_path=target,
            target_sha256=sha256,
            counts=json.dumps(result["counts"], sort_keys=True) if result["counts"] else "",
            reasons=",".join(r["code"] for r in result["reasons"]),
            gate=result.get("gate", ""),
        )
        ge.emit(event, ge.Config.for_project(root))
    except Exception as err:  # logging never changes a gate decision
        print(f"bmad-guard: warning: event not recorded ({type(err).__name__})", file=sys.stderr)


# --- CLI -------------------------------------------------------------------------------------------


def confidence_arg(value: str) -> int:
    if not re.fullmatch(r"\d{1,3}", value) or int(value) > 100:
        raise argparse.ArgumentTypeError("must be a whole number from 0 to 100")
    return int(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ready-for-dev gate: allow a story into development or block it.")
    parser.add_argument("story", help="story or plan markdown file")
    parser.add_argument("--epic-summary", help="the epic's epic-summary.md (default: linked from the story)")
    parser.add_argument("--epics-dir", help="folder with epic-<n>/epic-summary.md, used with frontmatter 'epic'")
    parser.add_argument("--frds-dir", help="folder with F-<nnn>/frd.md, used with frontmatter 'feature'")
    parser.add_argument("--min-confidence", type=confidence_arg, default=80, help="(default: %(default)s)")
    parser.add_argument("--baseline", help="git ref; ids present there but gone now count as deleted")
    parser.add_argument("--no-event", action="store_true", help="do not record a guard event")
    args = parser.parse_args(argv)

    story = Path(args.story)
    sha256, exit_code = "", 0
    try:
        try:
            raw = story.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as err:
            raise GateError("UNREADABLE_STORY", f"cannot read the story ({type(err).__name__})") from None
        sha256 = hashlib.sha256(raw).hexdigest()
        result = evaluate(
            text, story, args.min_confidence, args.epic_summary, args.epics_dir, args.baseline, args.frds_dir
        )
    except GateError as err:
        result = {"result": "block", "reasons": [{"code": err.code, "detail": err.detail}], "counts": {}}
        exit_code = 2

    if not args.no_event:
        emit_event(result, story, sha256)
    print(json.dumps(result))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
