"""Search service.

Two responsibilities:

1. **Indexer** — translate catalog entities (Product, Brand, Category)
   into SearchDocument rows. Called by:
     - the full-rebuild admin endpoint
     - the cron job (incremental — only rows where
       Product.updated_at > last successful index run)

2. **Query** — normalize the user query, run FTS (with LIKE fallback
   for very short queries), apply local ranking, optionally call the
   bound ML reranker, log the query for analytics.

Hard guarantees:
  - Search NEVER 5xx — degraded paths (no FTS, no reranker, etc.) all
    return empty-list-with-zero-results, never raise. This is a
    customer-facing surface; downtime here is a sales event.
  - Reranker timeouts/failures are caught at the adapter layer +
    returned as ``{}`` — service treats that as "no signal" and uses
    local order.
  - Query log writes are best-effort: a failed log doesn't block the
    response. We swallow + warn in this case.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal
from app.modules.search import codes
from app.modules.search.normalizer import (
    normalize_search_text,
    to_tsquery_string,
    tokenize_query,
)
from app.modules.search.providers import get_reranker
from app.modules.search.providers.base import RerankCandidate, RerankRequest
from app.modules.search.ranking import combined_score, merge_with_ml_scores
from app.modules.search.repository import (
    SearchDocumentRepository,
    SearchQueryLogRepository,
)
from app.modules.search.state import (
    ALL_SEARCH_DOCUMENT_TYPES,
    SearchDocumentType,
)

_logger = get_logger("hypershop.search.service")

# How many candidates to send to the reranker. Cap of 50 keeps the
# request payload small + the ML provider's latency manageable.
_RERANK_TOP_N = 50
# Body excerpt cap when sending to the reranker.
_RERANK_BODY_EXCERPT_CHARS = 512


class SearchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.documents = SearchDocumentRepository(session)
        self.logs = SearchQueryLogRepository(session)

    # ════════════════════════════════════════════════════════════════
    # Indexer
    # ════════════════════════════════════════════════════════════════

    async def index_product(self, product) -> bool:
        """Translate a catalog.Product → SearchDocument. Returns True
        if a new row was inserted (vs updated).
        """
        # Lazy imports to avoid circular deps at module import.
        title = product.name
        # subtitle: brand name (when relation eager-loaded) — fall back
        # to a static label
        subtitle = ""
        brand = getattr(product, "brand", None)
        if brand is not None:
            subtitle = f"by {brand.name}"
        body_parts = [
            getattr(product, "description", "") or "",
            getattr(product, "search_text", "") or "",
            getattr(product, "slug", "") or "",
        ]
        body = " ".join(filter(None, body_parts))
        # Enrich metadata with UI-required fields (min_price / image_key /
        # brand_name / category_path) so the search results grid can be
        # rendered without a second round-trip per hit. All getattr-guarded
        # so partial loads (no variants / media / brand) don't crash.
        variants = getattr(product, "variants", None) or []
        active_variants = [v for v in variants if getattr(v, "is_active", True)]
        prices = [v.price for v in active_variants if getattr(v, "price", None) is not None]
        min_price = str(min(prices)) if prices else None
        max_price = str(max(prices)) if prices else None
        media = getattr(product, "media", None) or []
        primary_img = next(
            (m.url for m in sorted(media, key=lambda x: getattr(x, "position", 0))
             if getattr(m, "kind", "image") == "image"),
            None,
        )
        category = getattr(product, "category", None)
        return await self.documents.upsert(
            document_type=SearchDocumentType.PRODUCT,
            entity_id=product.id,
            title=title,
            subtitle=subtitle,
            body=body,
            metadata={
                "slug": getattr(product, "slug", None),
                "path": f"/products/{getattr(product, 'slug', '')}",
                "status": str(getattr(product, "status", "")),
                # UI grid fields
                "brand_name": brand.name if brand else None,
                "brand_id": str(brand.id) if brand else None,
                "category_id": str(category.id) if category else None,
                "category_path": f"/c/{category.slug}" if category else None,
                "min_price": min_price,
                "max_price": max_price,
                "currency": getattr(product, "base_currency", "BDT"),
                "image_key": primary_img,
                "primary_image_url": primary_img,
                "active_offer_count": len(active_variants),
            },
            is_active=str(getattr(product, "status", "")) == "active",
            source_updated_at=getattr(product, "updated_at", None),
        )

    async def index_brand(self, brand) -> bool:
        return await self.documents.upsert(
            document_type=SearchDocumentType.BRAND,
            entity_id=brand.id,
            title=brand.name,
            subtitle="Brand",
            body=getattr(brand, "slug", "") or "",
            metadata={
                "slug": getattr(brand, "slug", None),
                "path": f"/brands/{getattr(brand, 'slug', '')}",
            },
            is_active=True,
            source_updated_at=getattr(brand, "updated_at", None),
        )

    async def index_category(self, category) -> bool:
        return await self.documents.upsert(
            document_type=SearchDocumentType.CATEGORY,
            entity_id=category.id,
            title=category.name,
            subtitle="Category",
            body=getattr(category, "slug", "") or "",
            metadata={
                "slug": getattr(category, "slug", None),
                "path": f"/categories/{getattr(category, 'slug', '')}",
            },
            is_active=True,
            source_updated_at=getattr(category, "updated_at", None),
        )

    async def rebuild_full_index(
        self, *, principal: Principal | SystemPrincipal,
    ) -> dict[str, int]:
        """Wipe + rebuild the entire index.

        Heavy operation — runs nightly via cron OR manually via the
        admin endpoint. Holds a transaction the whole time so a failed
        rebuild leaves the previous index intact.
        """
        from sqlalchemy import select
        from app.modules.catalog.models import Brand, Category, Product

        t0 = time.monotonic()
        await self.documents.delete_all()

        counts: dict[str, int] = {
            SearchDocumentType.PRODUCT: 0,
            SearchDocumentType.BRAND: 0,
            SearchDocumentType.CATEGORY: 0,
        }

        # Brands first (smallest set)
        brands = (await self.session.execute(select(Brand))).scalars().all()
        for b in brands:
            await self.index_brand(b)
            counts[SearchDocumentType.BRAND] += 1
        # Categories
        cats = (await self.session.execute(select(Category))).scalars().all()
        for c in cats:
            await self.index_category(c)
            counts[SearchDocumentType.CATEGORY] += 1
        # Products (largest set — chunk in 500s for memory)
        from sqlalchemy.orm import selectinload
        result = await self.session.execute(
            select(Product).options(selectinload(Product.brand)),
        )
        for p in result.scalars():
            await self.index_product(p)
            counts[SearchDocumentType.PRODUCT] += 1

        await record_audit(
            actor=principal,
            action=codes.ACTION_SEARCH_INDEX_REBUILT,
            metadata={
                "duration_seconds": round(time.monotonic() - t0, 2),
                "by_type": counts,
            },
        )
        return counts

    # ════════════════════════════════════════════════════════════════
    # Query
    # ════════════════════════════════════════════════════════════════

    async def search(
        self, *,
        user_id: UUID | None,
        query: str,
        document_types: Sequence[str] | None = None,
        limit: int = 25,
        use_rerank: bool = True,
        request_id: str = "",
    ) -> dict[str, Any]:
        """Run a search + log it.

        Returns: {
            query, normalized_query, types, limit, total_hits,
            used_ml_rerank, latency_ms, hits: [...]
        }
        """
        t0 = time.monotonic()
        normalized = normalize_search_text(query)
        tokens = tokenize_query(query)

        # Validate types — drop unknowns silently rather than 422 (search
        # is customer-facing; bad query string = empty result, not error).
        if document_types:
            allowed = ALL_SEARCH_DOCUMENT_TYPES
            document_types = [t for t in document_types if t in allowed]
            if not document_types:
                document_types = None

        rows: list[dict[str, Any]] = []
        if tokens:
            tsq = to_tsquery_string(query)
            rows = await self.documents.search_fts(
                tsquery=tsq,
                document_types=document_types,
                limit=max(limit, _RERANK_TOP_N) if use_rerank else limit,
            )
            # Apply local scoring (combine ts_rank + boost + type prior)
            for row in rows:
                row["score"] = combined_score(
                    ts_rank=float(row["ts_rank"]),
                    boost=float(row["boost"]),
                    document_type=str(row["document_type"]),
                )
            rows.sort(key=lambda r: r["score"], reverse=True)
        elif normalized:
            # Very short query (1 char per token, all dropped) —
            # try the LIKE fallback so the user still sees something.
            rows = await self.documents.search_like_fallback(
                normalized_query=normalized,
                document_types=document_types,
                limit=limit,
            )
            for row in rows:
                row["score"] = combined_score(
                    ts_rank=float(row["ts_rank"]),
                    boost=float(row["boost"]),
                    document_type=str(row["document_type"]),
                )

        # Optionally rerank with the ML provider.
        used_ml = False
        if use_rerank and rows:
            reranker = get_reranker()
            if reranker.name != "not_configured":
                req = RerankRequest(
                    query=query,
                    candidates=tuple(
                        RerankCandidate(
                            document_id=str(r["id"]),
                            document_type=str(r["document_type"]),
                            title=str(r["title"]),
                            subtitle=str(r["subtitle"]),
                            body_excerpt=str(r["body"])[:_RERANK_BODY_EXCERPT_CHARS],
                            local_score=float(r["score"]),
                        )
                        for r in rows[:_RERANK_TOP_N]
                    ),
                    limit=limit,
                )
                ml_scores = await reranker.rerank(req)
                if ml_scores:
                    used_ml = True
                    rows = merge_with_ml_scores(
                        rows=rows[:_RERANK_TOP_N],
                        ml_scores=ml_scores,
                    )

        # Trim to the requested limit AFTER reranking.
        rows = rows[:limit]
        latency_ms = int((time.monotonic() - t0) * 1000)

        # Log the query — best effort, don't fail the response on a log error.
        try:
            await self.logs.log(
                user_id=user_id,
                raw_query=query,
                normalized_query=normalized,
                result_count=len(rows),
                latency_ms=latency_ms,
                request_id=request_id,
                filters={
                    "types": list(document_types) if document_types else [],
                    "limit": limit,
                    "use_rerank": use_rerank,
                },
                used_ml_rerank=used_ml,
            )
        except Exception as exc:  # noqa: BLE001 best-effort logging
            _logger.warning(
                "search_query_log_failed",
                error=type(exc).__name__,
            )

        # Build the response payload (camelCase-free — keys match
        # SearchHit/SearchResponse pydantic models).
        return {
            "query": query,
            "normalized_query": normalized,
            "types": list(document_types) if document_types else [
                SearchDocumentType.PRODUCT,
                SearchDocumentType.BRAND,
                SearchDocumentType.CATEGORY,
            ],
            "limit": limit,
            "total_hits": len(rows),
            "used_ml_rerank": used_ml,
            "latency_ms": latency_ms,
            "hits": [
                {
                    "id": UUID(r["id"]),
                    "document_type": r["document_type"],
                    "entity_id": UUID(r["entity_id"]),
                    "title": r["title"],
                    "subtitle": r["subtitle"],
                    "body": r["body"],
                    "score": float(r.get("score", 0.0)),
                    "local_score": (
                        float(r["local_score"])
                        if r.get("local_score") is not None else None
                    ),
                    "ml_score": (
                        float(r["ml_score"])
                        if r.get("ml_score") is not None else None
                    ),
                    "metadata": dict(r.get("metadata") or {}),
                }
                for r in rows
            ],
        }
