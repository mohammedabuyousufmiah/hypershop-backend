from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.audit.models import AuditLog
from app.core.db.uow import current_session
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal

_logger = get_logger("hypershop.audit")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            kl = k.lower()
            if kl in {
                "password",
                "new_password",
                "old_password",
                "token",
                "secret",
                "otp",
                "otp_code",
                "card_number",
                "cvv",
                "pin",
                "authorization",
            }:
                out[k] = "***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


async def record_audit(
    *,
    actor: Principal | SystemPrincipal | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | UUID | None = None,
    outcome: str = "success",
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Insert an audit row in the *current* transaction. If no transaction
    exists this raises (audit must never be best-effort).
    """
    sess = current_session()

    actor_id: UUID | None
    actor_kind: str
    if actor is None:
        actor_id, actor_kind = None, "anonymous"
    elif isinstance(actor, SystemPrincipal):
        actor_id, actor_kind = None, "system"
    else:
        actor_id, actor_kind = actor.user_id, "user"

    row = AuditLog(
        actor_id=actor_id,
        actor_kind=actor_kind,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        outcome=outcome,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=(user_agent[:512] if user_agent else None),
        metadata_=_redact(metadata or {}),
    )
    sess.add(row)
    await sess.flush()
    _logger.info(
        "audit",
        action=action,
        outcome=outcome,
        actor_kind=actor_kind,
        actor_id=str(actor_id) if actor_id else None,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        request_id=request_id,
    )
    return row


class AuditService:
    """Thin object form for callers that prefer DI over a free function."""

    async def record(
        self,
        *,
        actor: Principal | SystemPrincipal | None,
        action: str,
        resource_type: str | None = None,
        resource_id: str | UUID | None = None,
        outcome: str = "success",
        request_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        return await record_audit(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata=metadata,
        )
