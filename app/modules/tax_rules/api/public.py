"""Tax preview — no auth."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.tax_rules.schemas import TaxPreviewIn, TaxPreviewOut
from app.modules.tax_rules.service import TaxRuleService

router = APIRouter(prefix="/pricing", tags=["pricing"])


@router.post("/tax-preview", response_model=TaxPreviewOut)
async def tax_preview(
    body: TaxPreviewIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> TaxPreviewOut:
    async with uow.transactional() as session:
        svc = TaxRuleService(session)
        result = await svc.preview(
            subtotal_minor=body.subtotal_minor,
            country=body.country,
            category_slug=body.category_slug,
        )
    return TaxPreviewOut(**result)
