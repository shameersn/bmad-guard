# /// script
# requires-python = ">=3.10"
# ///
"""Claude Code UserPromptSubmit hook: check every prompt locally before Claude sees it (ADR 0006).

Wired into Claude Code settings in Phase 2 (managed settings), as:
    python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/prompt-guard/scripts/prompt_guard_hook.py"

Reads the hook JSON ({"prompt", "session_id", "cwd", ...}) from stdin and answers:
  block -> {"decision": "block", "reason": ...}: Claude never sees the prompt. The reason names the
           categories and rule ids, never the matched text.
  warn  -> the prompt goes through; a warning is shown and added to Claude's context.
  allow -> no output (record-only findings are still recorded as an event).

Decisions fail closed: a "block" blocks. An internal error in the guard itself fails OPEN: the prompt
goes through with a warning, and a GUARD_ERROR event records that it was not checked. Reason: a bug in
this script must not stop all work in Claude Code; the error is visible to the user and in the events.
(Review question for the team: fail closed instead for HIPAA projects?)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import prompt_check as pc  # noqa: E402 - sibling module, found through the line above

TAG = "[prompt-guard]"
AGENT = f"{pc.GUARD}/HOOK"


def hook_output(result: dict, warnings: list[str]) -> dict | None:
    """Turn a check result into the hook's JSON answer (None = say nothing, let it through)."""
    summary = pc.decision_summary(result)
    notes = f" Config warnings: {'; '.join(warnings)}." if warnings else ""
    if result["result"] == "block":
        return {
            "decision": "block",
            "reason": f"Prompt blocked by prompt-guard: {summary}. Remove that value (or use an obvious fake) "
            f"and send again. The prompt was not sent.{notes}",
        }
    if result["result"] == "warn":
        return {
            "systemMessage": f"{TAG} Warning: {summary}. The prompt was sent.{notes}",
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": f"{TAG} This prompt may contain {summary}. Do not copy such values into "
                "files, logs, commits or other tools.",
            },
        }
    if warnings:
        return {"systemMessage": f"{TAG}{notes}"}
    return None


def run(payload: dict) -> dict | None:
    prompt = payload.get("prompt") or ""
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    cwd = payload.get("cwd")
    root = pc.project_root(Path(cwd) if isinstance(cwd, str) and cwd else None)
    result, warnings = pc.check(prompt, root)
    pc.record(result, root, AGENT, session_id=str(payload.get("session_id", "")))
    return hook_output(result, warnings)


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    answer = run(payload)
    if answer:
        print(json.dumps(answer))
    return 0  # the decision travels in the JSON


def fail_open(err: Exception) -> None:
    """Internal error: let the prompt through with a warning and record GUARD_ERROR. Never raises."""
    print(json.dumps({"systemMessage": f"{TAG} Guard error ({type(err).__name__}); this prompt was NOT checked."}))
    try:
        pc.emit("warn", "GUARD_ERROR", pc.project_root(), AGENT, error=type(err).__name__)
    except Exception:  # noqa: S110 - the warning above is already shown; nothing else to do
        pass


def entry() -> int:
    try:
        return main()
    except Exception as err:  # fail open on a bug in the guard itself
        fail_open(err)
        return 0


if __name__ == "__main__":
    sys.exit(entry())
