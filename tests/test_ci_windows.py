# /// script
# requires-python = ">=3.10"
# ///
"""The windows-latest job in .github/workflows/ci.yml (GUARD-3 T9): it must name tests that exist, and it must not touch
the ubuntu jobs. The job itself has never run on Windows; this only keeps its list honest."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

CI = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"


def job(text: str, name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  (?:\w[\w-]*:\n|\#)|\Z)", text, re.S | re.M)
    return match.group(1) if match else ""


class WindowsJobTests(unittest.TestCase):
    def setUp(self):
        self.text = CI.read_text(encoding="utf-8")
        self.windows = job(self.text, "windows")

    def test_there_is_a_separate_windows_job(self):
        self.assertIn("runs-on: windows-latest", self.windows)
        self.assertNotIn("windows-latest", job(self.text, "test") + job(self.text, "lint"))

    def test_the_ubuntu_jobs_are_as_they_were(self):
        self.assertIn("runs-on: ubuntu-latest", job(self.text, "test"))
        self.assertIn('python: ["3.10", "3.13"]', job(self.text, "test"))
        self.assertIn("python -m unittest discover -v", job(self.text, "test"))
        self.assertIn("ruff check .", job(self.text, "lint"))

    def test_every_listed_test_name_can_be_loaded(self):
        names = re.findall(r"^\s+(tests\.[\w.]+)", self.windows, re.M)
        self.assertGreaterEqual(len(names), 10)
        for name in names:
            with self.subTest(name=name):
                suite = unittest.defaultTestLoader.loadTestsFromName(name)
                self.assertGreater(suite.countTestCases(), 0)

    def test_the_job_says_it_has_not_run_and_is_allowed_to_fail_until_it_is_green(self):
        self.assertIn("NEVER RUN", self.text)
        self.assertIn("continue-on-error: true", self.windows)


if __name__ == "__main__":
    unittest.main()
