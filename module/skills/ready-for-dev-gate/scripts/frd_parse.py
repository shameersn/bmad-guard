# /// script
# requires-python = ">=3.10"
# ///
"""One reading of a feature FRD (ADR 0010): the ready-for-dev gate, the story slicer and the build share it.

The FRD is written by experion-frd (template: experion-frd/assets/frd-template.md). This file reads:

  front matter      `parse_frontmatter` (also used by the gate for stories) and the feature id
  Section 4         one block per `### 4.<n>. FR-<Fnnn>-<nn> — <title>`: id, title, section, withdrawn, screen line,
                    acceptance criteria as numbered items (sub-items as `7.1`, `7.2`), Key Transactions table, raw text
  Section 5         one screen per `### 5.<n>. <title>`: id `5.<n>`, title, raw text
  Section 8.1       the inherited NFR rows: NFR id, category, target, applies-to text

It never raises on odd input. A defect comes back as a problem: {"code": ..., "detail": ...}.

Library:
  parse_frd(text)             -> {"fields", "body", "frontmatter_error", "feature", "frd_id", "blocks", "screens",
                                  "nfrs", "problems"}
  split_ids(text)             -> ["FR-F001-02", "FR-F001-04#2,5"]   (a bare number or range after a `#` id stays on it)
  select_blocks(frd, ids)     -> {"blocks", "screens", "nfrs", "problems"} for the requested ids. An id is
                                 `FR-F001-02` (the whole FR) or `FR-F001-04#1-4` / `FR-F001-04#2,5` (only those
                                 criteria, with their sub-items). Problem codes: FR_ID_MALFORMED, FR_OTHER_FEATURE,
                                 FR_UNKNOWN, FR_DUPLICATE, FR_WITHDRAWN, FR_AC_UNKNOWN, FR_SCREEN_UNKNOWN.

Parse problem codes: FRD_FRONTMATTER, FRD_NO_FEATURE, FRD_NO_SECTION_4, FRD_NO_FR_BLOCKS, FRD_FR_HEADING_MALFORMED,
FRD_FR_DUPLICATE, FRD_FR_NO_CRITERIA.

Usage:
  python3 frd_parse.py --frd FILE [--ids FR-F001-02,FR-F001-04#1-4] [--json]

Prints one JSON object (JSON is always printed; --json is accepted and changes nothing):
  {"result": "ok" | "problems", "frd": ..., "feature": ..., "frd_id": ..., "blocks": [...], "screens": [...],
   "nfrs": [...], "problems": [{"code": ..., "detail": ...}]}
With --ids: only those FR blocks, the Section 5 screens they name, and the Section 8.1 rows that apply (a row applies
when its text names none of the FR ids, or names one of the requested ones). Without --ids: everything.

Exit code 0 when the file was read (problems are in the JSON); 2 for bad arguments or an unreadable file (the JSON
then says "problems" too).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FRONTMATTER_OPEN_RE = re.compile(r"\A---\r?\n")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n?---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")

FEATURE_RE = re.compile(r"^F-\d{1,4}$")
FR_ID_RE = re.compile(r"^FR-(F\d{1,4})-\d{2,3}$")
FR_IN_TEXT_RE = re.compile(r"(?<![A-Za-z0-9])FR-F\d{1,4}-\d{2,3}")  # not the FR- inside FNFR-
FR_HEADING_RE = re.compile(r"^4\.(\d+)\.?\s+(FR-F\d{1,4}-\d{2,3})\s+(?:—|–|-{1,2})\s+(\S.*)$")
SCREEN_HEADING_RE = re.compile(r"^5\.(\d+)\.?\s+(\S.*)$")
WITHDRAWN_RE = re.compile(r"(?<![\w*_])[*_]{1,2}withdrawn[*_]{1,2}(?![\w*_])", re.IGNORECASE)
SCREEN_LINE_RE = re.compile(r"^\s*Screen\s*:", re.IGNORECASE)
TOP_ITEM_RE = re.compile(r"^ {0,3}(\d+)[.)]\s+(.*)$")
SUB_ITEM_RE = re.compile(r"^(?: {4,}|\t+)(\d+)[.)]\s+(.*)$")
BARE_AC_RE = re.compile(r"^\d+(?:-\d+)?$")
RANGE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")
MAX_RANGE = 999  # a longer AC range is a typo, not a request


# --- front matter ----------------------------------------------------------------------------------


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


# --- small helpers ---------------------------------------------------------------------------------


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line index, level, text) of every Markdown heading outside a code fence."""
    found, fence = [], None
    for index, line in enumerate(lines):
        fenced = FENCE_RE.match(line)
        if fenced:
            fence = None if fence == fenced.group(1) else (fence or fenced.group(1))
            continue
        heading = None if fence else HEADING_RE.match(line)
        if heading:
            found.append((index, len(heading.group(1)), heading.group(2).strip()))
    return found


def _span(heads: list, total: int, position: int) -> tuple[int, int]:
    """Line range of heads[position]: its heading line, up to the next heading of the same or a higher level."""
    start, level = heads[position][0], heads[position][1]
    for line, deeper, _ in heads[position + 1 :]:
        if deeper <= level:
            return start, line
    return start, total


def _section(heads: list, total: int, number: str) -> tuple[int, int] | None:
    """Line range of the `## <number>.` heading (heading line included), None when there is no such heading."""
    wanted = re.compile(rf"^{number}\.?(?:\s|$)")
    for position, (_, level, text) in enumerate(heads):
        if level == 2 and wanted.match(text):
            return _span(heads, total, position)
    return None


def _cells(line: str) -> list[str]:
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [cell.strip().replace("\\|", "|") for cell in CELL_SPLIT_RE.split(inner)]


def _table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """(header, rows) of the first Markdown table in `lines`; ([], []) when there is none."""
    rows: list[list[str]] = []
    for line in lines:
        if line.strip().startswith("|"):
            rows.append(_cells(line))
        elif rows:
            break
    if not rows:
        return [], []
    body = [row for row in rows[1:] if not all(SEPARATOR_CELL_RE.match(cell) for cell in row)]
    return rows[0], body


def _text(lines: list[str]) -> str:
    return "\n".join(lines).strip("\n")


# --- Section 4 -------------------------------------------------------------------------------------


def _criteria(lines: list[str]) -> list[dict]:
    """Numbered items as {"number": "7", "text": ...}; an indented item under item 7 is "7.1". A wrapped line joins."""
    items: list[dict] = []
    parent = None
    for line in lines:
        if not line.strip():
            continue
        top, sub = TOP_ITEM_RE.match(line), SUB_ITEM_RE.match(line)
        if top:
            parent = str(int(top.group(1)))
            items.append({"number": parent, "text": top.group(2).strip()})
        elif sub and parent:
            items.append({"number": f"{parent}.{int(sub.group(1))}", "text": sub.group(2).strip()})
        elif items:
            items[-1]["text"] += " " + line.strip()
    return items


def _after_heading(heads: list, total: int, word: str) -> tuple[int, int] | None:
    """Lines under the first sub-heading (below the block heading) that has `word` in it, up to the next heading."""
    for position, (line, _, text) in enumerate(heads):
        if line > 0 and word in text.lower():
            following = heads[position + 1 :]
            return line + 1, following[0][0] if following else total
    return None


def _parse_block(lines: list[str], line_no: int, match: re.Match) -> dict:
    """One FR block. `lines` start at the `### 4.<n>.` heading."""
    heads = _headings(lines)
    first_body = next((line for line in lines[1:] if line.strip()), "")
    withdrawn = bool(WITHDRAWN_RE.search(lines[0]) or WITHDRAWN_RE.search(first_body))
    intro_end = heads[1][0] if len(heads) > 1 else len(lines)
    screen = next((line.strip() for line in lines[1:intro_end] if SCREEN_LINE_RE.match(line)), "")
    before_figma = re.split(r"figma", screen, maxsplit=1, flags=re.IGNORECASE)[0]
    screen_ids = list(dict.fromkeys(f"5.{n}" for n in re.findall(r"(?<![\w.])5\.(\d+)\b", before_figma)))
    ac = _after_heading(heads, len(lines), "acceptance criteria")
    kt = _after_heading(heads, len(lines), "key transactions")
    header, rows = _table(lines[kt[0] : kt[1]]) if kt else ([], [])
    return {
        "id": match.group(2),
        "title": WITHDRAWN_RE.sub("", match.group(3)).strip(" \t-—–:"),
        "section": f"4.{int(match.group(1))}",
        "line": line_no,
        "withdrawn": withdrawn,
        "screen": screen,
        "screen_ids": screen_ids,
        "criteria": _criteria(lines[ac[0] : ac[1]]) if ac else [],
        "transactions": {"header": header, "rows": rows},
        "text": _text(lines),
    }


def _parse_blocks(lines: list[str], heads: list, bounds: tuple[int, int], offset: int) -> tuple[list, list]:
    """(blocks, problems) of the `###` headings inside Section 4. `offset` turns a body line into a file line."""
    blocks, problems = [], []
    start, end = bounds
    for position, (line, level, text) in enumerate(heads):
        if level != 3 or not start <= line < end:
            continue
        match = FR_HEADING_RE.match(text)
        if not match:
            detail = f"line {line + offset + 1}: '{text[:60]}' is not '4.<n>. FR-<Fnnn>-<nn> — <title>'"
            problems.append({"code": "FRD_FR_HEADING_MALFORMED", "detail": detail})
            continue
        first, last = _span(heads, len(lines), position)
        blocks.append(_parse_block(lines[first:last], line + offset + 1, match))
    return blocks, problems


# --- Sections 5 and 8.1 ----------------------------------------------------------------------------


def _parse_screens(lines: list[str], heads: list) -> list[dict]:
    bounds = _section(heads, len(lines), "5")
    screens = []
    for position, (line, level, text) in enumerate(heads):
        match = SCREEN_HEADING_RE.match(text)
        if bounds and level == 3 and bounds[0] <= line < bounds[1] and match:
            first, last = _span(heads, len(lines), position)
            screens.append(
                {"id": f"5.{int(match.group(1))}", "title": match.group(2), "text": _text(lines[first:last])}
            )
    return screens


def _parse_nfrs(lines: list[str], heads: list) -> list[dict]:
    """The rows of the Section 8.1 table (NFR id, category, target, applies-to)."""
    for position, (_, level, text) in enumerate(heads):
        if level == 3 and re.match(r"^8\.1\.?(?:\s|$)", text):
            first, last = _span(heads, len(lines), position)
            _, rows = _table(lines[first + 1 : last])
            return [
                {"id": r[1], "category": r[2], "target": r[3], "applies_to": r[4]} for r in rows if len(r) >= 5 and r[1]
            ]
    return []


# --- the FRD ---------------------------------------------------------------------------------------


def parse_frd(text: str) -> dict:
    """Read one FRD. Never raises; a defect is a problem in the result."""
    fields, body, fm_error = parse_frontmatter(text)
    problems: list[dict] = []
    if fm_error:
        problems.append({"code": "FRD_FRONTMATTER", "detail": fm_error})
    feature = fields.get("feature", "").strip()
    if not FEATURE_RE.match(feature):
        feature = ""
        problems.append({"code": "FRD_NO_FEATURE", "detail": "no 'feature: F-<nnn>' in the frontmatter"})

    lines = body.splitlines()
    offset = text[: len(text) - len(body)].count("\n")
    heads = _headings(lines)
    bounds = _section(heads, len(lines), "4")
    blocks: list[dict] = []
    if bounds is None:
        problems.append({"code": "FRD_NO_SECTION_4", "detail": "no '## 4.' heading (Requirement Details)"})
    else:
        blocks, bad_headings = _parse_blocks(lines, heads, bounds, offset)
        problems.extend(bad_headings)
        if not blocks and not bad_headings:
            problems.append({"code": "FRD_NO_FR_BLOCKS", "detail": "Section 4 has no '### 4.<n>. FR-...' heading"})
    seen: set[str] = set()
    for block in blocks:
        if block["id"] in seen:
            problems.append(
                {"code": "FRD_FR_DUPLICATE", "detail": f"{block['id']} is the heading of more than one block"}
            )
        seen.add(block["id"])
        if not block["withdrawn"] and not block["criteria"]:
            problems.append(
                {"code": "FRD_FR_NO_CRITERIA", "detail": f"{block['id']} has no numbered acceptance criteria"}
            )
    problems = _unique(problems)
    return {
        "fields": fields,
        "body": body,
        "frontmatter_error": fm_error,
        "feature": feature,
        "frd_id": fields.get("frd_id", ""),
        "blocks": blocks,
        "screens": _parse_screens(lines, heads),
        "nfrs": _parse_nfrs(lines, heads),
        "problems": problems,
    }


def _unique(problems: list[dict]) -> list[dict]:
    """A duplicate FR id is one problem however often it repeats."""
    out = []
    for problem in problems:
        if problem not in out:
            out.append(problem)
    return out


# --- lookup ----------------------------------------------------------------------------------------


def split_ids(text: str) -> list[str]:
    """`FR-F001-02,FR-F001-04#2,5` -> ['FR-F001-02', 'FR-F001-04#2,5']. Brackets and quotes are dropped."""
    items: list[str] = []
    for part in re.split(r"\s*,\s*", text.strip().strip("[]")):
        part = part.strip().strip("'\"")
        if not part:
            continue
        if items and "#" in items[-1] and BARE_AC_RE.match(part):
            items[-1] += "," + part  # the rest of an AC list
        else:
            items.append(part)
    return items


def _parse_id(spec: str) -> tuple[str, set[int] | None, str]:
    """(FR id, AC numbers or None for the whole FR, error). `FR-F001-04#1-4` and `FR-F001-04#2,5` name AC numbers."""
    fr_id, _, acs = spec.strip().partition("#")
    fr_id = fr_id.strip()
    if not FR_ID_RE.match(fr_id):
        return "", None, f"'{spec[:40]}' is not an FR id (FR-<Fnnn>-<nn>)"
    if "#" not in spec:
        return fr_id, None, ""
    numbers: set[int] = set()
    for part in acs.split(","):
        match = RANGE_RE.match(part.strip())
        low, high = (int(match.group(1)), int(match.group(2) or match.group(1))) if match else (0, -1)
        if low < 1 or high < low or high - low > MAX_RANGE:
            return "", None, f"'{spec[:40]}': AC numbers after '#' must be like 1-4 or 2,5"
        numbers.update(range(low, high + 1))
    return fr_id, numbers, ""


def _top(criterion: dict) -> int:
    return int(criterion["number"].split(".")[0])


def _only(block: dict, numbers: set[int]) -> dict:
    """The block with only the criteria (and sub-items) whose top number is wanted. The text is rebuilt from them."""
    kept = [c for c in block["criteria"] if _top(c) in numbers]
    lines = block["text"].splitlines()
    ac = _after_heading(_headings(lines), len(lines), "acceptance criteria")
    items = []
    for c in kept:
        top, _, sub = c["number"].partition(".")
        items.append(f"    {sub}. {c['text']}" if sub else f"{top}. {c['text']}")
    text = _text(lines[: ac[0]] + [""] + items + [""] + lines[ac[1] :]) if ac else block["text"]
    return {**block, "criteria": kept, "ac_selected": sorted(numbers), "text": text}


def _applies(row: dict, ids: set[str]) -> bool:
    """An NFR row applies unless its text names FR ids and none of them is requested."""
    named = set(FR_IN_TEXT_RE.findall(row["applies_to"]))
    return not named or bool(named & ids)


def select_blocks(frd: dict, ids: list[str]) -> dict:
    """The FR blocks for `ids`, their Section 5 screens and the Section 8.1 rows that apply, plus selection problems."""
    problems: list[dict] = []
    wanted: dict[str, set[int] | None] = {}
    for spec in ids:
        fr_id, numbers, error = _parse_id(spec)
        if error:
            problems.append({"code": "FR_ID_MALFORMED", "detail": error})
        elif fr_id in wanted and wanted[fr_id] is None:
            continue  # the whole FR is already asked for
        elif numbers is None:
            wanted[fr_id] = None
        else:
            wanted[fr_id] = (wanted.get(fr_id) or set()) | numbers

    feature_key = frd["feature"].replace("-", "")
    blocks: list[dict] = []
    for fr_id, numbers in wanted.items():
        found = [b for b in frd["blocks"] if b["id"] == fr_id]
        if feature_key and FR_ID_RE.match(fr_id).group(1) != feature_key:
            problems.append(
                {"code": "FR_OTHER_FEATURE", "detail": f"{fr_id} belongs to another feature, not {frd['feature']}"}
            )
        elif not found:
            problems.append({"code": "FR_UNKNOWN", "detail": f"{fr_id} is not in this FRD"})
        elif len(found) > 1:
            problems.append({"code": "FR_DUPLICATE", "detail": f"{fr_id} is the heading of more than one block"})
        elif found[0]["withdrawn"]:
            problems.append({"code": "FR_WITHDRAWN", "detail": f"{fr_id} is withdrawn"})
        else:
            block = {**found[0], "ac_selected": None}
            if numbers is not None:
                existing = {_top(c) for c in block["criteria"]}
                missing = sorted(numbers - existing)
                if missing:
                    listed = ", ".join(str(n) for n in missing[:10]) + (", ..." if len(missing) > 10 else "")
                    problems.append(
                        {"code": "FR_AC_UNKNOWN", "detail": f"{fr_id} has no acceptance criterion {listed}"}
                    )
                block = _only(block, numbers & existing)
            blocks.append(block)

    screens = {s["id"]: s for s in frd["screens"]}
    wanted_screens: list[dict] = []
    for block in blocks:
        for screen_id in block["screen_ids"]:
            if screen_id not in screens:
                problems.append(
                    {
                        "code": "FR_SCREEN_UNKNOWN",
                        "detail": f"{block['id']} names Section {screen_id}, which is missing",
                    }
                )
            elif screens[screen_id] not in wanted_screens:
                wanted_screens.append(screens[screen_id])
    chosen = {b["id"] for b in blocks}
    return {
        "blocks": blocks,
        "screens": wanted_screens,
        "nfrs": [row for row in frd["nfrs"] if _applies(row, chosen)],
        "problems": problems,
    }


# --- CLI -------------------------------------------------------------------------------------------


def report(path: str, frd: dict | None, picked: dict, problems: list[dict]) -> dict:
    return {
        "result": "problems" if problems else "ok",
        "frd": path,
        "feature": frd["feature"] if frd else "",
        "frd_id": frd["frd_id"] if frd else "",
        "blocks": picked["blocks"],
        "screens": picked["screens"],
        "nfrs": picked["nfrs"],
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read a feature FRD: FR blocks, Section 5 screens, Section 8.1 NFRs.")
    parser.add_argument("--frd", required=True, help="the feature's frd.md")
    parser.add_argument("--ids", help="FR ids, e.g. FR-F001-02,FR-F001-04#1-4 (default: every FR)")
    parser.add_argument("--json", action="store_true", help="accepted for callers that pass it; JSON is always printed")
    args = parser.parse_args(argv)

    try:
        text = Path(args.frd).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        problem = {"code": "FRD_UNREADABLE", "detail": f"cannot read the FRD ({type(err).__name__})"}
        print(json.dumps(report(args.frd, None, {"blocks": [], "screens": [], "nfrs": []}, [problem])))
        return 2

    frd = parse_frd(text)
    if args.ids is None:
        picked = {"blocks": frd["blocks"], "screens": frd["screens"], "nfrs": frd["nfrs"], "problems": []}
    else:
        picked = select_blocks(frd, split_ids(args.ids))
    print(json.dumps(report(args.frd, frd, picked, frd["problems"] + picked["problems"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
