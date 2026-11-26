# /// script
# requires-python = ">=3.10"
# ///
"""Tests for otel_headers.py (the `otelHeadersHelper` of Claude Code telemetry) and the telemetry part of the
managed-settings template. Fake users and keys only; HOME is a temp folder."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.fake_home import isolated_env

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "module/skills/agent-project-logger/scripts/otel_headers.py"
TEMPLATE = ROOT / "module/managed-settings/managed-settings.json"
USER, SECRET = "fake-dev", "fake-ingest-value-1"


def run(env: dict[str, str]) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as cwd:
        return subprocess.run([sys.executable, str(SCRIPT)], cwd=cwd, env=env, capture_output=True, text=True)


def expected_header() -> str:
    return "Basic " + base64.b64encode(f"{USER}:{SECRET}".encode()).decode()


class HelperTests(unittest.TestCase):
    def test_environment_gives_the_header(self):
        env = {**isolated_env(), "BMAD_GUARD_USER": USER, "BMAD_GUARD_KEY": SECRET}
        proc = run(env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"Authorization": expected_header()})
        self.assertEqual(proc.stderr, "")

    def test_the_credential_file_gives_the_header(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".config/bmad-guard"
            folder.mkdir(parents=True, mode=0o700)
            file = folder / "credentials"
            file.write_text(f"[collector]\nendpoint = http://127.0.0.1:4318\nuser = {USER}\nkey = {SECRET}\n")
            file.chmod(0o600)
            proc = run({**isolated_env(), "HOME": home})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"Authorization": expected_header()})

    def test_nothing_set_prints_no_header_and_exits_1_without_a_value(self):
        proc = run(isolated_env())
        self.assertEqual((proc.returncode, proc.stdout), (1, ""))
        self.assertIn("no developer id and key", proc.stderr)

    def test_an_open_file_is_refused_and_the_value_is_not_printed(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".config/bmad-guard"
            folder.mkdir(parents=True, mode=0o700)
            file = folder / "credentials"
            file.write_text(f"[collector]\nuser = {USER}\nkey = {SECRET}\n")
            file.chmod(0o644)
            proc = run({**isolated_env(), "HOME": home})
        self.assertEqual((proc.returncode, proc.stdout), (1, ""))
        self.assertIn("mode 644", proc.stderr)
        self.assertNotIn(SECRET, proc.stderr)


class TemplateTelemetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = json.loads(TEMPLATE.read_text())
        cls.env = cls.template["env"]

    def test_logs_and_metrics_go_out_as_otlp_http_json_with_delta_sums(self):
        env = self.env
        self.assertEqual(env["CLAUDE_CODE_ENABLE_TELEMETRY"], "1")
        self.assertEqual(env["OTEL_LOGS_EXPORTER"], "otlp")
        self.assertEqual(env["OTEL_METRICS_EXPORTER"], "otlp")  # the collector has a metrics pipeline (T4)
        self.assertEqual(env["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"], "delta")  # the panels sum values
        self.assertEqual(env["OTEL_EXPORTER_OTLP_PROTOCOL"], "http/json")

    def test_every_content_switch_is_explicitly_off(self):
        for name in (
            "OTEL_LOG_USER_PROMPTS",
            "OTEL_LOG_ASSISTANT_RESPONSES",
            "OTEL_LOG_TOOL_DETAILS",
            "OTEL_LOG_TOOL_CONTENT",
            "OTEL_LOG_RAW_API_BODIES",
        ):
            self.assertEqual(self.env[name], "0", name)

    def test_the_endpoint_is_a_placeholder_and_no_header_value_is_in_the_file(self):
        self.assertIn("REPLACE", self.env["OTEL_EXPORTER_OTLP_ENDPOINT"])
        self.assertNotIn("OTEL_EXPORTER_OTLP_HEADERS", self.env)  # a shared header would be one key for everyone
        self.assertIn("otel_headers.py", self.template["otelHeadersHelper"])

    def test_every_metric_attribute_switch_is_set_on_purpose(self):
        want = {
            "OTEL_METRICS_INCLUDE_SESSION_ID": "true",  # also on events: groups a session across logs and metrics
            "OTEL_METRICS_INCLUDE_VERSION": "true",
            "OTEL_METRICS_INCLUDE_ACCOUNT_UUID": "false",  # off at the source; the collector deletes them too
            "OTEL_METRICS_INCLUDE_ENTRYPOINT": "true",
            "OTEL_METRICS_INCLUDE_REPOSITORY": "false",  # vcs.* would name the repository
            "OTEL_METRICS_INCLUDE_RESOURCE_ATTRIBUTES": "false",
        }
        for name, value in want.items():
            self.assertEqual(self.env[name], value, name)


if __name__ == "__main__":
    unittest.main()
