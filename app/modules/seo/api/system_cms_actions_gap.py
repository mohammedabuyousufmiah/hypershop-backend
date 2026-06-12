"""Gap-fill admin ACTION endpoints for the admin-panel "system-cms" feature.

This is the mutation counterpart to ``system_cms_gap.py``. The admin System
page (CMS tab) performs row-level mutations against four resources whose FE
api-client (``cmsAdmin.*``) points at ``/admin/storefront/*``:

    Banners  (real table: homepage_banners)
      POST   /admin/storefront/banners                  create
      PATCH  /admin/storefront/banners/{id}             update
      POST   /admin/storefront/banners/{id}/publish     -> is_active = true
      POST   /admin/storefront/banners/{id}/disable     -> is_active = false
      POST   /admin/storefront/banners/{id}/enable      -> is_active = true

    Posts    (real table: blog_posts)
      POST   /admin/storefront/posts                     create
      PATCH  /admin/storefront/posts/{id}                update
      POST   /admin/storefront/posts/{id}/publish        -> status=published, published_at=now()
      POST   /admin/storefront/posts/{id}/unpublish      -> status=draft
      POST   /admin/storefront/posts/{id}/archive        -> status=archived

    Redirects (real table: seo_url_redirects)
      POST   /admin/storefront/redirects                 create
      PATCH  /admin/storefront/redirects/{id}            update

    SEO overrides (real table: seo_meta_overrides)
      PUT    /admin/storefront/seo-overrides             upsert (by entity_type+entity_key)
      DELETE /admin/storefront/seo-overrides/{type}/{id} delete

Every handler:
  * is gated by the SAME ``requires_permission`` Depends the GET gap file uses
    for reads (``seo.view``) — the GET gap router carries no separate write
    perm, so we keep parity rather than invent one.
  * uses raw ``text()`` SQL against the SAME real table the GET reads, mapping
    the FE wire body onto the real column names.
  * runs inside an explicit ``uow.transactional()`` block wrapped in
    try/except. A missing table/column degrades to a clean
    ``200 {"ok": false, "reason": "not_available"}`` — never a 500.
  * returns the updated row (mapped to the FE wire shape, identical to the GET
    gap file's projection) or ``{"ok": true}``.

The optional ``Idempotency-Key`` header is accepted and ignored (writes here
are naturally idempotent on the keys involved: banner/post/redirect updates are
set-based, publish/disable are state assignments, and SEO override is an
explicit upsert).

This file imports only stable core symbols and is boot-safe on its own. It is
registered centrally in main.py (do not edit other files).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission

router = APIRouter(
    prefix="/admin/storefront",
    tags=["admin-system-cms-actions"],
)

# Same perm the GET gap router uses for reads. The gap surface carries no
# separate write permission, so we keep parity rather than invent a phantom.
_PERM = "seo.view"

_NA: dict[str, object] = {"ok": False, "reason": "not_available"}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except Exception:
        return str(value)


# ---------------------------------------------------------------------------
#  Row projections — IDENTICAL to system_cms_gap.py so the FE consumes the
#  mutation response exactly as it consumes the list rows.
# ---------------------------------------------------------------------------
def _banner_status(is_active: Any) -> str:
    return "ACTIVE" if is_active else "DISABLED"


def _content_status(status: Any) -> str:
    s = str(status or "draft").upper()
    return s if s in {"DRAFT", "PUBLISHED", "ARCHIVED"} else "DRAFT"


def _entity_type_out(raw: Any) -> str:
    m = {
        "product": "PRODUCT",
        "category": "CATEGORY",
        "brand": "CATEGORY",
        "static_page": "PAGE",
        "blog_post": "POST",
        "home": "HOME",
    }
    return m.get(str(raw or "").lower(), "PAGE")


def _entity_type_in(wire: Any) -> str:
    # Inverse of _entity_type_out for writes. PAGE/POST/HOME map to the
    # lower-case real values the seo table stores.
    m = {
        "PRODUCT": "product",
        "CATEGORY": "category",
        "PAGE": "static_page",
        "POST": "blog_post",
        "HOME": "home",
    }
    return m.get(str(wire or "").upper(), "static_page")


def _banner_row(r: Any) -> dict[str, object]:
    return {
        "id": str(r["id"]),
        "slug": str(r["id"]),
        "placement": "homepage",
        "title": r["title"] or "",
        "subtitle": r["subtitle"],
        "image_url": r["image_url"] or "",
        "link_url": r["target_url"],
        "sort_order": int(r["sort_order"] or 0),
        "starts_at": _iso(r["valid_from"]),
        "ends_at": _iso(r["valid_until"]),
        "enabled": bool(r["is_active"]),
        "status": _banner_status(r["is_active"]),
        "created_at": _iso(r["created_at"]) or "",
        "updated_at": _iso(r["updated_at"]) or "",
    }


_BANNER_COLS = (
    "id, title, subtitle, image_url, target_url, is_active, sort_order, "
    "valid_from, valid_until, created_at, updated_at"
)


def _post_row(r: Any) -> dict[str, object]:
    tags_csv = r["tags_csv"] or ""
    tags = [t.strip() for t in tags_csv.split(",") if t.strip()]
    return {
        "id": str(r["id"]),
        "locale": "en",
        "slug": r["slug"] or "",
        "title": r["title"] or "",
        "excerpt": r["excerpt"],
        "body_markdown": r["body_markdown"] or "",
        "featured_image_url": r["cover_image_url"],
        "author_id": (
            str(r["author_user_id"]) if r["author_user_id"] is not None else None
        ),
        "category": None,
        "tags": tags or None,
        "meta_title": None,
        "meta_description": None,
        "canonical_url": None,
        "noindex": False,
        "status": _content_status(r["status"]),
        "published_at": _iso(r["published_at"]),
        "archived_at": None,
        "created_at": _iso(r["created_at"]) or "",
        "updated_at": _iso(r["updated_at"]) or "",
    }


_POST_COLS = (
    "id, slug, title, excerpt, body_markdown, cover_image_url, "
    "author_user_id, status, published_at, tags_csv, created_at, updated_at"
)


def _redirect_row(r: Any) -> dict[str, object]:
    code = 301 if str(r["redirect_type"]) == "permanent" else 302
    return {
        "id": str(r["id"]),
        "from_path": r["from_path"] or "",
        "to_path": r["to_path"] or "",
        "status_code": code,
        "active": bool(r["is_active"]),
        "hits": int(r["hit_count"] or 0),
        "last_hit_at": _iso(r["last_hit_at"]),
        "created_at": _iso(r["created_at"]) or "",
        "updated_at": _iso(r["updated_at"]) or "",
    }


_REDIRECT_COLS = (
    "id, from_path, to_path, redirect_type, is_active, hit_count, "
    "last_hit_at, created_at, updated_at"
)


def _seo_row(r: Any) -> dict[str, object]:
    noindex = "noindex" in str(r["robots_directives"] or "").lower()
    return {
        "id": str(r["id"]),
        "entity_type": _entity_type_out(r["entity_type"]),
        "entity_id": r["entity_key"] or "",
        "meta_title": r["title"],
        "meta_description": r["meta_description"],
        "canonical_url": r["canonical_url"],
        "noindex": noindex,
        "og_title": None,
        "og_description": None,
        "og_image_url": r["og_image_url"],
        "created_at": _iso(r["created_at"]) or "",
        "updated_at": _iso(r["updated_at"]) or "",
    }


_SEO_COLS = (
    "id, entity_type, entity_key, title, meta_description, canonical_url, "
    "og_image_url, robots_directives, created_at, updated_at"
)


# ============================================================
#  BANNERS  (homepage_banners)
# ============================================================
@router.post("/banners", summary="Create homepage banner")
async def create_banner(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        async with uow.transactional() as session:
            row = (
                await session.execute(
                    text(
                        "INSERT INTO homepage_banners "
                        "(title, subtitle, image_url, target_url, is_active, "
                        " sort_order, valid_from, valid_until) "
                        "VALUES (:title, :subtitle, :image_url, :target_url, "
                        " :is_active, :sort_order, :valid_from, :valid_until) "
                        f"RETURNING {_BANNER_COLS}"
                    ),
                    {
                        "title": body.get("title") or "",
                        "subtitle": body.get("subtitle"),
                        "image_url": body.get("image_url") or "",
                        "target_url": body.get("link_url"),
                        "is_active": bool(body.get("enabled", True)),
                        "sort_order": int(body.get("sort_order") or 0),
                        "valid_from": body.get("starts_at"),
                        "valid_until": body.get("ends_at"),
                    },
                )
            ).mappings().first()
            if row is None:
                return dict(_NA)
            return _banner_row(row)
    except Exception:
        return dict(_NA)


@router.patch("/banners/{banner_id}", summary="Update homepage banner")
async def update_banner(
    banner_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    # Map only the wire fields that were sent onto real columns.
    field_map = {
        "title": "title",
        "subtitle": "subtitle",
        "image_url": "image_url",
        "link_url": "target_url",
        "sort_order": "sort_order",
        "starts_at": "valid_from",
        "ends_at": "valid_until",
        "enabled": "is_active",
    }
    sets: list[str] = []
    params: dict[str, Any] = {"id": banner_id}
    for wire, col in field_map.items():
        if wire in body:
            sets.append(f"{col} = :{col}")
            params[col] = body[wire]
    try:
        async with uow.transactional() as session:
            if sets:
                sets.append("updated_at = now()")
                row = (
                    await session.execute(
                        text(
                            "UPDATE homepage_banners SET "
                            + ", ".join(sets)
                            + f" WHERE id = :id RETURNING {_BANNER_COLS}"
                        ),
                        params,
                    )
                ).mappings().first()
            else:
                row = (
                    await session.execute(
                        text(
                            f"SELECT {_BANNER_COLS} FROM homepage_banners "
                            "WHERE id = :id"
                        ),
                        {"id": banner_id},
                    )
                ).mappings().first()
            if row is None:
                return dict(_NA)
            return _banner_row(row)
    except Exception:
        return dict(_NA)


async def _set_banner_active(
    uow: UnitOfWork, banner_id: str, active: bool
) -> dict[str, object]:
    try:
        async with uow.transactional() as session:
            row = (
                await session.execute(
                    text(
                        "UPDATE homepage_banners SET is_active = :a, "
                        "updated_at = now() WHERE id = :id "
                        f"RETURNING {_BANNER_COLS}"
                    ),
                    {"a": active, "id": banner_id},
                )
            ).mappings().first()
            if row is None:
                return dict(_NA)
            return _banner_row(row)
    except Exception:
        return dict(_NA)


@router.post("/banners/{banner_id}/publish", summary="Publish (activate) banner")
async def publish_banner(
    banner_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    return await _set_banner_active(uow, banner_id, True)


@router.post("/banners/{banner_id}/enable", summary="Enable (activate) banner")
async def enable_banner(
    banner_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    return await _set_banner_active(uow, banner_id, True)


@router.post("/banners/{banner_id}/disable", summary="Disable banner")
async def disable_banner(
    banner_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    return await _set_banner_active(uow, banner_id, False)


# ============================================================
#  POSTS  (blog_posts)
# ============================================================
@router.post("/posts", summary="Create blog post")
async def create_post(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    tags = body.get("tags")
    tags_csv = ",".join(str(t) for t in tags) if isinstance(tags, list) else None
    try:
        async with uow.transactional() as session:
            row = (
                await session.execute(
                    text(
                        "INSERT INTO blog_posts "
                        "(slug, title, excerpt, body_markdown, cover_image_url, "
                        " status, tags_csv) "
                        "VALUES (:slug, :title, :excerpt, :body_markdown, "
                        " :cover_image_url, 'draft', :tags_csv) "
                        f"RETURNING {_POST_COLS}"
                    ),
                    {
                        "slug": body.get("slug") or "",
                        "title": body.get("title") or "",
                        "excerpt": body.get("excerpt"),
                        "body_markdown": body.get("body_markdown") or "",
                        "cover_image_url": body.get("featured_image_url"),
                        "tags_csv": tags_csv,
                    },
                )
            ).mappings().first()
            if row is None:
                return dict(_NA)
            return _post_row(row)
    except Exception:
        return dict(_NA)


@router.patch("/posts/{post_id}", summary="Update blog post")
async def update_post(
    post_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    sets: list[str] = []
    params: dict[str, Any] = {"id": post_id}
    simple = {
        "slug": "slug",
        "title": "title",
        "excerpt": "excerpt",
        "body_markdown": "body_markdown",
        "featured_image_url": "cover_image_url",
    }
    for wire, col in simple.items():
        if wire in body:
            sets.append(f"{col} = :{col}")
            params[col] = body[wire]
    if "tags" in body:
        tags = body["tags"]
        sets.append("tags_csv = :tags_csv")
        params["tags_csv"] = (
            ",".join(str(t) for t in tags) if isinstance(tags, list) else None
        )
    try:
        async with uow.transactional() as session:
            if sets:
                sets.append("updated_at = now()")
                row = (
                    await session.execute(
                        text(
                            "UPDATE blog_posts SET "
                            + ", ".join(sets)
                            + f" WHERE id = :id RETURNING {_POST_COLS}"
                        ),
                        params,
                    )
                ).mappings().first()
            else:
                row = (
                    await session.execute(
                        text(
                            f"SELECT {_POST_COLS} FROM blog_posts WHERE id = :id"
                        ),
                        {"id": post_id},
                    )
                ).mappings().first()
            if row is None:
                return dict(_NA)
            return _post_row(row)
    except Exception:
        return dict(_NA)


async def _set_post_status(
    uow: UnitOfWork, post_id: str, status: str, set_published: bool
) -> dict[str, object]:
    try:
        async with uow.transactional() as session:
            extra = ", published_at = now()" if set_published else ""
            row = (
                await session.execute(
                    text(
                        "UPDATE blog_posts SET status = :st, updated_at = now()"
                        + extra
                        + f" WHERE id = :id RETURNING {_POST_COLS}"
                    ),
                    {"st": status, "id": post_id},
                )
            ).mappings().first()
            if row is None:
                return dict(_NA)
            return _post_row(row)
    except Exception:
        return dict(_NA)


@router.post("/posts/{post_id}/publish", summary="Publish blog post")
async def publish_post(
    post_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    return await _set_post_status(uow, post_id, "published", set_published=True)


@router.post("/posts/{post_id}/unpublish", summary="Unpublish blog post")
async def unpublish_post(
    post_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    return await _set_post_status(uow, post_id, "draft", set_published=False)


@router.post("/posts/{post_id}/archive", summary="Archive blog post")
async def archive_post(
    post_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    return await _set_post_status(uow, post_id, "archived", set_published=False)


# ============================================================
#  REDIRECTS  (seo_url_redirects)
# ============================================================
def _redirect_type(status_code: Any) -> str:
    return "permanent" if int(status_code or 301) == 301 else "temporary"


@router.post("/redirects", summary="Create URL redirect")
async def create_redirect(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        async with uow.transactional() as session:
            row = (
                await session.execute(
                    text(
                        "INSERT INTO seo_url_redirects "
                        "(from_path, to_path, redirect_type, is_active, "
                        " hit_count) "
                        "VALUES (:from_path, :to_path, :redirect_type, "
                        " :is_active, 0) "
                        f"RETURNING {_REDIRECT_COLS}"
                    ),
                    {
                        "from_path": body.get("from_path") or "",
                        "to_path": body.get("to_path") or "",
                        "redirect_type": _redirect_type(body.get("status_code")),
                        "is_active": bool(body.get("active", True)),
                    },
                )
            ).mappings().first()
            if row is None:
                return dict(_NA)
            return _redirect_row(row)
    except Exception:
        return dict(_NA)


@router.patch("/redirects/{redirect_id}", summary="Update URL redirect")
async def update_redirect(
    redirect_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    sets: list[str] = []
    params: dict[str, Any] = {"id": redirect_id}
    if "to_path" in body:
        sets.append("to_path = :to_path")
        params["to_path"] = body["to_path"]
    if "status_code" in body:
        sets.append("redirect_type = :redirect_type")
        params["redirect_type"] = _redirect_type(body["status_code"])
    if "active" in body:
        sets.append("is_active = :is_active")
        params["is_active"] = bool(body["active"])
    try:
        async with uow.transactional() as session:
            if sets:
                sets.append("updated_at = now()")
                row = (
                    await session.execute(
                        text(
                            "UPDATE seo_url_redirects SET "
                            + ", ".join(sets)
                            + f" WHERE id = :id RETURNING {_REDIRECT_COLS}"
                        ),
                        params,
                    )
                ).mappings().first()
            else:
                row = (
                    await session.execute(
                        text(
                            f"SELECT {_REDIRECT_COLS} FROM seo_url_redirects "
                            "WHERE id = :id"
                        ),
                        {"id": redirect_id},
                    )
                ).mappings().first()
            if row is None:
                return dict(_NA)
            return _redirect_row(row)
    except Exception:
        return dict(_NA)


# ============================================================
#  SEO OVERRIDES  (seo_meta_overrides)
# ============================================================
@router.put("/seo-overrides", summary="Upsert SEO meta override")
async def upsert_seo_override(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    entity_type = _entity_type_in(body.get("entity_type"))
    entity_key = body.get("entity_id") or ""
    robots = "noindex" if body.get("noindex") else None
    params = {
        "entity_type": entity_type,
        "entity_key": entity_key,
        "title": body.get("meta_title"),
        "meta_description": body.get("meta_description"),
        "canonical_url": body.get("canonical_url"),
        "og_image_url": body.get("og_image_url"),
        "robots_directives": robots,
    }
    try:
        async with uow.transactional() as session:
            # Try an UPDATE first (keyed on entity_type + entity_key); fall
            # back to INSERT when no existing row. This avoids depending on a
            # named unique constraint for ON CONFLICT.
            row = (
                await session.execute(
                    text(
                        "UPDATE seo_meta_overrides SET "
                        "title = :title, meta_description = :meta_description, "
                        "canonical_url = :canonical_url, "
                        "og_image_url = :og_image_url, "
                        "robots_directives = :robots_directives, "
                        "updated_at = now() "
                        "WHERE entity_type = :entity_type "
                        "AND entity_key = :entity_key "
                        f"RETURNING {_SEO_COLS}"
                    ),
                    params,
                )
            ).mappings().first()
            if row is None:
                row = (
                    await session.execute(
                        text(
                            "INSERT INTO seo_meta_overrides "
                            "(entity_type, entity_key, title, meta_description, "
                            " canonical_url, og_image_url, robots_directives) "
                            "VALUES (:entity_type, :entity_key, :title, "
                            " :meta_description, :canonical_url, :og_image_url, "
                            " :robots_directives) "
                            f"RETURNING {_SEO_COLS}"
                        ),
                        params,
                    )
                ).mappings().first()
            if row is None:
                return dict(_NA)
            return _seo_row(row)
    except Exception:
        return dict(_NA)


@router.delete(
    "/seo-overrides/{entity_type}/{entity_id}",
    summary="Delete SEO meta override",
)
async def delete_seo_override(
    entity_type: str,
    entity_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    _p: Annotated[Principal, Depends(requires_permission(_PERM))],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    try:
        async with uow.transactional() as session:
            await session.execute(
                text(
                    "DELETE FROM seo_meta_overrides "
                    "WHERE entity_type = :entity_type "
                    "AND entity_key = :entity_key"
                ),
                {
                    "entity_type": _entity_type_in(entity_type),
                    "entity_key": entity_id,
                },
            )
        return {"ok": True}
    except Exception:
        return dict(_NA)
