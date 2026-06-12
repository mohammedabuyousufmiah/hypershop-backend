"""Env-driven binding for push transports.

Both kinds bind independently — operators can run with FCM only
(BD market), APNS only, or both. Web push currently routes via FCM
(same backend), so binding FCM also serves kind='web'.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.modules.push.transport.push_registry import bind_transport

_logger = get_logger("hypershop.push.factory")


def _secret(value: object) -> str:
    if value is None:
        return ""
    get = getattr(value, "get_secret_value", None)
    if callable(get):
        return str(get() or "")
    return str(value)


def _try_bind_fcm() -> bool:
    from app.core.config import get_settings
    from app.core.errors import IntegrationError

    s = get_settings()
    sa_json = _secret(getattr(s, "fcm_service_account_json", None))
    project_id = getattr(s, "fcm_project_id", None) or None
    if not sa_json:
        _logger.warning("push_fcm_skipped_missing_creds")
        return False
    try:
        from app.modules.push.transport.push_fcm import FcmHttpV1Transport
        adapter = FcmHttpV1Transport(
            service_account_json=sa_json,
            project_id=project_id,
        )
        bind_transport("fcm", adapter)
        bind_transport("web", adapter)  # web push uses FCM too
        return True
    except IntegrationError as e:
        _logger.warning("push_fcm_bind_failed", reason=str(e))
        return False


def _try_bind_apns() -> bool:
    from app.core.config import get_settings
    from app.core.errors import IntegrationError

    s = get_settings()
    team_id = _secret(getattr(s, "apns_team_id", None))
    key_id = _secret(getattr(s, "apns_key_id", None))
    p8 = _secret(getattr(s, "apns_private_key_p8", None))
    bundle_id = _secret(getattr(s, "apns_bundle_id", None))
    is_sandbox = bool(getattr(s, "apns_is_sandbox", False))
    if not all([team_id, key_id, p8, bundle_id]):
        _logger.warning(
            "push_apns_skipped_missing_creds",
            has_team=bool(team_id),
            has_key_id=bool(key_id),
            has_p8=bool(p8),
            has_bundle=bool(bundle_id),
        )
        return False
    try:
        from app.modules.push.transport.push_apns import ApnsTransport
        adapter = ApnsTransport(
            team_id=team_id,
            key_id=key_id,
            private_key_p8=p8,
            bundle_id=bundle_id,
            is_sandbox=is_sandbox,
        )
        bind_transport("apns", adapter)
        return True
    except IntegrationError as e:
        _logger.warning("push_apns_bind_failed", reason=str(e))
        return False


def bind_from_settings() -> dict[str, bool]:
    return {
        "fcm": _try_bind_fcm(),
        "apns": _try_bind_apns(),
    }
