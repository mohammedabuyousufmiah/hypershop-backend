"""Customer-care extension routes: knowledge-base + CSAT.

Kept separate from the main router for module clarity. Included into
the same ``customer_care_router`` in ``__init__.py``.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import Field
from sqlalchemy import desc, select, text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.validation import StrictModel
from app.modules.customer_care import outbound
from app.modules.customer_care.models import (
    CCConversation,
    CCCsatSurvey,
    CCKnowledgeChunk,
    CCKnowledgeDocument,
)

_AGENT = "customercare.agent"
_RAG_ADMIN = "customercare.rag.admin"

# NOTE: no prefix — this router is included into customer_care_router
# under the main router's /customer-care prefix in __init__.py.
router = APIRouter(tags=["customer-care"])
_log = get_logger("hypershop.customer_care.kb_csat")


# ============================================================== KB SCHEMAS
class KbDocumentIngest(StrictModel):
    title: str = Field(..., min_length=1, max_length=255)
    body: str = Field(..., min_length=1, max_length=200_000)
    source_type: str = Field(default="text", max_length=40)
    source_url: str | None = Field(default=None, max_length=2048)
    language: str | None = Field(default=None, max_length=8)


class KbDocumentResponse(StrictModel):
    id: UUID
    title: str
    source_type: str
    source_url: str | None
    language: str | None
    chunk_count: int
    is_active: bool
    indexed_at: datetime | None
    created_at: datetime


class KbSearchResult(StrictModel):
    document_id: UUID
    document_title: str
    chunk_id: UUID
    text: str
    position: int
    score: float


class KbStats(StrictModel):
    documents: int
    active_documents: int
    chunks: int


# ============================================================== KB ROUTES
def _simple_chunk(body: str, max_chars: int = 1600, overlap_chars: int = 200) -> list[str]:
    """Naive character-based chunker — good enough for FAQ docs and
    avoids an OpenAI tokenizer dep. ~1600 chars ≈ 400 tokens.
    """
    body = body.strip()
    if not body:
        return []
    out: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        end = min(i + max_chars, n)
        # Try to break on a paragraph / sentence boundary
        if end < n:
            for sep in ("\n\n", ". ", "? ", "! ", "।", "\n"):
                pos = body.rfind(sep, i, end)
                if pos > i + (max_chars // 2):
                    end = pos + len(sep)
                    break
        out.append(body[i:end].strip())
        if end >= n:
            break
        i = max(end - overlap_chars, i + 1)
    return [c for c in out if c]


@router.post(
    "/kb/documents",
    response_model=KbDocumentResponse,
    status_code=201,
    summary="Ingest a knowledge-base document (FAQ, policy, product spec)",
    dependencies=[Depends(requires_permission(_RAG_ADMIN))],
)
async def kb_ingest(
    body: KbDocumentIngest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> KbDocumentResponse:
    """Chunks the body and stores it. Embedding generation is deferred
    to a background re-index pass (no OpenAI call inline) so the
    upload returns fast and idempotently.
    """
    digest = hashlib.sha256(body.body.encode("utf-8")).hexdigest()
    async with uow.transactional() as session:
        # Idempotency on body hash — if an active doc with the same
        # body already exists we return it.
        existing = (
            await session.execute(
                select(CCKnowledgeDocument).where(
                    CCKnowledgeDocument.body_sha256 == digest,
                    CCKnowledgeDocument.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if existing:
            return KbDocumentResponse(
                id=existing.id, title=existing.title,
                source_type=existing.source_type, source_url=existing.source_url,
                language=existing.language, chunk_count=existing.chunk_count,
                is_active=existing.is_active, indexed_at=existing.indexed_at,
                created_at=existing.created_at,
            )
        doc = CCKnowledgeDocument(
            title=body.title, source_type=body.source_type,
            source_url=body.source_url, language=body.language,
            body=body.body, body_sha256=digest,
        )
        session.add(doc)
        await session.flush()
        # Chunk + generate embeddings (inline; for big docs a
        # background job is better — see deferred work in changelog).
        chunks = _simple_chunk(body.body)
        embeddings: list[list[float]] | None = None
        if chunks:
            embeddings = await outbound.embed_texts(chunks)
        from app.modules.customer_care.config import settings as _cc_settings
        cfg = _cc_settings()
        emb_dim: int | None = None
        emb_model = cfg.openai_embedding_model if embeddings else None
        if embeddings and embeddings[0]:
            emb_dim = len(embeddings[0])
        for i, ch in enumerate(chunks):
            ch_hash = hashlib.sha256(ch.encode("utf-8")).hexdigest()
            stored_emb = (
                json.dumps(embeddings[i])
                if (embeddings and i < len(embeddings))
                else None
            )
            session.add(
                CCKnowledgeChunk(
                    document_id=doc.id, position=i,
                    text_body=ch, text_hash=ch_hash,
                    token_count=len(ch) // 4,
                    embedding=stored_emb,
                    embedding_model=emb_model,
                    embedding_dim=emb_dim,
                )
            )
        doc.chunk_count = len(chunks)
        doc.embedding_model = emb_model
        doc.embedding_dim = emb_dim
        doc.indexed_at = datetime.now(timezone.utc)
        await session.flush()
        _log.info(
            "kb_doc_ingested",
            document_id=str(doc.id),
            chunks=len(chunks),
            embedded=bool(embeddings),
        )
        return KbDocumentResponse(
            id=doc.id, title=doc.title, source_type=doc.source_type,
            source_url=doc.source_url, language=doc.language,
            chunk_count=doc.chunk_count, is_active=doc.is_active,
            indexed_at=doc.indexed_at, created_at=doc.created_at,
        )


@router.get(
    "/kb/documents",
    response_model=list[KbDocumentResponse],
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def kb_list(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: int = Query(default=50, ge=1, le=200),
) -> list[KbDocumentResponse]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                select(CCKnowledgeDocument)
                .order_by(desc(CCKnowledgeDocument.created_at))
                .limit(limit)
            )
        ).scalars().all()
        return [
            KbDocumentResponse(
                id=d.id, title=d.title, source_type=d.source_type,
                source_url=d.source_url, language=d.language,
                chunk_count=d.chunk_count, is_active=d.is_active,
                indexed_at=d.indexed_at, created_at=d.created_at,
            )
            for d in rows
        ]


@router.get(
    "/kb/search",
    response_model=list[KbSearchResult],
    summary="Semantic search (embedding cosine) with LIKE fallback",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def kb_search(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    q: str = Query(..., min_length=1, max_length=500),
    k: int = Query(default=5, ge=1, le=25),
) -> list[KbSearchResult]:
    """Two-stage:
    1. If OpenAI embeddings are configured, embed the query and rank
       candidate chunks by cosine similarity against their stored
       embeddings. In-memory cosine — fine for thousands of chunks.
    2. If embeddings aren't configured OR no chunks have embeddings
       yet, fall back to case-insensitive LIKE.
    """
    q_embedding: list[float] | None = None
    embs = await outbound.embed_texts([q])
    if embs and embs[0]:
        q_embedding = embs[0]

    async with uow.transactional() as session:
        if q_embedding:
            # Pull every embedded chunk (cap at 5000 for safety).
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT
                            d.id AS doc_id, d.title, c.id AS chunk_id,
                            c.text, c.position, c.embedding
                        FROM cc_knowledge_chunks c
                        JOIN cc_knowledge_documents d ON d.id = c.document_id
                        WHERE d.is_active = true
                          AND c.embedding IS NOT NULL
                        LIMIT 5000
                        """,
                    ),
                )
            ).all()
            scored: list[tuple[float, KbSearchResult]] = []
            for r in rows:
                try:
                    vec = json.loads(r[5])
                except Exception:  # noqa: BLE001
                    continue
                score = outbound.cosine_similarity(q_embedding, vec)
                scored.append(
                    (score, KbSearchResult(
                        document_id=r[0], document_title=r[1], chunk_id=r[2],
                        text=r[3][:500], position=r[4], score=round(score, 4),
                    ))
                )
            scored.sort(key=lambda x: x[0], reverse=True)
            top = [item for _, item in scored[:k]]
            if top:
                return top
            # Fall through to LIKE if embeddings exist but the query
            # didn't match anything semantically (rare).

        # Fallback: LIKE
        rows = (
            await session.execute(
                text(
                    """
                    SELECT
                        d.id AS doc_id, d.title, c.id AS chunk_id,
                        c.text, c.position
                    FROM cc_knowledge_chunks c
                    JOIN cc_knowledge_documents d ON d.id = c.document_id
                    WHERE d.is_active = true
                      AND lower(c.text) LIKE lower(:like)
                    ORDER BY c.position
                    LIMIT :k
                    """,
                ),
                {"like": f"%{q}%", "k": k},
            )
        ).all()
        return [
            KbSearchResult(
                document_id=r[0], document_title=r[1], chunk_id=r[2],
                text=r[3][:500], position=r[4], score=1.0,
            )
            for r in rows
        ]


@router.delete(
    "/kb/documents/{doc_id}",
    response_model=dict,
    dependencies=[Depends(requires_permission(_RAG_ADMIN))],
)
async def kb_delete(
    doc_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    """Soft-delete a KB document (chunks retained for audit)."""
    async with uow.transactional() as session:
        doc = (
            await session.execute(
                select(CCKnowledgeDocument).where(CCKnowledgeDocument.id == doc_id)
            )
        ).scalar_one_or_none()
        if doc is None:
            raise NotFoundError("Document not found")
        doc.is_active = False
        return {"id": str(doc_id), "is_active": False}


@router.get(
    "/kb/stats",
    response_model=KbStats,
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def kb_stats(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> KbStats:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM cc_knowledge_documents) AS docs,
                        (SELECT COUNT(*) FROM cc_knowledge_documents
                         WHERE is_active) AS active,
                        (SELECT COUNT(*) FROM cc_knowledge_chunks) AS chunks
                    """,
                )
            )
        ).first()
        return KbStats(
            documents=int(r[0] or 0),
            active_documents=int(r[1] or 0),
            chunks=int(r[2] or 0),
        )


# ============================================================== CSAT SCHEMAS
class CsatStartResponse(StrictModel):
    survey_id: UUID
    survey_token: str
    submit_url: str


class CsatSubmitRequest(StrictModel):
    token: str = Field(..., min_length=8, max_length=64)
    score: int = Field(..., ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2048)


class CsatSubmitResponse(StrictModel):
    score: int
    received_at: datetime


class CsatSummary(StrictModel):
    surveys_sent: int
    surveys_responded: int
    avg_score: float | None
    days: int


# ============================================================== CSAT ROUTES
@router.post(
    "/conversations/{conv_id}/csat/start",
    response_model=CsatStartResponse,
    status_code=201,
    summary="Generate a CSAT survey token + send the customer a 1-5 rating prompt",
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def csat_start(
    conv_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CsatStartResponse:
    from app.modules.customer_care.config import settings
    async with uow.transactional() as session:
        conv = (
            await session.execute(
                select(CCConversation).where(CCConversation.id == conv_id)
            )
        ).scalar_one_or_none()
        if conv is None:
            raise NotFoundError("Conversation not found")
        survey_token = secrets.token_urlsafe(24)
        survey = CCCsatSurvey(
            conversation_id=conv.id,
            customer_id=conv.customer_id,
            agent_id=conv.agent_id,
            survey_token=survey_token,
            sent_at=datetime.now(timezone.utc),
        )
        session.add(survey)
        await session.flush()
        # Pull phone for outbound send
        row = (
            await session.execute(
                text("SELECT phone FROM users WHERE id = :uid"),
                {"uid": conv.customer_id},
            )
        ).first()
        phone = row[0] if row else None
        survey_id = survey.id

    base = settings().base_url.rstrip("/")
    submit_url = f"{base}/customercare/csat?token={survey_token}"

    if phone:
        body = (
            "Hypershop: How was our service? Rate 1–5 and reply, or click "
            f"the link: {submit_url}"
        )
        await outbound.send_whatsapp_text(to_phone=phone, body=body)
    return CsatStartResponse(
        survey_id=survey_id,
        survey_token=survey_token,
        submit_url=submit_url,
    )


@router.post(
    "/csat/submit",
    response_model=CsatSubmitResponse,
    summary="Customer submits CSAT (PUBLIC — token-authenticated)",
)
async def csat_submit(
    body: CsatSubmitRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> CsatSubmitResponse:
    """No JWT required — token in the request body is the auth.

    Idempotent: a second submit with the same token overwrites score
    + comment but doesn't create a new row.
    """
    async with uow.transactional() as session:
        survey = (
            await session.execute(
                select(CCCsatSurvey).where(CCCsatSurvey.survey_token == body.token)
            )
        ).scalar_one_or_none()
        if survey is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invalid or expired survey token",
            )
        survey.score = body.score
        survey.comment = body.comment
        survey.responded_at = datetime.now(timezone.utc)
        survey.status = "responded"
        return CsatSubmitResponse(
            score=body.score,
            received_at=survey.responded_at,
        )


@router.get(
    "/csat/summary",
    response_model=CsatSummary,
    dependencies=[Depends(requires_permission(_AGENT))],
)
async def csat_summary(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=30, ge=1, le=365),
) -> CsatSummary:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                text(
                    f"""
                    SELECT
                        COUNT(*) FILTER (WHERE sent_at IS NOT NULL) AS sent,
                        COUNT(*) FILTER (WHERE responded_at IS NOT NULL) AS resp,
                        AVG(score) FILTER (WHERE responded_at IS NOT NULL) AS avg
                    FROM cc_csat_surveys
                    WHERE sent_at >= now() - INTERVAL '{int(days)} days'
                    """,
                )
            )
        ).first()
        return CsatSummary(
            surveys_sent=int(r[0] or 0),
            surveys_responded=int(r[1] or 0),
            avg_score=float(r[2]) if r[2] is not None else None,
            days=days,
        )
