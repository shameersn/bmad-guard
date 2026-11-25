# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for the tool-guard hook scripts: hook_common.py, pre_tool_use.py, session_start.py. Fake data only."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "module/skills/tool-guard/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hc = load("hook_common")
hw = load("hook_wiring")
pt = load("pre_tool_use")
ss = load("session_start")
REAL_MANAGED_DIR = ss.managed_dir  # SessionStartTests patch ss.managed_dir; keep the real one for its own tests

CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith(("BMAD_GUARD_", "CLAUDE_PROJECT_DIR"))}
CALL = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "echo hi"}, "session_id": "s-1"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def queue(root: Path) -> list[dict]:
    path = root / ".bmad-guard/events/queue.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def guard(name: str, tools: tuple[str, ...], reasons: list | Exception | None = None) -> hc.Guard:
    def check(payload: dict, root: Path) -> list[hc.Verdict]:
        if isinstance(reasons, Exception):
            raise reasons
        return [hc.Verdict(code, f"{code} reason", name) for code in reasons or []]

    return hc.Guard(name, tools, check)


class CommonTests(unittest.TestCase):
    def test_read_payload(self):
        self.assertEqual(hc.read_payload('{"a": 1}'), {"a": 1})

    def test_read_payload_rejects_bad_input(self):
        for raw in ("", "{not json", "[]", '"text"', "null"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                hc.read_payload(raw)

    def test_project_root_prefers_claude_project_dir(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/fake/project"}):
            self.assertEqual(hc.project_root("/elsewhere"), Path("/fake/project"))

    def test_project_root_falls_back_to_git_top_level_then_cwd(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, CLEAN_ENV, clear=True):
            repo = Path(tmp).resolve() / "repo"
            (repo / "a/b").mkdir(parents=True)
            git(repo, "init", "-q")
            self.assertEqual(hc.project_root(repo / "a/b").resolve(), repo)
            plain = Path(tmp).resolve() / "plain"
            plain.mkdir()
            self.assertEqual(hc.project_root(plain), plain)


class RunTests(unittest.TestCase):
    def run_call(self, payload, guards):
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        return pt.run(raw, guards, root=Path("/fake"))

    def test_registered_guards_allow_a_harmless_call(self):
        self.assertEqual(
            pt.GUARD_MODULES, ("command_guard", "path_guard")
        )  # T2 registers its guards here; T3-T5 add modules
        self.assertEqual(self.run_call(CALL, [])[1], [])
        self.assertEqual(self.run_call(CALL, pt.load_guards())[1], [])
        self.assertEqual([g.name for g in pt.load_guards()], ["command-guard", "path-guard"])

    def test_malformed_input_blocks(self):
        bad = [
            "",
            "{not json",
            "[]",
            "null",
            {"tool_input": {}},
            {"tool_name": "Bash"},
            {"tool_name": "", "tool_input": {}},
            {"tool_name": 5, "tool_input": {}},
            {"tool_name": "Bash", "tool_input": "echo hi"},
            {"tool_name": "Bash", "tool_input": {}, "hook_event_name": "PostToolUse"},
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                verdicts = self.run_call(payload, [])[1]
                self.assertEqual([v.code for v in verdicts], ["BAD_HOOK_INPUT"])

    def test_event_name_may_be_absent(self):
        call = {k: v for k, v in CALL.items() if k != "hook_event_name"}
        self.assertEqual(self.run_call(call, [])[1], [])

    def test_routes_by_tool_name(self):
        bash, read, anything = (
            guard("g-bash", ("Bash",), ["A"]),
            guard("g-read", ("Read",), ["B"]),
            guard("g-all", ("*",), ["C"]),
        )
        guards = [bash, read, anything]
        self.assertEqual([v.code for v in self.run_call(CALL, guards)[1]], ["A", "C"])
        read_call = {**CALL, "tool_name": "Read", "tool_input": {"file_path": "x"}}
        self.assertEqual([v.code for v in self.run_call(read_call, guards)[1]], ["B", "C"])
        other = {**CALL, "tool_name": "Glob", "tool_input": {}}
        self.assertEqual([v.code for v in self.run_call(other, guards)[1]], ["C"])

    def test_all_reasons_are_collected(self):
        verdicts = self.run_call(CALL, [guard("one", ("Bash",), ["A", "B"]), guard("two", ("Bash",), ["C"])])[1]
        self.assertEqual([(v.guard, v.code) for v in verdicts], [("one", "A"), ("one", "B"), ("two", "C")])

    def test_a_guard_that_raises_blocks_and_the_others_still_run(self):
        guards = [guard("broken", ("Bash",), RuntimeError("secret detail")), guard("ok", ("Bash",), ["A"])]
        verdicts = self.run_call(CALL, guards)[1]
        self.assertEqual([v.code for v in verdicts], ["GUARD_ERROR", "A"])
        self.assertEqual(verdicts[0].guard, "broken")
        self.assertNotIn("secret detail", verdicts[0].reason)
        self.assertIn("RuntimeError", verdicts[0].reason)

    def test_load_guards_imports_the_named_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "fake_guard_mod.py").write_text(
                "from hook_common import Guard, Verdict\n"
                "GUARDS = [Guard('fake', ('Bash',), lambda p, r: [Verdict('FAKE', 'fake reason', 'fake')])]\n"
            )
            sys.path.insert(0, tmp)
            self.addCleanup(sys.path.remove, tmp)
            self.addCleanup(sys.modules.pop, "fake_guard_mod", None)
            guards = pt.load_guards(("fake_guard_mod",))
            self.assertEqual([g.name for g in guards], ["fake"])
            with mock.patch.object(pt, "GUARD_MODULES", ("fake_guard_mod",)):
                self.assertEqual([g.name for g in pt.load_guards()], ["fake"])

    def test_load_guards_import_error_propagates(self):
        with self.assertRaises(ImportError):
            pt.load_guards(("no_such_guard_module",))

    def test_message_lists_every_reason_and_never_a_command(self):
        text = pt.message("Bash", [hc.Verdict("A", "first", "g1", "src/x.py"), hc.Verdict("B", "second", "g2")])
        self.assertIn("Blocked Bash", text)
        self.assertIn("- A (g1) [src/x.py]: first", text)
        self.assertIn("- B (g2): second", text)


class MainInProcessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        env = mock.patch.dict(os.environ, {**CLEAN_ENV, "CLAUDE_PROJECT_DIR": str(self.root)}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)

    def call_entry(self, raw: str, guards=None):
        out, err = io.StringIO(), io.StringIO()
        stdin = mock.Mock(buffer=io.BytesIO(raw.encode()))
        with (
            mock.patch.object(sys, "stdin", stdin),
            mock.patch.object(pt, "load_guards", return_value=[] if guards is None else guards),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = pt.entry()
        return code, out.getvalue(), err.getvalue()

    def test_allow_is_silent_exit_0_and_records_nothing(self):
        code, out, err = self.call_entry(json.dumps(CALL))
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(queue(self.root), [])

    def test_block_exits_2_with_reasons_on_stderr_and_one_event_each(self):
        call = {**CALL, "tool_input": {"command": "SECRET-COMMAND-TEXT"}}
        code, out, err = self.call_entry(json.dumps(call), [guard("g", ("Bash",), ["A", "B"])])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("A (g)", err)
        self.assertIn("B (g)", err)
        self.assertNotIn("SECRET-COMMAND-TEXT", err)
        events = queue(self.root)
        self.assertEqual([e["reason_code"] for e in events], ["A", "B"])
        first = events[0]
        self.assertEqual((first["guard"], first["stage"], first["verdict"]), ("tool-guard", "tool", "block"))
        self.assertEqual((first["rule"], first["tool"], first["session_id"]), ("g", "Bash", "s-1"))
        self.assertNotIn("SECRET-COMMAND-TEXT", json.dumps(events))

    def test_malformed_input_blocks_and_records_bad_input(self):
        code, _, err = self.call_entry("{not json")
        self.assertEqual(code, 2)
        self.assertIn("BAD_HOOK_INPUT", err)
        self.assertEqual([e["reason_code"] for e in queue(self.root)], ["BAD_HOOK_INPUT"])

    def test_empty_stdin_blocks(self):
        self.assertEqual(self.call_entry("")[0], 2)

    def test_invalid_utf8_blocks_instead_of_crashing(self):
        out, err = io.StringIO(), io.StringIO()
        stdin = mock.Mock(buffer=io.BytesIO(b"\xff\xfe{"))
        with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(pt.entry(), 2)

    def test_guard_exception_blocks(self):
        code, _, err = self.call_entry(json.dumps(CALL), [guard("broken", ("Bash",), ValueError("x"))])
        self.assertEqual(code, 2)
        self.assertIn("GUARD_ERROR (broken)", err)
        self.assertEqual([e["reason_code"] for e in queue(self.root)], ["GUARD_ERROR"])

    def test_registry_failure_blocks(self):
        out, err = io.StringIO(), io.StringIO()
        stdin = mock.Mock(buffer=io.BytesIO(json.dumps(CALL).encode()))
        with (
            mock.patch.object(sys, "stdin", stdin),
            mock.patch.object(pt, "load_guards", side_effect=ImportError("no module")),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(pt.entry(), 2)
        self.assertIn("GUARD_ERROR (dispatcher)", err.getvalue())
        self.assertIn("ImportError", err.getvalue())
        self.assertNotIn("no module", err.getvalue())

    def test_event_logging_failure_does_not_change_the_block(self):
        with mock.patch.object(hc, "_guard_events", side_effect=OSError("disk full")):
            code, _, err = self.call_entry("{bad")
        self.assertEqual(code, 2)
        self.assertIn("BAD_HOOK_INPUT", err)


class MainProcessTests(unittest.TestCase):
    """The real script as Claude Code runs it: a new process, JSON on stdin, exit code and stderr."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def run_script(self, name: str, raw: str):
        env = {**CLEAN_ENV, "CLAUDE_PROJECT_DIR": str(self.root)}
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name)], input=raw, capture_output=True, text=True, env=env, cwd=self.root
        )

    def test_pre_tool_use_allows_a_valid_call(self):
        proc = self.run_script("pre_tool_use.py", json.dumps(CALL))
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))

    def test_pre_tool_use_blocks_malformed_json_with_exit_2(self):
        proc = self.run_script("pre_tool_use.py", "{not json")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("BAD_HOOK_INPUT", proc.stderr)
        self.assertEqual([e["reason_code"] for e in queue(self.root)], ["BAD_HOOK_INPUT"])

    def test_session_start_exits_0_with_a_warning_for_a_bare_project(self):
        git(self.root, "init", "-q")
        proc = self.run_script("session_start.py", json.dumps({"hook_event_name": "SessionStart", "source": "startup"}))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("systemMessage", json.loads(proc.stdout))

    def test_session_start_survives_garbage_input(self):
        git(self.root, "init", "-q")
        self.assertEqual(self.run_script("session_start.py", "\x00{{{").returncode, 0)


class SessionStartTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        git(self.root, "init", "-q")
        env = mock.patch.dict(os.environ, CLEAN_ENV, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.managed = self.root / "managed"  # stands in for the OS managed-settings folder
        patcher = mock.patch.object(ss, "managed_dir", return_value=self.managed)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_managed(self, name: str, settings: dict | str) -> None:
        path = self.managed / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(settings if isinstance(settings, str) else json.dumps(settings))

    def wire(self, hooks_path: bool = True, settings: bool = True) -> None:
        if hooks_path:
            git(self.root, "config", "core.hooksPath", hw.HOOKS_REL)
        if settings:
            path = self.root / hw.SETTINGS_REL
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(hw.render(hw.merge(None).settings))

    def start(self, payload: dict | str | None = None):
        raw = payload if isinstance(payload, str) else json.dumps({"cwd": str(self.root), **(payload or {})})
        out, err = io.StringIO(), io.StringIO()
        stdin = mock.Mock(buffer=io.BytesIO(raw.encode()))
        with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ss.entry()
        return code, out.getvalue(), err.getvalue()

    def test_hooks_path_constants_agree(self):
        spec = importlib.util.spec_from_file_location("setup_guard_for_const", ROOT / "scripts/setup_guard.py")
        sg = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = sg
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(sg)
        self.assertEqual(hw.HOOKS_REL, sg.HOOKS_REL)
        self.assertEqual(hw.SETTINGS_REL, sg.SETTINGS_REL)

    def test_complete_setup_is_silent_and_records_nothing(self):
        self.wire()
        self.assertEqual(self.start(), (0, "", ""))
        self.assertEqual(queue(self.root), [])

    def test_nothing_set_up_warns_about_both_and_records_one_warn_event(self):
        code, out, _ = self.start({"session_id": "s-9", "source": "startup"})
        self.assertEqual(code, 0)
        message = json.loads(out)["systemMessage"]
        self.assertIn("core.hooksPath", message)
        self.assertIn(hw.SETTINGS_REL, message)
        events = queue(self.root)
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["verdict"], events[0]["reason_code"]), ("warn", "SETUP_INCOMPLETE"))
        self.assertEqual(events[0]["missing"], "hooks_path,hook_wiring")
        self.assertEqual((events[0]["stage"], events[0]["session_id"]), ("session", "s-9"))

    def test_only_the_missing_part_is_named(self):
        self.wire(hooks_path=False)
        message = json.loads(self.start()[1])["systemMessage"]
        self.assertIn("core.hooksPath", message)
        self.assertNotIn("Claude Code hooks will not run", message)
        self.assertEqual(queue(self.root)[0]["missing"], "hooks_path")

    def test_missing_wiring_only(self):
        self.wire(settings=False)
        self.assertEqual(json.loads(self.start()[1])["systemMessage"].count("will not run"), 1)
        self.assertEqual(queue(self.root)[0]["missing"], "hook_wiring")

    def test_hooks_path_pointing_elsewhere_warns(self):
        self.wire()
        git(self.root, "config", "core.hooksPath", ".husky")
        self.assertFalse(ss.hooks_path_ok(self.root))

    def test_absolute_hooks_path_to_the_same_folder_is_fine(self):
        self.wire()
        git(self.root, "config", "core.hooksPath", str(self.root / hw.HOOKS_REL))
        self.assertTrue(ss.hooks_path_ok(self.root))

    def test_out_of_date_wiring_warns(self):
        self.wire()
        path = self.root / hw.SETTINGS_REL
        settings = json.loads(path.read_text())
        settings["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 1
        path.write_text(json.dumps(settings))
        self.assertFalse(ss.wiring_ok(self.root))

    def test_broken_settings_file_warns_instead_of_crashing(self):
        self.wire()
        (self.root / hw.SETTINGS_REL).write_text("{broken")
        self.assertFalse(ss.wiring_ok(self.root))
        self.assertEqual(self.start()[0], 0)

    def test_managed_file_alone_counts_as_wired(self):
        self.wire(settings=False)
        self.write_managed("managed-settings.json", hw.merge(None).settings)
        self.assertTrue(ss.wiring_ok(self.root))
        self.assertEqual(self.start(), (0, "", ""))

    def test_template_in_managed_folder_counts_as_wired(self):
        self.wire(settings=False)
        self.write_managed(
            "managed-settings.json", (ROOT / "module/managed-settings/managed-settings.json").read_text()
        )
        self.assertTrue(ss.wiring_ok(self.root))

    def test_managed_drop_ins_are_read_together(self):
        self.wire(settings=False)
        full = hw.merge(None).settings
        self.write_managed("managed-settings.d/10-hooks.json", {"hooks": full["hooks"]})
        self.write_managed("managed-settings.d/20-deny.json", {"permissions": full["permissions"]})
        self.assertTrue(ss.wiring_ok(self.root))

    def test_partial_managed_wiring_still_warns(self):
        self.wire(settings=False)
        self.write_managed("managed-settings.json", {"hooks": hw.merge(None).settings["hooks"]})
        self.assertFalse(ss.wiring_ok(self.root))

    def test_broken_managed_file_warns_instead_of_crashing(self):
        self.wire(settings=False)
        self.write_managed("managed-settings.json", "{broken")
        self.assertFalse(ss.wiring_ok(self.root))
        self.assertEqual(self.start()[0], 0)

    def test_broken_managed_file_does_not_hide_good_project_wiring(self):
        self.wire()
        self.write_managed("managed-settings.json", "{broken")
        self.assertTrue(ss.wiring_ok(self.root))

    def test_managed_dir_follows_the_platform(self):
        for platform, expected in (
            ("darwin", "/Library/Application Support/ClaudeCode"),
            ("linux", "/etc/claude-code"),
        ):
            with mock.patch.object(ss.sys, "platform", platform):
                self.assertEqual(REAL_MANAGED_DIR(), Path(expected))

    def test_unknown_platform_has_no_managed_settings(self):
        with mock.patch.object(ss.sys, "platform", "plan9"):
            self.assertIsNone(REAL_MANAGED_DIR())
        self.assertIsNone(ss.managed_settings(None))

    def test_empty_managed_folder_is_not_wired(self):
        self.managed.mkdir()
        self.assertIsNone(ss.managed_settings(self.managed))

    def test_garbage_stdin_still_exits_0(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.root)}):
            code, out, _ = self.start("not json at all")
        self.assertEqual(code, 0)
        self.assertIn("systemMessage", json.loads(out))

    def test_never_blocks_even_when_the_check_crashes(self):
        with mock.patch.object(ss, "problems", side_effect=RuntimeError("boom")):
            code, out, err = self.start()
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("RuntimeError", err)

    def test_event_has_no_prompt_or_content_fields(self):
        self.start({"prompt": "SECRET-PROMPT-TEXT", "source": "startup"})
        self.assertNotIn("SECRET-PROMPT-TEXT", json.dumps(queue(self.root)))


if __name__ == "__main__":
    unittest.main()
