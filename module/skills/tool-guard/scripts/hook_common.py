# /// script
# requires-python = ">=3.10"
# ///
"""Shared pieces of the tool-guard hook scripts (Phase 2, T1).

- `Verdict` and `Guard`: the contract a guard module follows. A guard module defines `GUARDS = [Guard(...)]`;
  `pre_tool_use.py` imports it once its module name is listed in `GUARD_MODULES`.
- `read_payload()`: parse the hook JSON that Claude Code sends on stdin.
- `project_root()`, `emit()`: find the project and record one guard event through Ledger. Never raises.

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


@dataclass(frozen=True)
class Verdict:
    """One reason to block a tool call. `reason` must never contain file content or command text."""

    code: str  # stable reason code, e.g. "BAD_HOOK_INPUT"
    reason: str  # one short sentence for the developer and the model
    guard: str = ""  # name of the guard that decided
    path: str = ""  # project-relative path involved, if any


@dataclass(frozen=True)
class Guard:
    """A guard: runs for the listed tool names ("*" = every tool) and returns the reasons to block."""

    name: str
    tools: tuple[str, ...]
    check: Callable[[dict, Path], list[Verdict]]  # (hook payload, project root) -> [] means allow


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
