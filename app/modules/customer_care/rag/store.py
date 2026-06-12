"""Vector store backend dispatch.

- Postgres + pgvector: cast embedding column to `vector(N)` and use
  `<=>` (cosine distance) for ORDER BY. Requires `CREATE EXTENSION vector`.
- SQLite (and any non-pgvector dialect): load all chunks for the tenant and
  compute cosine in Python. Fine for dev/test and small KBs (~1k chunks);
  production with `RAG_REQUIRED=true` refuses SQLite at startup.

Embedding storage format on disk: JSON-encoded list[float] in the `text` column,
identical for both backends so a single migration works everywhere.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass

from sqlalchemy import select, text as sa_text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import KnowledgeChunk
from app.tenancy import current_tenant_id

logger = logging.getLogger(__name__)


@dataclass
class ScoredChunk:
    chunk_id: str
    document_id: str
    text: str
    position: int
    score: float  # higher = better (cosine similarity in [-1, 1])


def encode_embedding(vec: list[float]) -> str:
    return json.dumps(vec, separators=(",", ":"), ensure_ascii=False)


def decode_embedding(stored: str | None) -> list[float] | None:
    if not stored:
        return None
    try:
        out = json.loads(stored)
        if isinstance(out, list):
            return [float(x) for x in out]
    except Exception:
        return None
    return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _is_postgres(db: Session) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def _has_pgvector(db: Session) -> bool:
    if not _is_postgres(db):
        return False
    try:
        row = db.execute(
            sa_text("SELECT 1 FROM pg_extension WHERE extname = 'vector' LIMIT 1")
        ).scalar()
        return bool(row)
    except Exception:
        return False


def search_top_k(
    db: Session, query_vec: list[float], k: int = 5, *, min_score: float = 0.0
) -> list[ScoredChunk]:
    """Return the top-k chunks by cosine similarity for the current tenant."""
    if not query_vec or k <= 0:
        return []

    # Tenant filter is auto-applied via app.tenancy event listener; no need
    # to specify .where(KnowledgeChunk.tenant_id == ...) explicitly.

    if _has_pgvector(db):
        return _search_pgvector(db, query_vec, k, min_score)
    return _search_inmemory(db, query_vec, k, min_score)


def _search_pgvector(
    db: Session, query_vec: list[float], k: int, min_score: float
) -> list[ScoredChunk]:
    # We stored embeddings as JSON text. For pgvector queries we need a real
    # vector. Two options: (a) keep a parallel `embedding_vec vector(N)`
    # column maintained on insert, or (b) cast at query time. We use (a)
    # via a generated column added in migration 0003_rag if pgvector is
    # available. For now, fall back to in-memory if the parallel column is
    # missing — keeps the system working before/after migration.
    cfg = settings()
    dim = int(cfg.rag_embedding_dim or 1536)
    try:
        # Use raw SQL because the embedding_vec column type is dynamic
        rows = db.execute(
            sa_text(
                """
                SELECT id, document_id, text, position,
                       1 - (embedding_vec <=> CAST(:q AS vector)) AS score
                FROM knowledge_chunks
                WHERE tenant_id = :tenant
                  AND embedding_vec IS NOT NULL
                ORDER BY embedding_vec <=> CAST(:q AS vector)
                LIMIT :k
                """
            ),
            {"q": "[" + ",".join(str(x) for x in query_vec) + "]", "k": k, "tenant": current_tenant_id()},
        ).fetchall()
    except Exception as exc:
        logger.warning("pgvector_search_failed_falling_back err=%s", exc)
        return _search_inmemory(db, query_vec, k, min_score)

    out: list[ScoredChunk] = []
    for r in rows:
        score = float(r[4])
        if score >= min_score:
            out.append(ScoredChunk(chunk_id=r[0], document_id=r[1], text=r[2], position=r[3], score=score))
    _ = dim  # kept for future per-tenant dim tracking
    return out


def _search_inmemory(
    db: Session, query_vec: list[float], k: int, min_score: float
) -> list[ScoredChunk]:
    # Tenant filter is auto-applied; this select reads only this tenant's chunks.
    rows = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.embedding.is_not(None))).all()
    scored: list[ScoredChunk] = []
    for r in rows:
        vec = decode_embedding(r.embedding)
        if not vec:
            continue
        s = _cosine(query_vec, vec)
        if s >= min_score:
            scored.append(
                ScoredChunk(chunk_id=r.id, document_id=r.document_id, text=r.text, position=r.position, score=s)
            )
    scored.sort(key=lambda x: x.score, reverse=True)
    return scored[:k]
