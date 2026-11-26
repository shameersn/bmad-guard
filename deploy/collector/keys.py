# /// script
# requires-python = ">=3.10"
# ///
"""Ingest keys for the guard collector: add, rotate or remove a developer, and create the stack passwords.

    python3 keys.py init              create secrets.local.ini with random stack passwords and an empty
                                      local/devkeys.htpasswd (never overwrites either)
    python3 keys.py add <user>        new developer: random key, hash into local/devkeys.htpasswd
    python3 keys.py rotate <user>     new key for an existing developer (same id, history stays continuous)
    python3 keys.py remove <user>     remove the developer's hash

Rules this tool keeps:
- The key is made in memory, printed ONCE to the terminal, and never written to a file or a log.
- Only the bcrypt hash goes into the htpasswd file.
- Python has no bcrypt in the standard library. The hash is made by the `htpasswd` program inside a pinned
  `httpd` image (`docker run --rm -i`). The key goes in through stdin, so it is not in the process list.
- The collector reads the htpasswd file at start. After any change: `docker compose restart otelcol`.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = HERE / "local"
HTPASSWD = LOCAL / "devkeys.htpasswd"
SECRETS_FILE = HERE / "secrets.local.ini"
HTTPD_IMAGE = "httpd:2.4.66-alpine"  # pinned; provides the htpasswd program. Listed in README.md.
BCRYPT_COST = "10"
USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")  # no ":" (the htpasswd separator), no spaces
BCRYPT_PREFIX = "$2"  # $2y$ from htpasswd -B
PASSWORD_VARS = (
    "CLICKHOUSE_ADMIN_PASSWORD",
    "CLICKHOUSE_WRITER_PASSWORD",
    "CLICKHOUSE_READER_PASSWORD",
    "GRAFANA_ADMIN_PASSWORD",
)
RELOAD_HINT = "Reload the collector so it reads the new file:  docker compose restart otelcol"

Hasher = Callable[[str], str]


class KeysError(Exception):
    """A problem the operator can fix. Printed without a traceback."""


def bcrypt_hash(key: str) -> str:
    """Return the bcrypt hash of `key`, made by htpasswd in the pinned httpd image. The key goes in on stdin."""
    cmd = ["docker", "run", "--rm", "-i", HTTPD_IMAGE, "htpasswd", "-niB", "-C", BCRYPT_COST, "key"]
    try:
        done = subprocess.run(cmd, input=key + "\n", capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise KeysError(f"could not run docker to make the bcrypt hash: {type(err).__name__}") from err
    if done.returncode != 0:
        raise KeysError(f"htpasswd failed (docker exit {done.returncode}): {done.stderr.strip()[:200]}")
    _, _, hashed = done.stdout.strip().partition(":")
    if not hashed.startswith(BCRYPT_PREFIX):
        raise KeysError("htpasswd did not return a bcrypt hash")
    return hashed


def new_key() -> str:
    return secrets.token_urlsafe(32)


def check_user(user: str) -> str:
    if not USER_RE.match(user):
        raise KeysError("user id must be 2-64 characters: lower-case letters, digits, '.', '_' or '-'")
    return user


def read_entries(path: Path) -> dict[str, str]:
    """Read `user:hash` lines. Blank lines and `#` comments are ignored."""
    entries: dict[str, str] = {}
    if not path.exists():
        return entries
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        user, _, hashed = line.partition(":")
        entries[user] = hashed
    return entries


def write_entries(path: Path, entries: dict[str, str]) -> None:
    """Write the file in place (the container mounts the folder). Mode 644: it holds hashes only, and the
    non-root collector must read it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{user}:{hashed}\n" for user, hashed in entries.items())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    path.chmod(0o644)


def issue(user: str, *, path: Path, hasher: Hasher, rotate: bool, out) -> None:
    check_user(user)
    entries = read_entries(path)
    if rotate and user not in entries:
        raise KeysError(f"{user} is not in the file. Use 'add' for a new developer.")
    if not rotate and user in entries:
        raise KeysError(f"{user} already exists. Use 'rotate' to give a new key.")
    key = new_key()
    entries[user] = hasher(key)  # fails before anything is written
    write_entries(path, entries)
    verb = "rotated" if rotate else "added"
    print(f"{verb} {user}. Only the bcrypt hash was saved, in {path.name}.", file=out)
    print(f"KEY for {user} (shown once, it cannot be shown again):", file=out)
    print(key, file=out)
    print(
        "Give the user id and this key to the developer through a private channel (not chat, not a ticket).",
        file=out,
    )
    if rotate:
        print("Until the collector is reloaded the old key still works. After that it gets HTTP 401.", file=out)
    print(RELOAD_HINT, file=out)


def remove(user: str, *, path: Path, out) -> None:
    check_user(user)
    entries = read_entries(path)
    if user not in entries:
        raise KeysError(f"{user} is not in the file.")
    del entries[user]
    write_entries(path, entries)
    print(f"removed {user}.", file=out)
    print(RELOAD_HINT, file=out)


def init_secrets(path: Path, out) -> bool:
    """Create the stack password file with random values. Returns False (and keeps the file) if it exists."""
    if path.exists():
        print(f"{path.name} already exists. Kept as it is.", file=out)
        return False
    lines = [f"{name}={secrets.token_urlsafe(24).replace('-', 'x').replace('_', 'y')}" for name in PASSWORD_VARS]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"created {path.name} (mode 600, random values). It is git-ignored.", file=out)
    return True


def init_htpasswd(path: Path, out) -> bool:
    """Create an EMPTY key file if there is none, so a first `docker compose up` does not stop the collector
    (it refuses to start without the file). An empty file lets nobody in. Mode 644, like write_entries: the file holds
    hashes only and the non-root collector must read it. An existing file is never touched."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    os.close(fd)
    path.chmod(0o644)
    print(
        f"created empty {path.parent.name}/{path.name} (no developer can send yet: add one with `keys.py add`).",
        file=out,
    )
    return True


def main(argv: list[str] | None = None, *, hasher: Hasher = bcrypt_hash, path: Path = HTPASSWD, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create secrets.local.ini with random stack passwords")
    for name in ("add", "rotate", "remove"):
        sub.add_parser(name, help=f"{name} a developer key").add_argument("user")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            init_secrets(SECRETS_FILE, out)
            init_htpasswd(path, out)
        elif args.command == "remove":
            remove(args.user, path=path, out=out)
        else:
            issue(args.user, path=path, hasher=hasher, rotate=args.command == "rotate", out=out)
    except KeysError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
