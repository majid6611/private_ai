#!/bin/sh
# Coordinated local backup; pauses only this project's application writers.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"
umask 077
mkdir -p .backups
chmod 700 .backups
backup_dir=$(mktemp -d "$repo/.backups/snapshot-XXXXXXXX")
backup_services=$(docker compose ps --services --status running | awk '/^(backend|web|indexer)$/' || true)
backup_containers=''
if [ -n "$backup_services" ]; then
    backup_containers=$(docker compose ps -q $backup_services)
fi
resume() {
    backup_status=$?
    trap - EXIT INT TERM
    if [ -n "$backup_services" ]; then
        # All values come from the fixed allowlist above, not arbitrary input.
        if ! docker start $backup_containers >&2; then
            echo 'Backup captured, but restarting application containers failed' >&2
            backup_status=1
        fi
    fi
    exit "$backup_status"
}
trap resume EXIT INT TERM
if [ -n "$backup_services" ]; then
    docker compose stop $backup_services >&2
fi
docker compose exec -T db pg_dump -U workspace_admin -d workspace -Fc > "$backup_dir/database.dump"
docker compose run --rm -T --no-deps backend python -c 'import sys,tarfile; archive=tarfile.open(fileobj=sys.stdout.buffer,mode="w|"); archive.add("/data/documents",arcname="documents"); archive.close()' > "$backup_dir/documents.tar"
(cd "$backup_dir" && sha256sum database.dump documents.tar > SHA256SUMS)
printf 'private-ai-backup-v1\n' > "$backup_dir/FORMAT"
printf '%s\n' "$backup_dir"
