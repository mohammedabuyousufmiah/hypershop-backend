"""FastAPI dependencies for seller-scoped endpoints — phase 3.

Resolves the caller's seller_id at request time (not via JWT claim)
so the link is always fresh. Rotating a token isn't needed when an
admin unlinks a user from a seller — the next request just gets a 403.

The trade-off (one extra SQL per request) is fine at expected
volumes; the seller dashboard is low-traffic compared to the public
catalog.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ForbiddenError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.modules.sellers.authz import seller_id_for_user
from app.modules.sellers.codes import STATUS_APPROVED
from app.modules.sellers.models import Seller


async def get_current_seller_id(
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> UUID:
    """Return the approved seller_id this user is linked to, or 403.

    Admins (``*`` permission) get a 403 here too — they should use
    the ``/admin/sellers/*`` endpoints, not the seller-self-serve
    surface. Mixing the two roles' UIs would let an admin
    accidentally edit a real seller's row through the wrong path.
    """
    async with uow.transactional() as session:
        sid = await seller_id_for_user(session, principal.user_id)
        if sid is None:
            raise ForbiddenError(
                "This endpoint is only available to seller accounts.",
                details={"reason": "no_seller_link"},
            )
        # Block suspended / pending / rejected sellers from the
        # dashboard. They can still log in (the IAM session is fine)
        # but the seller-scoped surface is gated on `approved`.
        seller = await session.get(Seller, sid)
        if seller is None or seller.status != STATUS_APPROVED:
            raise ForbiddenError(
                "Your seller account is not approved.",
                details={
                    "reason": "seller_not_approved",
                    "status": seller.status if seller else None,
                },
            )
    return sid
