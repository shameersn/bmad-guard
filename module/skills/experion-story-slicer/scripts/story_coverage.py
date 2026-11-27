# /// script
# requires-python = ">=3.10"
# ///
"""Story coverage check (ADR 0010): thin stories must cover the FRD, once, in order, without copying it.

The stories are written by experion-story-slicer. A story holds front matter and pointers, no functional text. This
script checks the whole tree of stories against the approved FRDs, with the shared parser `frd_parse.py` of the
ready-for-dev-gate skill (installed next to this skill).

Layout (default folders under `_bmad-output/planning-artifacts/`):
  <stories-dir>/F-<nnn>/<story>-<slug>.md      feature stories, e.g. F001-02-list-free-slots.md
  <stories-dir>/F-<nnn>/technical-summary.md   not a story (the feature's technical part)
  <stories-dir>/technical/T-<nn>-<slug>.md     technical stories (`feature: none`, no `requirements` needed)
  <stories-dir>/stories.md                     the build order: one list item per story, `- [F001-01](F-001/...) — ...`
  <frds-dir>/F-<nnn>/frd.md                    the feature FRDs

Story front matter read: story, feature (`F-<nnn>` or `none`), requirements (`[FR-F001-02, FR-F001-04#1-4]`, one
line), depends_on (`[F001-01]`). `FR-F001-04#1-4` is a split: only those acceptance criteria (sub-items come along).

Checks (every code blocks here; where each code blocks or only warns in the gate and pre-commit is set there):
  STORY_FR_UNKNOWN        a `requirements` id is not in the FRD, is withdrawn, is malformed, names an acceptance
                          criterion the FR does not have, or the feature story has no requirements
  STORY_FR_OTHER_FEATURE  an FR id of another feature than the story's
  STORY_FR_UNCOVERED      a non-withdrawn FR (or, for a split FR, an acceptance criterion) of the feature is in no story
  STORY_FR_TWICE          the same FR or acceptance criterion is in two stories (whole FR plus a split counts too)
  STORY_FRD_TEXT          a story line of 8 or more words repeats an FRD line of a requirement block (Section 4) or a
                          screen (Section 5). Both lines are lower-cased, stripped of `*`, backticks, bullets and list
                          numbers, and spaces are collapsed; a table cell is a line. A match is the same text, or one
                          line inside the other. Headings do not count. A shorter shared phrase never matches.
  STORY_ORDER             `depends_on` names a missing story, a later story or is part of a cycle; a story file is not
                          in stories.md; a listed story has no file; stories.md is missing or lists a story twice
  STORY_FRD_UNREADABLE    the feature's FRD is missing, unreadable or has parse problems (coverage is skipped for it)
  STORY_FILE_INVALID      a story file has no usable front matter or id, or its folder, feature or name do not agree

A feature is checked when it has a story folder, when `--feature` names it, or when its FRD is approved
(`approval_status: approved`), so an approved FRD that nobody sliced yet shows up as uncovered.

Usage:
  python3 story_coverage.py [--stories-dir DIR] [--frds-dir DIR] [--feature F-001] [--no-event]

Prints one JSON object:
  {"result": "allow" | "block", "reasons": [{"code", "detail", "story"?, "feature"?}], "counts": {...}}

Exit code 0 when the check ran (allow or block); 2 for bad arguments or when it could not run (a folder is not
readable, frd_parse.py is missing); the JSON then says "block" too, so a caller that only reads the JSON fails closed.
Every run records one guard event (guard "story-coverage", `STORIES_CHECKED`) unless --no-event is given. The event
holds the counts and the reason codes, never document text.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

GUARD = "story-coverage"
STAGE = "story-coverage"
AGENT = "experion-story-slicer/COVERAGE"
EVENT = "STORIES_CHECKED"

DEFAULT_STORIES_DIR = "_bmad-output/planning-artifacts/stories"
DEFAULT_FRDS_DIR = "_bmad-output/planning-artifacts/frds"
ORDER_FILE = "stories.md"
FRD_NAME = "frd.md"
SUMMARY_NAME = "technical-summary.md"  # lives in a feature folder but is not a story
TECHNICAL_DIR = "technical"
MIN_WORDS = 8  # a story line must be this long to count as copied text
MAX_LISTED = 10  # numbers named in one reason

FEATURE_RE = re.compile(r"^F-\d{1,4}$")
STORY_ID_RE = re.compile(r"^(?:F\d{1,4}-\d{2,3}|T-\d{2,3})$")
STORY_PREFIX_RE = re.compile(r"^(?:F\d{1,4}-\d{2,3}|T-\d{2,3})")
LISTED_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[([^\]]+)\]")
LIST_MARK_RE = re.compile(r"^\s*(?:[-*+]|\d+(?:\.\d+)*[.)])\s+")
HEADING_LINE_RE = re.compile(r"^\s*#")

PROBLEM_TO_CODE = {  # frd_parse selection problem -> story code. Other selection problems are FRD defects.
    "FR_UNKNOWN": "STORY_FR_UNKNOWN",
    "FR_WITHDRAWN": "STORY_FR_UNKNOWN",
    "FR_ID_MALFORMED": "STORY_FR_UNKNOWN",
    "FR_AC_UNKNOWN": "STORY_FR_UNKNOWN",
    "FR_OTHER_FEATURE": "STORY_FR_OTHER_FEATURE",
}


class CoverageError(Exception):
    """A problem that stops the check from running at all (exit 2)."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


_frd_parse = None


def load_frd_parse():
    """The shared FRD parser, from the sibling ready-for-dev-gate skill folder, loaded by path."""
    global _frd_parse
    if _frd_parse is None:
        path = Path(__file__).resolve().parents[2] / "ready-for-dev-gate" / "scripts" / "frd_parse.py"
        if not path.is_file():
            raise CoverageError(
                "FRD_PARSE_MISSING",
                "frd_parse.py is missing (ready-for-dev-gate skill is not installed next to this one)",
            )
        spec = importlib.util.spec_from_file_location("frd_parse", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _frd_parse = module
    return _frd_parse


def reason(code: str, detail: str, story: str = "", feature: str = "") -> dict:
    out = {"code": code, "detail": detail}
    if story:
        out["story"] = story
    if feature:
        out["feature"] = feature
    return out


def listed(numbers: list) -> str:
    return ", ".join(str(n) for n in numbers[:MAX_LISTED]) + (", ..." if len(numbers) > MAX_LISTED else "")


def feature_of_id(story_id: str) -> str:
    """`F001-02` -> `F-001`, `T-01` -> `none`, anything else -> ''."""
    if story_id.startswith("T-"):
        return "none"
    match = re.match(r"^F(\d+)-", story_id)
    return f"F-{match.group(1)}" if match else ""


def id_list(value: str) -> list[str]:
    return [item for item in load_frd_parse().split_ids(value) if item.lower() != "none"]


# --- stories ---------------------------------------------------------------------------------------


def story_files(stories_dir: Path) -> list[tuple[Path, str]]:
    """(file, folder feature) of every story file; the folder feature is `none` for technical stories."""
    found = []
    for folder in sorted(p for p in stories_dir.iterdir() if p.is_dir()):
        if FEATURE_RE.match(folder.name):
            feature = folder.name
        elif folder.name == TECHNICAL_DIR:
            feature = "none"
        else:
            continue
        found += [(f, feature) for f in sorted(folder.glob("*.md")) if f.name != SUMMARY_NAME]
    return found


def read_story(path: Path, folder_feature: str, label: str) -> tuple[dict | None, list[dict]]:
    """(story or None, problems). A story with no id cannot be used and comes back as None."""
    parse_frontmatter = load_frd_parse().parse_frontmatter
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        detail = f"{label}: cannot read the file ({type(err).__name__})"
        return None, [reason("STORY_FILE_INVALID", detail, "", folder_feature)]
    fields, body, error = parse_frontmatter(text)
    problems = []

    def bad(detail: str, story_id: str = "") -> None:
        problems.append(reason("STORY_FILE_INVALID", detail, story_id, folder_feature))

    if error:
        bad(f"{label}: {error}")
    story_id = fields.get("story") or (STORY_PREFIX_RE.match(path.stem) or [""])[0]
    if not STORY_ID_RE.match(story_id):
        bad(f"{label}: no valid 'story:' id (F<nnn>-<nn> or T-<nn>)")
        return None, problems
    if not (path.stem == story_id or path.stem.startswith(story_id + "-")):
        bad(f"{label}: file name does not start with '{story_id}'", story_id)
    declared = fields.get("feature", "").strip() or folder_feature
    if declared != folder_feature:
        detail = f"{label}: feature '{declared[:20]}' does not match its folder ({folder_feature})"
        bad(detail, story_id)
    offset = text[: len(text) - len(body)].count("\n")
    story = {
        "id": story_id,
        "label": label,
        "feature": folder_feature,
        "requirements": id_list(fields.get("requirements", "")),
        "depends_on": id_list(fields.get("depends_on", "")),
        "body": body,
        "offset": offset,
    }
    return story, problems


def read_order(stories_dir: Path) -> list[str] | None:
    """The story ids listed in stories.md, in order; None when the file cannot be read."""
    try:
        lines = (stories_dir / ORDER_FILE).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return [m.group(1).strip() for m in (LISTED_RE.match(line) for line in lines) if m]


# --- FRD text --------------------------------------------------------------------------------------


def normalised_lines(text: str) -> list[str]:
    """Each line and table cell, cleaned as described at STORY_FRD_TEXT, that has MIN_WORDS words or more."""
    found = []
    for line in text.splitlines():
        for cell in line.split("|"):
            cell = LIST_MARK_RE.sub("", cell).replace("*", "").replace("`", "")
            cell = " ".join(cell.lower().split())
            if len(cell.split()) >= MIN_WORDS:
                found.append(cell)
    return found


def frd_text_index(frd: dict) -> list[tuple[str, str]]:
    """(normalised line, section) for every line of the FR blocks and screens of the FRD. Headings are left out."""
    index = []
    for part in frd["blocks"] + frd["screens"]:
        label = part.get("section") or part["id"]
        body = "\n".join(line for line in part["text"].splitlines() if not HEADING_LINE_RE.match(line))
        index += [(line, label) for line in normalised_lines(body)]
    return index


def copied_text(story: dict, index: list[tuple[str, str]]) -> list[dict]:
    """A STORY_FRD_TEXT reason when story lines repeat FRD lines; names line numbers and sections, never text."""
    lines, sections = [], []
    for number, line in enumerate(story["body"].splitlines(), start=story["offset"] + 1):
        for mine in normalised_lines(line):
            hits = [label for text, label in index if f" {mine} " in f" {text} " or f" {text} " in f" {mine} "]
            if hits:
                lines.append(number)
                sections += hits
                break
    if not lines:
        return []
    detail = f"line {listed(lines)} repeats FRD text (Section {listed(sorted(set(sections)))}); point to the FR instead"
    return [reason("STORY_FRD_TEXT", detail, story["id"], story["feature"])]


# --- one feature -----------------------------------------------------------------------------------


def load_frd(frds_dir: Path, feature: str) -> tuple[dict | None, dict | None]:
    """(frd, None) or (None, STORY_FRD_UNREADABLE reason)."""
    path = frds_dir / feature / FRD_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, reason(
            "STORY_FRD_UNREADABLE", f"{feature}: no readable {feature}/{FRD_NAME} in the FRDs folder", "", feature
        )
    frd = load_frd_parse().parse_frd(text)
    problems = [p["code"] for p in frd["problems"]]
    if frd["feature"] and frd["feature"] != feature:
        problems.append("FRD_FEATURE_MISMATCH")
    if problems:
        detail = f"{feature}: the FRD has problems ({', '.join(dict.fromkeys(problems))}); coverage is skipped"
        return None, reason("STORY_FRD_UNREADABLE", detail, "", feature)
    return frd, None


def check_feature(feature: str, frd: dict, stories: list[dict]) -> tuple[list[dict], int, int]:
    """(reasons, FRs checked, FRs fully covered) for the stories of one feature against its FRD."""
    select_blocks = load_frd_parse().select_blocks
    index = frd_text_index(frd)
    reasons: list[dict] = []
    users: dict[str, list[tuple[str, list[int] | None]]] = {}  # FR id -> [(story, AC numbers or None for all)]
    for story in stories:
        if not story["requirements"]:
            detail = "no requirements (write 'requirements: [FR-<Fnnn>-<nn>]' on one line)"
            reasons.append(reason("STORY_FR_UNKNOWN", detail, story["id"], feature))
        picked = select_blocks(frd, story["requirements"])
        for problem in picked["problems"]:
            if problem["code"] in PROBLEM_TO_CODE:
                reasons.append(reason(PROBLEM_TO_CODE[problem["code"]], problem["detail"], story["id"], feature))
        for block in picked["blocks"]:
            users.setdefault(block["id"], []).append((story["id"], block["ac_selected"]))
        reasons += copied_text(story, index)

    checked = covered = 0
    for block in frd["blocks"]:
        if block["withdrawn"]:
            continue
        checked += 1
        fr_id, used = block["id"], users.get(block["id"], [])
        numbers = sorted({int(c["number"].split(".")[0]) for c in block["criteria"]})
        owners = {n: [sid for sid, picked in used if picked is None or n in picked] for n in numbers}
        gaps = [n for n in numbers if not owners[n]]
        if not used:
            reasons.append(reason("STORY_FR_UNCOVERED", f"{fr_id} is in no story", "", feature))
        elif gaps:
            detail = f"{fr_id} acceptance criteria {listed(gaps)} are in no story"
            reasons.append(reason("STORY_FR_UNCOVERED", detail, "", feature))
        else:
            covered += 1
        whole = [sid for sid, picked in used if picked is None]
        doubled = [n for n in numbers if len(owners[n]) > 1]
        if len(whole) > 1 or (whole and len(used) > len(whole)):
            detail = f"{fr_id} is in more than one story: {', '.join(sid for sid, _ in used)}"
            reasons.append(reason("STORY_FR_TWICE", detail, "", feature))
        elif doubled:
            detail = f"{fr_id} acceptance criteria {listed(doubled)} are in more than one story"
            reasons.append(reason("STORY_FR_TWICE", detail, "", feature))
    return reasons, checked, covered


# --- order -----------------------------------------------------------------------------------------


def reaches(graph: dict[str, list[str]], start: str, target: str) -> bool:
    """True when `target` can be reached from `start` along depends_on edges (start itself counts)."""
    seen, todo = set(), [start]
    while todo:
        node = todo.pop()
        if node == target:
            return True
        if node not in seen:
            seen.add(node)
            todo += graph.get(node, [])
    return False


def check_order(stories: list[dict], order: list[str] | None) -> list[dict]:
    reasons: list[dict] = []
    by_id = {s["id"]: s for s in stories}
    position: dict[str, int] = {}
    if order is None:
        reasons.append(reason("STORY_ORDER", f"{ORDER_FILE} is missing or not readable"))
    else:
        for number, story_id in enumerate(order):
            if story_id in position:
                reasons.append(reason("STORY_ORDER", f"{story_id} is listed twice in {ORDER_FILE}", story_id))
            position.setdefault(story_id, number)
            if story_id not in by_id:
                detail = f"{story_id} is listed in {ORDER_FILE} but has no story file"
                reasons.append(reason("STORY_ORDER", detail, story_id, feature_of_id(story_id)))
        for story in stories:
            if story["id"] not in position:
                detail = f"{story['id']} has a story file but is not listed in {ORDER_FILE}"
                reasons.append(reason("STORY_ORDER", detail, story["id"], story["feature"]))

    graph = {s["id"]: s["depends_on"] for s in stories}
    for story in stories:
        for dep in story["depends_on"]:
            if dep not in by_id and dep not in position:
                detail = f"{story['id']} depends on {dep[:20]}, which is not a story"
                reasons.append(reason("STORY_ORDER", detail, story["id"], story["feature"]))
            elif reaches(graph, dep, story["id"]):
                detail = f"{story['id']} depends on {dep}, which depends back on it (cycle)"
                reasons.append(reason("STORY_ORDER", detail, story["id"], story["feature"]))
            elif dep in position and story["id"] in position and position[dep] > position[story["id"]]:
                detail = f"{story['id']} depends on {dep}, which comes later in {ORDER_FILE}"
                reasons.append(reason("STORY_ORDER", detail, story["id"], story["feature"]))
    return reasons


# --- the check -------------------------------------------------------------------------------------


def evaluate(stories_dir: Path, frds_dir: Path, feature: str | None = None) -> dict:
    """Check the stories tree. Raises CoverageError when the check cannot run at all.

    `feature` (`F-001`) limits the result to that feature's stories and FRD. Returns
    {"result", "reasons": [{"code", "detail", "story"?, "feature"?}], "counts"}.
    """
    load_frd_parse()  # fail first when the parser is missing
    stories_dir, frds_dir = Path(stories_dir), Path(frds_dir)
    try:
        files = story_files(stories_dir)
    except OSError as err:
        raise CoverageError(
            "STORIES_DIR_UNREADABLE", f"cannot read the stories folder ({type(err).__name__})"
        ) from None
    try:
        frd_features = sorted(p.name for p in frds_dir.iterdir() if p.is_dir() and FEATURE_RE.match(p.name))
    except OSError as err:
        raise CoverageError("FRDS_DIR_UNREADABLE", f"cannot read the FRDs folder ({type(err).__name__})") from None

    reasons: list[dict] = []
    stories: list[dict] = []
    seen_ids: set[str] = set()
    for path, folder_feature in files:
        story, problems = read_story(path, folder_feature, path.relative_to(stories_dir).as_posix())
        reasons += problems
        if story and story["id"] in seen_ids:
            reasons.append(
                reason("STORY_FILE_INVALID", f"{story['label']}: story id used twice", story["id"], story["feature"])
            )
        elif story:
            seen_ids.add(story["id"])
            stories.append(story)

    with_stories = {s["feature"] for s in stories if s["feature"] != "none"}
    todo = [feature] if feature else sorted(with_stories | set(frd_features))
    frds: dict[str, dict] = {}
    checked = covered = 0
    for name in todo:
        frd, problem = load_frd(frds_dir, name)
        if (
            name not in with_stories
            and name != feature
            and (problem or frd["fields"].get("approval_status") != "approved")
        ):
            continue  # not sliced yet, and not an approved readable FRD: nothing to cover
        if problem:
            reasons.append(problem)
            continue
        frds[name] = frd
        found, count, done = check_feature(name, frd, [s for s in stories if s["feature"] == name])
        reasons += found
        checked, covered = checked + count, covered + done

    everything = [line for frd in frds.values() for line in frd_text_index(frd)]
    for story in stories:
        if story["feature"] == "none":
            reasons += copied_text(story, everything)
    reasons += check_order(stories, read_order(stories_dir))

    if feature:  # keep this feature's reasons and the ones about the whole tree
        reasons = [r for r in reasons if r.get("feature", feature) == feature]
    counts = {
        "features": len(frds),
        "stories": sum(1 for s in stories if s["feature"] in frds),
        "technical_stories": sum(1 for s in stories if s["feature"] == "none"),
        "frs": checked,
        "frs_covered": covered,
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


def project_root_for(path: Path) -> Path:
    """Git top level of `path`; the current folder when it is not in git."""
    folder = path if path.is_dir() else Path.cwd()
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=folder, capture_output=True, encoding="utf-8")
    except OSError:
        return Path.cwd().resolve()
    return Path(out.stdout.strip()).resolve() if out.returncode == 0 and out.stdout.strip() else Path.cwd().resolve()


def project_name(project_root: Path) -> str:
    try:
        data = json.loads((project_root / "_bmad/custom/project.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("project_name", "")) if isinstance(data, dict) else ""


def emit_event(result: dict, stories_dir: Path) -> None:
    """Record the check as one guard event. Never raises; warns on stderr when it cannot."""
    try:
        ge = _guard_events()
        if ge is None:
            print(
                "bmad-guard: warning: Ledger (agent-project-logger) not installed; event not recorded", file=sys.stderr
            )
            return
        root = project_root_for(stories_dir)
        try:
            target = stories_dir.resolve().relative_to(root).as_posix()
        except ValueError:
            target = stories_dir.name
        event = ge.build_event(
            GUARD,
            STAGE,
            "allow" if result["result"] == "allow" else "block",
            EVENT,
            project=project_name(root),
            agent=AGENT,
            target_path=target,
            counts=json.dumps(result["counts"], sort_keys=True) if result["counts"] else "",
            reasons=",".join(dict.fromkeys(r["code"] for r in result["reasons"])),
        )
        ge.emit(event, ge.Config.for_project(root))
    except Exception as err:  # logging never changes a result
        print(f"bmad-guard: warning: event not recorded ({type(err).__name__})", file=sys.stderr)


# --- CLI -------------------------------------------------------------------------------------------


def feature_arg(value: str) -> str:
    if not FEATURE_RE.match(value):
        raise argparse.ArgumentTypeError("must look like F-001")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that the thin stories cover the FRDs once, in order.")
    parser.add_argument("--stories-dir", default=DEFAULT_STORIES_DIR, help="(default: %(default)s)")
    parser.add_argument("--frds-dir", default=DEFAULT_FRDS_DIR, help="(default: %(default)s)")
    parser.add_argument("--feature", type=feature_arg, help="check only this feature, e.g. F-001")
    parser.add_argument("--no-event", action="store_true", help="do not record a guard event")
    args = parser.parse_args(argv)

    exit_code = 0
    try:
        result = evaluate(Path(args.stories_dir), Path(args.frds_dir), args.feature)
    except CoverageError as err:
        result = {"result": "block", "reasons": [{"code": err.code, "detail": err.detail}], "counts": {}}
        exit_code = 2
    if not args.no_event:
        emit_event(result, Path(args.stories_dir))
    print(json.dumps(result))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
