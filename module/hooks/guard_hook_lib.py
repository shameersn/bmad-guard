# /// script
# requires-python = ">=3.10"
# ///
"""Shared pieces of the git hooks (Phase 2, T6; the pre-push hook of T7 uses them too).

- `git()` and friends: run git in the project and return bytes. They keep the environment as it is, so a hook that
  git starts for `git commit -- path` (a temporary index, `GIT_INDEX_FILE`) reads that index and not the real one.
- `load_skill_script()`: load a script of a guard skill from the project's `.claude/skills` or `.agents/skills`.
  The hook reuses the skills' code (prompt-guard rules, Warden's hash, the document parser, Ledger); it copies none.
  A skill that is missing raises `HookError`, and the hook blocks with a message that says what to install.
- `emit()`: record one guard event in the local queue. It never sends: a git hook never waits on the network
  (ADR 0003), so the collector settings in the environment are ignored here. It never raises.

Events carry rule ids, counts and (for approval events) a path and a SHA-256. Never file content or matched text.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

GUARD = "git-hooks"
SKILL_FOLDERS = (".claude/skills", ".agents/skills")
INSTALL_HINT = "Run scripts/setup_guard.py from the bmad-guard module to install it."
_warned: list[str] = []  # the event warning is printed once per run, not once per event


class HookError(Exception):
    """Something stops the hook from checking. The hook blocks. The message must never hold file content."""


# --- git -------------------------------------------------------------------------------------------------------


def _run(root: Path, args: list[str], data: bytes | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_LITERAL_PATHSPECS"] = "1"  # a file called a[1].md is that file, not a pattern
    try:
        return subprocess.run(["git", *args], cwd=root, input=data, capture_output=True, env=env)
    except OSError as err:
        raise HookError(f"git cannot be run ({type(err).__name__})") from err


def git(root: Path, *args: str) -> bytes:
    """The output of a git command. Raises HookError when it fails (the message names the git command only)."""
    proc = _run(root, list(args))
    if proc.returncode != 0:
        raise HookError(f"git {args[0]} failed (exit {proc.returncode})")
    return proc.stdout


def git_ok(root: Path, *args: str) -> bool:
    """True when the git command exits 0 (for questions such as "does this file exist in HEAD")."""
    return _run(root, list(args)).returncode == 0


def project_root(cwd: Path | None = None) -> Path:
    """The git top level of `cwd`. A hook runs in the top level of the work tree already."""
    folder = cwd or Path.cwd()
    out = _run(folder, ["rev-parse", "--show-toplevel"])
    if out.returncode != 0 or not out.stdout.strip():
        raise HookError("this is not a git work tree")
    return Path(os.fsdecode(out.stdout.strip()))


def has_head(root: Path) -> bool:
    return git_ok(root, "rev-parse", "--verify", "-q", "HEAD")


def index_blob(root: Path, rel: str) -> bytes | None:
    """The staged content of a file (what the commit will hold), or None when it is not in the index."""
    if not git_ok(root, "cat-file", "-e", f":{rel}"):
        return None
    return git(root, "cat-file", "blob", f":{rel}")


def head_blob(root: Path, rel: str, head: bool) -> bytes | None:
    """The committed content of a file, or None when there is no HEAD yet or the file is new."""
    if not head or not git_ok(root, "cat-file", "-e", f"HEAD:{rel}"):
        return None
    return git(root, "cat-file", "blob", f"HEAD:{rel}")


def text_of(data: bytes | None) -> str:
    """Text as Warden reads a file: UTF-8, with newlines folded to \\n (Python's universal newlines).

    A byte that is not UTF-8 becomes U+FFFD, so the text still parses but can never match a recorded hash.
    """
    if data is None:
        return ""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


# --- the guard skills --------------------------------------------------------------------------------------------


def find_script(root: Path, skill: str, script: str) -> Path:
    for folder in SKILL_FOLDERS:
        path = root / folder / skill / "scripts" / script
        if path.is_file():
            return path
    raise HookError(f"the {skill} skill is not installed in {' or '.join(SKILL_FOLDERS)}. {INSTALL_HINT}")


def load_skill_script(root: Path, skill: str, script: str):
    """A script of a guard skill as a module, loaded once. The module name is the script's file name."""
    name = Path(script).stem
    if name in sys.modules:
        return sys.modules[name]
    path = find_script(root, skill, script)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise HookError(f"{skill}/{script} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # before the run: dataclasses and sibling imports look the module up
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


# --- events ------------------------------------------------------------------------------------------------------


def _project_name(root: Path) -> str:
    try:
        data = json.loads((root / "_bmad/custom/project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("project_name", "")) if isinstance(data, dict) else ""


def emit(root: Path, stage: str, verdict: str, reason_code: str, agent: str, **fields) -> None:
    """Queue one guard event. Never raises, never sends. Warns on stderr when Ledger is not there."""
    try:
        ge = load_skill_script(root, "agent-project-logger", "guard_events.py")
        event = ge.build_event(GUARD, stage, verdict, reason_code, project=_project_name(root), agent=agent, **fields)
        ge.emit(event, ge.Config(queue=root / ge.QUEUE_REL))  # no endpoint: the queue only, no network
    except Exception as err:  # logging never changes a decision
        if not _warned:
            _warned.append(type(err).__name__)
            print(f"bmad-guard: warning: event not recorded ({type(err).__name__})", file=sys.stderr)
