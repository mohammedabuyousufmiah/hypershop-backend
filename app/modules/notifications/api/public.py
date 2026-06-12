"""Customer-facing notifications inbox."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.notifications.schemas import (
    CustomerNotificationListOut,
    CustomerNotificationOut,
    MarkReadIn,
)
from app.modules.notifications.service import NotificationService

router = APIRouter(prefix="/me/notifications", tags=["notifications"])

_R = "loyalty.read.self"  # reuse — customer-self read


@router.get(
    "",
    response_model=CustomerNotificationListOut,
    dependencies=[Depends(requires_permission(_R))],
)
async def list_mine(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    unread_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> CustomerNotificationListOut:
    async with uow.transactional() as session:
        svc = NotificationService(session)
        items, total, unread = await svc.list_for_user(
            principal.user_id,
            unread_only=unread_only,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
    return CustomerNotificationListOut(
        items=[CustomerNotificationOut.model_validate(n) for n in items],
        total=total,
        unread=unread,
    )


@router.get(
    "/unread-count",
    dependencies=[Depends(requires_permission(_R))],
)
async def unread_count(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict:
    async with uow.transactional() as session:
        svc = NotificationService(session)
        _items, _total, unread = await svc.list_for_user(
            principal.user_id, unread_only=True, offset=0, limit=1
        )
    return {"unread": unread}


@router.post(
    "/mark-read",
    dependencies=[Depends(requires_permission(_R))],
)
async def mark_read(
    body: MarkReadIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict:
    async with uow.transactional() as session:
        svc = NotificationService(session)
        affected = await svc.mark_read(
            user_id=principal.user_id, ids=body.ids, all_unread=body.all
        )
    return {"updated": affected}
