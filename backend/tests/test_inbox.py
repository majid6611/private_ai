"""Reviewer inbox: pending approvals a person can actually act on, nothing else."""

from uuid import UUID

from sqlalchemy import text

from tests.support import Workspace
from tests.test_approvals import approve, request, setup


def inbox(w: Workspace, p: str, headers: dict[str, str]) -> list[dict[str, str]]:
    response = w.client.get(f"/api/v1/projects/{p}/approvals/pending", headers=headers)
    assert response.status_code == 200, response.text
    items: list[dict[str, str]] = response.json()
    return items


def test_independent_reviewer_sees_pending_request_then_it_leaves(workspace: Workspace) -> None:
    w = workspace
    p, d, v = setup(w)  # user authors with all rights; admin is a can_approve-only member
    assert inbox(w, p, w.admin_headers) == []
    a = request(w, d, v)
    items = inbox(w, p, w.admin_headers)
    assert [i["approval_id"] for i in items] == [a]
    item = items[0]
    assert item["document_id"] == d and item["candidate_version_id"] == v
    assert item["title"] == "../contract.txt" and item["requester"] == w.username
    assert item["kind"] == "text" and item["requested_at"]
    assert set(item) == {
        "approval_id", "document_id", "candidate_version_id", "title",
        "requester", "kind", "requested_at",
    }
    approve(w, a)
    assert inbox(w, p, w.admin_headers) == []  # no longer waiting


def test_requesters_own_work_is_never_in_their_inbox(workspace: Workspace) -> None:
    w = workspace
    p, d, v = setup(w)  # the author also holds can_approve in setup()
    request(w, d, v)
    assert inbox(w, p, w.user_headers) == []


def test_non_approvers_and_non_members_get_nothing(workspace: Workspace) -> None:
    from tests.test_documents import project

    w = workspace
    solo = project(w)  # the user edits; the admin is not a member
    assert w.client.get(f"/api/v1/projects/{solo}/approvals/pending", headers=w.admin_headers).status_code == 404
    assert inbox(w, solo, w.user_headers) == []  # a member without can_approve


def test_stale_expired_and_unshared_requests_are_hidden(workspace: Workspace) -> None:
    w = workspace
    p, d, v = setup(w)
    old = request(w, d, v)
    assert [i["approval_id"] for i in inbox(w, p, w.admin_headers)] == [old]
    # Restricting the document hides it from a reviewer without a grant.
    assert w.client.patch(f"/api/v1/documents/{d}/access", headers=w.user_headers, json={"restricted": True}).status_code == 204
    assert inbox(w, p, w.admin_headers) == []
    # Sharing it back does not revive the old request: permission changes invalidate it.
    admin_id = w.client.get("/api/v1/auth/me", headers=w.admin_headers).json()["id"]
    assert w.client.put(f"/api/v1/documents/{d}/access/{admin_id}", headers=w.user_headers, json={}).status_code == 204
    assert inbox(w, p, w.admin_headers) == []
    assert w.client.post(f"/api/v1/approvals/{old}/approve", headers=w.admin_headers).status_code == 409
    # A fresh request after the change is listed; expiry removes it again.
    fresh = request(w, d, v)
    assert [i["approval_id"] for i in inbox(w, p, w.admin_headers)] == [fresh]
    with w.engine.begin() as c:
        c.execute(text("UPDATE approvals SET expires_at=now()-interval '1 second' WHERE id=:i"), {"i": UUID(fresh)})
    assert inbox(w, p, w.admin_headers) == []


def test_requests_from_a_disabled_requester_are_hidden(workspace: Workspace) -> None:
    w = workspace
    p, d, v = setup(w)
    request(w, d, v)
    assert len(inbox(w, p, w.admin_headers)) == 1
    off = w.client.patch(f"/api/v1/users/{w.user_id}", headers=w.admin_headers, json={"enabled": False})
    assert off.status_code == 200
    assert inbox(w, p, w.admin_headers) == []
