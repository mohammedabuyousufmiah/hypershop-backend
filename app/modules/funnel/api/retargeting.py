"""GET /funnel/retargeting/export — produce a consent-filtered audience
list for Meta / Google / TikTok / WhatsApp campaigns. Every export is
logged to ``funnel_retargeting_export_logs`` so the dashboard can show
how much of the raw segment was filtered out by missing consent.

Gated by ``funnel.export`` permission — this is the most sensitive
funnel call (it ships customer PII out to marketing platforms), so we
require an explicit ``funnel.export`` grant rather than letting any
``funnel.view`` holder run it.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db.session import get_session
from app.core.security.principal import Principal
from app.modules.funnel.integrations.capi import push_audience
from app.modules.funnel.models import FunnelCustomer, FunnelRetargetingExportLog
from app.modules.funnel.security import require_export
from app.modules.funnel.services.privacy import can_contact

router = APIRouter()


async def _build_audience(
    db: AsyncSession, *, platform: str, segment: str,
) -> tuple[list[dict], int]:
    """Consent-filtered audience for a segment (shared by export + push)."""
    settings = get_settings()
    allow_no_consent = getattr(
        settings, "funnel_allow_export_without_marketing_consent", False,
    )
    rows = (
        await db.execute(
            select(FunnelCustomer)
            .where(
                FunnelCustomer.deleted_at.is_(None),
                FunnelCustomer.segment == segment,
            )
            .limit(10000),
        )
    ).scalars().all()
    channel = "ad" if platform in {"meta", "google", "tiktok"} else "whatsapp"
    audience: list[dict] = []
    filtered = 0
    for c in rows:
        allowed, _reason = can_contact(c, channel)
        if not allowed and not allow_no_consent:
            filtered += 1
            continue
        audience.append({
            "external_customer_id": c.external_customer_id,
            "phone": c.phone,
            "email": c.email,
            "hypershop_customer_id": c.hypershop_customer_id,
            "score": c.current_score,
            "segment": c.segment,
        })
    return audience, filtered


@router.post("/push")
async def push_retargeting_audience(
    principal: Annotated[Principal, Depends(require_export)],
    db: Annotated[AsyncSession, Depends(get_session)],
    platform: str,
    segment: str,
) -> dict:
    """Server-side push of the consent-filtered audience to a platform's
    Conversions API (Meta / TikTok / Google). Env-gated per platform —
    returns status=not_configured when that platform's creds are absent."""
    if platform not in {"meta", "google", "tiktok"}:
        raise HTTPException(
            status_code=400,
            detail="push supports meta / google / tiktok (whatsapp is export-only).",
        )
    audience, filtered = await _build_audience(db, platform=platform, segment=segment)
    result = await push_audience(platform=platform, segment=segment, audience=audience)
    db.add(
        FunnelRetargetingExportLog(
            platform=platform,
            segment=segment,
            exported_count=int(result.get("sent", 0)),
            consent_filtered_count=filtered,
        ),
    )
    await db.commit()
    return {
        "platform": platform,
        "segment": segment,
        "audience_size": len(audience),
        "consent_filtered_count": filtered,
        "push": result,
    }


@router.get("/export")
async def export_retargeting_audience(
    principal: Annotated[Principal, Depends(require_export)],
    db: Annotated[AsyncSession, Depends(get_session)],
    platform: str,
    segment: str,
) -> dict:
    if platform not in {"meta", "google", "tiktok", "whatsapp"}:
        raise HTTPException(status_code=400, detail="Unsupported platform.")

    settings = get_settings()
    allow_no_consent = getattr(
        settings, "funnel_allow_export_without_marketing_consent", False,
    )

    rows = (
        await db.execute(
            select(FunnelCustomer)
            .where(
                FunnelCustomer.deleted_at.is_(None),
                FunnelCustomer.segment == segment,
            )
            .limit(10000),
        )
    ).scalars().all()

    channel = "ad" if platform in {"meta", "google", "tiktok"} else "whatsapp"
    audience: list[dict] = []
    filtered = 0
    for c in rows:
        allowed, _reason = can_contact(c, channel)
        if not allowed and not allow_no_consent:
            filtered += 1
            continue
        audience.append({
            "external_customer_id": c.external_customer_id,
            "phone": c.phone,
            "email": c.email,
            "hypershop_customer_id": c.hypershop_customer_id,
            "score": c.current_score,
            "segment": c.segment,
        })

    db.add(
        FunnelRetargetingExportLog(
            platform=platform,
            segment=segment,
            exported_count=len(audience),
            consent_filtered_count=filtered,
        ),
    )
    await db.commit()

    return {
        "platform": platform,
        "segment": segment,
        "count": len(audience),
        "consent_filtered_count": filtered,
        "audience": audience,
    }
