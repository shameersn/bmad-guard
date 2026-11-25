# /// script
# requires-python = ">=3.10"
# ///
"""Tests for the tech-stack list and the dependency readers (Phase 2, T6). Pure text logic; fake package names."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "module/hooks"))

import guard_tech_stack as ts  # noqa: E402 - found through the sys.path line above

HEAD = "---\nstatus: draft\nversion: 1\n---\n"


class ListFormatTests(unittest.TestCase):
    def test_a_list_with_a_frontmatter_block_and_comments(self):
        text = (
            HEAD + '# note\n[allowed]\nnpm = ["react", "@Types/*"]  # web\npython = ["Django", \'psycopg2_binary\']\n'
        )
        self.assertEqual(ts.parse_list(text), {"npm": ("@types/*", "react"), "python": ("django", "psycopg2-binary")})

    def test_a_list_may_run_over_several_lines_with_a_trailing_comma(self):
        text = '[allowed]\nnpm = [\n  "react",   # ui\n  "vue",\n]\n'
        self.assertEqual(ts.parse_list(text)["npm"], ("react", "vue"))

    def test_an_empty_list_is_allowed_and_governs_the_ecosystem(self):
        self.assertEqual(ts.parse_list("[allowed]\nnpm = []\n"), {"npm": ()})

    def test_bad_lists_raise_with_a_message(self):
        bad = {
            "empty file": HEAD,
            "unknown ecosystem": '[allowed]\nrust = ["serde"]\n',
            "other table": '[tools]\nnpm = ["react"]\n',
            "no table": 'npm = ["react"]\n',
            "not a list": '[allowed]\nnpm = "react"\n',
            "number in list": "[allowed]\nnpm = [1]\n",
            "inline table in list": '[allowed]\nnpm = [{name = "react"}]\n',
            "bad name": '[allowed]\nnpm = ["re act"]\n',
            "given twice": '[allowed]\nnpm = ["a"]\nnpm = ["b"]\n',
            "unclosed list": '[allowed]\nnpm = ["a"\n',
            "unclosed string": '[allowed]\nnpm = ["a]\n',
            "unclosed header": "[allowed\n",
            "not key = value": "[allowed]\nnpm\n",
        }
        for name, text in bad.items():
            with self.subTest(name):
                with self.assertRaises(ts.StackError):
                    ts.parse_list(text)

    def test_errors_never_repeat_a_package_name_from_the_file(self):
        with self.assertRaises(ts.StackError) as caught:
            ts.parse_list('[allowed]\nnpm = ["secret-looking name"]\n')
        self.assertNotIn("secret-looking", str(caught.exception))

    def test_matching_normalises_names_and_supports_a_star(self):
        npm = ("@types/*", "react")
        self.assertTrue(ts.is_allowed(npm, "npm", "@types/node"))
        self.assertTrue(ts.is_allowed(npm, "npm", "React"))
        self.assertFalse(ts.is_allowed(npm, "npm", "react-dom"))
        self.assertFalse(ts.is_allowed(npm, "npm", "@scope/types"))
        py = ("psycopg2-binary",)
        self.assertTrue(ts.is_allowed(py, "python", "Psycopg2_Binary"))
        self.assertTrue(ts.is_allowed(py, "python", "psycopg2.binary"))


class NpmTests(unittest.TestCase):
    def names(self, **sections):
        return ts.dependency_names("web/package.json", json.dumps(sections))

    def test_all_four_dependency_sections_count(self):
        found = self.names(
            dependencies={"a": "1"},
            devDependencies={"b": "1"},
            peerDependencies={"c": "1"},
            optionalDependencies={"d": "1"},
        )
        self.assertEqual(found, {"a", "b", "c", "d"})

    def test_local_code_is_not_a_new_package(self):
        found = self.names(
            dependencies={"a": "workspace:*", "b": "file:../b", "c": "link:../c", "d": "git+https://x/d"}
        )
        self.assertEqual(found, {"d"})

    def test_an_alias_counts_under_the_real_name_too(self):
        self.assertEqual(self.names(dependencies={"pretty": "npm:@scope/real@^1.0.0"}), {"pretty", "@scope/real"})
        self.assertEqual(self.names(dependencies={"pretty": "npm:real"}), {"pretty", "real"})

    def test_bad_json_and_bad_shapes_raise(self):
        for text in ("{nope", "[]", '{"dependencies": []}'):
            with self.subTest(text=text), self.assertRaises(ts.StackError):
                ts.dependency_names("package.json", text)

    def test_new_dependencies_is_a_difference(self):
        old = json.dumps({"dependencies": {"a": "1", "b": "1"}})
        new = json.dumps({"dependencies": {"b": "2", "c": "1"}})
        self.assertEqual(ts.new_dependencies("package.json", old, new), {"c"})
        self.assertEqual(ts.new_dependencies("package.json", None, new), {"b", "c"})
        self.assertEqual(ts.new_dependencies("package.json", "{broken", new), {"b", "c"})  # unreadable old = empty


class RequirementsTests(unittest.TestCase):
    def names(self, text):
        return ts.dependency_names("requirements-dev.txt", text)

    def test_lines_with_versions_extras_markers_hashes_and_comments(self):
        text = (
            "# comment\n"
            "requests[security]>=2.0,<3  # inline\n"
            "Flask_Login==0.6 ; python_version >= '3.9'\n"
            "pkg-a==1.0 \\\n    --hash=sha256:abcd\n"
            "name @ https://example.invalid/name.zip\n"
            "\n"
        )
        self.assertEqual(self.names(text), {"requests", "flask-login", "pkg-a", "name"})

    def test_options_are_skipped_and_editable_installs_are_read(self):
        text = (
            "-r base.txt\n--index-url https://example.invalid/simple\n-c constraints.txt\n"
            "-e git+https://example.invalid/x.git#egg=from-git\n-e .\n--editable ./local\n"
        )
        self.assertEqual(self.names(text), {"from-git"})

    def test_a_line_without_a_name_raises_with_its_number_and_no_content(self):
        with self.assertRaises(ts.StackError) as caught:
            self.names("good\nhttps://example.invalid/pkg.tar.gz\n")
        self.assertIn("line 2", str(caught.exception))
        self.assertNotIn("example.invalid", str(caught.exception))

    def test_which_files_are_requirements_files(self):
        for rel in ("requirements.txt", "api/requirements-dev.txt", "requirements/base.txt", "requirements.in"):
            with self.subTest(rel=rel):
                self.assertEqual(ts.kind_of(rel), "python")
        for rel in ("notes.txt", "src/requirements.py", "node_modules/x/package.json"):
            with self.subTest(rel=rel):
                self.assertIsNone(ts.kind_of(rel))


class PyprojectTests(unittest.TestCase):
    def names(self, text):
        return ts.dependency_names("pyproject.toml", text)

    def test_pep_621_dependencies_and_optional_groups(self):
        text = (
            '[project]\nname = "app"\ndescription = """a\nlong one"""\n'
            'dependencies = [\n  "Django>=4.2",\n  "psycopg2-binary",  # db\n'
            "  \"tool[extra]; python_version < '3.12'\",\n]\n"
            '[project.optional-dependencies]\ndev = ["pytest", "ruff"]\n'
            '[project.urls]\nHome = "https://example.invalid"\n'
        )
        self.assertEqual(self.names(text), {"django", "psycopg2-binary", "tool", "pytest", "ruff"})

    def test_pep_735_groups_skip_includes(self):
        text = '[dependency-groups]\ntest = ["pytest", {include-group = "lint"}]\nlint = ["ruff"]\n'
        self.assertEqual(self.names(text), {"pytest", "ruff"})

    def test_poetry_tables_groups_and_sub_tables(self):
        text = (
            '[tool.poetry.dependencies]\npython = "^3.10"\nrequests = "^2"\nDjango = {version = "^4", extras = ["x"]}\n'
            '[tool.poetry.dev-dependencies]\nblack = "*"\n'
            '[tool.poetry.group.test.dependencies]\npytest = "*"\n'
            '[tool.poetry.dependencies.celery]\nversion = "^5"\n'
        )
        self.assertEqual(self.names(text), {"requests", "django", "black", "pytest", "celery"})

    def test_other_keys_are_not_dependencies(self):
        text = '[build-system]\nrequires = ["setuptools"]\n[tool.ruff]\nselect = ["E"]\n[project]\nname = "x"\n'
        self.assertEqual(self.names(text), set())

    def test_broken_toml_raises(self):
        for text in ('[project]\ndependencies = ["a"\n', "[project\n", '[project]\nname = "x\n'):
            with self.subTest(text=text), self.assertRaises(ts.StackError):
                self.names(text)


class KindTests(unittest.TestCase):
    def test_dependency_files_of_other_managers_are_only_named(self):
        for rel in ("go.mod", "svc/Cargo.toml", "Gemfile", "pom.xml", "App/App.csproj", "Pipfile", "setup.py"):
            with self.subTest(rel=rel):
                self.assertEqual(ts.kind_of(rel), "other")
                with self.assertRaises(ts.StackError):
                    ts.dependency_names(rel, "")

    def test_ordinary_files_are_not_dependency_files(self):
        for rel in ("README.md", "src/package.js", "package-lock.json", "src/app.py"):
            with self.subTest(rel=rel):
                self.assertIsNone(ts.kind_of(rel))


if __name__ == "__main__":
    unittest.main()
