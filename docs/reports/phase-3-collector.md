# Phase 3 T2 report: collector stack in `deploy/collector/`

| | |
|---|---|
| Ticket | GUARD-3 (T2) |
| Date | 2026-11-26 |
| Stack | Docker 29.8.2 (OrbStack), Compose with `include`, run from `deploy/collector/` |
| Result | **Pass.** Spike tests 1-5 pass again on the hardened config. Three extra checks pass (rotation, hardening, dashboard). |

## Image versions

| Part | Version |
|---|---|
| `otel/opentelemetry-collector-contrib` | 0.165.0 |
| `clickhouse/clickhouse-server` | 26.8.45.6 |
| `grafana/grafana` | 13.3.1 |
| `grafana-clickhouse-datasource` | 4.23.5 |
| Grafana default apps (pinned) | advisor 1.1.4; exploretraces, lokiexplore, metricsdrilldown, pyroscope 3.2.0 |
| `httpd` (only for `keys.py`, bcrypt) | 2.4.66-alpine |

The collector, ClickHouse and Grafana versions are the same as in the spike. Only the hashing and settings changed.

## What changed from the spike

| Item | Spike | Now |
|---|---|---|
| Key hashes | salted SHA1 (`{SSHA}`) | bcrypt `$2y$`, cost 10, made by `keys.py` (`htpasswd` in the `httpd` image) |
| Key issue and rotation | a script that wrote keys to disk | `keys.py add / rotate / remove`. Key printed once, never written. Only the hash is stored |
| Data | bind folder `work/` | named Docker volumes; a backup volume and `backup.sh` |
| Grafana plugins | updated themselves at start | auto-update, UI install and update check off; all downloaded plugins pinned |
| Password file | a dot-env file made by `make_keys.py` | `secrets.local.ini` from a committed `*.example`; `keys.py init` can make it |
| Retention | 180-day TTL | 180-day TTL (unchanged); checked on the live table |

## Test results (run 2026-11-26, `python3 run_live_tests.py`)

Fake users `live-test-a` and `live-test-b`. Keys were random, kept in memory and removed at the end.

| # | Test | Result | Evidence |
|---|---|---|---|
| 1 | Happy path: 10 events as `live-test-a` | **Pass** | 10 sent, 10 stored, all visible after 1.4 s |
| 2 | User stamping: `live-test-b` sends `user=live-test-a` | **Pass** | Stored user = `live-test-b` |
| 3 | Bad key | **Pass** | HTTP 401, 0 rows stored |
| 4 | Server down | **Pass** | Collector stopped: 3 events queued, warning printed. Collector started: 3 drained and stored, queue file 0 bytes |
| 5 | Insert-only `otel_writer` | **Pass** | `SELECT`, `ALTER ADD COLUMN`, `ALTER DELETE`, `TRUNCATE`, `DROP` all refused. Table still exists. `SHOW GRANTS` shows only `INSERT ON guard.guard_events` |
| 6 (extra) | Key rotation | **Pass** | Old key 200 before. After `rotate` but before reload: old key still 200. After collector restart: old key 401, new key 200 |
| 7 (extra) | Hardening checks | **Pass** | All htpasswd hashes start with `$2y$10$`. Table TTL `toDateTime(Timestamp) + toIntervalDay(180)`. `grafana_reader` has `readonly = 2`. Grafana reports `preinstall_auto_update`, `plugin_admin_enabled` and `check_for_plugin_updates` as false and every pinned plugin with a version (read from `/api/admin/settings`). No port on `0.0.0.0` |
| 8 (extra) | Dashboard (spike test 7) | **Pass** | Panel query returns both fake users. Dashboard `guard-events` is provisioned |

Other checks, done by hand and not in the script:

- **bcrypt support:** the pinned collector accepts `$2y$` hashes: tests 1, 3 and 6 only pass if it verifies them.
- **Grafana plugin start:** on an empty volume the log shows each plugin installed at its pinned version. Two changes did not work and were dropped: `GF_PLUGINS_PREINSTALL_DISABLED=true` (also skips our own plugin) and `GF_INSTALL_PLUGINS` (deprecated, installed nothing).
- **Backup and restore:** `./backup.sh backup` wrote a zip; `./backup.sh verify` restored 42 of 42 rows into a scratch database and dropped it. A full restore was also tried once: the table was truncated, `./backup.sh restore` brought back all 14 rows.
- **Unit tests (no Docker):** `python3 -m unittest discover -s tests`: 999 tests OK (24 are new, in `tests/test_collector_keys.py`).

## Not tested

- TLS, ALB, AWS, VPN (written in `HOSTING.md`, not built).
- A real sender from T3 (tests use the frozen spike sender `spikes/collector/guard_events.py`).
- Claude Code telemetry through the new stack (T4). The scrub step is unchanged from the spike and was not re-run with real Claude Code events.
- Retention actually deleting rows after 180 days (only the TTL definition was checked).
- Load, many users, or the bcrypt cost under load (10 events took 1.4 s end to end).
- Other htpasswd hash formats, and htpasswd reload without restart (we restart the collector).
- Backup of Grafana and ClickHouse users; restore into a fresh stack from a zip copied out of Docker (the table-level restore into the same stack was tested).
- Windows, Linux, and Docker engines other than OrbStack.

## Leftovers on this machine

- Containers: none (the stack was stopped with `docker compose down`).
- Volumes (fake test data only): `bmad-guard-collector_clickhouse-data`, `bmad-guard-collector_clickhouse-backups`, `bmad-guard-collector_grafana-data`.
- Images pulled: the three stack images (already there from the spike), `httpd:2.4.66-alpine`, and `httpd:2.4-alpine` (pulled by mistake while looking for a tag; not used).
- Local, git-ignored files in `deploy/collector/`: `secrets.local.ini` (random passwords), `local/devkeys.htpasswd` (empty).
