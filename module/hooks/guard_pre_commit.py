# /// script
# requires-python = ">=3.10"
# ///
"""The pre-commit checks (Phase 2, T6). `pre-commit` is a thin launcher; this module holds the logic.

Three checks run on what is STAGED, never on the work tree. A block exits 1 with one message that lists every reason;
a warning exits 0 with a message on stderr. Any failure of the hook itself blocks (fail closed).

1. Scan (the T5 rules). The lines this commit ADDS (`git diff --cached`, so only the staged version) go through
   prompt-guard's `check()`: its rules, its project patterns and allow-listed fakes, its actions `secrets` and `phi`.
   Binary files (as git sees them) and deleted files are skipped, and so are lock files, which are generated hash
   lists. At most 1 MiB of added text per file is read; the rest is not scanned and the hook warns.
     COMMIT_SECRET   secrets in added lines. Blocks (prompt-guard `secrets = "block"`).
     COMMIT_PHI      personal or health identifiers. Blocks in a HIPAA project (`phi = "block"`), warns elsewhere.
     COMMIT_SCAN_PARTIAL   a file had more than 1 MiB of added text; only the first MiB was read. Warns.
     COMMIT_SCAN_CONFIG    prompt-guard's config or patterns file has a problem; the built-in rules still ran. Warns.

2. Approval fields (the port of revert-brief-status.js; it blocks, it never changes a file). For every staged
   Markdown file (and the tech-stack list), the staged text is compared with HEAD:
     COMMIT_APPROVAL_NO_ENTRY  `status` / `approval_status` is `approved`, or `approved_by` / `approved_at` changed,
                               and `approval-state.json` (the staged one) has no Warden entry for this file that is
                               still in force. Blocks.
     COMMIT_APPROVAL_STALE     the file is approved and Warden has an entry for it, but the staged text does not hash
                               to the recorded `approved_hash` (edited after approval). Blocks.
     COMMIT_STATE_UNREADABLE   approval-state.json is not valid JSON. Blocks.
     COMMIT_GATE_GENERIC       `gate: generic` was added, removed or changed. Warns: a person sets it, and the hook
                               cannot tell a person from the agent.
     COMMIT_QUESTION_REMOVED   an open question or assumption is gone and has no resolution record. Warns.
     COMMIT_RESOLUTION_CHANGED a `Resolved:` line or `[ASSUMPTION-RESOLVED ...]` tag changed. Event only, no message
                               (a person writes these and commits them all the time).
   An entry matches when its `doc_path` is the file, it is not superseded, and the hash of the staged text (full, or
   Warden's `build` mode) equals `approved_hash`. Status going from approved to something else is not checked: it
   removes approval, and nothing downstream accepts a document that is not approved.

3. Tech stack (optional). With `_bmad/custom/project/guard/tech-stack.toml` in the commit (see guard_tech_stack.py), a
   dependency that this commit ADDS to package.json, requirements*.txt or pyproject.toml must be on the list.
     COMMIT_TECH_STACK_NOT_ALLOWED  a new dependency is not on the list. Blocks.
     COMMIT_TECH_STACK_UNAPPROVED   a dependency was added but the list has no matching Warden entry (never approved,
                                    edited since, or reopened). Blocks: the list could be edited in the same commit.
     COMMIT_TECH_STACK_BAD_FILE     the list, or a dependency file, cannot be read. Blocks.
     COMMIT_TECH_STACK_REMOVED      the commit deletes a list that is approved and in force. Blocks: reopen it first.
     COMMIT_DEPENDENCY_UNCHECKED    go.mod, Cargo.toml, ... changed, or the list does not govern that ecosystem. Warns.
     COMMIT_NO_TECH_STACK           no list, and a dependency file changed. Event only, no message.

Other ids: COMMIT_ERROR (the hook failed: blocks), COMMIT_ALLOWED and COMMIT_NOTHING_TO_CHECK (events only).

Events go to the local queue only (guard_hook_lib.emit). They hold the rule id, the check, the staged file count and a
detail (rule ids or an error type). The approval events also carry the file path and the SHA-256 of the staged file.
Every event also carries `tree`: the id of the staged tree (`git write-tree`). The commit does not exist yet, but the
commit that follows holds exactly this tree, so the "commits with no guard events" report (deploy/collector/reports)
matches a commit to its pre-commit event through it (ADR 0003, update of 2026-11-26). A tree id is a hash; it holds
no content. Never file content, matched text or the paths of scanned files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import guard_hook_lib as lib  # noqa: E402 - sibling modules, found through the line above
import guard_tech_stack as ts  # noqa: E402

AGENT = "git-hooks/pre-commit"
STAGE = "pre-commit"
BLOCK, WARN, INFO = "block", "warn", "info"
MAX_SCAN_BYTES = 1024 * 1024
STATE_REL = "_bmad/custom/approval-state.json"
LIST_REL = "_bmad/custom/project/guard/tech-stack.toml"
MARKDOWN = (".md", ".markdown")
LOCK_FILES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lock",
    "poetry.lock",
    "pdm.lock",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
    "Gemfile.lock",
    "packages.lock.json",
}
SCAN_CODES = {"secrets": "COMMIT_SECRET", "phi": "COMMIT_PHI"}
SCAN_NAMES = {"secrets": "secrets", "phi": "personal or health identifiers"}
LISTED = 8  # file names shown in one message line


@dataclass
class Reason:
    code: str
    text: str = ""  # the message line; empty for an event-only reason
    severity: str = BLOCK
    rule: str = ""  # the check: scan, approval or tech-stack
    detail: str = ""  # rule ids or an error type, for the event only
    targets: list[tuple[str, str]] = field(default_factory=list)  # (path, SHA-256), one event each


def listing(names: list[str]) -> str:
    shown = ", ".join(names[:LISTED])
    return shown + (f" (+{len(names) - LISTED} more)" if len(names) > LISTED else "")


# --- what is staged ----------------------------------------------------------------------------------------------


def staged_paths(root: Path) -> list[str]:
    """Added, changed and type-changed files in the index, not deleted ones. A rename is a delete and an add."""
    out = lib.git(root, "diff", "--cached", "--name-only", "-z", "--no-renames", "--diff-filter=d")
    return [os.fsdecode(p) for p in out.split(b"\0") if p]


def added_text(root: Path, paths: list[str]) -> dict[str, tuple[bytes, bool]]:
    """path -> (the lines this commit adds to it, joined; True when git sees the file as binary). One git call."""
    patch = lib.git(
        root,
        "diff",
        "--cached",
        "-U0",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--no-renames",
        "--submodule=short",
        "--diff-filter=d",
    )
    sections = re.split(rb"(?m)^diff --git ", patch)[1:]
    if len(sections) != len(paths):
        raise lib.HookError("the staged diff does not match the list of staged files")
    found: dict[str, tuple[bytes, bool]] = {}
    for path, section in zip(paths, sections, strict=True):
        header, _, rest = section.partition(b"\n")
        if os.fsencode(path) not in header and not header.startswith(b'"'):
            raise lib.HookError("the staged diff is not in the order of the list of staged files")
        added: list[bytes] = []
        binary = in_hunk = False
        for line in rest.split(b"\n"):
            if in_hunk:
                if line.startswith(b"+"):
                    added.append(line[1:])
            elif line.startswith(b"@@"):
                in_hunk = True
            elif line.startswith((b"Binary files ", b"GIT binary patch")):
                binary = True
        found[path] = (b"\n".join(added), binary)
    return found


# --- 1. scan -----------------------------------------------------------------------------------------------------


def scan_text(category: str, action: str, where: str) -> str:
    """The message line for a finding. `where` lists the files and rule ids, never the matched text."""
    name = SCAN_NAMES[category]
    if action == "record":
        return ""  # an event only
    if action == WARN:
        return (
            f"the lines this commit adds may hold {name}: {where}. This is a warning, not a block. "
            "Use obviously fake data, and do not commit real values."
        )
    if category == "phi":
        return (
            f"{name} in the lines this commit adds: {where}. This project is marked HIPAA (prompt-guard phi = block). "
            "Remove the data, or use obviously fake values."
        )
    return (
        f"{name} in the lines this commit adds: {where}. Remove the value (read it from an environment variable), or "
        "use an obvious fake such as the word example. If it is a fake, add it to the allow list in "
        "_bmad/custom/project/guard/prompt-patterns.json."
    )


def scan(root: Path, paths: list[str], added: dict[str, tuple[bytes, bool]]) -> list[Reason]:
    pc = lib.load_skill_script(root, "prompt-guard", "prompt_check.py")
    config, _ = pc.load_config(root)
    found: dict[str, list[tuple[str, list[str]]]] = {"secrets": [], "phi": []}
    partial: list[str] = []
    notes: list[str] = []
    for path in paths:
        data, binary = added[path]
        if binary or not data.strip() or path.rsplit("/", 1)[-1] in LOCK_FILES:
            continue
        if len(data) > MAX_SCAN_BYTES:
            data = data[:MAX_SCAN_BYTES]
            partial.append(path)
        result, config_notes = pc.check(data.decode("utf-8", errors="replace"), root)
        notes += [n for n in config_notes if n not in notes]
        for category in found:
            ids = [f["rule_id"] for f in result["findings"] if f["category"] == category]
            if ids:
                found[category].append((path, ids))

    reasons: list[Reason] = []
    for category, hits in found.items():
        action = config[category]
        if not hits or action not in (BLOCK, WARN, "record"):
            continue
        ids = sorted({i for _, listed in hits for i in listed})
        where = listing([f"{path} ({', '.join(dict.fromkeys(listed))})" for path, listed in hits])
        text = scan_text(category, action, where)
        severity = INFO if action == "record" else action
        reasons.append(Reason(SCAN_CODES[category], text, severity, "scan", ", ".join(ids)))
    if partial:
        reasons.append(
            Reason(
                "COMMIT_SCAN_PARTIAL",
                f"more than {MAX_SCAN_BYTES // 1024 // 1024} MiB was added to {listing(partial)}; only the first MiB "
                "was scanned. Check the rest yourself.",
                WARN,
                "scan",
            )
        )
    if notes:
        reasons.append(
            Reason(
                "COMMIT_SCAN_CONFIG",
                f"prompt-guard config problem: {'; '.join(notes)}. The built-in rules still ran.",
                WARN,
                "scan",
            )
        )
    return reasons


# --- 2. approval fields ------------------------------------------------------------------------------------------


class Approvals:
    """Warden's entries, from the STAGED approval-state.json. Read once, and only when a check needs it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.core = None
        self.entries: dict | None = None
        self.loaded = False

    def load(self) -> dict | None:
        """The state, {} when the file does not exist, None when it cannot be read."""
        if not self.loaded:
            self.loaded = True
            self.core = lib.load_skill_script(self.root, "agent-approval-grant", "approval_core.py")
            data = lib.index_blob(self.root, STATE_REL)
            try:
                state = {} if data is None else json.loads(data.decode("utf-8"))
            except ValueError:  # includes UnicodeDecodeError
                state = None
            self.entries = state if isinstance(state, dict) else None
        return self.entries

    def status(self, rel: str, text: str) -> str:
        """ok, stale (edited since approval), superseded (reopened), none, or unreadable (the state file is broken)."""
        state = self.load()
        if state is None:
            return "unreadable"
        names = (rel, str(Path(os.path.realpath(self.root)) / rel))  # Warden may have recorded an absolute path
        seen = "none"
        for entry in state.values():
            if not isinstance(entry, dict) or entry.get("doc_path") not in names:
                continue
            if entry.get("superseded"):
                seen = "superseded" if seen == "none" else seen
                continue
            mode = entry.get("hash_mode", "full")
            if entry.get("approved_hash") == self.core.content_hash(text, mode if isinstance(mode, str) else "full"):
                return "ok"
            seen = "stale"
        return seen


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_documents(root: Path, paths: list[str], head: bool, approvals: Approvals) -> list[Reason]:
    candidates = [p for p in paths if p.lower().endswith(MARKDOWN) or p == LIST_REL]
    if not candidates:
        return []
    lib.find_script(root, "ready-for-dev-gate", "ready_for_dev.py")  # document_text needs it for the question markup
    dt = lib.load_skill_script(root, "tool-guard", "document_text.py")
    no_entry: list[tuple[str, str]] = []
    stale: list[tuple[str, str]] = []
    generic: list[str] = []
    removed: list[str] = []
    resolutions: list[str] = []
    for rel in candidates:
        data = lib.index_blob(root, rel)
        if data is None:
            raise lib.HookError("a staged file is not in the index")
        new, old = lib.text_of(data), lib.text_of(lib.head_blob(root, rel, head))
        old_front, old_body = dt.split_frontmatter(old)
        new_front, new_body = dt.split_frontmatter(new)
        before, now = dt.field_values(old_front), dt.field_values(new_front)
        approved = any(v == dt.APPROVED for key in dt.STATE_KEYS for v in now.get(key, []))
        recorded = any(sorted(before.get(key, [])) != sorted(now.get(key, [])) for key in dt.RECORD_KEYS)
        if approved or recorded:
            state = approvals.status(rel, new)  # "unreadable" is reported once, by run_checks
            if state == "stale":
                stale.append((rel, sha256(data)))
            elif state not in ("ok", "unreadable"):
                no_entry.append((rel, sha256(data)))
        if rel == LIST_REL:
            continue
        gate = [v for v in before.get("gate", []) if v == dt.GENERIC] != [
            v for v in now.get("gate", []) if v == dt.GENERIC
        ]
        if gate:
            generic.append(rel)
        if dt.resolution_records(old_body) != dt.resolution_records(new_body):
            resolutions.append(rel)
        if dt.items_removed(old_body, new_body):
            removed.append(rel)

    reasons: list[Reason] = []
    if no_entry:
        reasons.append(
            Reason(
                "COMMIT_APPROVAL_NO_ENTRY",
                f"{listing([p for p, _ in no_entry])}: the approval status is approved (or approved_by / approved_at "
                f"changed), but {STATE_REL} has no matching Warden approval for it that is still in force. Only Warden "
                "(agent-approval-grant, Grant Approval) sets these fields, after the developer confirms. Undo the "
                "change, or ask the developer to run Warden on the document.",
                BLOCK,
                "approval",
                targets=no_entry,
            )
        )
    if stale:
        reasons.append(
            Reason(
                "COMMIT_APPROVAL_STALE",
                f"{listing([p for p, _ in stale])}: approved, but the text differs from what Warden recorded, so it "
                "was edited after approval. Undo the edit, or ask the developer to reopen the document with Warden "
                "(Reopen Document) and approve it again.",
                BLOCK,
                "approval",
                targets=stale,
            )
        )
    if generic:
        reasons.append(
            Reason(
                "COMMIT_GATE_GENERIC",
                f"gate: generic changed in {listing(generic)}. It makes a story skip the ready-for-dev check "
                "(ADR 0004), so a person sets it, not the agent. Make sure you did.",
                WARN,
                "approval",
                targets=[(p, "") for p in generic],
            )
        )
    if removed:
        reasons.append(
            Reason(
                "COMMIT_QUESTION_REMOVED",
                f"an open question or assumption is gone from {listing(removed)} and has no resolution record. "
                "Answer it with a Resolved: line instead of deleting it (docs/ready-for-dev-markup.md).",
                WARN,
                "approval",
                targets=[(p, "") for p in removed],
            )
        )
    if resolutions:
        reasons.append(Reason("COMMIT_RESOLUTION_CHANGED", "", INFO, "approval", str(len(resolutions))))
    return reasons


# --- 3. tech stack -----------------------------------------------------------------------------------------------


def check_tech_stack(root: Path, paths: list[str], head: bool, approvals: Approvals, list_gone: bool) -> list[Reason]:
    deps = [p for p in paths if ts.kind_of(p)]
    if not deps and LIST_REL not in paths and not list_gone:
        return []
    blob = lib.index_blob(root, LIST_REL)
    if blob is None:
        old = lib.head_blob(root, LIST_REL, head)
        if old is not None and approvals.status(LIST_REL, lib.text_of(old)) == "ok":
            return [
                Reason(
                    "COMMIT_TECH_STACK_REMOVED",
                    f"this commit deletes {LIST_REL}, which the architect approved. A list is retired in two steps: "
                    "reopen it with Warden (agent-approval-grant, Reopen Document) and commit that, then delete it.",
                    BLOCK,
                    "tech-stack",
                )
            ]
        return [Reason("COMMIT_NO_TECH_STACK", "", INFO, "tech-stack", str(len(deps)))] if deps else []
    text = lib.text_of(blob)
    try:
        allowed = ts.parse_list(text)
    except ts.StackError as err:
        return [Reason("COMMIT_TECH_STACK_BAD_FILE", f"{LIST_REL} cannot be used: {err}.", BLOCK, "tech-stack")]

    unchecked: list[str] = []
    bad: list[str] = []
    added: dict[str, tuple[str, list[str]]] = {}  # file -> (ecosystem, new names)
    for rel in deps:
        kind = ts.kind_of(rel)
        if kind not in allowed:  # "other" files, and an ecosystem the list does not govern
            unchecked.append(rel)
            continue
        old = lib.head_blob(root, rel, head)
        try:
            names = ts.new_dependencies(
                rel, lib.text_of(old) if old is not None else None, lib.text_of(lib.index_blob(root, rel))
            )
        except ts.StackError as err:
            bad.append(f"{rel} ({err})")
            continue
        if names:
            added[rel] = (kind, sorted(names))

    reasons: list[Reason] = []
    if bad:
        reasons.append(
            Reason(
                "COMMIT_TECH_STACK_BAD_FILE",
                f"cannot read for the tech-stack check: {'; '.join(bad)}. Fix the file.",
                BLOCK,
                "tech-stack",
            )
        )
    if added:
        state = approvals.status(LIST_REL, text)
        if state != "ok":
            why = {
                "stale": "was changed after it was approved",
                "superseded": "was reopened and not approved again",
                "unreadable": f"cannot be checked because {STATE_REL} is not valid JSON",
            }.get(state, "has not been approved")
            reasons.append(
                Reason(
                    "COMMIT_TECH_STACK_UNAPPROVED",
                    f"this commit adds dependencies ({listing(sorted(added))}), but the list {LIST_REL} {why}. Ask "
                    "the architect to approve the list with Warden (agent-approval-grant, doc type tech-stack), "
                    "then commit again.",
                    BLOCK,
                    "tech-stack",
                    ",".join(sorted({eco for eco, _ in added.values()})),
                )
            )
        else:
            outside = {
                rel: [n for n in names if not ts.is_allowed(allowed[eco], eco, n)]
                for rel, (eco, names) in added.items()
            }
            outside = {rel: names for rel, names in outside.items() if names}
            if outside:
                lines = "; ".join(f"{rel}: {', '.join(names)}" for rel, names in outside.items())
                reasons.append(
                    Reason(
                        "COMMIT_TECH_STACK_NOT_ALLOWED",
                        f"new dependencies that are not on the approved tech-stack list ({lines}). Use a package "
                        f"that is on the list, or ask the architect to add it and approve the list with Warden.",
                        BLOCK,
                        "tech-stack",
                        str(sum(len(v) for v in outside.values())),
                    )
                )
    if unchecked:
        reasons.append(
            Reason(
                "COMMIT_DEPENDENCY_UNCHECKED",
                f"these dependency files changed, but the tech-stack list does not check them: "
                f"{listing(sorted(unchecked))}. Look at the new dependencies yourself.",
                WARN,
                "tech-stack",
            )
        )
    return reasons


# --- run ---------------------------------------------------------------------------------------------------------


def list_deleted(root: Path) -> bool:
    """True when this commit deletes the tech-stack list (staged_paths does not list deleted files)."""
    out = lib.git(root, "diff", "--cached", "--name-only", "-z", "--no-renames", "--diff-filter=D", "--", LIST_REL)
    return bool(out.strip(b"\0"))


def run_checks(root: Path, paths: list[str], list_gone: bool) -> list[Reason]:
    head = lib.has_head(root)
    approvals = Approvals(root)
    reasons = scan(root, paths, added_text(root, paths))
    reasons += check_documents(root, paths, head, approvals)
    reasons += check_tech_stack(root, paths, head, approvals, list_gone)
    if approvals.loaded and approvals.entries is None:
        reasons.append(
            Reason(
                "COMMIT_STATE_UNREADABLE",
                f"{STATE_REL} is not valid JSON, so approvals cannot be checked. Restore it from git "
                f"(git show HEAD:{STATE_REL}) or ask the developer who approved the document.",
                BLOCK,
                "approval",
            )
        )
    return reasons


def render(reasons: list[Reason]) -> str:
    blocks = [r for r in reasons if r.severity == BLOCK]
    warns = [r for r in reasons if r.severity == WARN]
    lines: list[str] = []
    if blocks:
        lines.append(
            f"bmad-guard pre-commit: the commit is blocked ({len(blocks)} reason{'s' if len(blocks) > 1 else ''})."
        )
        lines += [f"- {r.code}: {r.text}" for r in blocks]
        lines.append("Fix this, stage the files again and commit again. Do not use --no-verify.")
        if warns:
            lines.append("Also (warnings):")
    elif warns:
        lines.append("bmad-guard pre-commit: warning (the commit goes ahead):")
    lines += [f"- {r.code}: {r.text}" for r in warns]
    return "\n".join(lines)


def tree_id(root: Path) -> str:
    """The id of the staged tree, which the commit will hold. Empty when git cannot say (unmerged paths, no git):
    the events then carry no tree and the report counts the commit as unseen."""
    try:
        out = lib.git(root, "write-tree").decode("ascii", errors="replace").strip()
    except lib.HookError:
        return ""
    return out if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", out) else ""


def record(root: Path, reasons: list[Reason], file_count: int, nothing: bool = False) -> None:
    """One event per reason (per file for approval reasons) and, when nothing blocks, one allow event."""
    tree = tree_id(root)
    for r in reasons:
        for path, digest in r.targets or [("", "")]:
            lib.emit(
                root,
                STAGE,
                r.severity,
                r.code,
                AGENT,
                rule=r.rule,
                detail=r.detail,
                file_count=file_count,
                target_path=path,
                target_sha256=digest,
                tree=tree,
            )
    if not any(r.severity == BLOCK for r in reasons):
        warned = sum(1 for r in reasons if r.severity == WARN)
        code = "COMMIT_NOTHING_TO_CHECK" if nothing else "COMMIT_ALLOWED"
        lib.emit(root, STAGE, "allow", code, AGENT, file_count=file_count, warnings=warned or "", tree=tree)


def main() -> int:
    try:
        root = lib.project_root()
    except lib.HookError as err:
        print(f"bmad-guard pre-commit: the commit is blocked: {err}", file=sys.stderr)
        return 1
    paths: list[str] = []
    try:
        paths = staged_paths(root)
        list_gone = list_deleted(root)
        if not paths and not list_gone:
            record(root, [], 0, nothing=True)
            return 0
        reasons = run_checks(root, paths, list_gone)
    except Exception as err:  # any failure blocks: a hook that cannot check must not wave a commit through
        cause = str(err) if isinstance(err, lib.HookError) else type(err).__name__
        reasons = [
            Reason(
                "COMMIT_ERROR",
                f"the check could not run ({cause}), so the commit is blocked. Fix the cause (for example run "
                "scripts/setup_guard.py again), then commit again.",
                BLOCK,
                "hook",
                type(err).__name__,
            )
        ]
    record(root, reasons, len(paths))
    text = render(reasons)
    if text:
        print(text, file=sys.stderr)
    return 1 if any(r.severity == BLOCK for r in reasons) else 0


if __name__ == "__main__":
    sys.exit(main())
