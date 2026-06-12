from fastapi import APIRouter

from app.modules.loyalty.api.admin import router as admin_router
from app.modules.loyalty.api.public import router as public_router

loyalty_router = APIRouter()
loyalty_router.include_router(public_router)
loyalty_router.include_router(admin_router)

__all__ = ["loyalty_router"]
