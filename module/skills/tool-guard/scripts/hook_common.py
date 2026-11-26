# /// script
# requires-python = ">=3.10"
# ///
"""Shared pieces of the tool-guard hook scripts (Phase 2, T1).

- `Verdict` and `Guard`: the contract a guard module follows. A guard module defines `GUARDS = [Guard(...)]`;
  `pre_tool_use.py` imports it once its module name is listed in `GUARD_MODULES`. A verdict blocks by default;
  `severity="warn"` lets the call go ahead and tells the developer and the model (T5, content scan).
- `read_payload()`: parse the hook JSON that Claude Code sends on stdin.
- `project_root()`, `emit()`: find the project and record one guard event through Ledger. Never raises.
- `guard_state()`, `project_state()`: the unguarded-project rule (ADR 0008). Each hook entry asks first whether the
  project uses the guard, before any guard is imported. `prompt_check.py` holds a copy of `guard_state` (the prompt hook
  does not import this file); a test keeps the two equal.

Events carry codes, rule names and paths only. Never prompt text, file content or command text (ADR 0003).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

GUARD = "tool-guard"


BLOCK, WARN = "block", "warn"


@dataclass(frozen=True)
class Verdict:
    """One reason to block a tool call, or (severity "warn") one warning. `reason` and `detail` must never contain
    file content or command text."""

    code: str  # stable reason code, e.g. "BAD_HOOK_INPUT"
    reason: str  # one short sentence for the developer and the model
    guard: str = ""  # name of the guard that decided
    path: str = ""  # project-relative path involved, if any
    severity: str = BLOCK  # "block" stops the call; "warn" lets it through with a message
    detail: str = ""  # short text for the event only (rule ids, an error type); never content


@dataclass(frozen=True)
class Guard:
    """A guard: runs for the listed tool names ("*" = every tool) and returns the reasons to block."""

    name: str
    tools: tuple[str, ...]
    check: Callable[[dict, Path], list[Verdict]]  # (hook payload, project root) -> [] means allow


def blocks(verdicts: list[Verdict]) -> list[Verdict]:
    return [v for v in verdicts if v.severity != WARN]


def warnings(verdicts: list[Verdict]) -> list[Verdict]:
    return [v for v in verdicts if v.severity == WARN]


def read_payload(raw: str) -> dict:
    """The hook input as a dict. Raises ValueError when it is not a JSON object."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        raise ValueError("hook input is not valid JSON") from err
    if not isinstance(data, dict):
        raise ValueError("hook input is not a JSON object")
    return data


def project_root(cwd: str | Path | None = None) -> Path:
    """CLAUDE_PROJECT_DIR when Claude Code sets it, else the git top level of `cwd`, else `cwd`."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    folder = Path(cwd) if cwd else Path.cwd()
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=folder, capture_output=True, text=True)
    except OSError:
        return folder
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else folder


# --- the unguarded-project rule (ADR 0008) --------------------------------------------------------------------
# A copy of `_exists` and `guard_state` lives in prompt-guard/scripts/prompt_check.py. Keep them equal.
GUARD_MANIFEST_REL = "_bmad/custom/guard/manifest.json"  # written by scripts/setup_guard.py: "setup ran here"
GUARDED, NO_MANIFEST, UNGUARDED = "guarded", "no-manifest", "unguarded"


def _exists(path: str) -> bool:
    """True when something is at `path`. Only "it is not there" counts as absent: any other error (a permission error,
    a bad name) counts as present, so a guarded project cannot look unguarded."""
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except (OSError, ValueError):
        return True
    return True


def guard_state(root, cwd=None) -> str:
    """GUARDED when the setup manifest is in the project, NO_MANIFEST when there is `_bmad/` but no manifest,
    UNGUARDED when there is no `_bmad/`. Fail closed: the project is looked for in `root`, in the hook input's `cwd` and
    in the process folder, each also with links resolved, and the manifest also in every parent folder. The manifest in
    any of those places means GUARDED. A `cwd` of the wrong type, a path that cannot be read, or any error means
    GUARDED. Reads nothing and writes nothing."""
    try:
        places = [root, cwd, os.getcwd()]
        found = []
        for place in places:
            if place in (None, ""):
                continue
            if not isinstance(place, (str, os.PathLike)):
                return GUARDED
            found += [os.path.abspath(place), os.path.realpath(place)]
        bmad = False
        for place in dict.fromkeys(found):
            bmad = bmad or _exists(os.path.join(place, "_bmad"))
            folder = place
            while True:
                if _exists(os.path.join(folder, GUARD_MANIFEST_REL)):
                    return GUARDED
                parent = os.path.dirname(folder)
                if parent == folder:
                    break
                folder = parent
        return NO_MANIFEST if bmad else UNGUARDED
    except Exception:
        return GUARDED


def project_state(payload: dict) -> str:
    """The rule for a hook input (a dict, possibly empty): the project as `project_root()` finds it, plus the input's
    `cwd` as it is. Any error means GUARDED."""
    try:
        cwd = payload.get("cwd")
        return guard_state(project_root(cwd if isinstance(cwd, str) and cwd else None), cwd)
    except Exception:
        return GUARDED


def _guard_events():
    """Ledger's sender, from the sibling skill folder. None when Ledger is not installed."""
    path = Path(__file__).resolve().parents[2] / "agent-project-logger" / "scripts" / "guard_events.py"
    if not path.is_file():
        return None
    if "guard_events" in sys.modules:
        return sys.modules["guard_events"]
    spec = importlib.util.spec_from_file_location("guard_events", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["guard_events"] = module
    spec.loader.exec_module(module)
    return module


def project_name(root: Path) -> str:
    try:
        data = json.loads((root / "_bmad/custom/project.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("project_name", "")) if isinstance(data, dict) else ""


def emit(stage: str, verdict: str, reason_code: str, root: Path, agent: str, **fields) -> None:
    """Record one guard event through Ledger. Never raises; warns on stderr when it cannot."""
    try:
        ge = _guard_events()
        if ge is None:
            print(
                "bmad-guard: warning: Ledger (agent-project-logger) not installed; event not recorded", file=sys.stderr
            )
            return
        event = ge.build_event(GUARD, stage, verdict, reason_code, project=project_name(root), agent=agent, **fields)
        ge.emit(event, ge.Config.for_project(root))
    except Exception as err:  # logging never changes a decision
        print(f"bmad-guard: warning: event not recorded ({type(err).__name__})", file=sys.stderr)


def flush(root: Path) -> None:
    """Send the queue once (best effort). No endpoint or no queue: nothing happens. Respects the sender's backoff and
    3 s timeout. Never raises."""
    try:
        ge = _guard_events()
        if ge is None:
            return
        config = ge.Config.for_project(root)
        if config.sending_enabled and config.queue.is_file():
            ge.emit(None, config)
    except Exception as err:  # logging never changes a decision
        print(f"bmad-guard: warning: events not sent ({type(err).__name__})", file=sys.stderr)
