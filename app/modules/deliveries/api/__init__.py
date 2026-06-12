from fastapi import APIRouter

from app.modules.deliveries.api.admin import router as admin_router
from app.modules.deliveries.api.rider import router as rider_router

deliveries_router = APIRouter()
deliveries_router.include_router(admin_router)
deliveries_router.include_router(rider_router)

__all__ = ["deliveries_router"]
