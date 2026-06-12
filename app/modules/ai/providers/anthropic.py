"""Anthropic Claude adapter (skeleton).

Real HTTP shape against ``POST /v1/messages`` (Claude API). Refuses to
build without ``ANTHROPIC_API_KEY``. To activate:

  export AI_PROVIDER=anthropic
  export ANTHROPIC_API_KEY=sk-ant-...
  # optional:
  export ANTHROPIC_BASE_URL=https://api.anthropic.com/v1
  export ANTHROPIC_MODEL_DEFAULT=claude-sonnet-4-5
"""

from __future__ import annotations

from datetime import date as _date, timedelta
from decimal import Decimal
from typing import Any

from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.core.time import utc_now
from app.modules.ai.providers._http import extract_json_block, post_json
from app.modules.ai.providers.base import (
    AIProvider,
    FraudDetectionRequest,
    FraudDetectionResponse,
    FraudSignal,
    OcrLineItem,
    OcrRequest,
    OcrResponse,
    StockPredictionRequest,
    StockPredictionResponse,
    SuggestedMedicine,
    SuggestMedicinesRequest,
    SuggestMedicinesResponse,
)

_logger = get_logger("hypershop.ai.anthropic")
_DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
_DEFAULT_MODEL = "claude-sonnet-4-5"
_API_VERSION = "2023-06-01"


class AnthropicAdapter(AIProvider):
    name = "anthropic"

    def __init__(
        self, *, api_key: str,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        if not api_key:
            raise IntegrationError(
                "Anthropic adapter requires ANTHROPIC_API_KEY in env.",
                details={"missing_setting": "ANTHROPIC_API_KEY"},
            )
        self._api_key = api_key
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._model = default_model or _DEFAULT_MODEL

    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
            "User-Agent": "Hypershop/1.0 (ai)",
        }

    async def _messages_json(
        self, *, system: str, user_parts: list[dict[str, Any]],
        max_tokens: int = 800,
    ) -> dict[str, Any]:
        body = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_parts}],
        }
        resp = await post_json(
            base_url=self._base_url,
            path="/messages",
            headers=self._headers(),
            body=body,
        )
        # Claude returns content as a list of blocks; concatenate text blocks.
        blocks = resp.get("content") or []
        text = "".join(
            b.get("text", "") for b in blocks if b.get("type") == "text"
        )
        usage = resp.get("usage") or {}
        parsed = extract_json_block(text)
        parsed["__model"] = body["model"]
        # Sum input + output tokens for cost approximation.
        parsed["__cost_units"] = (
            (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        )
        return parsed

    # ------------------------------------------------------------------

    async def ocr_prescription(self, req: OcrRequest) -> OcrResponse:
        import base64
        b64 = base64.b64encode(req.image_bytes).decode("ascii")
        system = (
            "You are a clinical OCR assistant. Extract structured data "
            "from the prescription image. Return ONLY a JSON object "
            "with fields: doctor_name, issued_on (YYYY-MM-DD), "
            "patient_name, diagnosis, items[{medicine_name, dosage, "
            "frequency, duration, notes, confidence}], confidence "
            "(0..1), raw_text."
        )
        user_parts = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": req.image_mime,
                    "data": b64,
                },
            },
            {"type": "text", "text": req.hint or "Extract the prescription."},
        ]
        parsed = await self._messages_json(
            system=system, user_parts=user_parts, max_tokens=1500,
        )
        items = []
        for it in (parsed.get("items") or []):
            try:
                items.append(OcrLineItem(
                    medicine_name=str(it.get("medicine_name") or "").strip(),
                    dosage=it.get("dosage"),
                    frequency=it.get("frequency"),
                    duration=it.get("duration"),
                    notes=it.get("notes"),
                    confidence=float(it.get("confidence") or 0.0),
                ))
            except Exception:
                continue
        issued_on = None
        if parsed.get("issued_on"):
            try:
                issued_on = _date.fromisoformat(parsed["issued_on"])
            except (TypeError, ValueError):
                issued_on = None
        cost_raw = parsed.get("__cost_units")
        return OcrResponse(
            doctor_name=parsed.get("doctor_name"),
            issued_on=issued_on,
            patient_name=parsed.get("patient_name"),
            diagnosis=parsed.get("diagnosis"),
            items=items,
            confidence=float(parsed.get("confidence") or 0.0),
            raw_text=parsed.get("raw_text"),
            raw_response=parsed,
            provider=self.name,
            model=str(parsed.get("__model") or self._model),
            cost_units=Decimal(str(cost_raw)) if cost_raw else None,
        )

    async def suggest_medicines(
        self, req: SuggestMedicinesRequest,
    ) -> SuggestMedicinesResponse:
        ctx_bits = [f"Symptoms: {req.symptoms}"]
        if req.patient_age_years is not None:
            ctx_bits.append(f"Age: {req.patient_age_years} years")
        if req.patient_sex:
            ctx_bits.append(f"Sex: {req.patient_sex}")
        if req.catalog_filter_generic:
            ctx_bits.append(f"Restrict to generic: {req.catalog_filter_generic}")
        system = (
            "You are a clinical assistant suggesting candidate medicines "
            "for a doctor to consider. Return ONLY a JSON object with "
            "fields: suggestions[{suggested_generic, suggested_strength, "
            "rationale, requires_prescription, confidence (0..1)}], "
            "confidence (0..1). NEVER prescribe."
        )
        parsed = await self._messages_json(
            system=system,
            user_parts=[{"type": "text", "text": "\n".join(ctx_bits)}],
            max_tokens=900,
        )
        items = []
        for s in (parsed.get("suggestions") or []):
            try:
                items.append(SuggestedMedicine(
                    suggested_generic=str(s.get("suggested_generic") or "").strip(),
                    suggested_strength=s.get("suggested_strength"),
                    rationale=s.get("rationale"),
                    requires_prescription=bool(s.get("requires_prescription")),
                    confidence=float(s.get("confidence") or 0.0),
                ))
            except Exception:
                continue
        cost_raw = parsed.get("__cost_units")
        return SuggestMedicinesResponse(
            suggestions=items,
            confidence=float(parsed.get("confidence") or 0.0),
            raw_response=parsed,
            provider=self.name,
            model=str(parsed.get("__model") or self._model),
            cost_units=Decimal(str(cost_raw)) if cost_raw else None,
        )

    async def predict_stock(
        self, req: StockPredictionRequest,
    ) -> StockPredictionResponse:
        system = (
            "You are a retail demand forecaster. Return ONLY a JSON "
            "object: {predicted_units_consumed (int), "
            "recommended_reorder_units (int), confidence (0..1), "
            "rationale (short)}."
        )
        user_text = (
            f"variant_id: {req.variant_id}\n"
            f"horizon_days: {req.horizon_days}\n"
            f"history_days: {req.history_days}"
        )
        parsed = await self._messages_json(
            system=system,
            user_parts=[{"type": "text", "text": user_text}],
            max_tokens=400,
        )
        predicted = int(parsed.get("predicted_units_consumed") or 0)
        depletion = (
            (utc_now().date() + timedelta(days=req.horizon_days))
            if predicted > 0 else None
        )
        cost_raw = parsed.get("__cost_units")
        return StockPredictionResponse(
            variant_id=req.variant_id,
            horizon_days=req.horizon_days,
            predicted_units_consumed=predicted,
            predicted_depletion_date=depletion,
            recommended_reorder_units=int(parsed.get("recommended_reorder_units") or 0),
            confidence=float(parsed.get("confidence") or 0.0),
            rationale=parsed.get("rationale"),
            raw_response=parsed,
            provider=self.name,
            model=str(parsed.get("__model") or self._model),
            cost_units=Decimal(str(cost_raw)) if cost_raw else None,
        )

    async def detect_fraud(
        self, req: FraudDetectionRequest,
    ) -> FraudDetectionResponse:
        system = (
            "You are a fraud-risk analyst for a Bangladesh online "
            "pharmacy. Given an order_id, return ONLY a JSON object: "
            "{risk_score (0..100), recommendation ('allow'|'review'|"
            "'block'), signals[{code, severity ('low'|'medium'|'high'), "
            "detail}], confidence (0..1)}."
        )
        parsed = await self._messages_json(
            system=system,
            user_parts=[{"type": "text", "text": f"order_id: {req.order_id}"}],
            max_tokens=600,
        )
        signals = []
        for s in (parsed.get("signals") or []):
            try:
                signals.append(FraudSignal(
                    code=str(s.get("code") or ""),
                    severity=str(s.get("severity") or "medium"),
                    detail=s.get("detail"),
                ))
            except Exception:
                continue
        risk = max(0, min(100, int(parsed.get("risk_score") or 0)))
        rec = str(parsed.get("recommendation") or "review")
        if rec not in ("allow", "review", "block"):
            rec = "review"
        cost_raw = parsed.get("__cost_units")
        return FraudDetectionResponse(
            order_id=req.order_id,
            risk_score=risk,
            recommendation=rec,
            signals=signals,
            confidence=float(parsed.get("confidence") or 0.0),
            raw_response=parsed,
            provider=self.name,
            model=str(parsed.get("__model") or self._model),
            cost_units=Decimal(str(cost_raw)) if cost_raw else None,
        )
