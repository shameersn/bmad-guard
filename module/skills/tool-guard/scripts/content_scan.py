# /// script
# requires-python = ">=3.10"
# ///
"""Content scan for what the agent writes (Phase 2, T5): secrets and PHI in the new text of a file write.

Checked on Write (`content`), Edit (`new_string`), MultiEdit (each `new_string`), NotebookEdit (`new_source`) and on
text that a Bash command writes to a file (see below). Only what the agent ADDS is scanned, never the text that is
already in the file: an Edit scans its `new_string` and not the old text around it. The rules are prompt-guard's
(`prompt_rules.py`, loaded from the sibling skill folder as a library; none are copied here), with its project
patterns and allow-listed fakes (`_bmad/custom/project/guard/prompt-patterns.json`) and its per-category actions
(`secrets`, `phi` in `prompt-guard.toml`). Injection phrasing and size are not checked on writes.

Rule ids (the reason code, in the message and in the event):
  CONTENT_SECRET  the new text holds a secret (key format, credential assignment, connection string, token).
                  Blocks (prompt-guard `secrets = "block"`, the default).
  CONTENT_PHI     the new text holds a personal or health identifier (card, NHS, Aadhaar, SSN, email, phone).
                  A project marked HIPAA (`phi = "block"`) blocks. Other projects get a WARNING: the write goes
                  ahead, the developer and the model are told, and a `warn` event is recorded.
  GUARD_ERROR     the scan itself failed. A HIPAA project blocks; other projects get a warning (fail open).
                  This is the one guard that does not fail closed everywhere; the dispatcher still does for all others.

A call with no text field, or a field that is not text, has nothing to scan and passes (Claude Code rejects such a
call itself; the path guards report a call with no usable path).

HIPAA project: `prompt_check.is_hipaa()`, i.e. `phi = "block"` in the prompt-guard config. If that setting cannot be
read (an unreadable file, an invalid encoding, prompt-guard missing), the project counts as HIPAA.

Bash: a command can write a secret without a file tool (`cat > f <<EOF`, `echo KEY > f`, `printf ... | tee f`).
The guard scans the literal text that a command writes to a file. That is: a here-document or here-string, text piped
from echo or printf, and the arguments of echo or printf, when the same command writes to a file (a redirect or
`tee`). Writes to /dev/null, /dev/stderr and the like do not count. A command that only reads, or that only names a
value in `grep`, `curl` or `git log`, is not scanned. Not seen: `python3 -c`, a script that writes the file itself,
`sed -i` replacements, a value built from variables, text that `cat file > other` copies.

The event and the message carry the rule id, the rule ids of the findings and the tool name; never the matched
text, the file content or the path.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import bash_write_guard as bwg  # noqa: E402 - sibling modules, found through the line above
import hook_common as hc  # noqa: E402
import shell_parse as sp  # noqa: E402

GUARD = "content-scan"
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
TOOLS = (*FILE_TOOLS, "Bash")
CATEGORY_CODES = {"secrets": "CONTENT_SECRET", "phi": "CONTENT_PHI"}
CATEGORY_NAMES = {"secrets": "secrets", "phi": "personal or health identifiers"}
TEXT_COMMANDS = ("echo", "printf")
NULL_DEVICES = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/zero")
SKILL = "prompt-guard"


def _prompt_check():
    """prompt_check.py from the sibling skill folder (it loads prompt_rules.py itself). Raises when it is missing."""
    if "prompt_check" in sys.modules:
        return sys.modules["prompt_check"]
    path = Path(__file__).resolve().parents[2] / SKILL / "scripts" / "prompt_check.py"
    spec = importlib.util.spec_from_file_location("prompt_check", path) if path.is_file() else None
    if spec is None or spec.loader is None:
        raise ImportError("prompt-guard is not installed")
    module = importlib.util.module_from_spec(spec)
    sys.modules["prompt_check"] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop("prompt_check", None)
        raise
    return module


# --- what was written -------------------------------------------------------------------------------------------


def file_texts(tool: str, tool_input: dict) -> list[str]:
    """The new text of a file tool call. A field that is missing or not text adds nothing: Claude Code rejects such a
    call itself, and the path guards report a call with no usable path."""
    if tool == "Write":
        found = [tool_input.get("content")]
    elif tool == "Edit":
        found = [tool_input.get("new_string")]
    elif tool == "MultiEdit":
        edits = tool_input.get("edits")
        found = [e.get("new_string") for e in edits if isinstance(e, dict)] if isinstance(edits, list) else []
    elif tool == "NotebookEdit":
        found = [tool_input.get("new_source")]  # a deleted cell has none
    else:
        found = []
    return [text for text in found if isinstance(text, str) and text]


def _writes_to_file(inv: sp.Invocation, cwd: str) -> bool:
    """True when the command writes to a file other than a null device or a terminal."""
    for word, _ in bwg.targets(inv, cwd):
        if word.text in NULL_DEVICES:
            continue
        return True
    return False


def bash_texts(command: str, cwd: str) -> list[str]:
    """Literal text a command writes to a file. A command that cannot be parsed has none (command_guard blocks it)."""
    try:
        invocations = list(sp.walk(command))
    except sp.ParseError:
        return []
    found: list[str] = []
    for inv in invocations:
        if not _writes_to_file(inv, cwd):
            continue
        found += [text for text in inv.stdin_texts if text]
        if inv.name in TEXT_COMMANDS:
            found.append(" ".join(word.text for word in inv.args))
    return found


def texts(payload: dict) -> list[str]:
    tool, tool_input = payload["tool_name"], payload["tool_input"]
    if tool in FILE_TOOLS:
        return file_texts(tool, tool_input)
    command = tool_input.get("command")
    if not isinstance(command, str):
        return []  # command_guard reports a call with no command text
    cwd = payload.get("cwd")
    return bash_texts(command, cwd if isinstance(cwd, str) and os.path.isabs(cwd) else os.getcwd())


# --- decision ---------------------------------------------------------------------------------------------------


def _join(ids: list[str]) -> str:
    return ", ".join(dict.fromkeys(ids))


def verdicts_for(found: dict[str, list[str]], config: dict) -> list[hc.Verdict]:
    """One verdict per category with findings, by prompt-guard's action for it: block or warn. `record` and `off` say
    nothing here."""
    out: list[hc.Verdict] = []
    for category, code in CATEGORY_CODES.items():
        ids = found.get(category)
        action = config[category]
        if not ids or action not in (hc.BLOCK, hc.WARN):
            continue
        name, listed = CATEGORY_NAMES[category], _join(ids)
        if action == hc.BLOCK and category == "phi":
            reason = (
                f"The text you are writing holds {name} ({listed}), and this project is marked HIPAA (prompt-guard "
                "phi = block). Nothing was written. Use obviously fake data, or ask the developer."
            )
        elif action == hc.BLOCK:
            reason = (
                f"The text you are writing holds {name} ({listed}). Nothing was written. Keep the value out of files: "
                "read it from an environment variable, or use an obvious fake (for example the word example)."
            )
        else:
            reason = (
                f"The text you are writing may hold {name} ({listed}). The write was allowed. Use obviously fake "
                "data, and do not copy real values into files, logs or commits."
            )
        out.append(hc.Verdict(code, reason, GUARD, severity=action, detail=listed))
    return out


def scan(payload: dict, root: Path) -> list[hc.Verdict]:
    """The verdicts for one call. Raises when the scan fails; `check` turns that into a GUARD_ERROR verdict."""
    parts = texts(payload)
    if not parts:
        return []
    pc = _prompt_check()
    config, _ = pc.load_config(root)
    result, config_warnings = pc.check("\n".join(parts), root)
    found: dict[str, list[str]] = {}
    for finding in result["findings"]:
        found.setdefault(finding["category"], []).append(finding["rule_id"])
    verdicts = verdicts_for(found, config)
    if config_warnings:  # a bad config file is told once something is found
        note = f" Config warnings: {'; '.join(config_warnings)}."
        verdicts = [replace(v, reason=v.reason + note) for v in verdicts]
    return verdicts


def failure(err: Exception, root: Path) -> hc.Verdict:
    """The verdict for a scan that crashed: block in a HIPAA project (also when that cannot be told), else warn."""
    try:
        hipaa = _prompt_check().is_hipaa(root)
    except Exception:  # cannot tell: treat as HIPAA
        hipaa = True
    name = type(err).__name__
    if hipaa:
        reason = f"The content scan failed ({name}) and this project is marked HIPAA, so the write is blocked."
        return hc.Verdict("GUARD_ERROR", reason, GUARD, severity=hc.BLOCK, detail=name)
    reason = f"The content scan failed ({name}); this write was NOT checked for secrets or PHI. The write was allowed."
    return hc.Verdict("GUARD_ERROR", reason, GUARD, severity=hc.WARN, detail=name)


def check(payload: dict, root: Path) -> list[hc.Verdict]:
    try:
        return scan(payload, root)
    except Exception as err:  # this guard decides its own fail mode: it never lets the dispatcher block for it
        return [failure(err, root)]


GUARDS = [hc.Guard(GUARD, TOOLS, check)]
