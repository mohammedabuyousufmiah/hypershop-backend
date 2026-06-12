"""HTTP routes for the sellers module.

Phase 1 (admin-only) and phase 3 (seller-self-serve) routers live in
their own files. ``main.py`` mounts both under the API prefix.
"""

from app.modules.sellers.api.admin import router as sellers_router
from app.modules.sellers.api.seller import router as seller_self_router

__all__ = ["sellers_router", "seller_self_router"]
