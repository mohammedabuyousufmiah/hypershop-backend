from fastapi import APIRouter

from app.modules.supplier_payments.api.admin import router as admin_router

supplier_payments_router = APIRouter()
supplier_payments_router.include_router(admin_router)

__all__ = ["supplier_payments_router"]
