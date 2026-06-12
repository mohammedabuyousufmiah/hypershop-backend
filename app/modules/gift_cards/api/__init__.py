from fastapi import APIRouter

from app.modules.gift_cards.api.admin import router as admin_router
from app.modules.gift_cards.api.public import router as public_router

gift_cards_router = APIRouter()
gift_cards_router.include_router(public_router)
gift_cards_router.include_router(admin_router)

__all__ = ["gift_cards_router"]
