# /// script
# requires-python = ">=3.10"
# ///
"""Structure checks for every skill under module/skills/."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = sorted(p for p in (ROOT / "module/skills").iterdir() if p.is_dir())
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def frontmatter(path: Path) -> dict:
    match = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    if not match:
        return {}
    pairs = (line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    return {k.strip(): v.strip() for k, v in pairs}


class SkillStructureTests(unittest.TestCase):
    def test_there_are_skills(self):
        self.assertGreaterEqual(len(SKILLS), 6)

    def test_skill_md_name_matches_folder_and_has_description(self):
        for skill in SKILLS:
            with self.subTest(skill=skill.name):
                meta = frontmatter(skill / "SKILL.md")
                self.assertEqual(meta.get("name"), skill.name)
                self.assertTrue(meta.get("description"))

    def test_every_capability_route_exists(self):
        for skill in SKILLS:
            text = (skill / "SKILL.md").read_text(encoding="utf-8")
            for route in re.findall(r"Load `(references/[^`]+)`", text):
                with self.subTest(skill=skill.name, route=route):
                    self.assertTrue((skill / route).is_file())

    @unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11")
    def test_customize_toml_is_valid_and_code_matches_folder(self):
        import tomllib

        for skill in SKILLS:
            with self.subTest(skill=skill.name):
                data = tomllib.loads((skill / "customize.toml").read_text(encoding="utf-8"))
                self.assertEqual(data["agent"]["code"], skill.name)

    def test_no_builder_leftovers_or_secrets_in_skills(self):
        for path in (ROOT / "module/skills").rglob("*"):
            if path.is_file():
                with self.subTest(path=str(path.relative_to(ROOT))):
                    self.assertNotIn(path.name, {".memlog.md", "prompt-quality-canon.md"})
                    if path.name == "customize.toml":
                        self.assertNotRegex(path.read_text(encoding="utf-8"), r"(?m)^\s*api_key\s*=")

    def test_docs_never_tell_anyone_to_approve_by_hand(self):
        for path in (ROOT / "module/skills").rglob("*.md"):
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertNotIn("status by hand", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
