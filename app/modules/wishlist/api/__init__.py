from fastapi import APIRouter

from app.modules.wishlist.api.public import router as public_router

wishlist_router = APIRouter()
wishlist_router.include_router(public_router)

__all__ = ["wishlist_router"]
