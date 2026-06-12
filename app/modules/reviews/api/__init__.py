"""HTTP routes for the reviews module — phase 1.

Three routers exposed via the module-level ``router`` aggregator:

  * ``customer.py`` — POST + edit + helpful upvote (auth required)
  * ``admin.py``    — moderation queue + approve / reject / disable / reenable
  * ``public.py``   — list + aggregate (anonymous-safe)
"""

from fastapi import APIRouter

from app.modules.reviews.api.admin import router as _admin_router
from app.modules.reviews.api.customer import router as _customer_router
from app.modules.reviews.api.public import router as _public_router

reviews_router = APIRouter()
reviews_router.include_router(_public_router)
reviews_router.include_router(_customer_router)
reviews_router.include_router(_admin_router)

__all__ = ["reviews_router"]
