from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field
from sqlalchemy import Connection, text

from app.access import Access, Actor, audit
from app.documents import attachment, document_access, project_access
from app.identity import Input
from app.storage import BlobStore


class ApprovalInput(Input):
    candidate_version_id: UUID = Field(strict=False)
    expected_active_version_id: UUID | None = Field(strict=False)
    expected_publication_revision: int = Field(ge=0)


class PublishInput(Input):
    approval_id: UUID = Field(strict=False)


def approval_document(
    connection: Connection, human: Actor, document_id: UUID, capability: str
) -> dict[str, Any]:
    # Take the same exclusive project lock used by all permission changes before checking.
    project_id = connection.execute(
        text("SELECT project_id FROM documents WHERE id=:d"), {"d": document_id}
    ).scalar()
    if project_id is None:
        raise HTTPException(404, "Document not found")
    connection.execute(text("SELECT id FROM projects WHERE id=:p FOR UPDATE"), {"p": project_id})
    doc = document_access(connection, human, document_id)
    if capability == "edit":
        allowed = doc["can_edit"]
    else:
        # `capability` is an internal allowlisted key, never SQL interpolated input.
        membership = (
            connection.execute(
                text("""SELECT can_approve,can_publish FROM memberships
            WHERE project_id=:p AND user_id=:u"""),
                {"p": project_id, "u": human.id},
            )
            .mappings()
            .one()
        )
        allowed = (
            (doc["can_edit"] or membership["can_approve"] or membership["can_publish"])
            if capability == "workflow"
            else membership[capability]
        )
    if not allowed:
        raise HTTPException(403, "Workflow permission required")
    return doc


def approvals_router(access: Access, storage: BlobStore) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    DB = Annotated[Connection, Depends(access.transaction, scope="function")]
    Human = Annotated[Actor, Depends(access.actor, scope="function")]

    def candidate(connection: Connection, doc_id: UUID, version_id: UUID) -> dict[str, Any]:
        row = (
            connection.execute(
                text("""SELECT * FROM document_versions
            WHERE document_id=:d AND id=:v"""),
                {"d": doc_id, "v": version_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(404, "Version not found")
        if row["mime_type"] not in ("text/plain", "application/pdf"):
            raise HTTPException(409, "Only validated text or PDF versions can be reviewed/published")
        try:
            storage.get(row["blob_key"], row["sha256"], row["size"])
        except OSError:
            raise HTTPException(503, "Document storage unavailable") from None
        return dict(row)

    def approval(
        connection: Connection, human: Actor, approval_id: UUID, capability: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        doc_id = connection.execute(
            text("SELECT document_id FROM approvals WHERE id=:id"), {"id": approval_id}
        ).scalar()
        if doc_id is None:
            raise HTTPException(404, "Approval not found")
        doc = approval_document(connection, human, doc_id, capability)
        row = (
            connection.execute(
                text("SELECT * FROM approvals WHERE id=:id FOR UPDATE"), {"id": approval_id}
            )
            .mappings()
            .one()
        )
        return doc, dict(row)

    def validate(connection: Connection, doc: dict[str, Any], request: dict[str, Any]) -> None:
        current_acl = connection.execute(
            text("SELECT acl_revision FROM projects WHERE id=:p"), {"p": doc["project_id"]}
        ).scalar_one()
        unexpired = connection.execute(
            text("SELECT :expiry > clock_timestamp()"), {"expiry": request["expires_at"]}
        ).scalar()
        if not unexpired or request["status"] in ("CONSUMED", "REJECTED"):
            raise HTTPException(409, "Approval expired, rejected or consumed")
        if (
            current_acl != request["acl_revision"]
            or doc["active_version_id"] != request["expected_active_version_id"]
            or doc["publication_revision"] != request["expected_publication_revision"]
        ):
            raise HTTPException(409, "Permissions or publication changed; request a new review")
        requester = connection.execute(
            text("SELECT id FROM users WHERE id=:u AND enabled AND auth_revision=:rev FOR SHARE"),
            {"u": request["requester_id"], "rev": request["requester_auth_revision"]},
        ).scalar()
        if requester is None:
            raise HTTPException(409, "Requester authorization changed; request a new review")
        version = candidate(connection, doc["id"], request["candidate_version_id"])
        if version["state"] == "REJECTED":
            raise HTTPException(409, "Candidate was rejected")
        if version["sha256"] != request["candidate_sha256"]:
            raise HTTPException(409, "Candidate hash changed")

    @router.post("/documents/{document_id}/approvals", status_code=201)
    def request_approval(
        document_id: UUID, body: ApprovalInput, connection: DB, human: Human
    ) -> dict[str, str]:
        doc = approval_document(connection, human, document_id, "edit")
        if (
            doc["active_version_id"] != body.expected_active_version_id
            or doc["publication_revision"] != body.expected_publication_revision
        ):
            raise HTTPException(409, "Publication changed; reload before requesting review")
        if (
            body.candidate_version_id not in (doc["latest_version_id"], doc["active_version_id"])
            and not doc["can_history"]
        ):
            raise HTTPException(403, "History permission required for a historical candidate")
        version = candidate(connection, document_id, body.candidate_version_id)
        if version["state"] == "REJECTED":
            raise HTTPException(409, "Create a new draft for rejected content")
        if doc["active_version_id"] == body.candidate_version_id:
            raise HTTPException(409, "Version is already active")
        revision = connection.execute(
            text("SELECT acl_revision FROM projects WHERE id=:p"), {"p": doc["project_id"]}
        ).scalar_one()
        approval_id = uuid4()
        connection.execute(
            text("""INSERT INTO approvals(id,document_id,candidate_version_id,
            candidate_sha256,expected_active_version_id,expected_publication_revision,
            acl_revision,requester_id,requester_auth_revision) VALUES (:id,:d,:v,:hash,:active,:generation,:acl,:u,
            (SELECT auth_revision FROM users WHERE id=:u))"""),
            {
                "id": approval_id,
                "d": document_id,
                "v": body.candidate_version_id,
                "hash": version["sha256"],
                "active": body.expected_active_version_id,
                "generation": body.expected_publication_revision,
                "acl": revision,
                "u": human.id,
            },
        )
        if version["state"] == "DRAFT":
            connection.execute(
                text("UPDATE document_versions SET state='PENDING_APPROVAL' WHERE id=:v"),
                {"v": body.candidate_version_id},
            )
        audit(connection, human.id, "approval_requested", approval_id)
        return {"id": str(approval_id), "candidate_sha256": version["sha256"]}

    @router.get("/projects/{project_id}/approvals/pending")
    def pending_for_me(
        project_id: UUID, connection: DB, human: Human
    ) -> list[dict[str, str]]:
        """Requests this person could review right now: approver, independent, shared, unexpired."""
        membership = project_access(connection, human, project_id)
        if not membership["can_approve"]:
            return []
        rows = (
            connection.execute(
                text("""SELECT a.id,a.document_id,a.candidate_version_id,a.created_at,
            d.title,u.username AS requester,v.mime_type,v.author_id
            FROM approvals a JOIN documents d ON d.id=a.document_id
            JOIN projects pr ON pr.id=d.project_id
            JOIN users u ON u.id=a.requester_id
            JOIN document_versions v ON v.id=a.candidate_version_id
            WHERE d.project_id=:p AND a.status='PENDING' AND a.expires_at>clock_timestamp()
            AND a.requester_id<>:me AND v.author_id<>:me
            -- Mirror validate(): stale requests cannot be approved, so are not listed.
            AND a.acl_revision=pr.acl_revision
            AND a.expected_active_version_id IS NOT DISTINCT FROM d.active_version_id
            AND a.expected_publication_revision=d.publication_revision
            AND u.enabled AND u.auth_revision=a.requester_auth_revision
            AND v.state<>'REJECTED'

            ORDER BY a.created_at DESC LIMIT 50"""),
                {"p": project_id, "me": human.id},
            )
            .mappings()
            .all()
        )
        items: list[dict[str, str]] = []
        for row in rows:
            try:
                # Same visibility rules as opening the document (restricted/grants).
                approval_document(connection, human, row["document_id"], "can_approve")
            except HTTPException:
                continue
            items.append(
                {
                    "approval_id": str(row["id"]),
                    "document_id": str(row["document_id"]),
                    "candidate_version_id": str(row["candidate_version_id"]),
                    "title": row["title"],
                    "requester": row["requester"],
                    "kind": "pdf" if row["mime_type"] == "application/pdf" else "text",
                    "requested_at": row["created_at"].isoformat(),
                }
            )
        return items

    @router.get("/documents/{document_id}/approvals")
    def list_approvals(
        document_id: UUID, connection: DB, human: Human
    ) -> list[dict[str, str | None]]:
        document_access(connection, human, document_id)
        # Read-only members must not learn identifiers/hashes of unpublished drafts.
        approval_document(connection, human, document_id, "workflow")
        rows = connection.execute(
            text("""SELECT id,candidate_version_id,candidate_sha256,
            status,expires_at,approver_id FROM approvals WHERE document_id=:d
            ORDER BY created_at DESC LIMIT 100"""),
            {"d": document_id},
        ).mappings()
        return [
            {key: str(value) if value is not None else None for key, value in r.items()}
            for r in rows
        ]

    @router.get("/approvals/{approval_id}/review")
    def review(approval_id: UUID, connection: DB, human: Human) -> dict[str, str | None]:
        doc, request = approval(connection, human, approval_id, "can_approve")
        validate(connection, doc, request)
        version = candidate(connection, doc["id"], request["candidate_version_id"])
        # PDFs are reviewed in the reviewer's own viewer via /candidate; only text is inlined.
        proposed: str | None = None
        if version["mime_type"] == "text/plain":
            proposed = storage.get(version["blob_key"], version["sha256"], version["size"]).decode(
                "utf-8"
            )
        original: str | None = None
        if doc["active_version_id"]:
            base = candidate(connection, doc["id"], doc["active_version_id"])
            if base["mime_type"] == "text/plain":
                original = storage.get(base["blob_key"], base["sha256"], base["size"]).decode(
                    "utf-8"
                )
        return {
            "approval_id": str(approval_id),
            "candidate_sha256": version["sha256"],
            "kind": "pdf" if version["mime_type"] == "application/pdf" else "text",
            "original": original,
            "proposed": proposed,
        }

    @router.get("/approvals/{approval_id}/candidate")
    def candidate_content(approval_id: UUID, connection: DB, human: Human) -> Response:
        """The exact bytes under review, for approvers who cannot read drafts generally."""
        doc, request = approval(connection, human, approval_id, "can_approve")
        validate(connection, doc, request)
        version = candidate(connection, doc["id"], request["candidate_version_id"])
        content = storage.get(version["blob_key"], version["sha256"], version["size"])
        audit(connection, human.id, "candidate_downloaded", approval_id)
        return attachment(content, version["mime_type"], version["sha256"])

    @router.post("/approvals/{approval_id}/approve", status_code=204)
    def approve(approval_id: UUID, connection: DB, human: Human) -> None:
        doc, request = approval(connection, human, approval_id, "can_approve")
        validate(connection, doc, request)
        version = candidate(connection, doc["id"], request["candidate_version_id"])
        if human.id in (request["requester_id"], version["author_id"]):
            raise HTTPException(403, "Independent reviewer required")
        if request["status"] != "PENDING":
            raise HTTPException(409, "Approval is not pending")
        connection.execute(
            text("""UPDATE approvals SET status='APPROVED',approver_id=:u,
            approver_auth_revision=(SELECT auth_revision FROM users WHERE id=:u),
            approved_at=clock_timestamp() WHERE id=:id"""),
            {"u": human.id, "id": approval_id},
        )
        audit(connection, human.id, "approval_granted", approval_id)

    @router.post("/approvals/{approval_id}/reject", status_code=204)
    def reject(approval_id: UUID, connection: DB, human: Human) -> None:
        doc, request = approval(connection, human, approval_id, "can_approve")
        validate(connection, doc, request)
        connection.execute(
            text("UPDATE approvals SET status='REJECTED' WHERE id=:id"), {"id": approval_id}
        )
        connection.execute(
            text(
                "UPDATE document_versions SET state='REJECTED' WHERE id=:v AND state <> 'PUBLISHED'"
            ),
            {"v": request["candidate_version_id"]},
        )
        audit(connection, human.id, "approval_rejected", approval_id)

    @router.post("/documents/{document_id}/publish")
    def publish(
        document_id: UUID, body: PublishInput, connection: DB, human: Human
    ) -> dict[str, str | int]:
        doc = approval_document(connection, human, document_id, "can_publish")
        request = (
            connection.execute(
                text("""SELECT * FROM approvals
            WHERE id=:id AND document_id=:d FOR UPDATE"""),
                {"id": body.approval_id, "d": document_id},
            )
            .mappings()
            .first()
        )
        if request is None:
            raise HTTPException(404, "Approval not found")
        req = dict(request)
        validate(connection, doc, req)
        if req["status"] != "APPROVED" or req["approver_id"] is None:
            raise HTTPException(409, "Human approval required")
        # Lock reviewer status until commit: disable cannot race this final authorization.
        reviewer = (
            connection.execute(
                text(
                    "SELECT id,username FROM users WHERE id=:u AND enabled AND auth_revision=:rev FOR SHARE"
                ),
                {"u": req["approver_id"], "rev": req["approver_auth_revision"]},
            )
            .mappings()
            .first()
        )
        if reviewer is None:
            raise HTTPException(409, "Reviewer no longer authorized")
        approval_document(
            connection,
            Actor(reviewer["id"], reviewer["username"], False, ""),
            document_id,
            "can_approve",
        )
        generation = doc["publication_revision"] + 1
        connection.execute(
            text("""UPDATE documents SET active_version_id=:v,
            publication_revision=:generation WHERE id=:d"""),
            {"v": req["candidate_version_id"], "generation": generation, "d": document_id},
        )
        connection.execute(
            text("UPDATE document_versions SET state='PUBLISHED' WHERE id=:v"),
            {"v": req["candidate_version_id"]},
        )
        connection.execute(
            text("""UPDATE approvals SET status='CONSUMED',consumed_at=clock_timestamp()
            WHERE id=:id"""),
            {"id": body.approval_id},
        )
        connection.execute(
            text("""INSERT INTO publication_events
            (id,document_id,version_id,approval_id,actor_id,publication_revision)
            VALUES (:id,:d,:v,:approval,:u,:generation)"""),
            {
                "id": uuid4(),
                "d": document_id,
                "v": req["candidate_version_id"],
                "approval": body.approval_id,
                "u": human.id,
                "generation": generation,
            },
        )
        audit(connection, human.id, "version_published", body.approval_id)
        return {
            "active_version_id": str(req["candidate_version_id"]),
            "publication_revision": generation,
        }

    return router
