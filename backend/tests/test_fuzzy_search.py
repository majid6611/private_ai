"""Forgiving retrieval: word forms and typos find passages; permissions still apply."""

from uuid import UUID

from sqlalchemy import text

from app.ai import retrieve
from tests.support import Workspace
from tests.test_ai import prepare
from tests.test_chat import publish_second_document

RESUME = (
    b"Majid Example. Skills: programming languages Python, SQL and Go. "
    b"Experience: ten years building web platforms for logistics companies."
)


def found(w: Workspace, p: str, question: str, user: str | None = None) -> list[str]:
    with w.engine.begin() as c:
        rows = retrieve(c, UUID(p), UUID(user or w.user_id), question, 5)
    return [str(r["content"]) for r in rows]


def prepared(w: Workspace) -> tuple[str, str]:
    p, d = prepare(w)  # a published, indexed placeholder document
    publish_second_document(w, p, RESUME, "resume-fuzzy-0001")
    return p, d


def test_misspelled_questions_still_find_the_passage(workspace: Workspace) -> None:
    w = workspace
    p, _ = prepared(w)
    for question in (
        "what programing langues he knows?",  # the real failing question
        "programing langues",
        "wich languges does he use",
        "pyhton and sql skils",
    ):
        assert any("Python" in text_ for text_ in found(w, p, question)), question


def test_word_forms_match_by_stem(workspace: Workspace) -> None:
    w = workspace
    p, _ = prepared(w)
    for question in ("language", "build web platform", "logistic company experience", "skill"):
        assert any("Python" in text_ for text_ in found(w, p, question)), question


def test_nonsense_and_unrelated_words_stay_empty(workspace: Workspace) -> None:
    w = workspace
    p, _ = prepared(w)
    for question in ("zxqvbn wkjhgf", "favourite colour of the president", "parking spaces at the dock"):
        assert found(w, p, question) == [], question


def test_fuzzy_matches_never_cross_permission_boundaries(workspace: Workspace) -> None:
    w = workspace
    p, _ = prepared(w)
    assert any("Python" in t for t in found(w, p, "langues programing"))
    admin = w.client.get("/api/v1/auth/me", headers=w.admin_headers).json()["id"]
    with w.engine.connect() as c:  # the helper gives every upload the same title: find it by content
        resume_id = str(
            c.execute(
                text("""SELECT d.id FROM text_chunks k JOIN document_versions v ON v.id=k.version_id
                JOIN documents d ON d.id=v.document_id WHERE d.project_id=:p AND k.content LIKE '%Python%'"""),
                {"p": UUID(p)},
            ).scalar_one()
        )
    with w.engine.begin() as c:  # restrict the resume and withdraw every grant
        c.execute(text("UPDATE documents SET restricted=true WHERE id=:d"), {"d": UUID(resume_id)})
        c.execute(text("DELETE FROM document_grants WHERE document_id=:d"), {"d": UUID(resume_id)})
    assert not any("Python" in t for t in found(w, p, "langues programing"))
    assert not any("Python" in t for t in found(w, p, "languages", user=admin))
    # a grant brings it back, for exactly that person
    with w.engine.begin() as c:
        c.execute(text("INSERT INTO document_grants(document_id,user_id) VALUES (:d,:u)"), {"d": UUID(resume_id), "u": UUID(w.user_id)})
    assert any("Python" in t for t in found(w, p, "langues programing"))


def test_search_endpoint_is_just_as_forgiving(workspace: Workspace) -> None:
    w = workspace
    p, _ = prepared(w)
    hits = w.client.get("/api/v1/search", headers=w.user_headers, params={"project_id": p, "q": "programing langues"})
    assert hits.status_code == 200
    assert any("Python" in h["snippet"] for h in hits.json())
    assert {"document_id", "title", "version_id", "start_offset", "end_offset", "page_start", "page_end", "snippet"} <= set(hits.json()[0])
    empty = w.client.get("/api/v1/search", headers=w.user_headers, params={"project_id": p, "q": "zxqvbn wkjhgf"})
    assert empty.json() == []
