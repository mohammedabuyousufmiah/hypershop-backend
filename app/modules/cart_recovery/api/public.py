"""Public (no-auth) endpoints for cart_recovery — self-serve opt-out."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select

from app.core.config import get_settings
from app.core.db.uow import UnitOfWork, get_uow
from app.core.ratelimit import RateLimit, RateLimiter
from app.modules.cart_recovery.models import HypershopCartRecoverySuppression
from app.modules.cart_recovery.schemas import OptOutRequest, OptOutResponse
from app.modules.iam.api.deps import request_context
from app.modules.iam.service import RequestContext

router = APIRouter(prefix="/cart-recovery", tags=["cart-recovery-public"])


def _rate_limiter() -> RateLimiter:
    return RateLimiter()


@router.post(
    "/opt-out",
    response_model=OptOutResponse,
    summary="Self-serve opt-out by email or phone (no auth)",
)
async def opt_out(
    body: OptOutRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    limiter: Annotated[RateLimiter, Depends(_rate_limiter)],
) -> OptOutResponse:
    cfg = get_settings()
    ip = ctx.ip_address or "anonymous"
    is_loopback_dev = (
        not cfg.is_production
        and ip in ("127.0.0.1", "::1", "localhost", "anonymous")
    )
    if not is_loopback_dev:
        await limiter.check(
            "cart_recovery_optout_ip",
            ip,
            RateLimit(capacity=10, window_seconds=3600),
        )

    async with uow.transactional() as session:
        conds = []
        if body.key_email:
            conds.append(HypershopCartRecoverySuppression.key_email == body.key_email)
        if body.key_phone:
            conds.append(HypershopCartRecoverySuppression.key_phone == body.key_phone)
        existing = (
            await session.execute(
                select(HypershopCartRecoverySuppression).where(
                    or_(*conds),
                    HypershopCartRecoverySuppression.channel == body.channel,
                    HypershopCartRecoverySuppression.reason == "opted_out",
                ).limit(1),
            )
        ).scalars().first()
        if existing is not None:
            return OptOutResponse(ok=True, already_suppressed=True)

        row = HypershopCartRecoverySuppression(
            key_email=body.key_email,
            key_phone=body.key_phone,
            channel=body.channel,
            reason="opted_out",
            expires_at=None,
        )
        session.add(row)
        await session.flush()

    return OptOutResponse(ok=True, already_suppressed=False)
