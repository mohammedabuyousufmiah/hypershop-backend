"""Marketing automation HTTP API — Module 48.

Mounted at /api/v1/admin/marketing/*. RBAC: requires the existing
funnel.* perms (operators already have these for the funnel module).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import Field
from sqlalchemy import text as _t

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import BusinessRuleError, NotFoundError
from app.core.logging import get_logger
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.validation import StrictModel
from app.modules.marketing import service as mk

_log = get_logger("hypershop.marketing.api")
_ADMIN = "funnel.view"  # reuse marketing-class perm; admins have wildcard

router = APIRouter(prefix="/admin/marketing", tags=["marketing-automation"])


# ============================================================== Schemas
class AudienceCreate(StrictModel):
    name: str = Field(..., min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    rules: dict[str, Any] = Field(default_factory=dict)


class AudienceUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    rules: dict[str, Any] | None = None
    is_active: bool | None = None


class CampaignCreate(StrictModel):
    name: str = Field(..., min_length=2, max_length=160)
    audience_id: UUID
    channel: str = Field(..., pattern=r"^(whatsapp|email|sms|in_app)$")
    template_subject: str | None = Field(default=None, max_length=255)
    template_body: str = Field(..., min_length=1, max_length=8000)
    whatsapp_template_name: str | None = Field(default=None, max_length=80)
    schedule_at: datetime | None = None


# ============================================================== Audiences
@router.post(
    "/audiences",
    status_code=201,
    summary="Create an audience (segment) with rule JSON",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def create_audience(
    body: AudienceCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    import json
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                "INSERT INTO marketing_audiences "
                "(id, name, description, rules, created_by) "
                "VALUES (gen_random_uuid(), :n, :d, CAST(:r AS jsonb), :u) "
                "RETURNING id"
            ),
            {
                "n": body.name, "d": body.description,
                "r": json.dumps(body.rules), "u": principal.user_id,
            },
        )
        aid = r.scalar_one()
        await record_audit(
            actor=principal, action="marketing.audience.created",
            resource_type="marketing_audiences", resource_id=aid,
        )
    return {"id": str(aid), "name": body.name, "rules": body.rules}


@router.get(
    "/audiences",
    summary="List audiences",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def list_audiences(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    "SELECT id, name, description, rules, estimated_count, "
                    "counted_at, is_active, created_at "
                    "FROM marketing_audiences "
                    "ORDER BY created_at DESC LIMIT :lim"
                ),
                {"lim": limit},
            )
        ).all()
        return [
            {
                "id": str(r[0]), "name": r[1], "description": r[2],
                "rules": r[3], "estimated_count": r[4],
                "counted_at": r[5], "is_active": r[6], "created_at": r[7],
            }
            for r in rows
        ]


@router.get(
    "/audiences/{aid}/preview",
    summary="Evaluate audience rules — count + 5-customer sample",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def preview_audience(
    aid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                _t("SELECT rules FROM marketing_audiences WHERE id = :a"),
                {"a": aid},
            )
        ).first()
        if r is None:
            raise NotFoundError("Audience not found")
        rules = r[0] or {}
        out = await mk.preview_audience(session, rules)
        # Cache the count
        await session.execute(
            _t(
                "UPDATE marketing_audiences "
                "SET estimated_count = :c, counted_at = now() "
                "WHERE id = :a"
            ),
            {"c": out["count"], "a": aid},
        )
        return {"audience_id": str(aid), **out}


@router.patch(
    "/audiences/{aid}",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def update_audience(
    aid: Annotated[UUID, Path(...)],
    body: AudienceUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    import json
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise BusinessRuleError("nothing to update")
    parts = []
    params: dict[str, Any] = {"a": aid}
    for k, v in fields.items():
        if k == "rules":
            parts.append("rules = CAST(:rules AS jsonb)")
            params["rules"] = json.dumps(v)
        else:
            parts.append(f"{k} = :{k}")
            params[k] = v
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"UPDATE marketing_audiences SET {', '.join(parts)}, updated_at = now() "
                f"WHERE id = :a RETURNING id"
            ),
            params,
        )
        if r.first() is None:
            raise NotFoundError("Audience not found")
        await record_audit(
            actor=principal, action="marketing.audience.updated",
            resource_type="marketing_audiences", resource_id=aid,
            metadata={"fields": list(fields.keys())},
        )
    return {"id": str(aid), "updated": list(fields.keys())}


# ============================================================== Campaigns
@router.post(
    "/campaigns",
    status_code=201,
    summary="Create a campaign (draft or scheduled)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def create_campaign(
    body: CampaignCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    status = "scheduled" if body.schedule_at else "draft"
    async with uow.transactional() as session:
        # Verify audience exists
        a = (
            await session.execute(
                _t("SELECT 1 FROM marketing_audiences WHERE id = :a"),
                {"a": body.audience_id},
            )
        ).first()
        if a is None:
            raise NotFoundError("Audience not found")
        r = await session.execute(
            _t(
                "INSERT INTO marketing_campaigns "
                "(id, name, audience_id, channel, template_subject, "
                " template_body, whatsapp_template_name, status, "
                " schedule_at, created_by) "
                "VALUES (gen_random_uuid(), :n, :a, :ch, :ts, :tb, :tn, :st, :sa, :u) "
                "RETURNING id"
            ),
            {
                "n": body.name, "a": body.audience_id, "ch": body.channel,
                "ts": body.template_subject, "tb": body.template_body,
                "tn": body.whatsapp_template_name,
                "st": status, "sa": body.schedule_at, "u": principal.user_id,
            },
        )
        cid = r.scalar_one()
        await record_audit(
            actor=principal, action="marketing.campaign.created",
            resource_type="marketing_campaigns", resource_id=cid,
            metadata={"channel": body.channel, "status": status},
        )
    return {"id": str(cid), "name": body.name, "status": status}


@router.get(
    "/campaigns",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def list_campaigns(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    sql = (
        "SELECT c.id, c.name, c.channel, c.status, c.schedule_at, c.sent_at, "
        "c.sent_count, c.delivered_count, c.failed_count, a.name AS audience_name "
        "FROM marketing_campaigns c JOIN marketing_audiences a ON a.id = c.audience_id "
    )
    params: dict[str, Any] = {"lim": limit}
    if status_filter:
        sql += "WHERE c.status = :st "
        params["st"] = status_filter
    sql += "ORDER BY c.created_at DESC LIMIT :lim"
    async with uow.transactional() as session:
        rows = (await session.execute(_t(sql), params)).all()
        return [
            {
                "id": str(r[0]), "name": r[1], "channel": r[2], "status": r[3],
                "schedule_at": r[4], "sent_at": r[5],
                "sent_count": int(r[6]), "delivered_count": int(r[7]),
                "failed_count": int(r[8]), "audience_name": r[9],
            }
            for r in rows
        ]


@router.post(
    "/campaigns/{cid}/send-now",
    summary="Dispatch the campaign immediately (synchronous batch)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def send_campaign_now(
    cid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    # Step 1: prepare batch inside a transaction
    async with uow.transactional() as session:
        out = await mk.send_campaign(session, campaign_id=cid)
    enqueued = out.get("_enqueued") or []
    # Step 2: outbound dispatch (network calls outside the txn)
    sent_ok = 0
    failed = 0
    skipped = out.get("skipped", 0)
    if enqueued:
        from app.modules.customer_care import outbound, channels
        for item in enqueued:
            ch = item["channel"]
            ok = False
            pmid: str | None = None
            err: str | None = None
            try:
                if ch == "whatsapp":
                    if item.get("whatsapp_template_name"):
                        result = await outbound.send_whatsapp_template(
                            to_phone=item["phone"],
                            template_name=item["whatsapp_template_name"],
                            body_params=[],
                        )
                    else:
                        result = await outbound.send_whatsapp_text(
                            to_phone=item["phone"], body=item["body"],
                        )
                    ok = result is not None
                    if ok:
                        pmid = ((result.get("messages") or [{}])[0]).get("id")
                elif ch == "sms":
                    result = await channels.send_sms(
                        to_phone=item["phone"], body=item["body"],
                    )
                    ok = result is not None
                elif ch == "email":
                    ok = await channels.send_email(
                        to_address=item["email"],
                        subject=item.get("subject") or "Hypershop",
                        body_text=item["body"],
                    )
                elif ch == "in_app":
                    from app.modules.notifications.service import NotificationService
                    async with uow.transactional() as s2:
                        svc = NotificationService(s2)
                        await svc.create(
                            customer_user_id=item["user_id"],
                            title=item.get("subject") or "Hypershop",
                            body=item["body"], category="marketing",
                        )
                    ok = True
            except Exception as e:  # noqa: BLE001
                err = str(e)[:500]
            # Record result in its own txn so we don't lose state on
            # a poisoned session.
            async with uow.transactional() as s3:
                await mk.mark_send_result(
                    s3, send_id=item["send_id"], ok=ok,
                    provider_message_id=pmid, error=err,
                )
            if ok:
                sent_ok += 1
            else:
                failed += 1
    # Step 3: finalise campaign counters
    async with uow.transactional() as session:
        await mk.finalise_campaign(
            session, campaign_id=cid, sent=sent_ok, failed=failed,
        )
        await record_audit(
            actor=principal, action="marketing.campaign.sent",
            resource_type="marketing_campaigns", resource_id=cid,
            metadata={"sent": sent_ok, "failed": failed, "skipped": skipped},
        )
    _log.info("marketing_campaign_dispatched", campaign_id=str(cid),
              sent=sent_ok, failed=failed, skipped=skipped)
    return {
        "campaign_id": str(cid), "sent": sent_ok,
        "failed": failed, "skipped": skipped,
    }


class CampaignFromFunnelSegment(StrictModel):
    """Request body for ``/campaigns/from-funnel-segment/{seg_id}``.

    Same shape as ``CampaignCreate`` minus ``audience_id`` (audience is
    auto-created from the resolved segment members). ``campaign_name``
    falls back to the segment name when omitted.
    """
    campaign_name: str | None = Field(default=None, min_length=2, max_length=160)
    channel: str = Field(..., pattern=r"^(whatsapp|email|sms|in_app)$")
    template_subject: str | None = Field(default=None, max_length=255)
    template_body: str = Field(..., min_length=1, max_length=8000)
    whatsapp_template_name: str | None = Field(default=None, max_length=80)
    schedule_at: datetime | None = None
    audience_name: str | None = Field(default=None, min_length=2, max_length=120)
    member_limit: int = Field(default=10_000, ge=1, le=50_000)


@router.post(
    "/campaigns/from-funnel-segment/{seg_id}",
    status_code=201,
    summary=(
        "Materialise a behavioural funnel segment into a marketing "
        "audience + campaign in one call (M46 ⇒ M48 bridge)"
    ),
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def campaign_from_funnel_segment(
    seg_id: Annotated[UUID, Path(...)],
    body: CampaignFromFunnelSegment,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    """Bridge endpoint: takes a funnel-segment id, resolves its members,
    materialises them into an explicit-user-id marketing audience, and
    creates a campaign tied to it. Returns audience + campaign ids."""
    import json
    from app.modules.funnel.segmentation import segment_query_sql

    async with uow.transactional() as session:
        # 1. Load the funnel segment
        seg = (
            await session.execute(
                _t(
                    "SELECT id, name, rules, is_active "
                    "FROM funnel_segments WHERE id = :s"
                ),
                {"s": seg_id},
            )
        ).first()
        if seg is None:
            raise NotFoundError("Funnel segment not found")
        if not seg[3]:
            raise BusinessRuleError("Funnel segment is not active")
        seg_name = seg[1]
        seg_rules = seg[2] or {}

        # 2. Resolve segment members → hypershop user ids
        seg_sql, seg_params = segment_query_sql(seg_rules)
        seg_sql_capped = f"{seg_sql} LIMIT :_lim"
        seg_params_capped = {**seg_params, "_lim": body.member_limit}
        rows = (
            await session.execute(_t(seg_sql_capped), seg_params_capped)
        ).all()
        # Column index 1 = hypershop_customer_id (may be NULL for
        # anonymous funnel customers — we drop those).
        user_ids = [r[1] for r in rows if r[1] is not None]
        if not user_ids:
            raise BusinessRuleError(
                "Segment has no resolvable Hypershop users yet "
                "(empty, anonymous-only, or unmapped)"
            )

        # 3. Create the materialised audience
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        aud_name = body.audience_name or f"From funnel: {seg_name} @ {ts}"
        # Truncate to schema max
        aud_name = aud_name[:120]
        aud_rules = {
            "user_id_in": [str(u) for u in user_ids],
            "_source": {
                "kind": "funnel_segment",
                "segment_id": str(seg_id),
                "segment_name": seg_name,
                "materialised_at": ts,
                "member_count": len(user_ids),
            },
        }
        aud_r = await session.execute(
            _t(
                "INSERT INTO marketing_audiences "
                "(id, name, description, rules, estimated_count, "
                " counted_at, created_by) "
                "VALUES (gen_random_uuid(), :n, :d, CAST(:r AS jsonb), "
                "        :ec, now(), :u) "
                "RETURNING id"
            ),
            {
                "n": aud_name,
                "d": f"Auto-materialised from funnel segment {seg_id}",
                "r": json.dumps(aud_rules),
                "ec": len(user_ids),
                "u": principal.user_id,
            },
        )
        aud_id = aud_r.scalar_one()
        await record_audit(
            actor=principal,
            action="marketing.audience.created_from_funnel_segment",
            resource_type="marketing_audiences", resource_id=aud_id,
            metadata={
                "funnel_segment_id": str(seg_id),
                "funnel_segment_name": seg_name,
                "member_count": len(user_ids),
            },
        )

        # 4. Create the campaign tied to the new audience
        status = "scheduled" if body.schedule_at else "draft"
        camp_name = (body.campaign_name or f"Campaign — {seg_name}")[:160]
        camp_r = await session.execute(
            _t(
                "INSERT INTO marketing_campaigns "
                "(id, name, audience_id, channel, template_subject, "
                " template_body, whatsapp_template_name, status, "
                " schedule_at, created_by) "
                "VALUES (gen_random_uuid(), :n, :a, :ch, :ts, :tb, :tn, "
                "        :st, :sa, :u) "
                "RETURNING id"
            ),
            {
                "n": camp_name, "a": aud_id, "ch": body.channel,
                "ts": body.template_subject, "tb": body.template_body,
                "tn": body.whatsapp_template_name,
                "st": status, "sa": body.schedule_at,
                "u": principal.user_id,
            },
        )
        camp_id = camp_r.scalar_one()
        await record_audit(
            actor=principal,
            action="marketing.campaign.created_from_funnel_segment",
            resource_type="marketing_campaigns", resource_id=camp_id,
            metadata={
                "funnel_segment_id": str(seg_id),
                "audience_id": str(aud_id),
                "channel": body.channel,
                "status": status,
                "member_count": len(user_ids),
            },
        )

    _log.info(
        "marketing_campaign_from_funnel_segment",
        funnel_segment_id=str(seg_id),
        audience_id=str(aud_id), campaign_id=str(camp_id),
        member_count=len(user_ids), channel=body.channel,
    )
    return {
        "funnel_segment_id": str(seg_id),
        "funnel_segment_name": seg_name,
        "audience_id": str(aud_id),
        "audience_name": aud_name,
        "campaign_id": str(camp_id),
        "campaign_name": camp_name,
        "channel": body.channel,
        "status": status,
        "member_count": len(user_ids),
    }


@router.get(
    "/campaigns/{cid}/sends",
    summary="Per-send rows for a campaign (status + provider id + errors)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def list_campaign_sends(
    cid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    "SELECT s.id, s.customer_user_id, s.channel, s.status, "
                    "s.sent_at, s.provider_message_id, s.error_message, "
                    "u.email::text, u.phone, u.full_name "
                    "FROM marketing_campaign_sends s JOIN users u ON u.id = s.customer_user_id "
                    "WHERE s.campaign_id = :c ORDER BY s.created_at DESC LIMIT :lim"
                ),
                {"c": cid, "lim": limit},
            )
        ).all()
        return [
            {
                "id": str(r[0]), "customer_user_id": str(r[1]),
                "channel": r[2], "status": r[3], "sent_at": r[4],
                "provider_message_id": r[5], "error_message": r[6],
                "email": r[7], "phone": r[8], "full_name": r[9],
            }
            for r in rows
        ]
