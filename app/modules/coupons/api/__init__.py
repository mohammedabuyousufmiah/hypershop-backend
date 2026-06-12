from fastapi import APIRouter

from app.modules.coupons.api.admin import router as admin_router
from app.modules.coupons.api.public import router as public_router

coupons_router = APIRouter()
coupons_router.include_router(public_router)
coupons_router.include_router(admin_router)

__all__ = ["coupons_router"]
