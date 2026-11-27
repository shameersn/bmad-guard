# /// script
# requires-python = ">=3.10"
# ///
"""A temporary HOME for tests that run hooks or scripts, so none of them can read the real home folder.

The sender reads `~/.config/bmad-guard/` (ADR 0007). A test that starts a hook or script with the real HOME could pick
up a real file there, so it uses `isolated_env()` instead of a copy of `os.environ`. Fake data only.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

FAKE_HOME = os.path.realpath(tempfile.mkdtemp(prefix="bmad-guard-test-home-"))
atexit.register(shutil.rmtree, FAKE_HOME, ignore_errors=True)


def isolated_env() -> dict[str, str]:
    """The process environment without collector settings or an outer project dir, with HOME and USERPROFILE set to a
    temp folder."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BMAD_GUARD_", "CLAUDE_PROJECT_DIR"))}
    env["HOME"] = FAKE_HOME
    env["USERPROFILE"] = FAKE_HOME  # the home folder on Windows (ADR 0009); harmless elsewhere
    return env
