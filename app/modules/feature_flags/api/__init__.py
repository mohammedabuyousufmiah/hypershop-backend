from fastapi import APIRouter

from app.modules.feature_flags.api.admin import router as admin_router
from app.modules.feature_flags.api.public import router as public_router

feature_flags_router = APIRouter()
feature_flags_router.include_router(public_router)
feature_flags_router.include_router(admin_router)

__all__ = ["feature_flags_router"]
