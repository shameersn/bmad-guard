# /// script
# requires-python = ">=3.10"
# ///
"""Live check for GUARD-3 T4 against the running local stack: guard events, Claude Code telemetry, Grafana panels and
the two audit reports, from a throwaway BMAD project.

Run from this folder after `docker compose up -d` (set COMPOSE_PROJECT_NAME first if the stack runs under another name):

    python3 run_telemetry_live.py [--skip-claude] [--keep]

- Uses ONE fake developer, `t4-live`. The key is made by keys.py, kept in memory, handed to the throwaway project through
  the environment of each child process only, and never printed or written. The fake developer is removed at the end.
- The throwaway project is a temp folder: BMAD 6.12.4 installed like scripts/install_test.py does, `setup_guard.py` run,
  a temp HOME (no real credential file can be read). Needs network (npx), Node, git and, for the Claude part, `claude`.
- Reads the stack passwords from secrets.local.ini and never prints them.
- Prints one PASS/FAIL line per check and a block of facts for the report (no secrets, no prompt text).
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import os
import random
import shutil
import string
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import install_test as it  # noqa: E402 - the repo's own install test helpers

DEV = "t4-live"
COLLECTOR = "http://127.0.0.1:4318"
GRAFANA = "http://127.0.0.1:3000"
CLICKHOUSE = "http://127.0.0.1:8123"
RESULTS: list[tuple[str, bool]] = []
FACTS: list[str] = []


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


keys = load("collector_keys", HERE / "keys.py")
reports = load("guard_reports", HERE / "reports" / "guard_reports.py")


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""), flush=True)
    return ok


def fact(text: str) -> None:
    FACTS.append(text)
    print(f"  fact: {text}", flush=True)


def secret_from_file(name: str) -> str:
    for line in (HERE / "secrets.local.ini").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    raise KeyError(name)


def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=HERE, capture_output=True, text=True)


def ch(query: str) -> str:
    """A query as `admin`, inside the container (the password is expanded there). Returns TSV text."""
    out = compose(
        "exec",
        "-T",
        "-e",
        f"Q={query}",
        "clickhouse",
        "sh",
        "-c",
        'clickhouse-client --user admin --password "$CLICKHOUSE_PASSWORD" -q "$Q"',
    )
    if out.returncode != 0:
        raise RuntimeError(f"clickhouse query failed: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def wait_for(sql: str, want: int, limit: float = 60) -> int:
    """Poll `SELECT count()...` until it reaches `want`."""
    got = 0
    start = time.monotonic()
    while time.monotonic() - start < limit:
        got = int(ch(sql) or 0)
        if got >= want:
            break
        time.sleep(1)
    return got


def fake_phone() -> str:
    """The test suite's fake phone number, so no real-looking value is typed here."""
    sys.path.insert(0, str(REPO))
    from tests.hook_harness import FAKE

    return FAKE["phone-us"]


def fake_secret() -> str:
    """A GitHub-token-shaped fake, built at run time (no literal in the repo)."""
    return "gh" + "p_" + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(36))


class Project:
    """The throwaway project: BMAD installed, guard set up, a temp HOME, collector settings only in each child's env."""

    def __init__(self, root: Path, user_key: str) -> None:
        self.runner = it.Runner(root / "commands.log")
        self.sb = it.new_sandbox(self.runner, root, "t4-project")
        self.env = {
            "BMAD_GUARD_ENDPOINT": COLLECTOR,
            "BMAD_GUARD_USER": DEV,
            "BMAD_GUARD_KEY": user_key,
            "CLAUDE_PROJECT_DIR": str(self.sb),
        }
        self.session = "build-" + uuid.uuid4().hex[:8]

    def git(self, *args: str, hooks: bool = True) -> subprocess.CompletedProcess:
        return self.runner.run(["git", *args], self.sb, extra_env=self.env)[0]

    def commit(self, rel: str, text: str, message: str, *flags: str) -> subprocess.CompletedProcess:
        path = self.sb / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.git("add", "-A")
        return self.git("commit", "-q", "-m", message, *flags)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def hook(self, tool: str, tool_input: dict, session: str | None = None):
        script = self.sb / ".claude/skills/tool-guard/scripts/pre_tool_use.py"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
            "session_id": session or self.session,
            "cwd": str(self.sb),
        }
        return self.runner.run([sys.executable, str(script)], self.sb, stdin=json.dumps(payload), extra_env=self.env)[0]


def build_project(root: Path, user_key: str) -> Project:
    project = Project(root, user_key)
    runner, sb = project.runner, project.sb
    ok = it.step_install(runner, "t4", sb, it.BMAD_PIN) is not None
    check("BMAD 6.12.4 installed in the throwaway project", ok)
    ok = it.step_setup(runner, "t4", sb) is not None
    check("setup_guard.py ran, no conflicts", ok)
    return project


def produce_guard_events(project: Project) -> dict[str, str]:
    """A block and an allow through the L1 hook, commits (clean, secret refused, --no-verify), review records."""
    out: dict[str, str] = {"base": project.head()}
    blocked = project.hook("Bash", {"command": "git push --force origin main"})
    check("L1: git push --force is blocked (exit 2)", blocked.returncode == 2, blocked.stderr.strip()[:80])
    allowed = project.hook("Bash", {"command": "echo hello"})
    check("L1: echo is allowed (exit 0)", allowed.returncode == 0, allowed.stderr.strip()[:80])

    clean = project.commit("src/clean.py", "print('clean')\n", "clean commit with the hooks on")
    check("L2: a clean commit passes the pre-commit hook", clean.returncode == 0, clean.stderr.strip()[:80])
    out["clean"] = project.head()

    phone = fake_phone()
    warned = project.commit("src/contact.py", f"phone = '{phone}'\n", "commit with a fake phone number")
    check(
        "L2: a fake phone number only warns (non-HIPAA project)",
        warned.returncode == 0 and "COMMIT_PHI" in warned.stderr,
    )

    secret_text = f"token = '{fake_secret()}'\n"
    refused = project.commit("src/secret.py", secret_text, "commit with a fake secret")
    check("L2: a commit with a fake secret is refused", refused.returncode != 0, refused.stderr.strip()[:80])
    bypass = project.git("commit", "-q", "-m", "the same files, hooks skipped", "--no-verify")
    check("a --no-verify commit of the refused tree goes through", bypass.returncode == 0)
    out["blocked_only"] = project.head()

    skipped = project.commit("src/skipped.py", "print('skipped')\n", "commit with the hooks skipped", "--no-verify")
    check("a --no-verify commit of a new tree goes through", skipped.returncode == 0)
    out["skipped"] = project.head()

    story = "9-9"
    command = (
        f'python3 "{project.sb}/.claude/skills/tool-guard/scripts/review_record.py" --story {story} --verdict pass'
    )
    reviewer = "review-" + uuid.uuid4().hex[:8]
    recorded = project.hook("Bash", {"command": command}, session=reviewer)
    rec = project.sb / "_bmad-output/guard/reviews" / f"{story}.json"
    check("review record: the hook wrote the record file", recorded.returncode == 0 and rec.is_file())
    forged = project.sb / "_bmad-output/guard/reviews" / "hand-made.json"
    forged.write_text(
        json.dumps({"story": "hand-made", "reviewed_sha": "0" * 40, "sessions": ["x"], "verdict": "pass"}) + "\n"
    )
    done = project.commit("notes.txt", "review records committed\n", "commit both review records")
    check("L2: the commit with both records passes the hooks", done.returncode == 0, done.stderr.strip()[:80])
    out["records"] = project.head()
    return out


def flush(project: Project) -> int:
    script = project.sb / ".claude/skills/agent-project-logger/scripts/guard_events.py"
    proc = project.runner.run(
        [sys.executable, str(script), "flush", "--project-root", str(project.sb)], project.sb, extra_env=project.env
    )[0]
    result = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    check("queue flushed to the collector", result.get("error") is None and result.get("queued") == 0, str(result))
    return int(result.get("sent", 0))


def claude_run(project: Project, user_key: str, marker: str) -> bool:
    """One `claude -p` run: OTel variables in the child's environment only; headers from the helper script."""
    helper = project.sb / ".claude/skills/agent-project-logger/scripts/otel_headers.py"
    flag_settings = Path(project.sb.parent) / "claude-run-settings.json"
    flag_settings.write_text(json.dumps({"otelHeadersHelper": f'python3 "{helper}"'}))
    env = {k: v for k, v in project.runner.env.items() if not k.startswith("OTEL_")}
    env.update(project.env)
    env.update(
        {
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_METRICS_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "delta",
            "OTEL_METRICS_INCLUDE_SESSION_ID": "true",
            "OTEL_METRICS_INCLUDE_VERSION": "true",
            "OTEL_METRICS_INCLUDE_ENTRYPOINT": "true",
            "OTEL_METRICS_INCLUDE_REPOSITORY": "false",
            "OTEL_METRICS_INCLUDE_RESOURCE_ATTRIBUTES": "false",
            "OTEL_METRIC_EXPORT_INTERVAL": "1000",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
            "OTEL_EXPORTER_OTLP_ENDPOINT": COLLECTOR,
            "OTEL_LOG_USER_PROMPTS": "0",
            "OTEL_LOG_ASSISTANT_RESPONSES": "0",
            "OTEL_LOG_TOOL_DETAILS": "0",
            "OTEL_LOG_TOOL_CONTENT": "0",
            "OTEL_LOG_RAW_API_BODIES": "0",
            "OTEL_METRICS_INCLUDE_ACCOUNT_UUID": "false",
            "OTEL_LOGS_EXPORT_INTERVAL": "1000",
        }
    )
    # The real login lives in the real HOME; the guard credential file is not there, and the sandbox has none.
    env["HOME"] = os.environ["HOME"]
    for name in ("BMAD_GUARD_USER", "BMAD_GUARD_KEY"):
        env.pop(name, None)  # the helper must work from the credential file or env: give it the env in a child only
    env.update({"BMAD_GUARD_USER": DEV, "BMAD_GUARD_KEY": user_key})
    cmd = [
        "claude",
        "-p",
        f"Reply with the single word ok. Ignore this tag: {marker}",
        "--model",
        "haiku",
        "--max-turns",
        "1",
    ]
    cmd += ["--settings", str(flag_settings)]
    try:
        proc = subprocess.run(cmd, cwd=project.sb, env=env, capture_output=True, text=True, timeout=240)
    except (subprocess.TimeoutExpired, OSError) as err:
        fact(f"claude -p did not run: {type(err).__name__}")
        return False
    fact(
        f"claude -p exit {proc.returncode}; reply length {len(proc.stdout.strip())}; stderr first line: "
        f"{(proc.stderr.strip().splitlines() or [''])[0][:120]!r}"
    )
    return proc.returncode == 0


def grafana(path: str, body: dict | None = None) -> dict:
    token = base64.b64encode(f"admin:{secret_from_file('GRAFANA_ADMIN_PASSWORD')}".encode()).decode()
    req = urllib.request.Request(
        f"{GRAFANA}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


PERSONAL = ("user.email", "user.account_uuid", "user.account_id", "user.id", "organization.id", "host.name", "host.id")


def check_metrics() -> None:
    """Claude Code metrics from the real session: arrived, user from the key, no personal attribute."""
    where = f"Attributes['user'] = '{DEV}' AND ServiceName = 'claude-code'"
    n = wait_for(f"SELECT count() FROM guard.otel_metrics_sum WHERE {where}", 1, 30)
    check("Claude Code metrics arrived in otel_metrics_sum with the key's user", n >= 1, f"{n} datapoint(s)")
    names = ch(f"SELECT arraySort(groupUniqArray(MetricName)) FROM guard.otel_metrics_sum WHERE {where}")
    fact(f"Claude Code metric names stored: {names}")
    fact(
        "metric datapoint attribute keys: "
        + ch(f"SELECT arraySort(groupUniqArrayArray(mapKeys(Attributes))) FROM guard.otel_metrics_sum WHERE {where}")
    )
    fact(
        "metric resource attribute keys: "
        + ch(
            f"SELECT arraySort(groupUniqArrayArray(mapKeys(ResourceAttributes))) FROM guard.otel_metrics_sum WHERE {where}"
        )
    )
    users = ch("SELECT groupUniqArray(Attributes['user']) FROM guard.otel_metrics_sum")
    check("every metric datapoint has the key's user", users == f"['{DEV}']", users)
    for name in PERSONAL:
        hits = int(
            ch(
                f"SELECT count() FROM guard.otel_metrics_sum WHERE mapContains(Attributes, '{name}') "
                f"OR mapContains(ResourceAttributes, '{name}')"
            )
            or 0
        )
        check(f"metrics: '{name}' is absent", hits == 0, f"{hits} row(s)")


def check_metrics_scrub_synthetic(user_key: str) -> None:
    """One made-up counter with every personal attribute on the datapoint and the resource, sent as the fake developer."""
    run = "mscrub-" + uuid.uuid4().hex[:8]

    def attr(k: str, v: str) -> dict:
        return {"key": k, "value": {"stringValue": v}}

    now = str(time.time_ns())
    point = {
        "attributes": [attr("run_id", run), attr("user", "someone-else")]
        + [attr(k, "fake") for k in PERSONAL if not k.startswith("host.")]
        + [attr("type", "input")],
        "startTimeUnixNano": now,
        "timeUnixNano": now,
        "asDouble": 7,
    }
    body = {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [
                        attr("service.name", "claude-code"),
                        attr("host.name", "fake-laptop"),
                        attr("host.id", "fake-id"),
                    ]
                },
                "scopeMetrics": [
                    {
                        "scope": {"name": "com.anthropic.claude_code"},
                        "metrics": [
                            {
                                "name": "claude_code.token.usage",
                                "unit": "tokens",
                                "sum": {"dataPoints": [point], "aggregationTemporality": 1, "isMonotonic": True},
                            },
                            # What a plain reply does not produce, so the other panels have rows too (fake numbers).
                            *[
                                {
                                    "name": name,
                                    "sum": {
                                        "dataPoints": [
                                            {
                                                "attributes": [attr(k, v) for k, v in extra],
                                                "startTimeUnixNano": now,
                                                "timeUnixNano": now,
                                                "asDouble": 3,
                                            }
                                        ],
                                        "aggregationTemporality": 1,
                                        "isMonotonic": True,
                                    },
                                }
                                for name, extra in (
                                    ("claude_code.lines_of_code.count", [("type", "added")]),
                                    ("claude_code.commit.count", []),
                                    ("claude_code.pull_request.count", []),
                                    (
                                        "claude_code.code_edit_tool.decision",
                                        [("tool_name", "Edit"), ("decision", "accept")],
                                    ),
                                )
                            ],
                        ],
                    }
                ],
            }
        ]
    }
    token = base64.b64encode(f"{DEV}:{user_key}".encode()).decode()
    req = urllib.request.Request(
        f"{COLLECTOR}/v1/metrics",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        check("synthetic metric accepted", resp.status == 200)
    got = wait_for(f"SELECT count() FROM guard.otel_metrics_sum WHERE Attributes['run_id'] = '{run}'", 1)
    row = ch(
        f"SELECT arraySort(mapKeys(Attributes)), arraySort(mapKeys(ResourceAttributes)), Attributes['user'] "
        f"FROM guard.otel_metrics_sum WHERE Attributes['run_id'] = '{run}'"
    )
    fact(f"synthetic metric stored with: {row}")
    check("synthetic metric: stored", got >= 1)
    check("synthetic metric: every personal attribute is removed", not any(f"'{name}'" in row for name in PERSONAL))
    check("synthetic metric: the user is the key's, not the one in the datapoint", row.endswith(DEV))


def check_scrub_synthetic(user_key: str) -> None:
    """Send one made-up Claude-Code-shaped record with every personal attribute we know of, as the fake developer, and
    check what the collector stores. This covers attributes a real session did not carry (not logged in to an org)."""
    run = "scrub-" + uuid.uuid4().hex[:8]

    def attr(k: str, v: str) -> dict:
        return {"key": k, "value": {"stringValue": v}}

    body = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        attr("service.name", "claude-code"),
                        attr("host.name", "fake-laptop"),
                        attr("host.id", "fake-host-id"),
                        attr("os.type", "darwin"),
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {"name": "com.anthropic.claude_code.events"},
                        "logRecords": [
                            {
                                "timeUnixNano": str(time.time_ns()),
                                "body": {"stringValue": "claude_code.user_prompt"},
                                "attributes": [
                                    attr("run_id", run),
                                    attr("user", "someone-else"),
                                    attr("user.email", "fake@example.invalid"),
                                    attr("user.account_uuid", "fake-uuid"),
                                    attr("user.account_id", "fake-id"),
                                    attr("user.id", "fake-anon-id"),
                                    attr("organization.id", "fake-org"),
                                    {
                                        "key": "workspace.host_paths",
                                        "value": {"arrayValue": {"values": [{"stringValue": "/Users/fake/proj"}]}},
                                    },
                                    attr("session.id", "fake-session"),
                                    attr("terminal.type", "fake-term"),
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    token = base64.b64encode(f"{DEV}:{user_key}".encode()).decode()
    req = urllib.request.Request(
        f"{COLLECTOR}/v1/logs",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        check("synthetic Claude-Code-shaped record accepted", resp.status == 200)
    wait_for(f"SELECT count() FROM guard.guard_events WHERE LogAttributes['run_id'] = '{run}'", 1)
    row = ch(
        f"SELECT arraySort(mapKeys(LogAttributes)), arraySort(mapKeys(ResourceAttributes)), User FROM guard.guard_events WHERE LogAttributes['run_id'] = '{run}'"
    )
    fact(f"synthetic record stored with: {row}")
    gone = (
        "user.email",
        "user.account_uuid",
        "user.account_id",
        "user.id",
        "organization.id",
        "workspace.host_paths",
        "host.name",
        "host.id",
    )
    check("synthetic record: every personal attribute is removed", not any(f"'{name}'" in row for name in gone))
    check(
        "synthetic record: session.id and terminal.type stay, user is the key's",
        "'session.id'" in row and "'terminal.type'" in row and row.endswith(DEV),
    )


def check_grafana() -> None:
    dash = grafana("/api/dashboards/uid/guard-events")["dashboard"]
    now = int(time.time() * 1000)
    for panel in dash["panels"]:
        target = panel["targets"][0]
        body = {
            "from": str(now - 3600_000),
            "to": str(now),
            "queries": [{**target, "datasource": target["datasource"], "intervalMs": 1000, "maxDataPoints": 100}],
        }
        try:
            result = grafana("/api/ds/query", body)["results"]["A"]
            frames = result.get("frames", [])
            rows = len(frames[0]["data"]["values"][0]) if frames and frames[0]["data"]["values"] else 0
            need_rows = "otel_metrics_sum" in target["rawSql"] or panel["id"] <= 3
            check(
                f"Grafana panel '{panel['title']}' query runs" + (" and shows rows" if need_rows else ""),
                "error" not in result and (rows >= 1 or not need_rows),
                f"{rows} row(s)",
            )
            fact(f"panel {panel['id']} '{panel['title']}': {rows} row(s)")
        except urllib.error.HTTPError as err:
            check(f"Grafana panel '{panel['title']}' query runs", False, f"HTTP {err.code}")


def run_report(args: list[str], project: Project) -> tuple[int, str]:
    env = {**os.environ, "GUARD_REPORT_URL": CLICKHOUSE, "GUARD_REPORT_USER": "grafana_reader"}
    env["GUARD_REPORT_PASSWORD"] = secret_from_file("CLICKHOUSE_READER_PASSWORD")
    proc = subprocess.run(
        [sys.executable, str(HERE / "reports" / "guard_reports.py"), *args, "--repo", str(project.sb)],
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-claude", action="store_true")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway project and the fake developer")
    args = parser.parse_args(argv)
    if compose("ps", "--status", "running", "-q", "clickhouse").stdout.strip() == "":
        print("the stack is not running (docker compose up -d)", file=sys.stderr)
        return 2

    out = io.StringIO()
    keys.issue(DEV, path=keys.HTPASSWD, hasher=keys.bcrypt_hash, rotate=False, out=out)
    lines = out.getvalue().splitlines()
    user_key = lines[next(i for i, x in enumerate(lines) if x.startswith("KEY for ")) + 1]
    compose("restart", "otelcol")
    time.sleep(5)

    root = Path(tempfile.mkdtemp(prefix="bmad-guard-t4-live-"))
    print(f"throwaway project folder: {root}", flush=True)
    marker = "T4MARKER" + uuid.uuid4().hex[:10]
    started = int(time.time())
    try:
        project = build_project(root, user_key)
        commits = produce_guard_events(project)
        flush(project)
        since = f"Timestamp >= toDateTime({started}) AND ServiceName = 'bmad-guard' AND User = '{DEV}'"
        wait_for(
            f"SELECT count() FROM guard.guard_events WHERE {since} AND LogAttributes['reason_code'] = 'REVIEW_RECORDED'",
            1,
        )
        wanted = {
            "a pre-commit warn event (COMMIT_PHI)": "LogAttributes['reason_code'] = 'COMMIT_PHI' AND Verdict = 'warn'",
            "an L1 block event (CMD_GIT_FORCE_PUSH)": "LogAttributes['reason_code'] = 'CMD_GIT_FORCE_PUSH' AND Verdict = 'block'",
            "a pre-commit allow event with a tree": "LogAttributes['stage'] = 'pre-commit' AND Verdict = 'allow' AND LogAttributes['tree'] != ''",
            "a pre-commit block event (COMMIT_SECRET) with a tree": "LogAttributes['reason_code'] = 'COMMIT_SECRET' AND LogAttributes['tree'] != ''",
            "a REVIEW_RECORDED event with path and hash": "LogAttributes['reason_code'] = 'REVIEW_RECORDED' AND LogAttributes['target_sha256'] != '' AND LogAttributes['target_path'] != ''",
        }
        for name, cond in wanted.items():
            n = int(ch(f"SELECT count() FROM guard.guard_events WHERE {since} AND {cond}") or 0)
            check(f"guard events arrived for the key's user: {name}", n >= 1, f"{n} event(s)")
        users = ch("SELECT groupUniqArray(User) FROM guard.guard_events WHERE ServiceName='bmad-guard'")
        check("every guard event has the key's user", users == f"['{DEV}']", users)

        if not args.skip_claude:
            ran = claude_run(project, user_key, marker)
            got = wait_for(
                f"SELECT count() FROM guard.guard_events WHERE ServiceName != 'bmad-guard' AND User='{DEV}'", 1, 20
            )
            check("Claude Code events arrived with the key's user", got >= 1, f"{got} event(s); claude exit ok={ran}")
            names = ch("SELECT groupUniqArray(Body) FROM guard.guard_events WHERE ServiceName != 'bmad-guard'")
            fact(f"Claude Code event names (Body): {names}")
            svc = ch("SELECT groupUniqArray(ServiceName) FROM guard.guard_events WHERE ServiceName != 'bmad-guard'")
            fact(f"Claude Code ServiceName: {svc}")
            attr_keys = ch(
                "SELECT arraySort(groupUniqArrayArray(mapKeys(LogAttributes))) FROM guard.guard_events WHERE ServiceName != 'bmad-guard'"
            )
            fact(f"Claude Code log attribute keys seen: {attr_keys}")
            res_keys = ch(
                "SELECT arraySort(groupUniqArrayArray(mapKeys(ResourceAttributes))) FROM guard.guard_events WHERE ServiceName != 'bmad-guard'"
            )
            fact(f"Claude Code resource attribute keys seen: {res_keys}")
            for name in (
                "user.email",
                "user.account_uuid",
                "user.account_id",
                "user.id",
                "organization.id",
                "workspace.host_paths",
            ):
                n = int(ch(f"SELECT count() FROM guard.guard_events WHERE mapContains(LogAttributes, '{name}')") or 0)
                check(f"scrubbed attribute '{name}' is absent", n == 0, f"{n} row(s)")
            found = int(
                ch(
                    f"SELECT count() FROM guard.guard_events WHERE position(toString(LogAttributes), '{marker}') > 0 OR position(Body, '{marker}') > 0 OR position(toString(ResourceAttributes), '{marker}') > 0"
                )
                or 0
            )
            check("the prompt marker is stored nowhere", found == 0, f"{found} row(s)")
            for name in ("prompt", "prompt_text", "response"):
                loud = ch(
                    f"SELECT count() FROM guard.guard_events WHERE Timestamp >= toDateTime({started}) AND ServiceName != 'bmad-guard' "
                    f"AND LogAttributes['{name}'] != '' AND LogAttributes['{name}'] != '<REDACTED>'"
                )
                check(
                    f"every Claude Code '{name}' attribute is redacted or empty",
                    loud == "0",
                    f"{loud} row(s) not redacted",
                )
            check_metrics()
        check_scrub_synthetic(user_key)
        check_metrics_scrub_synthetic(user_key)

        check_grafana()

        base = commits["base"]
        code, text = run_report(["commits", "--range", f"{base}..HEAD"], project)
        print(text, flush=True)
        flagged = {line.split()[1] for line in text.splitlines() if line.startswith("  ")}
        want = {commits["blocked_only"][:12], commits["skipped"][:12]}
        check(
            "report 'commits' exits 1 and lists exactly the two bypassed commits",
            code == 1 and flagged == want,
            f"flagged {len(flagged)}",
        )
        check(
            "report 'commits' does not list the hooked commits",
            commits["clean"][:12] not in flagged and commits["records"][:12] not in flagged,
        )
        blocked_only = [line for line in text.splitlines() if commits["blocked_only"][:12] in line]
        check(
            "the refused-then-bypassed tree is labelled blocked-only",
            bool(blocked_only) and "blocked-only" in blocked_only[0],
        )

        code, text = run_report(["reviews"], project)
        print(text, flush=True)
        lines_ = [x for x in text.splitlines() if x.startswith("  ")]
        check(
            "report 'reviews' exits 1 and lists only the hand-made record",
            code == 1 and len(lines_) == 1 and "hand-made.json" in lines_[0],
        )
    finally:
        if not args.keep:
            keys.remove(DEV, path=keys.HTPASSWD, out=io.StringIO())
            compose("restart", "otelcol")
            shutil.rmtree(root, ignore_errors=True)  # only the temp folder created above
    failed = [name for name, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
