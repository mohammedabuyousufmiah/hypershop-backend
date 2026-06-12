"""Admin tax rules CRUD."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission
from app.modules.tax_rules.schemas import (
    TaxRuleCreateIn,
    TaxRuleListOut,
    TaxRuleOut,
)
from app.modules.tax_rules.service import TaxRuleService

router = APIRouter(prefix="/admin/pricing", tags=["admin-pricing"])

_W = "pricing.write"


@router.get("/tax-rules", response_model=TaxRuleListOut)
async def list_rules(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=200),
) -> TaxRuleListOut:
    async with uow.transactional() as session:
        svc = TaxRuleService(session)
        items, total = await svc.list_all(
            offset=(page - 1) * page_size, limit=page_size
        )
    return TaxRuleListOut(
        items=[TaxRuleOut.model_validate(r) for r in items], total=total
    )


@router.post(
    "/tax-rules",
    response_model=TaxRuleOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_W))],
)
async def create_rule(
    body: TaxRuleCreateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> TaxRuleOut:
    async with uow.transactional() as session:
        svc = TaxRuleService(session)
        row = await svc.create(**body.model_dump())
    return TaxRuleOut.model_validate(row)


@router.delete(
    "/tax-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_W))],
)
async def delete_rule(
    rule_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> None:
    async with uow.transactional() as session:
        svc = TaxRuleService(session)
        await svc.delete(rule_id)
