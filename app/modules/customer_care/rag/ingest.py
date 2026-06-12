"""Document ingestion: title+body → chunks → embeddings → persist."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import KnowledgeChunk, KnowledgeDocument
from app.rag.chunker import chunk_text
from app.rag.embeddings import embed_batch
from app.rag.store import encode_embedding
from app.tenancy import current_tenant_id

logger = logging.getLogger(__name__)


async def ingest_text(
    db: Session,
    *,
    title: str,
    body: str,
    source_type: str = "text",
    source_url: str | None = None,
    language: str | None = None,
) -> KnowledgeDocument:
    """Idempotent ingest: same body_sha256 → returns existing doc unchanged.
    Caller is responsible for the surrounding session/commit."""
    cfg = settings()
    body_clean = (body or "").strip()
    if not body_clean:
        raise ValueError("body cannot be empty")
    if not title.strip():
        raise ValueError("title cannot be empty")

    body_sha = hashlib.sha256(body_clean.encode("utf-8")).hexdigest()
    tenant = current_tenant_id()

    existing = db.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.body_sha256 == body_sha)
    )
    if existing:
        logger.info("rag_ingest_skipped_duplicate doc_id=%s", existing.id)
        return existing

    doc = KnowledgeDocument(
        tenant_id=tenant,
        title=title.strip()[:255],
        source_type=source_type,
        source_url=source_url,
        body=body_clean,
        body_sha256=body_sha,
        language=language,
        is_active=True,
    )
    db.add(doc)
    db.flush()

    chunks = chunk_text(
        body_clean,
        max_tokens=int(cfg.rag_chunk_max_tokens or 400),
        overlap_tokens=int(cfg.rag_chunk_overlap_tokens or 60),
    )
    if not chunks:
        logger.warning("rag_ingest_produced_no_chunks doc_id=%s", doc.id)
        doc.chunk_count = 0
        doc.indexed_at = datetime.utcnow()
        db.commit()
        return doc

    embeddings = await embed_batch([c.text for c in chunks])
    for chunk, emb in zip(chunks, embeddings):
        db.add(
            KnowledgeChunk(
                tenant_id=tenant,
                document_id=doc.id,
                position=chunk.position,
                text=chunk.text,
                text_hash=chunk.text_hash,
                token_count=chunk.token_count,
                embedding=encode_embedding(emb.vector),
                embedding_model=emb.model,
                embedding_dim=emb.dim,
            )
        )

    doc.chunk_count = len(chunks)
    doc.embedding_model = embeddings[0].model if embeddings else None
    doc.embedding_dim = embeddings[0].dim if embeddings else None
    doc.indexed_at = datetime.utcnow()
    db.commit()
    logger.info("rag_ingest_ok doc_id=%s chunks=%d model=%s", doc.id, len(chunks), doc.embedding_model)
    return doc


def delete_document(db: Session, doc_id: str) -> bool:
    doc = db.get(KnowledgeDocument, doc_id)
    if not doc:
        return False
    db.execute(
        KnowledgeChunk.__table__.delete().where(KnowledgeChunk.document_id == doc.id)
    )
    db.delete(doc)
    db.commit()
    return True
