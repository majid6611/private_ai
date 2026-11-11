"""Text -> vectors through the isolated embedder service, plus the background worker.

Only this backend talks to the embedder (a separate, secret-free container). When it is not
configured or unreachable every function degrades to "no vectors", and callers fall back to
lexical retrieval instead of failing.
"""

import json
import logging
import math
import os
import re
import threading
import urllib.request
from typing import Any

from sqlalchemy import Engine, text

log = logging.getLogger("embeddings")
MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def enabled() -> bool:
    return bool(os.environ.get("EMBEDDER_URL"))


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def embed(texts: list[str], kind: str, timeout: float = 30.0) -> list[list[float]] | None:
    """Unit vectors for texts ("query" or "document" side), or None if unavailable."""
    url = os.environ.get("EMBEDDER_URL")
    if not url or not texts:
        return None
    prefix = "search_query: " if kind == "query" else "search_document: "
    body = json.dumps(
        {"model": MODEL, "input": [prefix + t[:6000] for t in texts], "truncate": True}
    ).encode()
    request = urllib.request.Request(
        url.rstrip("/") + "/api/embed",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _opener.open(request, timeout=timeout) as response:
            vectors = json.loads(response.read(64 * 1024 * 1024))["embeddings"]
        if (
            not isinstance(vectors, list)
            or len(vectors) != len(texts)
            or not all(isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v) for v in vectors)
        ):
            return None
        return [_unit([float(x) for x in v]) for v in vectors]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def cosine(a: list[float], b: list[float]) -> float:
    """Dot product of unit vectors; 0 for mismatched or missing vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


CONTACT = re.compile(r"(\S+@\S+|\+?\d[\d\s().-]{7,}\d|https?://\S+|linkedin\.com\S*|github\.com\S*)", re.I)


def _scrub(text_: str) -> str:
    """Contact details tell a search nothing about what a document is."""
    return CONTACT.sub(" ", text_)


HEADING = re.compile(r"^[A-Za-z][A-Za-z0-9 &/,'’()\-]{2,58}$")


def build_card(title: str, chunk_texts: list[str]) -> str:
    """What a document is about, from its own words: title, opening, and section headings."""
    opening = _scrub(" ".join(chunk_texts[0].split()))[:700] if chunk_texts else ""
    headings: list[str] = []
    for chunk in chunk_texts:
        for line in chunk.splitlines():
            line = line.strip()
            if (
                HEADING.match(line)
                and not line.endswith((".", ","))
                and (line.isupper() or line.istitle() or len(line.split()) <= 4)
                and "@" not in line
                and line not in headings
            ):
                headings.append(line)
    return f"{title}\n{opening}\nSections: {'; '.join(headings[:14])[:500]}"


class EmbeddingWorker(threading.Thread):
    """Fills in passage vectors and document cards for published documents, in the background."""

    def __init__(self, engine: Engine, interval: float = 3.0) -> None:
        super().__init__(daemon=True, name="embedding-worker")
        self.engine = engine
        self.interval = interval
        self.stopping = threading.Event()

    def stop(self) -> None:
        self.stopping.set()

    def run(self) -> None:
        while not self.stopping.wait(self.interval):
            try:
                while self.step() and not self.stopping.is_set():
                    pass
            except Exception:  # noqa: BLE001 - never log document text; retry next tick
                log.warning("embedding step failed; will retry")

    def step(self) -> bool:
        """Do one unit of work; True when something was done (so the caller continues)."""
        return self.embed_passages() or self.build_next_card() or self.embed_cards()

    def embed_passages(self) -> bool:
        with self.engine.begin() as connection:
            rows = connection.execute(
                text("""SELECT k.id,k.content FROM text_chunks k
                JOIN documents d ON d.active_version_id=k.version_id
                WHERE k.embedding IS NULL ORDER BY k.version_id,k.ordinal
                LIMIT 16 FOR UPDATE OF k SKIP LOCKED""")
            ).all()
            if not rows:
                return False
            vectors = embed([r.content for r in rows], "document")
            if vectors is None:
                return False
            for row, vector in zip(rows, vectors):
                connection.execute(
                    text("""UPDATE text_chunks SET embedding=CAST(:v AS real[]),embedding_model=:m
                    WHERE id=:id"""),
                    {"v": vector, "m": MODEL, "id": row.id},
                )
        return True

    def embed_cards(self) -> bool:
        """Cards whose model-written description just arrived (embedding was cleared)."""
        with self.engine.begin() as connection:
            rows = connection.execute(
                text("""SELECT version_id,card_text FROM document_cards WHERE embedding IS NULL
                ORDER BY created_at LIMIT 8 FOR UPDATE SKIP LOCKED""")
            ).all()
            if not rows:
                return False
            vectors = embed([r.card_text for r in rows], "document")
            if vectors is None:
                return False
            for row, vector in zip(rows, vectors):
                connection.execute(
                    text("UPDATE document_cards SET embedding=CAST(:e AS real[]) WHERE version_id=:v"),
                    {"e": vector, "v": row.version_id},
                )
        return True

    def build_next_card(self) -> bool:
        with self.engine.begin() as connection:
            row = connection.execute(
                text("""SELECT d.title,v.id AS version_id FROM documents d
                JOIN document_versions v ON v.id=d.active_version_id
                WHERE NOT EXISTS (SELECT 1 FROM document_cards k WHERE k.version_id=v.id)
                AND EXISTS (SELECT 1 FROM text_chunks t WHERE t.version_id=v.id)
                AND NOT EXISTS (SELECT 1 FROM text_chunks t WHERE t.version_id=v.id AND t.embedding IS NULL)
                ORDER BY d.created_at LIMIT 1 FOR UPDATE OF d SKIP LOCKED""")
            ).first()
            if row is None:
                return False
            chunks = connection.execute(
                text("SELECT content FROM text_chunks WHERE version_id=:v ORDER BY ordinal"),
                {"v": row.version_id},
            ).scalars().all()
            card = build_card(row.title, list(chunks))
            vectors = embed([card], "document")
            if vectors is None:
                return False
            connection.execute(
                text("""INSERT INTO document_cards(version_id,card_text,embedding,embedding_model)
                VALUES (:v,:c,CAST(:e AS real[]),:m) ON CONFLICT DO NOTHING"""),
                {"v": row.version_id, "c": card, "e": vectors[0], "m": MODEL},
            )
        return True
