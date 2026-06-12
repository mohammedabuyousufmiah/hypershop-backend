"""Module 48 — Marketing automation.

Audience segmentation + campaign creation + multi-channel dispatch
(WhatsApp / SMS / email / in-app). Reuses outbound senders from the
customer_care module.
"""
from app.modules.marketing.api import router as marketing_router

__all__ = ["marketing_router"]
