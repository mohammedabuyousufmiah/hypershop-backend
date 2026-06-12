"""Shared httpx + JSON-mode helpers for the AI adapters.

All real adapters (OpenAI, Anthropic, Azure) hit chat/completions-style
endpoints, get back JSON, and parse it into the common :mod:`base`
DTOs. The shape conversion lives per-adapter; this file just owns the
HTTP + retry + timeout policy.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.errors import IntegrationError, ServiceUnavailableError

DEFAULT_TIMEOUT_S = 30.0


async def post_json(
    *,
    base_url: str,
    path: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """POST JSON, parse JSON response, normalise errors. No retries —
    the AI service layer surfaces failures as IntegrationError and
    records them in the AI usage ledger; retry policy belongs at a
    higher layer.
    """
    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(timeout_s),
        ) as c:
            resp = await c.post(path, headers=headers, json=body)
    except httpx.TimeoutException as e:
        raise ServiceUnavailableError(
            f"Provider timed out after {timeout_s}s.",
            details={"path": path},
        ) from e
    except httpx.HTTPError as e:
        raise IntegrationError(
            f"AI provider HTTP error: {type(e).__name__}.",
            details={"path": path, "error": str(e)[:512]},
        ) from e

    if resp.status_code == 401 or resp.status_code == 403:
        raise IntegrationError(
            "AI provider rejected credentials.",
            details={"status": resp.status_code, "path": path},
        )
    if resp.status_code == 429:
        from app.core.errors import RateLimitedError
        raise RateLimitedError(
            "AI provider rate limit hit.",
            details={"path": path},
        )
    if resp.status_code >= 500:
        raise ServiceUnavailableError(
            f"AI provider server error {resp.status_code}.",
            details={"path": path, "body": resp.text[:512]},
        )
    if resp.status_code >= 400:
        raise IntegrationError(
            f"AI provider returned {resp.status_code}.",
            details={"path": path, "body": resp.text[:512]},
        )
    try:
        return resp.json()
    except json.JSONDecodeError as e:
        raise IntegrationError(
            "AI provider returned non-JSON.",
            details={"path": path, "body": resp.text[:512]},
        ) from e


def extract_json_block(text: str) -> dict[str, Any]:
    """Tolerate models that wrap JSON in code fences. Raises
    IntegrationError if no parseable JSON object is found.
    """
    s = text.strip()
    # Strip ```json ... ``` fence if present.
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl > 0:
            s = s[first_nl + 1:]
        if s.endswith("```"):
            s = s[: -3]
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Fallback: find first { ... } block.
        start = s.find("{")
        end = s.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(s[start : end + 1])
            except json.JSONDecodeError as e:
                raise IntegrationError(
                    "AI returned text that is not parseable JSON.",
                    details={"sample": s[:256]},
                ) from e
        raise IntegrationError(
            "AI returned no JSON content.",
            details={"sample": s[:256]},
        )
