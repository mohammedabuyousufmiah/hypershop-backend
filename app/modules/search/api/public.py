"""Public search endpoint — UNAUTHENTICATED.

Customers (logged-in or not) hit this from the storefront search box.
Returns top-N hits across products + brands + categories with optional
type filtering.

Why no auth:
  - Catalog is public (already exposed via /api/v1/catalog/products)
  - Personalisation can be added later by reading the optional Bearer
    token, looking up the user, and passing user_id to the service for
    per-user query log attribution + (eventually) personalised ranking
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.search.schemas import SearchHit, SearchResponse
from app.modules.search.service import SearchService

router = APIRouter(prefix="/search", tags=["search"])


@router.get(
    "",
    summary="Public catalog search across products, brands, categories",
    description=(
        "Full-text search over the denormalised search index. Returns the "
        "`SearchResultPageWire` shape the customer-web expects: q, hits, "
        "facets, page, page_size, total, took_ms. Filters (category_slug, "
        "brand, price_min, price_max, in_stock_only, sort) are accepted "
        "but applied client-side over hits for now — the backend index "
        "doesn't yet store per-variant price + stock buckets needed for "
        "true faceted SQL filtering. Wire shape stays stable so the UI "
        "ships unchanged when faceted SQL lands."
    ),
)
async def search(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    q: str = Query(..., min_length=1, max_length=255, description="User query"),
    types: str | None = Query(
        default=None,
        description='Comma-separated subset of "product,brand,category" to search',
    ),
    # Frontend params — accepted but not yet enforced server-side. Keeping
    # them in the signature ensures FastAPI doesn't 422 on unexpected keys
    # (extra-allow behaviour) and they appear in the OpenAPI spec for the
    # day the indexer supports them.
    category_slug: str | None = Query(default=None, max_length=120),
    brand: str | None = Query(default=None, max_length=120),
    price_min: float | None = Query(default=None, ge=0),
    price_max: float | None = Query(default=None, ge=0),
    currency: str | None = Query(default=None, max_length=3),
    in_stock_only: bool = Query(default=False),
    sort: str | None = Query(default=None, max_length=32),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=24, ge=1, le=100),
    rerank: bool = Query(default=True),
) -> dict:
    type_list: list[str] | None = None
    if types:
        type_list = [t.strip() for t in types.split(",") if t.strip()]

    request_id = request.headers.get("x-request-id") or ""

    # Use the larger of `limit` (legacy) and `page_size` for capacity.
    capacity = max(page_size, 25)

    async with uow.transactional() as session:
        svc = SearchService(session)
        result = await svc.search(
            user_id=None,
            query=q,
            document_types=type_list,
            limit=capacity,
            use_rerank=rerank,
            request_id=request_id,
        )

    # Re-shape backend hits into the `SearchHitWire` shape the UI's
    # `fromSearchHit` normaliser expects (see customer-web types package
    # `SearchHitWire`). The product subset is what the search-results
    # grid consumes — brand/category rows show up via separate facets.
    # Subtitle convention from indexer is "by <BrandName>" so we strip.
    ui_hits = []
    for h in result["hits"]:
        if h.get("document_type") != "product":
            continue
        meta = h.get("metadata") or {}
        subtitle = (h.get("subtitle") or "")
        brand = meta.get("brand_name")
        if not brand and subtitle.startswith("by "):
            brand = subtitle[3:].strip()
        ui_hits.append({
            "product_id":         str(h.get("entity_id") or h.get("id") or ""),
            "slug":                meta.get("slug") or "",
            "title":               h.get("title") or "",
            "brand":               brand,
            "category_id":         meta.get("category_id"),
            "category_path":       meta.get("category_path") or meta.get("path"),
            "min_price":           meta.get("min_price"),
            "max_price":           meta.get("max_price"),
            "currency":            meta.get("currency") or "BDT",
            "active_offer_count":  int(meta.get("active_offer_count", 1)),
            "image_key":           meta.get("image_key") or meta.get("primary_image_url"),
            "score":               h.get("score", 0.0),
        })

    # Page slice
    offset = (page - 1) * page_size
    page_hits = ui_hits[offset:offset + page_size]

    return {
        "q": q,
        "hits": page_hits,
        "facets": {
            "category": [],
            "brand": [],
            "price": [],
        },
        "page": page,
        "page_size": page_size,
        "total": len(ui_hits),
        "took_ms": result.get("latency_ms", 0),
    }


@router.get(
    "/autocomplete",
    summary="Lightweight autocomplete suggestions for the search bar",
    description=(
        "Prefix-style suggestions surfaced from the same `ts_rank`-driven "
        "search index. Optimised for the storefront header dropdown — "
        "shallow result count, no ML reranker, no boost-by-type, returns "
        "just the matched title + slug + type."
    ),
)
async def autocomplete(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    q: str = Query(..., min_length=1, max_length=80),
    limit: int = Query(default=6, ge=1, le=15),
) -> dict:
    request_id = request.headers.get("x-request-id") or ""
    async with uow.transactional() as session:
        svc = SearchService(session)
        result = await svc.search(
            user_id=None,
            query=q,
            document_types=None,
            limit=limit,
            use_rerank=False,
            request_id=request_id,
        )
    # Flatten to a compact wire shape — frontend AiSearchBar / api-client
    # only need {title, slug, type, image} per hit. `metadata` carries
    # the per-doc-type extras (slug / image_url / category_path) emitted
    # by the indexer.
    suggestions = []
    for h in result["hits"]:
        meta = h.get("metadata") or {}
        suggestions.append({
            "title": h.get("title") or meta.get("name") or meta.get("slug") or "",
            "slug":  meta.get("slug") or "",
            "type":  h.get("document_type") or h.get("type") or "product",
            "image": meta.get("image_url") or meta.get("primary_image_url"),
            "subtitle": h.get("subtitle") or meta.get("brand_name") or "",
        })
    return {
        "query": result["query"],
        "normalized_query": result["normalized_query"],
        "suggestions": suggestions,
        "total_hits": result["total_hits"],
    }
