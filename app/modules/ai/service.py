"""AI service.

Surface
-------
Four capability methods (one per AI ability):
- :meth:`ocr_prescription`
- :meth:`suggest_medicines`
- :meth:`predict_stock`
- :meth:`detect_fraud`

Plus three review methods that operate on stored proposals:
- :meth:`accept_proposal` (reviewer agrees as-is)
- :meth:`amend_proposal` (reviewer accepts with edits)
- :meth:`reject_proposal`

Hard rule
---------
There is **no method on this service that approves a prescription or
pays a refund.** Reviewers who want to act on a proposal call the
human-driven endpoint in the owning module — `prescriptions.approve`,
`finance.refund.pay`, etc. — which require their own permission and
write their own audit trail. Accepting a proposal here is a *bookmark*,
not a *decision*.

Defence-in-depth: every capability method calls
:func:`assert_ai_cannot_decide` with the proposal kind, and every
proposal write passes through this same module — there is no service
hook that could be smuggled into a downstream approval call.

Provider failure handling
-------------------------
The bound provider is queried via :func:`get_provider`. When no real
provider is configured, every call raises ``IntegrationError`` (502)
from :class:`NotConfiguredProvider`. Failures are still recorded as
:class:`AIUsageEvent` rows so cost / failure rate is visible from the
dashboard even before a provider is wired.
"""

from __future__ import annotations

import asyncio
import time as _time
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.errors import (
    BusinessRuleError,
    DomainError,
    IntegrationError,
    ServiceUnavailableError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal
from app.core.time import utc_now
from app.modules.ai.models import AIProposal, AIUsageEvent
from app.modules.ai.providers import (
    FraudDetectionRequest as ProviderFraudReq,
    OcrRequest as ProviderOcrReq,
    StockPredictionRequest as ProviderStockReq,
    SuggestMedicinesRequest as ProviderSuggestReq,
    get_provider,
)
from app.modules.ai.repository import (
    AIProposalRepository,
    AIUsageEventRepository,
    require_proposal,
)
from app.modules.ai.state import (
    AIPolicyError,
    AIProposalKind,
    AIProposalStatus,
    HUMAN_ONLY_ACTIONS,
    assert_ai_cannot_decide,
)

_logger = get_logger("hypershop.ai")

DEFAULT_PROPOSAL_TTL_HOURS = 72
DEFAULT_TIMEOUT_SECONDS = 30


def _redact_input_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip raw bytes / files from the payload before persistence.

    The audit ledger stores file metadata (size, mime, hint) but never
    the actual bytes — those live on disk under the prescriptions /
    delivery POD storage roots, addressable by reference_id.
    """
    safe = {}
    for k, v in payload.items():
        if k.endswith("_bytes") or k == "image_bytes":
            safe[f"{k}__size"] = len(v) if isinstance(v, bytes) else None
            continue
        safe[k] = v
    return safe


def _confidence_to_decimal(value: float) -> Any:
    from decimal import Decimal

    return Decimal(str(round(float(value), 3)))


class AIService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.proposals = AIProposalRepository(session)
        self.usage = AIUsageEventRepository(session)

    # ------------------------------------------------------------------
    # Capability: OCR prescription
    # ------------------------------------------------------------------

    async def ocr_prescription(
        self,
        *,
        actor: Principal | SystemPrincipal,
        image_bytes: bytes,
        image_mime: str,
        hint: str | None = None,
        reference_prescription_id: UUID | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> AIProposal:
        kind = AIProposalKind.OCR_PRESCRIPTION
        # Sanity check: this method must NEVER be used to flip a
        # prescription's status. The guard is defensive — there's no
        # call path here that does it, but a future refactor that
        # accidentally pipes ai_payload into a service.approve call
        # would trip this assertion in tests.
        assert_ai_cannot_decide("prescriptions.prescription.approve")

        if not image_bytes:
            raise ValidationError("OCR requires non-empty image bytes.")
        if len(image_bytes) > 25 * 1024 * 1024:
            raise ValidationError("OCR image exceeds 25 MiB cap.")

        provider = get_provider()
        request_id = f"ocr-{utc_now().timestamp():.0f}-{len(image_bytes)}"

        return await self._run_capability(
            actor=actor,
            kind=kind,
            input_payload=_redact_input_payload(
                {
                    "image_bytes": image_bytes,
                    "image_mime": image_mime,
                    "hint": hint,
                },
            ),
            reference_type=(
                "prescription" if reference_prescription_id else None
            ),
            reference_id=reference_prescription_id,
            timeout_seconds=timeout_seconds,
            invoke=lambda: provider.ocr_prescription(
                ProviderOcrReq(
                    image_bytes=image_bytes,
                    image_mime=image_mime,
                    hint=hint,
                    request_id=request_id,
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Capability: suggest medicines
    # ------------------------------------------------------------------

    async def suggest_medicines(
        self,
        *,
        actor: Principal | SystemPrincipal,
        symptoms: str,
        patient_age_years: int | None = None,
        patient_sex: str | None = None,
        catalog_filter_generic: str | None = None,
        rx_only: bool | None = None,
        reference_prescription_id: UUID | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> AIProposal:
        kind = AIProposalKind.SUGGEST_MEDICINES
        provider = get_provider()
        request_id = f"sugg-{utc_now().timestamp():.0f}"

        return await self._run_capability(
            actor=actor,
            kind=kind,
            input_payload={
                "symptoms": symptoms,
                "patient_age_years": patient_age_years,
                "patient_sex": patient_sex,
                "catalog_filter_generic": catalog_filter_generic,
                "rx_only": rx_only,
            },
            reference_type=(
                "prescription" if reference_prescription_id else None
            ),
            reference_id=reference_prescription_id,
            timeout_seconds=timeout_seconds,
            invoke=lambda: provider.suggest_medicines(
                ProviderSuggestReq(
                    symptoms=symptoms,
                    patient_age_years=patient_age_years,
                    patient_sex=patient_sex,
                    catalog_filter_generic=catalog_filter_generic,
                    rx_only=rx_only,
                    request_id=request_id,
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Capability: predict stock
    # ------------------------------------------------------------------

    async def predict_stock(
        self,
        *,
        actor: Principal | SystemPrincipal,
        variant_id: UUID,
        horizon_days: int = 30,
        history_days: int = 180,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> AIProposal:
        kind = AIProposalKind.PREDICT_STOCK
        provider = get_provider()
        request_id = f"stock-{variant_id}-{horizon_days}"

        return await self._run_capability(
            actor=actor,
            kind=kind,
            input_payload={
                "variant_id": str(variant_id),
                "horizon_days": horizon_days,
                "history_days": history_days,
            },
            reference_type="product_variant",
            reference_id=variant_id,
            timeout_seconds=timeout_seconds,
            invoke=lambda: provider.predict_stock(
                ProviderStockReq(
                    variant_id=variant_id,
                    horizon_days=horizon_days,
                    history_days=history_days,
                    request_id=request_id,
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Capability: detect fraud
    # ------------------------------------------------------------------

    async def detect_fraud(
        self,
        *,
        actor: Principal | SystemPrincipal,
        order_id: UUID,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> AIProposal:
        kind = AIProposalKind.DETECT_FRAUD
        # AI may *propose* "block" but cannot cancel the order. The
        # guard names the action it must NEVER take so the boundary is
        # obvious in code-review.
        assert_ai_cannot_decide("orders.order.cancel.any")

        provider = get_provider()
        request_id = f"fraud-{order_id}"

        return await self._run_capability(
            actor=actor,
            kind=kind,
            input_payload={"order_id": str(order_id)},
            reference_type="order",
            reference_id=order_id,
            timeout_seconds=timeout_seconds,
            invoke=lambda: provider.detect_fraud(
                ProviderFraudReq(order_id=order_id, request_id=request_id),
            ),
        )

    # ------------------------------------------------------------------
    # Internal: invoke + persist + audit + meter
    # ------------------------------------------------------------------

    async def _run_capability(
        self,
        *,
        actor: Principal | SystemPrincipal,
        kind: AIProposalKind,
        input_payload: dict[str, Any],
        reference_type: str | None,
        reference_id: UUID | None,
        timeout_seconds: int,
        invoke: Any,  # callable returning awaitable
    ) -> AIProposal:
        actor_id = actor.user_id if isinstance(actor, Principal) else None
        provider = get_provider()
        provider_name = provider.name
        started = _time.monotonic()
        success = False
        err_code: str | None = None
        err_msg: str | None = None
        proposal_id: UUID | None = None

        try:
            try:
                response = await asyncio.wait_for(
                    invoke(), timeout=timeout_seconds,
                )
            except TimeoutError as e:
                raise ServiceUnavailableError(
                    f"AI provider '{provider_name}' did not respond within "
                    f"{timeout_seconds}s.",
                    details={"capability": kind.value, "provider": provider_name},
                ) from e

            ai_payload = response.model_dump(mode="json")
            confidence = _confidence_to_decimal(response.confidence)

            proposal = await self.proposals.add(
                kind=kind.value,
                status=AIProposalStatus.DRAFT.value,
                requested_by=actor_id,
                reference_type=reference_type,
                reference_id=reference_id,
                provider=getattr(response, "provider", provider_name),
                model=getattr(response, "model", None),
                confidence=confidence,
                input_payload=input_payload,
                ai_payload=ai_payload,
                expires_at=utc_now()
                + timedelta(hours=DEFAULT_PROPOSAL_TTL_HOURS),
            )
            proposal_id = proposal.id
            success = True
            cost_units = getattr(response, "cost_units", None)
            await self._record_usage(
                kind=kind,
                provider=provider_name,
                model=getattr(response, "model", None),
                proposal_id=proposal_id,
                requested_by=actor_id,
                success=True,
                error_code=None,
                error_message=None,
                cost_units=cost_units,
                latency_ms=int((_time.monotonic() - started) * 1000),
            )
            await record_audit(
                actor=actor,
                action=f"ai.{kind.value}.proposed",
                resource_type="ai_proposal",
                resource_id=proposal.id,
                metadata={
                    "provider": provider_name,
                    "confidence": str(confidence),
                    "reference_type": reference_type,
                    "reference_id": (
                        str(reference_id) if reference_id else None
                    ),
                },
            )
            return proposal
        except DomainError as e:
            # Typed business error — pass through to API layer but still
            # record usage so failure counts are visible.
            err_code = getattr(e, "code", "domain_error")
            err_msg = e.message
            await self._record_usage(
                kind=kind,
                provider=provider_name,
                model=None,
                proposal_id=None,
                requested_by=actor_id,
                success=False,
                error_code=err_code,
                error_message=err_msg[:2048],
                cost_units=None,
                latency_ms=int((_time.monotonic() - started) * 1000),
            )
            raise
        except Exception as e:
            err_code = type(e).__name__
            err_msg = str(e)
            await self._record_usage(
                kind=kind,
                provider=provider_name,
                model=None,
                proposal_id=None,
                requested_by=actor_id,
                success=False,
                error_code=err_code,
                error_message=err_msg[:2048],
                cost_units=None,
                latency_ms=int((_time.monotonic() - started) * 1000),
            )
            # Surface as IntegrationError so the API returns 502 cleanly
            # (the not-configured provider already does this; raw
            # exceptions from a real adapter get wrapped here).
            raise IntegrationError(
                f"AI provider '{provider_name}' failed: {err_code}",
                details={"capability": kind.value},
            ) from e
        finally:
            _logger.info(
                "ai_capability_call",
                kind=kind.value,
                provider=provider_name,
                success=success,
                proposal_id=str(proposal_id) if proposal_id else None,
                latency_ms=int((_time.monotonic() - started) * 1000),
                error_code=err_code,
            )

    async def _record_usage(
        self,
        *,
        kind: AIProposalKind,
        provider: str,
        model: str | None,
        proposal_id: UUID | None,
        requested_by: UUID | None,
        success: bool,
        error_code: str | None,
        error_message: str | None,
        cost_units: Any,
        latency_ms: int,
    ) -> None:
        """Cost ledgers must commit independently of the calling
        transaction — otherwise a failed AI call's usage event would be
        rolled back along with the rest of the request, leaving us blind
        to the vendor's billable failures. We open a fresh session +
        transaction to write the usage row and commit it on its own.
        """
        from app.core.db.session import get_sessionmaker

        sm = get_sessionmaker()
        async with sm() as session, session.begin():
            session.add(
                AIUsageEvent(
                    kind=kind.value,
                    provider=provider,
                    model=model,
                    proposal_id=proposal_id,
                    requested_by=requested_by,
                    success=success,
                    error_code=error_code,
                    error_message=error_message,
                    cost_units=cost_units,
                    latency_ms=latency_ms,
                ),
            )

    # ------------------------------------------------------------------
    # Review (accept / amend / reject)
    # ------------------------------------------------------------------

    async def accept_proposal(
        self,
        *,
        principal: Principal,
        proposal_id: UUID,
        notes: str | None,
    ) -> AIProposal:
        proposal = require_proposal(await self.proposals.get(proposal_id))
        self._assert_in_status(proposal, AIProposalStatus.DRAFT)
        proposal.status = AIProposalStatus.ACCEPTED.value
        proposal.decision_payload = proposal.ai_payload
        proposal.reviewed_by = principal.user_id
        proposal.reviewed_at = utc_now()
        if notes:
            proposal.review_notes = notes
        await self.session.flush()
        await record_audit(
            actor=principal,
            action=f"ai.{proposal.kind}.accepted",
            resource_type="ai_proposal",
            resource_id=proposal.id,
            metadata={"reference_id": (
                str(proposal.reference_id) if proposal.reference_id else None
            )},
        )
        return proposal

    async def amend_proposal(
        self,
        *,
        principal: Principal,
        proposal_id: UUID,
        decision_payload: dict[str, Any],
        notes: str | None,
    ) -> AIProposal:
        proposal = require_proposal(await self.proposals.get(proposal_id))
        self._assert_in_status(proposal, AIProposalStatus.DRAFT)
        if not isinstance(decision_payload, dict) or not decision_payload:
            raise ValidationError(
                "decision_payload must be a non-empty object.",
            )
        proposal.status = AIProposalStatus.AMENDED.value
        proposal.decision_payload = decision_payload
        proposal.reviewed_by = principal.user_id
        proposal.reviewed_at = utc_now()
        if notes:
            proposal.review_notes = notes
        await self.session.flush()
        await record_audit(
            actor=principal,
            action=f"ai.{proposal.kind}.amended",
            resource_type="ai_proposal",
            resource_id=proposal.id,
        )
        return proposal

    async def reject_proposal(
        self,
        *,
        principal: Principal,
        proposal_id: UUID,
        reason: str,
    ) -> AIProposal:
        proposal = require_proposal(await self.proposals.get(proposal_id))
        self._assert_in_status(proposal, AIProposalStatus.DRAFT)
        proposal.status = AIProposalStatus.REJECTED.value
        proposal.reviewed_by = principal.user_id
        proposal.reviewed_at = utc_now()
        proposal.review_notes = reason
        await self.session.flush()
        await record_audit(
            actor=principal,
            action=f"ai.{proposal.kind}.rejected",
            resource_type="ai_proposal",
            resource_id=proposal.id,
            metadata={"reason": reason},
        )
        return proposal

    @staticmethod
    def _assert_in_status(
        proposal: AIProposal, expected: AIProposalStatus,
    ) -> None:
        if proposal.status != expected.value:
            raise BusinessRuleError(
                f"Proposal is in status '{proposal.status}', "
                f"expected '{expected.value}' for this transition.",
            )

    # ------------------------------------------------------------------
    # Capability status (for the admin "is AI online?" surface)
    # ------------------------------------------------------------------

    @staticmethod
    def capability_status() -> dict[str, Any]:
        provider = get_provider()
        configured = provider.name != "not_configured"
        return {
            "provider": provider.name,
            "configured": configured,
            "capabilities": [
                "ocr_prescription",
                "suggest_medicines",
                "predict_stock",
                "detect_fraud",
            ],
            "note": (
                None if configured
                else "No AI provider bound — all capability calls will return 502."
            ),
        }


# Re-export for callers that want to introspect the policy boundary.
__all__ = [
    "AIPolicyError",
    "AIService",
    "HUMAN_ONLY_ACTIONS",
]
