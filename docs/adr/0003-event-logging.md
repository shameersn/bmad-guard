# 0003 — Event logging: OTel collector → ClickHouse → Grafana

- **Status:** Proposed
- **Date:** 2026-11-24
- **Project:** Other (Experion, Guarded BMAD pilot)
- **Deciders:** Shameer (architect), Manesh G; team approval by PR

## Context
Ledger writes a CSV on each laptop. Nobody else can see it, so we cannot answer questions like:
- Who approved this?
- Which guard blocked this?
- Who put PHI in a log?

We need a central, tamper-resistant event store.

Constraints:
- No database credentials on laptops.
- The user must come from something the developer cannot fake.
- A logging outage must never block a commit.
- No PHI or personal data in events.
- S3 is not needed if the store has the data (team decision, 24 Nov).

## Options
### Option A — Local CSV (today)
- Pros: simple.
- Cons: no central view; easy to edit; lost with the laptop.
- Cost / effort: none.

### Option B — Upload files to S3
- Pros: cheap storage.
- Cons: still needs a query engine and a dashboard; per-user write rights on a bucket.
- Cost / effort: medium.

### Option C — Small custom API writing to a central DB, with DB credentials shared to developers
- Pros: full control.
- Cons: shared credentials on laptops; custom server to build, secure and run.
- Cost / effort: medium to high.

### Option D — OpenTelemetry collector → ClickHouse → Grafana, one ingest key per developer
- Pros:
  - Off-the-shelf parts.
  - The collector stamps the user from the key.
  - The DB user is insert-only.
  - Claude Code can send its own telemetry to the same place.
- Cons:
  - Three services to run.
  - The ClickHouse exporter is beta for logs.
- Cost / effort: medium (mostly config).

## Decision
We choose **Option D** because the T3 spike passed with standard parts and no custom server.
- Events are visible in 1–2.2 s.
- A client cannot fake its user.
- A wrong key gets HTTP 401.
- When the collector is down, events queue locally and drain later.
- `otel_writer` can only `INSERT`.

Design:
- **Laptop:** guards append each event to `.bmad-guard/events/queue.jsonl` (git-ignored). A standard-library sender posts OTLP/HTTP JSON to `/v1/logs` with Basic auth (developer id + ingest key).
- **Collector** (`otel/opentelemetry-collector-contrib`), in order:
  - `basicauth/server` with an htpasswd file;
  - `attributes` processor sets `user` from `auth.username`;
  - `attributes` processor deletes `user.email`, `user.account_uuid` and `user.account_id`;
  - ClickHouse exporter with `create_schema: false`.
- **ClickHouse:**
  - the admin owns the schema;
  - the collector uses `otel_writer` (INSERT only);
  - Grafana uses `grafana_reader` (SELECT, `readonly = 2`).
- **Claude Code:** native OTel env vars, set by managed settings. `OTEL_LOG_USER_PROMPTS` stays off, so prompts are redacted (T3).
- **Event fields:**
  - identity: event_id, ts, user (from the key), session_id;
  - where: project, repo, branch, commit;
  - decision: guard, stage, verdict, reason_code;
  - target: path and SHA-256;
  - finding: type and location only, never content.

## Consequences
- Good:
  - Central, queryable audit trail.
  - No DB credentials on laptops.
  - The user is trustworthy.
  - Commits never wait.
- Bad / risks:
  - Three services to operate.
  - The exporter is beta: pin the version and re-test on upgrade.
  - Claude Code telemetry carries personal data: keep the scrub step and review it on each Claude Code upgrade.
  - The spike's key-hash format (salted SHA1) is too weak for production.
- Follow-up work (Phase 3):
  - bcrypt keys and a key-rotation process.
  - TLS via ALB + ACM.
  - VPN or public endpoint: still open.
  - Retention period (spike used 180 days): team decision.
  - Turn off Grafana plugin auto-update.
  - A queue lock that works on Windows.
  - Backups.
  - The "commits with no events" report.

## Related
- Past ADRs: 0002 (enforcement layers).
- Links: `spikes/collector/REPORT.md` (T3), `spikes/collector/otelcol/config.yaml`.
