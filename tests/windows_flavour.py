# /// script
# requires-python = ">=3.10"
# ///
"""A simulated Windows flavour for tests that run on macOS or Linux (GUARD-3 T9, ADR 0009).

`windows()` makes `os.path` the `ntpath` module, sets `winpaths.IS_WINDOWS`, and gives a fake home folder and a fake
temp folder. It is a simulation: it checks our string logic, not Windows. Nothing here says the guards work on Windows.
Keep the block short: while it is open, every `os.path` call in the process is `ntpath`. Fake data only.
"""

from __future__ import annotations

import contextlib
import ntpath
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "module/skills/tool-guard/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import winpaths  # noqa: E402 - found through the sys.path line above

POSIX_ONLY = unittest.skipIf(sys.platform == "win32", "a control for the behaviour off Windows")
HOME = "C:\\Users\\tester"
TEMP = "C:\\Users\\tester\\AppData\\Local\\Temp"


@contextlib.contextmanager
def windows(home: str = HOME, temp: str = TEMP, env: dict | None = None):
    """Simulate Windows: `os.path` is ntpath, `IS_WINDOWS` is true, `Path.home()` is `home`, the temp folder is `temp`.
    `env` is added to the environment (for `$APPDATA`)."""
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(os, "path", ntpath))
        stack.enter_context(mock.patch.object(winpaths, "IS_WINDOWS", True))
        stack.enter_context(mock.patch.object(Path, "home", return_value=Path(home)))
        stack.enter_context(mock.patch.object(tempfile, "gettempdir", return_value=temp))
        if env:
            stack.enter_context(mock.patch.dict(os.environ, env))
        yield
