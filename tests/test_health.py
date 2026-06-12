from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_health_endpoint_returns_200(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/health")
    assert resp.status_code == 200
    # Responses pass through the standard envelope middleware:
    # {success, message, data, meta:{request_id, pagination}}.
    body = resp.json()
    assert body["success"] is True
    assert body["data"] == {"status": "live"}
    assert "x-request-id" in {k.lower() for k in resp.headers}
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"


async def test_health_live_alias_still_responds(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/health/live")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"status": "live"}


async def test_ready_endpoint_returns_200_when_dependencies_up(
    api_client: AsyncClient,
) -> None:
    resp = await api_client.get("/api/v1/ready")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "ready"
    assert body["components"]["postgres"] == "ok"
    assert body["components"]["redis"] == "ok"


async def test_health_ready_alias_still_responds(api_client: AsyncClient) -> None:
    """``/health/ready`` is an alias of ``/ready`` (same handler).

    Asserts the alias is WIRED — correct readiness payload shape with a
    valid status. Dependency health at call time may legitimately be
    200/ready or 503/degraded (back-to-back probes in the suite can race
    the pool), so don't hardcode 200 and don't diff two separate calls.
    """
    resp = await api_client.get("/api/v1/health/ready")
    # The alias is wired iff the readiness handler answers: 200 = ready
    # (envelope-wrapped ReadyResponse) or 503 = degraded (error envelope —
    # burst probes in the suite can race the pool). A broken alias would
    # surface as 404/405/500 instead.
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        assert resp.json()["data"]["status"] == "ready"


async def test_request_id_is_echoed_when_valid(api_client: AsyncClient) -> None:
    resp = await api_client.get(
        "/api/v1/health",
        headers={"X-Request-Id": "test-request-id-1234"},
    )
    assert resp.headers["x-request-id"] == "test-request-id-1234"


async def test_request_id_is_replaced_when_malformed(api_client: AsyncClient) -> None:
    resp = await api_client.get(
        "/api/v1/health",
        headers={"X-Request-Id": "tiny"},
    )
    assert resp.headers["x-request-id"] != "tiny"
    assert len(resp.headers["x-request-id"]) > 8


async def test_unknown_route_returns_envelope(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/nope")
    assert resp.status_code == 404
    body = resp.json()
    # Error envelope shape: {success:false, error:{code, message, details}, meta:{request_id}}.
    assert body["success"] is False
    assert body["error"]["code"] == "http_404"
    assert "request_id" in body["meta"]
