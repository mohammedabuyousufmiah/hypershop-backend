from fastapi import APIRouter

from app.modules.mobile.api.customer import router as customer_router
from app.modules.mobile.api.public import router as public_router

mobile_router = APIRouter()
mobile_router.include_router(public_router)
mobile_router.include_router(customer_router)

__all__ = ["mobile_router"]
