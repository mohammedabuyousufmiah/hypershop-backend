from fastapi import APIRouter

from app.modules.dashboard.api.admin import router as admin_router
from app.modules.dashboard.api.pluggable import router as pluggable_router

dashboard_router = APIRouter()
dashboard_router.include_router(admin_router)
dashboard_router.include_router(pluggable_router)

__all__ = ["dashboard_router"]
