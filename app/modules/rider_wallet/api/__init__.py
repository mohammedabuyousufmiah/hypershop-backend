from fastapi import APIRouter

from app.modules.rider_wallet.api.admin import router as admin_router
from app.modules.rider_wallet.api.rider import router as rider_router

rider_wallet_router = APIRouter()
rider_wallet_router.include_router(rider_router)
rider_wallet_router.include_router(admin_router)

__all__ = ["rider_wallet_router"]
