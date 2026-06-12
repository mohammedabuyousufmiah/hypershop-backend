"""Module 47 — AI Customer Care + Sales Automation."""
# Side-effect: registers outbox subscribers
from app.modules.customer_care import handlers as _cc_handlers  # noqa: F401
from app.modules.customer_care.api.extras import router as _extras_router
from app.modules.customer_care.api.kb_csat import router as _kb_csat_router
from app.modules.customer_care.api.nice import router as _nice_router
from app.modules.customer_care.api.sprint5 import router as _sprint5_router
from app.modules.customer_care.api.sprint6 import router as _sprint6_router
from app.modules.customer_care.api.sprint7 import router as _sprint7_router
from app.modules.customer_care.api.router import router as customer_care_router
# Migration 0073 — unified inbox + voice-call sessions + CSAT (2026-05-18).
from app.modules.customer_care.api.inbox import router as cc_inbox_router
from app.modules.customer_care.api.voice_calls_admin import (
    router as cc_voice_calls_router,
)
from app.modules.customer_care.api.csat_admin import (
    router as cc_csat_router,
)
# SIM-gateway webhook receiver — Android shop-phone gateway POSTs call
# events here to populate voice_call_sessions in real time.
from app.modules.customer_care.api.sim_gateway_webhook import (
    router as cc_sim_gateway_router,
)

customer_care_router.include_router(_kb_csat_router)
customer_care_router.include_router(_extras_router)
customer_care_router.include_router(_nice_router)
customer_care_router.include_router(_sprint5_router)
customer_care_router.include_router(_sprint6_router)
customer_care_router.include_router(_sprint7_router)

__all__ = [
    "customer_care_router",
    "cc_inbox_router",
    "cc_voice_calls_router",
    "cc_csat_router",
    "cc_sim_gateway_router",
]
