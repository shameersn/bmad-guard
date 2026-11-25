# /// script
# requires-python = ">=3.10"
# ///
"""Shared setup for the git hook tests (Phase 2, T6): a real, throwaway git repository with the module installed.

`HookTestCase` builds a project in a temp folder: `git init`, one baseline commit, the guard skills copied to
`.claude/skills`, the hooks copied to `_bmad/custom/guard/hooks` and `core.hooksPath` pointing there (set in the temp
repository only). The installed files stay untracked, so a test stages only what it needs.

Git and the hooks run with a clean environment: no `GIT_*` variables, no global git config (a temp HOME), no collector
settings. All data is fake. Key-shaped values come from tests/prompt_guard_fakes.py, built at run time.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.prompt_guard_fakes import Fakes

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "module/skills"
HOOKS = ROOT / "module/hooks"
HOOKS_REL = "_bmad/custom/guard/hooks"
STATE_REL = "_bmad/custom/approval-state.json"
LIST_REL = "_bmad/custom/project/guard/tech-stack.toml"
WARDEN = ".claude/skills/agent-approval-grant/scripts"


def _load_rules():
    path = ROOT / "module/skills/prompt-guard/scripts/prompt_rules.py"
    spec = importlib.util.spec_from_file_location("prompt_rules", path)
    module = sys.modules.get("prompt_rules")
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules["prompt_rules"] = module
        spec.loader.exec_module(module)
    return module


_pr = _load_rules()
FAKE = Fakes().values(_pr.luhn_valid, _pr.nhs_valid, _pr.verhoeff_valid)
MARKER = "Zqxmarker7731"  # unique text that must never appear in a message or an event


def clean_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BMAD_GUARD_", "CLAUDE_PROJECT_DIR"))}
    env.update(HOME=str(home), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return env


class HookTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(os.path.realpath(tmp.name))
        (base / "home").mkdir()
        self.env = clean_env(base / "home")
        self.project = base / "project"
        self.project.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "dev")
        self.git("config", "user.email", "dev@example.invalid")
        self.write("README.md", "# Fake project\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "baseline")
        ignore = shutil.ignore_patterns("__pycache__", "tests", "README.md")
        for tool_dir in (".claude/skills",):
            shutil.copytree(SKILLS, self.project / tool_dir, ignore=ignore)
        shutil.copytree(HOOKS, self.project / HOOKS_REL, ignore=ignore)
        for path in (self.project / HOOKS_REL).iterdir():
            path.chmod(0o755)
        self.git("config", "core.hooksPath", HOOKS_REL)

    # --- the repository -----------------------------------------------------------------------------------------

    def git(self, *args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", *args], cwd=self.project, capture_output=True, text=True, env={**self.env, **(env or {})}
        )
        if check and proc.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
        return proc

    def write(self, rel: str, text: str = "") -> Path:
        path = self.project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:  # no newline translation
            handle.write(text)
        return path

    def write_bytes(self, rel: str, data: bytes) -> Path:
        path = self.project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def stage(self, *rels: str) -> None:
        self.git("add", "--", *rels)

    def commit_all(self, message: str = "commit") -> None:
        """A commit that skips the hook: set-up for a test, not the thing under test."""
        self.git("add", "-A", "--", ".", ":(exclude).claude", ":(exclude)_bmad/custom/guard", ":(exclude).bmad-guard")
        self.git("commit", "-q", "--no-verify", "-m", message)

    # --- the hook -----------------------------------------------------------------------------------------------

    def hook(self, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        """Run the installed pre-commit hook on what is staged. (exit code, stdout, stderr)."""
        return subprocess.run(
            [sys.executable, str(self.project / HOOKS_REL / "pre-commit")],
            cwd=self.project,
            capture_output=True,
            text=True,
            env={**self.env, **(extra_env or {})},
        )

    def commit(self, message: str = "commit", *args: str) -> subprocess.CompletedProcess:
        """A real `git commit`, which starts the hook through core.hooksPath."""
        return self.git("commit", "-q", "-m", message, *args, check=False)

    def assertQuiet(self, proc: subprocess.CompletedProcess) -> None:
        """The hook let the commit go ahead and said nothing."""
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))

    # --- fake files ---------------------------------------------------------------------------------------------

    def events(self) -> list[dict]:
        path = self.project / ".bmad-guard/events/queue.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        return [e for e in map(json.loads, lines) if e.get("guard") == "git-hooks"]

    def codes(self) -> list[str]:
        return [e["reason_code"] for e in self.events()]

    def warden(self, script: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.project / WARDEN / script), *args],
            cwd=self.project,
            capture_output=True,
            text=True,
            env=self.env,
        )

    def approve(self, rel: str, doc_type: str = "brief", *extra: str) -> subprocess.CompletedProcess:
        """Warden's real script: set the status to approved and record the approval. No commit unless the caller
        leaves `--no-commit` out (pass `extra` only)."""
        args = ["--doc-path", rel, "--doc-type", doc_type, "--state-file", STATE_REL, "--approved-by", "dev"]
        proc = self.warden("grant_approval.py", *args, *extra)
        if proc.returncode != 0:
            raise AssertionError(f"Warden failed: {proc.stderr}")
        return proc
