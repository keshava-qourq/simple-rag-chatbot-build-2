"""Local vector-index retrieval, scoped to one document (US-012-1).

`search` embeds the question via `app.services.embedder.embed_texts` and
ranks this document's already-embedded chunks against it by cosine
similarity, computed in Python -- the same code path regardless of whether
the `embedding` column is backed by a real pgvector `VECTOR` (Postgres) or a
JSON-encoded fallback (SQLite); see `app.models.VectorType`.

AC-035 is enforced at the query itself, not by filtering results afterwards:
the `WHERE document_id = ...` clause runs before a single chunk is loaded
into Python, so a chunk belonging to any other document is never fetched,
let alone ranked or returned.

`embed_texts` raises `EmbeddingError` -- never a raw provider exception --
on any embedding failure; `search` does not catch it, so the caller (the
`ask` router) decides how to turn that into a response.
"""

import math
import uuid

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Chunk
from app.services.embedder import embed_texts


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def search(db: Session, document_id: uuid.UUID, question: str, settings: Settings) -> list[Chunk]:
    """Return up to `settings.retrieval_top_k` chunks of `document_id`,
    most similar to `question` first.

    Embeds `question` once via `embed_texts`, then scores every chunk that
    belongs to `document_id` (and only those -- the filter is in the SQL
    query) by cosine similarity against its stored embedding, highest
    first.
    """
    [query_embedding] = embed_texts([question], settings)

    chunks = (
        db.query(Chunk)
        .filter(Chunk.document_id == document_id)
        .filter(Chunk.embedding.isnot(None))
        .all()
    )
    if not chunks:
        return []

    scored = [(chunk, _cosine_similarity(query_embedding, chunk.embedding)) for chunk in chunks]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [chunk for chunk, _ in scored[: settings.retrieval_top_k]]
