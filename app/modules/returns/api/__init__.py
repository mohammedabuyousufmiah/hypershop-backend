from fastapi import APIRouter

from app.modules.returns.api.admin import router as admin_router
from app.modules.returns.api.customer import router as customer_router

returns_router = APIRouter()
returns_router.include_router(customer_router)
returns_router.include_router(admin_router)

__all__ = ["returns_router"]
