# /// script
# requires-python = ">=3.10"
# ///
"""The optional tech-stack list and the dependency files it is checked against (Phase 2, T6). Pure text logic, no I/O.

The list is `_bmad/custom/project/guard/tech-stack.toml`. It names the packages a project may use, per ecosystem:

    ---
    status: draft
    ---
    # Approved by the architect with Warden (agent-approval-grant, doc type tech-stack).
    [allowed]
    npm = ["react", "react-dom", "@types/*"]
    python = [
        "django",
        "psycopg2-binary",
    ]

- Only the table `[allowed]` and the keys `npm` and `python`. Each value is a list of package names. `*` matches any
  characters (`@types/*`). Versions are not checked. A name is compared after normalising it: lower case for npm, and
  PEP 503 (`_`, `.` and `-` are the same) for Python.
- An ecosystem that is not in the file is not governed: its dependency files get a warning, not a block. An ecosystem
  with an empty list allows nothing new.
- The file starts with a small `---` block, because Warden approves a file through its frontmatter (`status`,
  `version`). It is not plain TOML for other tools. The block is cut off before the list is read.
- The reader is small and has no TOML library (Python 3.10 has none). It reads tables, `key = value`, comments,
  strings and lists that run over several lines. Any other shape raises `StackError`, and the hook blocks.

Dependency files that are read: `package.json` (dependencies, devDependencies, peerDependencies,
optionalDependencies; `workspace:`, `file:` and `link:` entries are local code, `npm:` aliases count under the real
name), `requirements*.txt` and `requirements*.in`, any `*.txt` in a folder called `requirements`, and `pyproject.toml`
(PEP 621 `[project]` dependencies and optional-dependencies, PEP 735 `[dependency-groups]`, and Poetry's dependency
tables). Other dependency managers' files (go.mod, Cargo.toml, ...) are only named, so the hook can warn about them.
"""

from __future__ import annotations

import fnmatch
import json
import posixpath
import re

ECOSYSTEMS = ("npm", "python")
NPM_FIELDS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
LOCAL_SPECS = ("workspace:", "file:", "link:")
OTHER_NAMES = {
    "go.mod",
    "Cargo.toml",
    "Gemfile",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "Pipfile",
    "setup.py",
    "setup.cfg",
    "environment.yml",
    "environment.yaml",
    "packages.config",
    "mix.exs",
    "pubspec.yaml",
    "Podfile",
    "Package.swift",
    "build.sbt",
}
OTHER_SUFFIXES = (".csproj", ".fsproj", ".vbproj", ".gemspec")
MAX_ENTRIES = 5000
MAX_NAME = 214  # npm's own limit
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)  # the same pattern Warden uses
ALLOWED_RE = re.compile(r"[A-Za-z0-9@][A-Za-z0-9._/*@~+-]*")
SPEC_NAME_RE = re.compile(r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)(?=\s|\[|[<>=!~@(;]|$)")
EGG_RE = re.compile(r"#egg=([A-Za-z0-9][A-Za-z0-9._-]*)")


class StackError(ValueError):
    """A file cannot be read. The message names the problem and never repeats file content."""


# --- a small TOML reader -----------------------------------------------------------------------------------------


def strip_frontmatter(text: str) -> str:
    match = FRONTMATTER_RE.match(text)
    return text[match.end() :] if match else text


def logical_lines(text: str) -> list[str]:
    """The lines of a TOML file with comments cut off and every list or inline table joined into one line."""
    lines: list[str] = []
    cur: list[str] = []
    quote, depth, i, n = "", 0, 0, len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\" and quote[0] == '"':
                cur.append(text[i : i + 2])
                i += 2
            elif text.startswith(quote, i):
                cur.append(quote)
                i += len(quote)
                quote = ""
            elif ch == "\n" and len(quote) == 1:
                raise StackError("a string is not closed on its line")
            else:
                cur.append(ch)
                i += 1
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith(('"""', "'''"), i):
            quote = text[i : i + 3]
            cur.append(quote)
            i += 3
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth < 0:
                raise StackError("a closing bracket has no opening one")
        if ch == "\n" and depth == 0:
            lines.append("".join(cur).strip())
            cur = []
        else:
            cur.append(" " if ch == "\n" else ch)
        i += 1
    if quote or depth:
        raise StackError("a string, list or table is not closed")
    lines.append("".join(cur).strip())
    return [line for line in lines if line]


def _unquote(part: str) -> str:
    part = part.strip()
    if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
        return part[1:-1]
    return part


def split_dotted(key: str) -> tuple[str, ...]:
    """`a.b."c.d"` -> ("a", "b", "c.d")."""
    parts: list[str] = []
    cur: list[str] = []
    quote = ""
    for ch in key:
        if quote:
            quote = "" if ch == quote else quote
            cur.append(ch)
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch == ".":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    names = tuple(_unquote(p) for p in parts)
    if not all(names):
        raise StackError("a key is empty")
    return names


def _split_assignment(line: str) -> tuple[str, str]:
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "=":
            return line[:i].strip(), line[i + 1 :].strip()
    raise StackError("a line is neither a table header nor key = value")


def toml_items(text: str) -> list[tuple[tuple[str, ...], str]]:
    """(full key path, value text) for every `key = value` line. The path holds the table name and the key."""
    table: tuple[str, ...] = ()
    items: list[tuple[tuple[str, ...], str]] = []
    for line in logical_lines(text):
        if line.startswith("["):
            if not line.endswith("]"):
                raise StackError("a table header is not closed")
            table = split_dotted(line.strip("[]").strip())
        else:
            key, value = _split_assignment(line)
            items.append((table + split_dotted(key), value))
    return items


def string_items(value: str, strict: bool) -> list[str]:
    """The text entries of a TOML list. `strict`: anything but text raises; otherwise other entries are skipped."""
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        raise StackError("a value must be a list in [ ]")
    body, items, i = value[1:-1], [], 0
    while i < len(body):
        ch = body[i]
        if ch in " \t,":
            i += 1
        elif ch in "\"'" and not body.startswith(ch * 3, i):
            end = i + 1
            while end < len(body) and body[end] != ch:
                end += 2 if (ch == '"' and body[end] == "\\") else 1
            if end >= len(body):
                raise StackError("a string in a list is not closed")
            raw = body[i + 1 : end]
            try:
                items.append(json.loads(f'"{raw}"') if ch == '"' else raw)
            except ValueError as err:
                raise StackError("a string in a list has an escape this reader does not know") from err
            i = end + 1
        elif strict:
            raise StackError('a list may hold text entries only, one "name" each')
        else:  # an inline table or another value: skip to the next comma outside brackets and quotes
            depth, quote = 0, ""
            while i < len(body) and not (body[i] == "," and depth == 0 and not quote):
                c = body[i]
                if quote:
                    quote = "" if c == quote else quote
                elif c in "\"'":
                    quote = c
                elif c in "[{":
                    depth += 1
                elif c in "]}":
                    depth -= 1
                i += 1
    return items


# --- the list ----------------------------------------------------------------------------------------------------


def normalize(ecosystem: str, name: str) -> str:
    name = name.strip().lower()
    return re.sub(r"[-_.]+", "-", name) if ecosystem == "python" else name


def parse_list(text: str) -> dict[str, tuple[str, ...]]:
    """ecosystem -> allowed name patterns (normalised). Raises StackError when the file is not a usable list."""
    allowed: dict[str, tuple[str, ...]] = {}
    for path, value in toml_items(strip_frontmatter(text)):
        if len(path) != 2 or path[0] != "allowed":
            raise StackError(
                f"'{'.'.join(path)[:40]}': the file holds an [allowed] table with npm and python lists only"
            )
        eco = path[1]
        if eco not in ECOSYSTEMS:
            raise StackError(f"unknown ecosystem '{eco[:40]}' in [allowed] (use {' or '.join(ECOSYSTEMS)})")
        if eco in allowed:
            raise StackError(f"'{eco}' is given twice")
        names = string_items(value, strict=True)
        if len(names) > MAX_ENTRIES or not all(len(n) <= MAX_NAME and ALLOWED_RE.fullmatch(n) for n in names):
            raise StackError(
                f"'{eco}' must be a list of package names (letters, digits and . _ - / @ ~ + *), "
                f"at most {MAX_ENTRIES}, each up to {MAX_NAME} characters"
            )
        allowed[eco] = tuple(sorted({normalize(eco, n) for n in names}))
    if not allowed:
        raise StackError("the file has no [allowed] lists (npm = [...] or python = [...])")
    return allowed


def is_allowed(patterns: tuple[str, ...], ecosystem: str, name: str) -> bool:
    name = normalize(ecosystem, name)
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


# --- dependency files --------------------------------------------------------------------------------------------


def kind_of(rel: str) -> str | None:
    """ "npm", "python" (requirements and pyproject.toml), "other" (a dependency file we only name) or None."""
    parts = rel.split("/")
    base = parts[-1]
    if "node_modules" in parts:
        return None
    if base == "package.json":
        return "npm"
    if base == "pyproject.toml" or fnmatch.fnmatchcase(base, "requirements*.txt"):
        return "python"
    if fnmatch.fnmatchcase(base, "requirements*.in") or (base.endswith(".txt") and "requirements" in parts[:-1]):
        return "python"
    if base in OTHER_NAMES or base.endswith(OTHER_SUFFIXES):
        return "other"
    return None


def spec_name(spec: str) -> str | None:
    """The package name of a requirement line (PEP 508), or None for a local path. Raises StackError without a name."""
    spec = spec.split(";", 1)[0].strip()
    egg = EGG_RE.search(spec)
    if egg:
        return egg.group(1)
    if spec.startswith((".", "/", "file:", "~")):
        return None
    match = SPEC_NAME_RE.match(spec)
    if not match:
        raise StackError("a requirement has no package name")
    return match.group(1)


def _requirements_names(text: str) -> set[str]:
    names: set[str] = set()
    for number, raw in enumerate(text.replace("\\\n", " ").splitlines(), 1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):  # an option; only an editable install names a package
            editable = re.match(r"(?:-e|--editable)(?:\s+|=)(.+)$", line)
            if not editable:
                continue
            line = editable.group(1).strip()
        try:
            name = spec_name(line)
        except StackError as err:
            raise StackError(f"line {number}: {err}") from err
        if name:
            names.add(normalize("python", name))
    return names


def _poetry_name(path: tuple[str, ...]) -> str:
    """The package a Poetry dependency key names, or "" when the key is not a Poetry dependency."""
    if path[:3] in (("tool", "poetry", "dependencies"), ("tool", "poetry", "dev-dependencies")) and len(path) >= 4:
        return path[3]
    if path[:3] == ("tool", "poetry", "group") and len(path) >= 6 and path[4] == "dependencies":
        return path[5]
    return ""


def _pyproject_names(text: str) -> set[str]:
    names: set[str] = set()
    for path, value in toml_items(text):
        listed: list[str] = []
        if path == ("project", "dependencies") or (path[:2] == ("project", "optional-dependencies") and len(path) >= 3):
            listed = string_items(value, strict=False)
        elif path[0] == "dependency-groups" and len(path) >= 2:
            listed = string_items(value, strict=False)
        else:
            poetry = _poetry_name(path)
            if poetry and poetry.lower() != "python":
                names.add(normalize("python", poetry))
        for spec in listed:
            name = spec_name(spec)
            if name:
                names.add(normalize("python", name))
    return names


def _npm_names(text: str) -> set[str]:
    try:
        data = json.loads(text)
    except ValueError as err:
        raise StackError("package.json is not valid JSON") from err
    if not isinstance(data, dict):
        raise StackError("package.json must hold a JSON object")
    names: set[str] = set()
    for field in NPM_FIELDS:
        section = data.get(field)
        if section is None:
            continue
        if not isinstance(section, dict):
            raise StackError(f"{field} in package.json must be an object")
        for name, spec in section.items():
            if isinstance(spec, str) and spec.startswith(LOCAL_SPECS):
                continue
            names.add(normalize("npm", name))
            if isinstance(spec, str) and spec.startswith("npm:"):  # an alias: the real package is named in the spec
                real = spec[4:]
                at = real.rfind("@")
                names.add(normalize("npm", real[:at] if at > 0 else real))
    return names


def dependency_names(rel: str, text: str) -> set[str]:
    """Normalised names a dependency file asks for. Raises StackError when the file cannot be read."""
    kind = kind_of(rel)
    if kind == "npm":
        return _npm_names(text)
    if posixpath.basename(rel) == "pyproject.toml":
        return _pyproject_names(text)
    if kind == "python":
        return _requirements_names(text)
    raise StackError("this file is not a dependency file that is read")


def new_dependencies(rel: str, old: str | None, new: str) -> set[str]:
    """Names in the new file that the old one did not have. An old file that cannot be read counts as empty."""
    try:
        before = dependency_names(rel, old) if old else set()
    except StackError:
        before = set()
    return dependency_names(rel, new) - before
