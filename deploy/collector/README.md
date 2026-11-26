# Guard event stack (local Docker)

OTel collector, ClickHouse and Grafana for the guard events (ADR 0003, option D). This is the pilot stack: it runs on
one dev machine (decision D4). The plan for the company network is in [HOSTING.md](HOSTING.md). It is written, not built.

The spike that this came from is in `spikes/collector/` and stays as it was.

## What it does

- The sender posts events to the collector (`http://127.0.0.1:4318`) with HTTP basic auth: user = developer id,
  password = the developer's ingest key.
- The collector checks the key against a bcrypt htpasswd file, **stamps `user` from the key** (a client cannot fake it),
  deletes the personal attributes Claude Code sends (see "Scrub list" below), and writes to ClickHouse as `otel_writer`.
- `otel_writer` can only `INSERT`. Grafana reads as `grafana_reader` (`readonly = 2`).
- Events are kept 180 days (TTL on the table, decision D6; compliance still has to approve the number).
- All ports bind to `127.0.0.1`.

## Pinned versions

Every image has an exact tag. No `latest`. The same tags are in `stack.yml`, `keys.py` and the test report.

| Part | Image / plugin | Version |
|---|---|---|
| Collector | `otel/opentelemetry-collector-contrib` | 0.165.0 (ClickHouse exporter: logs beta) |
| Database | `clickhouse/clickhouse-server` | 26.8.45.6 |
| Dashboards | `grafana/grafana` | 13.3.1 |
| Grafana plugin | `grafana-clickhouse-datasource` | 4.23.5 |
| Grafana plugins (Grafana's own default apps, pinned so nothing floats) | `grafana-advisor-app` 1.1.4, `grafana-exploretraces-app` 3.2.0, `grafana-lokiexplore-app` 3.2.0, `grafana-metricsdrilldown-app` 3.2.0, `grafana-pyroscope-app` 3.2.0 | as listed |
| Key hashing (used by `keys.py` only) | `httpd` | 2.4.66-alpine |

## Files

| File | What |
|---|---|
| `docker-compose.yml` | Entry point. Loads `stack.yml` with the password file. |
| `stack.yml` | The three services. |
| `otelcol/config.yaml` | Collector pipeline. |
| `clickhouse/init/01-schema.sh` | Creates the table (TTL 180 days) and the two users. Runs once, on an empty volume. |
| `clickhouse/config.d/backup-disk.xml` | Lets ClickHouse write backups to the `clickhouse-backups` volume. |
| `grafana/` | Data source and the dashboard (provisioned). |
| `keys.py` | Add, rotate or remove a developer key. Also `init` for the stack passwords. |
| `backup.sh` | Backup, restore check, restore. |
| `run_live_tests.py` | The live tests (spike tests 1-5 plus T2 checks). |
| `run_telemetry_live.py` | The T4 live check: guard events, Claude Code telemetry, Grafana panels and both reports, from a throwaway BMAD project. |
| `reports/guard_reports.py` | The two audit reports (commits with no guard events, review records with no `REVIEW_RECORDED` event). Read-only. |
| `secrets.local.ini.example` | Placeholder passwords. The real file `secrets.local.ini` is git-ignored. |
| `devkeys.htpasswd.example` | Example line with a fake hash. The real file `local/devkeys.htpasswd` is git-ignored. |

## Start

You need Docker with Compose v2.20 or newer, and Python 3.10+.

```sh
cd deploy/collector
python3 keys.py init            # creates secrets.local.ini with random passwords and an empty local/devkeys.htpasswd
                                # (or copy the .example files and edit)
python3 keys.py add dev-a       # first developer. Copy the key it prints: it is shown once.
docker compose up -d
docker compose ps               # clickhouse should be "healthy"
```

- Grafana: http://127.0.0.1:3000 (user `admin`, password `GRAFANA_ADMIN_PASSWORD` from `secrets.local.ini`).
  The first start downloads the pinned plugins, so it takes about a minute.
- The ClickHouse init script runs only when the database volume is empty. Changing a password in
  `secrets.local.ini` later does not change the database user. To start over: `docker compose down -v`
  (this deletes the stack's data, so back up first if you need it).
- Use letters, digits, `-` and `_` in the passwords. They are used in SQL and shell.

## Stop

```sh
docker compose down             # stops and removes the containers. Data stays in the named volumes.
```

## Add a developer

```sh
python3 keys.py add <user-id>   # prints the key ONCE
docker compose restart otelcol  # the collector reads the htpasswd file at start
```

- The user id is the developer id the sender uses (lower-case letters, digits, `.`, `_`, `-`).
- Give the id and the key to the developer through a private channel. The developer runs the setup script (T3).
- `keys.py` never writes the key to a file or a log. Only the bcrypt hash goes to `local/devkeys.htpasswd`.

## Rotate a key

```sh
python3 keys.py rotate <user-id>   # new key for the same id, prints it ONCE
docker compose restart otelcol
```

- The id stays the same, so the history stays continuous (ADR 0007).
- Between the file change and the restart the old key still works. After the restart the old key gets HTTP 401.
  Until the developer re-runs setup with the new key, their events wait in the local queue and are sent later.
- Rotate on a schedule that the owner sets, and at once when a key may have leaked or a developer leaves.

## Remove a developer

```sh
python3 keys.py remove <user-id>
docker compose restart otelcol
```

Old events stay in ClickHouse (the audit trail). Only new events are refused.

## Why `keys.py` uses Docker for bcrypt

Python's standard library has no bcrypt. A pip package would be a new dependency. So `keys.py` runs `htpasswd -niB`
from the pinned `httpd` image (`docker run --rm -i`). The key goes in through stdin, so it is not in the process list.
Cost factor 10.

**Verified:** the pinned collector (0.165.0) accepts bcrypt (`$2y$`) in the htpasswd file. A key checked against such a
hash returns HTTP 200, a wrong key and a rotated-away key return 401 (live tests 3 and 6). I did not test other hash
formats.

## Grafana: plugin auto-update off, plugins pinned

Setting names for Grafana 13.3.1, **verified** in three ways:

1. The image's `defaults.ini` (`docker run --rm --entrypoint cat grafana/grafana:13.3.1 /usr/share/grafana/conf/defaults.ini`)
   has `[plugins] preinstall_auto_update = true`, `[plugins] plugin_admin_enabled = true` and
   `[analytics] check_for_plugin_updates = true`.
2. Our values are set as `GF_PLUGINS_PREINSTALL_AUTO_UPDATE=false`, `GF_PLUGINS_PLUGIN_ADMIN_ENABLED=false` and
   `GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false`. The running Grafana reports them through `/api/admin/settings`
   (checked by `run_live_tests.py`, test 7).
3. A start on an empty volume, with the log read: the plugins were installed at the pinned versions.

What each does:

- `preinstall_auto_update = false` stops updates of preinstalled plugins at start (this caused the spike's surprise updates).
- `plugin_admin_enabled = false` removes install, update and uninstall from the Grafana UI.
- `check_for_plugin_updates = false` stops the update check (a request to grafana.com).

**A trap I found:** `preinstall_auto_update = false` is not enough. On a new volume Grafana still downloads its own
default apps (the Loki, traces, metrics and profiles "explore" apps and the advisor) at the latest version. Setting
`preinstall_disabled = true` also drops our own pinned plugin, and `GF_INSTALL_PLUGINS` is deprecated. So all six
plugins are named with a version in `GF_PLUGINS_PREINSTALL_SYNC`. On a Grafana upgrade, start on an empty volume and
read the log lines `Installing plugins` for new default apps. Pin them, too.

## Backup

```sh
./backup.sh backup                         # writes guard-<UTC time>.zip into the clickhouse-backups volume
./backup.sh list
./backup.sh copy guard-<time>.zip <folder> # copy it off the volume (the volume is on the same disk)
```

It runs ClickHouse `BACKUP TABLE guard.guard_events TO Disk('backups', ...)`. Run it daily (cron or launchd) and copy
the file somewhere else. Not covered: the ClickHouse users (the init script re-creates them), the Grafana database
(dashboards come from git), and `local/devkeys.htpasswd` (keep it in the owner's password manager or a secure backup).

## Restore check

Do this after the first backup and every month. It restores into a scratch database and drops it again, so live data
is not touched:

```sh
./backup.sh verify guard-<time>.zip
```

It prints the restored row count and fails when nothing is restored.

## Restore

On a fresh stack (`docker compose up -d` on empty volumes: the init script has made the empty table and the users):

```sh
docker compose cp <folder>/guard-<time>.zip clickhouse:/backups/    # if the backup came from outside
./backup.sh restore guard-<time>.zip
```

## Upgrade

Logs in the ClickHouse exporter are **beta**, so every collector upgrade can change the table layout or attributes.

1. Read the collector release notes for `clickhouseexporter` and `basicauthextension`. Read the ClickHouse and Grafana notes too.
2. Back up (`./backup.sh backup`) and run `./backup.sh verify`.
3. Change the tag in `stack.yml` (and `keys.py` for `httpd`). Update the table above and the test report.
4. `docker compose pull && docker compose up -d`.
5. Run `python3 run_live_tests.py`. All tests must pass. Write the result in a new report in `docs/reports/`.
6. Check the exporter's table DDL (`logs_table.sql` of the new version) against `clickhouse/init/01-schema.sh`. The
   collector does not create or change the table (`create_schema: false`).
7. On a Claude Code upgrade, check the attribute list again: the scrub step only removes what it knows (spike finding 2).

## Scrub list

The collector deletes personal and machine attributes from Claude Code telemetry before they reach ClickHouse
(`attributes/scrub` for log attributes, `resource/scrub` for resource attributes in `otelcol/config.yaml`). The user is
known from the key, so nothing is lost.

**Reviewed 2026-11-26 against Claude Code 2.1.332. Review it on each Claude Code upgrade** (same time as the collector
upgrade steps above). How: run a throwaway `claude -p` session against the stack (`run_telemetry_live.py` prints every
attribute key it saw), compare with this table and the Claude Code monitoring docs
(<https://code.claude.com/docs/en/monitoring-usage>, "Standard attributes"), then add what is new.

| Attribute | Decision | Why |
|---|---|---|
| `user.email` | **delete** | Personal. |
| `user.account_uuid`, `user.account_id` | **delete** | Account ids of the person. Also set `OTEL_METRICS_INCLUDE_ACCOUNT_UUID=false` in the managed template (the docs name it for metrics; not verified for logs). |
| `user.id` | **delete** | Claude Code's anonymous id (kept in `~/.claude.json`): a second, unrelated id for the same person. |
| `organization.id` | **delete** | Organization id. No use for the pilot; it names the customer account. |
| `workspace.host_paths` | **delete** | Folders on the laptop; they hold the account name. |
| `host.name`, `host.id` (resource) | **delete** | Machine names. Off by default (`OTEL_RESOURCE_INCLUDE_HOST`); removed in case someone turns them on. |
| `session.id` | keep | A random id. It groups the events of one session and matches the guards' `session_id`. |
| `terminal.type`, `prompt.id`, `event.sequence`, `model`, token and cost counts, durations, `plugin.*`, `hook_*`, `managed_settings.*`, `anthropic.*`, `os.type`, `os.version`, `host.arch`, `service.version` | keep | Not personal. |
| `prompt`, `prompt_text`, `response` | keep (redacted by Claude Code) | With the content switches at `0` they hold `<REDACTED>` or nothing. Checked live for all three. |
| `app.version`, `app.entrypoint`, `vcs.*` | not sent (off by default) | If you turn `OTEL_METRICS_INCLUDE_REPOSITORY` on, `vcs.*` may name the repository: decide first. |

The tested behaviour: `run_telemetry_live.py` sends one made-up record that carries every attribute in the "delete" rows
and checks that none is stored, and that `session.id` and `terminal.type` stay. A real session did not carry `user.id`,
`organization.id` or the email (this machine's login), so the made-up record is what proves those rules.

## Claude Code telemetry

Claude Code sends its own events (`claude_code.api_request`, `claude_code.user_prompt`, ...) to this collector. The
settings are in the managed-settings template (`module/managed-settings/managed-settings.json`, `env` block and
`otelHeadersHelper`; see `docs/managed-settings-rollout.md`). What the collector sees:

- The event name is in `Body` (`EventName` is empty). `ServiceName` is `claude-code`. The dashboard filters on both.
- Logs only. Metrics are `none`: the collector has a logs pipeline and the table holds logs. Turning metrics on needs a
  metrics pipeline and a table first.
- The auth header comes from `otel_headers.py` (Ledger), which reads the developer's own key from the credential file or
  the environment. So the managed file holds no key.

## Audit reports

Two small scripts compare the events with a git repository. They query ClickHouse through its HTTP interface as a
**read-only** user (`grafana_reader`), never as `otel_writer`. They live here and not in `module/` because they need
the reader's password and run where the stack runs, not on developer laptops (ADR 0005).

```sh
export GUARD_REPORT_PASSWORD=...          # the CLICKHOUSE_READER_PASSWORD value; never on the command line
python3 reports/guard_reports.py commits --repo <project> --range origin/main..<feature branch>
python3 reports/guard_reports.py reviews --repo <project> --ref origin/main
```

**Routine `commits` runs use `origin/main..<feature branch>`, before any rebase** (a rebase gives new trees, so its
commits would show up as `no-event`). `--range` defaults to `origin/main..HEAD`.

Defaults: `GUARD_REPORT_URL=http://127.0.0.1:8123`, `GUARD_REPORT_USER=grafana_reader`. Exit 0 = nothing to report, 1 =
findings, 2 = the report could not run.

| Report | Match key (ADR 0003 update) | Reports |
|---|---|---|
| `commits` | the staged tree id in the pre-commit event (`tree`) against each commit's tree | `no-event`: no pre-commit event at all. `blocked-only`: the hook refused that tree, but a commit holds it (committed with `--no-verify`). |
| `reviews` | SHA-256 of the record file, as the hook wrote it (`target_sha256`) against the record in the repo | `no-event`: written by hand or edited later. `story-mismatch`: a valid record copied to another story name. |

**What they can detect:** a commit made without the pre-commit hook (`--no-verify`, setup not run, another git client);
a review record that the hook did not write, or that changed after.

**What they cannot detect:**
- A `--no-verify` commit whose tree equals one the hook allowed before (for example a revert to an earlier state).
- A `--no-verify` commit of a tree the hook never saw, on a machine that sends no events at all: it IS reported, but so
  is every commit of that machine. Read the report together with "who sends events".
- Rebased, amended-with-changes or cherry-picked commits: the tree is new, so they show up as `no-event` (false alarm).
  Run `commits` on a branch before it is rebased, or compare by hand. Server-side squash and merge commits show up too.
- A review record the hook wrote for a "review" that the agent ran itself in the build session (the guard that blocks
  that is the review-record guard, not the report).
- A hook that was edited to emit events without checking. The hook scripts are in the project; the report trusts them.
- Anything older than the 180-day retention, and anything from before this field existed (older commits and records are
  all reported).
- A sender outage that has not drained yet: events still in a laptop's queue are not in ClickHouse. Run the report
  after the developers have pushed (the queue is flushed by the next tool call that sends).

## Tests

```sh
python3 -m unittest discover -s tests            # from the repo root. No Docker needed
python3 run_live_tests.py                         # here. Needs the running stack and Docker
python3 run_telemetry_live.py                     # here. T4 check; also needs npx, git and, for Claude Code, `claude`
```

The live script uses fake users `live-test-a` and `live-test-b`, keeps their keys in memory, and removes them at the end.
