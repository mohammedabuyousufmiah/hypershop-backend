"""Sprint 9 routes — closes deferred phase-2+ items across modules.

Lives at module root (not under a single Hypershop module) because it
spans loyalty + sellers + notifications. Each route group is gated by
the right RBAC permission set.

Surfaces added:
- /api/v1/loyalty/tiers                         — public list of tier benefits
- /api/v1/loyalty/me/tier                       — my current tier + perks
- /api/v1/admin/loyalty/tiers/{tier}            — admin tune benefits
- /api/v1/seller/me/dashboard                   — seller's own dashboard stats
- /api/v1/seller/me/products                    — seller's product list
- /api/v1/seller/me/orders                      — seller's order list
- /api/v1/seller/me/payouts                     — seller's payout history
- /api/v1/admin/seller-payouts                  — admin payout queue
- /api/v1/admin/seller-payouts/{id}/mark-paid   — admin marks payout paid
- /api/v1/admin/seller-payouts/from-preview     — admin creates pending payout from preview
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import Field
from sqlalchemy import text as _t

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.validation import StrictModel

_log = get_logger("hypershop.sprint9")

# RBAC permission strings
_CUSTOMER_READ = "iam.user.read.self"
_SELLER_READ = "sellers.read"
_ADMIN_FINANCE = "finance.settle"


# ============================================================== LOYALTY TIERS
loyalty_router = APIRouter(prefix="/loyalty", tags=["loyalty-tiers"])


class LoyaltyTierResponse(StrictModel):
    tier: str
    min_lifetime_points: int
    earn_multiplier: Decimal
    discount_percent: Decimal
    free_shipping_threshold: Decimal | None
    birthday_bonus_points: int
    description: str | None


@loyalty_router.get(
    "/tiers",
    response_model=list[LoyaltyTierResponse],
    summary="Public list of loyalty tiers and their perks",
)
async def loyalty_tier_list(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> list[LoyaltyTierResponse]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    "SELECT tier, min_lifetime_points, earn_multiplier, discount_percent, "
                    "free_shipping_threshold, birthday_bonus_points, description "
                    "FROM loyalty_tier_benefits ORDER BY min_lifetime_points ASC"
                )
            )
        ).all()
        return [
            LoyaltyTierResponse(
                tier=r[0], min_lifetime_points=r[1], earn_multiplier=r[2],
                discount_percent=r[3], free_shipping_threshold=r[4],
                birthday_bonus_points=r[5], description=r[6],
            )
            for r in rows
        ]


class MyTierResponse(StrictModel):
    tier: str
    points_balance: int
    lifetime_earned_points: int
    points_to_next_tier: int | None
    next_tier: str | None
    benefits: LoyaltyTierResponse


@loyalty_router.get(
    "/me/tier",
    response_model=MyTierResponse,
    summary="My current loyalty tier + perks + progress to next tier",
    dependencies=[Depends(requires_permission(_CUSTOMER_READ))],
)
async def my_loyalty_tier(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> MyTierResponse:
    async with uow.transactional() as session:
        # Pull account + benefits in two queries
        acct = (
            await session.execute(
                _t(
                    "SELECT tier, balance_points, lifetime_earned_points "
                    "FROM loyalty_accounts WHERE user_id = :u"
                ),
                {"u": principal.user_id},
            )
        ).first()
        if acct is None:
            # No loyalty account yet — return NONE tier defaults
            tier_row = (
                await session.execute(
                    _t(
                        "SELECT tier, min_lifetime_points, earn_multiplier, discount_percent, "
                        "free_shipping_threshold, birthday_bonus_points, description "
                        "FROM loyalty_tier_benefits WHERE tier = 'NONE'"
                    )
                )
            ).first()
            benefits = LoyaltyTierResponse(
                tier=tier_row[0], min_lifetime_points=tier_row[1],
                earn_multiplier=tier_row[2], discount_percent=tier_row[3],
                free_shipping_threshold=tier_row[4],
                birthday_bonus_points=tier_row[5], description=tier_row[6],
            )
            return MyTierResponse(
                tier="NONE", points_balance=0, lifetime_earned_points=0,
                points_to_next_tier=100, next_tier="BRONZE",
                benefits=benefits,
            )
        tier, bal, lt = acct[0], int(acct[1]), int(acct[2])
        tier_row = (
            await session.execute(
                _t(
                    "SELECT tier, min_lifetime_points, earn_multiplier, discount_percent, "
                    "free_shipping_threshold, birthday_bonus_points, description "
                    "FROM loyalty_tier_benefits WHERE tier = :t"
                ),
                {"t": tier},
            )
        ).first()
        benefits = LoyaltyTierResponse(
            tier=tier_row[0], min_lifetime_points=tier_row[1],
            earn_multiplier=tier_row[2], discount_percent=tier_row[3],
            free_shipping_threshold=tier_row[4],
            birthday_bonus_points=tier_row[5], description=tier_row[6],
        )
        next_row = (
            await session.execute(
                _t(
                    "SELECT tier, min_lifetime_points FROM loyalty_tier_benefits "
                    "WHERE min_lifetime_points > :lt "
                    "ORDER BY min_lifetime_points ASC LIMIT 1"
                ),
                {"lt": lt},
            )
        ).first()
        return MyTierResponse(
            tier=tier, points_balance=bal, lifetime_earned_points=lt,
            points_to_next_tier=(next_row[1] - lt) if next_row else None,
            next_tier=next_row[0] if next_row else None,
            benefits=benefits,
        )


class TierBenefitUpdate(StrictModel):
    min_lifetime_points: int | None = Field(default=None, ge=0)
    earn_multiplier: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("10"))
    discount_percent: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    free_shipping_threshold: Decimal | None = None
    birthday_bonus_points: int | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=2000)


admin_loyalty_router = APIRouter(
    prefix="/admin/loyalty", tags=["admin-loyalty"],
)


@admin_loyalty_router.patch(
    "/tiers/{tier}",
    response_model=LoyaltyTierResponse,
    summary="Admin updates a tier's benefits (admin-only)",
    dependencies=[Depends(requires_permission("*"))],
)
async def update_tier(
    tier: Annotated[str, Path(..., pattern=r"^(NONE|BRONZE|SILVER|GOLD|PLATINUM)$")],
    body: TierBenefitUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> LoyaltyTierResponse:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise ValidationError("nothing to update")
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    params = {**fields, "t": tier}
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"UPDATE loyalty_tier_benefits SET {sets}, updated_at = now() "
                f"WHERE tier = :t RETURNING "
                f"tier, min_lifetime_points, earn_multiplier, discount_percent, "
                f"free_shipping_threshold, birthday_bonus_points, description"
            ),
            params,
        )
        row = r.first()
        if row is None:
            raise NotFoundError(f"Tier {tier} not found")
        await record_audit(
            actor=principal,
            action="loyalty.tier.updated",
            resource_type="loyalty_tier_benefits",
            metadata={"tier": tier, "fields": list(fields.keys())},
        )
        return LoyaltyTierResponse(
            tier=row[0], min_lifetime_points=row[1], earn_multiplier=row[2],
            discount_percent=row[3], free_shipping_threshold=row[4],
            birthday_bonus_points=row[5], description=row[6],
        )


# ============================================================== SELLER SELF-SERVE
seller_self_router = APIRouter(prefix="/seller/me", tags=["seller-self-serve"])


@seller_self_router.get(
    "/dashboard",
    summary="Seller's own dashboard — sales/orders/payouts at a glance",
    dependencies=[Depends(requires_permission(_SELLER_READ))],
)
async def seller_dashboard(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    async with uow.transactional() as session:
        sid_row = (
            await session.execute(
                _t("SELECT seller_id FROM seller_users WHERE user_id = :u LIMIT 1"),
                {"u": principal.user_id},
            )
        ).first()
        if sid_row is None:
            raise HTTPException(403, "user is not linked to any seller")
        seller_id = sid_row[0]
        # Period stats
        stats = (
            await session.execute(
                _t(
                    f"""
                    SELECT
                        (SELECT COUNT(*) FROM products WHERE seller_id = :sid) AS products,
                        (SELECT COUNT(*) FROM products WHERE seller_id = :sid AND status = 'active') AS active_products,
                        (SELECT COUNT(DISTINCT o.id)
                            FROM orders o
                            JOIN order_lines ol ON ol.order_id = o.id
                            JOIN product_variants pv ON pv.id = ol.variant_id
                            JOIN products p ON p.id = pv.product_id
                            WHERE p.seller_id = :sid
                              AND o.placed_at >= now() - INTERVAL '{int(days)} days'
                        ) AS recent_orders,
                        (SELECT COALESCE(SUM(ol.line_total), 0)
                            FROM order_lines ol
                            JOIN orders o ON o.id = ol.order_id
                            JOIN product_variants pv ON pv.id = ol.variant_id
                            JOIN products p ON p.id = pv.product_id
                            WHERE p.seller_id = :sid
                              AND o.placed_at >= now() - INTERVAL '{int(days)} days'
                              AND o.status = 'completed'
                        ) AS recent_revenue
                    """,
                ),
                {"sid": seller_id},
            )
        ).first()
        # Pending payout total
        pending = (
            await session.execute(
                _t(
                    "SELECT COALESCE(SUM(net_amount), 0) "
                    "FROM seller_payouts WHERE seller_id = :sid AND status = 'pending'"
                ),
                {"sid": seller_id},
            )
        ).scalar_one()
    return {
        "seller_id": str(seller_id),
        "window_days": days,
        "total_products": int(stats[0] or 0),
        "active_products": int(stats[1] or 0),
        "recent_orders": int(stats[2] or 0),
        "recent_revenue": str(stats[3] or 0),
        "pending_payouts_total": str(pending or 0),
    }


@seller_self_router.get(
    "/products",
    summary="Seller's own product list",
    dependencies=[Depends(requires_permission(_SELLER_READ))],
)
async def seller_my_products(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        sid_row = (
            await session.execute(
                _t("SELECT seller_id FROM seller_users WHERE user_id = :u LIMIT 1"),
                {"u": principal.user_id},
            )
        ).first()
        if sid_row is None:
            raise HTTPException(403, "not a seller")
        rows = (
            await session.execute(
                _t(
                    "SELECT id, name, slug, status, created_at "
                    "FROM products WHERE seller_id = :sid "
                    "ORDER BY created_at DESC LIMIT :lim"
                ),
                {"sid": sid_row[0], "lim": limit},
            )
        ).all()
        return [
            {
                "id": str(r[0]), "name": r[1], "slug": r[2],
                "status": r[3], "created_at": r[4],
            }
            for r in rows
        ]


@seller_self_router.get(
    "/orders",
    summary="Orders that include this seller's products",
    dependencies=[Depends(requires_permission(_SELLER_READ))],
)
async def seller_my_orders(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        sid_row = (
            await session.execute(
                _t("SELECT seller_id FROM seller_users WHERE user_id = :u LIMIT 1"),
                {"u": principal.user_id},
            )
        ).first()
        if sid_row is None:
            raise HTTPException(403, "not a seller")
        rows = (
            await session.execute(
                _t(
                    """
                    SELECT DISTINCT o.id, o.code, o.status, o.grand_total, o.placed_at
                    FROM orders o
                    JOIN order_lines ol ON ol.order_id = o.id
                    JOIN product_variants pv ON pv.id = ol.variant_id
                    JOIN products p ON p.id = pv.product_id
                    WHERE p.seller_id = :sid
                    ORDER BY o.placed_at DESC LIMIT :lim
                    """,
                ),
                {"sid": sid_row[0], "lim": limit},
            )
        ).all()
        return [
            {
                "id": str(r[0]), "code": r[1], "status": r[2],
                "grand_total": str(r[3]), "placed_at": r[4],
            }
            for r in rows
        ]


class SellerProductPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    status: str | None = Field(default=None, pattern=r"^(active|inactive|draft)$")


@seller_self_router.get(
    "/products/{product_id}",
    summary="Single product detail (must be owned by this seller)",
    dependencies=[Depends(requires_permission(_SELLER_READ))],
)
async def seller_get_product(
    product_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        sid_row = (
            await session.execute(
                _t("SELECT seller_id FROM seller_users WHERE user_id = :u LIMIT 1"),
                {"u": principal.user_id},
            )
        ).first()
        if sid_row is None:
            raise HTTPException(403, "not a seller")
        r = (
            await session.execute(
                _t(
                    "SELECT id, name, slug, description, status, "
                    "created_at, updated_at "
                    "FROM products WHERE id = :p AND seller_id = :sid"
                ),
                {"p": product_id, "sid": sid_row[0]},
            )
        ).first()
        if r is None:
            raise NotFoundError("Product not found or not yours")
        # Variants
        variants = (
            await session.execute(
                _t(
                    "SELECT id, sku, price FROM product_variants "
                    "WHERE product_id = :p ORDER BY created_at ASC"
                ),
                {"p": product_id},
            )
        ).all()
        return {
            "id": str(r[0]), "name": r[1], "slug": r[2],
            "description": r[3], "status": r[4],
            "created_at": r[5], "updated_at": r[6],
            "variants": [
                {"id": str(v[0]), "sku": v[1], "price": str(v[2])}
                for v in variants
            ],
        }


@seller_self_router.patch(
    "/products/{product_id}",
    summary="Update seller's own product (name/description/status)",
    dependencies=[Depends(requires_permission(_SELLER_READ))],
)
async def seller_patch_product(
    product_id: Annotated[UUID, Path(...)],
    body: SellerProductPatch,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise ValidationError("nothing to update")
    async with uow.transactional() as session:
        sid_row = (
            await session.execute(
                _t("SELECT seller_id FROM seller_users WHERE user_id = :u LIMIT 1"),
                {"u": principal.user_id},
            )
        ).first()
        if sid_row is None:
            raise HTTPException(403, "not a seller")
        # Ownership-checked update
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        params = {**fields, "p": product_id, "sid": sid_row[0]}
        r = await session.execute(
            _t(
                f"UPDATE products SET {sets}, updated_at = now() "
                f"WHERE id = :p AND seller_id = :sid "
                f"RETURNING id, name, status"
            ),
            params,
        )
        row = r.first()
        if row is None:
            raise NotFoundError("Product not found or not yours")
        await record_audit(
            actor=principal,
            action="seller.product.updated",
            resource_type="products",
            resource_id=product_id,
            metadata={"fields": list(fields.keys())},
        )
    return {"id": str(row[0]), "name": row[1], "status": row[2]}


@seller_self_router.get(
    "/orders/timeseries",
    summary="Daily order count + revenue (for dashboard charts)",
    dependencies=[Depends(requires_permission(_SELLER_READ))],
)
async def seller_orders_timeseries(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    days: int = Query(default=30, ge=7, le=365),
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        sid_row = (
            await session.execute(
                _t("SELECT seller_id FROM seller_users WHERE user_id = :u LIMIT 1"),
                {"u": principal.user_id},
            )
        ).first()
        if sid_row is None:
            raise HTTPException(403, "not a seller")
        rows = (
            await session.execute(
                _t(
                    f"""
                    SELECT DATE_TRUNC('day', o.placed_at)::date AS day,
                           COUNT(DISTINCT o.id) AS orders,
                           COALESCE(SUM(ol.line_total) FILTER (WHERE o.status='completed'), 0) AS revenue
                    FROM order_lines ol
                    JOIN orders o ON o.id = ol.order_id
                    JOIN product_variants pv ON pv.id = ol.variant_id
                    JOIN products p ON p.id = pv.product_id
                    WHERE p.seller_id = :sid
                      AND o.placed_at >= now() - INTERVAL '{int(days)} days'
                    GROUP BY 1 ORDER BY 1
                    """,
                ),
                {"sid": sid_row[0]},
            )
        ).all()
        return [
            {
                "day": str(r[0]),
                "orders": int(r[1]),
                "revenue": str(r[2]),
            }
            for r in rows
        ]


@seller_self_router.get(
    "/top-products",
    summary="Seller's top products by revenue (for dashboard bar chart)",
    dependencies=[Depends(requires_permission(_SELLER_READ))],
)
async def seller_top_products(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    days: int = Query(default=30, ge=7, le=365),
    limit: int = Query(default=5, ge=1, le=20),
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        sid_row = (
            await session.execute(
                _t("SELECT seller_id FROM seller_users WHERE user_id = :u LIMIT 1"),
                {"u": principal.user_id},
            )
        ).first()
        if sid_row is None:
            raise HTTPException(403, "not a seller")
        rows = (
            await session.execute(
                _t(
                    f"""
                    SELECT p.id, p.name,
                           COUNT(DISTINCT ol.order_id) AS orders,
                           SUM(ol.quantity) AS units,
                           SUM(ol.line_total) AS revenue
                    FROM order_lines ol
                    JOIN orders o ON o.id = ol.order_id AND o.status = 'completed'
                    JOIN product_variants pv ON pv.id = ol.variant_id
                    JOIN products p ON p.id = pv.product_id
                    WHERE p.seller_id = :sid
                      AND o.placed_at >= now() - INTERVAL '{int(days)} days'
                    GROUP BY p.id, p.name
                    ORDER BY revenue DESC LIMIT :lim
                    """,
                ),
                {"sid": sid_row[0], "lim": limit},
            )
        ).all()
        return [
            {
                "product_id": str(r[0]), "name": r[1],
                "orders": int(r[2]), "units": int(r[3]),
                "revenue": str(r[4]),
            }
            for r in rows
        ]


@seller_self_router.get(
    "/payouts",
    summary="Seller's own payout history",
    dependencies=[Depends(requires_permission(_SELLER_READ))],
)
async def seller_my_payouts(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        sid_row = (
            await session.execute(
                _t("SELECT seller_id FROM seller_users WHERE user_id = :u LIMIT 1"),
                {"u": principal.user_id},
            )
        ).first()
        if sid_row is None:
            raise HTTPException(403, "not a seller")
        rows = (
            await session.execute(
                _t(
                    "SELECT id, period_start, period_end, gross_amount, "
                    "commission_deducted, return_debit, net_amount, "
                    "currency, status, payment_method, payment_reference, "
                    "paid_at, created_at "
                    "FROM seller_payouts WHERE seller_id = :sid "
                    "ORDER BY period_end DESC LIMIT :lim"
                ),
                {"sid": sid_row[0], "lim": limit},
            )
        ).all()
        return [
            {
                "id": str(r[0]), "period_start": r[1], "period_end": r[2],
                "gross_amount": str(r[3]), "commission_deducted": str(r[4]),
                "return_debit": str(r[5]), "net_amount": str(r[6]),
                "currency": r[7], "status": r[8],
                "payment_method": r[9], "payment_reference": r[10],
                "paid_at": r[11], "created_at": r[12],
            }
            for r in rows
        ]


# ============================================================== ADMIN PAYOUTS
admin_payout_router = APIRouter(
    prefix="/admin/seller-payouts", tags=["admin-seller-payouts"],
)


class PayoutFromPreviewRequest(StrictModel):
    seller_id: UUID
    period_start: datetime
    period_end: datetime
    gross_amount: Decimal
    commission_deducted: Decimal
    return_debit: Decimal = Decimal("0")
    net_amount: Decimal
    currency: str = "BDT"
    notes: str | None = None


@admin_payout_router.post(
    "/from-preview",
    status_code=201,
    summary="Create a pending payout row from a preview computation",
    dependencies=[Depends(requires_permission(_ADMIN_FINANCE))],
)
async def payout_from_preview(
    body: PayoutFromPreviewRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                """
                INSERT INTO seller_payouts
                  (id, seller_id, period_start, period_end, gross_amount,
                   commission_deducted, return_debit, net_amount, currency,
                   status, requested_by, notes)
                VALUES
                  (gen_random_uuid(), :sid, :ps, :pe, :gross, :comm,
                   :rd, :net, :cur, 'pending', :req, :n)
                RETURNING id
                """,
            ),
            {
                "sid": body.seller_id, "ps": body.period_start,
                "pe": body.period_end, "gross": body.gross_amount,
                "comm": body.commission_deducted, "rd": body.return_debit,
                "net": body.net_amount, "cur": body.currency,
                "req": principal.user_id, "n": body.notes,
            },
        )
        new_id = r.scalar_one()
        await record_audit(
            actor=principal,
            action="seller.payout.created",
            resource_type="seller_payouts",
            resource_id=new_id,
            metadata={"seller_id": str(body.seller_id), "net": str(body.net_amount)},
        )
    return {"id": str(new_id), "status": "pending"}


class MarkPaidRequest(StrictModel):
    payment_method: str = Field(..., pattern=r"^(bank_transfer|bkash|nagad|cheque|cash)$")
    payment_reference: str = Field(..., min_length=1, max_length=80)
    notes: str | None = Field(default=None, max_length=2000)


@admin_payout_router.post(
    "/{payout_id}/mark-paid",
    summary="Admin marks a payout as paid (records the bank/MFS reference)",
    dependencies=[Depends(requires_permission(_ADMIN_FINANCE))],
)
async def mark_payout_paid(
    payout_id: Annotated[UUID, Path(...)],
    body: MarkPaidRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                """
                UPDATE seller_payouts
                SET status = 'paid',
                    payment_method = :pm,
                    payment_reference = :pr,
                    paid_at = now(),
                    notes = COALESCE(:n, notes)
                WHERE id = :pid AND status IN ('pending', 'approved')
                RETURNING id, net_amount, currency, seller_id
                """,
            ),
            {"pm": body.payment_method, "pr": body.payment_reference,
             "n": body.notes, "pid": payout_id},
        )
        row = r.first()
        if row is None:
            raise NotFoundError("Payout not found or not in payable state")
        await record_audit(
            actor=principal,
            action="seller.payout.paid",
            resource_type="seller_payouts",
            resource_id=payout_id,
            metadata={
                "payment_method": body.payment_method,
                "payment_reference": body.payment_reference,
                "net": str(row[1]),
                "seller_id": str(row[3]),
            },
        )
    return {"id": str(payout_id), "status": "paid", "net_amount": str(row[1])}


@admin_payout_router.get(
    "",
    summary="Admin queue: list payouts (pending first)",
    dependencies=[Depends(requires_permission(_ADMIN_FINANCE))],
)
async def list_admin_payouts(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    sql = (
        "SELECT p.id, p.seller_id, s.business_name, p.period_start, p.period_end, "
        "p.net_amount, p.currency, p.status, p.payment_method, p.payment_reference, "
        "p.paid_at, p.created_at "
        "FROM seller_payouts p JOIN sellers s ON s.id = p.seller_id "
    )
    params: dict[str, Any] = {"lim": limit}
    if status_filter:
        sql += "WHERE p.status = :st "
        params["st"] = status_filter
    sql += (
        "ORDER BY CASE WHEN p.status='pending' THEN 0 ELSE 1 END, "
        "p.period_end DESC LIMIT :lim"
    )
    async with uow.transactional() as session:
        rows = (await session.execute(_t(sql), params)).all()
        return [
            {
                "id": str(r[0]), "seller_id": str(r[1]),
                "business_name": r[2],
                "period_start": r[3], "period_end": r[4],
                "net_amount": str(r[5]), "currency": r[6],
                "status": r[7], "payment_method": r[8],
                "payment_reference": r[9], "paid_at": r[10],
                "created_at": r[11],
            }
            for r in rows
        ]
