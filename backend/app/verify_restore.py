"""Validate an isolated restored database against the corresponding file snapshot."""

import hashlib
import json
import os
import tarfile
from pathlib import Path

from sqlalchemy import text

from app.database import make_engine
from app.storage import BlobStore


def main() -> None:
    backup = Path("/backup")
    if (backup / "FORMAT").read_text().strip() != "private-ai-backup-v1":
        raise ValueError("Unsupported/incomplete backup")
    for line in (backup / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        if name not in ("database.dump", "documents.tar"):
            raise ValueError("Unexpected backup member")
        with (backup / name).open("rb") as source_file:
            if hashlib.file_digest(source_file, "sha256").hexdigest() != digest:
                raise ValueError("Backup checksum mismatch")
    restore = Path("/tmp/restore")
    restore.mkdir()
    with tarfile.open(backup / "documents.tar") as archive:
        archive.extractall(restore, filter="data")
    storage = BlobStore(restore / "documents")
    engine = make_engine()
    try:
        if engine.url.database != "workspace_restore":
            raise ValueError("Refusing non-disposable target")
        with engine.begin() as connection:
            rows = connection.execute(
                text("SELECT blob_key,sha256,size FROM document_versions")
            ).mappings()
            versions = 0
            for row in rows:
                storage.get(row["blob_key"], row["sha256"], row["size"])
                versions += 1
            # Foreign keys validate document/pointer relationships during pg_restore.
            bad = connection.execute(
                text("""SELECT count(*) FROM approvals a
                JOIN document_versions v ON v.id=a.candidate_version_id
                WHERE a.candidate_sha256<>v.sha256""")
            ).scalar_one()
            if bad:
                raise ValueError("Approval target hash mismatch")
            chunks = 0
            for row in connection.execute(
                text("""SELECT c.*,v.blob_key,v.sha256,v.size
                FROM text_chunks c JOIN document_versions v ON v.id=c.version_id""")
            ).mappings():
                source = storage.get(row["blob_key"], row["sha256"], row["size"]).decode("utf-8")
                if row["content"] != source[row["start_offset"] : row["end_offset"]]:
                    raise ValueError("Restored chunk provenance mismatch")
                chunks += 1
            role = connection.execute(
                text("SELECT rolsuper,rolcreatedb FROM pg_roles WHERE rolname=current_user")
            ).one()
            if role[0] or role[1]:
                raise ValueError("Application database role is overprivileged")
            privileges = connection.execute(
                text("""SELECT
                has_schema_privilege(current_user,'workspace','CREATE'),
                has_column_privilege(current_user,'document_versions','sha256','UPDATE'),
                has_table_privilege(current_user,'security_events','DELETE')""")
            ).one()
            if any(privileges):
                raise ValueError("Restored immutable/audit privileges are too broad")
            inconsistent = connection.execute(
                text("""SELECT count(*) FROM documents d
                JOIN document_versions v ON v.id=d.active_version_id
                LEFT JOIN publication_events e ON e.document_id=d.id AND
                    e.publication_revision=d.publication_revision
                WHERE v.state<>'PUBLISHED' OR e.version_id IS DISTINCT FROM d.active_version_id""")
            ).scalar_one()
            if inconsistent:
                raise ValueError("Restored active pointer/publication event mismatch")
            documents = connection.execute(text("SELECT count(*) FROM documents")).scalar_one()
            users = connection.execute(text("SELECT count(*) FROM users")).scalar_one()
            if os.environ.get("REQUIRE_POPULATED") == "1" and not (versions and chunks and users):
                raise ValueError("A populated fixture restore was required")
            events = connection.execute(
                text("SELECT count(*) FROM publication_events")
            ).scalar_one()
        print(
            json.dumps(
                {
                    "verified_documents": documents,
                    "verified_versions": versions,
                    "publication_events": events,
                    "verified_chunks": chunks,
                    "verified_users": users,
                }
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
