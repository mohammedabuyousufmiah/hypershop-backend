"""Read-only KPI dashboard endpoint.

    GET /api/v1/kpi-dashboard

Single read-only endpoint. Gated on ``dashboard.read``. Response shape
is :class:`KpiDashboardResponse` regardless of role tier — sections may
be empty but never absent.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission

from .cache import get_or_compute
from .schemas import KpiDashboardResponse, KpiFilters
from .service import KpiDashboardService, resolve_tier

router = APIRouter(prefix="/kpi-dashboard", tags=["kpi-dashboard"])

_READ = "dashboard.read"


@router.get(
    "",
    response_model=KpiDashboardResponse,
    dependencies=[Depends(requires_permission(_READ))],
    summary="Role-scoped read-only KPI dashboard",
)
async def get_kpi_dashboard(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    city_id: Annotated[str | None, Query(max_length=64)] = None,
    branch_id: Annotated[str | None, Query(max_length=64)] = None,
    seller_id: Annotated[str | None, Query()] = None,
    category_id: Annotated[str | None, Query()] = None,
) -> KpiDashboardResponse:
    # Manual construction (filters aren't a body schema, so FastAPI does
    # not auto-translate ValidationError to 422 here — we do it ourselves).
    try:
        filters = KpiFilters(
            date_from=date_from,
            date_to=date_to,
            city_id=city_id,
            branch_id=branch_id,
            seller_id=seller_id,  # type: ignore[arg-type]  # pydantic coerces uuid str
            category_id=category_id,  # type: ignore[arg-type]
        )
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e
    tier = resolve_tier(principal)
    filter_dict = filters.model_dump(mode="json")

    async def _compute() -> dict:
        async with uow.transactional() as session:
            svc = KpiDashboardService(session)
            response = await svc.build(principal=principal, filters=filters)
        return response.model_dump(mode="json")

    payload, was_cached = await get_or_compute(
        tier=tier,
        filters=filter_dict,
        compute=_compute,
    )
    response = KpiDashboardResponse.model_validate(payload)
    # Mutate `cached` on the returned model so the client knows.
    response = response.model_copy(update={"cached": was_cached})
    return response
