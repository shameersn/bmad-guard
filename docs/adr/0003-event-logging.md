# 0003 — Event logging: OTel collector → ClickHouse → Grafana

- **Status:** Accepted (2026-11-25)
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

## Update (2026-11-26, GUARD-3 T4): what the two audit reports match on

Problem. ADR 0002 and the Phase 2 todo lists ask for two reports: "commits with no guard events" and "review records
with no `REVIEW_RECORDED` event". Events carried no commit id, story or record hash, so neither report could be built.
The `commit` field above is not filled by the pre-commit hook: it runs BEFORE the commit exists, so the SHA is unknown.

Options for the commits report:
- **A. Match by user and time window** (a commit at 10:04 and a pre-commit event at 10:03). No change to events. Weak:
  the key user is not the git author, clocks differ, and one event could cover a `--no-verify` commit made a minute later.
- **B. Add `tree` to the pre-commit events** (the id of the staged tree, `git write-tree`). The commit that follows holds
  exactly that tree, so the match is exact. One more field, a hash, no content. Survives an amend that changes only the
  message. Weak spots: a rebase or cherry-pick makes a new tree (false alarm); a `--no-verify` commit of a tree that
  the hook allowed before (for example a revert) looks seen.
- **C. A post-commit hook that sends the commit SHA.** Exact, but it is a new hook that `--no-verify` also skips (it
  would not catch the bypass it is meant to catch), and it adds a hook to every project.

Options for the review report:
- **D. Put `reviewed_sha` in the event.** The SHA is in the record already and is not secret, but a forger can copy it
  from `git log`, so it proves nothing about the record file.
- **E. Put the record path and the SHA-256 of the record file, as written, in the event.** ADR 0003 allows "path and
  SHA-256" for the target. A record made by hand, or changed after the hook wrote it, has a hash that no event holds.

**Decision: B and E.** `pre-commit` events carry `tree`; `REVIEW_RECORDED` carries `target_path` and `target_sha256`.
Both stay inside "target: path and SHA-256" and "where: commit". No content, no command text, no matched value: the
red-team case LEAK-1 stays green.

Rules that follow:
- A commit is seen only when its tree has an **allow** event from the pre-commit hook. A block event for a tree is no
  proof: a developer could be refused, then commit the same tree with `--no-verify`. The report lists it as
  `blocked-only`.
- The reports read ClickHouse as the read-only user, never as `otel_writer`.
- What the reports cannot see is printed with each report and listed in `deploy/collector/README.md`. They do not
  prove who did the review: a hook-written record for a review run in the build session is not caught here (that is
  the review-record guard's job).
- Events from before this update have no `tree` or record hash, so the reports flag older commits and records. Run the
  reports on commits made after the sender is rolled out.

## Update (2026-11-26, GUARD-3 T4 follow-up): Claude Code metrics, and when the queue is sent

**Metrics are in the pilot.** The collector gets a metrics pipeline next to the logs pipeline: the same OTLP receiver (so
the same basic auth), the same `user` stamp from the key, the same scrub (`attributes/scrub` works on metric datapoint
attributes, `resource/scrub` on the resource). Claude Code metrics carry the same personal attributes as its events.

Options for where the metrics go:
- **A. Same database `guard`, the exporter's default table names** (`otel_metrics_sum`, `otel_metrics_gauge`). One TTL
  and one grant model; `grafana_reader` already has SELECT on `guard.*`; one backup covers both.
- **B. A separate database.** Cleaner names, but a second TTL, a second grant set, a second backup path, and a second data
  source in Grafana for no gain at pilot size.

**Decision: A.** The tables are created by `clickhouse/init/02-metrics.sh` (layout copied from the exporter's own DDL,
180-day TTL). `otel_writer` gets INSERT on those two tables only and keeps `create_schema: false`, so the collector
still has no CREATE right at run time. Only sum and gauge exist (Claude Code sends counters); the exporter refuses a
histogram or summary until a table is added. An existing stack runs `02-metrics.sh` once by hand (README).

**When the queue is sent.** The git hooks only queued, so a developer who works in a terminal sent nothing until a
Claude Code tool call happened, and the "commits with no guard events" report listed those commits. Now the
`SessionStart` hook and the pre-push hook send the queue once, best effort: no endpoint means no network and no message;
the sender's 3 s timeout and 5-minute backoff apply; a failure never blocks a session or a push. Pre-commit stays
queue-only (it must never wait on the network). This is the "events never wait" rule of this ADR, kept.

## Related
- Past ADRs: 0002 (enforcement layers).
- Links: `spikes/collector/REPORT.md` (T3), `spikes/collector/otelcol/config.yaml`.
