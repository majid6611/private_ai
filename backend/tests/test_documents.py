from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import app.documents as documents
from app.main import create_app
from app.storage import BlobStore
from tests.support import Workspace


def project(w: Workspace) -> str:
    response = w.client.post(
        "/api/v1/projects", headers=w.admin_headers, json={"name": "Document lab"}
    )
    assert response.status_code == 201
    project_id = str(response.json()["id"])
    response = w.client.put(
        f"/api/v1/projects/{project_id}/members/{w.user_id}",
        headers=w.admin_headers,
        json={"can_edit": True, "can_history": True},
    )
    assert response.status_code == 204
    return project_id


def upload(
    w: Workspace,
    p: str,
    content: bytes = b"Original contract",
    key: str = "request-001",
    restricted: bool = False,
) -> dict[str, str]:
    response = w.client.post(
        "/api/v1/documents",
        params={"project_id": p, "title": "../contract.txt", "restricted": restricted},
        headers={**w.user_headers, "Content-Type": "text/plain", "Idempotency-Key": key},
        content=content,
    )
    assert response.status_code == 201, response.text
    result: dict[str, str] = response.json()
    return result


def test_immutable_versions_history_and_safe_download(workspace: Workspace) -> None:
    w = workspace
    p = project(w)
    first = upload(w, p)
    d, v = first["document_id"], first["version_id"]
    download = w.client.get(f"/api/v1/documents/{d}/content", headers=w.user_headers)
    assert download.content == b"Original contract"
    assert download.headers["content-disposition"] == 'attachment; filename="document.txt"'
    assert download.headers["cache-control"] == "no-store"
    appended = w.client.post(
        f"/api/v1/documents/{d}/versions",
        params={"parent_version_id": v},
        content=b"Revised contract",
        headers={**w.user_headers, "Content-Type": "text/plain", "Idempotency-Key": "append-001"},
    )
    assert appended.status_code == 201, appended.text
    assert (
        w.client.get(
            f"/api/v1/documents/{d}/content", params={"version_id": v}, headers=w.user_headers
        ).content
        == b"Original contract"
    )
    history = w.client.get(f"/api/v1/documents/{d}/versions", headers=w.user_headers)
    assert len(history.json()) == 2
    assert all(item["state"] == "DRAFT" for item in history.json())
    assert (
        w.client.get(f"/api/v1/documents/{d}", headers=w.user_headers).json()["active_version_id"]
        is None
    )
    stale = w.client.post(
        f"/api/v1/documents/{d}/versions",
        params={"parent_version_id": v},
        content=b"Stale edit",
        headers={**w.user_headers, "Content-Type": "text/plain", "Idempotency-Key": "append-stale"},
    )
    assert stale.status_code == 409
    with w.engine.connect() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                text("UPDATE document_versions SET sha256=:h WHERE id=:v"),
                {"h": "0" * 64, "v": UUID(v)},
            )
        connection.rollback()
        assert (
            connection.execute(
                text("SELECT count(*) FROM intake_jobs WHERE version_id=:v"), {"v": UUID(v)}
            ).scalar()
            == 1
        )


def test_idempotency_and_quota_do_not_duplicate_data(workspace: Workspace) -> None:
    w = workspace
    p = project(w)
    first = upload(w, p)
    assert upload(w, p) == first
    response = w.client.post(
        "/api/v1/documents",
        params={"project_id": p, "title": "../contract.txt"},
        content=b"different",
        headers={**w.user_headers, "Content-Type": "text/plain", "Idempotency-Key": "request-001"},
    )
    assert response.status_code == 409
    with w.engine.begin() as connection:
        used = connection.execute(
            text("SELECT storage_bytes FROM projects WHERE id=:p"), {"p": UUID(p)}
        ).scalar_one()
        assert used == len(b"Original contract")
        connection.execute(
            text("UPDATE projects SET quota_bytes=:q WHERE id=:p"), {"q": used, "p": UUID(p)}
        )
    response = w.client.post(
        "/api/v1/documents",
        params={"project_id": p, "title": "Too big"},
        content=b"x",
        headers={**w.user_headers, "Content-Type": "text/plain", "Idempotency-Key": "quota-001"},
    )
    assert response.status_code == 413


def test_document_restrictions_intersect_project_membership(workspace: Workspace) -> None:
    w = workspace
    p = project(w)
    d = upload(w, p, restricted=True)["document_id"]
    admin_id = w.client.get("/api/v1/auth/me", headers=w.admin_headers).json()["id"]
    w.client.put(
        f"/api/v1/projects/{p}/members/{admin_id}",
        headers=w.admin_headers,
        json={"can_edit": True, "can_history": True},
    )
    assert w.client.get(f"/api/v1/documents/{d}", headers=w.admin_headers).status_code == 404
    assert (
        w.client.get("/api/v1/documents", params={"project_id": p}, headers=w.admin_headers).json()
        == []
    )
    grant = f"/api/v1/documents/{d}/access/{admin_id}"
    assert w.client.put(grant, headers=w.user_headers, json={}).status_code == 204
    assert w.client.get(f"/api/v1/documents/{d}", headers=w.admin_headers).status_code == 200
    assert (
        w.client.get(f"/api/v1/documents/{d}/content", headers=w.admin_headers).status_code == 404
    )
    assert w.client.put(grant, headers=w.user_headers, json={"can_edit": True}).status_code == 204
    assert (
        w.client.get(f"/api/v1/documents/{d}/content", headers=w.admin_headers).status_code == 200
    )
    assert w.client.delete(grant, headers=w.user_headers).status_code == 204
    assert w.client.get(f"/api/v1/documents/{d}", headers=w.admin_headers).status_code == 404
    assert w.client.put(grant, headers=w.user_headers, json={"can_edit": True}).status_code == 204
    assert (
        w.client.delete(
            f"/api/v1/projects/{p}/members/{admin_id}", headers=w.admin_headers
        ).status_code
        == 204
    )
    assert w.client.get(f"/api/v1/documents/{d}", headers=w.admin_headers).status_code == 404
    assert (
        w.client.get(f"/api/v1/documents/{d}/content", headers=w.admin_headers).status_code == 404
    )
    # A grant alone never restores revoked project membership.
    assert w.client.put(grant, headers=w.user_headers, json={}).status_code == 404


def test_storage_failure_rolls_back_metadata_and_quota(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = workspace
    p = project(w)

    def fail(self: BlobStore) -> None:
        raise OSError("simulated fsync failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(BlobStore, "sync_directory", fail)
        response = w.client.post(
            "/api/v1/documents",
            params={"project_id": p, "title": "Failure"},
            content=b"Hello",
            headers={
                **w.user_headers,
                "Content-Type": "text/plain",
                "Idempotency-Key": "failure-001",
            },
        )
        assert response.status_code == 503
    with w.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT storage_bytes FROM projects WHERE id=:p"), {"p": UUID(p)}
            ).scalar()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM documents WHERE project_id=:p"), {"p": UUID(p)}
            ).scalar()
            == 0
        )
    assert upload(w, p, key="failure-001")


def test_database_failure_after_blob_write_is_retriable(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = workspace
    p = project(w)

    def fail(*args: object) -> None:
        raise RuntimeError("simulated pre-commit failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(documents, "audit", fail)
        with pytest.raises(RuntimeError, match="pre-commit"):
            upload(w, p)
    with w.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM documents WHERE project_id=:p"), {"p": UUID(p)}
            ).scalar()
            == 0
        )
    assert upload(w, p)


def test_limits_mime_quarantine_and_cross_document_version(workspace: Workspace) -> None:
    w = workspace
    p = project(w)
    for content, mime, status in (
        (b"", "text/plain", 422),
        (b"\xff", "text/plain", 415),
        (b"%PDF-1.7\n", "text/plain", 415),
        (b"fake", "application/pdf", 415),
        (b"x" * (2097153), "text/plain", 413),
        (b"<html>", "text/html", 415),
    ):
        response = w.client.post(
            "/api/v1/documents",
            params={"project_id": p, "title": "Test"},
            content=content,
            headers={**w.user_headers, "Content-Type": mime, "Idempotency-Key": uuid4().hex},
        )
        assert response.status_code == status, response.text
    response = w.client.post(
        "/api/v1/documents",
        params={"project_id": p, "title": "PDF"},
        content=b"%PDF-1.7\n%%EOF",
        headers={
            **w.user_headers,
            "Content-Type": "application/pdf",
            "Idempotency-Key": "pdf-intake-001",
        },
    )
    assert response.status_code == 201
    pdf = response.json()["document_id"]
    assert (
        w.client.get(f"/api/v1/documents/{pdf}/content", headers=w.user_headers).status_code == 409
    )
    first, second = upload(w, p, key="first-doc"), upload(w, p, key="second-doc")
    response = w.client.get(
        f"/api/v1/documents/{first['document_id']}/content",
        params={"version_id": second["version_id"]},
        headers=w.user_headers,
    )
    assert response.status_code == 404


def test_concurrent_retry_and_append_have_one_winner(workspace: Workspace) -> None:
    w = workspace
    p = project(w)

    def create(_: int) -> dict[str, str]:
        with TestClient(create_app()) as client:
            response = client.post(
                "/api/v1/documents",
                params={"project_id": p, "title": "Concurrent"},
                content=b"Original",
                headers={
                    **w.user_headers,
                    "Content-Type": "text/plain",
                    "Idempotency-Key": "concurrent-create",
                },
            )
            assert response.status_code == 201, response.text
            result: dict[str, str] = response.json()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert results[0] == results[1]
    first = results[0]

    def append(index: int) -> int:
        with TestClient(create_app()) as client:
            return int(
                client.post(
                    f"/api/v1/documents/{first['document_id']}/versions",
                    params={"parent_version_id": first["version_id"]},
                    content=b"Next",
                    headers={
                        **w.user_headers,
                        "Content-Type": "text/plain",
                        "Idempotency-Key": f"concurrent-append-{index}",
                    },
                ).status_code
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(append, range(2))) == [201, 409]


def test_orphan_report_preserves_referenced_and_recent_blobs(workspace: Workspace) -> None:
    import os
    import time

    from app.maintenance import reconcile

    w = workspace
    uploaded = upload(w, project(w))
    storage = BlobStore.from_environment()
    with w.engine.connect() as connection:
        referenced = connection.execute(
            text("SELECT blob_key FROM document_versions WHERE id=:v"),
            {"v": UUID(uploaded["version_id"])},
        ).scalar_one()
    old = storage.put(b"Uncommitted old orphan")
    recent = storage.put(b"Uncommitted recent orphan")
    old_time = time.time() - 7200
    os.utime(storage.root / str(old), (old_time, old_time))
    os.utime(storage.root / str(referenced), (old_time, old_time))
    report = reconcile(w.engine, storage)
    assert report["old_orphans"] >= 1 and report["removed"] == 0
    assert (storage.root / str(old)).exists()
    applied = reconcile(w.engine, storage, apply=True)
    assert applied["removed"] >= 1
    assert not (storage.root / str(old)).exists()
    assert (storage.root / str(recent)).exists()
    assert (storage.root / str(referenced)).exists()
