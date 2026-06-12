from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends

from app.core.errors import ForbiddenError
from app.core.security.principal import Principal


def requires_permission(*needed: str) -> Callable[[Principal], Principal]:
    """FastAPI dependency factory: enforces that the caller has every listed
    permission. If any is missing, raises ForbiddenError (mapped to 403).
    Wildcards: a principal holding "*" satisfies any check.
    """

    if not needed:
        raise ValueError("requires_permission needs at least one permission string")

    async def _checker(
        principal: Annotated[Principal, Depends(_principal_dep_placeholder)],
    ) -> Principal:
        missing = [p for p in needed if not principal.has_permission(p)]
        if missing:
            raise ForbiddenError(
                f"Missing required permission(s): {', '.join(missing)}",
                details={"missing_permissions": missing},
            )
        return principal

    return _checker


def requires_role(*roles: str) -> Callable[[Principal], Principal]:
    if not roles:
        raise ValueError("requires_role needs at least one role")

    async def _checker(
        principal: Annotated[Principal, Depends(_principal_dep_placeholder)],
    ) -> Principal:
        if not any(principal.has_role(r) for r in roles):
            raise ForbiddenError(
                f"Requires role one of: {', '.join(roles)}",
                details={"required_roles": list(roles)},
            )
        return principal

    return _checker


# Resolved at import time in app.core.security.deps to avoid circular imports.
_principal_dep_placeholder: Callable[..., Principal]  # type: ignore[assignment]


def _set_principal_dep(dep: Callable[..., Principal]) -> None:
    """Wired by `app.core.security.deps` once it's importable."""
    global _principal_dep_placeholder
    _principal_dep_placeholder = dep
