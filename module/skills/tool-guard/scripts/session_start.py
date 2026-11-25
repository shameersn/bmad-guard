# /// script
# requires-python = ">=3.10"
# ///
"""Claude Code SessionStart hook: warn when the guard setup is incomplete (Phase 2, T1). Never blocks.

Wired by scripts/setup_guard.py as:
    python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/tool-guard/scripts/session_start.py"

Checks two things and warns the developer (a `systemMessage`, shown by Claude Code) when one is missing:
  - git config core.hooksPath is not _bmad/custom/guard/hooks (the git hooks would not run);
  - neither the project's .claude/settings.json nor the managed settings file(s) hold the guard hook wiring
    (see hook_wiring.py). Managed settings: the OS file and its managed-settings.d/ drop-ins, read as one.
    A macOS configuration profile, the Windows registry and server-managed settings are not read.
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
    "hook_wiring": (
        f"neither {hw.SETTINGS_REL} nor the managed settings hold the guard hooks and deny rules, "
        "so Claude Code hooks will not run"
    ),
}
# Managed settings folder per OS (docs/managed-settings-rollout.md). WSL reports linux.
MANAGED_DIRS = {
    "darwin": "/Library/Application Support/ClaudeCode",
    "linux": "/etc/claude-code",
    "win32": "C:\\Program Files\\ClaudeCode",
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


def managed_dir() -> Path | None:
    value = MANAGED_DIRS.get(sys.platform)
    return Path(value) if value else None


def managed_settings(base: Path | None) -> dict | None:
    """The managed file and its drop-ins combined (hooks and deny lists merge), or None when there are none.
    Raises ValueError when a file is unusable."""
    if base is None:
        return None
    files = [base / "managed-settings.json", *sorted((base / "managed-settings.d").glob("*.json"))]
    combined: dict = {}
    for path in files:
        data = hw.load_settings(path)
        if data is None:
            continue
        hooks = data.get("hooks")
        if isinstance(hooks, dict):
            for event, groups in hooks.items():
                if isinstance(groups, list):
                    combined.setdefault("hooks", {}).setdefault(event, []).extend(groups)
        perms = data.get("permissions")
        deny = perms.get("deny") if isinstance(perms, dict) else None
        if isinstance(deny, list):
            combined.setdefault("permissions", {}).setdefault("deny", []).extend(deny)
    return combined or None


def _wired(load) -> bool:
    try:
        settings = load()
    except (ValueError, OSError):
        return False
    return settings is not None and hw.merge(settings).ok


def wiring_ok(root: Path) -> bool:
    """True when the project settings or the managed settings hold the full wiring."""
    return _wired(lambda: hw.load_settings(root / hw.SETTINGS_REL)) or _wired(lambda: managed_settings(managed_dir()))


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
