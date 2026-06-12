"""Public (no-auth) endpoints for SEO Domination.

Serves:
  GET /api/v1/seo-domination/page/{slug}    — render programmatic landing data
  GET /api/v1/seo-domination/story/{slug}   — render Web Story AMP HTML
  GET /api/v1/seo-domination/author/{slug}  — author profile + Person schema
  GET /api/v1/seo-domination/sitemap-urls   — list all indexable URLs (for sitemap inclusion)
  GET /api/v1/seo-domination/sitemap-programmatic-{page}.xml — direct sitemap shard
"""
from __future__ import annotations

import math
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, func

from app.core.db.uow import UnitOfWork, get_uow

from ..models import AuthorProfile, ProgrammaticPage, WebStory
from ..eeat import author_person_schema

router = APIRouter(prefix="/seo-domination", tags=["seo-domination-public"])

SITEMAP_URL_CAP = 45_000


@router.get("/page/{slug:path}")
async def get_programmatic_page(
    slug: str,
    uow: UnitOfWork = Depends(get_uow),
) -> dict:
    """Serve content for a programmatic landing page slug."""
    async with uow.transactional() as db:
        q = await db.execute(
            select(ProgrammaticPage).where(
                ProgrammaticPage.slug == slug,
                ProgrammaticPage.indexable == True,  # noqa: E712
            )
        )
        row = q.scalar_one_or_none()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="programmatic page not found")
    return {
        "slug": row.slug,
        "page_type": row.page_type,
        "locale": row.locale,
        "city_slug": row.city_slug,
        "category_slug": row.category_slug,
        "brand_slug": row.brand_slug,
        "title": row.title,
        "meta_description": row.meta_description,
        "h1": row.h1,
        "body_html": row.body_html,
        "schema_jsonld": row.schema_jsonld,
        "priority": float(row.priority),
    }


@router.get("/story/{slug}")
async def get_web_story(
    slug: str,
    uow: UnitOfWork = Depends(get_uow),
) -> Response:
    """Serve a pre-rendered Web Story as AMP HTML."""
    async with uow.transactional() as db:
        q = await db.execute(
            select(WebStory).where(WebStory.slug == slug, WebStory.status == "published")
        )
        story = q.scalar_one_or_none()
    if not story:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="story not found")
    # Re-render on demand if no cache, otherwise serve cached
    if story.amp_html_cached:
        body = story.amp_html_cached
    else:
        from ..web_stories import StoryPage, render_amp_story
        pages = [
            StoryPage(
                image_url=p["image_url"], alt=p.get("alt", ""),
                caption=p.get("caption", ""),
                cta_label=p.get("cta", {}).get("label", ""),
                cta_url=p.get("cta", {}).get("url", ""),
            )
            for p in story.pages_jsonb
        ]
        body = render_amp_story(
            title=story.title,
            canonical_url=f"https://hypershop.com.bd/stories/{story.slug}",
            publisher_logo_url=story.publisher_logo_url,
            poster_portrait_url=story.poster_portrait_url,
            pages=pages,
            locale=story.locale,
        )
    return Response(content=body, media_type="text/html; charset=utf-8")


@router.get("/author/{slug}")
async def get_author_profile(
    slug: str,
    uow: UnitOfWork = Depends(get_uow),
) -> dict:
    async with uow.transactional() as db:
        q = await db.execute(select(AuthorProfile).where(AuthorProfile.slug == slug))
        a = q.scalar_one_or_none()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="author not found")
    profile = {
        "slug": a.slug,
        "full_name": a.full_name,
        "title_role": a.title_role,
        "avatar_url": a.avatar_url,
        "bio_en": a.bio_en,
        "bio_bn": a.bio_bn,
        "expertise_areas": list(a.expertise_areas or []),
        "credentials": list(a.credentials or []),
        "social_links": dict(a.social_links or {}),
        "wikidata_qid": a.wikidata_qid,
        "article_count": a.article_count,
    }
    return {"profile": profile, "person_jsonld": author_person_schema(profile)}


@router.get("/sitemap-urls")
async def list_sitemap_urls(
    limit: int = 5_000,
    offset: int = 0,
    uow: UnitOfWork = Depends(get_uow),
) -> dict:
    """Return ``{urls: [{loc, priority, lastmod}]}`` for sitemap inclusion.

    The main sitemap-index can splice these in via a /sitemap-programmatic-*
    shard or merge into its own urlset.
    """
    async with uow.transactional() as db:
        q = await db.execute(
            select(
                ProgrammaticPage.slug,
                ProgrammaticPage.priority,
                ProgrammaticPage.updated_at,
            )
            .where(ProgrammaticPage.indexable == True)  # noqa: E712
            .order_by(ProgrammaticPage.slug)
            .offset(offset)
            .limit(limit)
        )
        rows = q.all()
        total_q = await db.execute(
            select(func.count())
            .select_from(ProgrammaticPage)
            .where(ProgrammaticPage.indexable == True)  # noqa: E712
        )
        total = total_q.scalar_one()
    return {
        "urls": [
            {
                "loc": f"https://hypershop.com.bd/lp/{slug}",
                "priority": float(priority),
                "lastmod": updated.isoformat() if updated else None,
            }
            for slug, priority, updated in rows
        ],
        "offset": offset,
        "limit": limit,
        "total": total,
    }


@router.get("/sitemap-programmatic-{page}.xml")
async def sitemap_programmatic_shard(
    page: int,
    uow: UnitOfWork = Depends(get_uow),
) -> Response:
    """Sitemap shard for programmatic pages. Linked from main sitemap-index."""
    async with uow.transactional() as db:
        q = await db.execute(
            select(
                ProgrammaticPage.slug,
                ProgrammaticPage.priority,
                ProgrammaticPage.updated_at,
            )
            .where(ProgrammaticPage.indexable == True)  # noqa: E712
            .order_by(ProgrammaticPage.slug)
            .offset(page * SITEMAP_URL_CAP)
            .limit(SITEMAP_URL_CAP)
        )
        rows = q.all()

    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="sitemap shard out of range")

    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for slug, priority, updated in rows:
        parts.append("<url>")
        parts.append(f"<loc>https://hypershop.com.bd/lp/{escape(slug)}</loc>")
        if updated:
            parts.append(f"<lastmod>{updated.date().isoformat()}</lastmod>")
        parts.append(f"<priority>{float(priority):.1f}</priority>")
        parts.append("<changefreq>weekly</changefreq>")
        parts.append("</url>")
    parts.append("</urlset>")
    return Response(content="\n".join(parts), media_type="application/xml; charset=utf-8")


@router.get("/sitemap-shard-count")
async def sitemap_shard_count(
    uow: UnitOfWork = Depends(get_uow),
) -> dict:
    """Number of sitemap shards needed for current programmatic page count."""
    async with uow.transactional() as db:
        n = (await db.execute(
            select(func.count())
            .select_from(ProgrammaticPage)
            .where(ProgrammaticPage.indexable == True)  # noqa: E712
        )).scalar_one()
    shards = max(0, math.ceil(n / SITEMAP_URL_CAP))
    return {
        "total_pages": n,
        "shards": shards,
        "shard_urls": [
            f"https://hypershop.com.bd/api/v1/seo-domination/sitemap-programmatic-{i}.xml"
            for i in range(shards)
        ],
    }
