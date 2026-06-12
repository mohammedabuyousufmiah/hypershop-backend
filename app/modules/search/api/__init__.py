from fastapi import APIRouter

from app.modules.search.api.admin import router as admin_router
from app.modules.search.api.public import router as public_router

search_router = APIRouter()
search_router.include_router(public_router)
search_router.include_router(admin_router)

__all__ = ["search_router"]
