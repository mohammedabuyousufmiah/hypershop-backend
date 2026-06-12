from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.errors import ForbiddenError
from app.core.security.principal import Principal, SystemPrincipal


@runtime_checkable
class ObjectPolicy[ResourceT](Protocol):
    """Object-level authorization policy.

    Modules implement one ``ObjectPolicy`` per resource type and call
    ``authorize(principal, obj, action, policy)`` from their service layer.
    RBAC (permission strings) gates the endpoint; this gates the specific
    instance.
    """

    def can(
        self,
        principal: Principal | SystemPrincipal,
        obj: ResourceT,
        action: str,
    ) -> bool: ...


def authorize[ResourceT](
    principal: Principal | SystemPrincipal,
    obj: ResourceT,
    action: str,
    policy: ObjectPolicy[ResourceT],
) -> None:
    """Raise ForbiddenError if the policy denies.

    System principals still pass through the policy so audit captures the
    actor; policies should return True for system unless the action is
    explicitly user-only.
    """
    if not policy.can(principal, obj, action):
        raise ForbiddenError(
            "Action not permitted on this resource.",
            details={"action": action, "resource_type": type(obj).__name__},
        )
