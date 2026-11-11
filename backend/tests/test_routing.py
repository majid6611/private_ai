"""Document routing: pick the file, ask when unsure, say so when nothing fits, remember the topic."""

from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy import text

import app.routing as routing
from app.embeddings import EmbeddingWorker
from app.routing import plan_answer, route_question
from tests.fake_embedder import FakeEmbedder
from tests.support import Workspace
from tests.test_ai import prepare, service
from tests.test_approvals import approve, publish, request
from tests.test_chat import finish, lease, read, say, thread
from tests.test_ingestion import headers
from tests.test_retrieval_eval import chunk_ranges

RESUME = (
    "MAJID EXAMPLE\nLinux Systems Engineer\nEXPERIENCE\n"
    "Backend Engineer | Foresight Analytics\nDeveloper | Kohancharm\n"
    "SKILLS\nPython, Bash, Rust\nEDUCATION\nBachelor of Computer Science, Example University\n"
)
CONTRACT = (
    "MASTER SERVICES AGREEMENT\nThe Supplier shall meet the delivery deadline.\n"
    "Payment is due within 45 days of invoice. Liability is capped.\n"
)
SECOND_RESUME = (
    "SARA SAMPLE\nLinux Systems Engineer\nEXPERIENCE\n"
    "Backend Engineer | Lakeshore Studio\nDeveloper | Brightside\n"
    "SKILLS\nPython, Bash, Go\nEDUCATION\nBachelor of Computer Science, Other University\n"
)


@pytest.fixture
def embedder(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeEmbedder]:
    fake = FakeEmbedder()
    monkeypatch.setenv("EMBEDDER_URL", fake.url)
    monkeypatch.setattr(routing, "DECISIVE_GAP", 0.10)
    yield fake
    fake.close()


def publish_doc(w: Workspace, p: str, title: str, content: str, key: str) -> str:
    response = w.client.post(
        "/api/v1/documents",
        params={"project_id": p, "title": title, "restricted": False},
        headers={**w.user_headers, "Content-Type": "text/plain", "Idempotency-Key": key},
        content=content.encode(),
    )
    assert response.status_code == 201, response.text
    document, version = response.json()["document_id"], response.json()["version_id"]
    a = request(w, document, version)
    approve(w, a)
    assert publish(w, document, a) == 200
    job = w.client.post("/internal/v1/ingestion/lease", headers=headers()).json()["job"]
    done = w.client.post(
        f"/internal/v1/ingestion/{job['id']}/complete",
        headers=headers(),
        json={"lease_token": job["lease_token"], "chunks": chunk_ranges(content)},
    )
    assert done.status_code == 204, done.text
    return str(document)


def embed_everything(w: Workspace) -> None:
    worker = EmbeddingWorker(w.engine)
    for _ in range(5000):  # the shared test database also holds earlier tests' documents
        if not worker.step():
            return
    raise AssertionError("embedding worker did not settle")


def library(w: Workspace, with_second_resume: bool = False) -> tuple[str, dict[str, str]]:
    p, _ = prepare(w)  # also clears queues; leaves a published placeholder ("Original contract")
    docs = {
        "resume": publish_doc(w, p, "Majid Example resume", RESUME, "route-resume-001"),
        "contract": publish_doc(w, p, "Northwind services agreement", CONTRACT, "route-contract-01"),
    }
    if with_second_resume:
        docs["resume2"] = publish_doc(w, p, "Sara Sample resume", SECOND_RESUME, "route-resume-002")
    embed_everything(w)
    return p, docs


def source_docs(task: dict[str, object] | None) -> set[str]:
    assert task is not None
    sources = task["sources"]
    assert isinstance(sources, list)
    return {str(s["document"]) for s in sources}


def turns_of(w: Workspace, t: str) -> list[dict[str, object]]:
    turns = read(w, t)["turns"]
    assert isinstance(turns, list)
    return turns


def focus_titles(w: Workspace, t: str) -> list[str]:
    focus = read(w, t)["focus"]
    assert isinstance(focus, list)
    return [str(f["title"]) for f in focus]


def route(w: Workspace, p: str, question: str, **kwargs: object) -> routing.Route:
    with w.engine.begin() as c:
        return route_question(c, UUID(p), UUID(w.user_id), question, **kwargs)  # type: ignore[arg-type]


def test_worker_builds_vectors_and_cards_for_published_documents(
    workspace: Workspace, embedder: FakeEmbedder
) -> None:
    w = workspace
    p, docs = library(w)
    with w.engine.connect() as c:
        missing = c.execute(text("""SELECT count(*) FROM text_chunks k JOIN documents d
            ON d.active_version_id=k.version_id WHERE d.project_id=:p AND k.embedding IS NULL"""), {"p": UUID(p)}).scalar()
        cards = c.execute(text("""SELECT d.title,k.card_text FROM document_cards k JOIN documents d
            ON d.active_version_id=k.version_id WHERE d.project_id=:p"""), {"p": UUID(p)}).all()
    assert missing == 0 and len(cards) == 3
    resume_card = next(t for title, t in cards if title.startswith("Majid"))
    assert "Linux Systems Engineer" in resume_card and "EXPERIENCE" in resume_card and "SKILLS" in resume_card
    assert docs["resume"]


def route_task(w: Workspace) -> dict[str, object]:
    """What the orchestrator would receive when a question needs the model to choose a document."""
    task = lease(w)
    assert task is not None and task["stage"] == "route", task
    return task


def route_back(w: Workspace, task: dict[str, object], documents: list[str], **flags: object) -> int:
    response = w.client.post(
        f"/internal/v1/ai/tasks/{task['id']}/route",
        headers=service(),
        json={"lease_token": task["lease_token"], "documents": documents, **flags},
    )
    return int(response.status_code)


def candidates_of(task: dict[str, object]) -> list[dict[str, str]]:
    found = task["candidates"]
    assert isinstance(found, list)
    return found


def test_a_career_question_goes_to_the_resume_not_the_contract(
    workspace: Workspace, embedder: FakeEmbedder
) -> None:
    w = workspace
    p, docs = library(w)
    for question in ("send me all companies he worked for", "what is his work experience?", "which languages does he know"):
        r = route(w, p, question)
        chosen = {str(d["id"]) for d in r.documents} | {str(c["id"]) for c in r.candidates}
        assert docs["resume"] in chosen and docs["contract"] not in {str(d["id"]) for d in r.documents}, (question, r.kind)
    c = route(w, p, "when is the delivery deadline for payment?")
    chosen_c = {str(d["id"]) for d in c.documents} | {str(x["id"]) for x in c.candidates}
    assert docs["contract"] in chosen_c and docs["resume"] not in {str(d["id"]) for d in c.documents}


def test_a_decisive_match_skips_the_model_and_answers_from_that_document(
    workspace: Workspace, embedder: FakeEmbedder
) -> None:
    w = workspace
    p, docs = library(w)
    t = thread(w, p)
    say(w, t, "send me all companies he worked for")
    task = lease(w)
    assert task is not None
    if task["stage"] == "route":  # a near tie is allowed to ask the model; the answer must still be right
        assert route_back(w, task, ["D1"]) == 204
        task = lease(w)
    assert task is not None and task["stage"] == "answer"
    sources = task["sources"]
    assert isinstance(sources, list) and sources
    finish(w, task, "Foresight Analytics, Kohancharm")
    assert read(w, t)["turns"][0]["status"] == "COMPLETED"  # type: ignore[index]


def test_the_model_picks_among_close_candidates_and_only_those(
    workspace: Workspace, embedder: FakeEmbedder
) -> None:
    w = workspace
    p, docs = library(w, with_second_resume=True)
    t = thread(w, p)
    say(w, t, "what companies did he work for?")
    assert read(w, t)["turns"][0]["status"] == "PENDING"  # type: ignore[index]  # routing is invisible to the user
    task = route_task(w)
    cands = candidates_of(task)
    assert [c["label"] for c in cands][:2] == ["D1", "D2"]
    assert all(set(c) == {"label", "title", "description"} for c in cands)  # no ids reach the model
    titles = {c["title"] for c in cands}
    assert {"Majid Example resume", "Sara Sample resume"} <= titles
    pick = next(c["label"] for c in cands if c["title"] == "Sara Sample resume")
    assert route_back(w, task, ["D99"]) == 422  # outside the allow-list
    assert route_back(w, task, [pick]) == 204
    answer = lease(w)
    assert answer is not None and answer["stage"] == "answer"
    assert source_docs(answer) == {"Sara Sample resume"}
    finish(w, answer, "Lakeshore Studio, Brightside")
    focus = read(w, t)["focus"]
    assert isinstance(focus, list) and [f["title"] for f in focus] == ["Sara Sample resume"]


def test_unsure_makes_the_assistant_ask_the_user(workspace: Workspace, embedder: FakeEmbedder) -> None:
    w = workspace
    p, docs = library(w, with_second_resume=True)
    t = thread(w, p)
    say(w, t, "what companies did he work for?")
    task = route_task(w)
    assert route_back(w, task, ["D1", "D2"], unsure=True) == 204
    assert lease(w) is None  # nothing more for the model to do
    turn = read(w, t)["turns"][0]  # type: ignore[index]
    assert turn["status"] == "COMPLETED" and "Which one do you mean" in str(turn["answer"])
    clarify = turn["clarify"]
    assert isinstance(clarify, list) and len(clarify) == 2
    # Choosing an option answers from exactly that document, without another routing step.
    response = w.client.post(
        f"/api/v1/chat/threads/{t}/messages",
        headers=w.user_headers,
        json={"content": "what companies did he work for?", "document_ids": [clarify[0]["document_id"]]},
    )
    assert response.status_code == 202
    answer = lease(w)
    assert answer is not None and answer["stage"] == "answer"


def test_no_matching_document_is_a_plain_not_found_without_an_answer_call(
    workspace: Workspace, embedder: FakeEmbedder
) -> None:
    w = workspace
    p, _ = library(w)
    t = thread(w, p)
    say(w, t, "what is the weather forecast on mars")
    task = route_task(w)
    assert route_back(w, task, []) == 204
    assert lease(w) is None
    turn = read(w, t)["turns"][0]  # type: ignore[index]
    assert "couldn't find anything" in str(turn["answer"]) and turn["model"] is None


def test_a_failed_routing_call_falls_back_to_the_best_vector_match(
    workspace: Workspace, embedder: FakeEmbedder
) -> None:
    w = workspace
    p, _ = library(w, with_second_resume=True)
    t = thread(w, p)
    say(w, t, "what companies did he work for?")
    task = route_task(w)
    assert route_back(w, task, [], failed=True) == 204
    answer = lease(w)
    assert answer is not None and answer["stage"] == "answer" and answer["sources"]


def test_routing_leases_cannot_be_replayed_or_forged(workspace: Workspace, embedder: FakeEmbedder) -> None:
    w = workspace
    p, _ = library(w, with_second_resume=True)
    t = thread(w, p)
    say(w, t, "what companies did he work for?")
    task = route_task(w)
    forged = w.client.post(
        f"/internal/v1/ai/tasks/{task['id']}/route", headers=service(),
        json={"lease_token": "x" * 40, "documents": ["D1"]},
    )
    assert forged.status_code == 409
    assert w.client.post(f"/internal/v1/ai/tasks/{task['id']}/route", headers=w.user_headers,
                         json={"lease_token": task["lease_token"], "documents": ["D1"]}).status_code == 401
    assert route_back(w, task, ["D1"]) == 204
    assert route_back(w, task, ["D1"]) == 409  # already used
    # A route lease cannot complete the answer stage.
    complete = w.client.post(f"/internal/v1/ai/tasks/{task['id']}/complete", headers=service(),
                             json={"lease_token": task["lease_token"], "answer": "x", "citation_ids": []})
    assert complete.status_code == 409


def test_a_document_restricted_while_routing_is_never_read(workspace: Workspace, embedder: FakeEmbedder) -> None:
    w = workspace
    p, docs = library(w, with_second_resume=True)
    t = thread(w, p)
    say(w, t, "what companies did he work for?")
    task = route_task(w)
    pick = next(c["label"] for c in candidates_of(task) if c["title"] == "Sara Sample resume")
    with w.engine.begin() as c:
        c.execute(text("UPDATE documents SET restricted=true WHERE id=:d"), {"d": UUID(docs["resume2"])})
        c.execute(text("DELETE FROM document_grants WHERE document_id=:d"), {"d": UUID(docs["resume2"])})
    assert route_back(w, task, [pick]) == 204
    assert lease(w) is None  # nothing to read any more: answered as "insufficient evidence"
    turn = read(w, t)["turns"][0]  # type: ignore[index]
    assert str(turn["answer"]).startswith("Insufficient evidence")


def test_the_topic_is_remembered_and_can_be_cleared(workspace: Workspace, embedder: FakeEmbedder) -> None:
    w = workspace
    p, docs = library(w, with_second_resume=True)
    t = thread(w, p)
    assert w.client.post(f"/api/v1/chat/threads/{t}/messages", headers=w.user_headers,
                         json={"content": "what companies did he work for?", "document_ids": [docs["resume"]]}).status_code == 202
    first = lease(w)
    assert first is not None
    finish(w, first, "Foresight Analytics, Kohancharm")
    say(w, t, "and what languages does he know?")
    task = lease(w)  # a follow-up in a settled conversation goes straight to the answer
    assert source_docs(task) == {"Majid Example resume"}
    assert task is not None and task["stage"] == "answer"
    finish(w, task, "Python")
    assert w.client.delete(f"/api/v1/chat/threads/{t}/focus", headers=w.user_headers).status_code == 204
    assert focus_titles(w, t) == []
    say(w, t, "and what languages does he know?")
    again = lease(w)
    assert again is not None and again["stage"] == "route"  # topic forgotten: the model must choose


def test_explicit_documents_must_be_visible_to_the_user(workspace: Workspace, embedder: FakeEmbedder) -> None:
    w = workspace
    p, docs = library(w)
    t = thread(w, p)
    assert w.client.post(f"/api/v1/chat/threads/{t}/messages", headers=w.user_headers,
                         json={"content": "hello there", "document_ids": ["00000000-0000-4000-8000-000000000000"]}).status_code == 404
    with w.engine.begin() as c:
        c.execute(text("UPDATE documents SET restricted=true WHERE id=:d"), {"d": UUID(docs["resume"])})
        c.execute(text("DELETE FROM document_grants WHERE document_id=:d"), {"d": UUID(docs["resume"])})
    assert w.client.post(f"/api/v1/chat/threads/{t}/messages", headers=w.user_headers,
                         json={"content": "work experience", "document_ids": [docs["resume"]]}).status_code == 404


def test_restricted_documents_are_never_candidates(workspace: Workspace, embedder: FakeEmbedder) -> None:
    w = workspace
    p, docs = library(w)
    with w.engine.begin() as c:
        c.execute(text("UPDATE documents SET restricted=true WHERE id=:d"), {"d": UUID(docs["resume"])})
        c.execute(text("DELETE FROM document_grants WHERE document_id=:d"), {"d": UUID(docs["resume"])})
    r = route(w, p, "send me all companies he worked for")
    offered = {str(d["id"]) for d in r.documents} | {str(c["id"]) for c in r.candidates}
    assert docs["resume"] not in offered


def test_without_an_embedder_everything_falls_back_to_word_search(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = workspace
    p, docs = prepare(w)
    monkeypatch.setenv("EMBEDDER_URL", "http://127.0.0.1:9")  # nothing listens here
    with w.engine.begin() as c:
        plan = plan_answer(c, UUID(p), UUID(w.user_id), "contract")
    assert plan.route == "lexical" and plan.sources
    t = thread(w, p)
    say(w, t, "contract")
    assert lease(w) is not None and docs


def test_list_questions_get_a_bigger_evidence_budget(workspace: Workspace, embedder: FakeEmbedder) -> None:
    w = workspace
    p, _ = prepare(w)
    long_resume = "MAJID EXAMPLE\nEXPERIENCE\n" + "".join(
        f"Engineer {n} | Company{n} Analytics worked on platform {n}. " + "x" * 400 + "\n" for n in range(14)
    )
    publish_doc(w, p, "Majid long resume", long_resume, "route-long-resume1")
    embed_everything(w)
    with w.engine.connect() as c:
        resume_id = c.execute(text("SELECT id FROM documents WHERE title='Majid long resume' AND project_id=:p"), {"p": UUID(p)}).scalar_one()
    with w.engine.begin() as c:
        normal = plan_answer(c, UUID(p), UUID(w.user_id), "what is his work experience", explicit_ids=[resume_id])
        listing = plan_answer(c, UUID(p), UUID(w.user_id), "list all the companies he worked for", explicit_ids=[resume_id])

    def chars(plan: routing.Plan) -> int:
        return sum(len(s["content"]) for s in plan.sources)

    assert chars(normal) <= 2500 or len(normal.sources) == 1
    assert len(listing.sources) > len(normal.sources) and chars(listing) <= 4800


def only_this_projects_cards_need_descriptions(w: Workspace, p: str) -> None:
    """The test database is shared: park every other project's cards so leases stay local."""
    with w.engine.begin() as c:
        c.execute(
            text("""UPDATE document_cards SET description='parked by the test'
            WHERE description IS NULL AND version_id NOT IN
            (SELECT active_version_id FROM documents WHERE project_id=:p AND active_version_id IS NOT NULL)"""),
            {"p": UUID(p)},
        )


def card_job(w: Workspace) -> dict[str, object] | None:
    card: dict[str, object] | None = w.client.post("/internal/v1/ai/card-lease", headers=service()).json()["card"]
    return card


def test_documents_get_a_model_written_description_that_replaces_the_raw_card(
    workspace: Workspace, embedder: FakeEmbedder
) -> None:
    w = workspace
    p, docs = library(w)
    only_this_projects_cards_need_descriptions(w, p)
    leased: dict[str, dict[str, object]] = {}
    for _ in range(10):
        card = card_job(w)
        if card is None:
            break
        leased[str(card["id"])] = card
    assert leased
    mine = next(c for c in leased.values() if str(c["title"]).startswith("Majid"))
    assert "EXPERIENCE" in str(mine["text"]) and len(str(mine["text"])) <= 2500
    bad = w.client.post(f"/internal/v1/ai/card/{mine['id']}/complete", headers=service(),
                        json={"lease_token": "x" * 40, "description": "A resume of a Linux engineer."})
    assert bad.status_code == 409
    assert w.client.post(f"/internal/v1/ai/card/{mine['id']}/complete", headers=service(),
                         json={"lease_token": mine["lease_token"], "description": "short"}).status_code == 422
    done = w.client.post(f"/internal/v1/ai/card/{mine['id']}/complete", headers=service(),
                         json={"lease_token": mine["lease_token"], "description": "A resume of Majid Example, a Linux engineer, with work experience and skills."})
    assert done.status_code == 204
    again = w.client.post(f"/internal/v1/ai/card/{mine['id']}/complete", headers=service(),
                          json={"lease_token": mine["lease_token"], "description": "A resume of Majid Example, a Linux engineer, with work experience and skills."})
    assert again.status_code == 409  # one description per lease
    with w.engine.connect() as c:
        row = c.execute(text("SELECT description,card_text,embedding IS NULL AS stale FROM document_cards WHERE version_id=:v"), {"v": UUID(str(mine["id"]))}).mappings().one()
    assert row["description"].startswith("A resume of Majid") and "Sections:" in row["card_text"] and row["stale"]
    embed_everything(w)  # the worker re-embeds the richer card
    with w.engine.connect() as c:
        assert c.execute(text("SELECT embedding IS NOT NULL FROM document_cards WHERE version_id=:v"), {"v": UUID(str(mine["id"]))}).scalar()
    # the candidate list given to the model now carries that description
    t = thread(w, p)
    with w.engine.begin() as c:
        rr = route_question(c, UUID(p), UUID(w.user_id), "what is on that resume zzz")
    assert any("A resume of Majid" in str(x["description"]) for x in rr.ranking)
    assert t


def test_card_descriptions_are_not_leased_twice_at_once(workspace: Workspace, embedder: FakeEmbedder) -> None:
    w = workspace
    p, _ = library(w)
    only_this_projects_cards_need_descriptions(w, p)
    first = card_job(w)
    assert first is not None
    seen = {str(first["id"])}
    for _ in range(40):
        nxt = card_job(w)
        if nxt is None:
            break
        assert str(nxt["id"]) not in seen
        seen.add(str(nxt["id"]))
    assert w.client.post("/internal/v1/ai/card-lease", headers=w.user_headers).status_code == 401


def test_a_complete_follow_up_is_searched_on_its_own_words_not_the_previous_question(
    workspace: Workspace, embedder: FakeEmbedder
) -> None:
    w = workspace
    p, _ = prepare(w)
    long_resume = "MAJID EXAMPLE\nEXPERIENCE\n" + "".join(
        f"Engineer {n} | Company{n} Analytics worked on platform {n}. " + "x" * 400 + "\n" for n in range(14)
    ) + "SKILLS\nPython, Bash and Rust programming languages.\n"
    publish_doc(w, p, "Majid long resume", long_resume, "route-follow-up-01")
    embed_everything(w)
    with w.engine.connect() as c:
        resume_id = c.execute(text("SELECT id FROM documents WHERE title='Majid long resume' AND project_id=:p"), {"p": UUID(p)}).scalar_one()
    with w.engine.begin() as c:
        plan = plan_answer(
            c, UUID(p), UUID(w.user_id), "and which programming languages does he know?",
            previous="list all the companies he worked for", focus_ids=[resume_id],
        )
        bare = plan_answer(
            c, UUID(p), UUID(w.user_id), "what about it?",
            previous="which programming languages does he know?", focus_ids=[resume_id],
        )
    chars = sum(len(s["content"]) for s in plan.sources)
    assert plan.route == "docs" and chars <= 2500, chars  # not a "list all" budget carried over
    assert any("Python, Bash and Rust" in s["content"] for s in plan.sources)
    assert bare.route == "docs" and bare.sources  # a question with nothing to search still borrows
