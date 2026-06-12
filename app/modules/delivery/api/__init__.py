from fastapi import APIRouter

from app.modules.delivery.api.admin import router as admin_router
from app.modules.delivery.api.public import router as public_router

delivery_router = APIRouter()
delivery_router.include_router(public_router)
delivery_router.include_router(admin_router)

__all__ = ["delivery_router"]
