# Demo kit

A 45-minute live demo of Guarded BMAD for senior review: a clinic-booking API from product brief to pushed code, with
the guards blocking live and every decision in Grafana. Local only. Fake data only.

The full runbook: [`docs/guides/demo-run.md`](../docs/guides/demo-run.md).

| File | What |
|---|---|
| `prepare.sh` | New demo project: tool checks, BMAD 6.12.4, guard setup, HIPAA mode, approved tech-stack list, local bare remote, tag `demo/0-setup`, telemetry settings file. |
| `make-live-copy.sh` | Demo-day copy of the rehearsed project, at `demo/0-setup`, with guards wired and a fresh remote. |
| `telemetry.json.example` | Claude Code telemetry settings for `claude --settings` (the demo stand-in for managed settings). No key in it. |
| `prompts.md` | Every prompt and terminal command of the live part, in order, with the rule id to expect. |

These scripts never start the collector stack and never make developer keys. Do both off-screen with
[`deploy/collector/README.md`](../deploy/collector/README.md).
