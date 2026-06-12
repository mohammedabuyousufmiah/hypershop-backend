from fastapi import APIRouter

from app.modules.packing.api.admin import router as admin_router

packing_router = APIRouter()
packing_router.include_router(admin_router)

__all__ = ["packing_router"]
