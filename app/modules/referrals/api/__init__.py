from fastapi import APIRouter

from app.modules.referrals.api.admin import router as admin_router
from app.modules.referrals.api.public import router as public_router

referrals_router = APIRouter()
referrals_router.include_router(public_router)
referrals_router.include_router(admin_router)

__all__ = ["referrals_router"]
