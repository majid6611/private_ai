import os
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import text

from tests.support import Workspace
from tests.test_approvals import setup
from tests.test_ingestion import index_one


def service() -> dict[str, str]:
    return {"X-Orchestrator-Key": Path(os.environ["ORCHESTRATOR_KEY_FILE"]).read_text().strip()}


def prepare(w: Workspace) -> tuple[str, str]:
    with w.engine.begin() as connection:
        connection.execute(text("UPDATE intake_jobs SET status='DONE'"))
        connection.execute(
            text("UPDATE ai_tasks SET status='FAILED' WHERE status IN ('PENDING','RUNNING')")
        )
    p, d, v = setup(w)
    job = index_one(w, d, v)
    from tests.test_ingestion import headers

    result = w.client.post(
        f"/internal/v1/ingestion/{job['id']}/complete",
        headers=headers(),
        json={
            "lease_token": job["lease_token"],
            "chunks": [{"start": 0, "end": len(str(job["text"]))}],
        },
    )
    assert result.status_code == 204
    return p, d


def ask(w: Workspace, p: str, question: str = "contract") -> str:
    response = w.client.post(
        "/api/v1/ai/tasks", headers=w.user_headers, json={"project_id": p, "question": question}
    )
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


def test_scoped_context_citations_and_task_owner(workspace: Workspace) -> None:
    w = workspace
    p, _ = prepare(w)
    task_id = ask(w, p)
    assert w.client.post("/internal/v1/ai/lease", headers=w.user_headers).status_code == 401
    assert w.client.get(f"/api/v1/ai/tasks/{task_id}", headers=w.admin_headers).status_code == 404
    task = w.client.post("/internal/v1/ai/lease", headers=service()).json()["task"]
    assert task["id"] == task_id
    assert task["sources"][0]["text"] == "Original contract"
    assert "session_hash" not in task and "user_id" not in task
    assert (
        w.client.post(
            "/api/v1/projects",
            headers={"Authorization": "Bearer " + task["lease_token"]},
            json={"name": "forged"},
        ).status_code
        == 401
    )
    result = {
        "lease_token": task["lease_token"],
        "answer": "The contract is available.",
        "citation_ids": [str(uuid4())],
    }
    path = f"/internal/v1/ai/tasks/{task_id}/complete"
    assert w.client.post(path, headers=service(), json=result).status_code == 422
    result["citation_ids"] = [task["sources"][0]["id"]]
    assert w.client.post(path, headers=service(), json=result).status_code == 204
    assert w.client.post(path, headers=service(), json=result).status_code == 409
    answer = w.client.get(f"/api/v1/ai/tasks/{task_id}", headers=w.user_headers).json()
    assert answer["status"] == "COMPLETED"
    assert answer["sources"][0]["id"] == task["sources"][0]["id"]


def test_revocation_after_context_denies_completion_and_history(workspace: Workspace) -> None:
    w = workspace
    p, d = prepare(w)
    task_id = ask(w, p)
    task = w.client.post("/internal/v1/ai/lease", headers=service()).json()["task"]
    assert (
        w.client.patch(
            f"/api/v1/documents/{d}/access", headers=w.user_headers, json={"restricted": True}
        ).status_code
        == 204
    )
    response = w.client.post(
        f"/internal/v1/ai/tasks/{task_id}/complete",
        headers=service(),
        json={
            "lease_token": task["lease_token"],
            "answer": "Should not be delivered.",
            "citation_ids": [task["sources"][0]["id"]],
        },
    )
    assert response.status_code == 409
    assert w.client.get(f"/api/v1/ai/tasks/{task_id}", headers=w.user_headers).status_code == 409
    with w.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT answer FROM ai_tasks WHERE id=:id"), {"id": UUID(task_id)}
            ).scalar()
            is None
        )


def test_no_evidence_model_failure_expiry_and_queue_bound(workspace: Workspace) -> None:
    w = workspace
    p, _ = prepare(w)
    empty = ask(w, p, "nonexistentwordxyz")
    result = w.client.get(f"/api/v1/ai/tasks/{empty}", headers=w.user_headers).json()
    assert result["status"] == "COMPLETED" and result["sources"] == []
    assert "Insufficient evidence" in result["answer"]
    first = ask(w, p)
    second = ask(w, p)
    assert (
        w.client.post(
            "/api/v1/ai/tasks",
            headers=w.user_headers,
            json={"project_id": p, "question": "contract"},
        ).status_code
        == 429
    )
    task = w.client.post("/internal/v1/ai/lease", headers=service()).json()["task"]
    assert task["id"] == first
    assert (
        w.client.post(
            f"/internal/v1/ai/tasks/{first}/complete",
            headers=service(),
            json={
                "lease_token": task["lease_token"],
                "answer": "",
                "citation_ids": [],
                "error_code": "MODEL_UNAVAILABLE",
            },
        ).status_code
        == 204
    )
    answer = w.client.get(f"/api/v1/ai/tasks/{first}", headers=w.user_headers).json()
    assert answer["status"] == "FAILED" and answer["answer"] is None
    with w.engine.begin() as connection:
        connection.execute(
            text("UPDATE ai_tasks SET expires_at=now()-interval '1 second' WHERE id=:id"),
            {"id": UUID(second)},
        )
    assert w.client.get(f"/api/v1/ai/tasks/{second}", headers=w.user_headers).status_code == 410


def test_logout_revokes_task_before_model_context(workspace: Workspace) -> None:
    w = workspace
    p, _ = prepare(w)
    task_id = ask(w, p)
    assert w.client.post("/api/v1/auth/logout", headers=w.user_headers).status_code == 204
    assert w.client.post("/internal/v1/ai/lease", headers=service()).json()["task"] is None
    with w.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM ai_tasks WHERE id=:id"), {"id": UUID(task_id)}
            ).scalar()
            == "FAILED"
        )
