"""Reconcile unreferenced blobs. Default is a read-only report; --apply is explicit."""

import argparse
import json
import time
from uuid import UUID

from sqlalchemy import Engine, text

from app.database import make_engine
from app.storage import BlobStore


def reconcile(engine: Engine, storage: BlobStore, apply: bool = False) -> dict[str, int]:
    result = {"referenced": 0, "recent_orphans": 0, "old_orphans": 0, "removed": 0}
    with engine.begin() as connection:
        # Serializes with all uploads through durable metadata commit, including failures.
        connection.execute(text("SELECT pg_advisory_xact_lock(813402)"))
        references = set(
            connection.execute(text("SELECT blob_key FROM document_versions")).scalars()
        )
        now = time.time()
        for path in storage.root.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            try:
                key = UUID(path.name)
            except ValueError:
                continue
            if key in references:
                result["referenced"] += 1
            elif now - path.stat().st_mtime < 3600:
                result["recent_orphans"] += 1
            else:
                result["old_orphans"] += 1
                if apply:
                    path.unlink()
                    result["removed"] += 1
        if apply and result["removed"]:
            storage.sync_directory()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Remove unreferenced blobs older than 1 hour"
    )
    options = parser.parse_args()
    engine = make_engine()
    try:
        print(json.dumps(reconcile(engine, BlobStore.from_environment(), options.apply)))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
