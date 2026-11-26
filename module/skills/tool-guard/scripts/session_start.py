# /// script
# requires-python = ">=3.10"
# ///
"""Claude Code SessionStart hook: warn when the guard setup is incomplete (Phase 2, T1). Never blocks.

Wired by scripts/setup_guard.py as:
    python3 "${CLAUDE_PROJECT_DIR}/.claude/skills/tool-guard/scripts/session_start.py"

Checks these and warns the developer (a `systemMessage`, shown by Claude Code) when one is missing:
  - git config core.hooksPath is not _bmad/custom/guard/hooks (the git hooks would not run);
  - neither the project's .claude/settings.json nor the managed settings file(s) hold the guard hook wiring
    (see hook_wiring.py). Managed settings: the OS file and its managed-settings.d/ drop-ins, read as one.
    A macOS configuration profile, the Windows registry and server-managed settings are not read.
A third check: the per-user credential file (ADR 0007) is there but not safe to use (too open, not yours, a link,
unreadable). A missing file is not a warning: with no endpoint, events only queue.
At the start it also sends the event queue once (hook_common.flush): the git hooks only queue. Best effort: no endpoint
means no network and no message; a collector that is down costs at most the sender's 3 s timeout, then 5 minutes
of backoff.
One `warn` event records it (code SETUP_INCOMPLETE, field `missing`). It always exits 0, even on an error.

The unguarded-project rule comes first (ADR 0008): no `_bmad/` in the project means silence (no output, no event, no
file written); `_bmad/` without `_bmad/custom/guard/manifest.json` means one line ("bmad-guard is not installed here,
the guards are off") and nothing else. Only a project with the manifest gets the checks above.
When this script runs from the machine folder (ADR 0008) and its `VERSION` differs from the version in the project's
manifest, it records one `info` event MACHINE_VERSION_SKEW (fields machine_version and project_version; no message),
so a report can list stale projects.
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
NOT_INSTALLED = (
    f"{TAG} BMAD found, bmad-guard is not installed here, so the guards are off. "
    "Run scripts/setup_guard.py --project . to turn them on."
)
FIXES = {
    "hooks_path": f"git config core.hooksPath is not {hw.HOOKS_REL}, so the git hooks will not run",
    "hook_wiring": (
        f"neither {hw.SETTINGS_REL} nor the managed settings hold the guard hooks and deny rules, "
        "so Claude Code hooks will not run"
    ),
    "credential": (
        "the bmad-guard credential file (~/.config/bmad-guard/credentials) is not safe to use "
        "(open to others, not yours, a link or unreadable), so events only queue. Fix: chmod 600 on the file and "
        "chmod 700 on its folder; run setup_guard.py --check for details"
    ),
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
    value = hw.MANAGED_DIRS.get(sys.platform)  # defined once, in hook_wiring.py
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


def _wired(load, base: str | None = None) -> bool:
    try:
        settings = load()
        return settings is not None and hw.merge(settings, base=base).ok
    except (ValueError, OSError):  # an unusable file, or a managed folder that is not a POSIX path (Windows)
        return False


def wiring_ok(root: Path) -> bool:
    """True when the project settings or the managed settings hold the full wiring. The managed file may hold the
    project form of the commands (the first template) or the machine form (ADR 0008): its scripts are in the
    `bmad-guard` folder next to the managed file."""
    managed = managed_dir()
    machine = str(managed / hw.MACHINE_DIR_NAME) if managed is not None else None
    return (
        _wired(lambda: hw.load_settings(root / hw.SETTINGS_REL))
        or _wired(lambda: managed_settings(managed))
        or (machine is not None and _wired(lambda: managed_settings(managed), machine))
    )


def credential_ok(root: Path) -> bool:
    """False when the per-user credential file exists but is not safe to use (ADR 0007). A missing file is fine:
    with no endpoint, events only queue. Uses Ledger's own check, so the two cannot disagree."""
    try:
        ge = hc._guard_events()
        return ge is None or not ge.Config.for_project(root).credential_problem
    except Exception:  # never block a session start
        return True


def problems(root: Path) -> list[str]:
    found = []
    if not hooks_path_ok(root):
        found.append("hooks_path")
    if not wiring_ok(root):
        found.append("hook_wiring")
    if not credential_ok(root):
        found.append("credential")
    return found


def machine_version() -> str | None:
    """The version of the machine folder this script runs from, or None when it runs from a project (ADR 0008)."""
    folder = HERE.parents[2]
    version = folder / "VERSION"
    if not (version.is_file() and (folder / "manifest.json").is_file()):
        return None
    return version.read_text(encoding="utf-8").strip() or None


def project_version(root: Path) -> str | None:
    try:
        data = json.loads((root / hc.GUARD_MANIFEST_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version else None


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        payload = hc.read_payload(raw or "{}")
    except ValueError:
        payload = {}
    state = hc.project_state(payload)
    if state == hc.UNGUARDED:
        return 0
    if state == hc.NO_MANIFEST:
        print(json.dumps({"systemMessage": NOT_INSTALLED}))
        return 0
    cwd = payload.get("cwd")
    root = hc.project_root(cwd if isinstance(cwd, str) and cwd else None)
    hc.flush(root)  # send what the git hooks queued (they never send); best effort, backoff and 3 s timeout apply
    session_id = payload.get("session_id")
    machine, project = machine_version(), project_version(root)
    if machine and project and machine != project:
        hc.emit(
            STAGE,
            "info",
            "MACHINE_VERSION_SKEW",
            root,
            AGENT,
            machine_version=machine,
            project_version=project,
            session_id=session_id if isinstance(session_id, str) else "",
        )
    found = problems(root)
    if not found:
        return 0
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
