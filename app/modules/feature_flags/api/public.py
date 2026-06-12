"""Public-readable feature flag evaluator."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.feature_flags.schemas import (
    FeatureFlagEvaluateIn,
    FeatureFlagEvaluateOut,
)
from app.modules.feature_flags.service import FeatureFlagService

router = APIRouter(prefix="/feature-flags", tags=["feature-flags"])


@router.post("/evaluate", response_model=FeatureFlagEvaluateOut)
async def evaluate(
    body: FeatureFlagEvaluateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> FeatureFlagEvaluateOut:
    async with uow.transactional() as session:
        svc = FeatureFlagService(session)
        result = await svc.evaluate(key=body.key, subject_id=body.subject_id)
    return FeatureFlagEvaluateOut(**result)
