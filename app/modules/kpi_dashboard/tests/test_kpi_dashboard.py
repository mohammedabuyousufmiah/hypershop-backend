"""Unit tests for the read-only KPI dashboard.

Coverage:
- ``resolve_tier`` chooses the highest tier from a multi-role principal.
- The response shape always carries the 7 sections (format invariant)
  no matter which tier is asking.
- HTTP gate:
  - Missing bearer → 401
  - Bearer without ``dashboard.read`` → 403
- Filter validation: ``date_from`` after ``date_to`` is a 422.
- Cached flag flips to True on second call with the same filters.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.ids import new_id
from app.core.security.jwt import issue_access_token
from app.core.security.principal import Principal
from app.modules.kpi_dashboard.schemas import KpiDashboardResponse
from app.modules.kpi_dashboard.service import resolve_tier

pytestmark = pytest.mark.integration


# ──────────────────────────────────────────────────────────────────────
# Pure-function tests (no DB, no HTTP)
# ──────────────────────────────────────────────────────────────────────


def _principal(*roles: str, permissions: tuple[str, ...] = ()) -> Principal:
    return Principal(
        user_id=new_id(),
        session_id=new_id(),
        roles=frozenset(roles),
        permissions=frozenset(permissions),
    )


async def test_resolve_tier_picks_highest() -> None:
    """One test covering every role-tier mapping case.

    Bundled into a single test (not parametrized) because the app-tree
    conftest's autouse async truncate fixture mis-interacts with the
    asyncpg pool on parametrized iterations under Python 3.14 — the
    second iteration sees ``Event loop is closed``. Pure-function checks
    don't need that fixture and don't need its scheduling cost either.
    """
    cases: list[tuple[tuple[str, ...], str]] = [
        (("staff",), "staff"),
        (("pharmacist",), "staff"),
        (("dispatcher",), "supervisor"),
        (("supervisor",), "supervisor"),
        (("admin",), "admin"),
        (("manager",), "admin"),
        (("super_admin",), "super_admin"),
        (("superadmin",), "super_admin"),
        (("staff", "admin"), "admin"),
        (("admin", "super_admin", "dispatcher"), "super_admin"),
        (("unknown_role",), "staff"),
        ((), "staff"),
    ]
    for roles, expected in cases:
        actual = resolve_tier(_principal(*roles))
        assert actual == expected, f"roles={roles}: expected {expected}, got {actual}"


# ──────────────────────────────────────────────────────────────────────
# HTTP gate
# ──────────────────────────────────────────────────────────────────────


async def test_kpi_dashboard_requires_bearer(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/kpi-dashboard")
    assert resp.status_code == 401, resp.text


def _bearer_with_perms(*perms: str, role: str = "staff") -> dict[str, str]:
    token, _ = issue_access_token(
        user_id=new_id(),
        session_id=new_id(),
        roles=(role,),
        permissions=perms,
    )
    return {"Authorization": f"Bearer {token}"}


async def test_kpi_dashboard_rejects_without_dashboard_read(
    api_client: AsyncClient,
) -> None:
    headers = _bearer_with_perms()  # no permissions at all
    resp = await api_client.get("/api/v1/kpi-dashboard", headers=headers)
    assert resp.status_code == 403, resp.text


async def test_kpi_dashboard_rejects_inverted_date_range(
    api_client: AsyncClient,
) -> None:
    headers = _bearer_with_perms("dashboard.read")
    today = date.today()
    resp = await api_client.get(
        "/api/v1/kpi-dashboard",
        headers=headers,
        params={"date_from": today.isoformat(), "date_to": (today - timedelta(days=1)).isoformat()},
    )
    # Pydantic v2 raises ValueError inside the model validator which
    # FastAPI maps to a 422. We don't pin the exact error code text;
    # only that the request is rejected with a 4xx and not silently
    # accepted.
    assert 400 <= resp.status_code < 500, resp.text
    assert resp.status_code != 401
    assert resp.status_code != 403


# ──────────────────────────────────────────────────────────────────────
# Response format invariant
# ──────────────────────────────────────────────────────────────────────


_REQUIRED_SECTIONS = (
    "kpi_cards",
    "round_bars",
    "horizontal_bars",
    "donut_charts",
    "line_charts",
    "alerts",
    "deep_links",
)


def _assert_shape(payload: dict[str, Any], expected_tier: str) -> None:
    """Every required section is present as a list. ``tier`` matches."""
    assert payload["tier"] == expected_tier, payload
    for key in _REQUIRED_SECTIONS:
        assert key in payload, f"missing section: {key}"
        assert isinstance(payload[key], list), f"{key} must be a list"
    assert "date_from" in payload and "date_to" in payload
    # Pydantic round-trip — also enforces the typed shape.
    KpiDashboardResponse.model_validate(payload)


@pytest.mark.parametrize(
    ("role", "expected_tier"),
    [
        ("staff", "staff"),
        ("dispatcher", "supervisor"),
        ("admin", "admin"),
        ("super_admin", "super_admin"),
    ],
)
async def test_kpi_dashboard_response_shape_per_tier(
    api_client: AsyncClient,
    role: str,
    expected_tier: str,
) -> None:
    headers = _bearer_with_perms("dashboard.read", role=role)
    resp = await api_client.get("/api/v1/kpi-dashboard", headers=headers)
    assert resp.status_code == 200, resp.text
    _assert_shape(resp.json(), expected_tier=expected_tier)


async def test_kpi_dashboard_admin_tier_includes_lower_tier_sections(
    api_client: AsyncClient,
) -> None:
    """Tiers are additive: admin must carry at least the staff KPIs."""
    staff_resp = await api_client.get(
        "/api/v1/kpi-dashboard",
        headers=_bearer_with_perms("dashboard.read", role="staff"),
    )
    admin_resp = await api_client.get(
        "/api/v1/kpi-dashboard",
        headers=_bearer_with_perms("dashboard.read", role="admin"),
    )
    assert staff_resp.status_code == 200
    assert admin_resp.status_code == 200

    staff_card_codes = {c["code"] for c in staff_resp.json()["kpi_cards"]}
    admin_card_codes = {c["code"] for c in admin_resp.json()["kpi_cards"]}
    # Admin's KPI card set is a superset of staff's.
    assert staff_card_codes.issubset(admin_card_codes), (
        f"admin missing staff cards: {staff_card_codes - admin_card_codes}"
    )


@pytest.mark.usefixtures("flush_kpi_cache")
async def test_kpi_dashboard_cache_flag_flips_on_second_call(
    api_client: AsyncClient,
) -> None:
    """First call is uncached; second identical call is served from Redis."""
    headers = _bearer_with_perms("dashboard.read", role="admin")
    first = await api_client.get("/api/v1/kpi-dashboard", headers=headers)
    second = await api_client.get("/api/v1/kpi-dashboard", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    # Bodies (excluding `cached`) should be identical.
    a = {k: v for k, v in first.json().items() if k != "cached"}
    b = {k: v for k, v in second.json().items() if k != "cached"}
    assert a == b
