# Guard event stack (local Docker)

OTel collector, ClickHouse and Grafana for the guard events (ADR 0003, option D). This is the pilot stack: it runs on
one dev machine (decision D4). The plan for the company network is in [HOSTING.md](HOSTING.md). It is written, not built.

The spike that this came from is in `spikes/collector/` and stays as it was.

## What it does

- The sender posts events to the collector (`http://127.0.0.1:4318`) with HTTP basic auth: user = developer id,
  password = the developer's ingest key.
- The collector checks the key against a bcrypt htpasswd file, **stamps `user` from the key** (a client cannot fake it),
  deletes `user.email`, `user.account_uuid` and `user.account_id`, and writes to ClickHouse as `otel_writer`.
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
| `secrets.local.ini.example` | Placeholder passwords. The real file `secrets.local.ini` is git-ignored. |
| `devkeys.htpasswd.example` | Example line with a fake hash. The real file `local/devkeys.htpasswd` is git-ignored. |

## Start

You need Docker with Compose v2.20 or newer, and Python 3.10+.

```sh
cd deploy/collector
python3 keys.py init            # creates secrets.local.ini with random passwords (or copy the .example and edit)
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

## Tests

```sh
python3 -m unittest discover -s tests            # from the repo root. No Docker needed
python3 run_live_tests.py                         # here. Needs the running stack and Docker
```

The live script uses fake users `live-test-a` and `live-test-b`, keeps their keys in memory, and removes them at the end.
