# /// script
# requires-python = ">=3.10"
# ///
"""Guard event sender with a local queue (ADR 0003).

`emit()` appends one event to the project's local queue
(`.bmad-guard/events/queue.jsonl`, git-ignored), then, if a collector is configured,
tries to send the whole queue as OTLP/HTTP JSON to `<collector>/v1/logs`.

- No collector configured (Phase 1 default): events only queue. No warning.
- Collector configured but down or refusing: events stay queued, one warning on stderr.
- `emit()` never raises, so a guard or a git commit never waits on logging.

Config (environment variables):
  BMAD_GUARD_COLLECTOR  base URL, e.g. https://guard-events.example.internal
  BMAD_GUARD_USER       developer id (the collector stamps the real user from the key)
  BMAD_GUARD_KEY        developer ingest key (never printed)

CLI:
  python3 guard_events.py flush [--project-root DIR]   send what is queued
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

try:  # file locking: macOS and Linux; Windows runs without a lock (Phase 3 follow-up)
    import fcntl
except ImportError:  # pragma: no cover - Windows only
    fcntl = None

QUEUE_REL = Path(".bmad-guard/events/queue.jsonl")
SEVERITY = {"info": 9, "allow": 9, "warn": 13, "block": 17}  # OTel INFO, WARN, ERROR
SERVICE_NAME = "bmad-guard"
EVENT_NAME = "guard.decision"


@dataclass
class Config:
    queue: Path
    endpoint: str = ""
    user: str = ""
    key: str = ""
    timeout: float = 3.0

    @property
    def sending_enabled(self) -> bool:
        return bool(self.endpoint)

    @classmethod
    def for_project(cls, project_root: Path, env: dict[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        return cls(
            queue=project_root / QUEUE_REL,
            endpoint=env.get("BMAD_GUARD_COLLECTOR", ""),
            user=env.get("BMAD_GUARD_USER", ""),
            key=env.get("BMAD_GUARD_KEY", ""),
        )


class SendError(Exception):
    def __init__(self, reason: str, status: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def build_event(guard: str, stage: str, verdict: str, reason_code: str, **fields: object) -> dict:
    """Build one event. Empty extra fields are dropped; the rest become strings."""
    if verdict not in SEVERITY:
        raise ValueError(f"unknown verdict: {verdict} (use one of {', '.join(sorted(SEVERITY))})")
    event = {
        "event_id": str(uuid.uuid4()),
        "ts_ns": time.time_ns(),
        "guard": guard,
        "stage": stage,
        "verdict": verdict,
        "reason_code": reason_code,
    }
    event.update({k: str(v) for k, v in fields.items() if v not in (None, "")})
    return event


def to_otlp(events: list[dict]) -> dict:
    """Convert events to an OTLP/JSON logs request body."""
    records = []
    for e in events:
        attrs = {k: v for k, v in e.items() if k != "ts_ns"}
        records.append(
            {
                "timeUnixNano": str(e["ts_ns"]),
                "severityText": e["verdict"],
                "severityNumber": SEVERITY.get(e["verdict"], 9),
                "eventName": EVENT_NAME,
                "body": {"stringValue": e["reason_code"]},
                "attributes": [{"key": k, "value": {"stringValue": str(v)}} for k, v in attrs.items()],
            }
        )
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": SERVICE_NAME}}]},
                "scopeLogs": [{"scope": {"name": SERVICE_NAME}, "logRecords": records}],
            }
        ]
    }


def send(events: list[dict], config: Config) -> None:
    """POST events to the collector. Raises SendError on any failure."""
    if not (config.endpoint and config.user and config.key):
        raise SendError("collector URL, user or key not set")
    if not config.endpoint.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise SendError("collector URL must use https (plain http only for localhost)")
    auth = base64.b64encode(f"{config.user}:{config.key}".encode()).decode()
    request = urllib.request.Request(  # noqa: S310 - scheme checked above
        f"{config.endpoint.rstrip('/')}/v1/logs",
        data=json.dumps(to_otlp(events)).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:  # noqa: S310
            if response.status >= 300:
                raise SendError(f"HTTP {response.status}", response.status)
    except urllib.error.HTTPError as err:
        raise SendError(f"HTTP {err.code}", err.code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise SendError(f"collector unreachable ({type(err).__name__})") from None


def _read_queue(handle) -> list[dict]:
    handle.seek(0)
    events = []
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn line from a crash; drop it
    return events


def emit(event: dict | None, config: Config, stderr=None) -> dict:
    """Queue the event (if any), then send the queue when a collector is configured. Never raises.

    Returns {"sent": n, "queued": n, "error": str | None}; queued = events still waiting.
    """
    stderr = sys.stderr if stderr is None else stderr  # looked up per call, so redirection works
    try:
        config.queue.parent.mkdir(parents=True, exist_ok=True)
        with open(config.queue, "a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_EX)
            if event is not None:
                handle.write(json.dumps(event) + "\n")
                handle.flush()
            events = _read_queue(handle)
            if not events or not config.sending_enabled:
                return {"sent": 0, "queued": len(events), "error": None}
            try:
                send(events, config)
            except SendError as err:
                hint = " (check BMAD_GUARD_KEY)" if err.status == 401 else ""
                print(
                    f"bmad-guard: warning: could not send {len(events)} event(s): {err.reason}{hint}. "
                    f"Kept in {QUEUE_REL}; they will be sent next time.",
                    file=stderr,
                )
                return {"sent": 0, "queued": len(events), "error": err.reason}
            handle.truncate(0)
            return {"sent": len(events), "queued": 0, "error": None}
    except Exception as err:  # never block the caller
        print(f"bmad-guard: warning: event logging failed: {type(err).__name__}", file=stderr)
        return {"sent": 0, "queued": -1, "error": type(err).__name__}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send queued guard events to the collector.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    flush = sub.add_parser("flush", help="send what is queued (no-op when no collector is configured)")
    flush.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    print(json.dumps(emit(None, Config.for_project(args.project_root.resolve()))))
    return 0  # logging never blocks


if __name__ == "__main__":
    sys.exit(main())
