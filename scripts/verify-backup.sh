#!/bin/sh
# Restores only into a separate tmpfs database and removes that verification project.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"
[ "$#" -eq 1 ] || { echo 'Usage: sh scripts/verify-backup.sh /absolute/snapshot-directory' >&2; exit 2; }
BACKUP_DIRECTORY=$(CDPATH= cd -- "$1" && pwd)
export BACKUP_DIRECTORY
[ "$(cat "$BACKUP_DIRECTORY/FORMAT")" = private-ai-backup-v1 ]
(cd "$BACKUP_DIRECTORY" && sha256sum -c SHA256SUMS)
restore_compose() { docker compose -p private-ai-restore-check -f compose.restore.yaml "$@"; }
cleanup() { restore_compose down >&2; }
trap cleanup EXIT INT TERM
restore_compose up -d --wait restore-db
# Hardcoded disposable service/database: never use application DB_HOST or DB_NAME here.
restore_compose exec -T restore-db psql -U workspace_admin -d workspace_restore -v ON_ERROR_STOP=1 -c 'DROP SCHEMA workspace CASCADE'
restore_compose exec -T restore-db pg_restore -U workspace_admin -d workspace_restore --exit-on-error < "$BACKUP_DIRECTORY/database.dump"
restore_compose run --rm --no-deps --user "$(id -u):$(id -g)" verify
