"""Admin endpoints — /api/v1/admin/seo-domination/*

Uses the project-standard `requires_permission` dependency + `get_uow`
unit-of-work session — the same pattern as the existing seo / catalog /
finance admin routers.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

from .. import service, programmatic, content_pipeline, generator
from ..models import (
    BacklinkOutreach,
    ContentPipelineItem,
    ProgrammaticPage,
    WebStory,
)

router = APIRouter(prefix="/admin/seo-domination", tags=["seo-domination"])


@router.get("/score-card")
async def score_card(
    _=Depends(requires_permission("seo.view")),
) -> dict:
    """Headline 10/10 vs Daraz BD scorecard."""
    return service.domination_score_card()


@router.get("/capacity")
async def capacity(
    _=Depends(requires_permission("seo.view")),
) -> dict:
    """How many programmatic pages we can generate from current seed data."""
    return programmatic.estimate_capacity()


@router.get("/expected-content-volume")
async def expected_content_volume(
    _=Depends(requires_permission("seo.view")),
) -> dict:
    return content_pipeline.expected_annual_output()


@router.post("/seed/programmatic")
async def seed_programmatic(
    uow: UnitOfWork = Depends(get_uow),
    _=Depends(requires_permission("seo.bulk_publish")),
) -> dict:
    categories = [
        ("electronics", "Electronics"), ("fashion", "Fashion"),
        ("home-kitchen", "Home & Kitchen"), ("beauty", "Beauty & Personal Care"),
        ("grocery", "Grocery"), ("baby-kids", "Baby & Kids"),
        ("sports", "Sports & Fitness"), ("books", "Books & Stationery"),
    ]
    brand_categories = [
        ("samsung", "Samsung", "mobile", "Mobile"),
        ("xiaomi", "Xiaomi", "mobile", "Mobile"),
        ("apple", "Apple", "mobile", "Mobile"),
        ("lenovo", "Lenovo", "laptop", "Laptop"),
        ("hp", "HP", "laptop", "Laptop"),
        ("nike", "Nike", "footwear", "Footwear"),
        ("adidas", "Adidas", "footwear", "Footwear"),
        ("jbl", "JBL", "audio", "Audio"),
        ("sony", "Sony", "audio", "Audio"),
    ]
    async with uow.transactional() as db:
        counts = await service.bulk_seed_programmatic_pages(
            db, categories=categories, brand_categories=brand_categories,
        )
    return {"seeded": counts, "total": sum(counts.values())}


@router.post("/seed/entity-graph")
async def seed_entity(
    uow: UnitOfWork = Depends(get_uow),
    _=Depends(requires_permission("seo.bulk_publish")),
) -> dict:
    async with uow.transactional() as db:
        n = await service.seed_entity_graph(db)
    return {"seeded_nodes": n}


@router.post("/seed/backlinks")
async def seed_backlinks(
    uow: UnitOfWork = Depends(get_uow),
    _=Depends(requires_permission("seo.bulk_publish")),
) -> dict:
    async with uow.transactional() as db:
        n = await service.seed_backlink_targets(db)
    return {"seeded_targets": n}


@router.get("/pages/count")
async def pages_count(
    uow: UnitOfWork = Depends(get_uow),
    _=Depends(requires_permission("seo.view")),
) -> dict:
    async with uow.transactional() as db:
        q = await db.execute(
            select(
                ProgrammaticPage.page_type,
                ProgrammaticPage.locale,
                func.count(),
            ).group_by(ProgrammaticPage.page_type, ProgrammaticPage.locale)
        )
        rows = [{"page_type": pt, "locale": loc, "count": c} for pt, loc, c in q.all()]
        total_q = await db.execute(select(func.count()).select_from(ProgrammaticPage))
        total = total_q.scalar_one()
    return {"by_type_locale": rows, "total": total}


@router.get("/stories/count")
async def stories_count(
    uow: UnitOfWork = Depends(get_uow),
    _=Depends(requires_permission("seo.view")),
) -> dict:
    async with uow.transactional() as db:
        q = await db.execute(
            select(WebStory.status, func.count()).group_by(WebStory.status)
        )
        rows = [{"status": s, "count": c} for s, c in q.all()]
    return {"by_status": rows}


@router.get("/content/queue")
async def content_queue(
    limit: int = 50,
    uow: UnitOfWork = Depends(get_uow),
    _=Depends(requires_permission("seo.view")),
) -> dict:
    async with uow.transactional() as db:
        q = await db.execute(
            select(ContentPipelineItem)
            .order_by(ContentPipelineItem.scheduled_for.asc().nullslast())
            .limit(limit)
        )
        items = q.scalars().all()
        out = [
            {
                "id": str(r.id), "kind": r.kind, "topic": r.topic,
                "status": r.status, "locale": r.locale,
                "scheduled_for": r.scheduled_for.isoformat() if r.scheduled_for else None,
                "seo_score": float(r.seo_score) if r.seo_score is not None else None,
            }
            for r in items
        ]
    return {"items": out, "count": len(out)}


@router.get("/backlinks/pipeline")
async def backlinks_pipeline(
    uow: UnitOfWork = Depends(get_uow),
    _=Depends(requires_permission("seo.view")),
) -> dict:
    async with uow.transactional() as db:
        q = await db.execute(
            select(BacklinkOutreach.outreach_status, func.count())
            .group_by(BacklinkOutreach.outreach_status)
        )
        rows = [{"status": s, "count": c} for s, c in q.all()]
    return {"by_status": rows}


@router.post("/cron/run-daily")
async def run_daily_cron(
    uow: UnitOfWork = Depends(get_uow),
    _=Depends(requires_permission("seo.cron_run")),
) -> dict:
    """Manual trigger for the SEO daily cron — queues today's blog spec."""
    spec = content_pipeline.daily_blog_spec()
    async with uow.transactional() as db:
        db.add(ContentPipelineItem(
            kind=spec.kind, topic=spec.topic, locale=spec.locale,
            target_keywords=spec.target_keywords, status="queued",
        ))
    return {"queued": 1, "topic": spec.topic}


@router.post("/content/{item_id}/generate")
async def generate_content_item(
    item_id: str,
    uow: UnitOfWork = Depends(get_uow),
    _=Depends(requires_permission("seo.bulk_publish")),
) -> dict:
    """Run the generator on a queued ContentPipelineItem.

    Transitions ``queued`` -> ``review`` and stashes body_html in
    generation_meta. Uses OpenAI / Claude if API key is set; otherwise
    falls back to a deterministic template so the pipeline never blocks.
    """
    import uuid as _uuid
    try:
        item_uuid = _uuid.UUID(item_id)
    except (TypeError, ValueError):
        from fastapi import HTTPException, status
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid item_id")

    async with uow.transactional() as db:
        q = await db.execute(
            select(ContentPipelineItem).where(ContentPipelineItem.id == item_uuid)
        )
        item = q.scalar_one_or_none()
        if not item:
            from fastapi import HTTPException, status
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="content item not found")
        if item.status != "queued":
            return {"skipped": True, "status": item.status, "reason": "item not in queued state"}
        out = generator.process_queued_item(item)
    return {
        "item_id": item_id,
        "status": "review",
        "word_count": out["word_count"],
        "seo_score": out["seo_score"],
        "source": out["source"],
    }


@router.get("/health")
async def health() -> dict:
    """Liveness probe — exposes core pillar capacity without DB."""
    return {
        "module": "seo_domination",
        "version": "v8-2026-05-28",
        "pillars": 7,
        "capacity": programmatic.estimate_capacity(),
        "annual_content_volume": content_pipeline.expected_annual_output(),
    }
