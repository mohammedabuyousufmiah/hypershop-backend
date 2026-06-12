"""Inventory Manager operational admin routes.

Mounted under ``/api/v1/admin/inventory-ops/``. Mirror of
``finance.api.operations`` — every mutator funnels through
``execute_inventory_action`` so the audit log + matrix gate fire
in the same transaction.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.modules.inventory.operations_actions import InventoryAction
from app.modules.inventory.operations_models import (
    DamagedLostInventory,
    InventoryAuditLog,
    InventoryStock,
    ReturnStockReview,
    StockAdjustmentRequest,
)
from app.modules.inventory.operations_service import (
    InventoryActionPayload,
    InventoryActor,
    InventoryRuleViolation,
    apply_damaged_lost_confirmation,
    apply_return_review_decision,
    apply_stock_adjustment_decision,
    execute_inventory_action,
)


router = APIRouter(
    prefix="/admin/inventory-ops",
    tags=["inventory-operations"],
)


class _ActionBody(BaseModel):
    reason: str = Field(..., min_length=4, max_length=2000)
    evidence_url: str | None = Field(default=None, max_length=512)
    reference_id: str | None = Field(default=None, max_length=80)
    decision_note: str | None = Field(default=None, max_length=2000)
    requested_by: str | None = Field(default=None, max_length=80)
    metadata: dict[str, Any] | None = None


def _actor(principal: Principal) -> InventoryActor:
    roles = list(getattr(principal, "roles", []) or [])
    for preferred in ("super_admin", "admin", "inventory_manager"):
        if preferred in roles:
            return InventoryActor(actor_id=str(principal.user_id), role=preferred)
    return InventoryActor(actor_id=str(principal.user_id),
                          role=roles[0] if roles else "guest")


def _payload_for(
    request: Request,
    body: _ActionBody,
    *,
    entity_type: str,
    entity_id: str,
    new_status: str,
    sku: str | None = None,
    warehouse_id: str | None = None,
    qty_before: int | None = None,
    qty_delta: int | None = None,
    qty_after: int | None = None,
) -> InventoryActionPayload:
    return InventoryActionPayload(
        entity_type=entity_type,
        entity_id=entity_id,
        new_status=new_status,
        reason=body.reason,
        evidence_url=body.evidence_url,
        reference_id=body.reference_id,
        requested_by=body.requested_by,
        sku=sku,
        warehouse_id=warehouse_id,
        qty_before=qty_before,
        qty_delta=qty_delta,
        qty_after=qty_after,
        ip_address=request.client.host if request.client else None,
        device_info=request.headers.get("User-Agent"),
        metadata=body.metadata,
    )


def _raise(e: InventoryRuleViolation) -> None:
    raise HTTPException(
        status_code=e.status_code,
        detail={"code": e.code, "message": e.message},
    )


# ============================================================
#  Stock dashboard reads
# ============================================================
@router.get("/dashboard")
async def dashboard(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, int]:
    """Counts pending operator queues — same shape the spec ships."""
    async with uow.transactional() as session:
        pending_adj = (await session.execute(
            select(StockAdjustmentRequest).where(
                StockAdjustmentRequest.status == "pending",
            ),
        )).scalars().all()
        pending_returns = (await session.execute(
            select(ReturnStockReview).where(
                ReturnStockReview.status == "pending",
            ),
        )).scalars().all()
        pending_damaged = (await session.execute(
            select(DamagedLostInventory).where(
                DamagedLostInventory.status == "pending",
            ),
        )).scalars().all()
        blocked = (await session.execute(
            select(InventoryStock).where(InventoryStock.is_blocked.is_(True)),
        )).scalars().all()
    return {
        "stock_adjustments_pending": len(pending_adj),
        "return_reviews_pending": len(pending_returns),
        "damaged_lost_pending": len(pending_damaged),
        "blocked_skus": len(blocked),
    }


@router.get("/stocks")
async def list_stocks(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    sku: Annotated[str | None, Query()] = None,
    warehouse_id: Annotated[str | None, Query()] = None,
    blocked_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        stmt = select(InventoryStock).limit(limit)
        if sku:
            stmt = stmt.where(InventoryStock.sku == sku)
        if warehouse_id:
            stmt = stmt.where(InventoryStock.warehouse_id == warehouse_id)
        if blocked_only:
            stmt = stmt.where(InventoryStock.is_blocked.is_(True))
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "sku": r.sku,
            "warehouse_id": r.warehouse_id,
            "available": r.available_qty,
            "reserved": r.reserved_qty,
            "damaged": r.damaged_qty,
            "lost": r.lost_qty,
            "quarantine": r.quarantine_qty,
            "blocked": r.is_blocked,
            "low_threshold": r.low_stock_threshold,
        }
        for r in rows
    ]


# ============================================================
#  Stock adjustment requests
# ============================================================
@router.get("/adjustments")
async def list_adjustments(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        stmt = select(StockAdjustmentRequest).order_by(
            StockAdjustmentRequest.created_at.desc(),
        ).limit(limit)
        if status:
            stmt = stmt.where(StockAdjustmentRequest.status == status)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "request_code": r.request_code,
            "sku": r.sku,
            "warehouse_id": r.warehouse_id,
            "direction": r.direction,
            "qty_delta": r.qty_delta,
            "category": r.category,
            "requested_by": r.requested_by,
            "status": r.status,
            "decided_by": r.decided_by,
        }
        for r in rows
    ]


@router.post("/adjustments/{request_id}/approve")
async def approve_adjustment(
    request: Request,
    request_id: Annotated[UUID, Path()],
    body: _ActionBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    actor = _actor(principal)
    async with uow.transactional() as session:
        adj = await session.get(StockAdjustmentRequest, request_id)
        if adj is None:
            raise HTTPException(status_code=404, detail="Adjustment not found.")
        body.requested_by = adj.requested_by
        try:
            audit = await execute_inventory_action(
                session=session, actor=actor,
                action=InventoryAction.APPROVE_STOCK_ADJUSTMENT,
                payload=_payload_for(
                    request, body,
                    entity_type="stock_adjustment_request",
                    entity_id=str(request_id),
                    new_status="approved",
                    sku=adj.sku, warehouse_id=adj.warehouse_id,
                    qty_before=adj.qty_before,
                    qty_delta=adj.qty_delta,
                    qty_after=adj.qty_before + adj.qty_delta,
                ),
            )
            row = await apply_stock_adjustment_decision(
                session, request_id=request_id,
                decided_by=actor.actor_id, audit_log_id=audit.id,
                new_status="approved", decision_note=body.decision_note,
            )
        except InventoryRuleViolation as e:
            _raise(e)
    return {"id": str(row.id), "status": row.status, "audit_code": audit.audit_code}


@router.post("/adjustments/{request_id}/reject")
async def reject_adjustment(
    request: Request,
    request_id: Annotated[UUID, Path()],
    body: _ActionBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    actor = _actor(principal)
    async with uow.transactional() as session:
        adj = await session.get(StockAdjustmentRequest, request_id)
        if adj is None:
            raise HTTPException(status_code=404, detail="Adjustment not found.")
        body.requested_by = adj.requested_by
        try:
            audit = await execute_inventory_action(
                session=session, actor=actor,
                action=InventoryAction.REJECT_STOCK_ADJUSTMENT,
                payload=_payload_for(
                    request, body,
                    entity_type="stock_adjustment_request",
                    entity_id=str(request_id),
                    new_status="rejected",
                    sku=adj.sku, warehouse_id=adj.warehouse_id,
                ),
            )
            row = await apply_stock_adjustment_decision(
                session, request_id=request_id,
                decided_by=actor.actor_id, audit_log_id=audit.id,
                new_status="rejected", decision_note=body.decision_note,
            )
        except InventoryRuleViolation as e:
            _raise(e)
    return {"id": str(row.id), "status": row.status, "audit_code": audit.audit_code}


# ============================================================
#  Return-to-stock reviews
# ============================================================
@router.get("/return-reviews")
async def list_return_reviews(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        stmt = select(ReturnStockReview).order_by(
            ReturnStockReview.created_at.desc(),
        ).limit(limit)
        if status:
            stmt = stmt.where(ReturnStockReview.status == status)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "review_code": r.review_code,
            "order_id": r.order_id,
            "return_id": r.return_id,
            "sku": r.sku,
            "warehouse_id": r.warehouse_id,
            "qty": r.qty,
            "qc_status": r.qc_status,
            "status": r.status,
            "mother_qr_match": r.mother_qr_match,
        }
        for r in rows
    ]


@router.post("/return-reviews/{review_id}/approve")
async def approve_return_review(
    request: Request,
    review_id: Annotated[UUID, Path()],
    body: _ActionBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    actor = _actor(principal)
    async with uow.transactional() as session:
        review = await session.get(ReturnStockReview, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Return review not found.")
        try:
            audit = await execute_inventory_action(
                session=session, actor=actor,
                action=InventoryAction.APPROVE_RETURN_TO_STOCK,
                payload=_payload_for(
                    request, body,
                    entity_type="return_stock_review",
                    entity_id=str(review_id),
                    new_status="approved",
                    sku=review.sku, warehouse_id=review.warehouse_id,
                ),
            )
            row = await apply_return_review_decision(
                session, review_id=review_id,
                decided_by=actor.actor_id, audit_log_id=audit.id,
                new_status="approved", decision_note=body.decision_note,
            )
        except InventoryRuleViolation as e:
            _raise(e)
    return {"id": str(row.id), "status": row.status, "audit_code": audit.audit_code}


# ============================================================
#  Damaged / lost incidents
# ============================================================
@router.get("/damaged-lost")
async def list_damaged_lost(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        stmt = select(DamagedLostInventory).order_by(
            DamagedLostInventory.created_at.desc(),
        ).limit(limit)
        if status:
            stmt = stmt.where(DamagedLostInventory.status == status)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "incident_code": r.incident_code,
            "sku": r.sku,
            "incident_type": r.incident_type,
            "qty": r.qty,
            "responsible": r.responsible_party,
            "status": r.status,
        }
        for r in rows
    ]


@router.post("/damaged-lost/{incident_id}/confirm")
async def confirm_damaged_lost(
    request: Request,
    incident_id: Annotated[UUID, Path()],
    body: _ActionBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    actor = _actor(principal)
    async with uow.transactional() as session:
        incident = await session.get(DamagedLostInventory, incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        try:
            audit = await execute_inventory_action(
                session=session, actor=actor,
                action=InventoryAction.APPROVE_DAMAGED_LOST_INVENTORY_ADJUSTMENT,
                payload=_payload_for(
                    request, body,
                    entity_type="damaged_lost_inventory",
                    entity_id=str(incident_id),
                    new_status="confirmed",
                    sku=incident.sku, warehouse_id=incident.warehouse_id,
                ),
            )
            row = await apply_damaged_lost_confirmation(
                session, incident_id=incident_id,
                confirmed_by=actor.actor_id, audit_log_id=audit.id,
                new_status="confirmed",
            )
        except InventoryRuleViolation as e:
            _raise(e)
    return {"id": str(row.id), "status": row.status, "audit_code": audit.audit_code}


# ============================================================
#  Audit log read
# ============================================================
@router.get("/audit-logs")
async def list_audit_logs(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    entity_type: Annotated[str | None, Query()] = None,
    sku: Annotated[str | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        stmt = select(InventoryAuditLog).order_by(
            InventoryAuditLog.created_at.desc(),
        ).limit(limit)
        if entity_type:
            stmt = stmt.where(InventoryAuditLog.entity_type == entity_type)
        if sku:
            stmt = stmt.where(InventoryAuditLog.sku == sku)
        if action:
            stmt = stmt.where(InventoryAuditLog.action == action)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(r.id),
            "audit_code": r.audit_code,
            "actor_id": r.actor_id,
            "actor_role": r.actor_role,
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "sku": r.sku,
            "qty_delta": r.qty_delta,
            "new_status": r.new_status,
            "reason": r.reason,
        }
        for r in rows
    ]
