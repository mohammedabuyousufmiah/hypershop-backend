"""Shared httpx wrapper for payment-gateway adapters.

Same shape as the AI module's _http but tuned for transactional money
APIs:
  - Slightly longer default timeout (45s — gateways sometimes do
    multi-step transactions).
  - Captures HTTP status + last 2KB of body on failure for the
    payment_attempts audit row.
  - All non-2xx becomes IntegrationError; 5xx becomes ServiceUnavailable
    so retries can be applied at higher layers.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.errors import IntegrationError, ServiceUnavailableError

DEFAULT_TIMEOUT_S = 45.0


class GatewayCallResult:
    """Lightweight container the adapter passes back to the service so
    it can write a meaningful payment_attempts row.
    """

    __slots__ = ("status", "body", "elapsed_ms")

    def __init__(
        self, *, status: int, body: dict[str, Any], elapsed_ms: int,
    ) -> None:
        self.status = status
        self.body = body
        self.elapsed_ms = elapsed_ms


async def post_json(
    *,
    base_url: str,
    path: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> GatewayCallResult:
    """POST JSON to a payment gateway. Raises on non-2xx."""
    import time
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(timeout_s),
        ) as c:
            resp = await c.post(path, headers=headers, json=body or {})
    except httpx.TimeoutException as e:
        raise ServiceUnavailableError(
            f"Payment gateway timed out after {timeout_s}s.",
            details={"path": path},
        ) from e
    except httpx.HTTPError as e:
        raise IntegrationError(
            f"Payment gateway HTTP error: {type(e).__name__}.",
            details={"path": path, "error": str(e)[:512]},
        ) from e

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    body_text = resp.text[:2048] if resp.text else ""
    try:
        body_json = resp.json() if body_text else {}
    except json.JSONDecodeError:
        body_json = {"raw": body_text}

    if resp.status_code in (401, 403):
        raise IntegrationError(
            "Payment gateway rejected credentials.",
            details={"status": resp.status_code, "path": path, "body": body_json},
        )
    if resp.status_code >= 500:
        raise ServiceUnavailableError(
            f"Payment gateway server error {resp.status_code}.",
            details={"status": resp.status_code, "path": path, "body": body_json},
        )
    if resp.status_code >= 400:
        raise IntegrationError(
            f"Payment gateway returned {resp.status_code}.",
            details={"status": resp.status_code, "path": path, "body": body_json},
        )
    return GatewayCallResult(
        status=resp.status_code, body=body_json, elapsed_ms=elapsed_ms,
    )


async def post_form(
    *,
    base_url: str,
    path: str,
    headers: dict[str, str] | None,
    form: dict[str, str],
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> GatewayCallResult:
    """POST application/x-www-form-urlencoded — used by SSLCommerz."""
    import time
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(timeout_s),
        ) as c:
            resp = await c.post(path, headers=headers or {}, data=form)
    except httpx.TimeoutException as e:
        raise ServiceUnavailableError(
            f"Payment gateway timed out after {timeout_s}s.",
            details={"path": path},
        ) from e
    except httpx.HTTPError as e:
        raise IntegrationError(
            f"Payment gateway HTTP error: {type(e).__name__}.",
            details={"path": path, "error": str(e)[:512]},
        ) from e

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    body_text = resp.text[:2048] if resp.text else ""
    try:
        body_json = resp.json() if body_text else {}
    except json.JSONDecodeError:
        body_json = {"raw": body_text}

    if resp.status_code >= 500:
        raise ServiceUnavailableError(
            f"Payment gateway server error {resp.status_code}.",
            details={"status": resp.status_code, "path": path, "body": body_json},
        )
    if resp.status_code >= 400:
        raise IntegrationError(
            f"Payment gateway returned {resp.status_code}.",
            details={"status": resp.status_code, "path": path, "body": body_json},
        )
    return GatewayCallResult(
        status=resp.status_code, body=body_json, elapsed_ms=elapsed_ms,
    )


async def get_json(
    *,
    base_url: str,
    path: str,
    headers: dict[str, str],
    params: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> GatewayCallResult:
    import time
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(timeout_s),
        ) as c:
            resp = await c.get(path, headers=headers, params=params or {})
    except httpx.TimeoutException as e:
        raise ServiceUnavailableError(
            f"Payment gateway timed out after {timeout_s}s.",
            details={"path": path},
        ) from e
    except httpx.HTTPError as e:
        raise IntegrationError(
            f"Payment gateway HTTP error: {type(e).__name__}.",
            details={"path": path, "error": str(e)[:512]},
        ) from e

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    body_text = resp.text[:2048] if resp.text else ""
    try:
        body_json = resp.json() if body_text else {}
    except json.JSONDecodeError:
        body_json = {"raw": body_text}

    if resp.status_code >= 500:
        raise ServiceUnavailableError(
            f"Payment gateway server error {resp.status_code}.",
            details={"status": resp.status_code, "path": path, "body": body_json},
        )
    if resp.status_code >= 400:
        raise IntegrationError(
            f"Payment gateway returned {resp.status_code}.",
            details={"status": resp.status_code, "path": path, "body": body_json},
        )
    return GatewayCallResult(
        status=resp.status_code, body=body_json, elapsed_ms=elapsed_ms,
    )
