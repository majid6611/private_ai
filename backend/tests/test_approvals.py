from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.approvals as approvals
from app.main import create_app
from tests.support import Workspace
from tests.test_documents import project, upload


def setup(w: Workspace) -> tuple[str, str, str]:
    p = project(w)
    reviewer = w.client.get("/api/v1/auth/me", headers=w.admin_headers).json()["id"]
    for user_id, flags in (
        (
            w.user_id,
            {"can_edit": True, "can_history": True, "can_publish": True, "can_approve": True},
        ),
        (reviewer, {"can_approve": True}),
    ):
        assert (
            w.client.put(
                f"/api/v1/projects/{p}/members/{user_id}", headers=w.admin_headers, json=flags
            ).status_code
            == 204
        )
    data = upload(w, p)
    return p, data["document_id"], data["version_id"]


def request(w: Workspace, d: str, v: str) -> str:
    current = w.client.get(f"/api/v1/documents/{d}", headers=w.user_headers).json()
    response = w.client.post(
        f"/api/v1/documents/{d}/approvals",
        headers=w.user_headers,
        json={
            "candidate_version_id": v,
            "expected_active_version_id": current["active_version_id"],
            "expected_publication_revision": int(current["publication_revision"]),
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def approve(w: Workspace, approval: str) -> None:
    assert (
        w.client.post(f"/api/v1/approvals/{approval}/approve", headers=w.admin_headers).status_code
        == 204
    )


def publish(w: Workspace, d: str, a: str) -> int:
    return int(
        w.client.post(
            f"/api/v1/documents/{d}/publish", headers=w.user_headers, json={"approval_id": a}
        ).status_code
    )


def append(w: Workspace, d: str, parent: str) -> str:
    response = w.client.post(
        f"/api/v1/documents/{d}/versions",
        params={"parent_version_id": parent},
        headers={
            **w.user_headers,
            "Content-Type": "text/plain",
            "Idempotency-Key": "approval-next-version",
        },
        content=b"Second version",
    )
    assert response.status_code == 201, response.text
    return str(response.json()["version_id"])


def test_independent_human_approval_publication_and_replay(workspace: Workspace) -> None:
    w = workspace
    _, d, v = setup(w)
    a = request(w, d, v)
    assert publish(w, d, a) == 409
    assert (
        w.client.post(f"/api/v1/approvals/{a}/approve", headers=w.user_headers).status_code == 403
    )
    reviewed = w.client.get(f"/api/v1/approvals/{a}/review", headers=w.admin_headers)
    assert reviewed.status_code == 200
    assert reviewed.json()["original"] is None
    assert reviewed.json()["proposed"] == "Original contract"
    approve(w, a)
    assert publish(w, d, a) == 200
    assert publish(w, d, a) == 409
    assert (
        w.client.get(f"/api/v1/documents/{d}/content", headers=w.admin_headers).content
        == b"Original contract"
    )
    with w.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM approvals WHERE id=:a"), {"a": UUID(a)}
            ).scalar()
            == "CONSUMED"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM publication_events WHERE approval_id=:a"), {"a": UUID(a)}
            ).scalar()
            == 1
        )


def test_permission_revision_invalidates_approval_even_after_restoring_access(
    workspace: Workspace,
) -> None:
    w = workspace
    p, d, v = setup(w)
    a = request(w, d, v)
    approve(w, a)
    reviewer = w.client.get("/api/v1/auth/me", headers=w.admin_headers).json()["id"]
    assert (
        w.client.delete(
            f"/api/v1/projects/{p}/members/{reviewer}", headers=w.admin_headers
        ).status_code
        == 204
    )
    assert publish(w, d, a) == 409
    assert (
        w.client.put(
            f"/api/v1/projects/{p}/members/{reviewer}",
            headers=w.admin_headers,
            json={"can_approve": True},
        ).status_code
        == 204
    )
    assert publish(w, d, a) == 409


def test_reviewer_account_revocation_is_permanent_for_old_approval(workspace: Workspace) -> None:
    w = workspace
    _, d, v = setup(w)
    a = request(w, d, v)
    approve(w, a)
    reviewer = w.client.get("/api/v1/auth/me", headers=w.admin_headers).json()["id"]
    for enabled in (False, True):
        with w.engine.begin() as connection:
            connection.execute(
                text("UPDATE users SET enabled=:e,auth_revision=auth_revision+1 WHERE id=:u"),
                {"e": enabled, "u": UUID(reviewer)},
            )
        assert publish(w, d, a) == 409


def test_expiry_rejection_and_forged_confirmation(workspace: Workspace) -> None:
    w = workspace
    _, d, v = setup(w)
    a = request(w, d, v)
    with w.engine.begin() as connection:
        connection.execute(
            text("UPDATE approvals SET expires_at=now()-interval '1 second' WHERE id=:a"),
            {"a": UUID(a)},
        )
    assert (
        w.client.post(f"/api/v1/approvals/{a}/approve", headers=w.admin_headers).status_code == 409
    )
    a = request(w, d, v)
    assert (
        w.client.post(f"/api/v1/approvals/{a}/reject", headers=w.admin_headers).status_code == 204
    )
    assert publish(w, d, a) == 409
    result = w.client.post(
        f"/api/v1/documents/{d}/publish",
        headers=w.user_headers,
        json={"approval_id": a, "confirmed": True},
    )
    assert result.status_code == 422
    result = w.client.post(
        f"/api/v1/documents/{d}/approvals",
        headers=w.user_headers,
        json={
            "candidate_version_id": v,
            "expected_active_version_id": None,
            "expected_publication_revision": 0,
        },
    )
    assert result.status_code == 409


def test_atomic_publish_rolls_back_after_audit_failure(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = workspace
    _, d, v = setup(w)
    a = request(w, d, v)
    approve(w, a)

    def fail(*args: object) -> None:
        raise RuntimeError("publication audit failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(approvals, "audit", fail)
        with pytest.raises(RuntimeError, match="audit failure"):
            publish(w, d, a)
    current = w.client.get(f"/api/v1/documents/{d}", headers=w.user_headers).json()
    assert current["active_version_id"] is None
    with w.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM approvals WHERE id=:a"), {"a": UUID(a)}
            ).scalar()
            == "APPROVED"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM publication_events WHERE approval_id=:a"), {"a": UUID(a)}
            ).scalar()
            == 0
        )
    assert publish(w, d, a) == 200


def test_concurrent_publish_consumes_once(workspace: Workspace) -> None:
    w = workspace
    _, d, v = setup(w)
    a = request(w, d, v)
    approve(w, a)

    def run(_: int) -> int:
        with TestClient(create_app()) as client:
            return int(
                client.post(
                    f"/api/v1/documents/{d}/publish",
                    headers=w.user_headers,
                    json={"approval_id": a},
                ).status_code
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, range(2))) == [200, 409]


def test_rollback_keeps_history_and_blocks_aba_stale_approval(workspace: Workspace) -> None:
    w = workspace
    _, d, v1 = setup(w)
    a = request(w, d, v1)
    approve(w, a)
    assert publish(w, d, a) == 200
    v2 = append(w, d, v1)
    stale = request(w, d, v2)
    approve(w, stale)
    fresh = request(w, d, v2)
    approve(w, fresh)
    assert publish(w, d, fresh) == 200
    rollback = request(w, d, v1)
    approve(w, rollback)
    assert publish(w, d, rollback) == 200
    assert publish(w, d, stale) == 409
    assert (
        w.client.get(f"/api/v1/documents/{d}/content", headers=w.admin_headers).content
        == b"Original contract"
    )
    assert (
        w.client.get(
            f"/api/v1/documents/{d}/content", headers=w.user_headers, params={"version_id": v2}
        ).content
        == b"Second version"
    )
    current = w.client.get(f"/api/v1/documents/{d}", headers=w.user_headers).json()
    assert int(current["publication_revision"]) == 3
