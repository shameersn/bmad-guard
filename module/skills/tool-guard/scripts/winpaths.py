# /// script
# requires-python = ">=3.10"
# ///
"""Windows path helpers for the guards (GUARD-3 T9, ADR 0009). Strings only; the one disk call is `realpath`.

Windows paths reach the guards in more shapes than POSIX paths: `C:\\proj\\x` and `C:/proj/x` in the same word, Git
Bash paths (`/c/Users/x`), `\\\\?\\C:\\x`, NTFS streams (`file::$DATA`), trailing dots and spaces that Windows drops,
and names that differ only by case. These functions put a path in one comparable form.

`IS_WINDOWS` is false on macOS and Linux. Then every function does what the guards did before, or nothing:
POSIX behaviour does not change. Tests switch `IS_WINDOWS` and `os.path` (to `ntpath`) to simulate Windows on a Mac.
Not verified on a real Windows machine (docs/guides/windows-check.md).
"""

from __future__ import annotations

import os
import re
import tempfile

IS_WINDOWS = os.name == "nt"

# Git Bash `/c/x`, Cygwin `/cygdrive/c/x`, WSL-style `/mnt/c/x`: a one-letter drive folder at the root.
_MSYS_RE = re.compile(r"^/(?:cygdrive/|mnt/)?([A-Za-z])(?:/(.*))?$", re.S)
_WIN_VAR_RE = re.compile(
    r"^(?:\$(USERPROFILE|APPDATA|LOCALAPPDATA)|\$\{(USERPROFILE|APPDATA|LOCALAPPDATA)\}"
    r"|%(USERPROFILE|APPDATA|LOCALAPPDATA)%|\$env:(USERPROFILE|APPDATA|LOCALAPPDATA))(?=$|[\\/])",
    re.I,
)


def is_device(text: str) -> bool:
    """A Windows device path (`\\\\.\\PhysicalDrive0`, `//./C:`). Windows only. The guards cannot check it: block."""
    return IS_WINDOWS and text.startswith(("\\\\.\\", "//./"))


def native(text: str) -> str:
    """Windows only: a Git Bash path (`/c/x`, `/cygdrive/c/x`, `/mnt/c/x`) becomes `C:\\x`; `/tmp` becomes the temp
    folder. Anything else stays as it is. Without this, `os.path.join(cwd, "/c/x")` gives `C:\\c\\x`."""
    if not IS_WINDOWS:
        return text
    match = _MSYS_RE.match(text)
    if match:
        return f"{match.group(1).upper()}:\\" + (match.group(2) or "").replace("/", "\\")
    if text == "/tmp" or text.startswith("/tmp/"):  # noqa: S108 - this is the Git Bash name, not a file we use
        return os.path.join(tempfile.gettempdir(), text[5:].replace("/", "\\"))
    return text


def strip_win_tricks(path: str) -> str:
    """Windows only: drop the `\\\\?\\` prefix, an NTFS stream suffix on the last name (`:name`, `::$DATA`) and the
    dots and spaces at the end of each name (Windows ignores them: `approval-state.json.` is `approval-state.json`)."""
    if not IS_WINDOWS:
        return path
    if path.startswith(("\\\\?\\", "//?/")):
        path = path[4:]
    drive, rest = os.path.splitdrive(path)
    names = re.split(r"[\\/]", rest)
    for i, name in enumerate(names):
        if i == len(names) - 1:
            name = name.split(":", 1)[0]
        if name not in (".", ".."):
            name = name.rstrip(". ")
        names[i] = name
    return drive + "\\".join(names)


def start_dir(cwd, root):
    """The folder a hook call starts in: the input's `cwd` when it is an absolute path text (Windows: a Git Bash form
    is made native first), else `root`. POSIX: what the guards did before (an absolute `cwd` text, else `root`)."""
    if isinstance(cwd, str) and cwd:
        cwd = native(cwd)
        if os.path.isabs(cwd):
            return cwd
    return root


def clean(text: str) -> str:
    """`native()` then `strip_win_tricks()`: the text to join to a folder."""
    return strip_win_tricks(native(text))


def key(path: str) -> str:
    """The form to compare paths in: real path, normal, case folded where the OS ignores case."""
    return os.path.normcase(os.path.normpath(os.path.realpath(clean(path))))


def name_of(text: str) -> str:
    """The last name of a path text. POSIX: split on `/`. Windows: on `/` and `\\`, minus tricks."""
    if not IS_WINDOWS:
        return text.rstrip("/").rsplit("/", 1)[-1]
    return strip_win_tricks(re.split(r"[\\/]", text.rstrip("\\/"))[-1])


def expand_home(text: str, home: str) -> str:
    """`~`, `~/`, `$HOME` and `${HOME}` at the start of `text` become `home`. Windows also: `~\\`, `$USERPROFILE`,
    `${USERPROFILE}`, `%USERPROFILE%`, `$env:USERPROFILE`, and `$APPDATA` / `$LOCALAPPDATA` (and the same forms)
    from the environment."""
    for var in ("${HOME}", "$HOME"):
        if text.startswith(var):
            return home + text[len(var) :]
    if text == "~" or text.startswith(("~/", "~\\") if IS_WINDOWS else "~/"):
        return home + text[1:]
    if IS_WINDOWS:
        match = _WIN_VAR_RE.match(text)
        if match:
            name = next(g for g in match.groups() if g).upper()
            value = home if name == "USERPROFILE" else os.environ.get(name, "")
            if value:
                return value + text[match.end() :]
    return text
