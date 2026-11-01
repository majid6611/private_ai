#!/bin/sh
# End-to-end recovery drill using disposable fixture accounts/documents, never app data.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"
umask 077
mkdir -p .backups
chmod 700 .backups
fixture_dir=$(mktemp -d "$repo/.backups/recovery-fixture-XXXXXXXX")
fixture_container=''
cleanup() {
    if [ -n "$fixture_container" ]; then docker rm -f "$fixture_container" >&2 || true; fi
    docker compose --profile test rm -sf test-migrate test-db >&2
}
trap cleanup EXIT INT TERM
docker compose --profile test rm -sf test-migrate test-db >&2
fixture_container=$(docker compose --profile test run -d tests sh -c '
python -m pytest -q tests/test_approvals.py::test_rollback_keeps_history_and_blocks_aba_stale_approval tests/test_ingestion.py::test_ingestion_identity_chunks_and_revocation_filtered_search > /tmp/recovery-test-results 2>&1
result=$?
printf "%s" "$result" > /tmp/recovery-test-exit
sleep 600
')
tries=0
until docker exec "$fixture_container" test -f /tmp/recovery-test-exit; do
    tries=$((tries+1))
    [ "$tries" -lt 90 ] || { echo 'Fixture test timed out' >&2; exit 1; }
    sleep 2
done
docker exec "$fixture_container" cat /tmp/recovery-test-results
[ "$(docker exec "$fixture_container" cat /tmp/recovery-test-exit)" = 0 ]
docker compose exec -T test-db pg_dump -U workspace_admin -d workspace_test -Fc > "$fixture_dir/database.dump"
docker exec "$fixture_container" python -c 'import sys,tarfile; a=tarfile.open(fileobj=sys.stdout.buffer,mode="w|"); a.add("/tmp/test-documents",arcname="documents"); a.close()' > "$fixture_dir/documents.tar"
(cd "$fixture_dir" && sha256sum database.dump documents.tar > SHA256SUMS)
printf 'private-ai-backup-v1\n' > "$fixture_dir/FORMAT"
REQUIRE_POPULATED=1 sh scripts/verify-backup.sh "$fixture_dir"
printf 'Populated recovery drill passed: %s\n' "$fixture_dir"
