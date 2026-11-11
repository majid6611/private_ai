import base64
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
from app.ai import retrieve
from app.documents import project_access
from app.identity import Input
from app.security import digest_token
from app.storage import BlobStore


class ChunkOffset(Input):
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class PageSpan(Input):
    page: int = Field(ge=1, le=2000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class ChunkResult(Input):
    lease_token: str = Field(min_length=32, max_length=128)
    chunks: list[ChunkOffset] = Field(max_length=8192)
    # PDF only: untrusted extraction output from the isolated parser.
    text: str | None = Field(default=None, max_length=2_000_000)
    pages: list[PageSpan] = Field(default=[], max_length=2000)
    page_count: int | None = Field(default=None, ge=1, le=2000)
    needs_ocr: bool = False
    failure: str | None = Field(
        default=None, pattern=r"^(ENCRYPTED|MALFORMED|TOO_LARGE|TOO_MANY_PAGES|TIMEOUT)$"
    )
    extractor: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.+-]{1,64}$")


def validate_pdf_result(body: ChunkResult) -> tuple[str, list[PageSpan], int]:
    """Check untrusted parser output; returns (source text, page spans, page count)."""
    source = body.text or ""
    if "\x00" in source:
        raise HTTPException(422, "Extracted text contains NUL")
    if body.needs_ocr:
        # Only an essentially empty text layer may be declared image-only.
        if len(source.strip()) >= 50 or body.chunks or body.pages:
            raise HTTPException(422, "Text was supplied; OCR is not needed")
        return "", [], body.page_count or 0
    if body.text is None or not source.strip():
        raise HTTPException(422, "Extracted text is required for a PDF")
    previous_page, previous_end = 0, 0
    for span in sorted(body.pages, key=lambda s: s.start):
        if (
            span.page <= previous_page
            or span.start < previous_end
            or span.end <= span.start
            or span.end > len(source)
        ):
            raise HTTPException(422, "Page ranges must be ordered, disjoint and within the text")
        previous_page, previous_end = span.page, span.end
    last_page = max((s.page for s in body.pages), default=0)
    count = body.page_count or last_page
    if count < last_page:
        raise HTTPException(422, "Page count is smaller than the last page range")
    return source, sorted(body.pages, key=lambda s: s.start), count


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
                text("""SELECT j.id,v.id AS version_id,v.blob_key,v.sha256,v.size,v.mime_type
            FROM intake_jobs j JOIN document_versions v ON v.id=j.version_id
            JOIN documents d ON d.active_version_id=v.id
            WHERE v.mime_type IN ('text/plain','application/pdf') AND j.attempts < 3
            AND (j.status='PENDING' OR (j.status='LEASED' AND j.lease_expires_at < clock_timestamp()))
            ORDER BY j.created_at,j.id LIMIT 1 FOR UPDATE OF j SKIP LOCKED""")
            )
            .mappings()
            .first()
        )
        if job is None:
            return {"job": None}
        try:
            raw = storage.get(job["blob_key"], job["sha256"], job["size"])
            content = raw.decode("utf-8") if job["mime_type"] == "text/plain" else ""
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
        if job["mime_type"] == "application/pdf":
            # The parser, not the backend, interprets PDF bytes; they travel as opaque base64.
            return {
                "job": {
                    "id": str(job["id"]),
                    "version_id": str(job["version_id"]),
                    "lease_token": token,
                    "kind": "pdf",
                    "pdf_b64": base64.b64encode(raw).decode("ascii"),
                }
            }
        return {
            "job": {
                "id": str(job["id"]),
                "version_id": str(job["version_id"]),
                "lease_token": token,
                "kind": "text",
                "text": content,
            }
        }

    @router.post("/internal/v1/ingestion/{job_id}/complete", status_code=204)
    def complete(job_id: UUID, body: ChunkResult, connection: DB, authenticated: Service) -> None:
        job = (
            connection.execute(
                text("""SELECT j.*,v.blob_key,v.sha256,v.size,v.mime_type FROM intake_jobs j
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
        if job["status"] in ("DONE", "NEEDS_OCR", "FAILED"):
            return
        valid = connection.execute(
            text("SELECT :expiry > clock_timestamp()"), {"expiry": job["lease_expires_at"]}
        ).scalar()
        if job["status"] != "LEASED" or not valid:
            raise HTTPException(409, "Lease expired; obtain a new job")
        pdf = job["mime_type"] == "application/pdf"
        if body.failure and not pdf:
            raise HTTPException(422, "Extraction fields are only valid for PDF versions")
        if pdf and body.failure:
            # Deterministic parser verdict on an unreadable file: do not burn retries.
            connection.execute(
                text("DELETE FROM text_chunks WHERE version_id=:v"), {"v": job["version_id"]}
            )
            connection.execute(
                text("UPDATE intake_jobs SET status='FAILED',failure_code=:c WHERE id=:id"),
                {"c": body.failure, "id": job_id},
            )
            return
        if pdf:
            source, spans, page_count = validate_pdf_result(body)
            if body.needs_ocr:
                connection.execute(
                    text("DELETE FROM text_chunks WHERE version_id=:v"), {"v": job["version_id"]}
                )
                connection.execute(
                    text("UPDATE intake_jobs SET status='NEEDS_OCR' WHERE id=:id"), {"id": job_id}
                )
                return
        else:
            if body.text is not None or body.pages or body.needs_ocr or body.extractor:
                raise HTTPException(422, "Extraction fields are only valid for PDF versions")
            try:
                source = storage.get(job["blob_key"], job["sha256"], job["size"]).decode("utf-8")
            except (OSError, UnicodeDecodeError):
                raise HTTPException(503, "Source storage unavailable") from None
            spans, page_count = [], 0
        end = 0
        start = -1
        if not body.chunks:
            raise HTTPException(422, "Chunks must cover the entire source")
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
        if pdf:
            connection.execute(
                text("DELETE FROM version_texts WHERE version_id=:v"), {"v": job["version_id"]}
            )
            connection.execute(
                text("""INSERT INTO version_texts(version_id,extractor,text,page_count)
                VALUES (:v,:extractor,:text,:pages)"""),
                {
                    "v": job["version_id"],
                    "extractor": body.extractor or "unknown",
                    "text": source,
                    "pages": page_count,
                },
            )
        for ordinal, chunk in enumerate(body.chunks):
            # Rebuilt from the stored source (original bytes, or the validated derived text),
            # never from per-chunk text supplied by the parser.
            touching = [
                s.page for s in spans if s.start < chunk.end and s.end > chunk.start
            ]
            connection.execute(
                text("""INSERT INTO text_chunks
                (id,version_id,ordinal,start_offset,end_offset,content,page_start,page_end)
                VALUES (:id,:v,:n,:start,:end,:text,:ps,:pe)"""),
                {
                    "id": uuid4(),
                    "v": job["version_id"],
                    "n": ordinal,
                    "start": chunk.start,
                    "end": chunk.end,
                    "text": source[chunk.start : chunk.end],
                    "ps": min(touching) if touching else None,
                    "pe": max(touching) if touching else None,
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
    ) -> list[dict[str, str | int | None]]:
        project_access(connection, human, project_id)
        rows = retrieve(connection, project_id, human.id, q, 20)
        return [
            {
                "document_id": str(r["document_id"]),
                "title": r["title"],
                "version_id": str(r["version_id"]),
                "start_offset": r["start_offset"],
                "end_offset": r["end_offset"],
                "page_start": r["page_start"],
                "page_end": r["page_end"],
                "snippet": r["content"][:350],
            }
            for r in rows
        ]

    return router
