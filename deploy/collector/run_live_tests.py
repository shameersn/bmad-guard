# /// script
# requires-python = ">=3.10"
# ///
"""Live tests against the running local stack (spike tests 1-5 and 7, plus checks for the T2 hardening).

Run from this folder after `docker compose up -d`:  python3 run_live_tests.py

- Uses FAKE developers `live-test-a` and `live-test-b`. It creates them with keys.py (bcrypt), keeps the keys
  in memory only, and removes both at the end. It never prints or writes a key.
- Reads the Grafana admin password from secrets.local.ini and never prints it.
- Sends events with the frozen spike sender (spikes/collector/guard_events.py). The real sender is built in T3.
- It restarts the collector (to load the new htpasswd file) and stops/starts it for test 4.
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPIKE_SENDER = HERE.parent.parent / "spikes" / "collector" / "guard_events.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ge = load("guard_events", SPIKE_SENDER)
keys = load("collector_keys", HERE / "keys.py")

COLLECTOR = "http://127.0.0.1:4318"
GRAFANA = "http://127.0.0.1:3000"
DEV_A, DEV_B = "live-test-a", "live-test-b"
RESULTS: list[tuple[str, bool, str]] = []
KEYS: dict[str, str] = {}  # in memory only


def compose(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=HERE, capture_output=True, text=True, check=check)


def grafana_password() -> str:
    for line in (HERE / "secrets.local.ini").read_text().splitlines():
        if line.startswith("GRAFANA_ADMIN_PASSWORD="):
            return line.split("=", 1)[1]
    raise KeyError("GRAFANA_ADMIN_PASSWORD")


def ch(query: str, user: str = "admin") -> subprocess.CompletedProcess:
    """Run a query inside the ClickHouse container. The password is expanded inside the container."""
    pw_var = {"admin": "CLICKHOUSE_PASSWORD", "otel_writer": "OTEL_WRITER_PASSWORD"}[user]
    return compose(
        "exec",
        "-T",
        "-e",
        f"Q={query}",
        "clickhouse",
        "sh",
        "-c",
        f'clickhouse-client --user {user} --password "${pw_var}" -q "$Q"',
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


def cfg(dev: str, queue: Path, key_value: str | None = None) -> ge.Config:
    return ge.Config(endpoint=COLLECTOR, user=dev, key=key_value or KEYS[dev], queue=queue, timeout=3)


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)


def issue_test_key(dev: str, rotate: bool = False) -> None:
    """Create or rotate a fake developer with keys.py. The key is read from its output, in memory."""
    out = io.StringIO()
    keys.issue(dev, path=keys.HTPASSWD, hasher=keys.bcrypt_hash, rotate=rotate, out=out)
    lines = out.getvalue().splitlines()
    KEYS[dev] = lines[lines.index(next(x for x in lines if x.startswith("KEY for "))) + 1]


def wait_collector(limit: float = 60) -> bool:
    for _ in range(int(limit)):
        probe = ge.Config(endpoint=COLLECTOR, user=DEV_A, key=KEYS[DEV_A], queue=HERE / "unused", timeout=2)
        try:
            ge.send([], probe)
            return True
        except Exception:  # noqa: BLE001 - any failure means "not ready yet"
            time.sleep(1)
    return False


def status_of(dev: str, key_value: str) -> int:
    """HTTP status of an empty OTLP logs request with this key."""
    token = base64.b64encode(f"{dev}:{key_value}".encode()).decode()
    req = urllib.request.Request(
        f"{COLLECTOR}/v1/logs",
        data=b'{"resourceLogs":[]}',
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code


def test_1_happy(tmp: Path) -> None:
    run = f"t1-{uuid.uuid4().hex[:8]}"
    c = cfg(DEV_A, tmp / "q1.jsonl")
    start = time.monotonic()
    sent = sum(
        ge.emit(
            ge.build_event("sentry", "prd", "allow", "OK", run_id=run, project="demo-api"), c, stderr=io.StringIO()
        )["sent"]
        for _ in range(10)
    )
    delay = wait_count(run, 10)
    total = None if delay is None else time.monotonic() - start
    record(
        "1 happy path",
        sent == 10 and delay is not None,
        f"sent={sent}, stored={count(run)}, all visible after {total:.1f}s"
        if total
        else f"sent={sent}, stored={count(run)}",
    )


def test_2_stamping(tmp: Path) -> None:
    run = f"t2-{uuid.uuid4().hex[:8]}"
    c = cfg(DEV_B, tmp / "q2.jsonl")
    ge.emit(ge.build_event("warden", "prd", "block", "FAKE_USER", run_id=run, user=DEV_A), c, stderr=io.StringIO())
    wait_count(run, 1)
    stored = ch(f"SELECT User FROM guard.guard_events WHERE LogAttributes['run_id'] = '{run}'").stdout.strip()
    record("2 user stamping", stored == DEV_B, f"client claimed {DEV_A}, stored user = {stored or '(none)'}")


def test_3_bad_key(tmp: Path) -> None:
    run = f"t3-{uuid.uuid4().hex[:8]}"
    c = cfg(DEV_A, tmp / "q3.jsonl", key_value="not-the-real-key")
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
    c = cfg(DEV_A, q)
    compose("stop", "otelcol", check=True)
    err = io.StringIO()
    results = [ge.emit(ge.build_event("ledger", "prd", "allow", "OK", run_id=run), c, stderr=err) for _ in range(3)]
    queued = results[-1]["queued"]
    warned = "warning" in err.getvalue()
    compose("start", "otelcol", check=True)
    drained = {"sent": 0}
    for _ in range(30):  # wait for the collector to accept again
        time.sleep(1)
        drained = ge.emit(None, c, stderr=io.StringIO())
        if drained["sent"]:
            break
    stored_delay = wait_count(run, 3)
    record(
        "4 server down",
        queued == 3 and warned and drained["sent"] == 3 and stored_delay is not None,
        f"queued={queued}, warned={warned}, drained={drained['sent']}, stored={count(run)}, "
        f"queue now {q.stat().st_size} bytes",
    )


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


def test_6_rotation() -> None:
    """T2 extra: after rotate + reload, the old key gets 401 and the new key works."""
    old = KEYS[DEV_B]
    before = status_of(DEV_B, old)
    issue_test_key(DEV_B, rotate=True)
    during = status_of(DEV_B, old)  # file changed, collector not reloaded yet
    compose("restart", "otelcol", check=True)
    ready = wait_collector()
    after_old, after_new = status_of(DEV_B, old), status_of(DEV_B, KEYS[DEV_B])
    record(
        "6 key rotation (extra)",
        ready and before == 200 and after_old == 401 and after_new == 200,
        f"old key before={before}, after rotate but before reload={during}, after reload: old={after_old}, "
        f"new={after_new}",
    )


def test_7_hardening() -> None:
    """T2 extra: bcrypt hashes in the file, 180-day TTL, Grafana plugin settings, users and ports."""
    lines = [x for x in keys.HTPASSWD.read_text().splitlines() if x.strip()]
    bcrypt_ok = bool(lines) and all(x.split(":", 1)[1].startswith("$2y$10$") for x in lines)
    ddl = ch("SHOW CREATE TABLE guard.guard_events").stdout
    ttl_ok = "toIntervalDay(180)" in ddl or "INTERVAL 180 DAY" in ddl
    reader = ch("SHOW CREATE USER grafana_reader").stdout
    reader_ok = "readonly = 2" in reader or "READONLY = 2" in reader.upper()
    auth = base64.b64encode(f"admin:{grafana_password()}".encode()).decode()
    req = urllib.request.Request(f"{GRAFANA}/api/admin/settings", headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        settings = json.loads(resp.read())
    plugins = settings.get("plugins", {})
    pinned = [x.strip() for x in plugins.get("preinstall_sync", "").split(",") if x.strip()]
    graf_ok = (
        plugins.get("preinstall_auto_update") == "false"
        and plugins.get("plugin_admin_enabled") == "false"
        and settings.get("analytics", {}).get("check_for_plugin_updates") == "false"
        and all("@" in x for x in pinned)
        and "grafana-clickhouse-datasource@4.23.5" in pinned
    )
    ports = compose("ps", "--format", "{{.Name}} {{.Publishers}}").stdout
    local_only = "0.0.0.0" not in ports and "[::]" not in ports
    record(
        "7 hardening checks (extra)",
        bcrypt_ok and ttl_ok and reader_ok and graf_ok and local_only,
        f"bcrypt hashes={bcrypt_ok}, TTL 180 days={ttl_ok}, reader readonly=2={reader_ok}, "
        f"grafana plugin settings={graf_ok}, ports on 127.0.0.1 only={local_only}",
    )


def test_8_grafana() -> None:
    auth = base64.b64encode(f"admin:{grafana_password()}".encode()).decode()
    dash = json.loads((HERE / "grafana/dashboards/guard-events.json").read_text())
    target = dash["panels"][0]["targets"][0]
    body = {"from": "now-1h", "to": "now", "queries": [target]}
    req = urllib.request.Request(
        f"{GRAFANA}/api/ds/query",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        users = data["results"]["A"]["frames"][0]["data"]["values"][0]
        ok = DEV_A in users and DEV_B in users
        detail = f"panel rows={len(users)}, users={sorted(set(users))}"
    except Exception as err:  # noqa: BLE001
        ok, detail = False, f"{type(err).__name__}: {err}"
    req = urllib.request.Request(
        f"{GRAFANA}/api/dashboards/uid/guard-events", headers={"Authorization": f"Basic {auth}"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        provisioned = resp.status == 200
    record("8 dashboard (spike test 7)", ok and provisioned, f"{detail}; dashboard provisioned={provisioned}")


def main() -> int:
    issue_test_key(DEV_A)
    issue_test_key(DEV_B)
    try:
        compose("restart", "otelcol", check=True)  # load the new htpasswd file
        if not wait_collector():
            print("FAIL  collector did not become ready")
            return 1
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            test_1_happy(t)
            test_2_stamping(t)
            test_3_bad_key(t)
            test_4_server_down(t)
        test_5_insert_only()
        test_6_rotation()
        test_7_hardening()
        test_8_grafana()
    finally:
        with contextlib.suppress(keys.KeysError):
            keys.remove(DEV_A, path=keys.HTPASSWD, out=io.StringIO())
        with contextlib.suppress(keys.KeysError):
            keys.remove(DEV_B, path=keys.HTPASSWD, out=io.StringIO())
        compose("restart", "otelcol")
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
