"""Private chat threads over the AI task pipeline.

A turn is a user question plus the AI task that answers it. Every turn is retrieved,
authorized and validated exactly like a one-off question; the thread only adds memory.
History is private to its owner, rechecked on every read, and redacted when a cited
document is no longer readable.
"""

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import Connection, text

from app.access import Access, Actor, audit
from app.ai import create_task, unauthorized_citations
from app.documents import project_access
from app.identity import Input
from app.routing import plan_answer, visible_documents

MAX_TURNS = 200
class NewMessage(Input):
    content: str = Field(min_length=2, max_length=1000, pattern=r".*\S.*")
    # Set when the user picked one of the assistant's "which document?" options.
    document_ids: list[Annotated[UUID, Field(strict=False)]] | None = Field(default=None, max_length=5)


class Rename(Input):
    title: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")


def chat_router(access: Access) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    DB = Annotated[Connection, Depends(access.transaction, scope="function")]
    Human = Annotated[Actor, Depends(access.actor, scope="function")]

    def member(connection: Connection, human: Actor, project_id: UUID) -> None:
        try:
            project_access(connection, human, project_id)
        except HTTPException:
            raise HTTPException(404, "Chat not found") from None

    def own_thread(connection: Connection, human: Actor, thread_id: UUID) -> dict[str, Any]:
        """The caller's thread, only while they are still a member of its project."""
        row = (
            connection.execute(
                text("SELECT * FROM chat_threads WHERE id=:t AND user_id=:u FOR UPDATE"),
                {"t": thread_id, "u": human.id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(404, "Chat not found")
        member(connection, human, row["project_id"])
        return dict(row)

    @router.post("/projects/{project_id}/chat/threads", status_code=201)
    def create_thread(project_id: UUID, connection: DB, human: Human) -> dict[str, str]:
        member(connection, human, project_id)
        count = connection.execute(
            text("SELECT count(*) FROM chat_threads WHERE user_id=:u AND project_id=:p"),
            {"u": human.id, "p": project_id},
        ).scalar_one()
        if count >= 200:
            raise HTTPException(429, "Too many chats in this project; delete some first")
        thread_id = uuid4()
        connection.execute(
            text("INSERT INTO chat_threads(id,user_id,project_id) VALUES (:id,:u,:p)"),
            {"id": thread_id, "u": human.id, "p": project_id},
        )
        audit(connection, human.id, "chat_thread_created", thread_id)
        return {"id": str(thread_id)}

    @router.get("/projects/{project_id}/chat/threads")
    def list_threads(project_id: UUID, connection: DB, human: Human) -> list[dict[str, str]]:
        member(connection, human, project_id)
        rows = connection.execute(
            text("""SELECT id,title,updated_at FROM chat_threads
            WHERE user_id=:u AND project_id=:p ORDER BY updated_at DESC LIMIT 100"""),
            {"u": human.id, "p": project_id},
        ).mappings()
        return [
            {"id": str(r["id"]), "title": r["title"], "updated_at": r["updated_at"].isoformat()}
            for r in rows
        ]

    @router.get("/chat/threads/{thread_id}")
    def read_thread(thread_id: UUID, connection: DB, human: Human) -> dict[str, Any]:
        thread = own_thread(connection, human, thread_id)
        rows = (
            connection.execute(
                text("""SELECT t.id,t.seq,t.question,t.created_at,k.status,k.answer,k.error_code,
            k.citation_ids,k.model_id,k.model_evaluated,k.clarification,
            (k.expires_at<=clock_timestamp()) AS expired
            FROM chat_turns t JOIN ai_tasks k ON k.id=t.task_id
            WHERE t.thread_id=:t ORDER BY t.seq"""),
                {"t": thread_id},
            )
            .mappings()
            .all()
        )
        turns = []
        for r in rows:
            status, error, answer = r["status"], r["error_code"], r["answer"]
            if status in ("ROUTING", "PENDING", "RUNNING") and r["expired"]:
                status, error = "FAILED", "TASK_EXPIRED"
            if status == "ROUTING":
                status = "PENDING"
            cited = list(r["citation_ids"] or [])
            redacted = status == "COMPLETED" and unauthorized_citations(connection, human.id, cited)
            sources: list[dict[str, Any]] = []
            if redacted:
                answer = None
            elif status == "COMPLETED" and cited:
                for s in (
                    connection.execute(
                        text("""SELECT c.id,c.version_id,c.start_offset,c.end_offset,c.page_start,
                    c.page_end,d.id AS document_id,d.title FROM text_chunks c
                    JOIN document_versions v ON v.id=c.version_id
                    JOIN documents d ON d.id=v.document_id WHERE c.id = ANY(:ids) ORDER BY c.id"""),
                        {"ids": cited},
                    )
                    .mappings()
                    .all()
                ):
                    sources.append(
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
                    )
            turns.append(
                {
                    "id": str(r["id"]),
                    "seq": r["seq"],
                    "question": r["question"],
                    "created_at": r["created_at"].isoformat(),
                    "status": status,
                    "answer": answer,
                    "redacted": redacted,
                    "error_code": error,
                    "clarify": [
                        o
                        for o in (r["clarification"] or [])
                        if visible_documents(connection, thread["project_id"], human.id, [UUID(o["document_id"])])
                    ],
                    "model": {"id": r["model_id"], "evaluated": bool(r["model_evaluated"])}
                    if r["model_id"] and status == "COMPLETED"
                    else None,
                    "sources": sources,
                }
            )
        focus = (
            visible_documents(connection, thread["project_id"], human.id, list(thread["focus_document_ids"]))
            if thread["focus_document_ids"]
            else []
        )
        return {
            "id": str(thread_id),
            "title": thread["title"],
            "focus": [{"id": str(d["id"]), "title": d["title"]} for d in focus],
            "turns": turns,
        }

    @router.post("/chat/threads/{thread_id}/messages", status_code=202)
    def send(thread_id: UUID, body: NewMessage, connection: DB, human: Human) -> dict[str, str]:
        thread = own_thread(connection, human, thread_id)
        last = (
            connection.execute(
                text("""SELECT t.seq,t.question,k.status,(k.expires_at>clock_timestamp()) AS live
            FROM chat_turns t JOIN ai_tasks k ON k.id=t.task_id
            WHERE t.thread_id=:t ORDER BY t.seq DESC LIMIT 1"""),
                {"t": thread_id},
            )
            .mappings()
            .first()
        )
        if last and last["status"] in ("ROUTING", "PENDING", "RUNNING") and last["live"]:
            raise HTTPException(409, "Wait for the current answer first")
        if last and last["seq"] >= MAX_TURNS:
            raise HTTPException(429, "This chat is full; start a new one")
        previous = last["question"] if last else None
        explicit = list(dict.fromkeys(body.document_ids)) if body.document_ids else None
        if explicit and len(visible_documents(connection, thread["project_id"], human.id, explicit)) != len(explicit):
            raise HTTPException(404, "Document not found")
        plan = plan_answer(
            connection,
            thread["project_id"],
            human.id,
            body.content,
            previous,
            list(thread["focus_document_ids"] or []),
            explicit,
        )
        task_id, status = create_task(
            connection,
            human,
            thread["project_id"],
            body.content,
            plan.sources,
            thread_id,
            answer=plan.answer,
            clarification=plan.clarification,
            route_candidates=plan.candidates,
            route_effective=plan.effective,
        )
        if plan.focus is not None:
            connection.execute(
                text("UPDATE chat_threads SET focus_document_ids=CAST(:f AS uuid[]) WHERE id=:t"),
                {"f": plan.focus, "t": thread_id},
            )
        turn_id = uuid4()
        connection.execute(
            text("""INSERT INTO chat_turns(id,thread_id,seq,question,task_id)
            VALUES (:id,:t,:seq,:q,:task)"""),
            {
                "id": turn_id,
                "t": thread_id,
                "seq": (last["seq"] if last else 0) + 1,
                "q": body.content,
                "task": task_id,
            },
        )
        connection.execute(
            text("""UPDATE chat_threads SET updated_at=now(),
            title=CASE WHEN :first THEN left(:q,100) ELSE title END WHERE id=:t"""),
            {"t": thread_id, "q": body.content, "first": last is None},
        )
        return {
            "turn_id": str(turn_id),
            "task_id": str(task_id),
            "status": "PENDING" if status == "ROUTING" else status,
        }

    @router.delete("/chat/threads/{thread_id}/focus", status_code=204)
    def clear_focus(thread_id: UUID, connection: DB, human: Human) -> None:
        own_thread(connection, human, thread_id)
        connection.execute(
            text("UPDATE chat_threads SET focus_document_ids='{}' WHERE id=:t"), {"t": thread_id}
        )

    @router.patch("/chat/threads/{thread_id}")
    def rename(thread_id: UUID, body: Rename, connection: DB, human: Human) -> dict[str, str]:
        own_thread(connection, human, thread_id)
        connection.execute(
            text("UPDATE chat_threads SET title=:title WHERE id=:t"),
            {"title": body.title, "t": thread_id},
        )
        return {"id": str(thread_id), "title": body.title}

    @router.delete("/chat/threads/{thread_id}", status_code=204)
    def delete_thread(thread_id: UUID, connection: DB, human: Human) -> None:
        own_thread(connection, human, thread_id)
        params = {"t": thread_id}
        connection.execute(
            text("DELETE FROM ai_sources WHERE task_id IN (SELECT id FROM ai_tasks WHERE thread_id=:t)"),
            params,
        )
        connection.execute(text("DELETE FROM chat_turns WHERE thread_id=:t"), params)
        connection.execute(text("DELETE FROM ai_tasks WHERE thread_id=:t"), params)
        connection.execute(text("DELETE FROM chat_threads WHERE id=:t"), params)
        audit(connection, human.id, "chat_thread_deleted", thread_id)

    return router
