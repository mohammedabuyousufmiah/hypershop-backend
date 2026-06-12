from fastapi import APIRouter

from app.modules.tax_rules.api.admin import router as admin_router
from app.modules.tax_rules.api.public import router as public_router

tax_rules_router = APIRouter()
tax_rules_router.include_router(public_router)
tax_rules_router.include_router(admin_router)

__all__ = ["tax_rules_router"]
