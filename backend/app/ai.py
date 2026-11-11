"""Read-only AI delegation. No filesystem/SQL/shell or mutation tools are delegated."""

import hmac
import json
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
    model_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:/-]{1,64}$")
    evaluated: bool = False
    declined: bool = False  # the model found no support in the evidence it was given


class RouteResult(Input):
    lease_token: str = Field(min_length=32, max_length=128)
    documents: list[str] = Field(default=[], max_length=3)
    unsure: bool = False
    failed: bool = False  # the model could not decide: fall back to the best vector match


class CardResult(Input):
    lease_token: str = Field(min_length=32, max_length=128)
    description: str = Field(min_length=10, max_length=700)


DECLINED_ANSWER = "I looked through the relevant documents but couldn't find the answer to that."

# Passages handed to the model per question. Fewer = faster on CPU; retrieval recall@5 was 95%.
CONTEXT_CHUNKS = max(1, min(8, int(os.environ.get("AI_CONTEXT_CHUNKS", "8"))))

# Evidence characters sent to the model per question. The model reads roughly 40 characters
# per second on a CPU-only host, so this is the main latency lever.
EVIDENCE_CHARS = int(os.environ.get("AI_EVIDENCE_CHARS", "2500"))
HISTORY_TURNS = 2
HISTORY_QUESTION_CHARS = 300
HISTORY_ANSWER_CHARS = 600


def within_budget(rows: list[dict[str, Any]], chars: int | None = None) -> list[dict[str, Any]]:
    """Highest-ranked distinct passages that fit the evidence budget (always at least one)."""
    budget = chars or EVIDENCE_CHARS
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()
    used = 0
    for row in rows:
        # Identical passages (the same file uploaded twice, copied versions) add nothing.
        fingerprint = " ".join(row["content"].split())
        if fingerprint in seen:
            continue
        size = len(row["content"])
        if chosen and used + size > budget:
            break
        seen.add(fingerprint)
        chosen.append(row)
        used += size
    return chosen


def unauthorized_citations(
    connection: Connection, user_id: UUID, chunk_ids: list[UUID]
) -> bool:
    """True when a cited passage now belongs to a restricted document the user cannot read."""
    if not chunk_ids:
        return False
    return bool(
        connection.execute(
            text("""SELECT count(*) FROM text_chunks c
            JOIN document_versions v ON v.id=c.version_id
            JOIN documents d ON d.id=v.document_id
            LEFT JOIN document_grants g ON g.document_id=d.id AND g.user_id=:u
            WHERE c.id = ANY(:ids) AND d.restricted AND g.user_id IS NULL"""),
            {"u": user_id, "ids": chunk_ids},
        ).scalar_one()
    )


def create_task(
    connection: Connection,
    human: Actor,
    project_id: UUID,
    question: str,
    sources: list[dict[str, Any]],
    thread_id: UUID | None = None,
    answer: str | None = None,
    clarification: list[dict[str, str]] | None = None,
    route_candidates: list[dict[str, Any]] | None = None,
    route_effective: str | None = None,
) -> tuple[UUID, str]:
    """Record one user-bound AI task with the exact passages it may use."""
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:u,813403))"), {"u": str(human.id)}
    )
    pending = connection.execute(
        text(
            "SELECT count(*) FROM ai_tasks WHERE user_id=:u AND status IN ('ROUTING','PENDING','RUNNING') AND expires_at>clock_timestamp()"
        ),
        {"u": human.id},
    ).scalar_one()
    if pending >= 2:
        raise HTTPException(429, "At most two pending questions per user")
    task_id = uuid4()
    if route_candidates:
        status = "ROUTING"
    else:
        status = "PENDING" if sources and answer is None else "COMPLETED"
    connection.execute(
        text("""INSERT INTO ai_tasks(id,user_id,session_hash,auth_revision,project_id,acl_revision,question,status,answer,thread_id,clarification,stage,route_candidates,route_effective)
        SELECT :id,:u,:session,u.auth_revision,p.id,p.acl_revision,:q,:status,:answer,:thread,CAST(:clar AS jsonb),:stage,CAST(:cands AS jsonb),:effective
        FROM users u,projects p WHERE u.id=:u AND p.id=:p"""),
        {
            "id": task_id,
            "u": human.id,
            "session": human.token_hash,
            "p": project_id,
            "q": question,
            "status": status,
            "answer": answer
            if answer is not None
            else (None if sources else "Insufficient evidence in the authorized published documents."),
            "thread": thread_id,
            "clar": json.dumps(clarification) if clarification else None,
            "stage": "route" if route_candidates else "answer",
            "cands": json.dumps(route_candidates) if route_candidates else None,
            "effective": route_effective,
        },
    )
    for rank, source in enumerate(sources):
        connection.execute(
            text(
                "INSERT INTO ai_sources(task_id,chunk_id,publication_revision,rank) VALUES (:task,:chunk,:rev,:rank)"
            ),
            {
                "task": task_id,
                "chunk": source["id"],
                "rev": source["publication_revision"],
                "rank": rank,
            },
        )
    audit(connection, human.id, "ai_task_created", task_id)
    return task_id, status


STOPWORDS = frozenset(
    """a about after all also an and any are as at be been before but by can could did do does
    for from had has have how i if in into is it its may me more most must my no not of on or
    our shall should so some such than that the their them then there these they this to under
    up was we were what when where which who whom why will with would you your he she his her him hers""".split()
)


def query_terms(question: str, limit: int = 24) -> list[str]:
    """Distinct content words of a question; user text is never passed to tsquery syntax."""
    seen: dict[str, None] = {}
    for word in re.findall(r"[a-z0-9]+(?:[.,][0-9]+)*", question.lower()):
        if word not in STOPWORDS and (len(word) > 1 or word.isdigit()):
            seen.setdefault(word)
    return list(seen)[:limit]


# Fuzzy (trigram) matching only for words long enough that a near-match is meaningful.
FUZZY_MIN_LENGTH = 5
FUZZY_THRESHOLD = 0.55

_VISIBLE = """d.project_id=:p AND d.active_version_id=c.version_id AND (NOT d.restricted OR
    EXISTS(SELECT 1 FROM document_grants g WHERE g.document_id=d.id AND g.user_id=:u))"""
_COLUMNS = """c.id,c.content,c.version_id,c.start_offset,c.end_offset,c.page_start,c.page_end,
    d.id AS document_id,d.title,d.publication_revision"""


def retrieve(
    connection: Connection,
    project_id: UUID,
    user_id: UUID,
    question: str,
    limit: int = 8,
    document_ids: list[UUID] | None = None,
) -> list[dict[str, Any]]:
    """Authorized published passages ranked for a question; the only AI/search retrieval path.

    Leg 1: any content word, stemmed ("knows" matches "know"), over passage text and the
    document title, ranked by PostgreSQL cover-density. Leg 2 fills the remaining slots with
    typo-tolerant matches (trigram word similarity: "langues" finds "languages"). Both legs
    carry the same authorization filters inside the statement, so a passage the person may not
    read is never matched, ranked or fuzzily guessed at.
    """
    terms = query_terms(question)
    if not terms:
        return []
    scope = " AND d.id = ANY(CAST(:docs AS uuid[]))" if document_ids else ""
    visible = _VISIBLE + scope
    docs = {"docs": document_ids} if document_ids else {}
    rows = [
        dict(r)
        for r in connection.execute(
            text(f"""WITH q AS (SELECT to_tsquery('english', string_agg(
                plainto_tsquery('english',t)::text,' | ')) AS query
                FROM unnest(CAST(:terms AS text[])) AS t
                WHERE numnode(plainto_tsquery('english',t)) > 0)
            SELECT {_COLUMNS} FROM text_chunks c, q
            JOIN documents d ON d.project_id=:p
            WHERE {visible} AND q.query IS NOT NULL
            AND (c.search_en || d.title_en) @@ q.query
            ORDER BY ts_rank_cd(c.search_en || d.title_en,q.query) DESC,d.title,c.ordinal
            LIMIT :n"""),  # noqa: S608 - fixed fragments; user text only as bound parameters
            {"p": project_id, "u": user_id, "terms": terms, "n": limit, **docs},
        ).mappings()
    ]
    fuzzy_terms = [t for t in terms if len(t) >= FUZZY_MIN_LENGTH]
    if len(rows) < limit and fuzzy_terms:
        connection.execute(text(f"SET LOCAL pg_trgm.word_similarity_threshold = {FUZZY_THRESHOLD}"))
        seen = {r["id"] for r in rows}
        extra = connection.execute(
            text(f"""SELECT {_COLUMNS}, sum(word_similarity(f.term,c.content)) AS score
            FROM unnest(CAST(:fuzzy AS text[])) AS f(term)
            JOIN text_chunks c ON f.term <% c.content
            JOIN documents d ON d.active_version_id=c.version_id
            WHERE {visible}
            GROUP BY c.id,c.content,c.version_id,c.start_offset,c.end_offset,c.page_start,
                c.page_end,d.id,d.title,d.publication_revision,c.ordinal
            ORDER BY score DESC,d.title,c.ordinal LIMIT :n"""),  # noqa: S608
            {"p": project_id, "u": user_id, "fuzzy": fuzzy_terms, "n": limit, **docs},
        ).mappings()
        for row in extra:
            if row["id"] not in seen and len(rows) < limit:
                rows.append({k: v for k, v in row.items() if k != "score"})
    return rows


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
                text("""SELECT c.id,c.version_id,c.content,c.start_offset,c.end_offset,c.page_start,c.page_end,
            d.id AS document_id,d.title,d.active_version_id,d.publication_revision,s.publication_revision AS expected_revision,
            d.restricted,g.user_id AS granted FROM ai_sources s JOIN text_chunks c ON c.id=s.chunk_id
            JOIN document_versions v ON v.id=c.version_id JOIN documents d ON d.id=v.document_id
            LEFT JOIN document_grants g ON g.document_id=d.id AND g.user_id=:u
            WHERE s.task_id=:task ORDER BY s.rank,c.id"""),
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
        from app.routing import plan_answer  # late import: routing builds on this module

        project_access(connection, human, body.project_id)
        plan = plan_answer(connection, body.project_id, human.id, body.question)
        task_id, status = create_task(
            connection,
            human,
            body.project_id,
            body.question,
            plan.sources,
            answer=plan.answer,
            clarification=plan.clarification,
            route_candidates=plan.candidates,
            route_effective=plan.effective,
        )
        return {"id": str(task_id), "status": "PENDING" if status == "ROUTING" else status}

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
            "status": "PENDING" if task["status"] == "ROUTING" else task["status"],
            "answer": task["answer"],
            "error_code": task["error_code"],
            "model": {"id": task["model_id"], "evaluated": bool(task["model_evaluated"])}
            if task["model_id"]
            else None,
            "sources": [
                {
                    "id": str(s["id"]),
                    "document_id": str(s["document_id"]),
                    "title": s["title"],
                    "version_id": str(s["version_id"]),
                    "start_offset": s["start_offset"],
                    "end_offset": s["end_offset"],
                    "page_start": s["page_start"],
                    "page_end": s["page_end"],
                }
                for s in sources
                if s["id"] in task["citation_ids"]
            ],
        }

    def thread_history(connection: Connection, task: dict[str, Any]) -> list[dict[str, str]]:
        """Recent finished turns of this chat that the user may still read, oldest first."""
        if not task["thread_id"]:
            return []
        from app.routing import refers_back  # late import: routing builds on this module

        # A complete question carries its own subject and the chat already knows which document
        # it is about. Replaying the previous answer only made the model repeat it, so history is
        # sent just for questions with almost nothing to go on ("what about it?").
        if refers_back(task["question"]) != "bare":
            return []
        rows = (
            connection.execute(
                text("""SELECT t.question,k.answer,k.citation_ids FROM chat_turns t
            JOIN ai_tasks k ON k.id=t.task_id
            WHERE t.thread_id=:th AND k.status='COMPLETED' AND k.answer IS NOT NULL
            AND t.seq < (SELECT seq FROM chat_turns WHERE task_id=:task)
            ORDER BY t.seq DESC LIMIT :n"""),
                {"th": task["thread_id"], "task": task["id"], "n": HISTORY_TURNS},
            )
            .mappings()
            .all()
        )
        history = []
        for row in reversed(rows):
            # Never replay an answer that cites something the user can no longer read.
            if unauthorized_citations(connection, task["user_id"], list(row["citation_ids"])):
                continue
            history.append(
                {
                    "question": row["question"][:HISTORY_QUESTION_CHARS],
                    "answer": row["answer"][:HISTORY_ANSWER_CHARS],
                }
            )
        return history

    @router.post("/internal/v1/ai/lease")
    def lease(connection: DB, authenticated: Service) -> dict[str, Any]:
        connection.execute(
            text(
                "UPDATE ai_tasks SET status='FAILED',error_code='TASK_EXPIRED' WHERE status IN ('ROUTING','PENDING','RUNNING') AND expires_at<=clock_timestamp()"
            )
        )
        row = (
            connection.execute(
                text(
                    """SELECT * FROM ai_tasks WHERE status IN ('ROUTING','PENDING') AND expires_at>clock_timestamp()
                    ORDER BY (status='ROUTING') DESC,created_at LIMIT 1 FOR UPDATE SKIP LOCKED"""
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
        if task["stage"] == "route":
            focus_titles: list[str] = []
            if task["thread_id"]:
                focus_titles = list(
                    connection.execute(
                        text("""SELECT d.title FROM chat_threads t JOIN documents d
                        ON d.id = ANY(t.focus_document_ids) WHERE t.id=:t"""),
                        {"t": task["thread_id"]},
                    ).scalars()
                )
            return {
                "task": {
                    "stage": "route",
                    "id": str(task["id"]),
                    "lease_token": token,
                    "question": task["question"],
                    "history": thread_history(connection, task),
                    "focus": focus_titles,
                    "candidates": [
                        {"label": f"D{n}", "title": c["title"], "description": c["description"]}
                        for n, c in enumerate(task["route_candidates"], 1)
                    ],
                }
            }
        return {
            "task": {
                "stage": "answer",
                "id": str(task["id"]),
                "lease_token": token,
                "question": task["question"],
                "history": thread_history(connection, task),
                "sources": [
                    {"id": str(s["id"]), "text": s["content"], "document": s["title"]}
                    for s in sources
                ],
            }
        }

    @router.post("/internal/v1/ai/tasks/{task_id}/route", status_code=204)
    def route(task_id: UUID, body: RouteResult, connection: DB, authenticated: Service) -> None:
        """The model's choice among the candidates stored on the task (and only those)."""
        from app.routing import NOT_FOUND, clarify_text, sources_in_documents

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
            or row["stage"] != "route"
            or not hmac.compare_digest(row["lease_hash"] or "", digest_token(body.lease_token))
        ):
            raise HTTPException(409, "Task delegation is not valid")
        task = dict(row)
        if not connection.execute(
            text("SELECT :expiry>clock_timestamp()"), {"expiry": task["expires_at"]}
        ).scalar():
            raise HTTPException(409, "Task delegation expired")
        validate(connection, task)
        candidates = task["route_candidates"]
        labels = {f"D{n}": c for n, c in enumerate(candidates, 1)}
        if any(label not in labels for label in body.documents):
            raise HTTPException(422, "Choice outside the allowed documents")
        chosen = list(dict.fromkeys(body.documents))
        if body.failed:
            chosen, body.unsure = ["D1"], False  # no model decision: best vector match
        def done(answer: str, options: list[dict[str, str]] | None = None) -> None:
            connection.execute(
                text(
                    "UPDATE ai_tasks SET status='COMPLETED',answer=:a,clarification=CAST(:c AS jsonb),lease_hash=NULL WHERE id=:id"
                ),
                {"a": answer, "c": json.dumps(options) if options else None, "id": task_id},
            )

        if body.unsure:
            shown = [labels[label] for label in chosen] if len(chosen) > 1 else candidates[:3]
            options = [{"document_id": c["document_id"], "title": c["title"]} for c in shown]
            done(clarify_text(shown), options)
            audit(connection, task["user_id"], "ai_route_unsure", task_id)
            return
        if not chosen:
            done(NOT_FOUND)
            audit(connection, task["user_id"], "ai_route_none", task_id)
            return
        ids = [UUID(labels[label]["document_id"]) for label in chosen]
        qvec = None
        sources = sources_in_documents(
            connection, task["project_id"], task["user_id"], task["route_effective"] or task["question"], ids, qvec
        )
        if task["thread_id"]:
            connection.execute(
                text("UPDATE chat_threads SET focus_document_ids=CAST(:f AS uuid[]) WHERE id=:t"),
                {"f": ids, "t": task["thread_id"]},
            )
        if not sources:
            done("Insufficient evidence in the authorized published documents.")
            return
        for rank, source in enumerate(sources):
            connection.execute(
                text(
                    "INSERT INTO ai_sources(task_id,chunk_id,publication_revision,rank) VALUES (:task,:chunk,:rev,:rank)"
                ),
                {"task": task_id, "chunk": source["id"], "rev": source["publication_revision"], "rank": rank},
            )
        connection.execute(
            text(
                """UPDATE ai_tasks SET status='PENDING',stage='answer',lease_hash=NULL,
                expires_at=greatest(expires_at,clock_timestamp()+interval '4 minutes') WHERE id=:id"""
            ),
            {"id": task_id},
        )
        audit(connection, task["user_id"], "ai_route_chosen", task_id)

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
            or row["stage"] != "answer"
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
        if body.declined:
            if body.citation_ids or body.error_code:
                raise HTTPException(422, "A decline carries no citations")
            body.answer = DECLINED_ANSWER
        elif not body.error_code and (not body.answer.strip() or not body.citation_ids):
            raise HTTPException(422, "An evidence-backed answer requires valid citations")
        connection.execute(
            text(
                "UPDATE ai_tasks SET status=:status,answer=:answer,error_code=:error,citation_ids=:citations,model_id=:model,model_evaluated=:evaluated WHERE id=:id"
            ),
            {
                "id": task_id,
                "status": "FAILED" if body.error_code else "COMPLETED",
                "answer": None if body.error_code else body.answer,
                "error": body.error_code,
                "citations": body.citation_ids,
                "model": body.model_id,
                "evaluated": body.evaluated if body.model_id else None,
            },
        )
        audit(connection, task["user_id"], "ai_task_completed", task_id)

    @router.post("/internal/v1/ai/card-lease")
    def card_lease(connection: DB, authenticated: Service) -> dict[str, Any]:
        """A document the model has not yet described (only asked for when it is otherwise idle)."""
        row = (
            connection.execute(
                text("""SELECT k.version_id,d.title FROM document_cards k
                JOIN documents d ON d.active_version_id=k.version_id
                WHERE k.description IS NULL AND k.attempts < 3
                AND (k.lease_hash IS NULL OR k.lease_expires_at < clock_timestamp())
                ORDER BY k.created_at LIMIT 1 FOR UPDATE OF k SKIP LOCKED""")
            )
            .mappings()
            .first()
        )
        if row is None:
            return {"card": None}
        chunks = connection.execute(
            text("SELECT content FROM text_chunks WHERE version_id=:v ORDER BY ordinal LIMIT 3"),
            {"v": row["version_id"]},
        ).scalars().all()
        token = secrets.token_urlsafe(32)
        connection.execute(
            text("""UPDATE document_cards SET lease_hash=:h,attempts=attempts+1,
            lease_expires_at=clock_timestamp()+interval '10 minutes' WHERE version_id=:v"""),
            {"h": digest_token(token), "v": row["version_id"]},
        )
        return {
            "card": {
                "id": str(row["version_id"]),
                "lease_token": token,
                "title": row["title"],
                "text": "\n".join(chunks)[:2500],
            }
        }

    @router.post("/internal/v1/ai/card/{version_id}/complete", status_code=204)
    def card_complete(
        version_id: UUID, body: CardResult, connection: DB, authenticated: Service
    ) -> None:
        row = (
            connection.execute(
                text("""SELECT k.*,d.title FROM document_cards k JOIN documents d
                ON d.active_version_id=k.version_id WHERE k.version_id=:v FOR UPDATE OF k"""),
                {"v": version_id},
            )
            .mappings()
            .first()
        )
        if (
            row is None
            or row["description"] is not None
            or not hmac.compare_digest(row["lease_hash"] or "", digest_token(body.lease_token))
        ):
            raise HTTPException(409, "Card lease is not valid")
        valid = connection.execute(
            text("SELECT :expiry > clock_timestamp()"), {"expiry": row["lease_expires_at"]}
        ).scalar()
        if not valid:
            raise HTTPException(409, "Card lease expired")
        description = " ".join(body.description.split())
        sections = row["card_text"].partition("\nSections:")[2]
        card_text = f"{row['title']}. {description}\nSections:{sections}"
        # embedding=NULL: the background worker re-embeds the richer card.
        connection.execute(
            text("""UPDATE document_cards SET description=:d,card_text=:c,embedding=NULL,
            lease_hash=NULL WHERE version_id=:v"""),
            {"d": description, "c": card_text, "v": version_id},
        )

    return router
