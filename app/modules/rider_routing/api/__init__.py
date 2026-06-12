from fastapi import APIRouter

from app.modules.rider_routing.api.admin import router as admin_router
from app.modules.rider_routing.api.rider import router as rider_router

rider_routing_router = APIRouter()
rider_routing_router.include_router(rider_router)
rider_routing_router.include_router(admin_router)

__all__ = ["rider_routing_router"]
