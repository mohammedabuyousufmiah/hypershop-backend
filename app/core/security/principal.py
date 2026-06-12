from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated actor for the current request.

    Roles and permissions come from the access token at issue time, so they
    reflect the user's grants when they logged in. For sensitive operations
    (e.g. deleting a user account) the service layer must re-load and re-check
    rather than trust token claims alone.
    """

    user_id: UUID
    session_id: UUID
    roles: frozenset[str]
    permissions: frozenset[str]
    is_system: ClassVar[bool] = False

    def has_permission(self, perm: str) -> bool:
        return perm in self.permissions or "*" in self.permissions

    def has_role(self, role: str) -> bool:
        return role in self.roles


@dataclass(frozen=True, slots=True)
class SystemPrincipal:
    """Used by background workers, callbacks, and migrations to perform
    privileged actions while still passing through audit + RBAC layers.
    """

    actor: str = "system"
    roles: frozenset[str] = field(default_factory=lambda: frozenset({"system"}))
    permissions: frozenset[str] = field(default_factory=lambda: frozenset({"*"}))
    is_system: ClassVar[bool] = True

    def has_permission(self, perm: str) -> bool:
        return True

    def has_role(self, role: str) -> bool:
        return role in self.roles
