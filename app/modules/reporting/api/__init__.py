from fastapi import APIRouter

from app.modules.reporting.api.admin import router as admin_router
from app.modules.reporting.api.user import router as user_router

reporting_router = APIRouter()
reporting_router.include_router(user_router)
reporting_router.include_router(admin_router)

__all__ = ["reporting_router"]
