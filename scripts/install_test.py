# /// script
# requires-python = ">=3.10"
# ///
"""End-to-end install test for bmad-guard (Phase 1, T9, Gate 1).

Builds throwaway BMAD projects in a new temp folder and checks that the guard module installs,
survives BMAD quick-update, full update and a 6.11.0 -> 6.12.4 upgrade, and still works (Phase 2, T1: the Claude Code
hook wiring in .claude/settings.json is checked after setup and after every BMAD run):

  fresh    BMAD 6.12.4 -> setup_guard.py -> quick-update -> update; overrides resolve; guard scripts run.
  upgrade  BMAD 6.11.0 -> setup_guard.py -> install 6.12.4 over it; guard files untouched; overrides resolve.

Needs network (npx downloads bmad-method@6.12.4 and @6.11.0), Node (npx), uv and git. uv also provides
a Python 3.11+ to read the override TOML files. Fake data only. Not part of `python3 -m unittest discover`.

Usage:
    python3 scripts/install_test.py [--report docs/reports/phase-1-install-test.md] [--keep] [--skip-upgrade]

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
    "pre_tool_use.py": (
        [],
        '{"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}',
    ),  # no guards yet
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

    def run(self, cmd: list[str], cwd: Path, stdin: str | None = None, timeout: int = 300):
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, cwd=cwd, input=stdin, capture_output=True, text=True, env=self.env, timeout=timeout
            )
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
    args = parser.parse_args(argv)

    missing = [tool for tool in ("npx", "uv", "git") if not shutil.which(tool)]
    if missing:
        print(f"install test: missing tool(s): {', '.join(missing)}", file=sys.stderr)
        return 2

    root = Path(tempfile.mkdtemp(prefix="bmad-guard-install-test-"))
    runner = Runner(root / "commands.log")
    guard_skills = {p.name for p in (REPO / "module/skills").iterdir() if p.is_dir()}
    started = time.monotonic()
    print(f"sandboxes in {root}", flush=True)
    try:
        stage_fresh(runner, root, guard_skills)
        if not args.skip_upgrade:
            stage_upgrade(runner, root, guard_skills)
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
