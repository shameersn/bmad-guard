# /// script
# requires-python = ">=3.10"
# ///
"""What changed in a Markdown document, as far as approval is concerned (Phase 2, T3). Pure text logic, no I/O.

`document_guard.py` calls `compare(old, new)` with the document text before and after an AI edit and gets the reason
codes to block with:

  DOC_APPROVAL_FIELD    a frontmatter line `status: approved`, `approval_status: approved`, `approved_by:` or
                        `approved_at:` was added, removed or changed, or a resolution record was: a `Resolved:` line,
                        or an `[ASSUMPTION-RESOLVED ...]` tag. Any other value of `status` / `approval_status`
                        (`draft`, `in-progress`, `pending-approval`) is an ordinary edit. Also `gate: generic`
                        added, removed or changed: it makes a story skip the ready-for-dev gate (ADR 0004), so a
                        person sets it, not the agent (T4 review).
  DOC_QUESTION_REMOVED  an open question (`Q-n` under `## Open questions`) or an open `[ASSUMPTION ...]` tag is gone
                        from the new text, and the new text has no record for it (as the ready-for-dev markup defines
                        it: the id is still there, resolved).

The question and assumption markup is parsed by the ready-for-dev gate (`ready_for_dev.py`, the sibling skill), so
the two cannot disagree about what an open question is. Editing other text, rewording an item that stays open and
adding items are not changes this module reports.

Whole-document mode (`exact=True`): both texts are complete documents; only the frontmatter counts for the approval
fields. Fragment mode (`exact=False`): the texts are the `old_string` and `new_string` of an edit that could not be
applied to the file, so every line is looked at and the question check is skipped (no section headings to go by).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections import Counter
from pathlib import Path

APPROVED = "approved"
STATE_KEYS = ("status", "approval_status")  # only the value `approved` matters
RECORD_KEYS = ("approved_by", "approved_at")  # any change matters
GENERIC = "generic"
GATE_KEYS = ("gate",)  # only the value `generic` matters: it skips the ready-for-dev gate
FIELD_RE = re.compile(r"^(?P<quote>[\"']?)(?P<key>[A-Za-z_][\w-]*)(?P=quote)[ \t]*:(?P<value>.*)$")  # key may be quoted
COMMENT_RE = re.compile(r"\s+#.*$")
FENCE_END_RE = re.compile(r"^(---|\.\.\.)[ \t]*$")
MAYBE_RECORDS_RE = re.compile(r"resolved|assumption|open questions", re.IGNORECASE)


def ready_for_dev():
    """The ready-for-dev gate's parser, from the sibling skill folder. Raises ImportError when it is not installed."""
    if "ready_for_dev" in sys.modules:
        return sys.modules["ready_for_dev"]
    path = Path(__file__).resolve().parents[2] / "ready-for-dev-gate" / "scripts" / "ready_for_dev.py"
    if not path.is_file():
        raise ImportError("the ready-for-dev gate (ready-for-dev-gate skill) is not installed")
    spec = importlib.util.spec_from_file_location("ready_for_dev", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ready_for_dev"] = module
    spec.loader.exec_module(module)
    return module


def split_frontmatter(text: str) -> tuple[list[str], str]:
    """(frontmatter lines, body). No frontmatter: ([], text). A block that never closes counts as all frontmatter,
    so a half-written block cannot hide a line."""
    text = text.lstrip("﻿").replace("\r\n", "\n")
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return [], text
    for i in range(1, len(lines)):
        if FENCE_END_RE.match(lines[i]):
            return lines[1:i], "\n".join(lines[i + 1 :])
    return lines[1:], ""


def field_values(lines: list[str]) -> dict[str, list[str]]:
    """key -> every value written for it (lower case, no quotes, no trailing comment), for the approval keys only."""
    found: dict[str, list[str]] = {}
    for line in lines:
        match = FIELD_RE.match(line)
        if not match:
            continue
        key = match.group("key").lower()
        if key in STATE_KEYS or key in RECORD_KEYS or key in GATE_KEYS:
            value = COMMENT_RE.sub("", match.group("value")).strip().strip("'\"").strip().lower()
            found.setdefault(key, []).append(value)
    return found


def fields_changed(old_lines: list[str], new_lines: list[str]) -> bool:
    old, new = field_values(old_lines), field_values(new_lines)
    for key in RECORD_KEYS:
        if sorted(old.get(key, [])) != sorted(new.get(key, [])):
            return True
    for key in STATE_KEYS:
        if [v for v in old.get(key, []) if v == APPROVED] != [v for v in new.get(key, []) if v == APPROVED]:
            return True
    for key in GATE_KEYS:
        if [v for v in old.get(key, []) if v == GENERIC] != [v for v in new.get(key, []) if v == GENERIC]:
            return True
    return False


def _squash(text: str) -> str:
    return " ".join(text.split())


def resolution_records(body: str) -> Counter:
    """Every `Resolved:` line and `[ASSUMPTION-RESOLVED ...]` tag, whitespace squashed, with how often it appears."""
    if not MAYBE_RECORDS_RE.search(body):
        return Counter()
    rfd = ready_for_dev()
    found: Counter = Counter()
    for line in body.splitlines():
        if rfd.RESOLVED_RE.match(line):
            found[_squash(line)] += 1
    for match in rfd.ASSUMPTION_RE.finditer(body):
        if match.group("resolved"):
            found[_squash(match.group(0))] += 1
    return found


def open_items(body: str) -> tuple[set[str], int, set[str]]:
    """(ids of open items, count of open items without an id, ids of all items) for questions and assumptions."""
    if not MAYBE_RECORDS_RE.search(body):
        return set(), 0, set()
    rfd = ready_for_dev()
    questions = rfd.parse_questions(body)
    assumptions, malformed = rfd.parse_assumptions(body)
    items = questions + assumptions
    open_ids = {i["id"] for i in items if i["id"] and not i["resolved"]}
    no_id = sum(1 for i in items if not i["id"] and not i["resolved"]) + malformed
    return open_ids, no_id, {i["id"] for i in items if i["id"]}


def items_removed(old_body: str, new_body: str) -> bool:
    """True when an open question or assumption of the old text is gone from the new text and has no record there.

    An id that is still in the new text (open, reworded, or resolved) is not removed. An item with no id cannot be
    followed, so only the count is compared; a new id makes up for one id-less item (the item got its id).
    """
    old_open, old_no_id, old_all = open_items(old_body)
    if not old_open and not old_no_id:
        return False
    _, new_no_id, new_all = open_items(new_body)
    if old_open - new_all:
        return True
    return old_no_id > new_no_id + len(new_all - old_all)


def compare(old: str, new: str, exact: bool = True) -> list[str]:
    """Reason codes (DOC_APPROVAL_FIELD, DOC_QUESTION_REMOVED) for an edit from `old` to `new`; [] means allow."""
    codes: list[str] = []
    if exact:
        old_front, old_body = split_frontmatter(old)
        new_front, new_body = split_frontmatter(new)
    else:
        old_front, old_body = old.replace("\r\n", "\n").split("\n"), old
        new_front, new_body = new.replace("\r\n", "\n").split("\n"), new
    if fields_changed(old_front, new_front) or resolution_records(old_body) != resolution_records(new_body):
        codes.append("DOC_APPROVAL_FIELD")
    if exact and items_removed(old_body, new_body):
        codes.append("DOC_QUESTION_REMOVED")
    return codes
