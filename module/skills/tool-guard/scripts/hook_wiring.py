# /// script
# requires-python = ">=3.10"
# ///
"""What the bmad-guard Claude Code settings must contain (Phase 2, T1). One definition, three users:

- `scripts/setup_guard.py` merges it into a project's `.claude/settings.json`;
- `session_start.py` checks at session start that it is still there;
- the managed-settings template and the install test compare against it.

`merge()` is pure: it takes the settings dict and returns a new one plus a list of changes and conflicts.
Our hook entries are identified by their exact command string, so a second run changes nothing and the
project's own entries are never touched.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

SETTINGS_REL = ".claude/settings.json"
HOOKS_REL = "_bmad/custom/guard/hooks"  # git hooks folder (core.hooksPath); same value as scripts/setup_guard.py
SKILLS_REL = ".claude/skills"

# Tools that reach the PreToolUse dispatcher. A guard added later for another tool needs this list changed.
PRE_TOOL_MATCHER = "Bash|Read|Grep|Glob|Write|Edit|NotebookEdit"

# (event, matcher or None for all, skill folder, script, extra hook fields).
# PreToolUse: "onFailure": "block" turns a crash or timeout of the hook (exit 1, no exit 2) into a block.
# UserPromptSubmit goes straight to the Phase 1 hook: it has its own fail mode (ADR 0006, T5).
HOOKS = (
    ("SessionStart", None, "tool-guard", "session_start.py", {"timeout": 10}),
    ("UserPromptSubmit", None, "prompt-guard", "prompt_guard_hook.py", {"timeout": 30}),
    ("PreToolUse", PRE_TOOL_MATCHER, "tool-guard", "pre_tool_use.py", {"timeout": 10, "onFailure": "block"}),
)

# Sensitive paths (Phase 2 plan, T2): (rule id, patterns, carve-outs). One list, two users:
#   - DENY_RULES below (Claude Code `permissions.deny`, the second layer);
#   - path_guard.py (the PreToolUse hook), which matches the same patterns and reports the rule id.
# A pattern with no "/" matches a file name at any depth (gitignore style). `~/dir/**` is a folder under the home
# folder. A carve-out is a name that stays readable. Matching is case-insensitive in the hook.
# `id_rsa*` and friends are SSH keys by name; `id_test.py` or `identity.md` are not (see references/guard-rules.md).
SENSITIVE_PATHS = (
    ("PATH_ENV_FILE", (".env", ".env.*", "*.env"), (".env.example",)),
    ("PATH_PRIVATE_KEY", ("*.pem", "*.key", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*"), ()),
    ("PATH_KEY_STORE", ("*.p12",), ()),
    ("PATH_DB_DUMP", ("*dump*.sql",), ()),
    (
        "PATH_DB_BACKUP",
        ("*.sql.bak", "*.db.bak", "*.sqlite.bak", "*.sqlite3.bak", "*dump*.bak", "*database*.bak"),
        (),
    ),
    ("PATH_AWS_DIR", ("~/.aws/**",), ()),
    ("PATH_SSH_DIR", ("~/.ssh/**",), ()),
    ("PATH_GUARD_CONFIG", ("~/.config/bmad-guard/**",), ()),
)


def _deny_rules() -> tuple[str, ...]:
    """`Read(...)` deny rules for SENSITIVE_PATHS. `!` carves out a name; it must come after the rule it carves from.
    A Read deny also blocks Edit and Write on the same path."""
    rules: list[str] = []
    for _, patterns, carve_outs in SENSITIVE_PATHS:
        rules.extend(f"Read({pattern})" for pattern in patterns)
        rules.extend(f"Read(!{name})" for name in carve_outs)
    return tuple(rules)


DENY_RULES = _deny_rules()

# A command containing one of these looks like ours. If it is not exactly one of ours, it is stale.
MARKERS = ("/skills/tool-guard/scripts/", "prompt_guard_hook.py")


def command(skill: str, script: str) -> str:
    """The exact hook command. CLAUDE_PROJECT_DIR is set by Claude Code for hooks; the quotes allow spaces."""
    return f'python3 "${{CLAUDE_PROJECT_DIR}}/{SKILLS_REL}/{skill}/scripts/{script}"'


def _hook(skill: str, script: str, extra: dict) -> dict:
    return {"type": "command", "command": command(skill, script), **extra}


def _group(matcher: str | None, hook: dict) -> dict:
    group: dict = {"matcher": matcher} if matcher else {}
    group["hooks"] = [hook]
    return group


def desired_hooks() -> dict[str, list[dict]]:
    """The `hooks` block we install, in Claude Code settings shape (also used by the managed template)."""
    out: dict[str, list[dict]] = {}
    for event, matcher, skill, script, extra in HOOKS:
        out.setdefault(event, []).append(_group(matcher, _hook(skill, script, extra)))
    return out


def script_paths() -> list[str]:
    """Project-relative paths of the scripts the hooks run."""
    return [f"{SKILLS_REL}/{skill}/scripts/{script}" for _, _, skill, script, _ in HOOKS]


@dataclass
class Merge:
    settings: dict
    changes: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.changes and not self.conflicts


def load_settings(path: Path) -> dict | None:
    """The settings dict, or None when the file does not exist. Raises ValueError when it is unusable."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise ValueError(f"{path.name} is not valid JSON; fix it first") from err
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a JSON object; fix it first")
    return data


def render(settings: dict) -> str:
    return json.dumps(settings, indent=2) + "\n"


def _is_group(item) -> bool:
    return isinstance(item, dict) and isinstance(item.get("hooks"), list)


def _commands(groups: list):
    """Yield (group, hook, command) for every command hook in a list of matcher groups."""
    for group in groups:
        if _is_group(group):
            for hook in group["hooks"]:
                if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                    yield group, hook, hook["command"]


def _drop(groups: list, commands: list[str]) -> None:
    """Remove the hooks that run one of `commands`; a group we emptied goes too."""
    kept = []
    for group in groups:
        if _is_group(group):
            inner = [h for h in group["hooks"] if not (isinstance(h, dict) and h.get("command") in commands)]
            if not inner and group["hooks"]:
                continue
            group["hooks"] = inner
        kept.append(group)
    groups[:] = kept


def _merge_hooks(result: Merge, force: bool) -> None:
    settings = result.settings
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        if not force:
            result.conflicts.append("hooks is not an object")
            return
        result.changes.append("replace invalid hooks value")
        hooks = {}
    settings["hooks"] = hooks
    for event, matcher, skill, script, extra in HOOKS:
        want = command(skill, script)
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            if not force:
                result.conflicts.append(f"hooks.{event} is not a list")
                continue
            result.changes.append(f"replace invalid hooks.{event} value")
            groups = []
        stale = [cmd for _, _, cmd in _commands(groups) if cmd != want and any(m in cmd for m in MARKERS)]
        if stale and not force:
            result.conflicts.append(f"{event}: '{stale[0]}' looks like a bmad-guard hook but is not the expected one")
        elif stale:
            _drop(groups, stale)
            result.changes.append(f"remove stale hook {event}: {stale[0]}")
        ours = [(group, hook) for group, hook, cmd in _commands(groups) if cmd == want]
        wanted_hook = _hook(skill, script, extra)
        if len(ours) == 1 and ours[0][1] == wanted_hook and ours[0][0].get("matcher") == matcher:
            hooks[event] = groups
            continue
        _drop(groups, [want])
        groups.append(_group(matcher, wanted_hook))
        hooks[event] = groups
        result.changes.append(f"{'update' if ours else 'add'} hook {event} {script}")


def _merge_deny(result: Merge, force: bool) -> None:
    settings = result.settings
    perms = settings.get("permissions", {})
    if not isinstance(perms, dict):
        if not force:
            result.conflicts.append("permissions is not an object")
            return
        result.changes.append("replace invalid permissions value")
        perms = {}
    deny = perms.get("deny", [])
    if not isinstance(deny, list):
        if not force:
            result.conflicts.append("permissions.deny is not a list")
            return
        result.changes.append("replace invalid permissions.deny value")
        deny = []
    missing = [rule for rule in DENY_RULES if rule not in deny]
    if missing:
        perms["deny"] = deny + missing
        settings["permissions"] = perms
    result.changes.extend(f"add deny rule {rule}" for rule in missing)


def merge(settings: dict | None, force: bool = False) -> Merge:
    """Add our hooks and deny rules to `settings` (a copy). Conflicts stay as they are unless `force`."""
    result = Merge(copy.deepcopy(settings) if settings else {})
    _merge_hooks(result, force)
    _merge_deny(result, force)
    if result.settings.get("disableAllHooks") is True:
        if force:
            del result.settings["disableAllHooks"]
            result.changes.append("remove disableAllHooks")
        else:
            result.conflicts.append("disableAllHooks is true: no hook would run")
    return result
