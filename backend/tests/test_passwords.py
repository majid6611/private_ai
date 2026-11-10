from uuid import UUID

from sqlalchemy import text

from tests.support import PASSWORD, Workspace, login
from tests.test_approvals import approve, publish, request, setup
from tests.test_documents import project

NEW = "Another long test password 456"


def change(w: Workspace, headers: dict[str, str], current: str, new: str) -> int:
    response = w.client.post(
        "/api/v1/auth/password",
        headers=headers,
        json={"current_password": current, "new_password": new},
    )
    return int(response.status_code)


def test_change_password_requires_current_keeps_this_session_and_revokes_others(
    workspace: Workspace,
) -> None:
    w = workspace
    other = login(w.client, w.username)  # a second session of the same account
    # Wrong current password is 403 (not 401: the UI signs out on 401).
    assert change(w, w.user_headers, "wrong password entirely", NEW) == 403
    assert change(w, w.user_headers, PASSWORD, "short") == 422
    assert change(w, w.user_headers, PASSWORD, PASSWORD) == 422  # must differ
    assert change(w, w.user_headers, PASSWORD, NEW) == 204
    assert w.client.get("/api/v1/auth/me", headers=w.user_headers).status_code == 200
    assert w.client.get("/api/v1/auth/me", headers=other).status_code == 401
    old = w.client.post("/api/v1/auth/login", json={"username": w.username, "password": PASSWORD})
    assert old.status_code == 401
    fresh = w.client.post("/api/v1/auth/login", json={"username": w.username, "password": NEW})
    assert fresh.status_code == 200
    assert w.client.post("/api/v1/auth/password", json={}).status_code == 401


def test_change_password_does_not_invalidate_pending_approvals(workspace: Workspace) -> None:
    w = workspace
    _, d, v = setup(w)
    approval = request(w, d, v)
    reviewer = w.client.get("/api/v1/auth/me", headers=w.admin_headers).json()["id"]
    assert reviewer
    assert change(w, w.user_headers, PASSWORD, NEW) == 204
    approve(w, approval)
    assert publish(w, d, approval) == 200


def test_wrong_current_password_is_rate_limited(workspace: Workspace) -> None:
    w = workspace
    for _ in range(5):
        assert change(w, w.user_headers, "wrong password entirely", NEW) == 403
    assert change(w, w.user_headers, PASSWORD, NEW) == 429


def test_admin_reset_revokes_sessions_and_invalidates_authorization(workspace: Workspace) -> None:
    w = workspace
    path = f"/api/v1/users/{w.user_id}/password"
    assert w.client.put(path, headers=w.user_headers, json={"new_password": NEW}).status_code == 403
    me = w.client.get("/api/v1/auth/me", headers=w.admin_headers).json()["id"]
    own = w.client.put(f"/api/v1/users/{me}/password", headers=w.admin_headers, json={"new_password": NEW})
    assert own.status_code == 409
    missing = w.client.put(
        "/api/v1/users/00000000-0000-4000-8000-000000000000/password",
        headers=w.admin_headers,
        json={"new_password": NEW},
    )
    assert missing.status_code == 404
    assert w.client.put(path, headers=w.admin_headers, json={"new_password": "short"}).status_code == 422
    with w.engine.connect() as c:
        before = c.execute(
            text("SELECT auth_revision FROM users WHERE id=:i"), {"i": UUID(w.user_id)}
        ).scalar()
    assert w.client.put(path, headers=w.admin_headers, json={"new_password": NEW}).status_code == 204
    with w.engine.connect() as c:
        after = c.execute(
            text("SELECT auth_revision FROM users WHERE id=:i"), {"i": UUID(w.user_id)}
        ).scalar()
    assert before is not None and after == before + 1
    assert w.client.get("/api/v1/auth/me", headers=w.user_headers).status_code == 401
    ok = w.client.post("/api/v1/auth/login", json={"username": w.username, "password": NEW})
    assert ok.status_code == 200
    with w.engine.connect() as c:
        events = c.execute(
            text("SELECT count(*) FROM security_events WHERE action='password_reset_by_admin' AND target_id=:i"),
            {"i": UUID(w.user_id)},
        ).scalar()
    assert events == 1


def test_project_members_are_visible_to_members_only(workspace: Workspace) -> None:
    w = workspace
    p, _, _ = setup(w)
    members = w.client.get(f"/api/v1/projects/{p}/members", headers=w.user_headers)
    assert members.status_code == 200
    names = {m["username"] for m in members.json()}
    assert w.username in names
    assert all(set(m) == {"id", "username"} for m in members.json())
    # An administrator who is not a member has no bypass and learns nothing.
    solo = project(w)
    assert w.client.get(f"/api/v1/projects/{solo}/members", headers=w.user_headers).status_code == 200
    assert w.client.get(f"/api/v1/projects/{solo}/members", headers=w.admin_headers).status_code == 404
