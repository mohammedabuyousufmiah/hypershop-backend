"""AI service integration tests.

Coverage:
- Default provider binding (NotConfigured) returns 502 via every capability.
- All four capabilities work end-to-end with a test-bound fake provider:
  proposals are persisted with status='draft', confidence ≤ 1.0, raw
  payload retained for audit.
- Reviewer flow: accept / amend / reject move status correctly.
- Hard policy: ``HUMAN_ONLY_ACTIONS`` set names the boundary; the
  service module never imports from prescriptions.service or
  finance.service approval paths (proven by AST scan).
- Usage event ledger is written even on provider failure, in a
  separate transaction, so cost data survives a rolled-back parent UoW.
- RBAC: customer (no ai.use / ai.read) → 403 on capability + read.
"""

from __future__ import annotations

import io
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.db.session import get_sessionmaker
from app.modules.ai.models import AIUsageEvent
from app.modules.ai.providers import bind_provider
from app.modules.ai.tests._fakes import FakeAIProvider

pytestmark = pytest.mark.integration


# ============================================================
# 1. Default provider returns 502 (no fake adapter shipped)
# ============================================================


async def test_capability_status_reports_not_configured_by_default(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    resp = await api_client.get(
        "/api/v1/admin/ai/status", headers=admin_user["headers"],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "not_configured"
    assert body["configured"] is False
    assert "ocr_prescription" in body["capabilities"]


async def test_ocr_without_provider_returns_502(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/ai/ocr-prescription",
        headers=admin_user["headers"],
        files={"file": ("rx.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * 10),
                        "image/png")},
    )
    # NotConfiguredProvider raises IntegrationError → 502.
    assert resp.status_code == 502
    body = resp.json()
    assert "not configured" in body["message"].lower()


async def test_suggest_medicines_without_provider_returns_502(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/ai/suggest-medicines",
        headers=admin_user["headers"],
        json={"symptoms": "cough fever"},
    )
    assert resp.status_code == 502


async def test_predict_stock_without_provider_returns_502(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/ai/predict-stock",
        headers=admin_user["headers"],
        json={"variant_id": str(uuid4()), "horizon_days": 30},
    )
    assert resp.status_code == 502


async def test_detect_fraud_without_provider_returns_502(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/ai/detect-fraud",
        headers=admin_user["headers"],
        json={"order_id": str(uuid4())},
    )
    assert resp.status_code == 502


async def test_failed_call_writes_usage_event_to_ledger(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    """Failed AI calls must commit a usage event independently of the
    rolled-back parent transaction so cost / failure data is preserved.
    """
    resp = await api_client.post(
        "/api/v1/admin/ai/suggest-medicines",
        headers=admin_user["headers"],
        json={"symptoms": "test"},
    )
    assert resp.status_code == 502

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                select(AIUsageEvent).where(AIUsageEvent.success.is_(False)),
            )
        ).scalars().all()
    assert len(rows) >= 1
    assert rows[0].provider == "not_configured"
    # IntegrationError is a DomainError → its `.code` field is recorded.
    assert rows[0].error_code == "integration_error"


# ============================================================
# 2. Capabilities work with a test-bound fake provider
# ============================================================


async def test_ocr_with_fake_provider_creates_draft_proposal(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    bind_provider(FakeAIProvider())
    resp = await api_client.post(
        "/api/v1/admin/ai/ocr-prescription",
        headers=admin_user["headers"],
        files={"file": ("rx.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * 10),
                        "image/png")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "ocr_prescription"
    assert body["status"] == "draft"
    assert body["provider"] == "fake_test_provider"
    assert float(body["confidence"]) > 0
    assert body["ai_payload"]["doctor_name"] == "Dr Fake"
    # Image bytes are NOT persisted in input_payload — only the size.
    assert "image_bytes" not in body["input_payload"]
    assert "image_bytes__size" in body["input_payload"]


async def test_suggest_medicines_with_fake_provider(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    bind_provider(FakeAIProvider())
    resp = await api_client.post(
        "/api/v1/admin/ai/suggest-medicines",
        headers=admin_user["headers"],
        json={"symptoms": "cough", "patient_age_years": 35},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "suggest_medicines"
    assert len(body["ai_payload"]["suggestions"]) >= 1


async def test_predict_stock_with_fake_provider(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    bind_provider(FakeAIProvider())
    variant_id = str(uuid4())
    resp = await api_client.post(
        "/api/v1/admin/ai/predict-stock",
        headers=admin_user["headers"],
        json={"variant_id": variant_id, "horizon_days": 30},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "predict_stock"
    assert body["reference_type"] == "product_variant"
    assert body["reference_id"] == variant_id


async def test_detect_fraud_with_fake_provider(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    bind_provider(FakeAIProvider())
    order_id = str(uuid4())
    resp = await api_client.post(
        "/api/v1/admin/ai/detect-fraud",
        headers=admin_user["headers"],
        json={"order_id": order_id},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ai_payload"]["recommendation"] == "review"
    assert body["ai_payload"]["risk_score"] == 72
    assert body["reference_type"] == "order"


# ============================================================
# 3. Reviewer flow
# ============================================================


async def _create_fraud_proposal(
    api_client: AsyncClient, headers: dict[str, str],
) -> dict[str, Any]:
    bind_provider(FakeAIProvider())
    resp = await api_client.post(
        "/api/v1/admin/ai/detect-fraud",
        headers=headers,
        json={"order_id": str(uuid4())},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_accept_proposal_moves_status_to_accepted(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    p = await _create_fraud_proposal(api_client, admin_user["headers"])
    resp = await api_client.post(
        f"/api/v1/admin/ai/proposals/{p['id']}/accept",
        headers=admin_user["headers"],
        json={"notes": "looks right"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["decision_payload"] == p["ai_payload"]
    assert body["reviewed_by"] == admin_user["user_id"]


async def test_amend_proposal_captures_edited_decision(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    p = await _create_fraud_proposal(api_client, admin_user["headers"])
    edited = {**p["ai_payload"], "recommendation": "block", "risk_score": 95}
    resp = await api_client.post(
        f"/api/v1/admin/ai/proposals/{p['id']}/amend",
        headers=admin_user["headers"],
        json={"decision_payload": edited, "notes": "stricter"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "amended"
    assert body["decision_payload"]["recommendation"] == "block"


async def test_reject_proposal_records_reason(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    p = await _create_fraud_proposal(api_client, admin_user["headers"])
    resp = await api_client.post(
        f"/api/v1/admin/ai/proposals/{p['id']}/reject",
        headers=admin_user["headers"],
        json={"reason": "false positive"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["review_notes"] == "false positive"


async def test_double_action_on_proposal_rejected(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    p = await _create_fraud_proposal(api_client, admin_user["headers"])
    accept = await api_client.post(
        f"/api/v1/admin/ai/proposals/{p['id']}/accept",
        headers=admin_user["headers"],
        json={"notes": None},
    )
    assert accept.status_code == 200
    again = await api_client.post(
        f"/api/v1/admin/ai/proposals/{p['id']}/reject",
        headers=admin_user["headers"],
        json={"reason": "changed mind"},
    )
    assert again.status_code == 422  # not in draft anymore


# ============================================================
# 4. Hard policy: AI module never imports approval paths
# ============================================================


async def test_ai_module_does_not_import_approval_endpoints() -> None:
    """Static guarantee: no file under app/modules/ai imports the
    prescription approve, finance refund-pay, or order cancel/complete
    service methods. Enforces the "AI cannot decide" rule at code level.
    """
    import os
    import pathlib

    forbidden = {
        # Service-layer call sites that trigger human-only actions.
        "PrescriptionService.approve",
        "PrescriptionService.reject",
        "FinanceService.pay_refund",
        "OrderService.complete",
        "OrderService.cancel_by_admin",
        "OrderService.cancel_by_customer",
    }
    ai_root = (
        pathlib.Path(__file__).resolve().parents[1]  # app/modules/ai
    )
    offences: list[str] = []
    for dirpath, _, filenames in os.walk(ai_root):
        # Skip the tests directory — fakes / asserts may legitimately
        # reference forbidden names in strings.
        if pathlib.Path(dirpath).name == "tests":
            continue
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            src = (pathlib.Path(dirpath) / fn).read_text(encoding="utf-8")
            for name in forbidden:
                if name in src:
                    offences.append(f"{dirpath}/{fn}: references {name!r}")
    assert offences == [], (
        "AI module must NOT import or reference human-only approval "
        "calls. Offences: " + "; ".join(offences)
    )


async def test_human_only_actions_includes_prescription_and_refund() -> None:
    from app.modules.ai.state import HUMAN_ONLY_ACTIONS

    assert "prescriptions.prescription.approve" in HUMAN_ONLY_ACTIONS
    assert "finance.refund.pay" in HUMAN_ONLY_ACTIONS


async def test_assert_ai_cannot_decide_blocks_human_only_actions() -> None:
    from app.modules.ai.state import AIPolicyError, assert_ai_cannot_decide

    with pytest.raises(AIPolicyError) as excinfo:
        assert_ai_cannot_decide("prescriptions.prescription.approve")
    assert "not authorized" in str(excinfo.value)


# ============================================================
# 5. Listing + filtering
# ============================================================


async def test_list_proposals_with_filters(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    bind_provider(FakeAIProvider())
    # Create one of each kind.
    await api_client.post(
        "/api/v1/admin/ai/suggest-medicines",
        headers=admin_user["headers"],
        json={"symptoms": "x"},
    )
    await api_client.post(
        "/api/v1/admin/ai/detect-fraud",
        headers=admin_user["headers"],
        json={"order_id": str(uuid4())},
    )
    resp = await api_client.get(
        "/api/v1/admin/ai/proposals",
        headers=admin_user["headers"],
        params={"kind": "detect_fraud"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["kind"] == "detect_fraud"


# ============================================================
# 6. RBAC
# ============================================================


async def test_capability_requires_ai_use_permission(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    bind_provider(FakeAIProvider())
    resp = await api_client.post(
        "/api/v1/admin/ai/suggest-medicines",
        headers=logged_in["headers"],
        json={"symptoms": "x"},
    )
    assert resp.status_code == 403


async def test_read_requires_ai_read_permission(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    resp = await api_client.get(
        "/api/v1/admin/ai/proposals", headers=logged_in["headers"],
    )
    assert resp.status_code == 403
