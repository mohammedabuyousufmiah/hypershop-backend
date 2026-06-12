from fastapi import APIRouter

from app.modules.payments.api.admin import router as admin_router
from app.modules.payments.api.customer import router as customer_router
from app.modules.payments.api.webhooks import router as webhooks_router

payments_router = APIRouter()
payments_router.include_router(customer_router)
payments_router.include_router(admin_router)
payments_router.include_router(webhooks_router)

__all__ = ["payments_router"]
