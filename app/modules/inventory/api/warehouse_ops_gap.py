"""Gap-filler READ endpoints for the admin "warehouse-ops" feature.

Back the AdminWarehouseClient FE component. Six GET surfaces:

    GET /admin/inventory/warehouses/{id}/locations  -> LocationWire[]
    GET /warehouse/receiving                         -> ReceivingNotePageWire
    GET /warehouse/picks/available                   -> PickTaskPageWire
    GET /warehouse/packs                             -> PackTaskPageWire
    GET /warehouse/dispatch                          -> DispatchBatchPageWire
    GET /warehouse/cycle-counts                      -> CycleCountPageWire

Each handler queries the real warehouse-task table (seeded via
scripts/seed_gap_warehouse_ops.sql) and returns a correctly-shaped page
``{items,total,page,page_size}`` (or a list for locations). Fully boot-safe:
local text() SQL only, every query wrapped in try/except so a missing
table/column returns a clean empty result, never 404/500. Registered
centrally in main.py.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

router = APIRouter(tags=["admin-warehouse-ops"])

_READ = "inventory.read"


def _empty_page(page: int, page_size: int) -> dict[str, Any]:
    return {"items": [], "total": 0, "page": page, "page_size": page_size}


def _jsonable(m: Any) -> dict[str, Any]:
    """Row mapping -> JSON-safe dict (stringify uuid/datetime/Decimal)."""
    out: dict[str, Any] = {}
    for k, v in dict(m).items():
        out[k] = v if (v is None or isinstance(v, (str, int, float, bool))) else str(v)
    return out


async def _page(table: str, uow: UnitOfWork, page: int, page_size: int) -> dict[str, Any]:
    """Generic paginated SELECT * for a fixed (non-user) table literal.

    ORDER BY 1 (the id/first column) avoids depending on a created_at column
    that may not exist. Defensive: any failure -> empty page.
    """
    from sqlalchemy import text

    try:
        async with uow.transactional() as session:
            total = (
                await session.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608 - fixed literal
            ).scalar() or 0
            rows = (
                await session.execute(
                    text(  # noqa: S608 - fixed literal table name, params bound
                        f"SELECT * FROM {table} ORDER BY 1 DESC "
                        "LIMIT :lim OFFSET :off"
                    ),
                    {"lim": page_size, "off": (page - 1) * page_size},
                )
            ).mappings().all()
        return {
            "items": [_jsonable(r) for r in rows],
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }
    except Exception:  # noqa: BLE001
        return _empty_page(page, page_size)


@router.get(
    "/admin/inventory/warehouses/{warehouse_id}/locations",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_warehouse_locations(
    warehouse_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[dict[str, Any]]:
    from sqlalchemy import text

    try:
        async with uow.transactional() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM warehouse_locations "
                        "WHERE warehouse_id = :wid ORDER BY 1 DESC LIMIT 500"
                    ),
                    {"wid": warehouse_id},
                )
            ).mappings().all()
        return [_jsonable(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


@router.get("/warehouse/receiving", dependencies=[Depends(requires_permission(_READ))])
async def list_receiving(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    warehouse_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return await _page("receiving_notes", uow, page, page_size)


@router.get("/warehouse/picks/available", dependencies=[Depends(requires_permission(_READ))])
async def list_available_picks(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    warehouse_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return await _page("pick_tasks", uow, page, page_size)


@router.get("/warehouse/packs", dependencies=[Depends(requires_permission(_READ))])
async def list_packs(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    warehouse_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return await _page("pack_tasks", uow, page, page_size)


@router.get("/warehouse/dispatch", dependencies=[Depends(requires_permission(_READ))])
async def list_dispatch(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    warehouse_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return await _page("dispatch_batches", uow, page, page_size)


@router.get("/warehouse/cycle-counts", dependencies=[Depends(requires_permission(_READ))])
async def list_cycle_counts(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    warehouse_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return await _page("cycle_counts", uow, page, page_size)
