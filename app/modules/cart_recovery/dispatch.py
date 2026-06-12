"""Channel dispatch — soft-fails to log_only when transport creds are missing."""
from __future__ import annotations

import logging

_log = logging.getLogger("hypershop.cart_recovery.dispatch")


async def send_whatsapp(*, to_phone: str, body: str) -> dict:
    """Returns {'status','provider_id','reason'}; never raises."""
    try:
        from app.core.config import get_settings
        cfg = get_settings()
        if not getattr(cfg, "meta_whatsapp_phone_number_id", None):
            _log.info(
                "cart_recovery_whatsapp_log_only to=%s body=%r",
                to_phone, body[:120],
            )
            return {"status": "log_only", "provider_id": None, "reason": "no_creds"}
        try:
            from app.modules.whatsapp_webhook import service as wa_service
            if hasattr(wa_service, "send_outbound"):
                r = await wa_service.send_outbound(to=to_phone, body=body)
                return {
                    "status": "sent",
                    "provider_id": getattr(r, "id", None),
                    "reason": None,
                }
        except ImportError:
            pass
        _log.info("cart_recovery_whatsapp_log_only_fallback to=%s", to_phone)
        return {"status": "log_only", "provider_id": None, "reason": "no_send_helper"}
    except Exception as e:  # noqa: BLE001
        _log.warning("cart_recovery_whatsapp_failed to=%s err=%s", to_phone, e)
        return {"status": "failed", "provider_id": None, "reason": str(e)[:200]}


async def send_email(*, to_email: str, subject: str, body: str) -> dict:
    """Soft-fail SMTP send; log-only when no smtp_host."""
    try:
        from app.core.config import get_settings
        cfg = get_settings()
        host = getattr(cfg, "smtp_host", None)
        if not host or host == "localhost":
            _log.info(
                "cart_recovery_email_log_only to=%s subject=%r",
                to_email, (subject or "")[:80],
            )
            return {"status": "log_only", "provider_id": None, "reason": "no_smtp"}
        _log.info("cart_recovery_email_log_only_fallback to=%s", to_email)
        return {"status": "log_only", "provider_id": None, "reason": "no_send_helper"}
    except Exception as e:  # noqa: BLE001
        _log.warning("cart_recovery_email_failed to=%s err=%s", to_email, e)
        return {"status": "failed", "provider_id": None, "reason": str(e)[:200]}


async def send_push(*, user_id: str, title: str, body: str) -> dict:
    """Soft-fail FCM push; log-only when no fcm credentials."""
    try:
        from app.core.config import get_settings
        cfg = get_settings()
        has_fcm = (
            getattr(cfg, "fcm_service_account_json", None)
            or getattr(cfg, "fcm_credentials_path", None)
        )
        if not has_fcm:
            _log.info(
                "cart_recovery_push_log_only user=%s body=%r",
                user_id, body[:80],
            )
            return {"status": "log_only", "provider_id": None, "reason": "no_fcm"}
        _log.info("cart_recovery_push_log_only_fallback user=%s", user_id)
        return {"status": "log_only", "provider_id": None, "reason": "no_send_helper"}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "provider_id": None, "reason": str(e)[:200]}
