"""Consent + PII masking — pure functions over the FunnelCustomer model.

PII_MASKING_ENABLED toggles whether the dashboard list endpoints expose
raw phone/email (admin debug only) or masked variants. Default ON.
"""
from __future__ import annotations

import hashlib

from app.core.config import get_settings
from app.modules.funnel.models import FunnelCustomer
from app.modules.funnel.security import mask_email, mask_phone


def sha256_or_none(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def can_contact(customer: FunnelCustomer, channel: str) -> tuple[bool, str | None]:
    if customer.deleted_at:
        return False, "customer_deleted"

    if channel == "whatsapp":
        if not customer.marketing_consent or not customer.whatsapp_consent:
            return False, "missing_whatsapp_or_marketing_consent"
    elif channel == "sms":
        if not customer.marketing_consent or not customer.sms_consent:
            return False, "missing_sms_or_marketing_consent"
    elif channel == "ad":
        if not customer.ad_retargeting_consent:
            return False, "missing_ad_retargeting_consent"

    return True, None


def customer_to_safe_dict(customer: FunnelCustomer) -> dict:
    settings = get_settings()
    masking_on = getattr(settings, "funnel_pii_masking_enabled", True)
    phone = mask_phone(customer.phone) if masking_on else customer.phone
    email = mask_email(customer.email) if masking_on else customer.email
    return {
        "id": customer.id,
        "external_customer_id": customer.external_customer_id,
        "hypershop_customer_id": customer.hypershop_customer_id,
        "name": customer.name,
        "phone": phone,
        "email": email,
        "marketing_consent": customer.marketing_consent,
        "whatsapp_consent": customer.whatsapp_consent,
        "sms_consent": customer.sms_consent,
        "ad_retargeting_consent": customer.ad_retargeting_consent,
        "current_score": customer.current_score,
        "segment": customer.segment,
        "last_event_name": customer.last_event_name,
    }
