# /// script
# requires-python = ">=3.10"
# ///
"""T3 spike: live tests 1-5 and 7 against the running Docker stack.

Reads developer keys from work/keys/ and the Grafana password from .env.
Never prints a secret. Run from this folder after `docker compose up -d`.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("guard_events", HERE / "guard_events.py")
ge = importlib.util.module_from_spec(spec)
sys.modules["guard_events"] = ge
spec.loader.exec_module(ge)

COLLECTOR = "http://127.0.0.1:4318"
GRAFANA = "http://127.0.0.1:3000"
RESULTS: list[tuple[str, bool, str]] = []


def key(dev: str) -> str:
    return (HERE / "work" / "keys" / f"{dev}.key").read_text().strip()


def env_value(name: str) -> str:
    for line in (HERE / ".env").read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    raise KeyError(name)


def ch(query: str, user: str = "admin") -> subprocess.CompletedProcess:
    """Run a query inside the ClickHouse container. The password is expanded inside the container."""
    pw_var = {"admin": "CLICKHOUSE_PASSWORD", "otel_writer": "OTEL_WRITER_PASSWORD"}[user]
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "-e", f"Q={query}", "clickhouse", "sh", "-c",
         f'clickhouse-client --user {user} --password "${pw_var}" -q "$Q"'],
        cwd=HERE, capture_output=True, text=True,
    )


def count(run_id: str) -> int:
    out = ch(f"SELECT count() FROM guard.guard_events WHERE LogAttributes['run_id'] = '{run_id}'")
    return int(out.stdout.strip() or 0)


def wait_count(run_id: str, want: int, limit: float = 60) -> float | None:
    start = time.monotonic()
    while time.monotonic() - start < limit:
        if count(run_id) >= want:
            return time.monotonic() - start
        time.sleep(0.5)
    return None


def cfg(dev: str, queue: Path, key_value: str | None = None) -> "ge.Config":
    return ge.Config(endpoint=COLLECTOR, user=dev, key=key_value or key(dev), queue=queue, timeout=3)


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)


def test_1_happy(tmp: Path) -> None:
    run = f"t1-{uuid.uuid4().hex[:8]}"
    c = cfg("dev-a", tmp / "q1.jsonl")
    start = time.monotonic()
    sent = sum(ge.emit(ge.build_event("sentry", "prd", "allow", "OK", run_id=run, project="demo-api"),
                       c, stderr=io.StringIO())["sent"] for _ in range(10))
    delay = wait_count(run, 10)
    total = None if delay is None else time.monotonic() - start
    record("1 happy path", sent == 10 and delay is not None,
           f"sent={sent}, stored={count(run)}, all visible after {total:.1f}s" if total else f"sent={sent}, stored={count(run)}")


def test_2_stamping(tmp: Path) -> None:
    run = f"t2-{uuid.uuid4().hex[:8]}"
    c = cfg("dev-b", tmp / "q2.jsonl")
    ge.emit(ge.build_event("warden", "prd", "block", "FAKE_USER", run_id=run, user="dev-a"), c, stderr=io.StringIO())
    wait_count(run, 1)
    stored = ch(f"SELECT User FROM guard.guard_events WHERE LogAttributes['run_id'] = '{run}'").stdout.strip()
    record("2 user stamping", stored == "dev-b", f"client claimed dev-a, stored user = {stored or '(none)'}")


def test_3_bad_key(tmp: Path) -> None:
    run = f"t3-{uuid.uuid4().hex[:8]}"
    c = cfg("dev-a", tmp / "q3.jsonl", key_value="not-the-real-key")
    try:
        ge.send([ge.build_event("sentry", "prd", "allow", "OK", run_id=run)], c)
        status = 200
    except ge.SendError as err:
        status = err.status
    time.sleep(5)
    stored = count(run)
    record("3 bad key", status == 401 and stored == 0, f"HTTP {status}, stored={stored}")


def test_4_server_down(tmp: Path) -> None:
    run = f"t4-{uuid.uuid4().hex[:8]}"
    q = tmp / "q4.jsonl"
    c = cfg("dev-a", q)
    subprocess.run(["docker", "compose", "stop", "otelcol"], cwd=HERE, capture_output=True, check=True)
    err = io.StringIO()
    results = [ge.emit(ge.build_event("ledger", "prd", "allow", "OK", run_id=run), c, stderr=err) for _ in range(3)]
    queued = results[-1]["queued"]
    warned = "warning" in err.getvalue()
    subprocess.run(["docker", "compose", "start", "otelcol"], cwd=HERE, capture_output=True, check=True)
    drained = {"sent": 0}
    for _ in range(30):  # wait for the collector to accept again
        time.sleep(1)
        drained = ge.emit(None, c, stderr=io.StringIO())
        if drained["sent"]:
            break
    stored_delay = wait_count(run, 3)
    record("4 server down", queued == 3 and warned and drained["sent"] == 3 and stored_delay is not None,
           f"queued={queued}, warned={warned}, drained={drained['sent']}, stored={count(run)}, queue now {q.stat().st_size} bytes")


def test_5_insert_only() -> None:
    attempts = {
        "SELECT": "SELECT count() FROM guard.guard_events",
        "ALTER": "ALTER TABLE guard.guard_events ADD COLUMN x String",
        "DELETE": "ALTER TABLE guard.guard_events DELETE WHERE 1",
        "TRUNCATE": "TRUNCATE TABLE guard.guard_events",
        "DROP": "DROP TABLE guard.guard_events",
    }
    refused = {}
    for name, q in attempts.items():
        out = ch(q, user="otel_writer")
        refused[name] = out.returncode != 0 and ("ACCESS_DENIED" in out.stderr or "Not enough privileges" in out.stderr)
    still_there = ch("EXISTS TABLE guard.guard_events").stdout.strip() == "1"
    detail = ", ".join(f"{k} {'refused' if v else 'ALLOWED'}" for k, v in refused.items())
    record("5 insert-only user", all(refused.values()) and still_there, f"{detail}; table still exists={still_there}")


def test_7_grafana() -> None:
    auth = base64.b64encode(f"admin:{env_value('GRAFANA_ADMIN_PASSWORD')}".encode()).decode()
    dash = json.loads((HERE / "grafana/dashboards/guard-events.json").read_text())
    target = dash["panels"][0]["targets"][0]
    body = {"from": "now-1h", "to": "now", "queries": [target]}
    req = urllib.request.Request(f"{GRAFANA}/api/ds/query", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        frame = data["results"]["A"]["frames"][0]
        users = frame["data"]["values"][0]
        ok = "dev-a" in users and "dev-b" in users
        detail = f"panel rows={len(users)}, users={sorted(set(users))}"
    except Exception as err:
        ok, detail = False, f"{type(err).__name__}: {err}"
    with urllib.request.urlopen(urllib.request.Request(
            f"{GRAFANA}/api/dashboards/uid/guard-events", headers={"Authorization": f"Basic {auth}"}), timeout=10) as r:
        provisioned = r.status == 200
    record("7 dashboard", ok and provisioned, f"{detail}; dashboard provisioned={provisioned}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        test_1_happy(t)
        test_2_stamping(t)
        test_3_bad_key(t)
        test_4_server_down(t)
    test_5_insert_only()
    test_7_grafana()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
