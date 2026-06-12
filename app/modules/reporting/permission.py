"""Permission decisions for the reporting platform.

A user can do anything iff:
  1. The report is active.
  2. They hold a role listed in ``allowed_roles_json`` OR they have the
     RBAC wildcard ``*`` permission.
  3. For the requested action (view / export / schedule), at least one
     of their roles has that permission set in
     ``report_access_policies``. Multi-role users get the *union* —
     denying via deletion of policy rows works; denying via
     ``can_view=false`` on one role does not (as long as another role
     has it ``true``).
  4. The export format (if any) is in ``allowed_export_formats_json``.

The widest scope across the user's matching policies is used for the
row filter — e.g. an "ops" user with ``self`` and a "finance" role
with ``branch`` gets ``branch`` (broader = more rows visible).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.core.security.principal import Principal, SystemPrincipal
from app.modules.reporting.models import ReportAccessPolicy, ReportDefinition
from app.modules.reporting.state import ScopeType


_SCOPE_RANK = {
    ScopeType.SELF: 0,
    ScopeType.TEAM: 1,
    ScopeType.DEPARTMENT: 2,
    ScopeType.BRANCH: 3,
    ScopeType.GLOBAL: 4,
}


@dataclass(slots=True)
class AccessDecision:
    allowed: bool
    can_view: bool
    can_export: bool
    can_schedule: bool
    scope_type: str
    matched_role_labels: list[str]
    reason: str = ""


def _principal_roles(principal: Principal | SystemPrincipal) -> list[str]:
    return sorted(getattr(principal, "roles", []))


def _is_superuser(principal: Principal | SystemPrincipal) -> bool:
    if isinstance(principal, SystemPrincipal):
        return True
    return "*" in getattr(principal, "permissions", set()) or principal.has_role(
        "super_admin",
    )


def evaluate(
    *,
    principal: Principal | SystemPrincipal,
    definition: ReportDefinition,
    policies: Iterable[ReportAccessPolicy],
) -> AccessDecision:
    """Compute the access decision against a definition + the policy
    rows for that definition.

    Pass *all* policies for the definition (typically <10 rows) — the
    function picks the union across roles the principal holds.
    """
    if not definition.is_active:
        return AccessDecision(
            allowed=False, can_view=False, can_export=False,
            can_schedule=False, scope_type="self",
            matched_role_labels=[], reason="Report is inactive.",
        )

    role_set = set(_principal_roles(principal))
    super_user = _is_superuser(principal)

    allowed_roles = set(definition.allowed_roles_json or [])
    if not super_user and not (role_set & allowed_roles):
        return AccessDecision(
            allowed=False, can_view=False, can_export=False,
            can_schedule=False, scope_type="self",
            matched_role_labels=[],
            reason="Caller's roles are not in allowed_roles for this report.",
        )

    if super_user:
        # Bypass policy table for superusers — they get can_*+global.
        return AccessDecision(
            allowed=True, can_view=True, can_export=True,
            can_schedule=True, scope_type=ScopeType.GLOBAL.value,
            matched_role_labels=sorted(role_set) or ["super_admin"],
        )

    matched: list[ReportAccessPolicy] = [
        p for p in policies if p.role_label in role_set
    ]
    if not matched:
        return AccessDecision(
            allowed=False, can_view=False, can_export=False,
            can_schedule=False, scope_type="self",
            matched_role_labels=[],
            reason="No access policy row matches this user's role(s).",
        )

    can_view = any(p.can_view for p in matched)
    can_export = any(p.can_export for p in matched)
    can_schedule = any(p.can_schedule for p in matched)

    # Widest scope across matched policies wins.
    scope = max(
        (p.scope_type for p in matched),
        key=lambda s: _SCOPE_RANK.get(s, 0),
    )
    return AccessDecision(
        allowed=can_view or can_export or can_schedule,
        can_view=can_view,
        can_export=can_export,
        can_schedule=can_schedule,
        scope_type=scope,
        matched_role_labels=sorted({p.role_label for p in matched}),
    )


def merge_filters(
    *,
    request_filters: dict[str, Any] | None,
    definition: ReportDefinition,
) -> dict[str, Any]:
    """Overlay request filters onto definition defaults.

    Request wins for any overlapping key. Used to keep builders simple
    — they always see a complete dict.
    """
    base = dict(definition.default_filters_json or {})
    if request_filters:
        base.update(request_filters)
    return base
