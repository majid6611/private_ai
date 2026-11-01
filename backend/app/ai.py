"""Read-only AI delegation. No filesystem/SQL/shell or mutation tools are delegated."""

import hmac
import os
import re
import secrets
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import Field
from sqlalchemy import Connection, text

from app.access import Access, Actor, audit
from app.documents import project_access
from app.identity import Input
from app.security import digest_token


class Question(Input):
    project_id: UUID = Field(strict=False)
    question: str = Field(min_length=2, max_length=1000)


class Result(Input):
    lease_token: str = Field(min_length=32, max_length=128)
    answer: str = Field(max_length=12000)
    citation_ids: list[Annotated[UUID, Field(strict=False)]] = Field(max_length=8)
    error_code: str | None = Field(default=None, pattern="^(MODEL_UNAVAILABLE|INVALID_OUTPUT)$")


STOPWORDS = frozenset(
    """a about after all also an and any are as at be been before but by can could did do does
    for from had has have how i if in into is it its may me more most must my no not of on or
    our shall should so some such than that the their them then there these they this to under
    up was we were what when where which who whom why will with would you your""".split()
)


def query_terms(question: str, limit: int = 24) -> list[str]:
    """Distinct content words of a question; user text is never passed to tsquery syntax."""
    seen: dict[str, None] = {}
    for word in re.findall(r"[a-z0-9]+(?:[.,][0-9]+)*", question.lower()):
        if word not in STOPWORDS and (len(word) > 1 or word.isdigit()):
            seen.setdefault(word)
    return list(seen)[:limit]


def retrieve(
    connection: Connection, project_id: UUID, user_id: UUID, question: str, limit: int = 8
) -> list[dict[str, Any]]:
    """Authorized published chunks ranked for a question; the only AI retrieval path.

    Any content word of the passage or its document title may match (OR), ranked by
    PostgreSQL cover-density. Authorization
    filters are part of the same statement, so unauthorized chunks are never ranked.
    """
    terms = query_terms(question)
    if not terms:
        return []
    rows = (
        connection.execute(
            text("""WITH q AS (SELECT to_tsquery('simple',
                string_agg(plainto_tsquery('simple',t)::text,' | ')) AS query
                FROM unnest(CAST(:terms AS text[])) AS t)
        SELECT c.id,c.content,d.publication_revision FROM text_chunks c, q
        JOIN documents d ON d.project_id=:p
        WHERE d.active_version_id=c.version_id
        AND (NOT d.restricted OR EXISTS(SELECT 1 FROM document_grants g WHERE g.document_id=d.id AND g.user_id=:u))
        AND (c.search_vector || d.title_vector) @@ q.query
        ORDER BY ts_rank_cd(c.search_vector || d.title_vector,q.query) DESC,d.title,c.ordinal LIMIT :n"""),
            {"p": project_id, "u": user_id, "terms": terms, "n": limit},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def ai_router(access: Access) -> APIRouter:
    router = APIRouter()
    DB = Annotated[Connection, Depends(access.transaction, scope="function")]
    Human = Annotated[Actor, Depends(access.actor, scope="function")]

    def service(x_orchestrator_key: Annotated[str | None, Header()] = None) -> None:
        path = os.environ.get("ORCHESTRATOR_KEY_FILE")
        if not path:
            raise HTTPException(503, "AI service is not configured")
        expected = Path(path).read_text().strip()
        if not x_orchestrator_key or not hmac.compare_digest(
            x_orchestrator_key.encode(), expected.encode()
        ):
            raise HTTPException(401, "Service authentication required")

    Service = Annotated[None, Depends(service)]

    def validate(connection: Connection, task: dict[str, Any]) -> list[dict[str, Any]]:
        user = (
            connection.execute(
                text("""SELECT u.id,u.username FROM users u JOIN sessions s ON s.user_id=u.id
            WHERE u.id=:u AND u.enabled AND u.auth_revision=:rev AND s.token_hash=:session
            AND s.expires_at>clock_timestamp() FOR SHARE OF u"""),
                {
                    "u": task["user_id"],
                    "rev": task["auth_revision"],
                    "session": task["session_hash"],
                },
            )
            .mappings()
            .first()
        )
        if not user:
            raise HTTPException(409, "Task authorization revoked")
        actor = Actor(user["id"], user["username"], False, task["session_hash"])
        project_access(connection, actor, task["project_id"])
        revision = connection.execute(
            text("SELECT acl_revision FROM projects WHERE id=:p"), {"p": task["project_id"]}
        ).scalar()
        if revision != task["acl_revision"]:
            raise HTTPException(409, "Project permissions changed; ask again")
        sources = (
            connection.execute(
                text("""SELECT c.id,c.version_id,c.content,c.start_offset,c.end_offset,
            d.id AS document_id,d.title,d.active_version_id,d.publication_revision,s.publication_revision AS expected_revision,
            d.restricted,g.user_id AS granted FROM ai_sources s JOIN text_chunks c ON c.id=s.chunk_id
            JOIN document_versions v ON v.id=c.version_id JOIN documents d ON d.id=v.document_id
            LEFT JOIN document_grants g ON g.document_id=d.id AND g.user_id=:u
            WHERE s.task_id=:task ORDER BY c.id"""),
                {"task": task["id"], "u": user["id"]},
            )
            .mappings()
            .all()
        )
        for row in sources:
            if (
                row["active_version_id"] != row["version_id"]
                or row["publication_revision"] != row["expected_revision"]
                or (row["restricted"] and not row["granted"])
            ):
                raise HTTPException(409, "Sources or permissions changed; ask again")
        return [dict(r) for r in sources]

    @router.post("/api/v1/ai/tasks", status_code=202)
    def ask(body: Question, connection: DB, human: Human) -> dict[str, str]:
        project_access(connection, human, body.project_id)
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:u,813403))"), {"u": str(human.id)}
        )
        pending = connection.execute(
            text(
                "SELECT count(*) FROM ai_tasks WHERE user_id=:u AND status IN ('PENDING','RUNNING') AND expires_at>clock_timestamp()"
            ),
            {"u": human.id},
        ).scalar_one()
        if pending >= 2:
            raise HTTPException(429, "At most two pending questions per user")
        sources = retrieve(connection, body.project_id, human.id, body.question)
        task_id = uuid4()
        connection.execute(
            text("""INSERT INTO ai_tasks(id,user_id,session_hash,auth_revision,project_id,acl_revision,question,status,answer)
            SELECT :id,:u,:session,u.auth_revision,p.id,p.acl_revision,:q,:status,:answer
            FROM users u,projects p WHERE u.id=:u AND p.id=:p"""),
            {
                "id": task_id,
                "u": human.id,
                "session": human.token_hash,
                "p": body.project_id,
                "q": body.question,
                "status": "PENDING" if sources else "COMPLETED",
                "answer": None
                if sources
                else "Insufficient evidence in the authorized published documents.",
            },
        )
        for source in sources:
            connection.execute(
                text(
                    "INSERT INTO ai_sources(task_id,chunk_id,publication_revision) VALUES (:task,:chunk,:rev)"
                ),
                {"task": task_id, "chunk": source["id"], "rev": source["publication_revision"]},
            )
        audit(connection, human.id, "ai_task_created", task_id)
        return {"id": str(task_id), "status": "PENDING" if sources else "COMPLETED"}

    @router.get("/api/v1/ai/tasks/{task_id}")
    def get_task(task_id: UUID, connection: DB, human: Human) -> dict[str, Any]:
        row = (
            connection.execute(
                text("SELECT * FROM ai_tasks WHERE id=:id AND user_id=:u"),
                {"id": task_id, "u": human.id},
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "Task not found")
        task = dict(row)
        if not connection.execute(
            text("SELECT :expiry>clock_timestamp()"), {"expiry": task["expires_at"]}
        ).scalar():
            raise HTTPException(410, "Task expired; ask again")
        sources = validate(connection, task)
        return {
            "id": str(task_id),
            "status": task["status"],
            "answer": task["answer"],
            "error_code": task["error_code"],
            "sources": [
                {
                    "id": str(s["id"]),
                    "document_id": str(s["document_id"]),
                    "title": s["title"],
                    "version_id": str(s["version_id"]),
                    "start_offset": s["start_offset"],
                    "end_offset": s["end_offset"],
                }
                for s in sources
                if s["id"] in task["citation_ids"]
            ],
        }

    @router.post("/internal/v1/ai/lease")
    def lease(connection: DB, authenticated: Service) -> dict[str, Any]:
        connection.execute(
            text(
                "UPDATE ai_tasks SET status='FAILED',error_code='TASK_EXPIRED' WHERE status IN ('PENDING','RUNNING') AND expires_at<=clock_timestamp()"
            )
        )
        row = (
            connection.execute(
                text(
                    "SELECT * FROM ai_tasks WHERE status='PENDING' AND expires_at>clock_timestamp() ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                )
            )
            .mappings()
            .first()
        )
        if not row:
            return {"task": None}
        task = dict(row)
        try:
            sources = validate(connection, task)
        except HTTPException:
            connection.execute(
                text(
                    "UPDATE ai_tasks SET status='FAILED',error_code='AUTHORIZATION_CHANGED' WHERE id=:id"
                ),
                {"id": task["id"]},
            )
            return {"task": None}
        token = secrets.token_urlsafe(32)
        connection.execute(
            text("UPDATE ai_tasks SET status='RUNNING',lease_hash=:hash WHERE id=:id"),
            {"hash": digest_token(token), "id": task["id"]},
        )
        return {
            "task": {
                "id": str(task["id"]),
                "lease_token": token,
                "question": task["question"],
                "sources": [{"id": str(s["id"]), "text": s["content"]} for s in sources],
            }
        }

    @router.post("/internal/v1/ai/tasks/{task_id}/complete", status_code=204)
    def complete(task_id: UUID, body: Result, connection: DB, authenticated: Service) -> None:
        row = (
            connection.execute(
                text("SELECT * FROM ai_tasks WHERE id=:id FOR UPDATE"), {"id": task_id}
            )
            .mappings()
            .first()
        )
        if (
            not row
            or row["status"] != "RUNNING"
            or not hmac.compare_digest(row["lease_hash"] or "", digest_token(body.lease_token))
        ):
            raise HTTPException(409, "Task delegation is not valid")
        task = dict(row)
        if not connection.execute(
            text("SELECT :expiry>clock_timestamp()"), {"expiry": task["expires_at"]}
        ).scalar():
            raise HTTPException(409, "Task delegation expired")
        sources = validate(connection, task)
        if not set(body.citation_ids).issubset({s["id"] for s in sources}):
            raise HTTPException(422, "Citation outside authorized task sources")
        if not body.error_code and (not body.answer.strip() or not body.citation_ids):
            raise HTTPException(422, "An evidence-backed answer requires valid citations")
        connection.execute(
            text(
                "UPDATE ai_tasks SET status=:status,answer=:answer,error_code=:error,citation_ids=:citations WHERE id=:id"
            ),
            {
                "id": task_id,
                "status": "FAILED" if body.error_code else "COMPLETED",
                "answer": None if body.error_code else body.answer,
                "error": body.error_code,
                "citations": body.citation_ids,
            },
        )
        audit(connection, task["user_id"], "ai_task_completed", task_id)

    return router
