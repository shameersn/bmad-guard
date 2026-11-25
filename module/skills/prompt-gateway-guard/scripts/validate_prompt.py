# /// script
# requires-python = ">=3.10"
# ///
"""Send text to PromptGateway's POST /api/v1/validate and print its decision.

The text is read from stdin, never from the command line (a command line is visible to other
users in the process list). The API key comes only from the BMAD_GUARD_GATEWAY_KEY environment
variable, never from a file in git or the command line. Plain http is refused except to localhost.

Prints one JSON object:
  PromptGateway's response: {"request_id", "decision": "ALLOW"|"FLAG"|"BLOCK", "risk_score",
                             "categories_triggered", "reason", "sanitized_prompt", ...}
  {"result": "unreachable", "base_url": ..., "error": ...}   the service could not be reached
  {"result": "http_error", "status": <code>, "body": ...}    the service answered with an error
  {"result": "refused", "error": ...}                        bad URL or text; nothing was sent

BLOCK, FLAG and an unavailable gateway are recorded as guard events (guard "gateway"),
with risk score, categories and request id only. The text itself is never recorded.

Usage:
    python3 validate_prompt.py --base-url https://gateway.example.internal < prompt.txt
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

KEY_ENV = "BMAD_GUARD_GATEWAY_KEY"
MAX_CHARS = 50_000
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
BODY_MAX = 500


def check_url(base_url: str) -> str | None:
    """Return an error message, or None when the URL is allowed."""
    parsed = urlparse(base_url)
    if parsed.scheme == "https" and parsed.hostname:
        return None
    if parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS:
        return None
    return "gateway URL must use https (plain http only for localhost)"


def validate(
    prompt: str,
    base_url: str,
    api_key: str = "",
    model: str = "",
    user_id: str = "",
    timeout: float = 15.0,
) -> dict:
    """Call the gateway. Never raises; failures come back as a {"result": ...} dict."""
    if not prompt.strip():
        return {"result": "refused", "error": "no text to check"}
    if len(prompt) > MAX_CHARS:
        return {"result": "refused", "error": f"text is longer than {MAX_CHARS} characters"}
    url_error = check_url(base_url)
    if url_error:
        return {"result": "refused", "error": url_error}

    body = {"prompt": prompt}
    if user_id:
        body["user_id"] = user_id
    if model:
        body["model"] = model
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(  # noqa: S310 - scheme checked above
        base_url.rstrip("/") + "/api/v1/validate",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")[:BODY_MAX]
        return {"result": "http_error", "status": exc.code, "body": text}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"result": "unreachable", "base_url": base_url, "error": type(exc).__name__}
    if not isinstance(result, dict) or result.get("decision") not in ("ALLOW", "FLAG", "BLOCK"):
        return {"result": "unreachable", "base_url": base_url, "error": "unexpected response"}
    return result


# --- events --------------------------------------------------------------------------------------


def _guard_events():
    """Ledger's sender from the sibling skill folder; None when Ledger is not installed."""
    path = Path(__file__).resolve().parents[2] / "agent-project-logger" / "scripts" / "guard_events.py"
    if not path.is_file():
        return None
    if "guard_events" in sys.modules:
        return sys.modules["guard_events"]
    spec = importlib.util.spec_from_file_location("guard_events", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["guard_events"] = module
    spec.loader.exec_module(module)
    return module


def project_root(cwd: Path | None = None) -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    folder = cwd or Path.cwd()
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=folder, capture_output=True, text=True)
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else folder


def record(result: dict, root: Path, source: str, session_id: str = "") -> None:
    """Record BLOCK, FLAG and an unavailable gateway as events. ALLOW is not recorded. Never raises."""
    decision = result.get("decision")
    if decision == "BLOCK":
        verdict, code = "block", "GATEWAY_BLOCK"
    elif decision == "FLAG":
        verdict, code = "warn", "GATEWAY_FLAG"
    elif result.get("result") in ("unreachable", "http_error"):
        verdict, code = "warn", "GATEWAY_UNAVAILABLE"
    else:
        return
    try:
        ge = _guard_events()
        if ge is None:
            print(
                "bmad-guard: warning: Ledger (agent-project-logger) not installed; event not recorded", file=sys.stderr
            )
            return
        event = ge.build_event(
            "gateway",
            "",
            verdict,
            code,
            agent=source,
            request_id=result.get("request_id", ""),
            risk_score=result.get("risk_score", ""),
            categories=",".join(result.get("categories_triggered", []) or []),
            gateway_status=result.get("status", ""),
            session_id=session_id,
        )
        ge.emit(event, ge.Config.for_project(root))
    except Exception as err:  # logging never blocks
        print(f"bmad-guard: warning: event not recorded ({type(err).__name__})", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check text with PromptGateway. The text is read from stdin.")
    parser.add_argument("--base-url", required=True, help="gateway base URL, e.g. https://gateway.example.internal")
    parser.add_argument("--model", default="", help="downstream model name (must be on the gateway's allowed list)")
    parser.add_argument("--user-id", default="", help="optional user id sent with the request")
    parser.add_argument("--timeout", type=float, default=15.0, help="seconds (default: %(default)s)")
    parser.add_argument("--no-event", action="store_true", help="do not record a guard event")
    parser.add_argument("-o", "--output", help="write the result JSON here instead of stdout")
    args = parser.parse_args(argv)

    result = validate(
        sys.stdin.read(), args.base_url, os.environ.get(KEY_ENV, ""), args.model, args.user_id, args.timeout
    )
    if not args.no_event:
        record(result, project_root(), "prompt-gateway-guard/VAL")
    text = json.dumps(result)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
