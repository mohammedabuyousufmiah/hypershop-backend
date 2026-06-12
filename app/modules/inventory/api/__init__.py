from fastapi import APIRouter

from app.modules.inventory.api.admin import router as admin_router
from app.modules.inventory.api.operations import router as operations_router

inventory_router = APIRouter()
inventory_router.include_router(admin_router)
inventory_router.include_router(operations_router)

__all__ = ["inventory_router"]
