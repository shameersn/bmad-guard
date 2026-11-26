# /// script
# requires-python = ">=3.10"
# ///
"""Guard event sender with a local queue (ADR 0003).

`emit()` appends one event to the project's local queue
(`.bmad-guard/events/queue.jsonl`, git-ignored), then, if a collector is configured,
tries to send the whole queue as OTLP/HTTP JSON to `<collector>/v1/logs`.

- No collector configured (Phase 1 default): events only queue. No warning.
- Collector configured but down or refusing: events stay queued, one warning on stderr, and no new
  attempt for BACKOFF_SECONDS (a marker file next to the queue), so an unreachable collector does not
  slow every hook by the send timeout. `flush` always tries.
- `emit()` never raises, so a guard or a git commit never waits on logging.

Config: each value comes from the environment first, then from the per-user file
`~/.config/bmad-guard/credentials` (INI, section [collector]; written by scripts/setup_guard.py --credentials).
The file is read only when a value is missing, and only if it is safe: a regular file you own, mode 600, in a folder
others cannot write. Otherwise it is not used, events queue, and the reason is shown by `flush` (ADR 0007).
On Windows (ADR 0009) the file is `%USERPROFILE%/.config/bmad-guard/credentials`; there are no mode bits, so the check
is: a regular file, and neither it nor its folder is a link or junction. There is no queue lock either: `emit()` only
appends one line there, and `flush` (the SessionStart hook, `guard_events.py flush`) sends.
  BMAD_GUARD_ENDPOINT   (file: endpoint) base URL, e.g. https://guard-events.example.internal
                        (BMAD_GUARD_COLLECTOR is read when it is not set: deprecated, one release; ADR 0007)
  BMAD_GUARD_USER       (file: user) developer id (the collector stamps the real user from the key)
  BMAD_GUARD_KEY        (file: key) developer ingest key (never printed)

CLI:
  python3 guard_events.py flush [--project-root DIR]   send what is queued
"""

from __future__ import annotations

import argparse
import base64
import configparser
import errno
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

try:  # file locking: macOS and Linux; Windows runs without a lock (ADR 0009; msvcrt is a later step)
    import fcntl
except ImportError:  # pragma: no cover - Windows only
    fcntl = None

IS_WINDOWS = os.name == "nt"  # tests set it: there are no POSIX mode bits to check, and HOME is not the home folder

QUEUE_REL = Path(".bmad-guard/events/queue.jsonl")
CREDENTIALS_REL = Path(".config/bmad-guard/credentials")  # under HOME; ADR 0007
SEVERITY = {"info": 9, "allow": 9, "warn": 13, "block": 17}  # OTel INFO, WARN, ERROR
SEND_AFTER_NAME = "send-after"  # marker next to the queue: no send attempt before this Unix time
BACKOFF_SECONDS = 300.0
SERVICE_NAME = "bmad-guard"
EVENT_NAME = "guard.decision"


def credentials_path(env: dict[str, str] | None = None) -> Path | None:
    """`<HOME>/.config/bmad-guard/credentials`. With an explicit `env` that has no HOME: None (no file).
    On Windows the home folder is `Path.home()` (USERPROFILE), not HOME: in Git Bash HOME can be `/c/Users/x`."""
    if IS_WINDOWS:
        home = str(Path.home()) if env is None else env.get("USERPROFILE", "")
    elif env is None:
        home = os.environ.get("HOME") or str(Path.home())
    else:
        home = env.get("HOME", "")
    return Path(home) / CREDENTIALS_REL if home else None


def _is_reparse_point(path: Path) -> bool:
    """True for a link or junction on Windows (`st_file_attributes`). Raises OSError when the path cannot be read."""
    return bool(getattr(os.lstat(path), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _windows_problem(path: Path) -> str:
    """Windows has no mode bits to check. The file and its folder must not be a link or a junction (it could point
    anywhere). The ACL is set by setup (`icacls`, best effort) and is not read here (ADR 0009)."""
    for what, item in (("file", path), ("folder", path.parent)):
        try:
            if _is_reparse_point(item):
                return f"credential {what} {item} is a link or junction (not used)"
        except (FileNotFoundError, NotADirectoryError):
            continue  # missing: the open below says so, and a missing file is not a problem
        except OSError as err:
            return f"credential {what} {item} cannot be checked ({type(err).__name__})"
    return ""


def _posix_problem(info: os.stat_result, path: Path) -> str:
    """Owner, file mode and folder mode of the open file, or "" when all are fine."""
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        return f"credential file {path} is not owned by you (not used)"
    if info.st_mode & 0o077:
        mode = stat.S_IMODE(info.st_mode)
        return (
            f"credential file {path} has mode {mode:03o}; group and others must have no access (not used). "
            f"Fix: chmod 600 {path}"
        )
    try:
        folder_mode = stat.S_IMODE(os.stat(path.parent).st_mode)
    except OSError as err:
        return f"credential folder {path.parent} cannot be checked ({type(err).__name__})"
    if folder_mode & 0o022:
        return f"folder {path.parent} can be written by group or others (not used). Fix: chmod 700 {path.parent}"
    return ""


def read_credential_file(path: Path) -> tuple[dict[str, str], str]:
    """Read the credential file (ADR 0007). Returns (values, problem).

    A missing file is not a problem: ({}, ""). A file that is not safe to use (a link, not a regular file, not
    yours, open to group or others, in a folder others can write) or that cannot be parsed gives ({}, reason).
    The file is judged by its open handle (`fstat`), so it cannot be swapped between the check and the read. The
    reason never holds file content: for a parse error only the error type.

    On Windows (ADR 0009) there are no mode bits: the owner and mode checks are skipped, and the file and its folder
    must not be a link or junction (checked just before the open). Still a regular file, parsed the same way.
    """
    path = Path(path)
    if IS_WINDOWS:
        problem = _windows_problem(path)
        if problem:
            return {}, problem
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except (FileNotFoundError, NotADirectoryError):
        return {}, ""
    except OSError as err:
        if err.errno == errno.ELOOP:
            return {}, f"credential file {path} is a symbolic link (not used)"
        return {}, f"credential file {path} cannot be opened ({type(err).__name__})"
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return {}, f"credential file {path} is not a regular file (not used)"
        problem = "" if IS_WINDOWS else _posix_problem(info, path)
        if problem:
            return {}, problem
        parser = configparser.ConfigParser(interpolation=None)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1  # the file object owns it now
            try:
                parser.read_string(handle.read())
            except (configparser.Error, UnicodeDecodeError) as err:  # the library's message quotes the bad line
                return {}, f"credential file {path} cannot be parsed ({type(err).__name__}); run setup again"
    finally:
        if fd >= 0:
            os.close(fd)
    values = {name: parser.get("collector", name, fallback="").strip() for name in ("endpoint", "user", "key")}
    return values, ""


@dataclass
class Config:
    queue: Path
    endpoint: str = ""
    user: str = ""
    key: str = ""
    timeout: float = 3.0
    backoff: float = BACKOFF_SECONDS
    key_source: str = "env"  # "env" or "file": where the key came from (for the 401 hint)
    credential_problem: str = ""  # why the credential file was not used, if it was refused

    @property
    def send_after_file(self) -> Path:
        return self.queue.with_name(SEND_AFTER_NAME)

    @property
    def sending_enabled(self) -> bool:
        return bool(self.endpoint)

    @classmethod
    def for_project(
        cls, project_root: Path, env: dict[str, str] | None = None, credentials_file: Path | None = None
    ) -> Config:
        """Each of endpoint, user and key: the environment first, then the credential file (ADR 0007). The file is
        read only when a value is missing. With an explicit `env` that has no HOME the file is skipped."""
        path = credentials_file or credentials_path(env)
        env = os.environ if env is None else env
        endpoint = env.get("BMAD_GUARD_ENDPOINT") or env.get("BMAD_GUARD_COLLECTOR", "")
        user, key = env.get("BMAD_GUARD_USER", ""), env.get("BMAD_GUARD_KEY", "")
        values: dict[str, str] = {}
        problem = ""
        if path is not None and not (endpoint and user and key):
            values, problem = read_credential_file(path)
        return cls(
            queue=project_root / QUEUE_REL,
            endpoint=endpoint or values.get("endpoint", ""),
            user=user or values.get("user", ""),
            key=key or values.get("key", ""),
            key_source="env" if key or not values.get("key") else "file",
            credential_problem=problem,
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
        why = f": {config.credential_problem}" if config.credential_problem else ""
        raise SendError(f"collector URL, user or key not set{why}")
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


def _backing_off(config: Config) -> bool:
    """True while an earlier failure's backoff runs. A missing or unreadable marker means no backoff."""
    try:
        return time.time() < float(config.send_after_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False


def _set_backoff(config: Config) -> None:
    if config.backoff <= 0:
        return  # no backoff: every call tries
    try:  # full precision: a rounded time could start a backoff that was not asked for
        config.send_after_file.write_text(f"{time.time() + config.backoff!r}\n", encoding="utf-8")
    except OSError:
        pass  # no marker: the next emit tries again, which is only slower


def _clear_backoff(config: Config) -> None:
    try:
        config.send_after_file.unlink()
    except OSError:
        pass


def emit(event: dict | None, config: Config, stderr=None, force: bool = False) -> dict:
    """Queue the event (if any), then send the queue when a collector is configured. Never raises.

    After a failed send, later calls only queue until the backoff ends (no wait, no warning). `force` (the
    `flush` command) tries anyway. Returns {"sent": n, "queued": n, "error": str | None}; queued = events still
    waiting.
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
                if fcntl is None:  # no lock (Windows): only append one line; sending is `flush`'s job (ADR 0009)
                    return {"sent": 0, "queued": -1, "error": None}  # -1: not counted
            events = _read_queue(handle)
            if not events or not config.sending_enabled:
                return {"sent": 0, "queued": len(events), "error": config.credential_problem or None}
            if not force and _backing_off(config):
                return {"sent": 0, "queued": len(events), "error": None}
            try:
                send(events, config)
            except SendError as err:
                _set_backoff(config)
                hint = ""
                if err.status == 401:
                    where = "BMAD_GUARD_KEY" if config.key_source == "env" else "the credential file"
                    hint = f" (the key is from {where})"
                print(
                    f"bmad-guard: warning: could not send {len(events)} event(s): {err.reason}{hint}. "
                    f"Kept in {QUEUE_REL}; next try in {config.backoff / 60:.0f} min or with `guard_events.py flush`.",
                    file=stderr,
                )
                return {"sent": 0, "queued": len(events), "error": err.reason}
            tail = handle.read() if fcntl is None else ""  # no lock: lines appended since the read stay in the queue
            handle.truncate(0)
            handle.write(tail)
            _clear_backoff(config)
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
    print(json.dumps(emit(None, Config.for_project(args.project_root.resolve()), force=True)))
    return 0  # logging never blocks


if __name__ == "__main__":
    sys.exit(main())
