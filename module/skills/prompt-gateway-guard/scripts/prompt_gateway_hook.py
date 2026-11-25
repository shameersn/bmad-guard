# /// script
# requires-python = ">=3.10"
# ///
"""Claude Code UserPromptSubmit hook: screen every prompt through PromptGateway.

Wired into Claude Code settings in Phase 2 (managed settings), as:
    python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/prompt-gateway-guard/scripts/prompt_gateway_hook.py"

Fails open (ADR 0002): if the gateway cannot be reached or errors, the prompt goes through with a
warning, and an event records that screening was skipped. A screening outage must not stop work.

  BLOCK -> {"decision": "block", ...}: Claude never sees the prompt; the reason is shown to the user.
  FLAG  -> the prompt goes through; a warning is shown and added to Claude's context.
  ALLOW -> goes through silently.

Config: base_url, downstream_model and timeout from the skill's customize.toml plus overrides in
_bmad/custom/prompt-gateway-guard.toml (scalars only). The API key comes only from the
BMAD_GUARD_GATEWAY_KEY environment variable.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import validate_prompt as vp  # noqa: E402 - sibling module, found through the line above

SKILL_ROOT = HERE.parent
DEFAULTS = {"base_url": "http://localhost:8000", "downstream_model": "", "timeout": "8"}
SCALAR_RE = re.compile(r'^\s*(base_url|downstream_model|timeout)\s*=\s*"?([^"#]*?)"?\s*(#.*)?$')
TAG = "[Marshal]"


def read_scalars(path: Path) -> dict:
    """Read the few scalar keys this hook needs. Simple on purpose: no TOML library on Python 3.10."""
    found = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = SCALAR_RE.match(line)
            if match:
                found[match.group(1)] = match.group(2).strip()
    return found


def load_config(root: Path) -> dict:
    config = dict(DEFAULTS)
    for path in (
        SKILL_ROOT / "customize.toml",
        root / "_bmad/custom/prompt-gateway-guard.toml",
        root / "_bmad/custom/prompt-gateway-guard.user.toml",
    ):
        config.update(read_scalars(path))
    return config


def hook_output(result: dict) -> dict | None:
    """Turn a gateway result into the hook's JSON answer (None = say nothing, let it through)."""
    outcome = result.get("result")
    if outcome in ("unreachable", "http_error", "refused"):
        detail = result.get("error") or f"HTTP {result.get('status')}"
        return {
            "systemMessage": f"{TAG} PromptGateway check skipped ({outcome}: {detail}); prompt sent without "
            "screening. Ask the team to check the gateway."
        }
    decision = result.get("decision")
    risk = result.get("risk_score")
    reason = result.get("reason", "")
    categories = ", ".join(result.get("categories_triggered", []) or []) or "none"
    if decision == "BLOCK":
        text = f"PromptGateway BLOCK (risk {risk}): {reason}. Categories: {categories}"
        return {"decision": "block", "reason": text, "systemMessage": f"{TAG} BLOCKED (risk {risk}): {reason}"}
    if decision == "FLAG":
        return {
            "systemMessage": f"{TAG} FLAGGED (risk {risk}): {reason}. Categories: {categories}",
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": f"[PromptGateway FLAG] risk_score={risk} categories={categories}",
            },
        }
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    prompt = payload.get("prompt") or ""
    if not prompt.strip():
        return 0

    root = vp.project_root(Path(payload.get("cwd") or Path.cwd()))
    config = load_config(root)
    try:
        timeout = float(config.get("timeout") or DEFAULTS["timeout"])
    except ValueError:
        timeout = float(DEFAULTS["timeout"])
    result = vp.validate(
        prompt, config["base_url"], os.environ.get(vp.KEY_ENV, ""), config.get("downstream_model", ""), "", timeout
    )
    vp.record(result, root, "prompt-gateway-guard/HOOK", session_id=str(payload.get("session_id", "")))
    answer = hook_output(result)
    if answer:
        print(json.dumps(answer))
    return 0  # always 0: the decision travels in the JSON, and a broken hook must not block (fail open)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as err:  # fail open on anything unexpected
        print(
            json.dumps(
                {"systemMessage": f"{TAG} PromptGateway hook error ({type(err).__name__}); prompt not screened."}
            )
        )
        sys.exit(0)
