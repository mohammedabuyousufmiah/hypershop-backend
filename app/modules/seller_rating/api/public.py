"""Public seller rating endpoint — storefront badge data."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.seller_rating import repository as repo
from app.modules.seller_rating.schemas import SellerRatingPublic

router = APIRouter(prefix="/seller-ratings", tags=["seller-ratings-public"])


@router.get("/{seller_id}", summary="Public seller rating (score + tier)")
async def read_public(
    seller_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        row = await repo.get_rating(session, seller_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Rating not found.")
    return SellerRatingPublic.model_validate(row).model_dump(mode="json")
