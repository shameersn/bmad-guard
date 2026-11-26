# /// script
# requires-python = ">=3.10"
# ///
"""Claude Code UserPromptSubmit hook: check every prompt locally before Claude sees it (ADR 0006).

Wired into the project's `.claude/settings.json` by the setup script (and the managed-settings template) as:
    python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/prompt-guard/scripts/prompt_guard_hook.py"

Reads the hook JSON ({"prompt", "session_id", "cwd", ...}) from stdin and answers:
  block -> {"decision": "block", "reason": ...}: Claude never sees the prompt. The reason names the
           categories and rule ids, never the matched text.
  warn  -> the prompt goes through; a warning is shown and added to Claude's context.
  allow -> no output (record-only findings are still recorded as an event).

Decisions fail closed: a "block" blocks. An internal error in the guard itself (Phase 2, T5) depends on the project:
  HIPAA project (`phi = "block"`, or the setting cannot be read) -> the prompt is BLOCKED: it was not checked, and
                         unchecked PHI must not reach the model. A GUARD_ERROR block event records it.
  other projects      -> the prompt goes through with a warning, and a GUARD_ERROR warn event records that it was
                         not checked. A bug in this script must not stop all work in Claude Code.
The same rule is used by the content scan on file writes (tool-guard `content_scan.py`). A prompt hook that
gets input it cannot read (not JSON, no prompt text) has nothing to check and lets it through, as before.

The unguarded-project rule comes first (ADR 0008): a project with no `_bmad/`, or with `_bmad/` and no setup manifest
(`_bmad/custom/guard/manifest.json`), is allowed silently: no output, no event, no file written. Any doubt about which
project it is means guarded.

The warning, the block reason and the events carry rule ids and the error type only, never any part of the prompt.
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


def unguarded(payload: dict) -> bool:
    """True when the project does not use the guard (ADR 0008). Any error means guarded."""
    try:
        cwd = payload.get("cwd")
        root = pc.project_root(Path(cwd) if isinstance(cwd, str) and cwd else None)
        return pc.guard_state(root, cwd) != pc.GUARDED
    except Exception:
        return False


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if unguarded(payload):
        return 0
    answer = run(payload)
    if answer:
        print(json.dumps(answer))
    return 0  # the decision travels in the JSON


def fail(err: Exception) -> None:
    """Internal error: block in a HIPAA project, else let the prompt through with a warning. Records GUARD_ERROR.
    Never raises."""
    name = type(err).__name__
    root = None
    try:
        root = pc.project_root()
        hipaa = pc.is_hipaa(root)
    except Exception:  # cannot tell: treat as HIPAA
        hipaa = True
    if hipaa:
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": f"Prompt blocked by prompt-guard: the check failed ({name}) and this project is "
                    "marked HIPAA, so the prompt was not checked and was not sent. Tell the developer.",
                }
            )
        )
    else:
        print(json.dumps({"systemMessage": f"{TAG} Guard error ({name}); this prompt was NOT checked."}))
    try:
        pc.emit("block" if hipaa else "warn", "GUARD_ERROR", root or Path.cwd(), AGENT, error=name)
    except Exception:  # noqa: S110 - the answer above is already printed; nothing else to do
        pass


def entry() -> int:
    try:
        return main()
    except Exception as err:  # a bug in the guard itself: the fail mode depends on the project (see top)
        fail(err)
        return 0


if __name__ == "__main__":
    sys.exit(entry())
