# /// script
# requires-python = ">=3.10"
# ///
"""Rules for the command and path guards (Phase 2, T2): the built-in list plus the project's extension file.

Built-in rules live in the skill's own `customize.toml` (`[[agent.command_rules]]`, `scratch_folders`,
`local_hosts`). They are read from that file only. A project's `_bmad/custom/tool-guard.toml` is NOT read for guard
rules, so a project cannot replace or switch off a built-in rule by an override.

A project adds rules in `_bmad/custom/project/guard/tool-rules.json` (JSON, like `prompt-patterns.json`):

    {
      "command_rules": [{"id": "no-terraform-destroy", "command": "terraform", "words": ["destroy"],
                         "reason": "Destroy is run by the platform team."}],
      "sensitive_paths": [{"id": "payroll", "pattern": "payroll*.csv", "reason": "Payroll data."}],
      "scratch_folders": ["_work/cache"]
    }

It can only add. A bad extension file does not get skipped: `load()` raises `ExtensionError` and the guard blocks.
Python 3.10 has no TOML parser, so `parse_toml()` reads the small subset our own `customize.toml` uses.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
CUSTOMIZE = SKILL_ROOT / "customize.toml"
EXTENSION_REL = Path("_bmad/custom/project/guard/tool-rules.json")

ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
COMMAND_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
FLAG_RE = re.compile(r"-{1,2}[A-Za-z0-9][A-Za-z0-9-]*(\|-{1,2}[A-Za-z0-9][A-Za-z0-9-]*)*")
BUILTIN_KEYS = {"id", "reason", "check", "command", "subcommand", "words", "flag_sets", "unless_flags", "arg_prefixes"}
PROJECT_KEYS = BUILTIN_KEYS - {"check"}
EXTENSION_KEYS = {"command_rules", "sensitive_paths", "scratch_folders"}
MAX_ENTRIES = 200
MAX_REASON = 200


class ConfigError(ValueError):
    """The built-in rules cannot be read. This is a bug in the module: the guard fails closed."""


class ExtensionError(ValueError):
    """The project's extension file is not usable. The message says what to fix; it never repeats file content."""


# --- the TOML subset -------------------------------------------------------------------------------


def _strip_comment(line: str) -> str:
    in_string, escaped = False, False
    for i, ch in enumerate(line):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "#":
            return line[:i]
    return line


def _table(root: dict, path: list[str], number: int) -> dict:
    node = root
    for part in path:
        node = node.setdefault(part, {})
        if isinstance(node, list):
            node = node[-1]
        if not isinstance(node, dict):
            raise ValueError(f"line {number}: '{part}' is not a table")
    return node


def parse_toml(text: str) -> dict:
    """Tables `[a.b]`, arrays of tables `[[a.b]]`, and `key = value` where value is a JSON string, number, boolean or
    (nested) array on one line. Anything else raises ValueError. Equal to `tomllib.loads` on such files."""
    root: dict = {}
    table = root
    for number, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("[["):
            if not line.endswith("]]"):
                raise ValueError(f"line {number}: bad table header")
            path = [p.strip() for p in line[2:-2].split(".")]
            parent = _table(root, path[:-1], number)
            items = parent.setdefault(path[-1], [])
            if not isinstance(items, list):
                raise ValueError(f"line {number}: '{path[-1]}' is not an array of tables")
            table = {}
            items.append(table)
        elif line.startswith("["):
            if not line.endswith("]"):
                raise ValueError(f"line {number}: bad table header")
            table = _table(root, [p.strip() for p in line[1:-1].split(".")], number)
        else:
            key, eq, value = line.partition("=")
            key = key.strip()
            if not eq or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
                raise ValueError(f"line {number}: expected key = value")
            if key in table:
                raise ValueError(f"line {number}: duplicate key '{key}'")
            try:
                table[key] = json.loads(value.strip())
            except json.JSONDecodeError as err:
                raise ValueError(
                    f"line {number}: value of '{key}' is not a one-line string, number, boolean or array"
                ) from err
    return root


# --- rule shape ------------------------------------------------------------------------------------


def _text_list(value, where: str, error: type[Exception], item_re: re.Pattern | None = None) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value) or len(value) > MAX_ENTRIES:
        raise error(f"{where} must be a list of non-empty text values")
    if item_re is not None and not all(item_re.fullmatch(v) for v in value):
        raise error(f"{where} has an entry in the wrong format")
    return list(value)


def check_rule(rule: dict, where: str, error: type[Exception], allowed: set[str]) -> dict:
    """Validate one command rule and return it with defaults. `where` names the rule in messages."""
    unknown = sorted(set(rule) - allowed)
    if unknown:
        raise error(f"{where}: unknown key '{unknown[0]}'")
    if not isinstance(rule.get("id"), str):
        raise error(f"{where}: 'id' is missing")
    reason = rule.get("reason", "")
    if not isinstance(reason, str) or len(reason) > MAX_REASON or "\n" in reason:
        raise error(f"{where}: 'reason' must be one line of text, at most {MAX_REASON} characters")
    out = {"id": rule["id"], "reason": reason}
    if "check" in rule:
        if not isinstance(rule["check"], str):
            raise error(f"{where}: 'check' must be text")
        out["check"] = rule["check"]
        return out
    command = rule.get("command")
    if not isinstance(command, str) or not COMMAND_RE.fullmatch(command):
        raise error(f"{where}: 'command' must be a program name such as git (no path)")
    out["command"] = command
    sub = rule.get("subcommand")
    if sub is not None:
        subs = [sub] if isinstance(sub, str) else sub
        out["subcommand"] = _text_list(subs, f"{where}: 'subcommand'", error)
        if command != "git":
            raise error(f"{where}: 'subcommand' works for git only; use 'words' for other programs")
    if "words" in rule:
        out["words"] = _text_list(rule["words"], f"{where}: 'words'", error)
    if "arg_prefixes" in rule:
        out["arg_prefixes"] = _text_list(rule["arg_prefixes"], f"{where}: 'arg_prefixes'", error)
    if "unless_flags" in rule:
        out["unless_flags"] = _text_list(rule["unless_flags"], f"{where}: 'unless_flags'", error, FLAG_RE)
    if "flag_sets" in rule:
        sets = rule["flag_sets"]
        if not isinstance(sets, list) or not sets or len(sets) > MAX_ENTRIES:
            raise error(f"{where}: 'flag_sets' must be a list of lists of flags")
        out["flag_sets"] = [_text_list(s, f"{where}: 'flag_sets' entry", error, FLAG_RE) for s in sets]
    return out


@dataclass(frozen=True)
class Rules:
    commands: tuple[dict, ...]  # built-in rules first, then the project's; each has "id", "code", "reason"
    scratch: tuple[str, ...]  # folders where recursive delete is allowed (built-in, then the project's)
    local_hosts: tuple[str, ...]
    project_paths: tuple[tuple[str, str, str], ...]  # (reason code, pattern, reason)


def load_builtin() -> tuple[list[dict], list[str], list[str]]:
    """(command rules, scratch folders, local hosts) from the skill's customize.toml. Raises ConfigError."""
    try:
        agent = parse_toml(CUSTOMIZE.read_text(encoding="utf-8")).get("agent", {})
        rules = [
            check_rule(rule, f"customize.toml rule {i + 1}", ConfigError, BUILTIN_KEYS)
            for i, rule in enumerate(agent.get("command_rules", []))
        ]
        scratch = _text_list(agent.get("scratch_folders", []), "customize.toml scratch_folders", ConfigError)
        hosts = _text_list(agent.get("local_hosts", []), "customize.toml local_hosts", ConfigError)
    except (OSError, ValueError) as err:
        raise ConfigError(f"built-in guard rules cannot be read ({type(err).__name__})") from err
    ids = [r["id"] for r in rules]
    if not rules or len(ids) != len(set(ids)):
        raise ConfigError("built-in guard rules are empty or have a duplicate id")
    return rules, scratch, hosts


def _scratch_entry(entry: str, where: str) -> str:
    parts = [p for p in entry.split("/") if p]
    if entry.startswith("/"):
        if len(parts) < 2 or any(p in ("..", ".", "*", "**") for p in parts):
            raise ExtensionError(f"{where}: an absolute scratch folder needs at least two parts, such as /var/tmp")
    elif not parts or entry.startswith(("~", "../")) or any(p in ("..", ".") for p in parts) or parts == ["**"]:
        raise ExtensionError(f"{where}: a scratch folder is a folder inside the project, such as tmp or **/cache")
    return entry


def load_extension(root: Path) -> tuple[list[dict], list[tuple[str, str, str]], list[str]]:
    """(command rules, sensitive paths, scratch folders) from the project's extension file; empty without the file.
    Raises ExtensionError for anything wrong with it."""
    path = root / EXTENSION_REL
    name = EXTENSION_REL.name
    if not path.is_file():
        return [], [], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ExtensionError(f"{name} cannot be read as JSON ({type(err).__name__})") from err
    if not isinstance(data, dict):
        raise ExtensionError(f"{name} must be a JSON object")
    unknown = sorted(set(data) - EXTENSION_KEYS)
    if unknown:
        raise ExtensionError(f"{name}: unknown key '{unknown[0]}' (allowed: {', '.join(sorted(EXTENSION_KEYS))})")

    commands: list[dict] = []
    entries = data.get("command_rules", [])
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ExtensionError(f"{name}: 'command_rules' must be a list")
    for i, entry in enumerate(entries):
        where = f"{name}: command_rules[{i}]"
        if not isinstance(entry, dict):
            raise ExtensionError(f"{where} must be an object")
        rule = check_rule(entry, where, ExtensionError, PROJECT_KEYS)
        if not ID_RE.fullmatch(rule["id"]):
            raise ExtensionError(f"{where}: 'id' must be lower-case letters, digits, '.', '_' or '-'")
        rule["code"] = "PROJECT_CMD_" + re.sub(r"[^A-Z0-9]", "_", rule["id"].upper())
        rule["reason"] = rule["reason"] or "Blocked by a project guard rule."
        commands.append(rule)

    paths: list[tuple[str, str, str]] = []
    entries = data.get("sensitive_paths", [])
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ExtensionError(f"{name}: 'sensitive_paths' must be a list")
    for i, entry in enumerate(entries):
        where = f"{name}: sensitive_paths[{i}]"
        if not isinstance(entry, dict) or set(entry) - {"id", "pattern", "reason"}:
            raise ExtensionError(f"{where} must be an object with id, pattern and reason")
        rule_id, pattern, reason = entry.get("id"), entry.get("pattern"), entry.get("reason", "")
        if not isinstance(rule_id, str) or not ID_RE.fullmatch(rule_id):
            raise ExtensionError(f"{where}: 'id' must be lower-case letters, digits, '.', '_' or '-'")
        if (
            not isinstance(pattern, str)
            or not pattern.strip("*/")
            or len(pattern) > 200
            or ".." in pattern.split("/")
            or ("/" in pattern and not pattern.endswith("/**"))
        ):
            raise ExtensionError(
                f"{where}: 'pattern' must be a file name pattern such as payroll*.csv or a folder like data/**"
            )
        if not isinstance(reason, str) or len(reason) > MAX_REASON or "\n" in reason:
            raise ExtensionError(f"{where}: 'reason' must be one line of text, at most {MAX_REASON} characters")
        code = "PROJECT_PATH_" + re.sub(r"[^A-Z0-9]", "_", rule_id.upper())
        paths.append((code, pattern, reason or "A project guard rule protects this path."))

    scratch = [
        _scratch_entry(s, f"{name}: scratch_folders[{i}]")
        for i, s in enumerate(_text_list(data.get("scratch_folders", []), f"{name}: 'scratch_folders'", ExtensionError))
    ]
    codes = [r["code"] for r in commands] + [p[0] for p in paths]
    if len(codes) != len(set(codes)):
        raise ExtensionError(f"{name}: an id is used twice")
    return commands, paths, scratch


def load(root: Path) -> Rules:
    """Built-in rules plus the project's additions. Raises ConfigError (module bug) or ExtensionError (project file)."""
    rules, scratch, hosts = load_builtin()
    for rule in rules:
        rule["code"] = rule["id"]
    ext_commands, ext_paths, ext_scratch = load_extension(root)
    return Rules(tuple(rules + ext_commands), tuple(scratch + ext_scratch), tuple(hosts), tuple(ext_paths))
