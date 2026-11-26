#!/usr/bin/env bash
# Backup and restore check for the guard event table (ClickHouse BACKUP ... TO Disk).
#
#   ./backup.sh backup            write guard-<UTC time>.zip into the clickhouse-backups volume
#   ./backup.sh verify <name>     restore it into a scratch database, compare row counts, drop the scratch copy
#   ./backup.sh list              list backups in the volume
#   ./backup.sh copy <name> <dir> copy one backup out of the volume to a folder (for off-machine storage)
#   ./backup.sh restore <name>    restore into the EMPTY table of a fresh stack (see README)
#
# Run from this folder. The admin password is read inside the container; it never appears here.
# What is covered: the guard.guard_events data. Not covered: ClickHouse users (the init script re-creates them on a
# fresh stack), the Grafana database (dashboards come from git), the htpasswd file (back it up with your key process).
set -euo pipefail
cd "$(dirname "$0")"

ch() { # run one query as admin inside the clickhouse container
  docker compose exec -T -e "Q=$1" clickhouse sh -c 'clickhouse-client --user admin --password "$CLICKHOUSE_PASSWORD" -q "$Q"'
}

name_ok() { [[ "$1" =~ ^[A-Za-z0-9._-]+\.zip$ ]] || { echo "error: backup name must look like guard-2026-01-01T00-00-00Z.zip" >&2; exit 2; }; }

case "${1:-}" in
  backup)
    name="guard-$(date -u +%Y-%m-%dT%H-%M-%SZ).zip"
    ch "BACKUP TABLE guard.guard_events TO Disk('backups', '$name')" >/dev/null
    echo "backup written: $name"
    echo "rows in the table now: $(ch 'SELECT count() FROM guard.guard_events')"
    ;;
  verify)
    name_ok "${2:-}"
    ch "DROP DATABASE IF EXISTS guard_restore_check" >/dev/null
    ch "CREATE DATABASE guard_restore_check" >/dev/null
    ch "RESTORE TABLE guard.guard_events AS guard_restore_check.guard_events FROM Disk('backups', '$2')" >/dev/null
    restored="$(ch 'SELECT count() FROM guard_restore_check.guard_events')"
    ch "DROP DATABASE guard_restore_check" >/dev/null
    echo "rows restored from the backup: $restored"
    live="$(ch 'SELECT count() FROM guard.guard_events')"
    echo "rows in the live table now:    $live"
    echo "(restored may be lower than live if events arrived after the backup. It must not be zero or higher.)"
    [[ "$restored" -gt 0 && "$restored" -le "$live" ]] || { echo "FAIL: restored count is zero or above the live count" >&2; exit 1; }
    echo "restore check OK (scratch database dropped)"
    ;;
  list)
    docker compose exec -T clickhouse ls -1 /backups
    ;;
  copy)
    name_ok "${2:-}"
    [[ -d "${3:-}" ]] || { echo "error: give an existing folder" >&2; exit 2; }
    docker compose cp "clickhouse:/backups/$2" "$3/$2"
    echo "copied to $3/$2"
    ;;
  restore)
    name_ok "${2:-}"
    ch "RESTORE TABLE guard.guard_events FROM Disk('backups', '$2') SETTINGS allow_non_empty_tables = 1" >/dev/null
    echo "restored. rows now: $(ch 'SELECT count() FROM guard.guard_events')"
    ;;
  *)
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
