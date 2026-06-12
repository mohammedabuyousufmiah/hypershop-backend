"""ARQ cron jobs for the Sponsored Products module.

  - ``reset_daily_budgets_job`` (daily at 18:00 UTC = 00:00 BDT) —
    zero ``today_spent_minor`` on every campaign and resume any
    campaign that was auto-paused with status='budget_exhausted'
    (provided its wallet still has at least MIN_BID_MINOR).

  - ``recompute_quality_scores_job`` (Mondays at 03:00 UTC) — for each
    active ad_group, compute 7-day CTR and derive a new quality_score
    in [0.5, 1.5]. Snapshot the computation into
    ``hypershop_ad_quality_snapshots`` for audit + chart history.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.core.time import utc_now
from app.modules.ads.codes import MIN_BID_MINOR
from app.modules.ads.models import (
    HypershopAdCampaign,
    HypershopAdClick,
    HypershopAdGroup,
    HypershopAdImpression,
    HypershopAdQualitySnapshot,
    HypershopAdWallet,
)

_log = get_logger("hypershop.ads.jobs")


async def reset_daily_budgets_job(_ctx: dict[str, Any]) -> dict[str, int]:
    """Roll every campaign's ``today_spent_minor`` to 0 and re-activate
    campaigns that were auto-paused at budget exhaustion (when their
    wallet still has spend headroom).

    Returns ``{"reset": N, "resumed": M}`` for observability.
    """
    async with UnitOfWork().transactional() as session:
        reset_result = await session.execute(
            update(HypershopAdCampaign)
            .where(HypershopAdCampaign.today_spent_minor > 0)
            .values(today_spent_minor=0)
        )

        # Resume budget_exhausted campaigns whose wallet still has min_bid.
        resumable_q = (
            select(HypershopAdCampaign.id)
            .join(
                HypershopAdWallet,
                HypershopAdWallet.seller_id == HypershopAdCampaign.seller_id,
            )
            .where(
                HypershopAdCampaign.status == "budget_exhausted",
                HypershopAdWallet.balance_minor >= MIN_BID_MINOR,
            )
        )
        resumable_ids = [r[0] for r in (await session.execute(resumable_q)).all()]
        resumed = 0
        if resumable_ids:
            res = await session.execute(
                update(HypershopAdCampaign)
                .where(HypershopAdCampaign.id.in_(resumable_ids))
                .values(status="active")
            )
            resumed = int(res.rowcount or 0)

    counts = {
        "reset": int(reset_result.rowcount or 0),
        "resumed": resumed,
    }
    _log.info("ads_daily_budget_reset", **counts)
    return counts


async def recompute_quality_scores_job(_ctx: dict[str, Any]) -> dict[str, int]:
    """Compute 7-day CTR per ad_group; update ``quality_score`` and
    snapshot the computation into hypershop_ad_quality_snapshots.

    Formula (clamped to [0.5, 1.5]):
        score = 1.0 + (ctr - 0.01) * 50
            CTR 0%  → 0.5  (penalty)
            CTR 1%  → 1.0  (baseline)
            CTR 2%  → 1.5  (cap)
    Ad-groups with < 100 impressions in the window keep their current
    score (insufficient signal); we still write a snapshot so dashboards
    show "not enough data".
    """
    since = utc_now() - timedelta(days=7)
    updated = 0
    snapshotted = 0

    async with UnitOfWork().transactional() as session:
        # Pull per-ad-group impression + click counts in one query.
        stmt = (
            select(
                HypershopAdGroup.id,
                func.count(HypershopAdImpression.id).label("imprs"),
                func.coalesce(
                    select(func.count())
                    .select_from(HypershopAdClick)
                    .where(
                        HypershopAdClick.ad_group_id == HypershopAdGroup.id,
                        HypershopAdClick.is_invalid.is_(False),
                        HypershopAdClick.created_at >= since,
                    )
                    .scalar_subquery(),
                    0,
                ).label("clicks"),
            )
            .select_from(HypershopAdGroup)
            .join(
                HypershopAdImpression,
                (HypershopAdImpression.ad_group_id == HypershopAdGroup.id)
                & (HypershopAdImpression.created_at >= since),
                isouter=True,
            )
            .where(HypershopAdGroup.status == "active")
            .group_by(HypershopAdGroup.id)
        )
        rows = (await session.execute(stmt)).all()

        for ag_id, imprs, clicks in rows:
            imprs = int(imprs or 0)
            clicks = int(clicks or 0)
            ctr = (clicks / imprs) if imprs > 0 else 0.0

            # Insufficient signal — keep current score, log snapshot only.
            new_score: float | None = None
            if imprs >= 100:
                raw = 1.0 + (ctr - 0.01) * 50
                new_score = max(0.5, min(1.5, raw))

            snap = HypershopAdQualitySnapshot(
                ad_group_id=ag_id,
                ctr=round(ctr, 4),
                rating_avg=None,
                in_stock_rate=None,
                computed_score=(
                    round(new_score, 2)
                    if new_score is not None else None
                ),
            )
            session.add(snap)
            snapshotted += 1

            if new_score is not None:
                await session.execute(
                    update(HypershopAdGroup)
                    .where(HypershopAdGroup.id == ag_id)
                    .values(quality_score=round(new_score, 2))
                )
                updated += 1

    counts = {"updated": updated, "snapshotted": snapshotted}
    _log.info("ads_quality_score_recompute", **counts)
    return counts
