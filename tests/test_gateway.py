# /// script
# requires-python = ">=3.10"
# ///
"""Tests for prompt-gateway-guard: validate_prompt.py and prompt_gateway_hook.py, against a stub gateway."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "module/skills/prompt-gateway-guard/scripts"
VALIDATE = SCRIPTS / "validate_prompt.py"
HOOK = SCRIPTS / "prompt_gateway_hook.py"
spec = importlib.util.spec_from_file_location("validate_prompt", VALIDATE)
vp = importlib.util.module_from_spec(spec)
sys.modules["validate_prompt"] = vp
spec.loader.exec_module(vp)

FAKE_SENSITIVE_PROMPT = "patient Jane Example, MRN 0000-FAKE, wants a refill"


class StubGateway:
    """Answers POST /api/v1/validate with a fixed decision, or a fixed HTTP error."""

    def __init__(self, decision: str = "ALLOW", status: int = 200):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append({"path": self.path, "body": body, "key": self.headers.get("X-API-Key")})
                if status != 200:
                    self.send_response(status)
                    self.end_headers()
                    self.wfile.write(b'{"detail": "model not allowed"}')
                    return
                answer = {
                    "request_id": "req-1",
                    "decision": decision,
                    "risk_score": {"ALLOW": 5, "FLAG": 55, "BLOCK": 92}[decision],
                    "categories_triggered": [] if decision == "ALLOW" else ["pii"],
                    "reason": "" if decision == "ALLOW" else "PII found",
                    "sanitized_prompt": "patient [NAME], MRN [ID], wants a refill",
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(answer).encode())

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def unused_url() -> str:
    server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_port
    server.server_close()
    return f"http://127.0.0.1:{port}"


def base_env(project: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BMAD_GUARD_", "CLAUDE_"))}
    env["CLAUDE_PROJECT_DIR"] = str(project)
    env.update(extra)
    return env


def events(project: Path) -> list[dict]:
    q = project / ".bmad-guard/events/queue.jsonl"
    return [json.loads(line) for line in q.read_text().splitlines()] if q.exists() else []


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        (self.project / "_bmad/custom").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def queue_text(self) -> str:
        q = self.project / ".bmad-guard/events/queue.jsonl"
        return q.read_text() if q.exists() else ""


class ValidateFunctionTests(unittest.TestCase):
    def test_allow_passthrough_and_body(self):
        with StubGateway("ALLOW") as gw:
            result = vp.validate("hello", gw.url, model="m1", user_id="u1")
            self.assertEqual(result["decision"], "ALLOW")
            self.assertEqual(gw.requests[0]["path"], "/api/v1/validate")
            self.assertEqual(gw.requests[0]["body"], {"prompt": "hello", "user_id": "u1", "model": "m1"})
            self.assertIsNone(gw.requests[0]["key"])

    def test_key_header_sent_when_given(self):
        with StubGateway() as gw:
            vp.validate("hello", gw.url, api_key="fake-key")
            self.assertEqual(gw.requests[0]["key"], "fake-key")

    def test_http_error_reports_status_and_short_body(self):
        with StubGateway(status=400) as gw:
            result = vp.validate("hello", gw.url)
            self.assertEqual((result["result"], result["status"]), ("http_error", 400))
            self.assertIn("model not allowed", result["body"])

    def test_unreachable(self):
        result = vp.validate("hello", unused_url(), timeout=1)
        self.assertEqual(result["result"], "unreachable")

    def test_refusals_send_nothing(self):
        cases = [
            ("hello", "http://gateway.example.invalid", "https"),
            ("hello", "ftp://localhost", "https"),
            ("   ", "https://gateway.example.invalid", "no text"),
            ("x" * (vp.MAX_CHARS + 1), "https://gateway.example.invalid", "longer than"),
        ]
        for prompt, url, words in cases:
            result = vp.validate(prompt, url)
            self.assertEqual(result["result"], "refused", url)
            self.assertIn(words, result["error"])

    def test_check_url(self):
        self.assertIsNone(vp.check_url("https://gw.example.invalid"))
        self.assertIsNone(vp.check_url("http://localhost:8000"))
        self.assertIsNotNone(vp.check_url("http://10.0.0.5:8000"))


class ValidateCliTests(Base):
    def run_cli(self, url: str, prompt: str = FAKE_SENSITIVE_PROMPT, **env: str):
        proc = subprocess.run(
            [sys.executable, str(VALIDATE), "--base-url", url],
            input=prompt,
            env=base_env(self.project, **env),
            capture_output=True,
            text=True,
        )
        return proc.returncode, json.loads(proc.stdout), proc.stderr

    def test_text_comes_from_stdin_and_key_from_env(self):
        with StubGateway("ALLOW") as gw:
            code, result, _ = self.run_cli(gw.url, BMAD_GUARD_GATEWAY_KEY="fake-key")
            self.assertEqual((code, result["decision"]), (0, "ALLOW"))
            self.assertEqual(gw.requests[0]["body"]["prompt"], FAKE_SENSITIVE_PROMPT)
            self.assertEqual(gw.requests[0]["key"], "fake-key")

    def test_cli_has_no_prompt_or_key_arguments(self):
        help_text = subprocess.run([sys.executable, str(VALIDATE), "--help"], capture_output=True, text=True).stdout
        self.assertNotIn("--prompt", help_text)
        self.assertNotIn("--api-key", help_text)

    def test_allow_is_not_recorded(self):
        with StubGateway("ALLOW") as gw:
            self.run_cli(gw.url)
        self.assertEqual(events(self.project), [])

    def test_block_and_flag_recorded_without_the_text(self):
        for decision in ("BLOCK", "FLAG"):
            with StubGateway(decision) as gw:
                self.run_cli(gw.url)
        recorded = events(self.project)
        self.assertEqual(
            [(e["reason_code"], e["verdict"]) for e in recorded], [("GATEWAY_BLOCK", "block"), ("GATEWAY_FLAG", "warn")]
        )
        self.assertEqual(recorded[0]["categories"], "pii")
        self.assertEqual(recorded[0]["request_id"], "req-1")
        self.assertNotIn("MRN", self.queue_text())
        self.assertNotIn("Jane", self.queue_text())

    def test_unavailable_gateway_recorded(self):
        self.run_cli(unused_url())
        self.assertEqual(events(self.project)[0]["reason_code"], "GATEWAY_UNAVAILABLE")


class HookTests(Base):
    def configure(self, url: str) -> None:
        (self.project / "_bmad/custom/prompt-gateway-guard.toml").write_text(
            f'[agent]\nbase_url = "{url}"  # test gateway\ntimeout = 3\n'
        )

    def run_hook(self, payload) -> tuple[int, dict | None]:
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        proc = subprocess.run(
            [sys.executable, str(HOOK)], input=stdin, env=base_env(self.project), capture_output=True, text=True
        )
        out = proc.stdout.strip()
        return proc.returncode, (json.loads(out) if out else None)

    def payload(self) -> dict:
        return {"prompt": FAKE_SENSITIVE_PROMPT, "session_id": "s-1", "cwd": str(self.project)}

    def test_allow_says_nothing(self):
        with StubGateway("ALLOW") as gw:
            self.configure(gw.url)
            self.assertEqual(self.run_hook(self.payload()), (0, None))

    def test_block_blocks_and_records_session(self):
        with StubGateway("BLOCK") as gw:
            self.configure(gw.url)
            code, out = self.run_hook(self.payload())
        self.assertEqual((code, out["decision"]), (0, "block"))
        self.assertIn("risk 92", out["reason"])
        (e,) = events(self.project)
        self.assertEqual(
            (e["reason_code"], e["session_id"], e["agent"]), ("GATEWAY_BLOCK", "s-1", "prompt-gateway-guard/HOOK")
        )
        self.assertNotIn("MRN", self.queue_text())

    def test_flag_lets_through_with_context(self):
        with StubGateway("FLAG") as gw:
            self.configure(gw.url)
            code, out = self.run_hook(self.payload())
        self.assertNotIn("decision", out)
        self.assertIn("risk_score=55", out["hookSpecificOutput"]["additionalContext"])

    def test_gateway_down_fails_open_with_warning(self):
        self.configure(unused_url())
        code, out = self.run_hook(self.payload())
        self.assertEqual(code, 0)
        self.assertNotIn("decision", out)
        self.assertIn("check skipped", out["systemMessage"])
        self.assertEqual(events(self.project)[0]["reason_code"], "GATEWAY_UNAVAILABLE")

    def test_plain_http_to_remote_gateway_is_refused_and_fails_open(self):
        self.configure("http://gateway.example.invalid")
        code, out = self.run_hook(self.payload())
        self.assertEqual(code, 0)
        self.assertIn("refused", out["systemMessage"])

    def test_empty_or_broken_input_does_nothing(self):
        for stdin in ("", "not json", "[]", json.dumps({"prompt": "   "})):
            self.assertEqual(self.run_hook(stdin), (0, None), stdin)

    def test_user_override_wins(self):
        with StubGateway("BLOCK") as gw:
            self.configure("http://gateway.example.invalid")
            (self.project / "_bmad/custom/prompt-gateway-guard.user.toml").write_text(f'base_url = "{gw.url}"\n')
            code, out = self.run_hook(self.payload())
        self.assertEqual(out["decision"], "block")


if __name__ == "__main__":
    unittest.main()
