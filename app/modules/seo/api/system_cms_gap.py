"""Gap-fill admin GET endpoints for the admin-panel "system-cms" feature.

The admin System page (CMS tab) reads four list endpoints that the FE
api-client points at ``/admin/storefront/*``:

    GET /admin/storefront/banners        -> {items: CmsBannerWire[], total}
    GET /admin/storefront/posts          -> {items: CmsPostWire[],   total}
    GET /admin/storefront/redirects      -> {items: CmsRedirectWire[], total}
    GET /admin/storefront/seo-overrides  -> {items: SeoOverrideWire[], total}

No ``storefront`` module exists; the backing tables live in the ``seo``
module (``homepage_banners``, ``blog_posts``, ``seo_url_redirects``,
``seo_meta_overrides``). The column names differ from the FE wire shape,
so each endpoint maps the real rows onto the exact JSON the FE reads.

Every query is wrapped in try/except and uses raw ``text()`` SQL so a
missing table/column degrades to an empty list rather than a 500. The
file imports only stable core symbols and is boot-safe on its own.

This router is registered centrally in main.py (do not edit other files).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission

router = APIRouter(prefix="/admin/storefront", tags=["admin-system-cms"])

# Read-level perm held by admin / support / data roles in the seo module.
_READ = "seo.view"


def _iso(value: Any) -> str | None:
    """datetime -> ISO 8601 string (or None). Never raises."""
    if value is None:
        return None
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _banner_status(is_active: Any) -> str:
    # FE BannerStatus union: DRAFT|SCHEDULED|ACTIVE|ENDED|DISABLED.
    # The real table only carries is_active, so collapse to ACTIVE/DISABLED.
    return "ACTIVE" if is_active else "DISABLED"


def _content_status(status: Any) -> str:
    # FE ContentStatus union: DRAFT|PUBLISHED|ARCHIVED (upper-case).
    s = str(status or "draft").upper()
    return s if s in {"DRAFT", "PUBLISHED", "ARCHIVED"} else "DRAFT"


def _entity_type(raw: Any) -> str:
    # FE SeoEntityType union: PRODUCT|CATEGORY|PAGE|POST|HOME.
    m = {
        "product": "PRODUCT",
        "category": "CATEGORY",
        "brand": "CATEGORY",
        "static_page": "PAGE",
        "blog_post": "POST",
        "home": "HOME",
    }
    return m.get(str(raw or "").lower(), "PAGE")


# ============================================================
#  GET /admin/storefront/banners
# ============================================================
@router.get("/banners", summary="List storefront/homepage banners")
async def list_banners(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_READ))],
    placement: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, object]:
    items: list[dict[str, object]] = []
    total = 0
    try:
        async with uow.transactional() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, title, subtitle, image_url, target_url, "
                        "is_active, sort_order, valid_from, valid_until, "
                        "created_at, updated_at "
                        "FROM homepage_banners "
                        "ORDER BY sort_order ASC, created_at DESC "
                        "LIMIT :limit OFFSET :offset"
                    ),
                    {"limit": limit, "offset": offset},
                )
            ).mappings().all()
            cnt = (
                await session.execute(
                    text("SELECT COUNT(*) AS c FROM homepage_banners"),
                )
            ).scalar()
            total = int(cnt or 0)
            for r in rows:
                st = _banner_status(r["is_active"])
                if status and st != status:
                    continue
                items.append(
                    {
                        "id": str(r["id"]),
                        "slug": str(r["id"]),
                        "placement": placement or "homepage",
                        "title": r["title"] or "",
                        "subtitle": r["subtitle"],
                        "image_url": r["image_url"] or "",
                        "link_url": r["target_url"],
                        "sort_order": int(r["sort_order"] or 0),
                        "starts_at": _iso(r["valid_from"]),
                        "ends_at": _iso(r["valid_until"]),
                        "enabled": bool(r["is_active"]),
                        "status": st,
                        "created_at": _iso(r["created_at"]) or "",
                        "updated_at": _iso(r["updated_at"]) or "",
                    }
                )
    except Exception:
        return {"items": [], "total": 0}
    return {"items": items, "total": total}


# ============================================================
#  GET /admin/storefront/posts
# ============================================================
@router.get("/posts", summary="List CMS blog posts")
async def list_posts(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_READ))],
    status: str | None = None,
    locale: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, object]:
    items: list[dict[str, object]] = []
    total = 0
    try:
        async with uow.transactional() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, slug, title, excerpt, body_markdown, "
                        "cover_image_url, author_user_id, status, "
                        "published_at, tags_csv, created_at, updated_at "
                        "FROM blog_posts "
                        "ORDER BY created_at DESC "
                        "LIMIT :limit OFFSET :offset"
                    ),
                    {"limit": limit, "offset": offset},
                )
            ).mappings().all()
            cnt = (
                await session.execute(
                    text("SELECT COUNT(*) AS c FROM blog_posts"),
                )
            ).scalar()
            total = int(cnt or 0)
            for r in rows:
                st = _content_status(r["status"])
                if status and st != status:
                    continue
                tags_csv = r["tags_csv"] or ""
                tags = [t.strip() for t in tags_csv.split(",") if t.strip()]
                if tag and tag.lower() not in [t.lower() for t in tags]:
                    continue
                items.append(
                    {
                        "id": str(r["id"]),
                        # blog_posts has no per-row locale; default to "en"
                        "locale": locale or "en",
                        "slug": r["slug"] or "",
                        "title": r["title"] or "",
                        "excerpt": r["excerpt"],
                        "body_markdown": r["body_markdown"] or "",
                        "featured_image_url": r["cover_image_url"],
                        "author_id": (
                            str(r["author_user_id"])
                            if r["author_user_id"] is not None
                            else None
                        ),
                        # no category column in blog_posts v1
                        "category": None,
                        "tags": tags or None,
                        "meta_title": None,
                        "meta_description": None,
                        "canonical_url": None,
                        "noindex": False,
                        "status": st,
                        "published_at": _iso(r["published_at"]),
                        "archived_at": None,
                        "created_at": _iso(r["created_at"]) or "",
                        "updated_at": _iso(r["updated_at"]) or "",
                    }
                )
    except Exception:
        return {"items": [], "total": 0}
    return {"items": items, "total": total}


# ============================================================
#  GET /admin/storefront/redirects
# ============================================================
@router.get("/redirects", summary="List URL redirects")
async def list_redirects(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_READ))],
    active: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, object]:
    items: list[dict[str, object]] = []
    total = 0
    try:
        async with uow.transactional() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, from_path, to_path, redirect_type, "
                        "is_active, hit_count, last_hit_at, "
                        "created_at, updated_at "
                        "FROM seo_url_redirects "
                        "ORDER BY created_at DESC "
                        "LIMIT :limit OFFSET :offset"
                    ),
                    {"limit": limit, "offset": offset},
                )
            ).mappings().all()
            cnt = (
                await session.execute(
                    text("SELECT COUNT(*) AS c FROM seo_url_redirects"),
                )
            ).scalar()
            total = int(cnt or 0)
            for r in rows:
                is_active = bool(r["is_active"])
                if active is not None and is_active != active:
                    continue
                code = 301 if str(r["redirect_type"]) == "permanent" else 302
                items.append(
                    {
                        "id": str(r["id"]),
                        "from_path": r["from_path"] or "",
                        "to_path": r["to_path"] or "",
                        "status_code": code,
                        "active": is_active,
                        "hits": int(r["hit_count"] or 0),
                        "last_hit_at": _iso(r["last_hit_at"]),
                        "created_at": _iso(r["created_at"]) or "",
                        "updated_at": _iso(r["updated_at"]) or "",
                    }
                )
    except Exception:
        return {"items": [], "total": 0}
    return {"items": items, "total": total}


# ============================================================
#  GET /admin/storefront/seo-overrides
# ============================================================
@router.get("/seo-overrides", summary="List SEO meta overrides")
async def list_seo_overrides(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_READ))],
    entity_type: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, object]:
    items: list[dict[str, object]] = []
    total = 0
    try:
        async with uow.transactional() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, entity_type, entity_key, title, "
                        "meta_description, canonical_url, og_image_url, "
                        "robots_directives, created_at, updated_at "
                        "FROM seo_meta_overrides "
                        "ORDER BY updated_at DESC "
                        "LIMIT :limit OFFSET :offset"
                    ),
                    {"limit": limit, "offset": offset},
                )
            ).mappings().all()
            cnt = (
                await session.execute(
                    text("SELECT COUNT(*) AS c FROM seo_meta_overrides"),
                )
            ).scalar()
            total = int(cnt or 0)
            for r in rows:
                et = _entity_type(r["entity_type"])
                if entity_type and et != entity_type:
                    continue
                noindex = "noindex" in str(r["robots_directives"] or "").lower()
                items.append(
                    {
                        "id": str(r["id"]),
                        "entity_type": et,
                        "entity_id": r["entity_key"] or "",
                        "meta_title": r["title"],
                        "meta_description": r["meta_description"],
                        "canonical_url": r["canonical_url"],
                        "noindex": noindex,
                        # og_title/og_description not stored separately v1
                        "og_title": None,
                        "og_description": None,
                        "og_image_url": r["og_image_url"],
                        "created_at": _iso(r["created_at"]) or "",
                        "updated_at": _iso(r["updated_at"]) or "",
                    }
                )
    except Exception:
        return {"items": [], "total": 0}
    return {"items": items, "total": total}
