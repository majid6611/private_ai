import asyncio
import hashlib
import json
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import Connection, text
from starlette.concurrency import run_in_threadpool

from app.access import Access, Actor, audit
from app.identity import Input, Membership
from app.storage import BlobStore

MAX_UPLOAD = 2 * 1024 * 1024


def project_access(
    connection: Connection, human: Actor, project_id: UUID, edit: bool = False, write: bool = False
) -> dict[str, Any]:
    # Lock project first, then evaluate membership in a fresh READ COMMITTED statement.
    # This is also the lock used by membership/ACL changes and quota accounting.
    connection.execute(
        text(
            "SELECT id FROM projects WHERE id=:p "
            + ("FOR UPDATE" if edit or write else "FOR SHARE")
        ),
        {"p": project_id},
    )
    row = (
        connection.execute(
            text("""SELECT m.* FROM memberships m
        WHERE project_id=:p AND user_id=:u"""),
            {"p": project_id, "u": human.id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Project not found")
    if edit and not row["can_edit"]:
        raise HTTPException(403, "Edit permission required")
    return dict(row)


def document_access(
    connection: Connection, human: Actor, document_id: UUID, edit: bool = False
) -> dict[str, Any]:
    project_id = connection.execute(
        text("SELECT project_id FROM documents WHERE id=:d"), {"d": document_id}
    ).scalar()
    if project_id is None:
        raise HTTPException(404, "Document not found")
    membership = project_access(connection, human, project_id, write=edit)
    row = (
        connection.execute(
            text("""SELECT d.*, g.user_id AS grant_user,
        g.can_edit AS grant_edit, g.can_history AS grant_history FROM documents d
        LEFT JOIN document_grants g ON g.document_id=d.id AND g.user_id=:u
        WHERE d.id=:d"""),
            {"d": document_id, "u": human.id},
        )
        .mappings()
        .one()
    )
    if row["restricted"] and row["grant_user"] is None:
        raise HTTPException(404, "Document not found")
    result = dict(row)
    result["can_edit"] = membership["can_edit"] and (not row["restricted"] or row["grant_edit"])
    result["can_history"] = membership["can_history"] and (
        not row["restricted"] or row["grant_history"]
    )
    if edit and not result["can_edit"]:
        raise HTTPException(403, "Edit permission required")
    return result


async def read_upload(request: Request, mime: str) -> bytes:
    if mime not in ("text/plain", "application/pdf"):
        raise HTTPException(415, "Only UTF-8 text and PDF intake are supported")
    data = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_UPLOAD:
                    raise HTTPException(413, "Upload exceeds 2 MiB")
                data.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "Upload timed out") from None
    if not data:
        raise HTTPException(422, "Empty uploads are not accepted")
    if mime == "text/plain":
        try:
            decoded = data.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(415, "Text must be UTF-8") from None
        if "\x00" in decoded or decoded.lstrip().startswith(("%PDF-", "<!DOCTYPE html", "<html")):
            raise HTTPException(415, "Content does not match plain text")
    elif not data.startswith(b"%PDF-"):
        raise HTTPException(415, "Content does not match PDF")
    return bytes(data)


def attachment(content: bytes, mime: str, sha256: str) -> Response:
    """Stored bytes are only ever served as an inert download, never rendered by origin."""
    return Response(
        content,
        media_type=mime,
        headers={
            "Content-Disposition": 'attachment; filename="document.'
            + ("pdf" if mime == "application/pdf" else "txt")
            + '"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "no-store",
            "ETag": '"' + sha256 + '"',
        },
    )


def documents_router(access: Access, storage: BlobStore) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    DB = Annotated[Connection, Depends(access.transaction, scope="function")]
    Human = Annotated[Actor, Depends(access.actor, scope="function")]
    Key = Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"),
    ]

    def persist(
        connection: Connection,
        human: Actor,
        content: bytes,
        project_id: UUID,
        title: str,
        mime: str,
        restricted: bool,
        key: str,
        document_id: UUID | None = None,
        parent: UUID | None = None,
    ) -> dict[str, str]:
        operation = f"version:{document_id}" if document_id else f"create:{project_id}"
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    str(project_id),
                    title,
                    mime,
                    restricted,
                    str(parent),
                    hashlib.sha256(content).hexdigest(),
                ]
            ).encode()
        ).hexdigest()
        previous = (
            connection.execute(
                text("""SELECT * FROM document_requests
            WHERE actor_id=:u AND operation=:op AND request_key=:key"""),
                {"u": human.id, "op": operation, "key": key},
            )
            .mappings()
            .first()
        )
        if previous:
            document_access(connection, human, previous["document_id"], edit=True)
            if previous["fingerprint"] != fingerprint:
                raise HTTPException(409, "Idempotency key was used with different content")
            return {
                "document_id": str(previous["document_id"]),
                "version_id": str(previous["version_id"]),
            }
        if document_id:
            document = document_access(connection, human, document_id, edit=True)
            if document["latest_version_id"] != parent:
                raise HTTPException(409, "Version changed; reload before creating a draft")
        new_document = document_id is None
        document_id = document_id or uuid4()
        version_id = uuid4()
        # Maintenance takes the exclusive counterpart; no in-flight blob can be collected.
        connection.execute(text("SELECT pg_advisory_xact_lock_shared(813402)"))
        try:
            blob_key = storage.put(content)
        except OSError:
            raise HTTPException(503, "Document storage unavailable") from None
        if new_document:
            connection.execute(
                text("""INSERT INTO documents(id,project_id,title,owner_id,restricted)
                VALUES (:d,:p,:title,:u,:restricted)"""),
                {
                    "d": document_id,
                    "p": project_id,
                    "title": title,
                    "u": human.id,
                    "restricted": restricted,
                },
            )
            connection.execute(
                text("""INSERT INTO document_grants(document_id,user_id,can_edit,can_history)
                VALUES (:d,:u,true,true)"""),
                {"d": document_id, "u": human.id},
            )
        connection.execute(
            text("""INSERT INTO document_versions
            (id,document_id,parent_version_id,blob_key,sha256,size,mime_type,author_id)
            VALUES (:v,:d,:parent,:blob,:hash,:size,:mime,:u)"""),
            {
                "v": version_id,
                "d": document_id,
                "parent": parent,
                "blob": blob_key,
                "hash": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "mime": mime,
                "u": human.id,
            },
        )
        connection.execute(
            text("UPDATE documents SET latest_version_id=:v WHERE id=:d"),
            {"v": version_id, "d": document_id},
        )
        connection.execute(
            text("""INSERT INTO intake_jobs(id,version_id) VALUES (:id,:v)"""),
            {"id": uuid4(), "v": version_id},
        )
        connection.execute(
            text("""INSERT INTO document_requests
            (actor_id,operation,request_key,fingerprint,document_id,version_id)
            VALUES (:u,:op,:key,:fp,:d,:v)"""),
            {
                "u": human.id,
                "op": operation,
                "key": key,
                "fp": fingerprint,
                "d": document_id,
                "v": version_id,
            },
        )
        audit(connection, human.id, "draft_created", document_id)
        return {"document_id": str(document_id), "version_id": str(version_id)}

    def reserve_intake(
        connection: Connection,
        human: Actor,
        project_id: UUID,
        operation: str,
        key: str,
        request: Request,
    ) -> int:
        try:
            expected = int(request.headers["content-length"])
        except (KeyError, ValueError):
            raise HTTPException(411, "An exact Content-Length is required") from None
        if expected <= 0:
            raise HTTPException(422, "Empty uploads are not accepted")
        if expected > MAX_UPLOAD:
            raise HTTPException(413, "Upload exceeds 2 MiB")
        previous = connection.execute(
            text("""SELECT 1 FROM document_requests
            WHERE actor_id=:u AND operation=:op AND request_key=:key"""),
            {"u": human.id, "op": operation, "key": key},
        ).scalar()
        if not previous:
            reserved = connection.execute(
                text("""UPDATE projects SET storage_bytes=storage_bytes+:size
                WHERE id=:p AND storage_bytes+:size <= quota_bytes RETURNING id"""),
                {"p": project_id, "size": expected},
            ).scalar()
            if reserved is None:
                raise HTTPException(413, "Project storage quota exceeded")
        return expected

    @router.post("/documents", status_code=201)
    async def create(
        request: Request,
        connection: DB,
        human: Human,
        key: Key,
        project_id: UUID,
        title: Annotated[str, Query(min_length=1, max_length=200)],
        restricted: bool = False,
    ) -> dict[str, str]:
        await run_in_threadpool(project_access, connection, human, project_id, True)
        expected = reserve_intake(
            connection, human, project_id, f"create:{project_id}", key, request
        )
        mime = request.headers.get("content-type", "").split(";")[0].strip().lower()
        content = await read_upload(request, mime)
        if len(content) != expected:
            raise HTTPException(400, "Content-Length does not match upload")
        return await run_in_threadpool(
            persist, connection, human, content, project_id, title, mime, restricted, key
        )

    @router.post("/documents/{document_id}/versions", status_code=201)
    async def append(
        document_id: UUID,
        parent_version_id: UUID,
        request: Request,
        connection: DB,
        human: Human,
        key: Key,
    ) -> dict[str, str]:
        document = await run_in_threadpool(document_access, connection, human, document_id, True)
        expected = reserve_intake(
            connection, human, document["project_id"], f"version:{document_id}", key, request
        )
        mime = request.headers.get("content-type", "").split(";")[0].strip().lower()
        content = await read_upload(request, mime)
        if len(content) != expected:
            raise HTTPException(400, "Content-Length does not match upload")
        return await run_in_threadpool(
            persist,
            connection,
            human,
            content,
            document["project_id"],
            document["title"],
            mime,
            document["restricted"],
            key,
            document_id,
            parent_version_id,
        )

    @router.get("/documents")
    def listing(
        project_id: UUID,
        connection: DB,
        human: Human,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict[str, str]]:
        project_access(connection, human, project_id)
        rows = connection.execute(
            text("""SELECT d.id,d.title FROM documents d WHERE project_id=:p
            AND (NOT restricted OR EXISTS(SELECT 1 FROM document_grants g
            WHERE g.document_id=d.id AND g.user_id=:u)) ORDER BY d.id LIMIT :limit OFFSET :offset"""),
            {"p": project_id, "u": human.id, "limit": limit, "offset": offset},
        ).mappings()
        return [{"id": str(row["id"]), "title": row["title"]} for row in rows]

    @router.get("/documents/{document_id}")
    def metadata(document_id: UUID, connection: DB, human: Human) -> dict[str, str | bool | None]:
        doc = document_access(connection, human, document_id)
        return {
            "id": str(doc["id"]),
            "title": doc["title"],
            "owner_id": str(doc["owner_id"]),
            "restricted": bool(doc["restricted"]),
            "publication_revision": str(doc["publication_revision"]),
            "latest_version_id": str(doc["latest_version_id"]) if doc["can_edit"] else None,
            "active_version_id": str(doc["active_version_id"])
            if doc["active_version_id"]
            else None,
        }

    @router.get("/documents/{document_id}/versions")
    def versions(
        document_id: UUID,
        connection: DB,
        human: Human,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, str | int | None]]:
        doc = document_access(connection, human, document_id)
        if not doc["can_history"]:
            raise HTTPException(403, "History permission required")
        rows = connection.execute(
            text("""SELECT v.id,v.sha256,v.size,v.state,v.mime_type,
            j.status AS index_status,j.failure_code AS index_error,t.page_count
            FROM document_versions v LEFT JOIN intake_jobs j ON j.version_id=v.id
            LEFT JOIN version_texts t ON t.version_id=v.id
            WHERE v.document_id=:d AND (v.state='PUBLISHED' OR :edit)
            ORDER BY v.created_at DESC,v.id DESC LIMIT :limit"""),
            {"d": document_id, "edit": bool(doc["can_edit"]), "limit": limit},
        ).mappings()
        return [
            {
                "id": str(r["id"]),
                "sha256": r["sha256"],
                "size": r["size"],
                "state": r["state"],
                "mime_type": r["mime_type"],
                "index_status": r["index_status"],
                "index_error": r["index_error"],
                "page_count": r["page_count"],
            }
            for r in rows
        ]

    @router.get("/documents/{document_id}/content")
    def download(
        document_id: UUID, connection: DB, human: Human, version_id: UUID | None = None
    ) -> Response:
        doc = document_access(connection, human, document_id)
        chosen = (
            version_id
            or doc["active_version_id"]
            or (doc["latest_version_id"] if doc["can_edit"] else None)
        )
        row = (
            connection.execute(
                text("SELECT * FROM document_versions WHERE id=:v AND document_id=:d"),
                {"v": chosen, "d": document_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(404, "Version not found")
        if row["state"] != "PUBLISHED" and not doc["can_edit"]:
            raise HTTPException(403, "Draft read permission required")
        if (
            chosen not in (doc["active_version_id"], doc["latest_version_id"])
            and not doc["can_history"]
        ):
            raise HTTPException(403, "History permission required")
        try:
            content = storage.get(row["blob_key"], row["sha256"], row["size"])
        except OSError:
            raise HTTPException(503, "Document storage unavailable") from None
        audit(connection, human.id, "document_downloaded", document_id)
        return attachment(content, row["mime_type"], row["sha256"])

    def require_owner(connection: Connection, human: Actor, document_id: UUID) -> dict[str, Any]:
        doc = document_access(connection, human, document_id, edit=True)
        if doc["owner_id"] != human.id:
            raise HTTPException(403, "Document owner permission required")
        return doc

    class Restriction(Input):
        restricted: bool

    @router.patch("/documents/{document_id}/access", status_code=204)
    def restriction(document_id: UUID, body: Restriction, connection: DB, human: Human) -> None:
        doc = require_owner(connection, human, document_id)
        connection.execute(
            text("UPDATE documents SET restricted=:r WHERE id=:d"),
            {"r": body.restricted, "d": document_id},
        )
        connection.execute(
            text("UPDATE projects SET acl_revision=acl_revision+1 WHERE id=:p"),
            {"p": doc["project_id"]},
        )
        audit(connection, human.id, "document_access_changed", document_id)

    @router.put("/documents/{document_id}/access/{user_id}", status_code=204)
    def grant(
        document_id: UUID, user_id: UUID, body: Membership, connection: DB, human: Human
    ) -> None:
        doc = require_owner(connection, human, document_id)
        if body.can_approve or body.can_publish:
            raise HTTPException(422, "Document approval grants are not implemented")
        if not connection.execute(
            text("SELECT 1 FROM memberships WHERE project_id=:p AND user_id=:u"),
            {"p": doc["project_id"], "u": user_id},
        ).scalar():
            raise HTTPException(404, "Project member not found")
        connection.execute(
            text("""INSERT INTO document_grants(document_id,user_id,can_edit,can_history)
            VALUES (:d,:u,:edit,:history) ON CONFLICT(document_id,user_id) DO UPDATE SET
            can_edit=EXCLUDED.can_edit,can_history=EXCLUDED.can_history"""),
            {"d": document_id, "u": user_id, "edit": body.can_edit, "history": body.can_history},
        )
        connection.execute(
            text("UPDATE projects SET acl_revision=acl_revision+1 WHERE id=:p"),
            {"p": doc["project_id"]},
        )
        audit(connection, human.id, "document_grant_updated", document_id)

    @router.delete("/documents/{document_id}/access/{user_id}", status_code=204)
    def revoke_grant(document_id: UUID, user_id: UUID, connection: DB, human: Human) -> None:
        doc = require_owner(connection, human, document_id)
        if user_id == doc["owner_id"]:
            raise HTTPException(409, "Cannot revoke the owner's grant")
        connection.execute(
            text("DELETE FROM document_grants WHERE document_id=:d AND user_id=:u"),
            {"d": document_id, "u": user_id},
        )
        connection.execute(
            text("UPDATE projects SET acl_revision=acl_revision+1 WHERE id=:p"),
            {"p": doc["project_id"]},
        )
        audit(connection, human.id, "document_grant_revoked", document_id)

    return router
