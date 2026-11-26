# /// script
# requires-python = ">=3.10"
# ///
"""Document guard for the file-writing tools (Phase 2, T3): approval, guard records and guard config. Fails closed.

Checked on Write, Edit, MultiEdit and NotebookEdit (`file_path` / `notebook_path`). The reason code is the rule id:

  DOC_APPROVAL_STATE    the approval-state.json file (any folder). Only Warden writes it, through its script.
  DOC_GUARD_RECORD      _bmad-output/guard/**: the review and build records. Only guard scripts and hooks write them.
  DOC_GUARD_CONFIG      _bmad/custom/project/guard/**: the project's guard rules and tech-stack list. The developer
                        owns them; an agent that can edit them can widen what it is allowed to do.
  DOC_GUARD_SCRIPTS     the guard itself: _bmad/custom/guard/** (git hooks, manifest), .claude/settings.json and
                        settings.local.json (hook wiring), the guard skills in .claude/skills and .agents/skills,
                        and every file the setup script installed (its manifest). An agent that edits them can turn
                        the guard off. The developer changes them, or re-runs the setup script.
  DOC_APPROVAL_FIELD    a Markdown file: an AI edit adds, removes or changes `status: approved`, `approval_status:
                        approved`, `approved_by`, `approved_at`, a `Resolved:` line or an `[ASSUMPTION-RESOLVED ...]`
                        tag. People and Warden write those.
  DOC_QUESTION_REMOVED  a Markdown file: an open question or `[ASSUMPTION ...]` is gone from the text and has no
                        resolution record. What counts as open or resolved is the ready-for-dev markup.

The Markdown rules apply to every `.md` and `.markdown` file in the project, not only to a list of folders: BMAD's
output folders can be renamed, and the checked lines are narrow (see document_text.py). An edit elsewhere in an
approved document is allowed; Sentry's hash catches it later.

How the text is compared: Write compares the new content with the file on disk (a new file compares with nothing).
Edit applies old_string and new_string to the file on disk (replace_all included) and compares the whole old and new
text; MultiEdit applies its edits one after the other. If the file is missing, or an old_string is not in it, Claude
Code fails the edit by itself and the guard has nothing to protect, so it allows. One exception: when the file exists
but the old_string is not found as typed (Claude Code also matches curly quotes and line endings loosely), the
guard compares the old_string and new_string lines on their own, so a changed approval line still blocks.

The event and the message carry the rule id, never the path or the text.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import document_text as dt  # noqa: E402 - sibling modules, found through the line above
import hook_common as hc  # noqa: E402
import winpaths as wp  # noqa: E402

GUARD = "document-guard"
TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
PATH_KEY = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
MARKDOWN = (".md", ".markdown")
STATE_FILE_NAME = "approval-state.json"
GUARD_DIR_REL = "_bmad/custom/guard"
MANIFEST_REL = f"{GUARD_DIR_REL}/manifest.json"  # written by scripts/setup_guard.py: {"files": {path: sha256}}
SETTINGS_FILES = (".claude/settings.json", ".claude/settings.local.json")
SKILL_DIRS = (".claude/skills", ".agents/skills")
# Protected even when the manifest is missing or unreadable.
GUARD_SKILLS = ("tool-guard", "prompt-guard", "ready-for-dev-gate", "agent-approval-grant", "agent-approval-gate-check")
REASONS = {
    "DOC_APPROVAL_STATE": (
        "approval-state.json is written only by agent-approval-grant (Warden), after the developer confirms twice. "
        "Ask the developer to approve or reopen the document with agent-approval-grant (Warden)."
    ),
    "DOC_GUARD_RECORD": (
        "Review and build records in _bmad-output/guard are written by the guard scripts and hooks only. "
        "Run the review or build step that writes them; do not write them by hand."
    ),
    "DOC_GUARD_CONFIG": (
        "Guard settings in _bmad/custom/project/guard belong to the developer. "
        "Tell the developer which rule or path you need; do not edit them."
    ),
    "DOC_GUARD_SCRIPTS": (
        "{file} is a guard file (hook script, guard skill, git hook, .claude/settings.json, guard config in "
        "_bmad/custom/). It belongs to the developer. Tell the developer what needs to change; "
        "they edit it or re-run the bmad-guard setup script."
    ),
    "DOC_APPROVAL_FIELD": (
        "This edit changes an approval field (status or approval_status 'approved', approved_by, approved_at) "
        "or a resolution record (Resolved:, [ASSUMPTION-RESOLVED ...]). "
        "Ask the developer to approve with agent-approval-grant (Warden), or to write the answer."
    ),
    "DOC_QUESTION_REMOVED": (
        "An open question or [ASSUMPTION ...] was removed and has no resolution record. "
        "Keep it open. A person answers it with a Resolved: line (name and date) or resolves the assumption."
    ),
}


@dataclass(frozen=True)
class Zone:
    code: str
    folder: str  # lower case, absolute, in the form of winpaths.fold()
    rel: str = ""  # project-relative, as listed (a guard file or folder): named in the message


def guard_files(root: Path) -> list[str]:
    """Project-relative files and folders that make up the guard. The manifest adds what setup installed; a missing
    or broken manifest leaves the fixed list (the folder that holds the manifest is protected itself)."""
    found = [GUARD_DIR_REL, *SETTINGS_FILES, *(f"{d}/{s}" for d in SKILL_DIRS for s in GUARD_SKILLS)]
    # The developer's override of a guard skill (e.g. prompt-guard.toml with phi = "block" in a HIPAA project).
    found += [f"_bmad/custom/{s}.toml" for s in GUARD_SKILLS]
    try:
        files = json.loads((root / MANIFEST_REL).read_text(encoding="utf-8")).get("files")
    except (OSError, ValueError, AttributeError):
        files = None
    if isinstance(files, dict):
        found += [rel for rel in files if isinstance(rel, str) and rel and not os.path.isabs(rel) and ".." not in rel]
    return list(dict.fromkeys(found))


def bad_input(tool: str) -> hc.Verdict:
    return hc.Verdict("BAD_HOOK_INPUT", f"The {tool} call has no usable path or text, so it cannot be checked.", GUARD)


class Zones:
    """The protected folders of one project and the file name of the approval state, for one working directory."""

    def __init__(self, root: Path, cwd: Path):
        self.cwd = os.path.realpath(cwd)
        self.home = os.path.realpath(Path.home())
        roots = {os.path.normpath(root), os.path.realpath(root)}
        self.roots = [wp.fold(base).lower() for base in roots]
        self.bmad = [wp.fold(os.path.join(base, "_bmad")).lower() for base in roots]  # where approval-state.json lives
        self.zones = [
            Zone(code, wp.fold(os.path.join(base, rel)).lower(), rel)
            for base in roots
            for code, rel in (
                ("DOC_APPROVAL_STATE", "_bmad/custom/approval-state.json"),  # so `rm -r _bmad` is seen to take it
                ("DOC_GUARD_RECORD", "_bmad-output/guard"),
                ("DOC_GUARD_CONFIG", "_bmad/custom/project/guard"),
                *(("DOC_GUARD_SCRIPTS", rel) for rel in guard_files(root)),
            )
        ]

    def expand(self, text: str, cwd: str) -> str:
        return os.path.normpath(os.path.join(cwd, wp.clean(wp.expand_home(text, self.home))))

    def paths(self, text: str, cwd: str | None = None) -> set[str]:
        """The path as written and with symlinks followed, lower case (macOS volumes ignore case)."""
        plain = self.expand(text.strip(), cwd or self.cwd)
        return {wp.fold(plain).lower(), wp.fold(os.path.realpath(plain)).lower()}

    def in_bmad(self, text: str, cwd: str | None = None) -> bool:
        """True when the path is the project's _bmad folder or inside it."""
        return any(p == b or p.startswith(b + os.path.sep) for p in self.paths(text, cwd) for b in self.bmad)

    def hits(self, text: str, cwd: str | None = None, ancestors: bool = False) -> list[str]:
        """Reason codes for a path. `ancestors`: a folder that holds a zone counts too (for delete and move), when it
        is inside the project: `rm -rf .` and `rm -rf /` are the command guard's."""
        return list(dict.fromkeys(code for code, _ in self.hit_zones(text, cwd, ancestors)))

    def hit_zones(self, text: str, cwd: str | None = None, ancestors: bool = False) -> list[tuple[str, str]]:
        """(reason code, zone path) for a path; the zone path is empty for the approval state file name."""
        if not text.strip():
            return []
        found: list[tuple[str, str]] = []
        for path in self.paths(text, cwd):
            if os.path.basename(path) == STATE_FILE_NAME:
                found.append(("DOC_APPROVAL_STATE", ""))
            for zone in self.zones:
                inside = path == zone.folder or path.startswith(zone.folder + os.path.sep)
                holds = (
                    ancestors
                    and zone.folder.startswith(path.rstrip(os.path.sep) + os.path.sep)
                    and any(path.startswith(root + os.path.sep) for root in self.roots)
                )
                if inside or holds:
                    found.append((zone.code, zone.rel))
        return list(dict.fromkeys(found))


def reasons(codes: list[str], files: list[str], guard: str) -> list[hc.Verdict]:
    """One verdict per code. A guard file hit is named in its message (up to three, the guard's own paths)."""
    named = ", ".join(list(dict.fromkeys(files))[:3]) or "A guard file"
    return [hc.Verdict(code, REASONS[code].replace("{file}", named), guard) for code in dict.fromkeys(codes)]


def _normal(text: str) -> str:
    return text.replace("\r\n", "\n")


def read_current(path: Path) -> str | None:
    """The file on disk, or None when it does not exist. Another read error counts as empty text (cautious)."""
    try:
        return _normal(path.read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        return None
    except OSError:
        return ""


def _is_text(value) -> bool:
    return isinstance(value, str)


def _apply(text: str, old: str, new: str, replace_all: bool) -> str | None:
    """The text after one edit, or None when Claude Code would fail the edit (old_string not found, or empty)."""
    if old == "" or old not in text:
        return None
    return text.replace(old, new) if replace_all else text.replace(old, new, 1)


def _edits(tool: str, tool_input: dict) -> list[tuple[str, str, bool]] | None:
    """[(old_string, new_string, replace_all)] of an Edit or MultiEdit call, None when the input is malformed."""
    raw = [tool_input] if tool == "Edit" else tool_input.get("edits")
    if not isinstance(raw, list) or not raw:
        return None
    out = []
    for item in raw:
        if not isinstance(item, dict) or not _is_text(item.get("old_string")) or not _is_text(item.get("new_string")):
            return None
        out.append((_normal(item["old_string"]), _normal(item["new_string"]), bool(item.get("replace_all"))))
    return out


def markdown_codes(tool: str, tool_input: dict, path: Path) -> list[str] | None:
    """Reason codes for an edit of a Markdown file. None when the call is malformed."""
    current = read_current(path)
    if tool == "Write":
        content = tool_input.get("content")
        if not _is_text(content):
            return None
        return dt.compare(current or "", _normal(content))
    edits = _edits(tool, tool_input)
    if edits is None:
        return None
    if current is None:
        # No file: Claude Code fails the edit, except for an edit with an empty old_string, which creates the file.
        if edits[0][0] == "":
            return dt.compare("", edits[0][1])
        return []
    text, failed = current, False
    for old, new, replace_all in edits:
        if old == "" and text == "":  # creates the content of an empty file
            text = new
            continue
        after = _apply(text, old, new, replace_all)
        if after is None:
            failed = True
            break
        text = after
    if not failed:
        return dt.compare(current, text)
    # Some old_string is not in the file as typed. Claude Code may still find it (curly quotes, line endings),
    # so compare what each edit takes out with what it puts in.
    codes: list[str] = []
    for old, new, _ in edits:
        codes += dt.compare(old, new, exact=False)
    return list(dict.fromkeys(codes))


def check(payload: dict, root: Path) -> list[hc.Verdict]:
    tool, tool_input = payload["tool_name"], payload["tool_input"]
    target = tool_input.get(PATH_KEY[tool])
    if not isinstance(target, str) or not target.strip():
        return [bad_input(tool)]
    start = wp.start_dir(payload.get("cwd"), root)
    zones = Zones(root, start)
    hit = zones.hit_zones(target)
    codes = [code for code, _ in hit]
    # On Windows `prd.md.`, `prd.md ` and `prd.md::$DATA` name the same file: look at the cleaned name.
    if not codes and tool != "NotebookEdit" and wp.clean(target).lower().endswith(MARKDOWN):
        found = markdown_codes(tool, tool_input, Path(zones.expand(target.strip(), zones.cwd)))
        if found is None:
            return [bad_input(tool)]
        codes = found
    return reasons(codes, [rel for code, rel in hit if code == "DOC_GUARD_SCRIPTS"], GUARD)


GUARDS = [hc.Guard(GUARD, TOOLS, check)]
