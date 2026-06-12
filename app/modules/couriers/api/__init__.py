"""Couriers API package — admin + webhook surfaces."""
from app.modules.couriers.api.admin import router as admin_router
from app.modules.couriers.api.webhooks import router as webhook_router

__all__ = ["admin_router", "webhook_router"]
