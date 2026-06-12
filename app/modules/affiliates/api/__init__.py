from fastapi import APIRouter

from app.modules.affiliates.api.admin import router as admin_router
from app.modules.affiliates.api.public import router as public_router

affiliates_router = APIRouter()
affiliates_router.include_router(public_router)
affiliates_router.include_router(admin_router)

__all__ = ["affiliates_router"]
