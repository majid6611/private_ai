"""Retrieval quality gate (recall@5) on the synthetic corpus in tests/eval/corpus.py."""

import json
import os
from uuid import UUID

import pytest
from sqlalchemy import text

from app.ai import retrieve
from tests.eval.corpus import ANSWERABLE, DOCS, FACTS, HARD, NO_ANSWER, TITLES, TYPO, question_text
from tests.eval.distractors import distractors
from tests.support import Workspace
from tests.test_approvals import approve, publish, request, setup
from tests.test_ingestion import headers

K = 5
# Pilot target from the architecture (section 20). Lower only deliberately, with a reason.
RECALL_TARGET = float(os.environ.get("RETRIEVAL_RECALL_TARGET", "0.90"))


def chunk_ranges(source: str) -> list[dict[str, int]]:
    """Same windowing as workers/parser.py (1200 chars, 150 overlap, break on space)."""
    chunks: list[dict[str, int]] = []
    start = 0
    while start < len(source):
        end = min(start + 1200, len(source))
        if end < len(source):
            boundary = source.rfind(" ", start + 700, end)
            if boundary > start:
                end = boundary
        chunks.append({"start": start, "end": end})
        if end == len(source):
            break
        start = end - 150
    return chunks


def publish_corpus(w: Workspace) -> str:
    """Publish the real corpus plus generated distractors; returns the project id."""
    with w.engine.begin() as connection:
        connection.execute(text("UPDATE intake_jobs SET status='DONE'"))
    p, _, _ = setup(w)  # its placeholder draft is never published or indexed
    documents = {TITLES[name]: content for name, content in DOCS.items()}
    documents.update(distractors())
    for number, (title, content) in enumerate(documents.items()):
        response = w.client.post(
            "/api/v1/documents",
            params={"project_id": p, "title": title, "restricted": False},
            headers={
                **w.user_headers,
                "Content-Type": "text/plain",
                "Idempotency-Key": f"eval-{number:04d}",
            },
            content=content.encode(),
        )
        assert response.status_code == 201, response.text
        document, version = response.json()["document_id"], response.json()["version_id"]
        approval = request(w, document, version)
        approve(w, approval)
        assert publish(w, document, approval) == 200
        job = w.client.post("/internal/v1/ingestion/lease", headers=headers()).json()["job"]
        assert job is not None and job["version_id"] == version, "ingestion order mismatch"
        done = w.client.post(
            f"/internal/v1/ingestion/{job['id']}/complete",
            headers=headers(),
            json={"lease_token": job["lease_token"], "chunks": chunk_ranges(content)},
        )
        assert done.status_code == 204, done.text
    return p


def test_retrieval_recall_at_k(workspace: Workspace, capsys: pytest.CaptureFixture[str]) -> None:
    w = workspace
    p = publish_corpus(w)
    user = UUID(w.user_id)
    hits = {"direct": [0, 0], "paraphrase": [0, 0], "hard": [0, 0], "typo": [0, 0]}
    at = {1: 0, 3: 0}
    misses: list[str] = []
    with w.engine.begin() as connection:
        for q in ANSWERABLE + HARD + TYPO:
            rows = retrieve(connection, UUID(p), user, question_text(q), K)
            rank = next((i for i, r in enumerate(rows, 1) if q.evidence in r["content"]), None)
            for cutoff in at:
                at[cutoff] += rank is not None and rank <= cutoff
            found = rank is not None
            hits[q.kind][0] += found
            hits[q.kind][1] += 1
            if not found:
                misses.append(f"[{q.kind}] {question_text(q)}")
        no_answer_with_hits = sum(bool(retrieve(connection, UUID(p), user, q, K)) for q in NO_ANSWER)
    export = os.environ.get("RETRIEVAL_EXPORT")
    if export:
        # What the app would hand the model, for the generation evaluator.
        export_limit = int(os.environ.get("RETRIEVAL_EXPORT_LIMIT", "8"))
        cases = []
        with w.engine.begin() as connection:
            for q in ANSWERABLE + HARD:
                rows = retrieve(connection, UUID(p), user, question_text(q), export_limit)
                cases.append(
                    {
                        "question": question_text(q),
                        "kind": q.kind,
                        "answerable": True,
                        "fact": FACTS.get(q.evidence, ""),
                        "sources": [
                            {"id": str(r["id"]), "text": r["content"], "gold": q.evidence in r["content"]}
                            for r in rows
                        ],
                    }
                )
            for question in NO_ANSWER:
                rows = retrieve(connection, UUID(p), user, question, export_limit)
                cases.append(
                    {
                        "question": question,
                        "kind": "no_answer",
                        "answerable": False,
                        "fact": "",
                        "sources": [{"id": str(r["id"]), "text": r["content"], "gold": False} for r in rows],
                    }
                )
        with open(export, "w") as handle:
            json.dump(cases, handle)
    total = sum(v[0] for v in hits.values())
    count = sum(v[1] for v in hits.values())
    recall = total / count
    with capsys.disabled():
        print(f"\nRETRIEVAL recall@{K}: {total}/{count} = {recall:.0%}")
        with w.engine.connect() as c:
            chunks = c.execute(
                text("""SELECT count(*) FROM text_chunks t JOIN document_versions v ON v.id=t.version_id
                JOIN documents d ON d.id=v.document_id WHERE d.project_id=:p"""),
                {"p": p},
            ).scalar()
        print(f"  recall@1 {at[1]}/{count}, recall@3 {at[3]}/{count} (corpus: {chunks} chunks)")
        for kind, (ok, n) in hits.items():
            print(f"  {kind}: {ok}/{n}")
        print(f"  no-answer questions returning any chunk: {no_answer_with_hits}/{len(NO_ANSWER)}")
        for miss in misses:
            print("  MISS", miss)
    assert recall >= RECALL_TARGET, f"recall@{K} {recall:.0%} below target {RECALL_TARGET:.0%}"


def test_harness_sanity_keyword_query_finds_evidence(workspace: Workspace) -> None:
    """Guards the eval itself: a unique figure must find its chunk, or recall 0% means a bug."""
    w = workspace
    p = publish_corpus(w)
    with w.engine.begin() as connection:
        rows = retrieve(connection, UUID(p), UUID(w.user_id), "fee 184,500", K)
    assert any("fixed implementation fee of USD 184,500" in r["content"] for r in rows)
