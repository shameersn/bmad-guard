# /// script
# requires-python = ">=3.10"
# ///
"""The marker that makes the hooks guard a test project (ADR 0008): the setup manifest.

The manifest is `_bmad/custom/guard/manifest.json`. Without it a hook entry treats the project as not using the guard
and allows everything (the unguarded-project rule, tests/test_unguarded.py). Tests that drive a hook entry in a fake
project call `mark_guarded()` first.
"""

from __future__ import annotations

from pathlib import Path

MANIFEST_REL = "_bmad/custom/guard/manifest.json"


def mark_guarded(project: Path) -> Path:
    """Write an empty setup manifest into `project` (no file list, so no guard zone changes). Returns its path."""
    path = Path(project) / MANIFEST_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('{"version": "test", "files": {}}\n', encoding="utf-8")
    return path
