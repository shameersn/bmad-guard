# /// script
# requires-python = ">=3.10"
# ///
"""T3 spike: guard event sender with a local queue.

Every guard decision becomes one event. `emit()` appends the event to a local
queue, then tries to send the whole queue to the OTel collector (OTLP/HTTP JSON,
/v1/logs). If sending fails, the events stay queued and a warning goes to stderr.
`emit()` never raises, so a guard (or a git commit) never waits on the log server.

Config (environment variables):
  BMAD_GUARD_COLLECTOR  base URL, e.g. http://127.0.0.1:4318
  BMAD_GUARD_USER       developer id (the collector stamps the real user from the key)
  BMAD_GUARD_KEY        developer ingest key
  BMAD_GUARD_QUEUE      queue file (default .bmad-guard/events/queue.jsonl)
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

DEFAULT_QUEUE = Path(".bmad-guard/events/queue.jsonl")
SEVERITY = {"allow": 9, "warn": 13, "block": 17}  # OTel INFO, WARN, ERROR


@dataclass
class Config:
    endpoint: str
    user: str
    key: str
    queue: Path
    timeout: float = 3.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        return cls(
            endpoint=env.get("BMAD_GUARD_COLLECTOR", ""),
            user=env.get("BMAD_GUARD_USER", ""),
            key=env.get("BMAD_GUARD_KEY", ""),
            queue=Path(env.get("BMAD_GUARD_QUEUE", str(DEFAULT_QUEUE))),
        )


class SendError(Exception):
    def __init__(self, reason: str, status: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def build_event(guard: str, stage: str, verdict: str, reason_code: str, **fields: str) -> dict:
    """Build one guard event. Extra fields (project, target_path, ...) are kept as strings."""
    if verdict not in SEVERITY:
        raise ValueError(f"unknown verdict: {verdict}")
    event = {
        "event_id": str(uuid.uuid4()),
        "ts_ns": time.time_ns(),
        "guard": guard,
        "stage": stage,
        "verdict": verdict,
        "reason_code": reason_code,
    }
    event.update({k: str(v) for k, v in fields.items()})
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
                "severityNumber": SEVERITY[e["verdict"]],
                "eventName": "guard.decision",
                "body": {"stringValue": e["reason_code"]},
                "attributes": [{"key": k, "value": {"stringValue": str(v)}} for k, v in attrs.items()],
            }
        )
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "bmad-guard"}}]},
                "scopeLogs": [{"scope": {"name": "bmad-guard"}, "logRecords": records}],
            }
        ]
    }


def send(events: list[dict], config: Config) -> None:
    """POST events to the collector. Raises SendError on any failure."""
    if not config.endpoint or not config.user or not config.key:
        raise SendError("collector not configured")
    auth = base64.b64encode(f"{config.user}:{config.key}".encode()).decode()
    request = urllib.request.Request(
        f"{config.endpoint.rstrip('/')}/v1/logs",
        data=json.dumps(to_otlp(events)).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
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
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn line from a crash; drop it
    return events


def emit(event: dict | None, config: Config, stderr=sys.stderr) -> dict:
    """Queue the event (if any), then try to send the whole queue. Never raises.

    Returns {"sent": n, "queued": n, "error": str | None}.
    """
    try:
        config.queue.parent.mkdir(parents=True, exist_ok=True)
        with open(config.queue, "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            if event is not None:
                handle.write(json.dumps(event) + "\n")
                handle.flush()
            events = _read_queue(handle)
            if not events:
                return {"sent": 0, "queued": 0, "error": None}
            try:
                send(events, config)
            except SendError as err:
                hint = " (check BMAD_GUARD_KEY)" if err.status == 401 else ""
                print(
                    f"bmad-guard: warning: could not send {len(events)} event(s): {err.reason}{hint}. "
                    f"Kept in {config.queue}; they will be sent next time.",
                    file=stderr,
                )
                return {"sent": 0, "queued": len(events), "error": err.reason}
            handle.truncate(0)
            return {"sent": len(events), "queued": 0, "error": None}
    except Exception as err:  # never block the caller
        print(f"bmad-guard: warning: event logging failed: {type(err).__name__}", file=stderr)
        return {"sent": 0, "queued": -1, "error": type(err).__name__}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Send a guard event, or flush the queue.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("emit")
    e.add_argument("--guard", required=True)
    e.add_argument("--stage", required=True)
    e.add_argument("--verdict", required=True, choices=sorted(SEVERITY))
    e.add_argument("--reason-code", required=True)
    e.add_argument("--field", action="append", default=[], help="extra key=value (repeatable)")
    sub.add_parser("flush")
    args = parser.parse_args(argv)

    config = Config.from_env()
    event = None
    if args.cmd == "emit":
        extra = dict(item.split("=", 1) for item in args.field)
        event = build_event(args.guard, args.stage, args.verdict, args.reason_code, **extra)
    print(json.dumps(emit(event, config)))
    return 0  # always 0: logging never blocks


if __name__ == "__main__":
    raise SystemExit(main())
