# /// script
# requires-python = ">=3.10"
# ///
"""Shared setup for the tool-guard tests (command, path and extension guards): a fake project and a fake home.

`GuardTestCase.call()` runs a hook payload through the real dispatcher with the real registered guards, so a test
also proves the guard is registered. Fake data only.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "module/skills/tool-guard/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pre_tool_use as pt  # noqa: E402 - found through the sys.path line above

LOAD_GUARDS = pt.load_guards  # kept here: `entry()` patches pt.load_guards
ENV_PREFIXES = ("PG", "MYSQL", "BMAD_GUARD_", "CLAUDE_PROJECT_DIR", "GIT_CONFIG")


def queue(project: Path) -> list[dict]:
    path = project / ".bmad-guard/events/queue.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


class GuardTestCase(unittest.TestCase):
    """self.project is the project root and the working directory; self.home is the fake home folder.

    The write gate (T4) is left out of `call()` unless a test class sets `with_write_gate = True`: it blocks every
    source write that has no approved plan, which is not what the other guards' tests are about. It has its own tests.
    The content scan (T5) is left out unless a test class sets `with_content_scan = True`: it reads the prompt-guard
    config and scans every written text, which the other guards' tests do not want. It has its own tests.
    """

    with_write_gate = False
    with_content_scan = False

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(os.path.realpath(tmp.name))
        self.home = base / "home"
        self.project = base / "project"
        self.home.mkdir()
        self.project.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(ENV_PREFIXES)}
        env["HOME"] = str(self.home)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.system_tmp = base / "systmp"  # stands in for $TMPDIR, so the test folders themselves are not "scratch"
        self.system_tmp.mkdir()
        patcher = mock.patch("tempfile.gettempdir", return_value=str(self.system_tmp))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, rel: str, text: str = "") -> Path:
        path = self.project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def payload(self, tool: str, tool_input: dict, **extra) -> dict:
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
            "session_id": "s-test",
            "cwd": str(self.project),
            **extra,
        }

    def call(self, tool: str, tool_input: dict, **extra):
        """(payload, verdicts) from the dispatcher with the registered guards."""
        raw = json.dumps(self.payload(tool, tool_input, **extra))
        return pt.run(raw, self.guards(), root=self.project)

    def guards(self, names=None):
        """The registered guards (or the modules in `names`), without the write gate and the content scan unless the
        class wants them."""
        optional = {"write-gate": self.with_write_gate, "content-scan": self.with_content_scan}
        return [g for g in LOAD_GUARDS(names) if optional.get(g.name, True)]

    def codes(self, tool: str, tool_input: dict, **extra) -> list[str]:
        return [v.code for v in self.call(tool, tool_input, **extra)[1]]

    def bash(self, command: str, **extra) -> list[str]:
        return self.codes("Bash", {"command": command}, **extra)

    def entry(self, raw: str):
        """The hook as Claude Code runs it, in this process: (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        stdin = mock.Mock(buffer=io.BytesIO(raw.encode()))
        env = mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.project)})
        with (
            env,
            mock.patch.object(pt, "load_guards", self.guards),
            mock.patch.object(sys, "stdin", stdin),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = pt.entry()
        return code, out.getvalue(), err.getvalue()

    def assertBlocks(self, command: str, *codes: str):
        self.assertEqual(sorted(self.bash(command)), sorted(codes), command)

    def assertAllows(self, *commands: str):
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), [], command)
