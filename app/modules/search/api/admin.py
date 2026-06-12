"""Admin search endpoints — manual reindex + analytics queries."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.search.schemas import ReindexResponse
from app.modules.search.service import SearchService

router = APIRouter(prefix="/admin/search", tags=["admin-search"])

# Reuse the catalog-write permission since reindex is essentially a
# catalog-management action (writes derived data based on catalog state).
_ADMIN = "catalog.write"


@router.post(
    "/reindex",
    response_model=ReindexResponse,
    summary="Wipe + rebuild the entire search index from catalog state",
    description=(
        "Heavy operation — runs in one transaction so a failed rebuild "
        "leaves the previous index intact. Also runs nightly at 03:00 "
        "UTC via the worker cron, so manual rebuild is only needed "
        "after a bulk catalog import or to recover from index drift."
    ),
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def reindex(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReindexResponse:
    t0 = time.monotonic()
    async with uow.transactional() as session:
        svc = SearchService(session)
        counts = await svc.rebuild_full_index(principal=principal)
    return ReindexResponse(
        documents_indexed=sum(counts.values()),
        by_type=counts,
        seconds=round(time.monotonic() - t0, 2),
    )
