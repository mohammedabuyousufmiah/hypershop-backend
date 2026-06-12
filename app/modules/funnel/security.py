"""Funnel auth gates — **production**: admin JWT + RBAC permissions.

The dashboard surface (``/api/v1/funnel/*``) is now protected by the
same JWT-bearer flow every other admin route in the Master Bundle uses
(``app.core.security.deps.get_current_principal`` →
``Principal.has_permission``). The demo ``X-HYPERSHOP-FUNNEL-KEY`` +
``X-HYPERSHOP-ROLE`` header gates that shipped with the source zip are
gone.

Three permission strings drive everything:

* ``funnel.view``   — read /kpi/* + /customers + /followup-tasks
* ``funnel.track``  — write /events/track
* ``funnel.export`` — call /retargeting/export (audience download)

Grant ``funnel.*`` (the wildcard) for super-admins, or any subset to
narrower roles. Permission claims travel inside the JWT issued by the
IAM module; missing claims → 403 ForbiddenError; missing token → 401
UnauthenticatedError. No header secrets, no in-browser API keys.

Backwards-incompatible. Any caller that was authenticating via header
will need to switch to ``Authorization: Bearer <admin-jwt>``. The
customer-web dashboard does this through the new server-side proxy at
``/api/admin/funnel/[...path]`` so the bearer never reaches the browser.

The PII maskers (``mask_phone`` / ``mask_email``) stay here — they're
import targets for ``services/privacy.py`` and have nothing to do with
the auth swap.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission

# Permission constants — exported so docs/admin-role-seed scripts can
# import them rather than re-spelling the dotted strings.
PERM_VIEW = "funnel.view"
PERM_TRACK = "funnel.track"
PERM_EXPORT = "funnel.export"


# Pre-built dependencies. Use directly as ``Depends(require_view)``
# in route signatures rather than calling the factory at every site.
require_view = requires_permission(PERM_VIEW)
require_track = requires_permission(PERM_TRACK)
require_export = requires_permission(PERM_EXPORT)


# Convenience: principal accessor for endpoints that need the user id
# (e.g. retargeting export audit log). Endpoints that only need the
# permission gate should depend on ``require_view`` etc. directly.
async def current_funnel_principal(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> Principal:
    return principal


def mask_phone(phone: str | None) -> str | None:
    """Masking helper for dashboard list endpoints. Production caveat:
    masking is for ad-hoc human inspection only — the underlying row
    still has the raw phone, so any service that queries the table
    directly bypasses this. For full PII protection use the postgres
    row-level encryption track (out of scope for funnel phase 1)."""
    if not phone:
        return None
    if len(phone) <= 4:
        return "****"
    return f"{phone[:3]}****{phone[-3:]}"


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    name, domain = email.split("@", 1)
    safe_name = name[:2] + "***" if len(name) > 2 else "***"
    return f"{safe_name}@{domain}"
