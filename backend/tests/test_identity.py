from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.cli import bootstrap_admin
from app.main import create_app
from app.security import digest_token
from tests.support import PASSWORD, Workspace, login


def test_sessions_revoke_expire_and_never_store_raw_tokens(workspace: Workspace) -> None:
    w = workspace
    raw = w.user_headers["Authorization"].split()[1]
    with w.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT token_hash FROM sessions WHERE token_hash=:h"), {"h": raw}
            ).scalar()
            is None
        )
        assert connection.execute(
            text("SELECT token_hash FROM sessions WHERE token_hash=:h"), {"h": digest_token(raw)}
        ).scalar() == digest_token(raw)
    assert w.client.get("/api/v1/auth/me", headers=w.user_headers).json()["id"] == w.user_id
    assert w.client.post("/api/v1/auth/logout", headers=w.user_headers).status_code == 204
    assert w.client.get("/api/v1/auth/me", headers=w.user_headers).status_code == 401
    headers = login(w.client, w.username)
    with w.engine.begin() as connection:
        connection.execute(
            text("UPDATE sessions SET expires_at=now()-interval '1 second' WHERE user_id=:id"),
            {"id": UUID(w.user_id)},
        )
    assert w.client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_project_access_has_no_admin_bypass_and_revokes_immediately(workspace: Workspace) -> None:
    w = workspace
    created = w.client.post("/api/v1/projects", headers=w.admin_headers, json={"name": "Secret"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    path = f"/api/v1/projects/{project_id}"
    assert w.client.get(path, headers=w.admin_headers).status_code == 404
    assert w.client.get(path, headers=w.user_headers).status_code == 404
    member = path + "/members/" + w.user_id
    assert w.client.put(member, headers=w.user_headers, json={}).status_code == 403
    assert (
        w.client.put(
            member, headers=w.admin_headers, json={"can_edit": True, "can_history": True}
        ).status_code
        == 204
    )
    view = w.client.get(path, headers=w.user_headers)
    assert view.status_code == 200
    assert view.json()["can_edit"] is True
    revision = view.json()["acl_revision"]
    visible = w.client.get("/api/v1/projects", headers=w.user_headers).json()
    assert any(item["id"] == project_id for item in visible)
    assert w.client.delete(member, headers=w.admin_headers).status_code == 204
    assert w.client.get(path, headers=w.user_headers).status_code == 404
    assert all(
        item["id"] != project_id
        for item in w.client.get("/api/v1/projects", headers=w.user_headers).json()
    )
    with w.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT acl_revision FROM projects WHERE id=:id"), {"id": UUID(project_id)}
            ).scalar()
            == revision + 1
        )


def test_disable_user_revokes_existing_sessions(workspace: Workspace) -> None:
    w = workspace
    path = "/api/v1/users/" + w.user_id
    assert w.client.patch(path, headers=w.user_headers, json={"enabled": False}).status_code == 403
    assert w.client.patch(path, headers=w.admin_headers, json={"enabled": False}).status_code == 200
    assert w.client.get("/api/v1/auth/me", headers=w.user_headers).status_code == 401
    response = w.client.post(
        "/api/v1/auth/login", json={"username": w.username, "password": PASSWORD}
    )
    assert response.status_code == 401
    assert w.client.patch(path, headers=w.admin_headers, json={"enabled": True}).status_code == 200
    assert w.client.get("/api/v1/auth/me", headers=w.user_headers).status_code == 401
    assert login(w.client, w.username)


def test_login_throttle_persists_failures(workspace: Workspace) -> None:
    w = workspace
    for _ in range(5):
        assert (
            w.client.post(
                "/api/v1/auth/login",
                json={"username": w.username, "password": "An incorrect password!!"},
            ).status_code
            == 401
        )
    response = w.client.post(
        "/api/v1/auth/login", json={"username": w.username, "password": PASSWORD}
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "300"
    # A fresh app instance uses the same database-backed limit.
    with TestClient(create_app()) as other:
        assert (
            other.post(
                "/api/v1/auth/login", json={"username": w.username, "password": PASSWORD}
            ).status_code
            == 429
        )


def test_unauthenticated_forged_identity_and_input_redaction(workspace: Workspace) -> None:
    w = workspace
    for headers in ({}, {"X-User-Id": w.user_id}, {"Authorization": "Bearer forged"}):
        assert w.client.get("/api/v1/projects", headers=headers).status_code == 401
    secret = "short-secret"
    response = w.client.post(
        "/api/v1/auth/login", json={"username": w.username, "password": secret}
    )
    assert response.status_code == 422
    assert secret not in response.text
    response = w.client.post(
        "/api/v1/users",
        headers=w.admin_headers,
        json={"username": "attempt", "password": PASSWORD, "is_admin": True},
    )
    assert response.status_code == 422
    assert (
        w.client.post(
            "/api/v1/users",
            headers=w.user_headers,
            json={"username": "attempt", "password": PASSWORD},
        ).status_code
        == 403
    )
    assert (
        w.client.post(
            "/api/v1/users",
            headers=w.admin_headers,
            json={"username": w.username, "password": PASSWORD},
        ).status_code
        == 409
    )


def test_audit_is_append_only_and_bootstrap_cannot_repeat(workspace: Workspace) -> None:
    w = workspace
    with w.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM security_events")).scalar()
        for statement in (
            "DELETE FROM security_events",
            "UPDATE security_events SET action='tampered'",
        ):
            with pytest.raises(DBAPIError):
                connection.execute(text(statement))
            connection.rollback()
    with pytest.raises(ValueError, match="already exists"):
        bootstrap_admin(w.engine, "second_admin", PASSWORD)


def test_commit_failure_returns_error_before_sending_success(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    from sqlalchemy import event

    import app.main as main

    w = workspace
    title = "commit-failure-" + uuid4().hex
    monkeypatch.setattr(main, "make_engine", lambda: w.engine)

    def fail_commit(connection: object) -> None:
        raise RuntimeError("simulated commit failure")

    with TestClient(main.create_app(), raise_server_exceptions=False) as client:
        event.listen(w.engine, "commit", fail_commit)
        try:
            response = client.post(
                "/api/v1/projects", headers=w.admin_headers, json={"name": title}
            )
            assert response.status_code == 500
        finally:
            event.remove(w.engine, "commit", fail_commit)
    with w.engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM projects WHERE name=:title"), {"title": title}
            ).scalar()
            == 0
        )
