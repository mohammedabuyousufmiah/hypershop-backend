from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from app.core.audit.service import record_audit
from app.core.errors import DomainError
from app.core.security.principal import Principal, SystemPrincipal

P = ParamSpec("P")
R = TypeVar("R")


def audited(
    action: str,
    *,
    resource_type: str | None = None,
    actor_arg: str = "actor",
    resource_id_arg: str | None = None,
    extract_metadata: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Wrap a service method so its outcome is audited inside the same
    transaction.

    The decorated method MUST be called inside an active
    ``UnitOfWork.transactional()`` scope; the audit row commits/rolls back
    atomically with the rest of the work. On any exception (DomainError or
    not), an audit row with ``outcome="failure"`` is recorded BEFORE the
    exception propagates.

    Args:
        action: machine-readable action name, e.g. "user.login".
        resource_type: optional human label for the resource being acted on.
        actor_arg: kwarg name where the Principal/SystemPrincipal is passed.
        resource_id_arg: kwarg name where the resource id is passed.
        extract_metadata: optional fn receiving (*args, **kwargs) → dict for
            auxiliary metadata. Sensitive keys are redacted server-side.
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            actor: Principal | SystemPrincipal | None = kwargs.get(actor_arg)  # type: ignore[assignment]
            resource_id = kwargs.get(resource_id_arg) if resource_id_arg else None
            metadata = extract_metadata(*args, **kwargs) if extract_metadata else None
            try:
                result = await fn(*args, **kwargs)
            except DomainError as e:
                await record_audit(
                    actor=actor,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    outcome="failure",
                    metadata={**(metadata or {}), "error_code": e.code, "error_message": e.message},
                )
                raise
            except Exception:
                await record_audit(
                    actor=actor,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    outcome="failure",
                    metadata={**(metadata or {}), "error_code": "internal_error"},
                )
                raise
            else:
                await record_audit(
                    actor=actor,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    outcome="success",
                    metadata=metadata,
                )
                return result

        return wrapper

    return decorator
