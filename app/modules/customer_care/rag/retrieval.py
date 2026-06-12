"""Query → embed → top-k → format prompt context."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.rag.embeddings import embed_one
from app.rag.store import search_top_k

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    text: str
    position: int
    score: float


async def retrieve(
    db: Session, query: str, *, k: int | None = None, min_score: float | None = None
) -> list[RetrievedChunk]:
    cfg = settings()
    if not query.strip():
        return []
    k = k or int(cfg.rag_retrieval_top_k or 5)
    min_score = min_score if min_score is not None else float(cfg.rag_min_score or 0.2)

    try:
        emb = await embed_one(query)
    except Exception:
        logger.exception("rag_retrieve_embedding_failed")
        return []
    raw = search_top_k(db, emb.vector, k=k, min_score=min_score)
    return [
        RetrievedChunk(
            chunk_id=r.chunk_id,
            document_id=r.document_id,
            text=r.text,
            position=r.position,
            score=r.score,
        )
        for r in raw
    ]


def format_context(chunks: list[RetrievedChunk], *, max_chars: int = 2400) -> str:
    """Pack retrieved chunks into a prompt-ready text block, capped to keep
    LLM input cost predictable. Each chunk is prefixed with `[doc:N]` so the
    LLM can cite back."""
    if not chunks:
        return ""
    lines: list[str] = []
    used = 0
    for i, c in enumerate(chunks, 1):
        prefix = f"[{i}] (score={c.score:.2f}) "
        body = c.text.strip()
        chunk_text = prefix + body
        if used + len(chunk_text) > max_chars and lines:
            break
        lines.append(chunk_text)
        used += len(chunk_text) + 1
    return "\n\n".join(lines)
