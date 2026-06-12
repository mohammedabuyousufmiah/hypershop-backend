from fastapi import APIRouter

from app.modules.finance.api.admin import router as admin_router
from app.modules.finance.api.operations import router as operations_router

finance_router = APIRouter()
finance_router.include_router(admin_router)
finance_router.include_router(operations_router)

__all__ = ["finance_router"]
