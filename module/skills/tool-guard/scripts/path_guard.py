# /// script
# requires-python = ">=3.10"
# ///
"""Sensitive-path guard (Phase 2, T2): secrets, keys, dumps and guard settings. Fails closed.

The list is `SENSITIVE_PATHS` in hook_wiring.py: the same patterns that go into `permissions.deny`. A project can add
patterns in _bmad/custom/project/guard/tool-rules.json (`sensitive_paths`); it cannot remove one. Checked on:

  Read, Edit, MultiEdit, Write, NotebookEdit   file_path / notebook_path
  Grep                              path and glob (not the search pattern, which is text)
  Glob                              pattern and path
  Bash                              every word and redirect target of every command, also inside `sh -c`, `$(...)`,
                                    pipes and `sudo`. The text of `echo` / `printf` and the search pattern of
                                    `grep` are not paths. A command that cannot be read is reported by command_guard.

The reason code is the rule id (PATH_ENV_FILE, PATH_PRIVATE_KEY, ... or PROJECT_PATH_<ID>). The path is never put in
the event or the message: the caller knows what it asked for.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import guard_config as gc  # noqa: E402 - sibling modules, found through the line above
import hook_common as hc  # noqa: E402
import hook_wiring as hw  # noqa: E402
import shell_parse as sp  # noqa: E402

GUARD = "path-guard"
FILE_KEYS = {
    "Read": ("file_path",),
    "Edit": ("file_path",),
    "MultiEdit": ("file_path",),
    "Write": ("file_path",),
    "NotebookEdit": ("notebook_path",),
    "Grep": ("path", "glob"),
    "Glob": ("pattern", "path"),
}
REASONS = {
    "PATH_ENV_FILE": "Environment files hold secrets. Read .env.example for the names, or ask the developer.",
    "PATH_PRIVATE_KEY": "Private keys and certificates are secrets. Do not read or write them; ask the developer.",
    "PATH_KEY_STORE": "A key store (.p12) holds private keys. Do not read or write it; ask the developer.",
    "PATH_DB_DUMP": "A database dump can hold real data, including personal data. Ask the developer.",
    "PATH_DB_BACKUP": "A database backup can hold real data, including personal data. Ask the developer.",
    "PATH_AWS_DIR": "~/.aws holds cloud credentials. Do not read or write it; ask the developer.",
    "PATH_SSH_DIR": "~/.ssh holds SSH keys and host settings. Do not read or write it; ask the developer.",
    "PATH_GUARD_CONFIG": "~/.config/bmad-guard holds the guard's own settings and credentials. Do not touch it.",
}
GREP_COMMANDS = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
TEXT_COMMANDS = {"echo", "printf"}
GIT_TEXT_OPTIONS = {
    "-m",
    "-S",
    "-G",
    "--message",
    "--grep",
    "--author",
    "--committer",
    "--pretty",
    "--format",
    "--date",
    "--since",
    "--until",
    "--exec",
}
GREP_VALUE_OPTIONS = {"-m", "-A", "-B", "-C", "-d", "-D", "--max-count", "--after-context", "--before-context"}
GREP_PATTERN_OPTIONS = {"-e", "--regexp"}
KEY_VALUE_RE = re.compile(r"^-{0,2}[A-Za-z_][A-Za-z0-9_-]*=")
ANCHOR_RE = re.compile(r"[a-z0-9_]{2,}")


@dataclass(frozen=True)
class NameRule:
    code: str
    glob: str  # lower-case file name pattern, matched at any depth
    carve_outs: tuple[str, ...]


@dataclass(frozen=True)
class DirRule:
    code: str
    folder: str  # absolute, real path


def _has_wildcard(text: str) -> bool:
    return any(c in text for c in "*?[")


class Matcher:
    """All sensitive path rules for one call: the built-in list, the project's additions, the home folder."""

    def __init__(self, root: Path, cwd: Path, extra: tuple[tuple[str, str, str], ...] = ()):
        self.cwd = Path(os.path.realpath(cwd))
        self.home = os.path.realpath(Path.home())
        self.reasons = dict(REASONS)
        self.names: list[NameRule] = []
        self.dirs: list[DirRule] = []
        for code, patterns, carve_outs in hw.SENSITIVE_PATHS:
            for pattern in patterns:
                self._add(code, pattern, carve_outs, root)
        for code, pattern, reason in extra:
            self.reasons[code] = reason
            self._add(code, pattern, (), root)

    def _add(self, code: str, pattern: str, carve_outs: tuple[str, ...], root: Path) -> None:
        if "/" in pattern:  # "folder/**": the folder and everything in it
            base = pattern[:-3]
            if base.startswith("~/"):
                folder = os.path.join(self.home, base[2:])
            else:
                folder = os.path.join(root, base)  # absolute stays absolute
            self.dirs.append(DirRule(code, os.path.realpath(folder)))
        else:
            self.names.append(NameRule(code, pattern.lower(), tuple(c.lower() for c in carve_outs)))

    def _expand(self, text: str) -> str:
        for var in ("${HOME}", "$HOME"):
            if text.startswith(var):
                return self.home + text[len(var) :]
        if text == "~" or text.startswith("~/"):
            return self.home + text[1:]
        return text

    def _name_hits(self, name: str) -> list[str]:
        name = name.lower()
        if not name:
            return []
        glob = _has_wildcard(name)
        found = []
        for rule in self.names:
            if glob:
                witness = rule.glob.replace("*", "x")
                anchors = ANCHOR_RE.findall(rule.glob)
                hit = fnmatch.fnmatchcase(name, rule.glob) or (
                    fnmatch.fnmatchcase(witness, name) and all(a in name for a in anchors)
                )
            else:
                hit = fnmatch.fnmatchcase(name, rule.glob) and name not in rule.carve_outs
            if hit:
                found.append(rule.code)
        return found

    def hits(self, text: str) -> list[str]:
        """Reason codes of the rules this path text matches (a path, a glob, or a path with variables)."""
        found: list[str] = []
        text = self._expand(text.strip())
        if not text:
            return found
        literal = text[: min((text.index(c) for c in "*?[" if c in text), default=len(text))]
        if "$" not in text:
            plain = os.path.normpath(os.path.join(self.cwd, literal))
            real = os.path.realpath(plain)
            for candidate in {plain, real}:
                for rule in self.dirs:
                    if candidate == rule.folder or candidate.startswith(rule.folder + os.sep):
                        found.append(rule.code)
            if real != plain and literal == text:  # a link to a sensitive file name
                found += self._name_hits(os.path.basename(real))
        name = text.rstrip("/").rsplit("/", 1)[-1]
        if not name.startswith("$"):
            found += self._name_hits(name)
        return list(dict.fromkeys(found))

    def word_hits(self, text: str) -> list[str]:
        """Like hits(), and also the value in `--out=path` or `of=path`."""
        found = self.hits(text)
        if KEY_VALUE_RE.match(text):
            found += self.hits(text.partition("=")[2])
        return list(dict.fromkeys(found))


def _grep_exempt(args: list[sp.Word]) -> set[int]:
    """Positions of words in a grep-like command that are search patterns, not paths."""
    skip: set[int] = set()
    has_pattern_option = False
    first_positional = None
    i = 0
    while i < len(args):
        text = args[i].text
        if text in GREP_PATTERN_OPTIONS:
            has_pattern_option = True
            skip.add(i + 1)
            i += 2
            continue
        if text in ("-f", "--file"):  # patterns come from a file: the file is a path, and no word is a pattern
            has_pattern_option = True
        if text.startswith("--regexp=") or (text.startswith("-e") and len(text) > 2 and not text.startswith("--")):
            has_pattern_option = True
            skip.add(i)
        elif text in GREP_VALUE_OPTIONS:
            i += 2
            continue
        elif not text.startswith("-") and first_positional is None:
            first_positional = i
        i += 1
    if not has_pattern_option and first_positional is not None:
        skip.add(first_positional)
    return skip


def _git_text(args: list[sp.Word]) -> set[int]:
    """Positions of words in a git command that are text, not paths: messages, -S/-G strings, git grep patterns."""
    skip: set[int] = set()
    for i, word in enumerate(args):
        key, eq, _ = word.text.partition("=")
        if word.text in GIT_TEXT_OPTIONS:
            skip.update({i, i + 1})
        elif eq and key in GIT_TEXT_OPTIONS:
            skip.add(i)
        elif len(word.text) > 2 and word.text[:2] in GIT_TEXT_OPTIONS and word.text[1] != "-":  # -G.env, -mtext
            skip.add(i)
    _, sub, rest = sp.git_split(args)
    if sub == "grep":
        offset = len(args) - len(rest)
        skip |= {offset + k for k in _grep_exempt(rest)}
    return skip


def bash_words(invocations: list[sp.Invocation]) -> list[str]:
    words: list[str] = []
    for inv in invocations:
        name = inv.name.lower()
        args = inv.args
        skip: set[int] = set()
        if name in GREP_COMMANDS:
            skip = _grep_exempt(args)
        elif name == "git":
            skip = _git_text(args)
        if name not in TEXT_COMMANDS:
            words += [w.text for i, w in enumerate(args) if i not in skip]
        words += [w.text for op, w in inv.redirects if not op.endswith("&")]
    return words


def check(payload: dict, root: Path) -> list[hc.Verdict]:
    tool, tool_input = payload["tool_name"], payload["tool_input"]
    try:
        extra = gc.load_extension(root)[1]
    except gc.ExtensionError as err:
        if tool == "Bash":  # command_guard reports it once
            return []
        return [hc.Verdict("EXT_BAD_FILE", f"{err}. Fix the file by hand; this tool is blocked until then.", GUARD)]
    cwd = payload.get("cwd")
    start = Path(cwd) if isinstance(cwd, str) and cwd and Path(cwd).is_absolute() else root
    matcher = Matcher(root, start, tuple(extra))
    if tool == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return []
        try:
            words = bash_words(list(sp.walk(command)))
        except sp.ParseError:
            return []  # command_guard blocks a command it cannot read
    else:
        words = [v for key in FILE_KEYS.get(tool, ()) for v in [tool_input.get(key)] if isinstance(v, str)]
    codes: list[str] = []
    for word in words:
        for code in matcher.word_hits(word):
            if code not in codes:
                codes.append(code)
    return [hc.Verdict(code, matcher.reasons.get(code, "This path is protected."), GUARD) for code in codes]


GUARDS = [hc.Guard(GUARD, tuple(FILE_KEYS) + ("Bash",), check)]
