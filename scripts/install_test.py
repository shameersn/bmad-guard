# /// script
# requires-python = ">=3.10"
# ///
"""End-to-end install test for bmad-guard (Phase 1, T9, Gate 1).

Builds throwaway BMAD projects in a new temp folder and checks that the guard module installs,
survives BMAD quick-update, full update and a 6.11.0 -> 6.12.4 upgrade, and still works (Phase 2, T1: the Claude Code
hook wiring in .claude/settings.json is checked after setup and after every BMAD run):

  fresh    BMAD 6.12.4 -> setup_guard.py -> quick-update -> update; overrides resolve; guard scripts run.
  upgrade  BMAD 6.11.0 -> setup_guard.py -> install 6.12.4 over it; guard files untouched; overrides resolve.

The fresh stage ends with smoke calls for every guard. The pre-commit hook (Phase 2, T6) is tested through real
`git commit` calls in the sandbox, so git starts the installed hook through core.hooksPath. The pre-push hook (T7) is
tested through real `git push` calls to a bare remote in the temp folder: one block (no review record), then one allow
(a review record made by the tool-guard hook from a fresh session, committed on its own).

A third stage, "machine" (GUARD-3 T5, ADR 0008, cases M-I1 to M-I9), needs no BMAD and no network: it installs the guard
scripts into a temp "machine folder" with scripts/install_machine.py (never a system path, never as root), starts the
three hooks through the managed-template commands from that folder, and checks the unguarded-project rule and the
version-skew line. Not tested here (needs root and a real machine): root ownership, the OS paths, a real managed file;
the checks that simulate them say so. `--machine-only` runs just this stage (git only).

Needs network (npx downloads bmad-method@6.12.4 and @6.11.0), Node (npx), uv and git. uv also provides
a Python 3.11+ to read the override TOML files. Fake data only. Not part of `python3 -m unittest discover`.

Usage:
    python3 scripts/install_test.py [--report docs/reports/phase-1-install-test.md] [--keep] [--skip-upgrade]
    python3 scripts/install_test.py --machine-only        # just the machine-folder stage: no BMAD, no network

Prints one PASS/FAIL line per check and a summary; exits 1 if any check fails, 2 if a tool is missing.
Only the temp folder this script creates is deleted (unless --keep).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import setup_guard  # noqa: E402 - sibling module, found through the line above

REPO = HERE.parent
BMAD_PIN = "6.12.4"
BMAD_OLD = "6.11.0"
TOOLS = "claude-code,github-copilot"
GENERATED_FILES = {"_bmad/config.toml", "_bmad/core/config.yaml", "_bmad/bmm/config.yaml"}
GENERATED_PREFIXES = ("_bmad/_config/",)
NPX_TIMEOUT = 1200
SHOW_PATHS = 8
FAKE_USER = ("dev", "dev@example.invalid")
# Guard scripts without argparse, and the smoke call used instead of --help: (argv after the script, stdin).
NO_ARGPARSE_SMOKE = {
    "approval_core.py": None,  # library: imported, not run
    "prompt_rules.py": None,  # library: imported, not run
    "prompt_guard_hook.py": ([], "{}"),  # empty prompt: exits 0 at once, nothing recorded
    "hook_common.py": None,  # library
    "hook_wiring.py": None,  # library
    "shell_parse.py": None,  # library
    "guard_config.py": None,  # library
    "command_guard.py": None,  # library
    "path_guard.py": None,  # library
    "powershell_guard.py": None,  # library (run through pre_tool_use.py)
    "winpaths.py": None,  # library
    "document_guard.py": None,  # library
    "bash_write_guard.py": None,  # library
    "content_scan.py": None,  # library (run through pre_tool_use.py)
    "review_record.py": (["--help"], ""),  # the hook reads the same arguments, so no argparse: --help exits 0
    "document_text.py": None,  # library
    "pre_tool_use.py": (
        [],
        '{"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "echo hi"}}',
    ),  # a harmless command: allowed, exit 0
    "session_start.py": ([], "{}"),  # silent and exit 0 when hooksPath and the wiring are in place
}


# ---------------------------------------------------------------- pure helpers (unit-tested)


@dataclass
class Check:
    stage: str
    name: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0


def is_generated(path: str) -> bool:
    """True for files every BMAD installer run rewrites (spike T2): config and manifest files."""
    return path in GENERATED_FILES or path.startswith(GENERATED_PREFIXES)


def classify(path: str, guard_skills: set[str]) -> str:
    """'generated', 'guard' (our skill folders, _bmad/custom/) or 'stock' (everything else)."""
    if is_generated(path):
        return "generated"
    if path.startswith("_bmad/custom/") or path == setup_guard.SETTINGS_REL:
        return "guard"
    for tool_dir in setup_guard.TOOL_SKILL_DIRS:
        if path.startswith(tool_dir + "/") and path[len(tool_dir) + 1 :].split("/")[0] in guard_skills:
            return "guard"
    return "stock"


def group_changes(paths: list[str], guard_skills: set[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {"generated": [], "guard": [], "stock": []}
    for path in sorted(set(paths)):
        groups[classify(path, guard_skills)].append(path)
    return groups


def split_z(output: str) -> list[str]:
    """Split NUL-separated git output (-z) into paths."""
    return [p for p in output.split("\0") if p]


def parse_resolver_json(stdout: str) -> dict:
    """The JSON object resolve_customization.py prints. Tolerates note lines before it."""
    text = stdout.strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in resolver output")
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(data, dict):
        raise ValueError("resolver output is not a JSON object")
    return data


def missing_keys(expected, resolved, prefix: str = "") -> list[str]:
    """Where `resolved` lacks something we set: dict keys recursively, list items (BMAD appends), scalars equal."""
    if isinstance(expected, dict):
        if not isinstance(resolved, dict):
            return [prefix or "<root>"]
        out = []
        for key, value in expected.items():
            where = f"{prefix}.{key}" if prefix else key
            out += [where] if key not in resolved else missing_keys(value, resolved[key], where)
        return out
    if isinstance(expected, list):
        if not isinstance(resolved, list):
            return [prefix]
        return [f"{prefix}[{i}]" for i, item in enumerate(expected) if item not in resolved]
    return [] if expected == resolved else [prefix]


def wiring_problems(sandbox: Path) -> list[str]:
    """What is missing from the Claude Code hook wiring in `sandbox` (empty list = all in place)."""
    wiring = setup_guard.load_wiring(REPO)
    try:
        settings = wiring.load_settings(sandbox / wiring.SETTINGS_REL)
    except ValueError as err:
        return [str(err)]
    if settings is None:
        return [f"{wiring.SETTINGS_REL} is missing"]
    result = wiring.merge(settings)
    problems = [*result.changes, *result.conflicts]
    problems += [f"{rel} is missing" for rel in wiring.script_paths() if not (sandbox / rel).is_file()]
    return problems


def short_list(paths: list[str], limit: int = SHOW_PATHS) -> str:
    more = f" (+{len(paths) - limit} more)" if len(paths) > limit else ""
    return ", ".join(paths[:limit]) + more


def summary(checks: list[Check]) -> tuple[int, int]:
    failed = sum(1 for c in checks if not c.ok)
    return len(checks) - failed, failed


def render_report(checks: list[Check], meta: dict[str, str]) -> str:
    passed, failed = summary(checks)
    lines = [
        "# Phase 1 install test (T9)",
        "",
        f"Result: **{'PASS' if not failed else 'FAIL'}**: {passed} passed, {failed} failed.",
        "",
        "| | |",
        "|---|---|",
    ]
    lines += [f"| {key} | {value} |" for key, value in meta.items()]
    stages: list[str] = []
    for check in checks:
        if check.stage not in stages:
            stages.append(check.stage)
    for stage in stages:
        lines += ["", f"## {stage}", "", "| Check | Result | Time | Detail |", "|---|---|---|---|"]
        for c in (c for c in checks if c.stage == stage):
            detail = c.detail.replace("|", "\\|").replace("\n", " ")
            took = f"{c.seconds:.1f}s" if c.seconds else ""
            lines.append(f"| {c.name} | {'PASS' if c.ok else 'FAIL'} | {took} | {detail} |")
    lines += [
        "",
        "Generated files (allowed to change on every BMAD run): "
        + ", ".join(sorted(GENERATED_FILES) + [p + "*" for p in GENERATED_PREFIXES])
        + ".",
        "",
        "Re-run: `python3 scripts/install_test.py --report docs/reports/phase-1-install-test.md`",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- sandbox runner


class Runner:
    def __init__(self, log: Path) -> None:
        self.log = log
        self.checks: list[Check] = []
        # No collector, and no project dir from an outer Claude Code session: the hooks must use the sandbox.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("BMAD_GUARD_", "CLAUDE_PROJECT_DIR"))}
        # A temp HOME, so no hook can read a real file under ~/.config/bmad-guard (ADR 0007). npm and uv keep their
        # real caches and settings (pinned when not set), or every run would download everything again.
        real_home = Path(self.env.get("HOME") or Path.home())
        home = log.parent / "home"
        home.mkdir(parents=True, exist_ok=True)
        self.env["HOME"] = str(home)
        for name, rel in (
            ("npm_config_cache", ".npm"),
            ("NPM_CONFIG_USERCONFIG", ".npmrc"),
            ("UV_CACHE_DIR", ".cache/uv"),
            ("UV_PYTHON_INSTALL_DIR", ".local/share/uv/python"),
        ):
            if name not in self.env and (real_home / rel).exists():
                self.env[name] = str(real_home / rel)

    def run(
        self, cmd: list[str], cwd: Path, stdin: str | None = None, timeout: int = 300, extra_env: dict | None = None
    ):
        start = time.monotonic()
        env = {**self.env, **extra_env} if extra_env else self.env
        try:
            proc = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc = subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout}s")
        took = time.monotonic() - start
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write(f"\n$ (cd {cwd}) {' '.join(cmd)}\n[exit {proc.returncode}, {took:.1f}s]\n")
            fh.write(proc.stdout[-4000:] + "\n" + proc.stderr[-4000:] + "\n")
        return proc, took

    def add(self, stage: str, name: str, ok: bool, detail: str = "", seconds: float = 0.0) -> bool:
        check = Check(stage, name, ok, detail, seconds)
        self.checks.append(check)
        took = f" ({seconds:.1f}s)" if seconds else ""
        print(f"{'PASS' if ok else 'FAIL'}  [{stage}] {name}{took}" + (f": {detail}" if detail else ""), flush=True)
        return ok


def tail(proc: subprocess.CompletedProcess, lines: int = 3) -> str:
    text = (proc.stderr.strip() or proc.stdout.strip()).splitlines()
    return " / ".join(text[-lines:])


def git(runner: Runner, sandbox: Path, *args: str) -> subprocess.CompletedProcess:
    return runner.run(["git", *args], sandbox)[0]


def commit(runner: Runner, sandbox: Path, message: str) -> str:
    git(runner, sandbox, "add", "-A")
    git(runner, sandbox, "commit", "-q", "--allow-empty", "--no-verify", "-m", message)
    return git(runner, sandbox, "rev-parse", "HEAD").stdout.strip()


def changes_since(runner: Runner, sandbox: Path, ref: str) -> list[str]:
    tracked = git(runner, sandbox, "diff", "--name-only", "-z", ref).stdout
    untracked = git(runner, sandbox, "ls-files", "--others", "--exclude-standard", "-z").stdout
    return split_z(tracked) + split_z(untracked)


def bmad(runner: Runner, sandbox: Path, version: str, *extra: str):
    cmd = ["npx", "-y", f"bmad-method@{version}", "install", "--directory", ".", "--modules", "bmm"]
    cmd += ["--tools", TOOLS, "--user-name", FAKE_USER[0], *extra, "--yes"]
    return runner.run(cmd, sandbox, timeout=NPX_TIMEOUT)


def setup(runner: Runner, sandbox: Path, *extra: str):
    return runner.run([sys.executable, str(HERE / "setup_guard.py"), "--project", str(sandbox), *extra], sandbox)


def new_sandbox(runner: Runner, root: Path, name: str) -> Path:
    sandbox = root / name
    sandbox.mkdir()
    git(runner, sandbox, "init", "-q", "-b", "main")
    git(runner, sandbox, "config", "user.name", FAKE_USER[0])
    git(runner, sandbox, "config", "user.email", FAKE_USER[1])
    return sandbox


def step_install(runner: Runner, stage: str, sandbox: Path, version: str) -> str | None:
    proc, took = bmad(runner, sandbox, version)
    ok = proc.returncode == 0 and (sandbox / "_bmad").is_dir()
    runner.add(stage, f"install BMAD {version}", ok, "" if ok else tail(proc), took)
    return commit(runner, sandbox, f"BMAD {version}") if ok else None


def step_setup(runner: Runner, stage: str, sandbox: Path) -> str | None:
    proc, took = setup(runner, sandbox)
    last = proc.stdout.strip().splitlines()[-2:] if proc.stdout.strip() else []
    ok = proc.returncode == 0 and "0 conflict(s)" in proc.stdout
    runner.add(stage, "setup_guard.py installs, no conflicts", ok, " / ".join(last) or tail(proc), took)
    hooks = git(runner, sandbox, "config", "--local", "core.hooksPath").stdout.strip()
    runner.add(stage, "core.hooksPath set", hooks == setup_guard.HOOKS_REL, hooks or "not set")
    check_wiring(runner, stage, sandbox)
    return commit(runner, sandbox, "bmad-guard setup") if ok else None


def check_wiring(runner: Runner, stage: str, sandbox: Path) -> None:
    problems = wiring_problems(sandbox)
    runner.add(
        stage, "Claude Code hook wiring in place", not problems, short_list(problems) or "hooks, deny rules, scripts"
    )


def check_untouched(runner: Runner, stage: str, sandbox: Path, ref: str, guard_skills: set[str], strict: bool) -> None:
    """After a BMAD run: guard files, core.hooksPath and (if strict) stock files unchanged since `ref`."""
    groups = group_changes(changes_since(runner, sandbox, ref), guard_skills)
    gen = f"{len(groups['generated'])} generated file(s) changed"
    runner.add(stage, "guard files untouched", not groups["guard"], short_list(groups["guard"]) or gen)
    hooks = git(runner, sandbox, "config", "--local", "core.hooksPath").stdout.strip()
    runner.add(stage, "core.hooksPath kept", hooks == setup_guard.HOOKS_REL, hooks or "not set")
    check_wiring(runner, stage, sandbox)
    if strict:
        runner.add(stage, "only generated files changed", not groups["stock"], short_list(groups["stock"]) or gen)
    else:
        runner.add(stage, "stock files changed by the upgrade (info)", True, f"{len(groups['stock'])} file(s)")
    proc, took = setup(runner, sandbox, "--check")
    runner.add(stage, "setup_guard.py --check", proc.returncode == 0, tail(proc, 2), took)


def step_update(runner: Runner, stage: str, sandbox: Path, action: str, baseline: str, guard_skills: set[str]) -> None:
    before = git(runner, sandbox, "rev-parse", "HEAD").stdout.strip()
    proc, took = bmad(runner, sandbox, BMAD_PIN, "--action", action)
    runner.add(stage, f"BMAD {action}", proc.returncode == 0, "" if proc.returncode == 0 else tail(proc), took)
    check_untouched(runner, stage, sandbox, before, guard_skills, strict=True)
    stock = group_changes(changes_since(runner, sandbox, baseline), guard_skills)["stock"]
    runner.add(stage, "stock files unchanged vs baseline commit", not stock, short_list(stock))
    commit(runner, sandbox, f"BMAD {action}")


def read_toml(runner: Runner, path: Path) -> dict:
    """Parse a TOML file. Python 3.10 has no TOML parser, so a uv-provided Python 3.11+ reads it."""
    code = "import json, sys, tomllib; print(json.dumps(tomllib.load(open(sys.argv[1], 'rb'))))"
    proc, _ = runner.run(["uv", "run", "--no-project", "--python", ">=3.11", "python", "-c", code, str(path)], REPO)
    if proc.returncode != 0:
        raise ValueError(f"cannot read {path.name}: {tail(proc)}")
    return json.loads(proc.stdout)


def check_overrides(runner: Runner, stage: str, sandbox: Path) -> None:
    for path in sorted((REPO / "module/overrides").glob("*.toml")):
        try:
            expected = read_toml(runner, path)
        except ValueError as err:
            runner.add(stage, f"override {path.stem} is valid TOML", False, str(err))
            continue
        skill = f".claude/skills/{path.stem}"
        for section, body in expected.items():
            cmd = ["uv", "run", "_bmad/scripts/resolve_customization.py", "--skill", skill, "--key", section]
            proc, took = runner.run(cmd, sandbox)
            try:
                resolved = parse_resolver_json(proc.stdout)
                missing = missing_keys(body, resolved.get(section), section)
                detail = f"missing: {short_list(missing)}" if missing else f"{len(body)} key(s) present"
                ok = proc.returncode == 0 and not missing
            except ValueError as err:
                ok, detail = False, f"{err}; {tail(proc)}"
            runner.add(stage, f"override {path.stem} [{section}] resolves", ok, detail, took)


def guard_scripts(sandbox: Path, guard_skills: set[str]) -> list[Path]:
    return sorted(p for name in guard_skills for p in (sandbox / ".claude/skills" / name / "scripts").glob("*.py"))


def check_scripts_run(runner: Runner, stage: str, sandbox: Path, guard_skills: set[str]) -> None:
    scripts = guard_scripts(sandbox, guard_skills)
    runner.add(stage, "guard scripts found", bool(scripts), f"{len(scripts)} script(s) in .claude/skills")
    for script in scripts:
        rel = script.relative_to(sandbox).as_posix()
        if script.name in NO_ARGPARSE_SMOKE:
            smoke = NO_ARGPARSE_SMOKE[script.name]
            if smoke is None:
                code = f"import sys; sys.path.insert(0, {str(script.parent)!r}); import {script.stem}"
                proc, took = runner.run([sys.executable, "-c", code], sandbox)
                how = "import"
            else:
                proc, took = runner.run([sys.executable, str(script), *smoke[0]], sandbox, stdin=smoke[1])
                how = f"stdin {smoke[1]}"
        elif "ArgumentParser" in script.read_text(encoding="utf-8"):
            proc, took = runner.run([sys.executable, str(script), "--help"], sandbox)
            how = "--help"
        else:
            runner.add(stage, f"{rel} runs", False, "no argparse and no smoke call defined in install_test.py")
            continue
        runner.add(stage, f"{rel} runs ({how})", proc.returncode == 0, "" if proc.returncode == 0 else tail(proc))


def run_json(runner: Runner, sandbox: Path, script: str, *args: str) -> tuple[dict, str]:
    proc, _ = runner.run([sys.executable, str(sandbox / ".claude/skills" / script), *args], sandbox)
    try:
        return parse_resolver_json(proc.stdout), ""
    except ValueError:
        return {}, f"exit {proc.returncode}: {tail(proc)}"


def queue_lines(sandbox: Path) -> list[dict]:
    queue = sandbox / ".bmad-guard/events/queue.jsonl"
    if not queue.is_file():
        return []
    return [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines() if line.strip()]


def check_smokes(runner: Runner, stage: str, sandbox: Path) -> None:
    """One cheap real call per guard, with fake data."""
    state = "_bmad/custom/approval-state.json"
    sentry = "agent-approval-gate-check/scripts/check_gate.py"
    warden = "agent-approval-grant/scripts"
    gate_args = ["--doc-type", "brief", "--state-file", state, "--status-field", "approval_status"]

    before = len(queue_lines(sandbox))
    ledger = "agent-project-logger/scripts/log_action.py"
    _, err = run_json(
        runner, sandbox, ledger, "--project-name", "demo", "--action", "install test smoke", "--user", "dev"
    )
    events = queue_lines(sandbox)
    ok = len(events) == before + 1 and events[-1].get("guard") == "ledger"
    runner.add(stage, "Ledger log_action writes one event to the queue", ok, err or f"{len(events)} event(s) queued")

    result, err = run_json(runner, sandbox, sentry, "--doc-path", "docs/missing.md", *gate_args)
    ok = result.get("result") == "blocked" and result.get("reason") == "missing"
    runner.add(stage, "Sentry check_gate on a missing doc blocks", ok, err or json.dumps(result))

    doc = sandbox / "docs/smoke-brief.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("---\ntitle: Smoke brief\napproval_status: draft\n---\n\n# Smoke brief\n\nFake text.\n")
    result, err = run_json(
        runner,
        sandbox,
        f"{warden}/grant_approval.py",
        "--doc-path",
        "docs/smoke-brief.md",
        *gate_args,
        "--approved-by",
        "dev",
        "--no-commit",
    )
    gate, gerr = run_json(runner, sandbox, sentry, "--doc-path", "docs/smoke-brief.md", *gate_args)
    ok = not err and gate.get("result") == "ok"
    runner.add(stage, "Warden grant_approval, then Sentry allows", ok, err or gerr or json.dumps(gate))

    result, err = run_json(
        runner,
        sandbox,
        f"{warden}/reopen_document.py",
        "--doc-path",
        "docs/smoke-brief.md",
        "--doc-type",
        "brief",
        "--state-file",
        state,
        "--status-field",
        "approval_status",
        "--reopened-by",
        "dev",
    )
    gate, gerr = run_json(runner, sandbox, sentry, "--doc-path", "docs/smoke-brief.md", *gate_args)
    ok = not err and gate.get("result") == "blocked"
    runner.add(stage, "Warden reopen_document, then Sentry blocks", ok, err or gerr or json.dumps(gate))

    story = sandbox / "stories/smoke-story.md"
    summary_md = sandbox / "epics/epic-1/epic-summary.md"
    summary_md.parent.mkdir(parents=True, exist_ok=True)
    summary_md.write_text("# Epic 1\n\n## Functional\nFake.\n\n## Technical\nFake.\n")
    story.parent.mkdir(parents=True, exist_ok=True)
    story.write_text("---\nconfidence: 90\n---\n\n# Story 1.1: Fake\n\n## Open questions\n\n- Q-1: Which fake?\n")
    result, err = run_json(
        runner,
        sandbox,
        "ready-for-dev-gate/scripts/ready_for_dev.py",
        "stories/smoke-story.md",
        "--epic-summary",
        "epics/epic-1/epic-summary.md",
    )
    codes = [r.get("code") for r in result.get("reasons", [])]
    ok = result.get("result") == "block" and codes == ["OPEN_QUESTIONS"]
    runner.add(
        stage, "ready_for_dev blocks a story with an open question", ok, err or f"{result.get('result')} {codes}"
    )

    src = sandbox / "src/app.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("print('fake')\n")
    result, err = run_json(
        runner,
        sandbox,
        "confidence-scorer/scripts/confidence_score.py",
        "--file-path",
        "src/app.py",
        "--tests-passed",
        "3",
    )
    ok = isinstance(result.get("confidence"), int)
    runner.add(stage, "confidence_score scores a file", ok, err or f"confidence {result.get('confidence')}")

    check_pre_tool_use(runner, stage, sandbox)
    check_write_gate(runner, stage, sandbox)
    check_content_scan(runner, stage, sandbox)
    check_pre_commit(runner, stage, sandbox)
    check_pre_push(runner, stage, sandbox)


# (label, tool, tool input, expected exit code, expected reason code or "") for the PreToolUse hook as Claude Code
# runs it. "{sandbox}" in a value is replaced by the sandbox path.
PRE_TOOL_SMOKE = (
    ("blocks git push --force", "Bash", {"command": "git push --force origin main"}, 2, "CMD_GIT_FORCE_PUSH"),
    ("allows git push", "Bash", {"command": "git push origin main"}, 0, ""),
    ("blocks a read of the env file", "Read", {"file_path": "{sandbox}/.env"}, 2, "PATH_ENV_FILE"),
    ("allows a read of the env example", "Read", {"file_path": "{sandbox}/.env.example"}, 0, ""),
    (
        "blocks a write to the approval state",
        "Write",
        {"file_path": "{sandbox}/_bmad/custom/approval-state.json", "content": "{}"},
        2,
        "DOC_APPROVAL_STATE",
    ),
    (
        "blocks a new document that says approved",
        "Write",
        {"file_path": "{sandbox}/docs/prd.md", "content": "---\napproval_status: approved\n---\n# PRD\n"},
        2,
        "DOC_APPROVAL_FIELD",
    ),
    (
        "blocks a shell write to the approval state",
        "Bash",
        {"command": "echo {} > _bmad/custom/approval-state.json"},
        2,
        "DOC_APPROVAL_STATE",
    ),
    ("allows a shell read of the approval state", "Bash", {"command": "cat _bmad/custom/approval-state.json"}, 0, ""),
    (
        "allows a new document that waits for approval",
        "Write",
        {"file_path": "{sandbox}/docs/prd.md", "content": "---\napproval_status: pending-approval\n---\n# PRD\n"},
        0,
        "",
    ),
    (
        "blocks a source write when no plan is in progress",
        "Write",
        {"file_path": "{sandbox}/src/app.py", "content": "print('fake')\n"},
        2,
        "GATE_NO_ACTIVE_PLAN",
    ),
    (
        "blocks a shell write to source when no plan is in progress",
        "Bash",
        {"command": "echo x > src/app.py"},
        2,
        "GATE_NO_ACTIVE_PLAN",
    ),
    ("allows a shell write to /dev/null", "Bash", {"command": "echo x > /dev/null"}, 0, ""),
    ("allows a shell write to a scratch folder", "Bash", {"command": "echo x > tmp/out.txt"}, 0, ""),
)


GATE_PLAN_REL = "_bmad-output/implementation-artifacts/spec-smoke.md"
GATE_PLAN = (
    "---\ntitle: Smoke plan\ntype: chore\nstatus: in-progress\nroute: dispatch\nreview_loop_iteration: 0\n"
    "approval_status: pending-approval\nconfidence: 90\ngate: generic\n---\n\n# Smoke plan\n\nFake text.\n"
)


def check_write_gate(runner: Runner, stage: str, sandbox: Path) -> None:
    """The write gate end to end: no plan blocks; after Warden approves a fake plan the gate opens and records."""
    scripts = sandbox / ".claude/skills"
    gate = scripts / "tool-guard/scripts/write_gate.py"
    hook = scripts / "tool-guard/scripts/pre_tool_use.py"
    write = {"hook_event_name": "PreToolUse", "tool_name": "Write", "session_id": "s-smoke-build", "cwd": str(sandbox)}
    write["tool_input"] = {"file_path": str(sandbox / "src/app.py"), "content": "print('fake')\n"}

    proc, took = runner.run([sys.executable, str(gate), "--check"], sandbox)
    ok = proc.returncode == 1 and "GATE_NO_ACTIVE_PLAN" in proc.stdout
    runner.add(stage, "write_gate --check blocks when no plan is in progress", ok, "" if ok else tail(proc), took)

    plan = sandbox / GATE_PLAN_REL
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(GATE_PLAN, encoding="utf-8")
    proc, took = runner.run([sys.executable, str(hook)], sandbox, stdin=json.dumps(write))
    ok = proc.returncode == 2 and "GATE_PLAN_NOT_APPROVED" in proc.stderr
    runner.add(
        stage, "tool-guard PreToolUse blocks a source write while the plan is not approved", ok, tail(proc), took
    )

    grant = "agent-approval-grant/scripts/grant_approval.py"
    result, err = run_json(
        runner,
        sandbox,
        grant,
        "--doc-path",
        GATE_PLAN_REL,
        "--doc-type",
        f"build:{GATE_PLAN_REL}",
        "--state-file",
        "_bmad/custom/approval-state.json",
        "--status-field",
        "approval_status",
        "--create-status-field",
        "--hash-mode",
        "build",
        "--no-commit",
        "--approved-by",
        "dev",
    )
    proc, took = runner.run([sys.executable, str(hook)], sandbox, stdin=json.dumps(write))
    ok = not err and proc.returncode == 0
    runner.add(stage, "Warden approves a fake plan, then the source write passes the hook", ok, err or tail(proc), took)

    record = sandbox / "_bmad-output/guard/builds" / f"{plan.stem}.json"
    try:
        sessions = json.loads(record.read_text(encoding="utf-8")).get("sessions")
    except (OSError, ValueError):
        sessions = None
    runner.add(stage, "the hook records the build session id", sessions == ["s-smoke-build"], f"sessions {sessions}")

    proc, took = runner.run([sys.executable, str(gate), "--check"], sandbox)
    ok = proc.returncode == 0 and "OK" in proc.stdout
    runner.add(stage, "write_gate --check passes for the approved plan", ok, "" if ok else tail(proc), took)


# Fake values, built here so no key-shaped literal sits in this file (the same habit as the unit tests).
FAKE_KEY = "gh" + "p_" + "a1" * 18
FAKE_PHONE = "(202) 311-" + "0187"
HIPAA_CONFIG_REL = "_bmad/custom/prompt-guard.toml"


def check_content_scan(runner: Runner, stage: str, sandbox: Path) -> None:
    """The content scan end to end: a fake key blocks; a fake phone number warns, or blocks in a HIPAA project."""
    hook = sandbox / ".claude/skills/tool-guard/scripts/pre_tool_use.py"
    target = str(sandbox / "docs/smoke-content.txt")  # docs/ is not gated by the write gate

    def call(content: str):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": target, "content": content},
            "session_id": "s-smoke-content",
            "cwd": str(sandbox),
        }
        return runner.run([sys.executable, str(hook)], sandbox, stdin=json.dumps(payload))

    proc, took = call(f"token = {FAKE_KEY}\n")
    ok = proc.returncode == 2 and "CONTENT_SECRET" in proc.stderr and FAKE_KEY not in proc.stderr
    runner.add(stage, "tool-guard PreToolUse blocks a fake key in a written file", ok, "" if ok else tail(proc), took)

    proc, took = call("print('plain text')\n")
    ok = proc.returncode == 0 and not proc.stdout and not proc.stderr
    runner.add(stage, "tool-guard PreToolUse allows a plain written file", ok, "" if ok else tail(proc), took)

    proc, took = call(f"phone {FAKE_PHONE}\n")
    ok = proc.returncode == 0 and "CONTENT_PHI" in proc.stdout and FAKE_PHONE not in proc.stdout
    runner.add(
        stage, "tool-guard PreToolUse warns about PHI in a non-HIPAA project", ok, "" if ok else tail(proc), took
    )

    config = sandbox / HIPAA_CONFIG_REL
    if config.exists():
        runner.add(stage, "tool-guard PreToolUse blocks PHI in a HIPAA project", False, f"{HIPAA_CONFIG_REL} exists")
        return
    config.write_text('[agent]\nphi = "block"\n', encoding="utf-8")
    try:
        proc, took = call(f"phone {FAKE_PHONE}\n")
    finally:
        config.unlink()
    ok = proc.returncode == 2 and "CONTENT_PHI" in proc.stderr and FAKE_PHONE not in proc.stderr
    runner.add(stage, "tool-guard PreToolUse blocks PHI in a HIPAA project", ok, "" if ok else tail(proc), took)


def try_commit(runner: Runner, sandbox: Path, rel: str, text: str, message: str):
    """Write a file, stage only it and run a real `git commit`, so git starts the hook through core.hooksPath.
    Returns (process, True when a commit was made). A blocked file is taken out of the index again."""
    path = sandbox / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    before = git(runner, sandbox, "rev-parse", "HEAD").stdout.strip()
    git(runner, sandbox, "add", "--", rel)
    proc, _ = runner.run(["git", "commit", "-q", "-m", message], sandbox)
    made = git(runner, sandbox, "rev-parse", "HEAD").stdout.strip() != before
    if not made:
        git(runner, sandbox, "rm", "--cached", "-q", "-f", "--", rel)
        path.unlink()
    return proc, made


def check_pre_commit(runner: Runner, stage: str, sandbox: Path) -> None:
    """The pre-commit hook end to end, through real commits: a fake key and a hand-set approval block; a plain file and
    a Warden approval (Warden commits through the hook itself) go through."""
    hook = sandbox / setup_guard.HOOKS_REL / "pre-commit"
    runner.add(
        stage, "pre-commit hook is installed and executable", os.access(hook, os.X_OK), str(hook.relative_to(sandbox))
    )

    proc, took = runner.run([sys.executable, str(hook)], sandbox)
    runner.add(
        stage,
        "pre-commit with nothing staged exits 0 at once",
        proc.returncode == 0 and not proc.stderr,
        tail(proc),
        took,
    )

    proc, made = try_commit(runner, sandbox, "docs/hook-key.txt", f"token = {FAKE_KEY}\n", "fake key")
    ok = (
        not made
        and proc.returncode != 0
        and "COMMIT_SECRET" in proc.stderr
        and FAKE_KEY not in proc.stderr + proc.stdout
    )
    runner.add(stage, "git commit with a fake key is blocked by the pre-commit hook", ok, tail(proc))

    proc, made = try_commit(runner, sandbox, "docs/hook-ok.txt", "plain text\n", "plain file")
    runner.add(
        stage, "git commit of a plain file goes through the pre-commit hook", made and proc.returncode == 0, tail(proc)
    )

    forged = "---\ntitle: Fake\nstatus: approved\n---\n\n# Fake\n"
    proc, made = try_commit(runner, sandbox, "docs/hook-forged.md", forged, "forged approval")
    ok = not made and "COMMIT_APPROVAL_NO_ENTRY" in proc.stderr
    runner.add(stage, "git commit of a hand-set approved status is blocked", ok, tail(proc))

    draft = forged.replace("approved", "draft")
    proc, made = try_commit(runner, sandbox, "docs/hook-brief.md", draft, "draft brief")
    runner.add(stage, "git commit of a draft document goes through", made and proc.returncode == 0, tail(proc))
    result, err = run_json(
        runner,
        sandbox,
        "agent-approval-grant/scripts/grant_approval.py",
        "--doc-path",
        "docs/hook-brief.md",
        "--doc-type",
        "hook-brief",
        "--state-file",
        "_bmad/custom/approval-state.json",
        "--approved-by",
        "dev",
    )
    committed = result.get("git_commit", {})
    runner.add(
        stage,
        "Warden approves and commits through the pre-commit hook",
        bool(committed.get("ok")),
        err or json.dumps(committed),
    )
    codes = [e.get("reason_code") for e in queue_lines(sandbox) if e.get("guard") == "git-hooks"]
    runner.add(
        stage,
        "the hook queued its events",
        "COMMIT_SECRET" in codes and "COMMIT_ALLOWED" in codes,
        ", ".join(dict.fromkeys(codes)),
    )


def check_pre_push(runner: Runner, stage: str, sandbox: Path) -> None:
    """The pre-push hook end to end, through real pushes to a bare remote: source commits with no review record are
    blocked; after a review recorded by the tool-guard hook in a fresh session, and committed on its own, the push goes
    through. The remote is a folder next to the sandbox; nothing leaves the temp folder."""
    hook = sandbox / setup_guard.HOOKS_REL / "pre-push"
    runner.add(
        stage, "pre-push hook is installed and executable", os.access(hook, os.X_OK), str(hook.relative_to(sandbox))
    )
    remote = sandbox.parent / f"{sandbox.name}-remote.git"
    proc, _ = runner.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], sandbox)
    git(runner, sandbox, "remote", "add", "origin", str(remote))
    proc, _ = runner.run(["git", "push", "-q", "--no-verify", "origin", "HEAD:refs/heads/main"], sandbox)
    if not runner.add(stage, "set-up: the sandbox history is on the bare remote", proc.returncode == 0, tail(proc)):
        return
    git(runner, sandbox, "checkout", "-q", "-b", "smoke-push")
    path = sandbox / "src/push-smoke.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("print('fake')\n", encoding="utf-8")
    git(runner, sandbox, "add", "--", "src/push-smoke.py")
    proc, _ = runner.run(["git", "commit", "-q", "-m", "source change"], sandbox)
    code_sha = git(runner, sandbox, "rev-parse", "HEAD").stdout.strip()
    runner.add(stage, "set-up: a source commit goes through the pre-commit hook", proc.returncode == 0, tail(proc))

    proc, took = runner.run(["git", "push", "origin", "smoke-push"], sandbox)
    ok = proc.returncode != 0 and "PUSH_NO_RECORD" in proc.stderr
    runner.add(stage, "git push of source with no review record is blocked by the pre-push hook", ok, tail(proc), took)

    command = (
        f'python3 "{sandbox}/.claude/skills/tool-guard/scripts/review_record.py" --story smoke-story --verdict pass'
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "s-smoke-review",
        "cwd": str(sandbox),
    }
    hook_script = sandbox / ".claude/skills/tool-guard/scripts/pre_tool_use.py"
    proc, took = runner.run([sys.executable, str(hook_script)], sandbox, stdin=json.dumps(payload))
    record = sandbox / "_bmad-output/guard/reviews/smoke-story.json"
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    ok = (
        proc.returncode == 0
        and data.get("sessions") == ["s-smoke-review"]
        and data.get("reviewed_sha") == code_sha
        and data.get("verdict") == "pass"
    )
    runner.add(stage, "the tool-guard hook writes the review record with the hook's session id", ok, tail(proc), took)

    git(runner, sandbox, "add", "--", "_bmad-output/guard")
    proc, _ = runner.run(["git", "commit", "-q", "-m", "review record"], sandbox)
    runner.add(stage, "the review record is committed through the pre-commit hook", proc.returncode == 0, tail(proc))

    proc, took = runner.run(["git", "push", "origin", "smoke-push"], sandbox)
    runner.add(
        stage,
        "git push with a valid review record goes through the pre-push hook",
        proc.returncode == 0,
        tail(proc),
        took,
    )
    codes = [e.get("reason_code") for e in queue_lines(sandbox) if e.get("guard") == "git-hooks"]
    runner.add(
        stage,
        "the pre-push hook queued its events",
        "PUSH_NO_RECORD" in codes and "PUSH_ALLOWED" in codes,
        ", ".join(dict.fromkeys(c for c in codes if c.startswith("PUSH_"))),
    )


def check_pre_tool_use(runner: Runner, stage: str, sandbox: Path) -> None:
    """The tool-guard hook blocks a destructive command and a sensitive read, and lets close-but-safe calls pass."""
    script = sandbox / ".claude/skills/tool-guard/scripts/pre_tool_use.py"
    for label, tool, tool_input, want_exit, want_code in PRE_TOOL_SMOKE:
        tool_input = {k: v.replace("{sandbox}", str(sandbox)) for k, v in tool_input.items()}
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": str(sandbox)}
        proc, took = runner.run([sys.executable, str(script)], sandbox, stdin=json.dumps(payload))
        ok = proc.returncode == want_exit and want_code in proc.stderr
        runner.add(stage, f"tool-guard PreToolUse {label}", ok, "" if ok else tail(proc), took)


# ---------------------------------------------------------------- stages


def stage_fresh(runner: Runner, root: Path, guard_skills: set[str]) -> None:
    stage = f"Fresh BMAD {BMAD_PIN}"
    sandbox = new_sandbox(runner, root, "fresh")
    if not step_install(runner, stage, sandbox, BMAD_PIN):
        return
    baseline = step_setup(runner, stage, sandbox)
    if not baseline:
        return
    for action in ("quick-update", "update"):
        step_update(runner, f"{stage}: {action}", sandbox, action, baseline, guard_skills)
    check_overrides(runner, f"{stage}: overrides", sandbox)
    check_scripts_run(runner, f"{stage}: guard scripts", sandbox, guard_skills)
    check_smokes(runner, f"{stage}: guard smoke calls", sandbox)


def stage_upgrade(runner: Runner, root: Path, guard_skills: set[str]) -> None:
    stage = f"Upgrade {BMAD_OLD} -> {BMAD_PIN}"
    sandbox = new_sandbox(runner, root, "upgrade")
    if not step_install(runner, stage, sandbox, BMAD_OLD):
        return
    baseline = step_setup(runner, stage, sandbox)
    if not baseline:
        return
    proc, took = bmad(runner, sandbox, BMAD_PIN)
    runner.add(
        stage, f"upgrade to BMAD {BMAD_PIN}", proc.returncode == 0, "" if not proc.returncode else tail(proc), took
    )
    check_untouched(runner, stage, sandbox, baseline, guard_skills, strict=False)
    shims = [
        n
        for n in ("bmad-dev-story", "bmad-quick-dev", "bmad-create-story")
        if (sandbox / ".claude/skills" / n).is_dir()
    ]
    runner.add(stage, "deprecated shim skills kept (info, expected)", True, ", ".join(shims) or "none")
    commit(runner, sandbox, f"BMAD {BMAD_PIN}")
    check_overrides(runner, f"{stage}: overrides", sandbox)
    check_scripts_run(runner, f"{stage}: guard scripts", sandbox, guard_skills)


# ---------------------------------------------------------------- machine stage (ADR 0008)

MACHINE_STAGE = "Machine scripts (ADR 0008)"
FAKE_KEY_PROMPT = "gh" + "p_" + "b7" * 18  # built at run time: no key-shaped literal in this file
PUSH_FORCE = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push --force"}}


def machine_installer(runner: Runner, cwd: Path, *args: str):
    return runner.run([sys.executable, str(HERE / "install_machine.py"), *args], cwd)


def tree_names(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


def run_hook_command(runner: Runner, project: Path, command: str, payload: dict | str, cwd: Path | None = None):
    """A hook as Claude Code starts it: a shell, CLAUDE_PROJECT_DIR set, the JSON on stdin. No bytecode is written, so
    the machine folder stays as installed."""
    stdin = payload if isinstance(payload, str) else json.dumps({"cwd": str(project), "session_id": "s-mi", **payload})
    extra = {"CLAUDE_PROJECT_DIR": str(project), "PYTHONDONTWRITEBYTECODE": "1"}
    return runner.run(["sh", "-c", command], cwd or project, stdin=stdin, extra_env=extra)[0]


def machine_project(runner: Runner, root: Path, name: str, bmad: bool = False, manifest: bool = False) -> Path:
    project = new_sandbox(runner, root, name)
    if bmad:
        (project / "_bmad").mkdir()
    if manifest:
        (project / setup_guard.MANIFEST_REL).parent.mkdir(parents=True)
        (project / setup_guard.MANIFEST_REL).write_text('{"version": "v0", "files": {}}\n')
    return project


def check_machine_install(runner: Runner, stage: str, root: Path, machine: Path, version: str) -> None:
    """M-I1 and M-I2: install, install again, and what --check says."""
    uid = str(os.getuid())
    proc, took = machine_installer(runner, root, "--target", str(machine), "--version", version)
    manifest = machine / "manifest.json"
    wanted = ["VERSION", "manifest.json", "otel_headers.py", "guard_events.py"]
    wanted += [f"skills/{s}/scripts" for s in ("tool-guard", "prompt-guard", "ready-for-dev-gate")]
    wanted += ["skills/agent-project-logger/scripts", "skills/agent-approval-gate-check/scripts"]
    missing = [rel for rel in wanted if not (machine / rel).exists()]
    ok = proc.returncode == 0 and manifest.is_file() and not missing
    runner.add(
        stage,
        "M-I1 install_machine.py installs the folder (path with a space)",
        ok,
        ", ".join(missing) or tail(proc),
        took,
    )
    before = {p: p.stat().st_mtime_ns for p in machine.rglob("*")}
    proc, _ = machine_installer(runner, root, "--target", str(machine), "--version", version)
    after = {p: p.stat().st_mtime_ns for p in machine.rglob("*")}
    runner.add(stage, "M-I1 a second run changes nothing", proc.returncode == 0 and before == after, tail(proc))

    base = ["--target", str(machine), "--version", version, "--check", "--expect-owner", uid]
    proc, _ = machine_installer(runner, root, *base)
    runner.add(
        stage,
        "M-I2 --check passes on a fresh folder (owner simulated: a test folder is not root-owned)",
        proc.returncode == 0,
        tail(proc),
    )
    proc, _ = machine_installer(runner, root, "--target", str(machine), "--version", version, "--check")
    runner.add(
        stage,
        "M-I2 --check fails on a folder the current user owns (the default expects root)",
        proc.returncode == 1 and "not owned by uid 0" in proc.stdout or os.getuid() == 0,
        "needs a real root-owned folder to pass: manual step in docs/managed-settings-rollout.md",
    )
    script = machine / "skills/tool-guard/scripts/pre_tool_use.py"
    original = script.read_text(encoding="utf-8")
    script.write_text(original + "\n# edited\n", encoding="utf-8")
    proc, _ = machine_installer(runner, root, *base)
    runner.add(
        stage,
        "M-I2 --check fails after a file is edited",
        proc.returncode == 1 and "changed since the install" in proc.stdout,
        tail(proc),
    )
    script.write_text(original, encoding="utf-8")
    script.chmod(0o666)
    proc, _ = machine_installer(runner, root, *base)
    runner.add(
        stage,
        "M-I2 --check fails when a file is writable by others",
        proc.returncode == 1 and "writable by group or other" in proc.stdout,
        tail(proc),
    )
    script.chmod(0o644)
    proc, _ = machine_installer(runner, root, *base)
    runner.add(stage, "M-I2 --check passes again after the file is put back", proc.returncode == 0, tail(proc))


def check_machine_templates(runner: Runner, stage: str, root: Path, machine: Path) -> dict[str, str]:
    """M-I3: the templates equal hook_wiring; the three hooks run through the commands, from a folder with a space."""
    wiring = setup_guard.load_wiring(REPO)
    for name, os_name in (("macos", "macos"), ("linux", "linux")):
        proc, _ = machine_installer(runner, root, "--print-template", os_name)
        text = (REPO / f"module/managed-settings/managed-settings.{name}.json").read_text(encoding="utf-8")
        runner.add(
            stage,
            f"M-I3 the {name} template is what hook_wiring writes",
            proc.returncode == 0 and proc.stdout == text,
            tail(proc),
        )
    template = json.loads(machine_installer(runner, root, "--target", str(machine), "--print-template")[0].stdout)
    commands = {event: groups[0]["hooks"][0]["command"] for event, groups in template["hooks"].items()}
    ok = commands == {e: g[0]["hooks"][0]["command"] for e, g in wiring.desired_hooks(str(machine)).items()}
    runner.add(
        stage,
        "M-I3 --print-template for the temp folder equals hook_wiring's commands",
        ok and " " in str(machine),
        str(machine),
    )
    project = machine_project(runner, root, "m-guarded", bmad=True, manifest=True)
    proc = run_hook_command(runner, project, commands["PreToolUse"], PUSH_FORCE)
    runner.add(
        stage,
        "M-I3 PreToolUse runs from the machine folder (git push --force is blocked)",
        proc.returncode == 2 and "CMD_GIT_FORCE_PUSH" in proc.stderr,
        tail(proc),
    )
    prompt = {"hook_event_name": "UserPromptSubmit", "prompt": f"use this token {FAKE_KEY_PROMPT}"}
    proc = run_hook_command(runner, project, commands["UserPromptSubmit"], prompt)
    blocked = '"decision": "block"' in proc.stdout
    runner.add(
        stage,
        "M-I3 UserPromptSubmit runs from the machine folder (a fake key is blocked)",
        proc.returncode == 0 and blocked,
        tail(proc),
    )
    proc = run_hook_command(
        runner, project, commands["SessionStart"], {"hook_event_name": "SessionStart", "source": "startup"}
    )
    runner.add(
        stage,
        "M-I3 SessionStart runs from the machine folder (warns: no core.hooksPath)",
        proc.returncode == 0 and "core.hooksPath" in proc.stdout,
        tail(proc),
    )
    return commands


def check_machine_without_copies(runner: Runner, stage: str, root: Path, commands: dict[str, str]) -> Path:
    """M-I4: setup_guard installs a project, then its skill copies are deleted; the hooks still run from the machine."""
    project = machine_project(runner, root, "m-nocopies", bmad=True)
    (project / ".claude/skills").mkdir(parents=True)
    proc, _ = setup(runner, project)
    ok = proc.returncode == 0 and (project / ".claude/skills/tool-guard/scripts/pre_tool_use.py").is_file()
    runner.add(stage, "M-I4 set-up project has its own skill copies and settings", ok, tail(proc))
    shutil.rmtree(project / ".claude/skills")  # only inside the throwaway sandbox
    proc = run_hook_command(runner, project, commands["PreToolUse"], PUSH_FORCE)
    ok = proc.returncode == 2 and "CMD_GIT_FORCE_PUSH" in proc.stderr
    runner.add(
        stage,
        "M-I4 with the project's skill copies deleted, PreToolUse still blocks from the machine folder",
        ok,
        tail(proc),
    )
    prompt = {"hook_event_name": "UserPromptSubmit", "prompt": f"use this token {FAKE_KEY_PROMPT}"}
    proc = run_hook_command(runner, project, commands["UserPromptSubmit"], prompt)
    runner.add(
        stage,
        "M-I4 UserPromptSubmit still blocks from the machine folder",
        '"decision": "block"' in proc.stdout,
        tail(proc),
    )
    start = {"hook_event_name": "SessionStart", "source": "startup"}
    proc = run_hook_command(runner, project, commands["SessionStart"], start)
    runner.add(
        stage,
        "M-I4 SessionStart runs from the machine folder",
        proc.returncode == 0 and "systemMessage" not in proc.stdout,
        tail(proc),
    )
    return project


def check_machine_unguarded(runner: Runner, stage: str, root: Path, commands: dict[str, str]) -> None:
    """M-I5, M-I6, M-I7: the unguarded-project rule."""
    prompt = {"hook_event_name": "UserPromptSubmit", "prompt": f"use this token {FAKE_KEY_PROMPT}"}
    start = {"hook_event_name": "SessionStart", "source": "startup"}
    calls = (("PreToolUse", PUSH_FORCE), ("UserPromptSubmit", prompt), ("SessionStart", start))
    plain = machine_project(runner, root, "m-plain")
    before = tree_names(plain)
    results = [run_hook_command(runner, plain, commands[event], payload) for event, payload in calls]
    silent = all(p.returncode == 0 and not p.stdout and not p.stderr for p in results)
    runner.add(
        stage,
        "M-I5 no _bmad/: all three hooks exit 0 and print nothing",
        silent,
        " / ".join(tail(p) for p in results if p.stdout or p.stderr),
    )
    runner.add(
        stage,
        "M-I5 no _bmad/: no file or folder is written in the project",
        tree_names(plain) == before,
        ", ".join(set(tree_names(plain)) ^ set(before)),
    )

    bmad = machine_project(runner, root, "m-nomanifest", bmad=True)
    before = tree_names(bmad)
    pre = run_hook_command(runner, bmad, commands["PreToolUse"], PUSH_FORCE)
    prm = run_hook_command(runner, bmad, commands["UserPromptSubmit"], prompt)
    runner.add(
        stage,
        "M-I6 _bmad/ and no manifest: PreToolUse and UserPromptSubmit allow",
        pre.returncode == 0 and prm.returncode == 0 and not pre.stdout + pre.stderr + prm.stdout + prm.stderr,
        tail(pre) or tail(prm),
    )
    sst = run_hook_command(runner, bmad, commands["SessionStart"], start)
    line = json.loads(sst.stdout)["systemMessage"] if sst.stdout.strip().startswith("{") else ""
    runner.add(
        stage,
        "M-I6 _bmad/ and no manifest: SessionStart prints one line",
        sst.returncode == 0 and "not installed" in line and "\n" not in line,
        line or tail(sst),
    )
    runner.add(
        stage,
        "M-I6 nothing is written in the project",
        tree_names(bmad) == before,
        ", ".join(set(tree_names(bmad)) ^ set(before)),
    )

    guarded = machine_project(runner, root, "m-manifest", bmad=True, manifest=True)
    pre = run_hook_command(runner, guarded, commands["PreToolUse"], PUSH_FORCE)
    prm = run_hook_command(runner, guarded, commands["UserPromptSubmit"], prompt)
    runner.add(
        stage,
        "M-I7 manifest present: git push --force is blocked",
        pre.returncode == 2 and "CMD_GIT_FORCE_PUSH" in pre.stderr,
        tail(pre),
    )
    runner.add(
        stage,
        "M-I7 manifest present: a fake key in a prompt is blocked",
        '"decision": "block"' in prm.stdout,
        tail(prm),
    )
    odd = run_hook_command(runner, guarded, commands["PreToolUse"], {**PUSH_FORCE, "cwd": str(plain)})
    runner.add(
        stage,
        "M-I7 manifest present and the input cwd elsewhere: still blocked (fail closed)",
        odd.returncode == 2,
        tail(odd),
    )
    bad = run_hook_command(runner, guarded, commands["PreToolUse"], "this is not json")
    runner.add(
        stage,
        "M-I7 manifest present and bad input: blocked",
        bad.returncode == 2 and "BAD_HOOK_INPUT" in bad.stderr,
        tail(bad),
    )


def check_machine_skew(runner: Runner, stage: str, root: Path, machine: Path, project: Path) -> None:
    """M-I8: `setup_guard.py --check` prints the skew line when VERSION differs, and nothing when it is the same."""
    version = setup_guard.module_version(REPO)
    proc, _ = machine_installer(runner, root, "--target", str(machine), "--version", "v-machine-other")
    proc, _ = setup(runner, project, "--check", "--machine-dir", str(machine))
    line = next((ln for ln in proc.stdout.splitlines() if "machine scripts" in ln), "")
    ok = "v-machine-other" in line and f"this project's module: {version}" in line and "They differ" in line
    runner.add(
        stage, "M-I8 setup_guard --check prints the version-skew line when VERSION differs", ok, line or tail(proc)
    )
    machine_installer(runner, root, "--target", str(machine), "--version", version)
    proc, _ = setup(runner, project, "--check", "--machine-dir", str(machine))
    runner.add(stage, "M-I8 the same version prints no skew line", "machine scripts" not in proc.stdout, tail(proc))


DRIVER = (
    "import sys, json\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import session_start as ss\n"
    "ss.managed_dir = lambda: Path(sys.argv[2])\n"
    "sys.exit(ss.entry())\n"
)


def check_machine_managed_wiring(runner: Runner, stage: str, root: Path, machine: Path) -> None:
    """M-I9: SessionStart does not warn about hook_wiring for a managed file with machine commands. The managed file
    is in the machine folder's parent (where it sits on a real machine); `managed_dir` is patched in a driver because
    the real OS folder needs root. Not tested here: the real folder and a real managed file (rollout doc, step 2)."""
    wiring = setup_guard.load_wiring(REPO)
    managed = machine.parent
    (managed / "managed-settings.json").write_text(
        wiring.render(wiring.managed_settings(str(machine))), encoding="utf-8"
    )
    project = machine_project(runner, root, "m-wiring", bmad=True, manifest=True)
    git(runner, project, "config", "--local", "core.hooksPath", setup_guard.HOOKS_REL)
    scripts = machine / "skills/tool-guard/scripts"
    payload = json.dumps({"hook_event_name": "SessionStart", "cwd": str(project), "source": "startup"})
    extra = {"CLAUDE_PROJECT_DIR": str(project), "PYTHONDONTWRITEBYTECODE": "1"}
    cmd = [sys.executable, "-c", DRIVER, str(scripts), str(managed)]
    proc, _ = runner.run(cmd, project, stdin=payload, extra_env=extra)
    ok = proc.returncode == 0 and not proc.stdout.strip()
    runner.add(
        stage,
        "M-I9 no hook_wiring warning for a managed file with machine commands",
        ok,
        proc.stdout.strip() or tail(proc),
    )
    other = root / "elsewhere"
    other.mkdir()
    (other / "managed-settings.json").write_text(
        wiring.render(wiring.managed_settings(str(machine / "x"))), encoding="utf-8"
    )
    proc, _ = runner.run([*cmd[:-1], str(other)], project, stdin=payload, extra_env=extra)
    ok = proc.returncode == 0 and "will not run" in proc.stdout
    runner.add(
        stage, "M-I9 a managed file that points to another folder still warns", ok, proc.stdout.strip() or tail(proc)
    )


def check_machine_rollback(runner: Runner, stage: str, machine: Path) -> None:
    """Rollback order (ADR 0008): --uninstall refuses while a managed file points here, and works once it is gone."""
    managed = machine.parent
    args = ["--target", str(machine), "--managed-dir", str(managed), "--uninstall"]
    proc, _ = machine_installer(runner, managed, *args)
    ok = proc.returncode == 2 and machine.is_dir()
    runner.add(stage, "rollback: --uninstall refuses while the managed file points to the folder", ok, tail(proc))
    (managed / "managed-settings.json").unlink()
    proc, _ = machine_installer(runner, managed, *args)
    runner.add(
        stage,
        "rollback: after the managed file is gone, --uninstall removes the folder",
        proc.returncode == 0 and not machine.exists(),
        tail(proc),
    )


def stage_machine(runner: Runner, root: Path) -> None:
    stage = MACHINE_STAGE
    machine = root / "Application Support" / "bmad-guard"  # a space, like the macOS path
    version = setup_guard.module_version(REPO)
    check_machine_install(runner, stage, root, machine, version)
    commands = check_machine_templates(runner, stage, root, machine)
    project = check_machine_without_copies(runner, stage, root, commands)
    check_machine_unguarded(runner, stage, root, commands)
    args = ["--target", str(machine), "--version", version, "--check", "--expect-owner", str(os.getuid())]
    proc, _ = machine_installer(runner, root, *args)
    runner.add(
        stage, "the hooks did not change the machine folder (--check still passes)", proc.returncode == 0, tail(proc)
    )
    check_machine_skew(runner, stage, root, machine, project)
    check_machine_managed_wiring(runner, stage, root, machine)
    check_machine_rollback(runner, stage, machine)


def tool_versions() -> dict[str, str]:
    out = {}
    for name, cmd in (("node", ["node", "--version"]), ("uv", ["uv", "--version"]), ("git", ["git", "--version"])):
        try:
            out[name] = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
        except OSError:
            out[name] = "missing"
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, help="write a markdown report here")
    parser.add_argument("--keep", action="store_true", help="keep the sandboxes for inspection")
    parser.add_argument("--skip-upgrade", action="store_true", help=f"skip the {BMAD_OLD} -> {BMAD_PIN} upgrade test")
    parser.add_argument(
        "--machine-only", action="store_true", help="only the machine-folder stage (ADR 0008): no BMAD, no network"
    )
    args = parser.parse_args(argv)

    needed = ("git",) if args.machine_only else ("npx", "uv", "git")
    missing = [tool for tool in needed if not shutil.which(tool)]
    if missing:
        print(f"install test: missing tool(s): {', '.join(missing)}", file=sys.stderr)
        return 2

    root = Path(tempfile.mkdtemp(prefix="bmad-guard-install-test-"))
    runner = Runner(root / "commands.log")
    guard_skills = {p.name for p in (REPO / "module/skills").iterdir() if p.is_dir()}
    started = time.monotonic()
    print(f"sandboxes in {root}", flush=True)
    try:
        if not args.machine_only:
            stage_fresh(runner, root, guard_skills)
            if not args.skip_upgrade:
                stage_upgrade(runner, root, guard_skills)
        stage_machine(runner, root)
    finally:
        total = time.monotonic() - started
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)  # only the temp folder created above

    passed, failed = summary(runner.checks)
    print(f"\n{'PASS' if not failed else 'FAIL'}: {passed} passed, {failed} failed in {total:.0f}s")
    if args.keep:
        print(f"sandboxes kept in {root} (command log: {root / 'commands.log'})")
    if args.report:
        meta = {
            "Date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "BMAD": f"{BMAD_PIN} (upgrade from {BMAD_OLD}: {'skipped' if args.skip_upgrade else 'yes'})",
            "Tools": TOOLS,
            "Module": setup_guard.module_version(REPO),
            "Python": platform.python_version(),
            "OS": f"{platform.system()} {platform.release()}",
            **tool_versions(),
            "Total time": f"{total:.0f}s",
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_report(runner.checks, meta), encoding="utf-8")
        print(f"report written to {args.report}")
    return 1 if failed or not runner.checks else 0


if __name__ == "__main__":
    sys.exit(main())
