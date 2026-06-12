from fastapi import APIRouter

from app.modules.analytics.api.public import router as public_router
from app.modules.analytics.api.admin import router as admin_router

analytics_router = APIRouter()
analytics_router.include_router(public_router)
analytics_router.include_router(admin_router)

__all__ = ["analytics_router"]
