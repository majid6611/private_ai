import hmac
import os
import secrets
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import Field
from sqlalchemy import Connection, text

from app.access import Access, Actor
from app.documents import project_access
from app.identity import Input
from app.security import digest_token
from app.storage import BlobStore


class ChunkOffset(Input):
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class ChunkResult(Input):
    lease_token: str = Field(min_length=32, max_length=128)
    chunks: list[ChunkOffset] = Field(min_length=1, max_length=8192)


def ingestion_router(access: Access, storage: BlobStore) -> APIRouter:
    router = APIRouter()
    DB = Annotated[Connection, Depends(access.transaction, scope="function")]
    Human = Annotated[Actor, Depends(access.actor, scope="function")]

    def service(x_ingestion_key: Annotated[str | None, Header()] = None) -> None:
        path = os.environ.get("INGESTION_KEY_FILE")
        if not path:
            raise HTTPException(503, "Ingestion is not configured")
        expected = Path(path).read_text().strip()
        if not x_ingestion_key or not hmac.compare_digest(
            x_ingestion_key.encode(), expected.encode()
        ):
            raise HTTPException(401, "Service authentication required")

    Service = Annotated[None, Depends(service)]

    @router.post("/internal/v1/ingestion/lease")
    def lease(connection: DB, authenticated: Service) -> dict[str, Any]:
        connection.execute(
            text("""UPDATE intake_jobs SET status='FAILED' WHERE attempts >= 3
            AND status='LEASED' AND lease_expires_at < clock_timestamp()""")
        )
        job = (
            connection.execute(
                text("""SELECT j.id,v.id AS version_id,v.blob_key,v.sha256,v.size
            FROM intake_jobs j JOIN document_versions v ON v.id=j.version_id
            JOIN documents d ON d.active_version_id=v.id
            WHERE v.mime_type='text/plain' AND j.attempts < 3
            AND (j.status='PENDING' OR (j.status='LEASED' AND j.lease_expires_at < clock_timestamp()))
            ORDER BY j.created_at,j.id LIMIT 1 FOR UPDATE OF j SKIP LOCKED""")
            )
            .mappings()
            .first()
        )
        if job is None:
            return {"job": None}
        try:
            content = storage.get(job["blob_key"], job["sha256"], job["size"]).decode("utf-8")
        except (OSError, UnicodeDecodeError):
            connection.execute(
                text("""UPDATE intake_jobs SET attempts=attempts+1,
                status=CASE WHEN attempts+1 >= 3 THEN 'FAILED' ELSE 'PENDING' END WHERE id=:id"""),
                {"id": job["id"]},
            )
            return {"job": None}
        token = secrets.token_urlsafe(32)
        connection.execute(
            text("""UPDATE intake_jobs SET status='LEASED',attempts=attempts+1,
            lease_hash=:hash,lease_expires_at=clock_timestamp()+interval '5 minutes' WHERE id=:id"""),
            {"hash": digest_token(token), "id": job["id"]},
        )
        return {
            "job": {
                "id": str(job["id"]),
                "version_id": str(job["version_id"]),
                "lease_token": token,
                "text": content,
            }
        }

    @router.post("/internal/v1/ingestion/{job_id}/complete", status_code=204)
    def complete(job_id: UUID, body: ChunkResult, connection: DB, authenticated: Service) -> None:
        job = (
            connection.execute(
                text("""SELECT j.*,v.blob_key,v.sha256,v.size FROM intake_jobs j
            JOIN document_versions v ON v.id=j.version_id WHERE j.id=:id FOR UPDATE OF j"""),
                {"id": job_id},
            )
            .mappings()
            .first()
        )
        if job is None or not hmac.compare_digest(
            job["lease_hash"] or "", digest_token(body.lease_token)
        ):
            raise HTTPException(409, "Lease is not valid")
        if job["status"] == "DONE":
            return
        valid = connection.execute(
            text("SELECT :expiry > clock_timestamp()"), {"expiry": job["lease_expires_at"]}
        ).scalar()
        if job["status"] != "LEASED" or not valid:
            raise HTTPException(409, "Lease expired; obtain a new job")
        try:
            source = storage.get(job["blob_key"], job["sha256"], job["size"]).decode("utf-8")
        except (OSError, UnicodeDecodeError):
            raise HTTPException(503, "Source storage unavailable") from None
        end = 0
        start = -1
        for chunk in body.chunks:
            if (
                chunk.start <= start
                or chunk.start > end
                or chunk.end <= end
                or chunk.end > len(source)
                or chunk.end - chunk.start > 1600
                or end - chunk.start > 200
            ):
                raise HTTPException(422, "Chunks must cover the source in bounded ordered ranges")
            start, end = chunk.start, chunk.end
        if body.chunks[0].start != 0 or end != len(source):
            raise HTTPException(422, "Chunks must cover the entire source")
        connection.execute(
            text("DELETE FROM text_chunks WHERE version_id=:v"), {"v": job["version_id"]}
        )
        for ordinal, chunk in enumerate(body.chunks):
            # Reconstruct from stored original bytes: parser cannot invent indexed text.
            connection.execute(
                text("""INSERT INTO text_chunks
                (id,version_id,ordinal,start_offset,end_offset,content) VALUES (:id,:v,:n,:start,:end,:text)"""),
                {
                    "id": uuid4(),
                    "v": job["version_id"],
                    "n": ordinal,
                    "start": chunk.start,
                    "end": chunk.end,
                    "text": source[chunk.start : chunk.end],
                },
            )
        connection.execute(
            text("UPDATE intake_jobs SET status='DONE' WHERE id=:id"), {"id": job_id}
        )

    @router.get("/api/v1/search")
    def search(
        project_id: UUID,
        q: Annotated[str, Query(min_length=2, max_length=200)],
        connection: DB,
        human: Human,
    ) -> list[dict[str, str | int]]:
        project_access(connection, human, project_id)
        rows = connection.execute(
            text("""SELECT d.id AS document_id,d.title,c.version_id,
            c.start_offset,c.end_offset,left(c.content,350) AS snippet
            FROM text_chunks c JOIN documents d ON d.active_version_id=c.version_id
            WHERE d.project_id=:p AND (NOT d.restricted OR EXISTS
            (SELECT 1 FROM document_grants g WHERE g.document_id=d.id AND g.user_id=:u))
            AND c.search_vector @@ websearch_to_tsquery('simple',:q)
            ORDER BY ts_rank_cd(c.search_vector,websearch_to_tsquery('simple',:q)) DESC,c.id LIMIT 20"""),
            {"p": project_id, "u": human.id, "q": q},
        ).mappings()
        return [
            {
                "document_id": str(r["document_id"]),
                "title": r["title"],
                "version_id": str(r["version_id"]),
                "start_offset": r["start_offset"],
                "end_offset": r["end_offset"],
                "snippet": r["snippet"],
            }
            for r in rows
        ]

    return router
