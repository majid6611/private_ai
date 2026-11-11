"""Decide which documents a question is about, then look only inside them.

Old behaviour: words matched across every passage of every file, so a question about a person's
career could be answered from a contract that shared a word. Now:

1. Every published document has a card (a model-written description plus headings) and vectors.
2. A question is scored against the cards, then the best passages of the top few documents.
3. A decisive winner, an explicit user choice, a one-document project, or a settled chat topic
   goes straight to retrieval. Otherwise the chat model picks among the top candidates (an
   allow-list stored on the task) or says it is unsure, in which case the user is asked.

Measured on 59 questions over 8 documents: vector scores alone picked the right file 81% of the
time (19% silent mistakes); vectors plus the model's choice 88% right, 3% silently wrong and 8%
"unsure" (asks the user).
"""

import os
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.ai import CONTEXT_CHUNKS, STOPWORDS, query_terms, retrieve, within_budget
from app.embeddings import cosine, embed

DECISIVE_GAP = float(os.environ.get("ROUTE_DECISIVE_GAP", "0.10"))
SHORTLIST = 4  # the true document was in the top four for 97% of measured questions
CARD_PREFILTER = 8  # passages are only scored for the best few documents, not for all of them
FOCUS_BONUS = 0.06
NAME_BONUS = 0.12
NAME_BONUS_MAX = 0.24
MAX_OPTIONS = 3

QUESTION_WORDS = frozenset(
    "what who when where why how does can could which the and please send tell give list show find "
    "is are did do will would should may let lets also just".split()
)
MULTI_DOCUMENT = re.compile(
    r"\b(all (the )?(documents|files|contracts|resumes|policies|agreements)|every (document|file|contract)"
    r"|each (document|file|contract)|compare|across|both documents)\b",
    re.I,
)
LIST_QUESTION = re.compile(
    r"\b(all|every|each|list|everything|names? of|which (companies|projects|skills|languages))\b", re.I
)
PRONOUNS = frozenset(
    "it its they them their that this those these he she his her also too another else more same there then".split()
)
LIST_LIMIT = int(os.environ.get("AI_LIST_CHUNKS", "8"))
LIST_EVIDENCE_CHARS = int(os.environ.get("AI_LIST_EVIDENCE_CHARS", "4800"))
NOT_FOUND = (
    "I couldn't find anything about that in the documents available to you. "
    "If you mean a particular document or person, mention them and I'll look there."
)

VISIBLE = """d.project_id=:p AND (NOT d.restricted OR
    EXISTS(SELECT 1 FROM document_grants g WHERE g.document_id=d.id AND g.user_id=:u))"""


@dataclass
class Route:
    kind: str  # "docs" | "llm" | "lexical"
    documents: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)  # for "llm"
    ranking: list[dict[str, Any]] = field(default_factory=list)
    query_vector: list[float] | None = None


@dataclass
class Plan:
    sources: list[dict[str, Any]]
    route: str
    answer: str | None = None  # set when no model call is needed
    clarification: list[dict[str, str]] | None = None
    focus: list[UUID] | None = None  # None: leave the chat's focus unchanged
    candidates: list[dict[str, Any]] | None = None  # set: the model must route first
    effective: str | None = None


def is_list_question(question: str) -> bool:
    return bool(LIST_QUESTION.search(question))


def proper_nouns(question: str) -> list[str]:
    words = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", question)
    return [w.lower() for w in words if w.lower() not in QUESTION_WORDS and w.lower() not in STOPWORDS]


ANAPHORA = frozenset("it its that this those these them there then same another else more also too".split())


def refers_back(current: str) -> str:
    """How much a question leans on the previous one.

    "bare": almost nothing to search for ("why?", "who signed it?", "what about that?"): the earlier
    turn is needed. "pronoun": a complete question that says he/she/they, so the chat's topic
    carries on but the question stands alone. "standalone": neither.
    """
    words = set(re.findall(r"[a-z]+", current.lower()))
    terms = len(query_terms(current))
    if terms <= 1 or (terms <= 3 and words & ANAPHORA):
        return "bare"
    return "pronoun" if words & PRONOUNS else "standalone"


def visible_cards(connection: Connection, project_id: UUID, user_id: UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(f"""SELECT d.id,d.title,k.card_text,k.description,k.embedding FROM documents d
        JOIN document_cards k ON k.version_id=d.active_version_id
        WHERE {VISIBLE} AND k.embedding IS NOT NULL"""),  # noqa: S608 - fixed fragment
        {"p": project_id, "u": user_id},
    ).mappings()
    return [dict(r) for r in rows]


def visible_documents(
    connection: Connection, project_id: UUID, user_id: UUID, ids: list[UUID]
) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(f"""SELECT d.id,d.title FROM documents d
        WHERE {VISIBLE} AND d.active_version_id IS NOT NULL AND d.id = ANY(CAST(:ids AS uuid[]))"""),  # noqa: S608
        {"p": project_id, "u": user_id, "ids": ids},
    ).mappings()
    return [dict(r) for r in rows]


def best_passage_scores(
    connection: Connection, project_id: UUID, user_id: UUID, qvec: list[float], doc_ids: list[UUID]
) -> dict[UUID, float]:
    rows = connection.execute(
        text(f"""SELECT d.id AS doc,c.embedding FROM text_chunks c
        JOIN documents d ON d.active_version_id=c.version_id
        WHERE {VISIBLE} AND d.id = ANY(CAST(:docs AS uuid[])) AND c.embedding IS NOT NULL"""),  # noqa: S608
        {"p": project_id, "u": user_id, "docs": doc_ids},
    ).all()
    best: dict[UUID, float] = {}
    for doc, embedding in rows:
        score = cosine(qvec, list(embedding))
        if score > best.get(doc, -1.0):
            best[doc] = score
    return best


def route_question(
    connection: Connection,
    project_id: UUID,
    user_id: UUID,
    question: str,
    focus_ids: list[UUID] | None = None,
    explicit_ids: list[UUID] | None = None,
    follows_up: bool = False,
) -> Route:
    """Which document(s) is this about? A decisive answer, a candidate list, or "lexical"."""
    if explicit_ids:
        chosen = visible_documents(connection, project_id, user_id, explicit_ids)
        return Route("docs", [{"id": d["id"], "title": d["title"], "score": 1.0} for d in chosen])
    focus = [f for f in (focus_ids or [])]
    if focus and follows_up:
        settled = visible_documents(connection, project_id, user_id, focus)
        if settled:
            return Route("docs", [{"id": d["id"], "title": d["title"], "score": 1.0} for d in settled])
    vectors = embed([question], "query")
    cards = visible_cards(connection, project_id, user_id) if vectors else []
    if not vectors or not cards:
        return Route("lexical")
    qvec = vectors[0]
    names = proper_nouns(question)
    by_card = sorted(cards, key=lambda c: -cosine(qvec, list(c["embedding"])))[:CARD_PREFILTER]
    passages = best_passage_scores(connection, project_id, user_id, qvec, [c["id"] for c in by_card])
    ranking = []
    for card in by_card:
        haystack = (card["title"] + " " + card["card_text"]).lower()
        bonus = min(NAME_BONUS_MAX, NAME_BONUS * sum(1 for n in names if n in haystack))
        if card["id"] in focus:
            bonus += FOCUS_BONUS
        score = 0.5 * cosine(qvec, list(card["embedding"])) + 0.5 * passages.get(card["id"], 0.0)
        ranking.append(
            {
                "id": card["id"],
                "title": card["title"],
                "description": card["description"] or card["card_text"][:400],
                "score": round(score + bonus, 4),
            }
        )
    ranking.sort(key=lambda r: (-r["score"], r["title"]))
    if MULTI_DOCUMENT.search(question):
        return Route("docs", ranking[:MAX_OPTIONS], ranking=ranking, query_vector=qvec)
    if len(ranking) == 1 or ranking[0]["score"] - ranking[1]["score"] >= DECISIVE_GAP:
        return Route("docs", [ranking[0]], ranking=ranking, query_vector=qvec)
    return Route("llm", candidates=ranking[:SHORTLIST], ranking=ranking, query_vector=qvec)


def retrieve_in_documents(
    connection: Connection,
    project_id: UUID,
    user_id: UUID,
    question: str,
    document_ids: list[UUID],
    qvec: list[float] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Best passages inside the chosen documents: word matches and meaning matches, fused."""
    wanted = limit * 2
    lexical = retrieve(connection, project_id, user_id, question, wanted, document_ids)
    semantic: list[dict[str, Any]] = []
    if qvec is not None:
        rows = connection.execute(
            text(f"""SELECT c.id,c.content,c.version_id,c.start_offset,c.end_offset,c.page_start,
            c.page_end,d.id AS document_id,d.title,d.publication_revision,c.embedding
            FROM text_chunks c JOIN documents d ON d.active_version_id=c.version_id
            WHERE {VISIBLE} AND d.id = ANY(CAST(:docs AS uuid[])) AND c.embedding IS NOT NULL"""),  # noqa: S608
            {"p": project_id, "u": user_id, "docs": document_ids},
        ).mappings()
        scored = sorted(
            ((cosine(qvec, list(r["embedding"])), dict(r)) for r in rows), key=lambda x: -x[0]
        )
        for _, row in scored[:wanted]:
            row.pop("embedding", None)
            semantic.append(row)
    # Reciprocal rank fusion: a passage ranked well by either method rises.
    fused: dict[Any, float] = {}
    by_id: dict[Any, dict[str, Any]] = {}
    for ranked in (lexical, semantic):
        for position, row in enumerate(ranked):
            fused[row["id"]] = fused.get(row["id"], 0.0) + 1.0 / (10 + position)
            by_id.setdefault(row["id"], row)
    order = sorted(fused, key=lambda i: (-fused[i], by_id[i]["title"], by_id[i].get("start_offset", 0)))
    return [by_id[i] for i in order[:limit]]


def gather_sources(
    connection: Connection, project_id: UUID, user_id: UUID, current: str, previous: str | None
) -> list[dict[str, Any]]:
    """Word-based fallback (embedder unavailable): search the new question first, then borrow."""
    primary = retrieve(connection, project_id, user_id, current, CONTEXT_CHUNKS)
    lean = refers_back(current) if previous else "standalone"
    if lean == "standalone" or (lean == "bare" and primary):
        return primary
    context = retrieve(connection, project_id, user_id, f"{current} {previous}", CONTEXT_CHUNKS)
    seen = {row["id"] for row in primary}
    return (primary + [row for row in context if row["id"] not in seen])[:CONTEXT_CHUNKS]


def clarify_text(options: list[dict[str, Any]]) -> str:
    names = "; ".join(f"“{o['title']}”" for o in options)
    return f"More than one document could match your question: {names}. Which one do you mean?"


def sources_in_documents(
    connection: Connection,
    project_id: UUID,
    user_id: UUID,
    question: str,
    ids: list[UUID],
    qvec: list[float] | None,
) -> list[dict[str, Any]]:
    listing = is_list_question(question)
    if qvec is None:
        vectors = embed([question], "query")
        qvec = vectors[0] if vectors else None
    rows = retrieve_in_documents(
        connection, project_id, user_id, question, ids, qvec, LIST_LIMIT if listing else CONTEXT_CHUNKS
    )
    return within_budget(rows, LIST_EVIDENCE_CHARS if listing else None)


def plan_answer(
    connection: Connection,
    project_id: UUID,
    user_id: UUID,
    question: str,
    previous: str | None = None,
    focus_ids: list[UUID] | None = None,
    explicit_ids: list[UUID] | None = None,
) -> Plan:
    """Route the question, then collect the evidence the model may use (or defer to the model)."""
    lean = refers_back(question) if previous else "standalone"
    # A pronoun ("he", "it") means the same topic carries on, so a settled document is kept. But
    # only a question with almost nothing to search for ("what about it?") borrows the previous
    # question's words; borrowing them for a complete question ("and what languages does he
    # know?") pulled the earlier passages back in and even made it look like a "list all" question.
    effective = f"{question} {previous}" if lean == "bare" else question
    route = route_question(
        connection, project_id, user_id, effective, focus_ids, explicit_ids, follows_up=lean != "standalone"
    )
    if route.kind == "lexical":
        return Plan(within_budget(gather_sources(connection, project_id, user_id, question, previous)), "lexical")
    if route.kind == "llm":
        return Plan(
            [],
            "llm",
            candidates=[
                {"document_id": str(c["id"]), "title": c["title"], "description": c["description"]}
                for c in route.candidates
            ],
            effective=effective,
        )
    ids = [d["id"] for d in route.documents]
    sources = sources_in_documents(connection, project_id, user_id, effective, ids, route.query_vector)
    return Plan(sources, "docs", focus=ids)
