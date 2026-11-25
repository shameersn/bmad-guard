# /// script
# requires-python = ">=3.10"
# ///
"""Quality gate for prompt-guard's built-in rules (ADR 0006): miss rate and false-alarm rate.

Test set: tests/fixtures/prompt-guard/positives.txt (fake prompts that must be caught, with the
expected category) and negatives.txt (ordinary developer prompts, near misses included). {{name}}
placeholders are filled with generated fakes (tests/prompt_guard_fakes.py).

Definitions:
  miss         a positive whose expected category is not among its findings
  false alarm  a negative with ANY finding (record-only injection findings count too)
Limits (agreed in T8; raise them only with a PR that explains why):
  miss rate         0%  -- every positive in the set must be caught
  false-alarm rate  <= 5%
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from tests.prompt_guard_fakes import Fakes, fill
from tests.test_prompt_guard import pc, pr

FIXTURES = Path(__file__).resolve().parent / "fixtures/prompt-guard"
MAX_MISS_RATE = 0.0
MAX_FALSE_ALARM_RATE = 0.05
MIN_SAMPLES = 40


def samples(name: str) -> list[str]:
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.strip() and not line.startswith("#")]


class QualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = Fakes().values(pr.luhn_valid, pr.nhs_valid, pr.verhoeff_valid)
        cls._tmp = tempfile.TemporaryDirectory()
        cls.project = Path(cls._tmp.name)  # no project config: built-in rules and default actions

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def categories(self, text: str) -> set[str]:
        result, _ = pc.check(fill(text, self.values), self.project)
        return {f["category"] for f in result["findings"]}

    def test_miss_rate(self):
        positives = [line.split(" | ", 1) for line in samples("positives.txt")]
        self.assertGreaterEqual(len(positives), MIN_SAMPLES)
        self.assertTrue({c for c, _ in positives} >= set(pr.CATEGORIES))
        missed = [text for category, text in positives if category not in self.categories(text)]
        rate = len(missed) / len(positives)
        print(f"\nprompt-guard miss rate: {len(missed)}/{len(positives)} = {rate:.1%}", file=sys.stderr)
        self.assertLessEqual(rate, MAX_MISS_RATE, f"missed: {missed}")

    def test_false_alarm_rate(self):
        negatives = samples("negatives.txt")
        self.assertGreaterEqual(len(negatives), MIN_SAMPLES)
        alarms = [text for text in negatives if self.categories(text)]
        rate = len(alarms) / len(negatives)
        print(f"\nprompt-guard false-alarm rate: {len(alarms)}/{len(negatives)} = {rate:.1%}", file=sys.stderr)
        self.assertLessEqual(rate, MAX_FALSE_ALARM_RATE, f"false alarms: {alarms}")

    def test_fixtures_hold_no_literal_key_shapes(self):
        """Fixtures use {{placeholders}}; a committed key-shaped string would trip scanners."""
        for name in ("positives.txt", "negatives.txt"):
            for line in samples(name):
                found = {rule_id for category, rule_id in pr.scan(line) if category == "secrets"}
                self.assertEqual(found, set(), f"{name}: {line[:40]}")


if __name__ == "__main__":
    unittest.main()
