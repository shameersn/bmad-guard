# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for scripts/red_team.py (Phase 2, T8). Fake data; no network, no npx, no BMAD.

The end-to-end run itself needs network and npx: `python3 scripts/red_team.py` (see scripts/README.md). What can run
offline does: the case table, the classifiers, the report, the git helpers, and every stateless case against the real
tool-guard hook in an empty project, so the table cannot drift from what the guards do.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from tests import guard_harness  # noqa: F401 - puts module/skills/tool-guard/scripts on sys.path
from tests.fake_home import isolated_env
from tests.guarded import mark_guarded

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("red_team", ROOT / "scripts" / "red_team.py")
rt = importlib.util.module_from_spec(spec)
sys.modules["red_team"] = rt
spec.loader.exec_module(rt)

import write_gate as wg  # noqa: E402 - tool-guard script, on sys.path through the harness import

PROMPT_SCRIPTS = ROOT / "module/skills/prompt-guard/scripts"
sys.path.insert(0, str(ROOT / "module/hooks"))
import guard_tech_stack as ts  # noqa: E402


def load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    module_spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module
    module_spec.loader.exec_module(module)
    return module


pr = load("prompt_rules", PROMPT_SCRIPTS / "prompt_rules.py")
rfd = load("ready_for_dev", ROOT / "module/skills/ready-for-dev-gate/scripts/ready_for_dev.py")


def spec_of(cid: str) -> rt.Spec:
    return rt.BY_ID[cid]


class CaseTableTests(unittest.TestCase):
    def test_ids_are_unique_and_fields_are_valid(self):
        ids = [s.id for s in rt.SPECS]
        self.assertEqual(len(ids), len(set(ids)))
        for s in rt.SPECS:
            with self.subTest(case=s.id):
                self.assertIn(s.expected, rt.EXPECTED)
                self.assertIn(s.layer, ("L1", "L2", "L1+L2"))
                self.assertTrue(s.family and s.title and s.entry)

    def test_block_and_warn_rows_name_the_rule_they_expect(self):
        for s in rt.SPECS:
            if s.expected in ("block", "warn"):
                with self.subTest(case=s.id):
                    self.assertTrue(s.rule, "a block or warn row must name its rule id")

    def test_control_rows_are_allowed(self):
        controls = [s for s in rt.SPECS if "-C" in s.id]
        self.assertGreaterEqual(len(controls), 10)
        for s in controls:
            with self.subTest(case=s.id):
                self.assertEqual(s.expected, "allow")
                self.assertTrue(s.title.startswith("Control:"))

    def test_every_guard_family_has_a_control_row(self):
        for family in {s.family for s in rt.SPECS if s.expected == "block"}:
            with self.subTest(family=family):
                self.assertTrue(
                    any(s.family == family and s.expected == "allow" for s in rt.SPECS)
                    or family in ("Same-session review", "Write gate", "Edit after approval"),
                    "block rows without a close-but-safe allow row",
                )

    def test_rollout_rows_exist_and_are_block_rows(self):
        self.assertEqual([r[0] for r in rt.ROLLOUT], ["PHI", "Wrong DB", "Edit after approval", "Same-session review"])
        for name, _what, rows in rt.ROLLOUT:
            for cid in rows:
                with self.subTest(case=name, row=cid):
                    self.assertEqual(rt.BY_ID[cid].expected, "block")

    def test_every_case_is_wired_into_the_script(self):
        """A row that nothing records would be reported NOT RUN for ever. Each id must be used in a record call."""
        source = (ROOT / "scripts/red_team.py").read_text(encoding="utf-8")
        stateless = {c.id for c in rt.stateless_calls(Path("/x"))}
        for s in rt.SPECS:
            with self.subTest(case=s.id):
                self.assertTrue(s.id in stateless or f'"{s.id}"' in source, "no record() call for this case")

    def test_mach_1_edit_still_finds_its_line_and_switches_the_rule_off_in_a_project_copy(self):
        """MACH-1 edits one line of the project copy of customize.toml; if the line moves, the case must say so."""
        text = (ROOT / "module/skills/tool-guard/customize.toml").read_text(encoding="utf-8")
        flags = 'flag_sets = [["-f|--force|--force-with-lease|--force-if-includes"]]'
        self.assertEqual(text.count(flags), 1)
        source = (ROOT / "scripts/red_team.py").read_text(encoding="utf-8")
        self.assertIn(flags, source)

    def test_stateless_calls_all_have_a_row(self):
        for call in rt.stateless_calls(Path("/x")):
            with self.subTest(call=call.id):
                self.assertIn(call.id, rt.BY_ID)
                self.assertEqual(rt.BY_ID[call.id].entry, f"PreToolUse {call.tool}")

    def test_entries_match_the_layer(self):
        for s in rt.SPECS:
            with self.subTest(case=s.id):
                if s.entry.startswith("git "):
                    self.assertEqual(s.layer, "L2")
                elif s.layer == "L2":
                    self.fail("an L2 row must be a git command")


class HelperTests(unittest.TestCase):
    def test_rule_ids_from_a_hook_message(self):
        text = "- CMD_GIT_RESET_HARD (command-guard): x\n- GATE_NO_ACTIVE_PLAN (write-gate): y (CMD_GIT_RESET_HARD)"
        self.assertEqual(rt.rule_ids(text), ["CMD_GIT_RESET_HARD", "GATE_NO_ACTIVE_PLAN"])

    def test_rule_ids_from_the_prompt_hook_and_ignore_noise(self):
        text = "Prompt blocked by prompt-guard: phi (phi.us-ssn); CLAUDE_PROJECT_DIR and HIPAA are not rule ids"
        self.assertEqual(rt.rule_ids(text), ["phi.us-ssn"])

    def test_matcher_covers(self):
        self.assertTrue(rt.matcher_covers("", "Bash"))
        self.assertTrue(rt.matcher_covers("*", "Bash"))
        self.assertTrue(rt.matcher_covers("Bash|Read", "Read"))
        self.assertFalse(rt.matcher_covers("Bash|Read", "Edit"))
        self.assertFalse(rt.matcher_covers("Bash", "BashOutput"), "the whole name must match")
        self.assertFalse(rt.matcher_covers("(", "Bash"), "a bad pattern covers nothing")

    def test_hook_commands_follow_the_matcher(self):
        settings = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "a"}, {"type": "prompt"}]},
                    {"matcher": "Read|Write", "hooks": [{"type": "command", "command": "b"}]},
                ],
                "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "c"}]}],
            }
        }
        self.assertEqual([h["command"] for h in rt.hook_commands(settings, "PreToolUse", "Bash")], ["a"])
        self.assertEqual([h["command"] for h in rt.hook_commands(settings, "PreToolUse", "Write")], ["b"])
        self.assertEqual(rt.hook_commands(settings, "PreToolUse", "Edit"), [])
        self.assertEqual([h["command"] for h in rt.hook_commands(settings, "UserPromptSubmit")], ["c"])
        self.assertEqual(rt.hook_commands(settings, "SessionStart"), [])
        self.assertEqual(rt.hook_commands({"hooks": {"PreToolUse": "bad"}}, "PreToolUse", "Bash"), [])

    def test_the_real_wiring_covers_every_tool_and_event_the_cases_use(self):
        settings = rt.setup_guard.load_wiring(ROOT).merge(None).settings
        for s in rt.SPECS:
            if s.entry.startswith("PreToolUse "):
                with self.subTest(case=s.id):
                    self.assertEqual(len(rt.hook_commands(settings, "PreToolUse", s.entry.split()[1])), 1)
        self.assertEqual(len(rt.hook_commands(settings, "UserPromptSubmit")), 1)
        self.assertEqual(len(rt.hook_commands(settings, "SessionStart")), 1)

    def test_classify_hook(self):
        self.assertEqual(rt.classify_hook("PreToolUse", [(0, "", "", True)]), "allow")
        self.assertEqual(rt.classify_hook("PreToolUse", []), "allow")
        self.assertEqual(rt.classify_hook("PreToolUse", [(2, "", "no", False)]), "block")
        self.assertEqual(rt.classify_hook("PreToolUse", [(1, "", "crash", True)]), "block", "onFailure block")
        self.assertEqual(
            rt.classify_hook("PreToolUse", [(1, "", "crash", False)]), "allow", "a plain error is not a block"
        )
        warn = json.dumps({"systemMessage": "careful"})
        self.assertEqual(rt.classify_hook("PreToolUse", [(0, warn, "", True)]), "warn")
        deny = json.dumps({"hookSpecificOutput": {"permissionDecision": "deny"}})
        self.assertEqual(rt.classify_hook("PreToolUse", [(0, deny, "", True)]), "block")
        self.assertEqual(
            rt.classify_hook("UserPromptSubmit", [(0, json.dumps({"decision": "block"}), "", False)]), "block"
        )
        self.assertEqual(rt.classify_hook("PreToolUse", [(0, warn, "", True), (2, "", "x", True)]), "block")
        self.assertEqual(rt.classify_hook("PreToolUse", [(0, "not json", "", True)]), "allow")

    def test_classify_git(self):
        self.assertEqual(rt.classify_git(False, "COMMIT_SECRET"), "block")
        self.assertEqual(rt.classify_git(True, "bmad-guard pre-commit: warning (the commit goes ahead)"), "warn")
        self.assertEqual(rt.classify_git(True, ""), "allow")

    def test_order_seen_puts_the_expected_rule_first_once(self):
        self.assertEqual(rt.order_seen("B", ["A", "B", "A", "C"]), ["B", "A", "C"])
        self.assertEqual(rt.order_seen("Z", ["A", "B"]), ["A", "B"])
        self.assertEqual(rt.order_seen("", ["A"]), ["A"])

    def test_evaluate(self):
        block = spec_of("DESTR-1")
        self.assertEqual(rt.evaluate(block, "block", ["CMD_GIT_RESET_HARD"]), (rt.PASS, ""))
        self.assertEqual(rt.evaluate(block, "allow", [])[0], rt.FAIL)
        self.assertIn("not seen", rt.evaluate(block, "block", ["CMD_OTHER"])[1])
        self.assertIn("no hook entry", rt.evaluate(block, "block", ["CMD_GIT_RESET_HARD"], ran=False)[1])
        self.assertIn("echoed", rt.evaluate(block, "block", ["CMD_GIT_RESET_HARD"], leaked=True)[1])
        control = spec_of("DESTR-C1")
        self.assertEqual(rt.evaluate(control, "allow", []), (rt.PASS, ""))
        self.assertEqual(
            rt.evaluate(control, "allow", [], ran=False)[0], rt.FAIL, "a control with no hook proves nothing"
        )
        self.assertEqual(rt.evaluate(control, "block", ["CMD_X"])[0], rt.FAIL)

    def test_summary_and_rollout_status(self):
        results = {s.id: rt.Result(s) for s in rt.SPECS}
        self.assertEqual(rt.summary(list(results.values())), (0, 0, len(rt.SPECS)))
        self.assertFalse(any(ok for *_x, ok in rt.rollout_status(results)), "NOT RUN is not blocked")
        for cid in ("DB-1", "DB-2", "REV-2"):
            results[cid].status = rt.PASS
        results["PHI-1"].status = rt.PASS
        results["PHI-2"].status = rt.FAIL
        got = {name: ok for name, _what, _rows, ok in rt.rollout_status(results)}
        self.assertEqual(
            got, {"PHI": False, "Wrong DB": True, "Edit after approval": False, "Same-session review": True}
        )
        self.assertEqual(rt.summary(list(results.values()))[:2], (4, 1))

    def test_rollout_status_with_a_missing_result(self):
        self.assertFalse(any(ok for *_x, ok in rt.rollout_status({})))

    def test_payloads(self):
        sb = Path("/x/y")
        pay = rt.tool_payload(sb, "Bash", {"command": "ls"}, "s1")
        self.assertEqual(
            (pay["hook_event_name"], pay["tool_name"], pay["session_id"], pay["cwd"]),
            ("PreToolUse", "Bash", "s1", "/x/y"),
        )
        self.assertEqual(rt.prompt_payload(sb, "hi", "s1")["prompt"], "hi")
        self.assertEqual(rt.start_payload(sb, "s1")["source"], "startup")

    def test_short_codes_and_cell(self):
        self.assertEqual(rt.short_codes([]), "-")
        self.assertEqual(rt.short_codes(["A", "B", "C"], 2), "A, B (+1)")
        self.assertEqual(rt.cell("a|b\nc"), "a\\|b c")

    def test_record_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.json"
            self.assertIsNone(rt.record_sessions(path))
            path.write_text("{nope")
            self.assertIsNone(rt.record_sessions(path))
            path.write_text(json.dumps({"sessions": "x"}))
            self.assertIsNone(rt.record_sessions(path))
            path.write_text(json.dumps({"sessions": ["s1"]}))
            self.assertEqual(rt.record_sessions(path), ["s1"])


class FakeValueTests(unittest.TestCase):
    """The fakes must trigger (or skip) the real rules, or the cases would prove nothing."""

    def rules(self, text: str) -> list[str]:
        return [rule for _category, rule in pr.scan(text)]

    def test_the_fake_patient_identifier_is_flagged(self):
        self.assertIn("phi.us-ssn", self.rules(f"Patient SSN {rt.FAKE_SSN}"))

    def test_the_allow_listed_fakes_pass(self):
        self.assertEqual(self.rules(f"Patient SSN {rt.ALLOWED_SSN}"), [])
        self.assertEqual(self.rules(f"key = {rt.FAKE_TEST_KEY}"), [])

    def test_the_fake_key_is_a_secret(self):
        self.assertIn("secret.github-token", self.rules(f"token = {rt.FAKE_KEY}"))

    def test_no_key_shaped_literal_in_the_script(self):
        source = (ROOT / "scripts/red_team.py").read_text(encoding="utf-8")
        for value in (rt.FAKE_KEY, rt.FAKE_TEST_KEY, rt.FAKE_SSN):
            self.assertNotIn(value, source)


class TextBuilderTests(unittest.TestCase):
    def test_the_tech_stack_list_is_a_valid_list_and_rejects_the_fake_drivers(self):
        listed = ts.parse_list(rt.tech_stack_text())
        self.assertEqual(listed["npm"], ("express", "pg"))
        self.assertTrue(ts.is_allowed(listed["npm"], "npm", "pg"))
        self.assertFalse(ts.is_allowed(listed["npm"], "npm", rt.FAKE_DRIVERS["npm"]))
        self.assertFalse(ts.is_allowed(listed["python"], "python", rt.FAKE_DRIVERS["python"]))

    def test_dependency_files_are_read_by_the_hook_reader(self):
        names = ts.new_dependencies("package.json", None, rt.package_json(["pg", rt.FAKE_DRIVERS["npm"]]))
        self.assertEqual(names, {"pg", rt.FAKE_DRIVERS["npm"]})
        names = ts.new_dependencies(
            "requirements.txt", None, rt.requirements_text(["django", rt.FAKE_DRIVERS["python"]])
        )
        self.assertEqual(names, {"django", rt.FAKE_DRIVERS["python"]})

    def test_a_plan_is_found_by_the_write_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(os.path.realpath(tmp))
            (root / rt.BUILD_PLAN_REL).parent.mkdir(parents=True)
            (root / rt.BUILD_PLAN_REL).write_text(rt.plan_text("Plan"), encoding="utf-8")
            (root / rt.STORY_PLAN_REL).write_text(rt.plan_text("Plan 2", story="docs/s.md"), encoding="utf-8")
            plans = {p.rel: p for p in wg.scan_plans(root)}
        self.assertEqual(set(plans), {rt.BUILD_PLAN_REL, rt.STORY_PLAN_REL})
        self.assertEqual(plans[rt.BUILD_PLAN_REL].fields.get("gate"), "generic")
        self.assertNotIn("story", plans[rt.BUILD_PLAN_REL].fields)
        self.assertEqual(plans[rt.STORY_PLAN_REL].fields.get("story"), "docs/s.md")
        self.assertNotIn("gate", plans[rt.STORY_PLAN_REL].fields)

    def test_the_story_has_open_questions_and_the_epic_summary_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            story = root / "docs/stories/story.md"
            story.parent.mkdir(parents=True)
            text = rt.story_text(rt.QUESTIONS)
            story.write_text(text, encoding="utf-8")
            summary = root / "epics/epic-1/epic-summary.md"
            summary.parent.mkdir(parents=True)
            summary.write_text(rt.EPIC_SUMMARY, encoding="utf-8")
            result = rfd.evaluate(text, story, 80, str(summary))
        self.assertEqual([r["code"] for r in result["reasons"]], ["OPEN_QUESTIONS"])

    def test_the_thin_story_cases_get_the_gate_answers_their_rows_expect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            story = root / "docs/stories/story.md"
            frd = root / "frds/F-001/frd.md"
            summary = root / "epics/epic-1/epic-summary.md"
            for path, text in ((frd, rt.FRD_TEXT), (summary, rt.EPIC_SUMMARY)):
                path.parent.mkdir(parents=True)
                path.write_text(text, encoding="utf-8")
            story.parent.mkdir(parents=True)

            def gate(requirements: str, technical: bool = False) -> list[str]:
                text = rt.thin_story_text(requirements, technical)
                result = rfd.evaluate(text, story, 80, str(summary), None, None, str(root / "frds"))
                return [r["code"] for r in result["reasons"]]

            self.assertEqual(gate("[FR-F001-01]"), [], "the known id is clean, so the rows below fail for one reason")
            self.assertEqual(gate("[FR-F001-09]"), ["STORY_FR_UNKNOWN"])
            self.assertEqual(gate("[FR-F001-01]", technical=True), ["TECHNICAL_STORY_INVALID", "ARCH_UNAPPROVED"])
        self.assertEqual(spec_of("GATE-4").rule, "GATE_READY_STORY_FR_UNKNOWN")
        self.assertEqual(spec_of("GATE-5").rule, "GATE_READY_TECHNICAL_STORY_INVALID")
        for cid in ("GATE-4", "GATE-5"):
            self.assertEqual((spec_of(cid).family, spec_of(cid).expected), ("Write gate", "block"))

    def test_the_thin_story_plan_is_found_by_the_write_gate_and_names_the_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(os.path.realpath(tmp))
            (root / rt.THIN_PLAN_REL).parent.mkdir(parents=True)
            (root / rt.THIN_PLAN_REL).write_text(rt.plan_text("Plan 3", story=rt.THIN_STORY_REL), encoding="utf-8")
            plans = wg.scan_plans(root)
        self.assertEqual([p.rel for p in plans], [rt.THIN_PLAN_REL])
        self.assertEqual(plans[0].fields.get("story"), rt.THIN_STORY_REL)


class OfflineHookTests(unittest.TestCase):
    """Every stateless case against the real tool-guard hook, in an empty project. Same answers as in the sandbox."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.sb = Path(os.path.realpath(cls._tmp.name))
        mark_guarded(cls.sb)
        cls.script = ROOT / "module/skills/tool-guard/scripts/pre_tool_use.py"
        cls.env = isolated_env()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_hook(self, stdin: str):
        return subprocess.run(
            [sys.executable, str(self.script)], input=stdin, capture_output=True, text=True, env=self.env, cwd=self.sb
        )

    def test_each_stateless_case_gets_its_expected_answer(self):
        for call in rt.stateless_calls(self.sb):
            with self.subTest(case=call.id):
                payload = rt.tool_payload(self.sb, call.tool, call.tool_input, rt.SESSIONS["main"])
                proc = self.run_hook(json.dumps(payload))
                actual = rt.classify_hook("PreToolUse", [(proc.returncode, proc.stdout, proc.stderr, True)])
                seen = rt.rule_ids(proc.stdout + proc.stderr)
                status, note = rt.evaluate(
                    rt.BY_ID[call.id], actual, seen, leaked=any(v in proc.stdout + proc.stderr for v in call.forbidden)
                )
                self.assertEqual((status, note), (rt.PASS, ""), proc.stderr)

    def test_input_that_is_not_json_blocks(self):
        proc = self.run_hook("this is not json")
        self.assertEqual(rt.classify_hook("PreToolUse", [(proc.returncode, proc.stdout, proc.stderr, True)]), "block")
        self.assertIn("BAD_HOOK_INPUT", rt.rule_ids(proc.stderr))

    def test_command_texts_do_not_hold_a_fake_value(self):
        texts = rt.command_texts()
        self.assertGreater(len(texts), 10)
        for text in texts:
            self.assertNotIn(rt.FAKE_KEY, text)


class LabTests(unittest.TestCase):
    """Lab.fire runs the hook commands of a settings file the way Claude Code does."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sb = Path(os.path.realpath(self._tmp.name)) / "box"
        (self.sb / ".claude").mkdir(parents=True)
        self.runner = rt.it.Runner(self.sb.parent / "commands.log")
        self.results = {s.id: rt.Result(s) for s in rt.SPECS}

    def write_settings(self, groups: dict) -> rt.Lab:
        (self.sb / ".claude/settings.json").write_text(json.dumps({"hooks": groups}), encoding="utf-8")
        return rt.Lab(self.runner, self.sb, self.results)

    def hook_entry(self, body: str, matcher: str = "Bash", **extra) -> dict:
        script = self.sb / f"hook-{abs(hash(body))}.py"
        script.write_text(body, encoding="utf-8")
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
        group = {"hooks": [{"type": "command", "command": command, **extra}]}
        return {**group, "matcher": matcher} if matcher else group

    def test_exit_2_blocks_and_the_rule_id_is_seen(self):
        body = "import sys; sys.stdin.read(); sys.stderr.write('- CMD_FAKE_RULE (x): no'); sys.exit(2)"
        lab = self.write_settings({"PreToolUse": [self.hook_entry(body)]})
        obs = lab.tool("Bash", {"command": "ls"})
        self.assertEqual((obs.actual, obs.ran, obs.seen), ("block", True, ["CMD_FAKE_RULE"]))

    def test_the_hook_gets_the_project_dir_and_the_payload(self):
        body = textwrap.dedent(
            """
            import json, os, sys
            p = json.load(sys.stdin)
            seen = [os.environ["CLAUDE_PROJECT_DIR"], p["tool_name"], p["session_id"]]
            print(json.dumps({"systemMessage": " ".join(seen)}))
            """
        )
        lab = self.write_settings({"PreToolUse": [self.hook_entry(body)]})
        obs = lab.tool("Bash", {"command": "ls"}, "s-x")
        self.assertEqual(obs.actual, "warn")
        self.assertIn(f"{self.sb} Bash s-x", obs.text)

    def test_a_tool_the_matcher_does_not_cover_is_not_checked(self):
        lab = self.write_settings({"PreToolUse": [self.hook_entry("import sys; sys.exit(2)", matcher="Read")]})
        obs = lab.tool("Bash", {"command": "ls"})
        self.assertEqual((obs.actual, obs.ran), ("allow", False))

    def test_a_crash_blocks_only_with_on_failure_block(self):
        crash = "raise SystemExit(1)"
        lab = self.write_settings({"PreToolUse": [self.hook_entry(crash, onFailure="block")]})
        self.assertEqual(lab.tool("Bash", {"command": "ls"}).actual, "block")
        lab = self.write_settings({"PreToolUse": [self.hook_entry(crash)]})
        self.assertEqual(lab.tool("Bash", {"command": "ls"}).actual, "allow")

    def test_new_event_codes_are_added_to_the_rules_seen(self):
        queue = self.sb / ".bmad-guard/events/queue.jsonl"
        queue.parent.mkdir(parents=True)
        queue.write_text(json.dumps({"reason_code": "OLD_EVENT"}) + "\n", encoding="utf-8")
        body = textwrap.dedent(
            f"""
            import json, sys
            sys.stdin.read()
            with open({str(queue)!r}, "a") as fh:
                fh.write(json.dumps({{"reason_code": "NEW_EVENT"}}) + chr(10))
            """
        )
        lab = self.write_settings({"UserPromptSubmit": [self.hook_entry(body, matcher="")]})
        obs = lab.prompt("hello")
        self.assertEqual((obs.actual, obs.seen), ("allow", ["NEW_EVENT"]))

    def test_record_stores_one_result_and_refuses_a_second(self):
        lab = self.write_settings({})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            lab.record("DESTR-C1", rt.Observed("allow"))
            lab.record("DESTR-1", rt.Observed("allow", True, []))
            self.assertRaises(rt.SuiteError, lab.record, "DESTR-C1", rt.Observed("allow"))
        self.assertEqual(self.results["DESTR-C1"].status, rt.PASS)
        self.assertEqual(self.results["DESTR-1"].status, rt.FAIL)
        self.assertIn("expected block, got allow", self.results["DESTR-1"].note)
        self.assertIn("PASS", out.getvalue())

    def test_a_forbidden_value_in_the_output_fails_the_case(self):
        lab = self.write_settings({})
        with contextlib.redirect_stdout(io.StringIO()):
            lab.record("DESTR-C1", rt.Observed("allow", True, [], "leaked VALUE here"), ("VALUE",))
        self.assertEqual(self.results["DESTR-C1"].status, rt.FAIL)


class GitHelperTests(unittest.TestCase):
    """commit_files / commit_paths with a real repository whose pre-commit hook blocks files named bad*."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sb = Path(os.path.realpath(self._tmp.name))
        self.runner = rt.it.Runner(self.sb.parent / "commands.log")
        git = lambda *a: rt.it.git(self.runner, self.sb, *a)  # noqa: E731
        git("init", "-q", "-b", "main")
        git("config", "user.name", "dev")
        git("config", "user.email", "dev@example.invalid")
        hooks = self.sb / "hooks"
        hooks.mkdir()
        hook = hooks / "pre-commit"
        hook.write_text(
            "#!/bin/sh\nif git diff --cached --name-only | grep -q '^bad'; then echo BLOCKED >&2; exit 1; fi\n"
        )
        hook.chmod(0o755)
        git("config", "core.hooksPath", "hooks")
        (self.sb / "ok.txt").write_text("one\n")
        rt.it.commit(self.runner, self.sb, "start")

    def test_a_file_that_passes_is_committed(self):
        proc, made = rt.commit_files(self.runner, self.sb, {"good.txt": "x\n"}, "add good")
        self.assertTrue(made)
        self.assertEqual(proc.returncode, 0)

    def test_a_new_file_that_is_blocked_is_removed_again(self):
        before = rt.head(self.runner, self.sb)
        proc, made = rt.commit_files(self.runner, self.sb, {"bad.txt": "x\n"}, "add bad")
        self.assertFalse(made)
        self.assertIn("BLOCKED", proc.stderr)
        self.assertFalse((self.sb / "bad.txt").exists())
        self.assertEqual(rt.head(self.runner, self.sb), before)
        self.assertEqual(rt.it.git(self.runner, self.sb, "status", "--short").stdout.strip(), "")

    def test_a_tracked_file_that_is_blocked_goes_back_to_head(self):
        rt.commit_files(self.runner, self.sb, {"bad-tracked.txt": "v1\n"}, "x")  # blocked: not tracked
        (self.sb / "ok.txt").write_text("one\n")
        self.runner.run(["git", "config", "core.hooksPath", "none"], self.sb)
        rt.commit_files(self.runner, self.sb, {"bad2.txt": "v1\n"}, "track it")  # hook off: committed
        self.runner.run(["git", "config", "core.hooksPath", "hooks"], self.sb)
        proc, made = rt.commit_files(self.runner, self.sb, {"bad2.txt": "v2\n"}, "change it")
        self.assertFalse(made)
        self.assertEqual((self.sb / "bad2.txt").read_text(), "v1\n")
        self.assertEqual(rt.it.git(self.runner, self.sb, "status", "--short").stdout.strip(), "")

    def test_commit_paths_stages_only_the_named_files(self):
        (self.sb / "a.txt").write_text("a\n")
        (self.sb / "b.txt").write_text("b\n")
        _proc, made = rt.commit_paths(self.runner, self.sb, ["a.txt"], "only a")
        self.assertTrue(made)
        self.assertEqual(rt.it.git(self.runner, self.sb, "status", "--short").stdout.strip(), "?? b.txt")


class PrivacyTests(unittest.TestCase):
    def check(self, queue_text: str | None) -> rt.Result:
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            if queue_text is not None:
                (sandbox / ".bmad-guard/events").mkdir(parents=True)
                (sandbox / ".bmad-guard/events/queue.jsonl").write_text(queue_text, encoding="utf-8")
            results = {"LEAK-1": rt.Result(spec_of("LEAK-1"))}
            with contextlib.redirect_stdout(io.StringIO()):
                rt.scenario_privacy(results, [(mock.Mock(), sandbox)])
        return results["LEAK-1"]

    def test_clean_events_pass(self):
        self.assertEqual(self.check('{"reason_code": "CMD_X"}\n').status, rt.PASS)

    def test_a_fake_value_in_an_event_fails(self):
        result = self.check(json.dumps({"reason_code": "CMD_X", "detail": rt.FAKE_SSN}) + "\n")
        self.assertEqual((result.status, result.actual), (rt.FAIL, "leak"))

    def test_command_text_in_an_event_fails(self):
        result = self.check(json.dumps({"detail": "git reset --hard HEAD~1"}) + "\n")
        self.assertEqual(result.status, rt.FAIL)

    def test_no_events_at_all_fails(self):
        self.assertEqual(self.check(None).status, rt.FAIL)


class ReportTests(unittest.TestCase):
    def results(self, **status: str) -> list[rt.Result]:
        out = []
        for s in rt.SPECS:
            res = rt.Result(s, s.expected, [s.rule] if s.rule else [], status.get(s.id.replace("-", "_"), rt.PASS))
            out.append(res)
        return out

    META = {"Date": "2026-11-26", "BMAD": "6.12.4"}

    def test_all_pass_report(self):
        text = rt.render_report(self.results(), self.META, [rt.it.Check("Set-up", "install", True)], 12.0)
        self.assertIn(f"Result: **PASS**: {len(rt.SPECS)} passed, 0 failed, 0 not run", text)
        self.assertIn("Rollout doc cases blocked: **4 of 4**", text)
        self.assertIn("| BMAD | 6.12.4 |", text)
        self.assertIn(
            "| PHI-1 | Fake patient SSN in a prompt | L1 | UserPromptSubmit | block | block | phi.us-ssn | PASS |", text
        )
        self.assertIn("## Not covered", text)
        self.assertIn("no live model session", text.lower())
        self.assertNotIn("## Failures", text)

    def test_a_failed_case_and_a_case_that_did_not_run_are_named(self):
        results = self.results(DB_1=rt.FAIL, REV_2=rt.NOT_RUN)
        results[[r.spec.id for r in results].index("DB-1")].note = "expected block, got allow"
        text = rt.render_report(results, self.META, [], 1.0)
        self.assertIn("Result: **FAIL**", text)
        self.assertIn("Rollout doc cases blocked: **2 of 4**", text)
        self.assertIn("| Wrong DB | ", text)
        self.assertIn("NOT BLOCKED", text)
        self.assertIn("## Failures", text)
        self.assertIn("**DB-1** (FAIL): expected block, got allow", text)
        self.assertIn("**REV-2** (NOT RUN)", text)

    def test_a_failed_set_up_step_fails_the_report(self):
        text = rt.render_report(self.results(), self.META, [rt.it.Check("Set-up", "install", False, "boom")], 1.0)
        self.assertIn("Result: **FAIL**", text)
        self.assertIn("- Set-up: install: boom", text)

    def test_every_family_has_a_table(self):
        text = rt.render_report(self.results(), self.META, [], 1.0)
        for family in {s.family for s in rt.SPECS}:
            self.assertIn(f"### {family}", text)


class MainTests(unittest.TestCase):
    def test_missing_tool_exits_2_without_creating_sandboxes(self):
        err = io.StringIO()
        with (
            mock.patch.object(rt.shutil, "which", return_value=None),
            mock.patch.object(rt.tempfile, "mkdtemp") as mkdtemp,
            mock.patch.object(rt.tempfile, "TemporaryDirectory") as tmpdir,
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(rt.main([]), 2)
        mkdtemp.assert_not_called()
        tmpdir.assert_not_called()
        self.assertIn("missing tool(s): npx, uv, git", err.getvalue())

    def test_use_python_puts_the_interpreter_first_on_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = rt.it.Runner(root / "commands.log")
            with mock.patch.object(rt.shutil, "which", return_value=sys.executable):
                found = rt.use_python(runner, root, "python-x")
            self.assertEqual(found, sys.executable)
            self.assertTrue((root / "pybin/python3").is_symlink())
            self.assertTrue(runner.env["PATH"].startswith(str(root / "pybin")))

    def test_use_python_refuses_an_unknown_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(rt.shutil, "which", return_value=None):
            self.assertRaises(rt.SuiteError, rt.use_python, rt.it.Runner(Path(tmp) / "c.log"), Path(tmp), "nope")

    def test_tool_version(self):
        self.assertTrue(rt.tool_version([sys.executable, "--version"]).startswith("Python"))
        self.assertEqual(rt.tool_version(["no-such-program-xyz", "--version"]), "missing")


class WindowsFamilyTests(unittest.TestCase):
    """The "Windows paths" family (GUARD-3 T9) runs the real PreToolUse scripts of the module in a child process with a
    simulated Windows flavour. No BMAD and no network, so it runs here too. A simulation; not run on Windows."""

    SCRIPTS = ROOT / "module/skills/tool-guard/scripts"

    def observe(self, tool: str, tool_input: dict) -> tuple[str, list[str]]:
        raw = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool,
                "tool_input": tool_input,
                "session_id": "s-test",
                "cwd": rt.WIN_PROJECT,
            }
        )
        proc = subprocess.run(
            [sys.executable, "-c", rt.WIN_HOOK_DRIVER, str(self.SCRIPTS)], input=raw, capture_output=True, text=True
        )
        self.assertIn(proc.returncode, (0, 2), proc.stderr)
        return ("block" if proc.returncode == 2 else "allow"), rt.rule_ids(proc.stderr)

    def test_the_calls_cover_every_row_of_the_family(self):
        family = {s.id for s in rt.SPECS if s.family.startswith("Windows paths")}
        self.assertEqual(set(rt.windows_calls()), family)
        self.assertEqual(len(family), 11)

    def test_every_case_gets_its_expected_answer_and_rule_from_the_real_scripts(self):
        for cid, calls in rt.windows_calls().items():
            spec = rt.BY_ID[cid]
            for tool, tool_input in calls:
                with self.subTest(case=cid, tool=tool, path=tool_input.get("file_path") or tool_input.get("command")):
                    actual, seen = self.observe(tool, tool_input)
                    self.assertEqual(actual, spec.expected)
                    self.assertEqual(rt.evaluate(spec, actual, seen)[0], rt.PASS)

    def test_the_family_says_it_is_a_simulation(self):
        family = next(s.family for s in rt.SPECS if s.id == "WIN-1")
        self.assertIn("simulated", family)
        self.assertIn("not run on Windows", family)
        self.assertTrue(any("Simulated Windows flavour" in item for item in rt.NOT_COVERED))


if __name__ == "__main__":
    unittest.main()
