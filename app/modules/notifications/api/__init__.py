from fastapi import APIRouter

from app.modules.notifications.api.public import router as public_router

notifications_router = APIRouter()
notifications_router.include_router(public_router)

__all__ = ["notifications_router"]
