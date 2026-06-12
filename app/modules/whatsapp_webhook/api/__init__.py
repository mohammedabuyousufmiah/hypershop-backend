from fastapi import APIRouter

from app.modules.whatsapp_webhook.api.webhook import router as _webhook_router

whatsapp_webhook_router = APIRouter()
whatsapp_webhook_router.include_router(_webhook_router)

__all__ = ["whatsapp_webhook_router"]
