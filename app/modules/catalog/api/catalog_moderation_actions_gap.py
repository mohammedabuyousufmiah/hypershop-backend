"""Gap-fill admin ACTION endpoint for the catalog-moderation FE surface.

This module is self-contained and boot-safe. It is registered centrally in
``main.py`` (see structured result). It backs the single row-level mutation the
moderation queue performs on its own resource:

  * POST /catalog/products/{product_id}/moderate  -> ProductAdminWire

The moderation queue (``catalog_moderation_gap.py``) DERIVES a ``ModerationStatus``
from the real ``products.status`` column because this build has no
``moderation_status`` column:

  draft    -> PENDING   (awaiting review/publish)
  active   -> APPROVED
  archived -> REJECTED

To stay consistent with that read, the moderate action writes ``products.status``:

  action="approve" -> status='active'   (+ stamp published_at if not yet set)
  action="reject"  -> status='archived'

This mirrors the typed SDK call ``api.catalogAdmin.moderateProduct`` which POSTs
``{action, note}`` to ``/catalog/products/{id}/moderate`` (see FE
``AdminCatalogClient.tsx`` ModerateModal). The ``note`` is accepted but, with no
moderation-audit table in this build, is not persisted — the row mutation is the
load-bearing effect.

Design notes
------------
* Raw ``text()`` SQL against the real ``products`` table; only symbols known to
  exist are imported at module load, keeping the file import-safe.
* Each mutation runs in an explicit transaction. Any DB error (missing
  table/column) degrades to a clean ``200 {"ok": false, "reason": ...}`` instead
  of a 500.
* Same ``requires_permission`` gate the GET gap file uses (``catalog.product.write``).
* ``Idempotency-Key`` header is accepted (optional) for parity with the FE, which
  sends one on POST; re-applying the same status transition is naturally
  idempotent so no dedup store is required.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Header
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

router = APIRouter(prefix="/catalog", tags=["admin-catalog-moderation-actions"])

# Same write perm the GET gap router (and the existing catalog admin GETs) hold.
_PERM = "catalog.product.write"


class ProductModerateIn(BaseModel):
    """Body of ``POST /catalog/products/{id}/moderate`` (mirrors FE ProductModerateIn)."""

    action: Literal["approve", "reject"]
    note: str | None = None


def _derive_moderation_status(status_value: str | None) -> str:
    """Map ``products.status`` to the FE ModerationStatus union (read parity)."""
    s = (status_value or "").lower()
    if s == "active":
        return "APPROVED"
    if s == "archived":
        return "REJECTED"
    if s == "draft":
        return "PENDING"
    return "DRAFT"


@router.post(
    "/products/{product_id}/moderate",
    dependencies=[Depends(requires_permission(_PERM))],
)
async def moderate_product(
    product_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: ProductModerateIn = Body(...),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Approve or reject a product by transitioning ``products.status``.

    approve -> status='active' (and stamp ``published_at`` if still NULL)
    reject  -> status='archived'

    Returns the updated row as ``ProductAdminWire``. Defensive: a missing
    table/column or unknown id yields ``200 {"ok": false, "reason": ...}``
    rather than a 500.
    """
    new_status = "active" if body.action == "approve" else "archived"

    try:
        async with uow.transactional() as session:
            # Transition status; stamp published_at on first approve only.
            result = await session.execute(
                text(
                    """
                    UPDATE products
                    SET status = :new_status,
                        published_at = CASE
                            WHEN :new_status = 'active' AND published_at IS NULL
                                THEN now()
                            ELSE published_at
                        END
                    WHERE id = CAST(:pid AS uuid)
                    RETURNING id::text AS id, status AS status
                    """
                ),
                {"new_status": new_status, "pid": product_id},
            )
            updated = result.mappings().first()
            if updated is None:
                return {"ok": False, "reason": "not_found"}

            # Re-read with joins to return the full ProductAdminWire shape.
            row = (
                await session.execute(
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
                        WHERE p.id = CAST(:pid AS uuid)
                        """
                    ),
                    {"pid": product_id},
                )
            ).mappings().first()
    except Exception:
        # Missing table/column or invalid id form — never 500.
        return {"ok": False, "reason": "not_available"}

    if row is None:
        return {"ok": False, "reason": "not_found"}

    return {
        "id": row["id"],
        "slug": row["slug"],
        "title": row["title"],
        "brand": row["brand"],
        "brand_id": row["brand_id"],
        "category_path": row["category_path"],
        "moderation_status": _derive_moderation_status(row["status"]),
        "is_active": bool(row["is_active"]),
    }
