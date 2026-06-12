from fastapi import APIRouter

from app.modules.fraud.api.admin import router as admin_router

fraud_router = APIRouter()
fraud_router.include_router(admin_router)

__all__ = ["fraud_router"]
