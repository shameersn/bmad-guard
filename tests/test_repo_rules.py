# /// script
# requires-python = ">=3.10"
# ///
"""Repo rules from ADR 0001 and the Phase 1 plan, checked on every script we ship.

- Every Python file under module/ and scripts/ starts with a PEP 723 header that sets requires-python.
- It imports only the standard library or a sibling module in the same folder.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ("module", "scripts")
HEADER = re.compile(r"\A(?:#!.*\n)?# /// script\n(?:#.*\n)*?# requires-python = \"[^\"]+\"\n(?:#.*\n)*?# ///\n")


def has_pep723_header(source: str) -> bool:
    return bool(HEADER.match(source))


def non_stdlib_imports(source: str, siblings: set[str]) -> list[str]:
    """Return top-level imported names that are neither stdlib nor a sibling module."""
    allowed = set(sys.stdlib_module_names) | siblings | {"__future__"}
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            if top not in allowed:
                found.append(top)
    return found


def shipped_python_files() -> list[Path]:
    files = []
    for folder in SHIPPED:
        files.extend(p for p in (ROOT / folder).rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


class CheckerTests(unittest.TestCase):
    """The checks themselves, so an empty repo still proves they work."""

    def test_header_accepted(self):
        src = '# /// script\n# requires-python = ">=3.10"\n# ///\n"""doc"""\n'
        self.assertTrue(has_pep723_header(src))

    def test_header_after_shebang_accepted(self):
        src = '#!/usr/bin/env python3\n# /// script\n# requires-python = ">=3.10"\n# ///\n'
        self.assertTrue(has_pep723_header(src))

    def test_header_missing_rejected(self):
        self.assertFalse(has_pep723_header('"""doc"""\nimport os\n'))

    def test_header_without_requires_python_rejected(self):
        self.assertFalse(has_pep723_header("# /// script\n# dependencies = []\n# ///\n"))

    def test_stdlib_and_sibling_imports_allowed(self):
        src = "import json, os.path\nfrom pathlib import Path\nimport guard_events\nfrom . import x\n"
        self.assertEqual(non_stdlib_imports(src, {"guard_events"}), [])

    def test_third_party_import_reported(self):
        src = "import requests\nfrom yaml import safe_load\n"
        self.assertEqual(non_stdlib_imports(src, set()), ["requests", "yaml"])


class ShippedFilesTests(unittest.TestCase):
    def test_every_shipped_script_has_pep723_header(self):
        missing = [str(p.relative_to(ROOT)) for p in shipped_python_files() if not has_pep723_header(p.read_text())]
        self.assertEqual(missing, [], "add a PEP 723 header with requires-python")

    def test_every_shipped_script_uses_stdlib_only(self):
        problems = {}
        for path in shipped_python_files():
            siblings = {p.stem for p in path.parent.glob("*.py")}
            bad = non_stdlib_imports(path.read_text(), siblings)
            if bad:
                problems[str(path.relative_to(ROOT))] = bad
        self.assertEqual(problems, {}, "shipped scripts must use the standard library only")


if __name__ == "__main__":
    unittest.main()
