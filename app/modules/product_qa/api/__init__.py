"""HTTP routes for the product Q&A module — phase 3."""

from fastapi import APIRouter

from app.modules.product_qa.api.admin import router as _admin_router
from app.modules.product_qa.api.customer import router as _customer_router
from app.modules.product_qa.api.public import router as _public_router

qa_router = APIRouter()
qa_router.include_router(_public_router)
qa_router.include_router(_customer_router)
qa_router.include_router(_admin_router)

__all__ = ["qa_router"]
