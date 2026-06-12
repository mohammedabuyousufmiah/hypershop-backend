from fastapi import APIRouter

from app.modules.ai.api.admin import router as admin_router

ai_router = APIRouter()
ai_router.include_router(admin_router)

__all__ = ["ai_router"]
