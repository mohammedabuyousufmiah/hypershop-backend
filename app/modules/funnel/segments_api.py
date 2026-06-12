"""Funnel segments + lifecycle analytics API (sprint 15).

Mounted at `/api/v1/admin/funnel/*`. Reuses Module 46's `funnel.view`
permission so funnel-admin operators have access by default.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import Field
from sqlalchemy import text as _t

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import BusinessRuleError, NotFoundError
from app.core.logging import get_logger
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.validation import StrictModel
from app.modules.funnel import segmentation

_log = get_logger("hypershop.funnel.segments")
_ADMIN = "funnel.view"

router = APIRouter(prefix="/admin/funnel", tags=["funnel-segments"])


# ============================================================== Schemas
class SegmentCreate(StrictModel):
    name: str = Field(..., min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    rules: dict[str, Any] = Field(default_factory=dict)


class SegmentUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    rules: dict[str, Any] | None = None
    is_active: bool | None = None


# ============================================================== Segments CRUD
@router.post(
    "/segments",
    status_code=201,
    summary="Create a behavioural funnel segment",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def create_segment(
    body: SegmentCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                "INSERT INTO funnel_segments "
                "(id, name, description, rules, created_by) "
                "VALUES (gen_random_uuid(), :n, :d, CAST(:r AS jsonb), :u) "
                "RETURNING id"
            ),
            {
                "n": body.name, "d": body.description,
                "r": json.dumps(body.rules), "u": principal.user_id,
            },
        )
        sid = r.scalar_one()
        await record_audit(
            actor=principal, action="funnel.segment.created",
            resource_type="funnel_segments", resource_id=sid,
        )
    return {"id": str(sid), "name": body.name, "rules": body.rules}


@router.get(
    "/segments",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def list_segments(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    "SELECT id, name, description, rules, estimated_count, "
                    "counted_at, is_active, created_at FROM funnel_segments "
                    "ORDER BY created_at DESC LIMIT :lim"
                ),
                {"lim": limit},
            )
        ).all()
        return [
            {
                "id": str(r[0]), "name": r[1], "description": r[2],
                "rules": r[3], "estimated_count": int(r[4]),
                "counted_at": r[5], "is_active": r[6], "created_at": r[7],
            }
            for r in rows
        ]


@router.get(
    "/segments/{sid}/preview",
    summary="Evaluate segment rules — count + sample",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def preview_segment(
    sid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                _t("SELECT rules FROM funnel_segments WHERE id = :s"),
                {"s": sid},
            )
        ).first()
        if r is None:
            raise NotFoundError("Segment not found")
        rules = r[0] or {}
        out = await segmentation.preview_segment(session, rules)
        # Cache count
        await session.execute(
            _t(
                "UPDATE funnel_segments SET estimated_count = :c, "
                "counted_at = now() WHERE id = :s"
            ),
            {"c": out["count"], "s": sid},
        )
    return {"segment_id": str(sid), **out}


@router.patch(
    "/segments/{sid}",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def update_segment(
    sid: Annotated[UUID, Path(...)],
    body: SegmentUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise BusinessRuleError("nothing to update")
    parts = []
    params: dict[str, Any] = {"s": sid}
    for k, v in fields.items():
        if k == "rules":
            parts.append("rules = CAST(:rules AS jsonb)")
            params["rules"] = json.dumps(v)
        else:
            parts.append(f"{k} = :{k}")
            params[k] = v
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"UPDATE funnel_segments SET {', '.join(parts)}, updated_at = now() "
                f"WHERE id = :s RETURNING id"
            ),
            params,
        )
        if r.first() is None:
            raise NotFoundError("Segment not found")
        await record_audit(
            actor=principal, action="funnel.segment.updated",
            resource_type="funnel_segments", resource_id=sid,
            metadata={"fields": list(fields.keys())},
        )
    return {"id": str(sid), "updated": list(fields.keys())}


# ============================================================== Lifecycle distribution
@router.get(
    "/lifecycle",
    summary="Customer lifecycle stage distribution (N/B/C/P/L/D)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def lifecycle_distribution(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    """Infers a single-letter lifecycle stage per funnel_customer.

    Stage definitions:
      N  = New      — funnel_customer created in last 7d AND has no
                       events recorded
      B  = Browser  — events present, but no add_to_cart in last 30d
      C  = Cart     — has add_to_cart in last 30d but no order_placed
      P  = Purchaser — order_placed within last 30d
      L  = Lapsed   — purchased before, but nothing in last 90d
      D  = Dormant  — no activity in 180d+
    """
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    """
                    WITH event_facts AS (
                      SELECT customer_id,
                             MAX(created_at) AS last_event_at,
                             BOOL_OR(event_name = 'add_to_cart' AND created_at >= now() - INTERVAL '30 days') AS recent_cart,
                             BOOL_OR(event_name IN ('order_placed','order_completed') AND created_at >= now() - INTERVAL '30 days') AS recent_order,
                             BOOL_OR(event_name IN ('order_placed','order_completed') AND created_at <  now() - INTERVAL '90 days') AS old_order
                      FROM funnel_events
                      GROUP BY customer_id
                    )
                    SELECT
                      CASE
                        WHEN ef.last_event_at IS NULL AND c.created_at >= now() - INTERVAL '7 days' THEN 'N'
                        WHEN ef.last_event_at IS NULL THEN 'D'
                        WHEN ef.last_event_at < now() - INTERVAL '180 days' THEN 'D'
                        WHEN ef.recent_order THEN 'P'
                        WHEN ef.recent_cart THEN 'C'
                        WHEN ef.old_order AND ef.last_event_at < now() - INTERVAL '90 days' THEN 'L'
                        ELSE 'B'
                      END AS stage,
                      COUNT(*) AS n
                    FROM funnel_customers c
                    LEFT JOIN event_facts ef ON ef.customer_id = c.id
                    GROUP BY 1
                    ORDER BY 1
                    """,
                ),
            )
        ).all()
    by_stage = {r[0]: int(r[1]) for r in rows}
    return {
        "stages": by_stage,
        "labels": {
            "N": "New", "B": "Browser", "C": "Cart abandoner",
            "P": "Purchaser", "L": "Lapsed", "D": "Dormant",
        },
        "total": sum(by_stage.values()),
    }


# ============================================================== Drop-off analysis
@router.get(
    "/dropoff",
    summary="Stage drop-off rates: view → cart → checkout → order",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def funnel_dropoff(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    days: int = Query(default=30, ge=1, le=365),
    segment_id: UUID | None = Query(default=None,
                                     description="Optional: restrict to a saved segment"),
) -> dict[str, Any]:
    """Counts distinct customers reaching each funnel stage. If
    segment_id is provided, restricts the universe to that segment's
    members for cohort-aware drop-off analysis.
    """
    cohort_cte = ""
    cohort_join = ""
    cohort_params: dict[str, Any] = {}
    if segment_id:
        async with uow.transactional() as session:
            r = (
                await session.execute(
                    _t("SELECT rules FROM funnel_segments WHERE id = :s"),
                    {"s": segment_id},
                )
            ).first()
            if r is None:
                raise NotFoundError("Segment not found")
            rules = r[0] or {}
        seg_sql, seg_params = segmentation.segment_query_sql(rules)
        cohort_cte = f"WITH cohort AS ({seg_sql}) "
        cohort_join = " AND e.customer_id IN (SELECT id FROM cohort)"
        cohort_params.update(seg_params)
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"""
                    {cohort_cte}
                    SELECT event_name,
                           COUNT(DISTINCT e.customer_id) AS uniq
                    FROM funnel_events e
                    WHERE e.event_name IN
                          ('product_view','add_to_cart','checkout_start','order_placed','order_completed')
                      AND e.created_at >= now() - INTERVAL '{int(days)} days'
                      {cohort_join}
                    GROUP BY event_name
                    """,
                ),
                cohort_params,
            )
        ).all()
    by_event = {r[0]: int(r[1]) for r in rows}
    stages = [
        ("product_view", "Product view"),
        ("add_to_cart", "Added to cart"),
        ("checkout_start", "Checkout started"),
        ("order_placed", "Order placed"),
        ("order_completed", "Order completed"),
    ]
    out_stages = []
    prev_count: int | None = None
    first_count: int | None = None
    for code, label in stages:
        count = by_event.get(code, 0)
        if first_count is None and count > 0:
            first_count = count
        step_pct = (
            round(count / prev_count * 100, 1) if prev_count and prev_count > 0 else None
        )
        cum_pct = (
            round(count / first_count * 100, 1) if first_count and first_count > 0 else None
        )
        out_stages.append({
            "stage": code, "label": label, "count": count,
            "step_conversion_pct": step_pct,
            "cumulative_conversion_pct": cum_pct,
            "dropoff_pct": (
                round((1 - count / prev_count) * 100, 1)
                if prev_count and prev_count > 0 else None
            ),
        })
        if count > 0:
            prev_count = count
    return {
        "window_days": days,
        "segment_id": str(segment_id) if segment_id else None,
        "stages": out_stages,
    }


# ============================================================== Score histogram
@router.get(
    "/score-histogram",
    summary="Engagement-score distribution across funnel customers",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def score_histogram(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    """
                    SELECT
                      width_bucket(COALESCE(current_score, 0), 0, 100, 10) AS bucket,
                      COUNT(*) AS n
                    FROM funnel_customers
                    WHERE deleted_at IS NULL
                    GROUP BY bucket
                    ORDER BY bucket
                    """,
                )
            )
        ).all()
    return {
        "buckets": [
            {
                "score_range": f"{int(r[0]) * 10 - 10}-{int(r[0]) * 10}"
                if r[0] is not None else "unknown",
                "count": int(r[1]),
            }
            for r in rows
        ],
    }
