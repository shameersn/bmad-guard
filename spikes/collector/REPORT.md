# T3 report: event pipeline

| | |
|---|---|
| Ticket | GUARD-0 (T3) |
| Date | 2026-11-24 |
| Stack | `otel/opentelemetry-collector-contrib:0.165.0`, `clickhouse/clickhouse-server:26.8.45.6`, `grafana/grafana:13.3.1` + `grafana-clickhouse-datasource@4.23.5`, Docker via OrbStack |
| Result | **Pass.** Tests 1–5 and 7 pass. Test 6 passes with one gap (see below). Test 8 recorded. No nginx fallback needed. |

## Answer

Guard event → OTel collector → ClickHouse → Grafana works with off-the-shelf parts and no custom server code.

- The user is stamped from the developer's key. A client cannot fake it.
- A wrong key gets HTTP 401 and nothing is stored.
- When the collector is down, events wait in a local queue, the developer sees a warning, and the queue drains on the next send.
- The collector's database user can only `INSERT`. It cannot read, change or drop anything.
- Events are visible in ClickHouse **1–2.2 seconds** after sending.

One new finding: **Claude Code telemetry contains the developer's email and account ids.** The collector must strip them (done in the config here, tested).

## Test results

| # | Test | Result | Evidence |
|---|---|---|---|
| 1 | Happy path: 10 events as `dev-a` | **Pass** | 10 sent, 10 stored, all visible after 1.0 s (second run: 2.2 s) |
| 2 | User stamping: `dev-b` sends `user=dev-a` | **Pass** | Stored user = `dev-b` |
| 3 | Bad key | **Pass** | HTTP 401, 0 rows stored |
| 4 | Server down | **Pass** | Collector stopped: 3 events queued, warning printed. Collector started: 3 drained, 3 stored, queue file empty |
| 5 | Insert-only user `otel_writer` | **Pass** | `SELECT`, `ALTER ADD COLUMN`, `ALTER DELETE`, `TRUNCATE`, `DROP` all refused (`ACCESS_DENIED`). Table still there |
| 6 | Claude Code telemetry, prompts not stored | **Pass, with gap** | 33 events arrived per session, stamped `dev-a`. `prompt` and `prompt_text` = `<REDACTED>`; only `prompt_length` kept. A unique marker in the prompt was found 0 times. **Gap:** the local `claude` login had expired, so the model call failed (`api_error`). We did not see the `api_request` events (tokens, cost) of a successful call. |
| 7 | Grafana panel: events per user and verdict | **Pass** | Provisioned dashboard `guard-events`. Panel query returns `dev-a` and `dev-b` rows |
| 8 | ClickHouse exporter stability | Recorded | At v0.165.0: **logs beta**, traces beta, metrics alpha |

Unit tests (no Docker): `python3 -m unittest discover -s tests` → **15 pass** (sender: 11, key script: 4).

## What we built

| File | What |
|---|---|
| `docker-compose.yml` | 3 services, pinned tags, all ports on `127.0.0.1` only |
| `otelcol/config.yaml` | OTLP/HTTP receiver with `basicauth/server` (htpasswd file); `attributes/who` (`user` ← `auth.username`); `attributes/scrub` (drop email and account ids); ClickHouse exporter with `create_schema: false` |
| `clickhouse/init/01-schema.sh` | Admin creates `guard.guard_events` (exporter's logs DDL minus k8s columns, plus materialized `User`, `Guard`, `Verdict`, `Project`, `EventId`), 180-day TTL, users `otel_writer` (INSERT only) and `grafana_reader` (SELECT, `readonly = 2`) |
| `grafana/` | Data source (as `grafana_reader`) and one dashboard |
| `guard_events.py` | Sender, standard library only. `emit()` appends to `.bmad-guard/events/queue.jsonl` under a file lock, then sends the whole queue. Never raises. Warns on stderr. On 401 it hints to check `BMAD_GUARD_KEY`, without printing the key. |
| `make_keys.py` | Writes random stack passwords to `.env` and one key per fake developer to `work/keys/`, plus `work/devkeys.htpasswd`. Prints file names only. |
| `run_live_tests.py` | Live tests 1–5 and 7. Reads keys itself, never prints them |
| `run_claude_telemetry.py` | Test 6: one `claude -p` run with OTel env vars pointed at the local collector |

Stack passwords needed in `.env` (git-ignored, created by `make_keys.py`): `CLICKHOUSE_ADMIN_PASSWORD`, `CLICKHOUSE_WRITER_PASSWORD`, `CLICKHOUSE_READER_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`. (The planned `.env.example` was not written: the path is blocked by local tool permissions. This list replaces it.)

## Problems found and fixed during the spike

| Problem | Fix |
|---|---|
| Grafana plugin failed: "Cannot modify 'max_execution_time' setting in readonly mode" | `grafana_reader` uses `readonly = 2` (no writes, settings allowed). Checked: the reader's `INSERT` is still refused. Grafana had to restart to drop its pooled connection. |
| Claude Code events carry `user.email`, `user.account_uuid`, `user.account_id` | `attributes/scrub` deletes them in the collector. Checked: next session stored 0 of them. |
| ClickHouse healthcheck with `clickhouse-client` needs a password | Healthcheck uses `GET /ping` instead |

## Findings for Phase 3 (and ADR 0003)

1. **The design works as planned.** No nginx fallback needed: `from_context: auth.username` works with `basicauth/server` at 0.165.0.
2. **Strip personal data at the collector.** Claude Code telemetry includes email, account ids and `organization.id`. Keep `attributes/scrub` and review the attribute list on each Claude Code upgrade. `OTEL_LOG_USER_PROMPTS` must stay off; with it off, prompts are redacted.
3. **Claude Code puts the event name in `Body`**, not in `EventName` (empty for those rows). Our guard events fill both. Dashboards for Claude Code events should filter on `Body` or `LogAttributes['event.name']`.
4. **Exporter maturity:** logs are beta. Pin the collector version and re-test the exporter on each upgrade.
5. **Key hashes:** the spike uses salted SHA1 (`{SSHA}`) because Python has no built-in bcrypt. Production keys should be bcrypt hashes in the htpasswd file. Key rotation = edit the file and restart (or reload) the collector; not tested here.
6. **Grafana downloads plugin updates at start** (it updated bundled plugins such as `stackdriver` and `tempo` on its own). For production, turn off plugin auto-update and pin plugins. I'm not sure of the exact Grafana 13 setting; check before Phase 3.
7. **Local queue:** one JSONL file with a file lock (`fcntl`, macOS/Linux only). Windows needs a different lock. A torn line from a crash is skipped.
8. **TTL** is 180 days in the spike. The real value is a team decision (audit needs vs. storage).

## Not covered

- TLS, ALB, AWS deployment (later phase).
- Load, multi-day retention, backups.
- Key rotation and bcrypt hashes.
- A successful Claude Code model call (login expired on this machine).
- Windows.

## Local data note

The spike database in `work/clickhouse-data/` (git-ignored, on this laptop only) still holds the 33 Claude Code events from before the scrub fix, including one real email address. It should be wiped with the stack: `docker compose down` and then delete `work/clickhouse-data/`.
