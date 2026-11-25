# /// script
# requires-python = ">=3.10"
# ///
"""Claude Code SessionStart hook: warn when the guard setup is incomplete (Phase 2, T1). Never blocks.

Wired by scripts/setup_guard.py as:
    python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/tool-guard/scripts/session_start.py"

Checks two things and warns the developer (a `systemMessage`, shown by Claude Code) when one is missing:
  - git config core.hooksPath is not _bmad/custom/guard/hooks (the git hooks would not run);
  - the project's .claude/settings.json does not hold the guard hook wiring (see hook_wiring.py).
One `warn` event records it (code SETUP_INCOMPLETE, field `missing`). It always exits 0, even on an error.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import hook_common as hc  # noqa: E402 - sibling module, found through the line above
import hook_wiring as hw  # noqa: E402

STAGE = "session"
AGENT = f"{hc.GUARD}/SessionStart"
TAG = "[tool-guard]"
FIXES = {
    "hooks_path": f"git config core.hooksPath is not {hw.HOOKS_REL}, so the git hooks will not run",
    "hook_wiring": f"{hw.SETTINGS_REL} is missing the guard hooks or deny rules, so Claude Code hooks will not run",
}


def hooks_path_ok(root: Path) -> bool:
    try:
        out = subprocess.run(["git", "config", "core.hooksPath"], cwd=root, capture_output=True, text=True)
    except OSError:
        return False
    value = out.stdout.strip()
    if out.returncode != 0 or not value:
        return False
    return (root / value).resolve() == (root / hw.HOOKS_REL).resolve()


def wiring_ok(root: Path) -> bool:
    try:
        return hw.merge(hw.load_settings(root / hw.SETTINGS_REL)).ok
    except ValueError:
        return False


def problems(root: Path) -> list[str]:
    found = []
    if not hooks_path_ok(root):
        found.append("hooks_path")
    if not wiring_ok(root):
        found.append("hook_wiring")
    return found


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        payload = hc.read_payload(raw or "{}")
    except ValueError:
        payload = {}
    cwd = payload.get("cwd")
    root = hc.project_root(cwd if isinstance(cwd, str) and cwd else None)
    found = problems(root)
    if not found:
        return 0
    session_id = payload.get("session_id")
    hc.emit(
        STAGE,
        "warn",
        "SETUP_INCOMPLETE",
        root,
        AGENT,
        missing=",".join(found),
        session_id=session_id if isinstance(session_id, str) else "",
    )
    text = "; ".join(FIXES[name] for name in found)
    advice = "Run the bmad-guard setup script (scripts/setup_guard.py --project .)."
    print(json.dumps({"systemMessage": f"{TAG} Warning: {text}. {advice}"}))
    return 0


def entry() -> int:
    try:
        return main()
    except Exception as err:  # never block a session start
        print(f"{TAG} warning: setup check failed ({type(err).__name__})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(entry())
