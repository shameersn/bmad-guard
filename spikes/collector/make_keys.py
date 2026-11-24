# /// script
# requires-python = ">=3.10"
# ///
"""T3 spike: create random stack passwords and fake developer ingest keys.

Writes (all git-ignored):
  .env                    stack passwords (only if it does not exist yet)
  work/keys/<dev>.key     one random ingest key per developer
  work/devkeys.htpasswd   {SSHA} hashes of those keys, read by the collector

Prints file paths only, never a secret value.

Usage:
    python3 make_keys.py [--dev dev-a --dev dev-b]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import secrets
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
WORK = HERE / "work"
PASSWORD_VARS = (
    "CLICKHOUSE_ADMIN_PASSWORD",
    "CLICKHOUSE_WRITER_PASSWORD",
    "CLICKHOUSE_READER_PASSWORD",
    "GRAFANA_ADMIN_PASSWORD",
)


def ssha(password: str, salt: bytes | None = None) -> str:
    """Return an htpasswd {SSHA} hash: base64(sha1(password + salt) + salt)."""
    salt = salt if salt is not None else secrets.token_bytes(8)
    digest = hashlib.sha1(password.encode() + salt).digest()
    return "{SSHA}" + base64.b64encode(digest + salt).decode()


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)


def make_env(env_file: Path) -> bool:
    """Create the env file with random passwords. Returns False if it already exists."""
    if env_file.exists():
        return False
    lines = [f"{name}={secrets.token_urlsafe(24)}" for name in PASSWORD_VARS]
    write_private(env_file, "\n".join(lines) + "\n")
    return True


def make_dev_keys(devs: list[str], work: Path) -> Path:
    """Create one key per developer and the htpasswd file. Existing keys are kept."""
    entries = []
    for dev in devs:
        key_file = work / "keys" / f"{dev}.key"
        if not key_file.exists():
            write_private(key_file, secrets.token_urlsafe(32))
        entries.append(f"{dev}:{ssha(key_file.read_text().strip())}")
    htpasswd = work / "devkeys.htpasswd"
    write_private(htpasswd, "\n".join(entries) + "\n")
    # The collector container runs as a non-root user and must read this file.
    htpasswd.chmod(0o644)
    return htpasswd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dev", action="append", help="developer id (repeatable)")
    args = parser.parse_args(argv)
    devs = args.dev or ["dev-a", "dev-b"]

    print(f"{'created' if make_env(ENV_FILE) else 'kept existing'} {ENV_FILE.name}")
    print(f"wrote {make_dev_keys(devs, WORK).relative_to(HERE)} for: {', '.join(devs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
