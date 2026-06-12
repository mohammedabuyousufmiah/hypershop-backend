"""Module 49 — Subscriptions / recurring orders."""
from app.modules.subscriptions.api import (
    admin_router as subscriptions_admin_router,
    customer_router as subscriptions_customer_router,
)

__all__ = ["subscriptions_customer_router", "subscriptions_admin_router"]
