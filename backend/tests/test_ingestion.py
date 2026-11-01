import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from tests.support import Workspace
from tests.test_approvals import approve, publish, request, setup


def headers() -> dict[str, str]:
    return {"X-Ingestion-Key": Path(os.environ["INGESTION_KEY_FILE"]).read_text().strip()}


def index_one(w: Workspace, d: str, v: str) -> dict[str, object]:
    a = request(w, d, v)
    approve(w, a)
    assert publish(w, d, a) == 200
    response = w.client.post("/internal/v1/ingestion/lease", headers=headers())
    assert response.status_code == 200, response.text
    job: dict[str, object] = response.json()["job"]
    return job


def test_ingestion_identity_chunks_and_revocation_filtered_search(workspace: Workspace) -> None:
    w = workspace
    # This isolated test DB has no running worker; discard prior tests' pending jobs.
    with w.engine.begin() as connection:
        connection.execute(text("UPDATE intake_jobs SET status='DONE'"))
    p, d, v = setup(w)
    assert w.client.post("/internal/v1/ingestion/lease", headers=w.user_headers).status_code == 401
    # An unpublished draft never enters the ingestion queue.
    assert w.client.post("/internal/v1/ingestion/lease", headers=headers()).json()["job"] is None
    job = index_one(w, d, v)
    content = str(job["text"])
    result = {"lease_token": job["lease_token"], "chunks": [{"start": 0, "end": len(content)}]}
    path = f"/internal/v1/ingestion/{job['id']}/complete"
    assert w.client.post(path, headers=headers(), json=result).status_code == 204
    assert w.client.post(path, headers=headers(), json=result).status_code == 204
    query = {"project_id": p, "q": "contract"}
    hits = w.client.get("/api/v1/search", headers=w.admin_headers, params=query)
    assert hits.status_code == 200
    assert hits.json()[0]["document_id"] == d
    assert hits.json()[0]["version_id"] == v
    assert hits.json()[0]["snippet"] == "Original contract"
    reviewer = w.client.get("/api/v1/auth/me", headers=w.admin_headers).json()["id"]
    assert (
        w.client.patch(
            f"/api/v1/documents/{d}/access", headers=w.user_headers, json={"restricted": True}
        ).status_code
        == 204
    )
    assert w.client.get("/api/v1/search", headers=w.admin_headers, params=query).json() == []
    assert (
        w.client.put(
            f"/api/v1/documents/{d}/access/{reviewer}", headers=w.user_headers, json={}
        ).status_code
        == 204
    )
    assert w.client.get("/api/v1/search", headers=w.admin_headers, params=query).json()
    assert (
        w.client.delete(
            f"/api/v1/documents/{d}/access/{reviewer}", headers=w.user_headers
        ).status_code
        == 204
    )
    assert w.client.get("/api/v1/search", headers=w.admin_headers, params=query).json() == []


def test_stale_lease_and_invalid_ranges_never_write_index(workspace: Workspace) -> None:
    w = workspace
    # This isolated test DB has no running worker; discard prior tests' pending jobs.
    with w.engine.begin() as connection:
        connection.execute(text("UPDATE intake_jobs SET status='DONE'"))
    _, d, v = setup(w)
    job = index_one(w, d, v)
    path = f"/internal/v1/ingestion/{job['id']}/complete"
    bad = {"lease_token": job["lease_token"], "chunks": [{"start": 1, "end": 3}]}
    assert w.client.post(path, headers=headers(), json=bad).status_code == 422
    with w.engine.begin() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM text_chunks WHERE version_id=:v"), {"v": UUID(v)}
            ).scalar()
            == 0
        )
        connection.execute(
            text("UPDATE intake_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=:j"),
            {"j": UUID(str(job["id"]))},
        )
    correct = {
        "lease_token": job["lease_token"],
        "chunks": [{"start": 0, "end": len(str(job["text"]))}],
    }
    assert w.client.post(path, headers=headers(), json=correct).status_code == 409
    new = w.client.post("/internal/v1/ingestion/lease", headers=headers()).json()["job"]
    assert new["id"] == job["id"] and new["lease_token"] != job["lease_token"]
    assert w.client.post(path, headers=headers(), json=correct).status_code == 409
    correct["lease_token"] = new["lease_token"]
    assert w.client.post(path, headers=headers(), json=correct).status_code == 204
