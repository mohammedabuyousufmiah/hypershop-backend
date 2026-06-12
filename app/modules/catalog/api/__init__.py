from fastapi import APIRouter

from app.modules.catalog.api.admin import router as admin_router
from app.modules.catalog.api.public import router as public_router

catalog_router = APIRouter()
catalog_router.include_router(public_router)
catalog_router.include_router(admin_router)

__all__ = ["catalog_router"]
