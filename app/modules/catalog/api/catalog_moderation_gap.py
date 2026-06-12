"""Gap-fill admin GET endpoints for the catalog-moderation FE surface.

This module is self-contained and boot-safe. It is registered centrally in
``main.py`` (see structured result). It backs two FE reads that the existing
catalog admin router does not yet expose:

  * GET /catalog/moderation/queue  -> ProductAdminWire[]
  * GET /catalog/attributes        -> AttributeWire[]

Design notes
------------
* The ``products`` table has NO ``moderation_status`` column in this build;
  product lifecycle is tracked by ``status`` (draft/active/archived). We DERIVE
  a ``ModerationStatus`` from ``status`` so the moderation queue renders real
  rows instead of 404-ing:  draft -> PENDING, active -> APPROVED,
  archived -> REJECTED. The FE's moderation queue intentionally shows PENDING
  rows as actionable, which maps to draft products awaiting publish.
* There is NO attribute-definition table (product ``attributes`` is a JSONB bag
  on the row, not a normalized catalog of attribute definitions). The
  ``/catalog/attributes`` endpoint therefore returns an empty list of the
  correct shape so the FE connects and renders an empty state.
* Every query is wrapped in try/except and uses raw ``text()`` SQL so a missing
  table/column degrades to an empty list instead of a 500. Only symbols known
  to exist are imported at module load, keeping the file import-safe.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

router = APIRouter(prefix="/catalog", tags=["admin-catalog-moderation"])

# Read perm that catalog admins hold (the existing catalog admin GETs were
# tightened to this exact constant on 2026-05-16).
_PERM = "catalog.product.write"


def _derive_moderation_status(status_value: str | None) -> str:
    """Map the real ``products.status`` enum to the FE ModerationStatus union.

    draft -> PENDING (awaiting publish / review)
    active -> APPROVED
    archived -> REJECTED
    anything else -> DRAFT
    """
    s = (status_value or "").lower()
    if s == "active":
        return "APPROVED"
    if s == "archived":
        return "REJECTED"
    if s == "draft":
        return "PENDING"
    return "DRAFT"


@router.get(
    "/moderation/queue",
    dependencies=[Depends(requires_permission(_PERM))],
)
async def list_moderation_queue(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """Return products for the admin moderation queue as ``ProductAdminWire[]``.

    Defensive: any DB error (missing table/column) yields an empty list rather
    than a 500, so the FE renders an empty state.
    """
    try:
        async with uow.transactional() as session:
            result = await session.execute(
                text(
                    """
                    SELECT
                        p.id::text          AS id,
                        p.slug              AS slug,
                        p.name              AS title,
                        b.name              AS brand,
                        p.brand_id::text    AS brand_id,
                        c.name              AS category_path,
                        p.status            AS status,
                        (p.status = 'active') AS is_active
                    FROM products p
                    LEFT JOIN brands b ON b.id = p.brand_id
                    LEFT JOIN categories c ON c.id = p.category_id
                    ORDER BY p.created_at DESC NULLS LAST
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"limit": limit, "offset": offset},
            )
            rows = result.mappings().all()
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "slug": r["slug"],
                "title": r["title"],
                "brand": r["brand"],
                "brand_id": r["brand_id"],
                "category_path": r["category_path"],
                "moderation_status": _derive_moderation_status(r["status"]),
                "is_active": bool(r["is_active"]),
            }
        )
    return out


@router.get(
    "/attributes",
    dependencies=[Depends(requires_permission(_PERM))],
)
async def list_attributes(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """Return attribute definitions as ``AttributeWire[]``.

    No attribute-definition table exists in this build (product attributes are a
    JSONB bag, not a normalized catalog), so we attempt to read an
    ``attribute_definitions`` table if one is ever added and otherwise return an
    empty list of the correct shape. The endpoint never 500s on a missing table.
    """
    try:
        async with uow.transactional() as session:
            result = await session.execute(
                text(
                    """
                    SELECT
                        a.id::text          AS id,
                        a.slug              AS slug,
                        a.name              AS name,
                        a.description       AS description,
                        a.data_type         AS data_type,
                        a.is_active         AS is_active,
                        a.created_at        AS created_at,
                        a.updated_at        AS updated_at
                    FROM attribute_definitions a
                    WHERE (:include_inactive OR a.is_active = true)
                    ORDER BY a.name ASC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {
                    "include_inactive": include_inactive,
                    "limit": limit,
                    "offset": offset,
                },
            )
            rows = result.mappings().all()
            # Load options for all attributes (small catalog → fetch all + group).
            opt_result = await session.execute(
                text(
                    """
                    SELECT id::text AS id, attribute_id::text AS attribute_id,
                           value_code, display_label, position, is_active
                    FROM attribute_options
                    ORDER BY attribute_id, position, display_label
                    """
                )
            )
            opt_rows = opt_result.mappings().all()
    except Exception:
        # No attribute-definition table in this build — correct empty shape.
        return []

    opts_by_attr: dict[str, list[dict[str, Any]]] = {}
    for o in opt_rows:
        opts_by_attr.setdefault(o["attribute_id"], []).append(
            {
                "id": o["id"],
                "attribute_id": o["attribute_id"],
                "value_code": o["value_code"],
                "display_label": o["display_label"],
                "position": o["position"],
                "is_active": bool(o["is_active"]),
            }
        )

    out: list[dict[str, Any]] = []
    for r in rows:
        created = r["created_at"]
        updated = r["updated_at"]
        out.append(
            {
                "id": r["id"],
                "slug": r["slug"],
                "name": r["name"],
                "description": r["description"],
                "data_type": (r["data_type"] or "STRING"),
                "is_active": bool(r["is_active"]),
                "created_at": created.isoformat() if created is not None else None,
                "updated_at": updated.isoformat() if updated is not None else None,
                "options": opts_by_attr.get(r["id"], []),
            }
        )
    return out
