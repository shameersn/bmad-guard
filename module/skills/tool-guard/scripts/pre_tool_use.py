# /// script
# requires-python = ">=3.10"
# ///
"""Claude Code PreToolUse hook: route each tool call to the guards registered for that tool (Phase 2, T1).

Wired by scripts/setup_guard.py as:
    python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/tool-guard/scripts/pre_tool_use.py"

Reads the hook JSON ({"tool_name", "tool_input", "session_id", "cwd", ...}) from stdin.
  allow -> exit 0, no output.
  block -> exit 2 and one message on stderr listing every reason. Claude Code stops the tool call and shows
           the message to the model. The same exit code is used for a guard verdict and for a guard failure.
  warn  -> a guard may return a verdict with severity "warn" (T5, content scan). With no block, the call goes ahead:
           exit 0 and JSON on stdout, {"systemMessage": text, "hookSpecificOutput": {"hookEventName": "PreToolUse",
           "additionalContext": text}}. No permissionDecision is sent, so the normal permission flow still applies
           (an "allow" would skip the permission prompt). All warnings are in one message. When a block and a
           warning come together, the block message (exit 2) also lists the warnings.

Fails closed (ADR 0002): bad input, a guard that raises, a guard module that cannot be imported, or any other
error in this script blocks the call and records a GUARD_ERROR / BAD_HOOK_INPUT event. A crash that exits 1
(for example a Python syntax error) would not block by itself; the hook entry sets "onFailure": "block" for that.

Each guard is a module with `GUARDS = [Guard(...)]`, listed by name in GUARD_MODULES
(T2: command_guard, path_guard; T3: document_guard, bash_write_guard; T4: write_gate; T5: content_scan).
content_scan is the one guard that does not fail closed in every project: it catches its own errors and decides
by the HIPAA setting (see content_scan.py). The dispatcher still fails closed for every other guard.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import hook_common as hc  # noqa: E402 - sibling module, found through the line above

GUARD_MODULES: tuple[str, ...] = (
    "command_guard",
    "path_guard",
    "document_guard",
    "bash_write_guard",
    "write_gate",
)
# module names next to this file
STAGE = "tool"
AGENT = f"{hc.GUARD}/PreToolUse"
BAD_INPUT = hc.Verdict(
    "BAD_HOOK_INPUT", "The hook input was not the expected JSON, so the call cannot be checked.", "dispatcher"
)


def load_guards(names: tuple[str, ...] | None = None) -> list[hc.Guard]:
    guards: list[hc.Guard] = []
    for name in GUARD_MODULES if names is None else names:
        guards.extend(importlib.import_module(name).GUARDS)
    return guards


def run(raw: str, guards: list[hc.Guard], root: Path | None = None) -> tuple[dict, list[hc.Verdict]]:
    """Check one hook call. Returns (payload, verdicts): blocks and warnings; no verdicts means allow, only warnings
    means allow with a message. Never raises for bad input."""
    try:
        payload = hc.read_payload(raw)
    except ValueError:
        return {}, [BAD_INPUT]
    tool, tool_input = payload.get("tool_name"), payload.get("tool_input")
    event = payload.get("hook_event_name", "PreToolUse")
    if not (isinstance(tool, str) and tool and isinstance(tool_input, dict) and event == "PreToolUse"):
        return payload, [BAD_INPUT]
    root = root or hc.project_root(payload.get("cwd") or None)
    verdicts: list[hc.Verdict] = []
    for guard in guards:
        if tool in guard.tools or "*" in guard.tools:
            try:
                verdicts.extend(guard.check(payload, root))
            except Exception as err:  # a guard that cannot decide blocks
                verdicts.append(hc.Verdict("GUARD_ERROR", f"Guard failed ({type(err).__name__}).", guard.name))
    return payload, verdicts


def _line(v: hc.Verdict) -> str:
    where = f" [{v.path}]" if v.path else ""
    return f"- {v.code} ({v.guard or 'tool-guard'}){where}: {v.reason}"


def message(tool: str, verdicts: list[hc.Verdict]) -> str:
    """The block message: every blocking reason, then the warnings (if any) of the same call."""
    lines = [f"[tool-guard] Blocked {tool or 'tool call'}:"]
    lines += [_line(v) for v in hc.blocks(verdicts)]
    lines.append("Do not retry the same call. Change the approach, or ask the developer.")
    warned = hc.warnings(verdicts)
    if warned:
        lines.append("Also (warnings, not blocking):")
        lines += [_line(v) for v in warned]
    return "\n".join(lines)


def warning_message(tool: str, verdicts: list[hc.Verdict]) -> str:
    """One message for all warnings of an allowed call."""
    lines = [f"[tool-guard] Warning for {tool or 'tool call'} (the call was allowed):"]
    lines += [_line(v) for v in hc.warnings(verdicts)]
    return "\n".join(lines)


def warning_output(tool: str, verdicts: list[hc.Verdict]) -> dict:
    """The hook JSON for an allowed call with warnings: shown to the developer and added to the model's context."""
    text = warning_message(tool, verdicts)
    return {"systemMessage": text, "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


def record(payload: dict, verdicts: list[hc.Verdict]) -> None:
    root = hc.project_root(payload.get("cwd") or None)
    for v in verdicts:
        hc.emit(
            STAGE,
            hc.WARN if v.severity == hc.WARN else hc.BLOCK,
            v.code,
            root,
            AGENT,
            rule=v.guard,
            path=v.path,
            detail=v.detail,
            tool=payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else "",
            session_id=payload.get("session_id") if isinstance(payload.get("session_id"), str) else "",
        )


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    payload, verdicts = run(raw, load_guards())
    if not verdicts:
        return 0
    record(payload, verdicts)
    tool = payload.get("tool_name")
    tool = tool if isinstance(tool, str) else ""
    if hc.blocks(verdicts):
        print(message(tool, verdicts), file=sys.stderr)
        return 2
    print(json.dumps(warning_output(tool, verdicts)))
    return 0


def entry() -> int:
    try:
        return main()
    except Exception as err:  # fail closed on a bug in the guard itself
        verdict = hc.Verdict("GUARD_ERROR", f"The guard could not run ({type(err).__name__}).", "dispatcher")
        try:
            record({}, [verdict])
        except Exception:  # noqa: S110 - the block below is what matters; logging never changes a decision
            pass
        print(message("", [verdict]), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(entry())
