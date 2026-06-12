from fastapi import APIRouter

from app.modules.orders.api.admin import router as admin_router
from app.modules.orders.api.customer import router as customer_router

orders_router = APIRouter()
orders_router.include_router(customer_router)
orders_router.include_router(admin_router)

__all__ = ["orders_router"]
