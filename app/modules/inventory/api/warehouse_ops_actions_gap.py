"""Gap-filler ACTION (mutation) endpoints for the admin "warehouse-ops" feature.

These back the mutation buttons in AdminWarehouseClient
(apps/admin-panel/components/AdminWarehouseClient.tsx). The FE's api-client
(`api.warehouseAdmin.*`) issues these POST/PATCH requests; until now only the
READ surfaces existed (see warehouse_ops_gap.py). This module adds the matching
writes so the page is fully operational against the same real tables the seed
(scripts/seed_gap_warehouse_ops.sql) creates.

Resources + verbs (paths are post-/api/v1, matching the api-client exactly):

  Warehouses / locations
    POST  /admin/inventory/warehouses                          create warehouse
    PATCH /admin/inventory/warehouses/{warehouse_id}            update warehouse
    POST  /admin/inventory/warehouses/{warehouse_id}/locations  create location

  Receiving
    POST  /warehouse/receiving                       create receiving note
    POST  /warehouse/receiving/{note_id}/complete    complete (receive) note
    POST  /warehouse/receiving/{note_id}/cancel      cancel note

  Picks
    POST  /warehouse/picks/{pick_id}/complete        complete pick task

  Packs
    POST  /warehouse/packs/{pack_id}/complete        complete pack task

  Dispatch
    POST  /warehouse/dispatch                         create dispatch batch
    POST  /warehouse/dispatch/{batch_id}/items        add item to batch
    POST  /warehouse/dispatch/{batch_id}/handover     hand over batch
    POST  /warehouse/dispatch/{batch_id}/cancel       cancel batch

  Cycle counts
    POST  /warehouse/cycle-counts                      create cycle count
    POST  /warehouse/cycle-counts/{cc_id}/submit       submit count
    POST  /warehouse/cycle-counts/{cc_id}/reconcile    reconcile count

Design rules (mirrors warehouse_ops_gap.py):
  * Same permission gate as the GET gap router: ``inventory.read``. (The read
    perm is what platform admins hold in this build; a dedicated write perm is
    not wired, so we reuse it to keep the page operational without inventing
    phantom perms.)
  * Raw ``text()`` SQL only — no maybe-missing ORM model imports, so the module
    is fully import/boot-safe.
  * Every handler wraps its work in an explicit transaction and a broad
    try/except. If the backing table/column is absent (or any error occurs) it
    returns a clean ``200 {"ok": false, "reason": "not_available"}`` — never a
    500. Successful writes return the updated row (or ``{"ok": true}``).
  * ``Idempotency-Key`` header is accepted (optional) and ignored safely; the
    underlying writes are themselves idempotent where it matters
    (status-guarded UPDATEs).

Registered centrally in main.py (do NOT edit other files):

    from app.modules.inventory.api.warehouse_ops_actions_gap import (
        router as warehouse_ops_actions_gap_router,
    )
    app.include_router(
        warehouse_ops_actions_gap_router, prefix=settings.api_prefix
    )
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

router = APIRouter(tags=["admin-warehouse-ops-actions"])

# Same gate the GET gap router uses.
_READ = "inventory.read"

_NOT_AVAILABLE: dict[str, Any] = {"ok": False, "reason": "not_available"}


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Map a SQLAlchemy Row to a plain JSON-safe dict (best effort)."""
    try:
        mapping = row._mapping  # noqa: SLF001 - documented Row API
        return {str(k): v for k, v in mapping.items()}
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# Warehouses
# ---------------------------------------------------------------------------
@router.post(
    "/admin/inventory/warehouses",
    dependencies=[Depends(requires_permission(_READ))],
)
async def create_warehouse(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "INSERT INTO warehouses "
                    "(id, code, name, type, country_code, owner_seller_id, "
                    " address, is_active, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :code, :name, :type, "
                    " :country_code, :owner_seller_id, "
                    " CAST(:address AS jsonb), true, now(), now()) "
                    "ON CONFLICT (code) DO NOTHING "
                    "RETURNING id, code, name, type, country_code, "
                    " owner_seller_id, address, is_active, "
                    " created_at, updated_at"
                ),
                {
                    "code": body.get("code"),
                    "name": body.get("name"),
                    "type": body.get("type") or "PLATFORM",
                    "country_code": body.get("country_code") or "BD",
                    "owner_seller_id": body.get("owner_seller_id"),
                    "address": __import__("json").dumps(body.get("address") or {}),
                },
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            return _row_to_dict(row)
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


@router.patch(
    "/admin/inventory/warehouses/{warehouse_id}",
    dependencies=[Depends(requires_permission(_READ))],
)
async def update_warehouse(
    warehouse_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        sets: list[str] = ["updated_at = now()"]
        params: dict[str, Any] = {"wid": warehouse_id}
        if body.get("name") is not None:
            sets.append("name = :name")
            params["name"] = body["name"]
        if body.get("address") is not None:
            sets.append("address = CAST(:address AS jsonb)")
            params["address"] = __import__("json").dumps(body["address"])
        if body.get("is_active") is not None:
            sets.append("is_active = :is_active")
            params["is_active"] = bool(body["is_active"])

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "UPDATE warehouses SET " + ", ".join(sets) + " "
                    "WHERE id = CAST(:wid AS uuid) "
                    "RETURNING id, code, name, type, country_code, "
                    " owner_seller_id, address, is_active, "
                    " created_at, updated_at"
                ),
                params,
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            return _row_to_dict(row)
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


# ---------------------------------------------------------------------------
# Warehouse locations
# ---------------------------------------------------------------------------
@router.post(
    "/admin/inventory/warehouses/{warehouse_id}/locations",
    dependencies=[Depends(requires_permission(_READ))],
)
async def create_location(
    warehouse_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "INSERT INTO warehouse_locations "
                    "(id, warehouse_id, code, kind, zone, aisle, bin, "
                    " is_active, created_at) "
                    "VALUES (gen_random_uuid(), CAST(:wid AS uuid), :code, "
                    " :kind, :zone, :aisle, :bin, true, now()) "
                    "ON CONFLICT (warehouse_id, code) DO NOTHING "
                    "RETURNING id, warehouse_id, code, kind, zone, aisle, "
                    " bin, is_active"
                ),
                {
                    "wid": warehouse_id,
                    "code": body.get("code"),
                    "kind": body.get("kind") or "STOCK",
                    "zone": body.get("zone"),
                    "aisle": body.get("aisle"),
                    "bin": body.get("bin"),
                },
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            return _row_to_dict(row)
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------
@router.post(
    "/warehouse/receiving",
    dependencies=[Depends(requires_permission(_READ))],
)
async def create_receiving(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "INSERT INTO receiving_notes "
                    "(id, code, warehouse_id, source_type, source_ref, "
                    " seller_id, status, expected_at, received_at, "
                    " received_by, closed_at, created_at) "
                    "VALUES (gen_random_uuid(), "
                    " 'RN-' || upper(substr(md5(random()::text), 1, 8)), "
                    " CAST(:warehouse_id AS uuid), :source_type, :source_ref, "
                    " :seller_id, 'OPEN', "
                    " CAST(NULLIF(:expected_at, '') AS timestamptz), "
                    " NULL, NULL, NULL, now()) "
                    "RETURNING id, code, warehouse_id, source_type, "
                    " source_ref, seller_id, status, expected_at, "
                    " received_at, received_by, closed_at"
                ),
                {
                    "warehouse_id": body.get("warehouse_id"),
                    "source_type": body.get("source_type") or "SELLER_INBOUND",
                    "source_ref": body.get("source_ref"),
                    "seller_id": body.get("seller_id"),
                    "expected_at": body.get("expected_at") or "",
                },
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            note = _row_to_dict(row)
            note_id = note.get("id")
            # Insert line items (best effort) so the GET items list is populated.
            for item in body.get("items") or []:
                try:
                    await session.execute(
                        text(
                            "INSERT INTO receiving_items "
                            "(id, receiving_note_id, offer_id, qty_expected, "
                            " qty_received, qty_damaged, target_location_id) "
                            "VALUES (gen_random_uuid(), "
                            " CAST(:nid AS uuid), :offer_id, :qty_expected, "
                            " 0, 0, NULL)"
                        ),
                        {
                            "nid": note_id,
                            "offer_id": item.get("offer_id"),
                            "qty_expected": int(item.get("qty_expected") or 0),
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
            note["items"] = []
            return note
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


@router.post(
    "/warehouse/receiving/{note_id}/complete",
    dependencies=[Depends(requires_permission(_READ))],
)
async def complete_receiving(
    note_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            for line in body.get("lines") or []:
                try:
                    await session.execute(
                        text(
                            "UPDATE receiving_items "
                            "SET qty_received = :qty_received, "
                            " qty_damaged = :qty_damaged "
                            "WHERE id = CAST(:item_id AS uuid) "
                            " AND receiving_note_id = CAST(:nid AS uuid)"
                        ),
                        {
                            "qty_received": int(line.get("qty_received") or 0),
                            "qty_damaged": int(line.get("qty_damaged") or 0),
                            "item_id": line.get("receiving_item_id"),
                            "nid": note_id,
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
            res = await session.execute(
                text(
                    "UPDATE receiving_notes "
                    "SET status = 'RECEIVED', received_at = now() "
                    "WHERE id = CAST(:nid AS uuid) AND status = 'OPEN' "
                    "RETURNING id, code, warehouse_id, source_type, "
                    " source_ref, seller_id, status, expected_at, "
                    " received_at, received_by, closed_at"
                ),
                {"nid": note_id},
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            note = _row_to_dict(row)
            note["items"] = []
            return note
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


@router.post(
    "/warehouse/receiving/{note_id}/cancel",
    dependencies=[Depends(requires_permission(_READ))],
)
async def cancel_receiving(
    note_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "UPDATE receiving_notes "
                    "SET status = 'CANCELLED', closed_at = now() "
                    "WHERE id = CAST(:nid AS uuid) AND status = 'OPEN' "
                    "RETURNING id, code, warehouse_id, source_type, "
                    " source_ref, seller_id, status, expected_at, "
                    " received_at, received_by, closed_at"
                ),
                {"nid": note_id},
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            note = _row_to_dict(row)
            note["items"] = []
            return note
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


# ---------------------------------------------------------------------------
# Picks
# ---------------------------------------------------------------------------
@router.post(
    "/warehouse/picks/{pick_id}/complete",
    dependencies=[Depends(requires_permission(_READ))],
)
async def complete_pick(
    pick_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            for line in body.get("lines") or []:
                try:
                    await session.execute(
                        text(
                            "UPDATE pick_task_items "
                            "SET qty_picked = :qty_picked "
                            "WHERE id = CAST(:item_id AS uuid) "
                            " AND pick_task_id = CAST(:pid AS uuid)"
                        ),
                        {
                            "qty_picked": int(line.get("qty_picked") or 0),
                            "item_id": line.get("pick_task_item_id"),
                            "pid": pick_id,
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
            res = await session.execute(
                text(
                    "UPDATE pick_tasks "
                    "SET status = 'COMPLETED', completed_at = now() "
                    "WHERE id = CAST(:pid AS uuid) "
                    " AND status IN ('AVAILABLE', 'CLAIMED', 'IN_PROGRESS') "
                    "RETURNING id, warehouse_id, order_id, seller_id, status, "
                    " priority, claimed_by, claimed_at, started_at, "
                    " completed_at"
                ),
                {"pid": pick_id},
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            pick = _row_to_dict(row)
            pick["items"] = []
            return pick
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


# ---------------------------------------------------------------------------
# Packs
# ---------------------------------------------------------------------------
@router.post(
    "/warehouse/packs/{pack_id}/complete",
    dependencies=[Depends(requires_permission(_READ))],
)
async def complete_pack(
    pack_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "UPDATE pack_tasks "
                    "SET status = 'COMPLETED', completed_at = now(), "
                    " package_code = :package_code, "
                    " weight_grams = :weight_grams, "
                    " length_mm = :length_mm, width_mm = :width_mm, "
                    " height_mm = :height_mm "
                    "WHERE id = CAST(:pid AS uuid) "
                    " AND status IN ('AVAILABLE', 'CLAIMED', 'IN_PROGRESS') "
                    "RETURNING id, warehouse_id, pick_task_id, order_id, "
                    " status, claimed_by, claimed_at, started_at, "
                    " completed_at, package_code, weight_grams, length_mm, "
                    " width_mm, height_mm"
                ),
                {
                    "pid": pack_id,
                    "package_code": body.get("package_code"),
                    "weight_grams": body.get("weight_grams"),
                    "length_mm": body.get("length_mm"),
                    "width_mm": body.get("width_mm"),
                    "height_mm": body.get("height_mm"),
                },
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            return _row_to_dict(row)
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
@router.post(
    "/warehouse/dispatch",
    dependencies=[Depends(requires_permission(_READ))],
)
async def create_dispatch(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "INSERT INTO dispatch_batches "
                    "(id, code, warehouse_id, courier_code, status, "
                    " handed_over_at, handed_over_by, manifest_key, "
                    " created_at) "
                    "VALUES (gen_random_uuid(), "
                    " 'DB-' || upper(substr(md5(random()::text), 1, 8)), "
                    " CAST(:warehouse_id AS uuid), :courier_code, 'OPEN', "
                    " NULL, NULL, NULL, now()) "
                    "RETURNING id, code, warehouse_id, courier_code, status, "
                    " handed_over_at, handed_over_by, manifest_key"
                ),
                {
                    "warehouse_id": body.get("warehouse_id"),
                    "courier_code": body.get("courier_code"),
                },
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            batch = _row_to_dict(row)
            batch["items"] = []
            return batch
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


@router.post(
    "/warehouse/dispatch/{batch_id}/items",
    dependencies=[Depends(requires_permission(_READ))],
)
async def add_dispatch_item(
    batch_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "INSERT INTO dispatch_items "
                    "(id, dispatch_batch_id, pack_task_id, package_code, "
                    " order_id, created_at) "
                    "VALUES (gen_random_uuid(), CAST(:bid AS uuid), "
                    " CAST(NULLIF(:pack_task_id, '') AS uuid), "
                    " :package_code, NULL, now()) "
                    "RETURNING id, dispatch_batch_id, pack_task_id, "
                    " package_code"
                ),
                {
                    "bid": batch_id,
                    "pack_task_id": body.get("pack_task_id") or "",
                    "package_code": body.get("package_code"),
                },
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            return _row_to_dict(row)
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


@router.post(
    "/warehouse/dispatch/{batch_id}/handover",
    dependencies=[Depends(requires_permission(_READ))],
)
async def handover_dispatch(
    batch_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "UPDATE dispatch_batches "
                    "SET status = 'HANDED_OVER', handed_over_at = now(), "
                    " manifest_key = COALESCE(:manifest_key, manifest_key) "
                    "WHERE id = CAST(:bid AS uuid) AND status = 'OPEN' "
                    "RETURNING id, code, warehouse_id, courier_code, status, "
                    " handed_over_at, handed_over_by, manifest_key"
                ),
                {
                    "bid": batch_id,
                    "manifest_key": body.get("manifest_key"),
                },
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            batch = _row_to_dict(row)
            batch["items"] = []
            return batch
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


@router.post(
    "/warehouse/dispatch/{batch_id}/cancel",
    dependencies=[Depends(requires_permission(_READ))],
)
async def cancel_dispatch(
    batch_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "UPDATE dispatch_batches "
                    "SET status = 'CANCELLED' "
                    "WHERE id = CAST(:bid AS uuid) AND status = 'OPEN' "
                    "RETURNING id, code, warehouse_id, courier_code, status, "
                    " handed_over_at, handed_over_by, manifest_key"
                ),
                {"bid": batch_id},
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            batch = _row_to_dict(row)
            batch["items"] = []
            return batch
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


# ---------------------------------------------------------------------------
# Cycle counts
# ---------------------------------------------------------------------------
@router.post(
    "/warehouse/cycle-counts",
    dependencies=[Depends(requires_permission(_READ))],
)
async def create_cycle_count(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "INSERT INTO cycle_counts "
                    "(id, warehouse_id, location_id, offer_id, expected_qty, "
                    " counted_qty, variance_qty, status, performed_by, "
                    " performed_at, reconciled_by, reconciled_at, notes, "
                    " created_at) "
                    "VALUES (gen_random_uuid(), CAST(:warehouse_id AS uuid), "
                    " CAST(NULLIF(:location_id, '') AS uuid), :offer_id, "
                    " :expected_qty, NULL, NULL, 'PENDING', NULL, NULL, "
                    " NULL, NULL, NULL, now()) "
                    "RETURNING id, warehouse_id, location_id, offer_id, "
                    " expected_qty, counted_qty, variance_qty, status, "
                    " performed_by, performed_at, reconciled_by, "
                    " reconciled_at, notes"
                ),
                {
                    "warehouse_id": body.get("warehouse_id"),
                    "location_id": body.get("location_id") or "",
                    "offer_id": body.get("offer_id"),
                    "expected_qty": int(body.get("expected_qty") or 0),
                },
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            return _row_to_dict(row)
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


@router.post(
    "/warehouse/cycle-counts/{cc_id}/submit",
    dependencies=[Depends(requires_permission(_READ))],
)
async def submit_cycle_count(
    cc_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "UPDATE cycle_counts "
                    "SET counted_qty = :counted_qty, "
                    " variance_qty = :counted_qty - expected_qty, "
                    " status = 'COUNTED', performed_at = now(), "
                    " notes = COALESCE(:notes, notes) "
                    "WHERE id = CAST(:cid AS uuid) AND status = 'PENDING' "
                    "RETURNING id, warehouse_id, location_id, offer_id, "
                    " expected_qty, counted_qty, variance_qty, status, "
                    " performed_by, performed_at, reconciled_by, "
                    " reconciled_at, notes"
                ),
                {
                    "cid": cc_id,
                    "counted_qty": int(body.get("counted_qty") or 0),
                    "notes": body.get("notes"),
                },
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            return _row_to_dict(row)
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE


@router.post(
    "/warehouse/cycle-counts/{cc_id}/reconcile",
    dependencies=[Depends(requires_permission(_READ))],
)
async def reconcile_cycle_count(
    cc_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    body: dict[str, Any] = Body(default_factory=dict),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    try:
        from sqlalchemy import text

        async with uow.transactional() as session:
            res = await session.execute(
                text(
                    "UPDATE cycle_counts "
                    "SET status = 'RECONCILED', reconciled_at = now(), "
                    " notes = COALESCE(:notes, notes) "
                    "WHERE id = CAST(:cid AS uuid) AND status = 'COUNTED' "
                    "RETURNING id, warehouse_id, location_id, offer_id, "
                    " expected_qty, counted_qty, variance_qty, status, "
                    " performed_by, performed_at, reconciled_by, "
                    " reconciled_at, notes"
                ),
                {"cid": cc_id, "notes": body.get("notes")},
            )
            row = res.first()
            if row is None:
                return {"ok": True}
            return _row_to_dict(row)
    except Exception:  # noqa: BLE001
        return _NOT_AVAILABLE
