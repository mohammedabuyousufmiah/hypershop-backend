from __future__ import annotations

from app.core.security.principal import Principal, SystemPrincipal
from app.modules.iam.models import User
from app.modules.iam.permissions import P_USER_DELETE_ANY, P_USER_READ_ANY, P_USER_UPDATE_ANY


class UserPolicy:
    """Object-level policy for the ``User`` resource.

    Read/update/delete on *self* is always allowed for the authenticated owner.
    Any other actor needs the corresponding ``iam.user.*.any`` permission.
    """

    def can(
        self,
        principal: Principal | SystemPrincipal,
        obj: User,
        action: str,
    ) -> bool:
        if isinstance(principal, SystemPrincipal):
            return True

        is_owner = principal.user_id == obj.id

        if action == "read":
            return is_owner or principal.has_permission(P_USER_READ_ANY)
        if action == "update":
            return is_owner or principal.has_permission(P_USER_UPDATE_ANY)
        if action == "delete":
            # No self-delete; admin only.
            return principal.has_permission(P_USER_DELETE_ANY)
        if action == "assign_role":
            return principal.has_permission("iam.role.assign")
        return False


user_policy = UserPolicy()
