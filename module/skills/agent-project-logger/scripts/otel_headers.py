# /// script
# requires-python = ">=3.10"
# ///
"""Headers for Claude Code's own telemetry (`otelHeadersHelper`, GUARD-3 T4).

Claude Code runs this script at start and every 29 minutes. It prints one JSON object on stdout:

    {"Authorization": "Basic <base64 of developer-id:ingest-key>"}

The developer id and the key come from the same place as the guard events' sender (ADR 0007): the environment
(`BMAD_GUARD_USER`, `BMAD_GUARD_KEY`), then `~/.config/bmad-guard/credentials`. So the managed settings file holds no
key and is the same for every developer, and the collector stamps `user` from the developer's own key.

The key is never printed anywhere except in that header, on stdout, which only Claude Code reads. With no usable
developer id and key it prints nothing on stdout, says why on stderr (no value) and exits 1. Claude Code then shows
"otelHeadersHelper failed" and exports nothing, so a machine without credentials sends no telemetry.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import guard_events as ge  # noqa: E402 - sibling module, found through the line above


def headers(config: ge.Config) -> dict[str, str]:
    """The header, or raises ValueError (the message holds no value)."""
    if not (config.user and config.key):
        why = f": {config.credential_problem}" if config.credential_problem else ""
        raise ValueError(f"no developer id and key (set them with scripts/setup_guard.py --credentials){why}")
    token = base64.b64encode(f"{config.user}:{config.key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        description="Print the Authorization header for Claude Code telemetry (otelHeadersHelper)."
    ).parse_args(argv)
    try:
        print(json.dumps(headers(ge.Config.for_project(Path.cwd()))))
    except ValueError as err:
        print(f"bmad-guard otel_headers: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
