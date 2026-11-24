# /// script
# requires-python = ">=3.10"
# ///
"""T3 spike test 6: run one throwaway Claude Code session with OTel logs sent to the local collector.

Builds the Basic auth header from dev-a's key without printing it. Prompt logging stays OFF
(OTEL_LOG_USER_PROMPTS is not set). Prints the marker so the caller can check it is not stored.
"""

import base64
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
key = (HERE / "work" / "keys" / "dev-a.key").read_text().strip()
auth = base64.b64encode(f"dev-a:{key}".encode()).decode()
marker = f"T3MARKER{uuid.uuid4().hex[:10]}"

env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
env.update(
    {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318",
        "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Basic {auth}",
        "OTEL_LOGS_EXPORT_INTERVAL": "1000",
    }
)
env.pop("OTEL_LOG_USER_PROMPTS", None)

with tempfile.TemporaryDirectory() as cwd:
    result = subprocess.run(
        ["claude", "-p", f"Reply with the single word ok. Ignore this tag: {marker}", "--model", "haiku", "--max-turns", "1"],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=180,
    )
print(f"marker={marker}")
print(f"exit={result.returncode} reply={result.stdout.strip()[:40]!r}")
sys.exit(result.returncode)
