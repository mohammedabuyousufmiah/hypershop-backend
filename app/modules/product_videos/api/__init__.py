from fastapi import APIRouter

from app.modules.product_videos.api.admin import router as admin_router
from app.modules.product_videos.api.public import router as public_router

product_videos_router = APIRouter()
product_videos_router.include_router(public_router)
product_videos_router.include_router(admin_router)

__all__ = ["product_videos_router"]
