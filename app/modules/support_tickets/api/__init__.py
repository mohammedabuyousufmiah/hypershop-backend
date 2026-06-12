"""Support tickets API package."""
from app.modules.support_tickets.api.admin import router as support_admin_router
from app.modules.support_tickets.api.customer import router as support_router

__all__ = ["support_router", "support_admin_router"]
