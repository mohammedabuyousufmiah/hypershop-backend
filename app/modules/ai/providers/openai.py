"""OpenAI Chat Completions adapter (skeleton).

Real HTTP shape against ``POST /v1/chat/completions`` with
``response_format={'type':'json_object'}`` so the model returns
structured JSON we can parse into the AI port DTOs.

**No fake responses.** Adapter constructor refuses to build without
``OPENAI_API_KEY``; every capability hits the real OpenAI API. To
activate:

  export AI_PROVIDER=openai
  export OPENAI_API_KEY=sk-...
  # optional:
  export OPENAI_BASE_URL=https://api.openai.com/v1
  export OPENAI_MODEL_DEFAULT=gpt-4o-mini

The factory in :mod:`app.modules.ai.providers.factory` reads these and
binds this adapter at app startup.
"""

from __future__ import annotations

from datetime import date as _date
from decimal import Decimal
from typing import Any

from app.core.errors import IntegrationError
from app.core.logging import get_logger
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

_logger = get_logger("hypershop.ai.openai")
_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIAdapter(AIProvider):
    name = "openai"

    def __init__(
        self, *, api_key: str,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        if not api_key:
            raise IntegrationError(
                "OpenAI adapter requires OPENAI_API_KEY in env.",
                details={"missing_setting": "OPENAI_API_KEY"},
            )
        self._api_key = api_key
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._model = default_model or _DEFAULT_MODEL

    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Hypershop/1.0 (ai)",
        }

    async def _chat_json(
        self, *, system: str, user: str,
        max_tokens: int = 800, model: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "model": model or self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        resp = await post_json(
            base_url=self._base_url,
            path="/chat/completions",
            headers=self._headers(),
            body=body,
        )
        choices = resp.get("choices") or []
        if not choices:
            raise IntegrationError(
                "OpenAI returned no choices.",
                details={"response_id": resp.get("id")},
            )
        content = (choices[0].get("message") or {}).get("content") or ""
        usage = resp.get("usage") or {}
        parsed = extract_json_block(content)
        parsed["__model"] = body["model"]
        parsed["__cost_units"] = usage.get("total_tokens")
        return parsed

    # ------------------------------------------------------------------
    # Capability: OCR prescription
    # ------------------------------------------------------------------

    async def ocr_prescription(self, req: OcrRequest) -> OcrResponse:
        # OpenAI image input via the vision-capable models. We use the
        # same /chat/completions endpoint with a base64 image_url part.
        import base64

        b64 = base64.b64encode(req.image_bytes).decode("ascii")
        data_url = f"data:{req.image_mime};base64,{b64}"
        system = (
            "You are a clinical OCR assistant. Extract structured data "
            "from the prescription image. Return ONLY a JSON object with "
            "fields: doctor_name, issued_on (YYYY-MM-DD), patient_name, "
            "diagnosis, items[{medicine_name, dosage, frequency, "
            "duration, notes, confidence}], confidence (0..1), raw_text."
        )
        user_text = req.hint or "Extract the prescription."

        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 1200,
        }
        resp = await post_json(
            base_url=self._base_url,
            path="/chat/completions",
            headers=self._headers(),
            body=body,
        )
        choices = resp.get("choices") or []
        if not choices:
            raise IntegrationError("OpenAI returned no choices.")
        content = (choices[0].get("message") or {}).get("content") or ""
        parsed = extract_json_block(content)
        usage = resp.get("usage") or {}

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
            model=body["model"],
            cost_units=(
                Decimal(str(usage.get("total_tokens"))) if usage.get("total_tokens") else None
            ),
        )

    # ------------------------------------------------------------------
    # Capability: suggest medicines
    # ------------------------------------------------------------------

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
        if req.rx_only is True:
            ctx_bits.append("Only prescription-required medicines.")
        elif req.rx_only is False:
            ctx_bits.append("Only OTC medicines.")
        user = "\n".join(ctx_bits)
        system = (
            "You are a clinical assistant suggesting candidate medicines "
            "for a doctor to consider. Return ONLY a JSON object with "
            "fields: suggestions[{suggested_generic, suggested_strength, "
            "rationale, requires_prescription, confidence (0..1)}], "
            "confidence (0..1). NEVER prescribe — these are candidates "
            "the doctor will choose from."
        )
        parsed = await self._chat_json(system=system, user=user)
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

    # ------------------------------------------------------------------
    # Capability: predict stock — implemented by the AI as a forecast
    # over the variant's recent sales history. The adapter delegates
    # the math to the model; for a real production deployment a
    # statistical forecast (ARIMA / Prophet) is usually cheaper.
    # ------------------------------------------------------------------

    async def predict_stock(
        self, req: StockPredictionRequest,
    ) -> StockPredictionResponse:
        # The adapter doesn't have access to the DB; it can only reason
        # about the variant abstractly. This capability is best wired
        # to a statistical forecaster — leaving it as a structural
        # call so the contract is honoured.
        system = (
            "You are a retail demand forecaster. Given a variant ID and "
            "horizon, return a conservative best-guess. Return ONLY a "
            "JSON object: {predicted_units_consumed (int), "
            "recommended_reorder_units (int), confidence (0..1), "
            "rationale (short)}."
        )
        user = (
            f"variant_id: {req.variant_id}\n"
            f"horizon_days: {req.horizon_days}\n"
            f"history_days: {req.history_days}"
        )
        parsed = await self._chat_json(system=system, user=user, max_tokens=400)
        from datetime import timedelta
        from app.core.time import utc_now

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
            recommended_reorder_units=int(
                parsed.get("recommended_reorder_units") or 0,
            ),
            confidence=float(parsed.get("confidence") or 0.0),
            rationale=parsed.get("rationale"),
            raw_response=parsed,
            provider=self.name,
            model=str(parsed.get("__model") or self._model),
            cost_units=Decimal(str(cost_raw)) if cost_raw else None,
        )

    # ------------------------------------------------------------------
    # Capability: detect fraud
    # ------------------------------------------------------------------

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
        user = f"order_id: {req.order_id}"
        parsed = await self._chat_json(system=system, user=user, max_tokens=600)
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
        risk_score = max(0, min(100, int(parsed.get("risk_score") or 0)))
        rec = str(parsed.get("recommendation") or "review")
        if rec not in ("allow", "review", "block"):
            rec = "review"
        cost_raw = parsed.get("__cost_units")
        return FraudDetectionResponse(
            order_id=req.order_id,
            risk_score=risk_score,
            recommendation=rec,
            signals=signals,
            confidence=float(parsed.get("confidence") or 0.0),
            raw_response=parsed,
            provider=self.name,
            model=str(parsed.get("__model") or self._model),
            cost_units=Decimal(str(cost_raw)) if cost_raw else None,
        )
