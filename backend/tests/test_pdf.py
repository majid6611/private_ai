"""PDF intake contract: inert downloads, review/publish, isolated extraction results."""

import base64
import os
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import text

from tests.support import Workspace
from tests.test_approvals import approve, publish, request
from tests.test_documents import project

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
PAGE1 = "SERVICE CONTRACT. Delivery deadline: 30 October 2026."
PAGE2 = "Payment is due within 45 days. Final report deadline: 15 December 2026."
EXTRACTED = PAGE1 + "\n\n" + PAGE2
SPANS = [
    {"page": 1, "start": 0, "end": len(PAGE1)},
    {"page": 2, "start": len(PAGE1) + 2, "end": len(EXTRACTED)},
]


def key() -> dict[str, str]:
    return {"X-Ingestion-Key": Path(os.environ["INGESTION_KEY_FILE"]).read_text().strip()}


def reviewer_setup(w: Workspace) -> str:
    p = project(w)
    admin = w.client.get("/api/v1/auth/me", headers=w.admin_headers).json()["id"]
    for user_id, flags in (
        (w.user_id, {"can_edit": True, "can_history": True, "can_publish": True}),
        (admin, {"can_approve": True}),
    ):
        w.client.put(f"/api/v1/projects/{p}/members/{user_id}", headers=w.admin_headers, json=flags)
    return p


def upload_pdf(w: Workspace, p: str, content: bytes = PDF, title: str = "Contract") -> tuple[str, str]:
    response = w.client.post(
        "/api/v1/documents",
        params={"project_id": p, "title": title},
        content=content,
        headers={
            **w.user_headers,
            "Content-Type": "application/pdf",
            "Idempotency-Key": uuid4().hex,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["document_id"]), str(response.json()["version_id"])


def publish_pdf(w: Workspace, content: bytes = PDF, title: str = "Contract") -> tuple[str, str, str]:
    with w.engine.begin() as connection:
        connection.execute(text("UPDATE intake_jobs SET status='DONE'"))
    p = reviewer_setup(w)
    d, v = upload_pdf(w, p, content, title)
    a = request(w, d, v)
    approve(w, a)
    assert publish(w, d, a) == 200
    return p, d, v


def lease(w: Workspace) -> dict[str, object]:
    response = w.client.post("/internal/v1/ingestion/lease", headers=key())
    assert response.status_code == 200, response.text
    job: dict[str, object] = response.json()["job"]
    return job


def complete(w: Workspace, job: dict[str, object], **body: object) -> int:
    payload: dict[str, object] = {"lease_token": job["lease_token"], **body}
    return int(
        w.client.post(
            f"/internal/v1/ingestion/{job['id']}/complete", headers=key(), json=payload
        ).status_code
    )


def good_result() -> dict[str, object]:
    return {
        "text": EXTRACTED,
        "pages": SPANS,
        "chunks": [{"start": 0, "end": len(EXTRACTED)}],
        "extractor": "pypdf-test",
    }


def test_pdf_review_publish_and_inert_download(workspace: Workspace) -> None:
    w = workspace
    p = reviewer_setup(w)
    d, v = upload_pdf(w, p)
    # Draft download needs edit rights and is an attachment, never rendered inline.
    mine = w.client.get(f"/api/v1/documents/{d}/content", headers=w.user_headers)
    assert mine.status_code == 200 and mine.content == PDF
    assert mine.headers["content-type"] == "application/pdf"
    assert mine.headers["content-disposition"] == 'attachment; filename="document.pdf"'
    assert mine.headers["x-content-type-options"] == "nosniff"
    assert mine.headers["cache-control"] == "no-store"
    assert "sandbox" in mine.headers["content-security-policy"]
    a = request(w, d, v)
    # The independent reviewer reads the exact candidate through the approval, not the draft.
    review = w.client.get(f"/api/v1/approvals/{a}/review", headers=w.admin_headers)
    assert review.status_code == 200
    assert review.json()["kind"] == "pdf" and review.json()["proposed"] is None
    blob = w.client.get(f"/api/v1/approvals/{a}/candidate", headers=w.admin_headers)
    assert blob.status_code == 200 and blob.content == PDF
    assert blob.headers["content-disposition"].startswith("attachment")
    assert w.client.get(f"/api/v1/approvals/{a}/candidate", headers=w.user_headers).status_code == 403
    assert w.client.get(f"/api/v1/approvals/{uuid4()}/candidate", headers=w.admin_headers).status_code == 404
    approve(w, a)
    assert publish(w, d, a) == 200


def test_pdf_lease_complete_stores_derived_text_pages_and_search(workspace: Workspace) -> None:
    w = workspace
    p, d, v = publish_pdf(w)
    job = lease(w)
    assert job["version_id"] == v and "text" not in job
    assert job["kind"] == "pdf"
    assert base64.b64decode(str(job["pdf_b64"])) == PDF
    assert complete(w, job, **good_result()) == 204
    assert complete(w, job, **good_result()) == 204  # idempotent replay of a DONE job
    hits = w.client.get("/api/v1/search", headers=w.admin_headers, params={"project_id": p, "q": "deadline"})
    assert hits.status_code == 200 and hits.json()
    hit = hits.json()[0]
    assert hit["version_id"] == v and hit["page_start"] == 1 and hit["page_end"] == 2
    assert "Delivery deadline" in hit["snippet"]
    with w.engine.connect() as c:
        row = c.execute(
            text("SELECT extractor,page_count,length(text) AS n FROM version_texts WHERE version_id=:v"),
            {"v": UUID(v)},
        ).mappings().one()
    assert row["extractor"] == "pypdf-test" and row["page_count"] == 2 and row["n"] == len(EXTRACTED)
    listing = w.client.get(f"/api/v1/documents/{d}/versions", headers=w.user_headers).json()[0]
    assert listing["mime_type"] == "application/pdf"
    assert listing["index_status"] == "DONE" and listing["page_count"] == 2


def test_chunk_page_ranges_are_computed_by_the_backend(workspace: Workspace) -> None:
    w = workspace
    p, d, v = publish_pdf(w)
    job = lease(w)
    cut = len(PAGE1) + 2
    chunks = [{"start": 0, "end": cut + 10}, {"start": cut, "end": len(EXTRACTED)}]
    assert complete(w, job, text=EXTRACTED, pages=SPANS, chunks=chunks, extractor="x") == 204
    with w.engine.connect() as c:
        rows = c.execute(
            text("SELECT ordinal,page_start,page_end FROM text_chunks WHERE version_id=:v ORDER BY ordinal"),
            {"v": UUID(v)},
        ).all()
    assert [tuple(r) for r in rows] == [(0, 1, 2), (1, 2, 2)]


def test_untrusted_extraction_results_are_validated(workspace: Workspace) -> None:
    w = workspace
    publish_pdf(w)
    job = lease(w)
    n = len(EXTRACTED)
    cover = [{"start": 0, "end": n}]
    bad: list[dict[str, object]] = [
        {"pages": SPANS, "chunks": cover},  # no text for a PDF
        {"text": EXTRACTED, "pages": SPANS, "chunks": [{"start": 0, "end": n - 5}]},  # not covered
        {"text": EXTRACTED, "pages": SPANS, "chunks": [{"start": 0, "end": n + 5}]},  # past the text
        {"text": EXTRACTED[:10] + "\x00" + EXTRACTED[11:], "pages": SPANS, "chunks": cover},
        {"text": "x" * 2_000_001, "pages": [], "chunks": cover},  # too large
        {"text": EXTRACTED, "pages": [{"page": 1, "start": 0, "end": n + 1}], "chunks": cover},
        {"text": EXTRACTED, "pages": [{"page": 2, "start": 0, "end": 5}, {"page": 1, "start": 6, "end": 9}], "chunks": cover},  # pages go backwards
        {"text": EXTRACTED, "pages": [{"page": 1, "start": 0, "end": 5}, {"page": 1, "start": 6, "end": 9}], "chunks": cover},
        {"text": EXTRACTED, "pages": [{"page": 1, "start": 0, "end": 20}, {"page": 2, "start": 10, "end": 30}], "chunks": cover},  # overlap
        {"text": EXTRACTED, "pages": [{"page": 0, "start": 0, "end": 5}], "chunks": cover},
        {"text": EXTRACTED, "pages": SPANS, "chunks": [], "needs_ocr": False},
    ]
    for body in bad:
        assert complete(w, job, **body) in (422,), body.keys()
    assert complete(w, job, **good_result()) == 204  # a valid result still succeeds afterwards


def test_image_only_pdf_is_flagged_needs_ocr_not_indexed(workspace: Workspace) -> None:
    w = workspace
    p, d, v = publish_pdf(w)
    job = lease(w)
    # Claiming "needs OCR" while supplying real text is refused.
    assert complete(w, job, text=EXTRACTED, pages=SPANS, chunks=[{"start": 0, "end": len(EXTRACTED)}], needs_ocr=True) == 422
    assert complete(w, job, text="", pages=[], chunks=[], needs_ocr=True, extractor="pypdf-test") == 204
    with w.engine.connect() as c:
        assert c.execute(text("SELECT status FROM intake_jobs WHERE version_id=:v"), {"v": UUID(v)}).scalar() == "NEEDS_OCR"
        assert c.execute(text("SELECT count(*) FROM text_chunks WHERE version_id=:v"), {"v": UUID(v)}).scalar() == 0
    status = w.client.get(f"/api/v1/documents/{d}/versions", headers=w.user_headers).json()[0]["index_status"]
    assert status == "NEEDS_OCR"
    found = w.client.get("/api/v1/search", headers=w.admin_headers, params={"project_id": p, "q": "deadline"})
    assert found.json() == []


def test_text_versions_still_reject_extraction_fields(workspace: Workspace) -> None:
    from tests.test_approvals import setup
    from tests.test_ingestion import index_one

    w = workspace
    with w.engine.begin() as connection:
        connection.execute(text("UPDATE intake_jobs SET status='DONE'"))
    p, d, v = setup(w)
    job = index_one(w, d, v)
    assert job["kind"] == "text" and "pdf_b64" not in job
    assert complete(w, job, text="forged", pages=[], chunks=[{"start": 0, "end": 5}]) == 422


def test_pdf_search_still_respects_document_restrictions(workspace: Workspace) -> None:
    w = workspace
    p, d, v = publish_pdf(w)
    job = lease(w)
    assert complete(w, job, **good_result()) == 204
    assert w.client.patch(f"/api/v1/documents/{d}/access", headers=w.user_headers, json={"restricted": True}).status_code == 204
    hidden = w.client.get("/api/v1/search", headers=w.admin_headers, params={"project_id": p, "q": "deadline"})
    assert hidden.json() == []


def test_parser_failure_verdicts_are_terminal_and_visible(workspace: Workspace) -> None:
    w = workspace
    p, d, v = publish_pdf(w)
    job = lease(w)
    assert complete(w, job, chunks=[], failure="NOT_A_REAL_CODE") == 422
    assert complete(w, job, chunks=[], failure="ENCRYPTED") == 204
    listing = w.client.get(f"/api/v1/documents/{d}/versions", headers=w.user_headers).json()[0]
    assert listing["index_status"] == "FAILED" and listing["index_error"] == "ENCRYPTED"
    assert lease(w) is None  # terminal: not leased again
    found = w.client.get("/api/v1/search", headers=w.admin_headers, params={"project_id": p, "q": "deadline"})
    assert found.json() == []


def test_failure_verdict_is_refused_for_text_versions(workspace: Workspace) -> None:
    from tests.test_approvals import setup
    from tests.test_ingestion import index_one

    w = workspace
    with w.engine.begin() as connection:
        connection.execute(text("UPDATE intake_jobs SET status='DONE'"))
    p, d, v = setup(w)
    job = index_one(w, d, v)
    assert complete(w, job, chunks=[], failure="MALFORMED") == 422
