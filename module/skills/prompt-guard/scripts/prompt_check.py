# /// script
# requires-python = ">=3.10"
# ///
"""Check prompt text locally for secrets, personal / health identifiers, prompt injection and size (ADR 0006).

The text is read from stdin, never from the command line (a command line is visible to other users in
the process list). Nothing is sent anywhere.

Prints one JSON object, and never any part of the text:
  {"result": "allow" | "warn" | "block",
   "findings": [{"category": "secrets", "rule_id": "secret.github-token"}, ...],
   "counts": {"secrets": 1, "phi": 0, "injection": 0, "size": 0}}

Actions per category come from the skill's customize.toml, then the project's
_bmad/custom/prompt-guard.toml (team layer, owned by the project), then
_bmad/custom/prompt-guard.user.toml (personal layer; it may only make an action stricter):
  block   the result is "block"
  warn    the result is at least "warn"
  record  the finding is listed and recorded as an event; the result stays "allow"
  off     the category is not reported

Project rules and allow-listed fakes: _bmad/custom/project/guard/prompt-patterns.json
  {"patterns": [{"id": "mrn", "category": "phi", "regex": "MRN-\\d{8}"}], "allow": ["MRN-00000000"]}
A bad file or entry is skipped with a warning on stderr; the built-in rules still run.

Every warn, block or record result is one guard event (guard "prompt-guard") with categories and rule
ids only. Exit code 0 for every result; 2 for bad arguments.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import prompt_rules as pr  # noqa: E402 - sibling module, found through the line above

GUARD = "prompt-guard"
STAGE = "prompt"
SKILL_ROOT = HERE.parent
PATTERNS_REL = Path("_bmad/custom/project/guard/prompt-patterns.json")
CATEGORIES = ("secrets", "phi", "injection", "size")
ACTIONS = ("off", "record", "warn", "block")  # weakest to strictest
REASON_CODES = {"secrets": "PROMPT_SECRET", "phi": "PROMPT_PHI", "injection": "PROMPT_INJECTION", "size": "PROMPT_SIZE"}
DEFAULTS = {"secrets": "block", "phi": "warn", "injection": "record", "size": "warn", "max_prompt_tokens": "8000"}
SCALAR_RE = re.compile(r'^\s*(secrets|phi|injection|size|max_prompt_tokens)\s*=\s*"?([^"#]*?)"?\s*(#.*)?$')
TABLE_RE = re.compile(r"^\s*\[([^\]]*)\]\s*(#.*)?$")
ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
MAX_PATTERN_CHARS = 500
MIN_ALLOW_CHARS = 4


# --- config ----------------------------------------------------------------------------------------


def read_scalars(path: Path) -> dict:
    """Read the few scalar keys this check needs from the [agent] table (or the top level).

    Simple on purpose: Python 3.10 has no TOML library.
    """
    found: dict[str, str] = {}
    if not path.is_file():
        return found
    table = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        header = TABLE_RE.match(line)
        if header:
            table = header.group(1).strip()
            continue
        match = SCALAR_RE.match(line)
        if match and table in ("", "agent"):
            found[match.group(1)] = match.group(2).strip()
    return found


def load_config(root: Path) -> tuple[dict, list[str]]:
    """Merge base, team and personal layers. The personal layer may only tighten an action."""
    config = dict(DEFAULTS)
    warnings: list[str] = []
    layers = (
        (SKILL_ROOT / "customize.toml", False),
        (root / "_bmad/custom/prompt-guard.toml", False),
        (root / "_bmad/custom/prompt-guard.user.toml", True),
    )
    for path, tighten_only in layers:
        for key, value in read_scalars(path).items():
            if key == "max_prompt_tokens":
                if value.isdigit() and int(value) > 0:
                    config[key] = value
                else:
                    warnings.append(f"{path.name}: max_prompt_tokens must be a positive whole number; ignored")
            elif value not in ACTIONS:
                warnings.append(f"{path.name}: {key} must be one of {', '.join(ACTIONS)}; ignored")
            elif not tighten_only or ACTIONS.index(value) > ACTIONS.index(config[key]):
                config[key] = value
    return config, warnings


def is_hipaa(root: Path) -> bool:
    """True when the project is marked HIPAA: `phi = "block"` in the prompt-guard config (the team layer sets it).

    Fails closed: when the setting cannot be read (an unreadable or invalid file), the project counts as HIPAA.
    Used to choose the fail mode of the hooks (a crash blocks in a HIPAA project, warns elsewhere).
    """
    try:
        return load_config(root)[0]["phi"] == "block"
    except Exception:  # any failure to read the setting means "treat as HIPAA"
        return True


def load_project_patterns(root: Path) -> tuple[tuple[pr.Rule, ...], tuple[str, ...], list[str]]:
    """Project rules and allow-list. Bad entries are skipped with a warning; never raises."""
    path = root / PATTERNS_REL
    if not path.is_file():
        return (), (), []
    name = PATTERNS_REL.as_posix()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        return (), (), [f"{name}: not valid JSON ({type(err).__name__}); project patterns skipped"]
    if not isinstance(data, dict):
        return (), (), [f"{name}: must be a JSON object; project patterns skipped"]

    rules: list[pr.Rule] = []
    warnings: list[str] = []
    patterns = data.get("patterns", [])
    if not isinstance(patterns, list):
        warnings.append(f"{name}: 'patterns' must be a list; skipped")
        patterns = []
    for index, entry in enumerate(patterns):
        where = f"{name}: patterns[{index}]"
        if not isinstance(entry, dict):
            warnings.append(f"{where}: must be an object; skipped")
            continue
        rule_id, category, regex = entry.get("id"), entry.get("category"), entry.get("regex")
        if not isinstance(rule_id, str) or not ID_RE.fullmatch(rule_id):
            warnings.append(f"{where}: 'id' must be lower-case letters, digits, '.', '_' or '-'; skipped")
            continue
        if category not in pr.CATEGORIES:
            warnings.append(f"{where} ({rule_id}): 'category' must be one of {', '.join(pr.CATEGORIES)}; skipped")
            continue
        if not isinstance(regex, str) or not regex or len(regex) > MAX_PATTERN_CHARS:
            warnings.append(
                f"{where} ({rule_id}): 'regex' must be text of 1 to {MAX_PATTERN_CHARS} characters; skipped"
            )
            continue
        try:
            compiled = re.compile(regex)
        except re.error:
            warnings.append(f"{where} ({rule_id}): 'regex' does not compile; skipped")
            continue
        rules.append(pr.Rule(f"project.{rule_id}", category, compiled))

    allow: list[str] = []
    entries = data.get("allow", [])
    if not isinstance(entries, list):
        warnings.append(f"{name}: 'allow' must be a list; skipped")
        entries = []
    for index, entry in enumerate(entries):
        if isinstance(entry, str) and len(entry.strip()) >= MIN_ALLOW_CHARS:
            allow.append(entry.strip())
        else:
            warnings.append(f"{name}: allow[{index}] must be text of at least {MIN_ALLOW_CHARS} characters; skipped")
    return tuple(rules), tuple(allow), warnings


# --- check -----------------------------------------------------------------------------------------


def check(text: str, root: Path) -> tuple[dict, list[str]]:
    """Return (result, warnings). The result holds categories, rule ids and counts only."""
    config, warnings = load_config(root)
    extra_rules, allow, pattern_warnings = load_project_patterns(root)
    warnings.extend(pattern_warnings)

    matches = pr.scan(text, extra_rules, allow)
    if pr.estimate_tokens(text) > int(config["max_prompt_tokens"]):
        matches.append(("size", "size.max-tokens"))

    counts = dict.fromkeys(CATEGORIES, 0)
    findings: list[dict] = []
    result = "allow"
    for category, rule_id in matches:
        action = config[category]
        if action == "off":
            continue
        counts[category] += 1
        finding = {"category": category, "rule_id": rule_id}
        if finding not in findings:
            findings.append(finding)
        if action == "block" or (action == "warn" and result == "allow"):
            result = action
    return {"result": result, "findings": findings, "counts": counts}, warnings


def decision_summary(result: dict) -> str:
    """'secrets (secret.github-token); phi (phi.email)' -- categories and rule ids only."""
    parts = []
    for category in CATEGORIES:
        ids = [f["rule_id"] for f in result["findings"] if f["category"] == category]
        if ids:
            parts.append(f"{category} ({', '.join(ids)})")
    return "; ".join(parts)


# --- events ----------------------------------------------------------------------------------------


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


IS_WINDOWS = os.name == "nt"  # tests set it (ADR 0009)


def _native(text: str) -> str:
    """Windows only: a Git Bash path `/c/x` (also `/cygdrive/c/x`, `/mnt/c/x`) becomes `C:\\x`. A copy of
    tool-guard/scripts/hook_common.py: this hook does not import it. A test keeps the two equal."""
    match = re.match(r"^/(?:cygdrive/|mnt/)?([A-Za-z])(?:/(.*))?$", text, re.S) if IS_WINDOWS else None
    return f"{match.group(1).upper()}:\\" + (match.group(2) or "").replace("/", "\\") if match else text


def utf8_output() -> None:
    """Write stdout and stderr as UTF-8 whatever the console code page is (cp1252 on Windows). A copy of
    tool-guard/scripts/hook_common.py; a test keeps the two equal."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass  # a stream that cannot be reconfigured (a test, a closed pipe) is left as it is


def project_root(cwd: Path | None = None) -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(_native(env))
    folder = cwd or Path.cwd()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=folder, capture_output=True, encoding="utf-8", errors="replace"
        )
    except OSError:
        return folder
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else folder


# The unguarded-project rule (ADR 0008): a copy of `guard_state` in tool-guard/scripts/hook_common.py (this hook does
# not import it). A test keeps the two equal.
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


def project_name(root: Path) -> str:
    try:
        data = json.loads((root / "_bmad/custom/project.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("project_name", "")) if isinstance(data, dict) else ""


def emit(verdict: str, reason_code: str, root: Path, agent: str, **fields) -> None:
    """Record one guard event through Ledger. Never raises; warns on stderr when it cannot."""
    try:
        ge = _guard_events()
        if ge is None:
            print(
                "bmad-guard: warning: Ledger (agent-project-logger) not installed; event not recorded", file=sys.stderr
            )
            return
        event = ge.build_event(GUARD, STAGE, verdict, reason_code, project=project_name(root), agent=agent, **fields)
        ge.emit(event, ge.Config.for_project(root))
    except Exception as err:  # logging never changes a decision
        print(f"bmad-guard: warning: event not recorded ({type(err).__name__})", file=sys.stderr)


def record(result: dict, root: Path, agent: str, session_id: str = "") -> None:
    """One event for a result with findings: categories and rule ids, never the text."""
    if not result["findings"]:
        return
    config, _ = load_config(root)
    top = max(
        {f["category"] for f in result["findings"]},
        key=lambda c: (ACTIONS.index(config[c]), -CATEGORIES.index(c)),
    )
    verdict = {"block": "block", "warn": "warn"}.get(result["result"], "info")
    emit(
        verdict,
        REASON_CODES[top],
        root,
        agent,
        result=result["result"],
        categories=",".join(c for c in CATEGORIES if result["counts"][c]),
        rule_ids=",".join(f["rule_id"] for f in result["findings"]),
        counts=json.dumps({k: v for k, v in result["counts"].items() if v}, sort_keys=True),
        session_id=session_id,
    )


# --- CLI -------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check prompt text locally for secrets, personal data, injection and size. "
        "The text is read from stdin; nothing is sent anywhere."
    )
    parser.add_argument("--project-root", help="project folder (default: CLAUDE_PROJECT_DIR, else the git top level)")
    parser.add_argument("--no-event", action="store_true", help="do not record a guard event")
    args = parser.parse_args(argv)

    root = Path(args.project_root) if args.project_root else project_root()
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    result, warnings = check(text, root)
    for warning in warnings:
        print(f"bmad-guard: warning: {warning}", file=sys.stderr)
    if not args.no_event:
        record(result, root, f"{GUARD}/CHK")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
