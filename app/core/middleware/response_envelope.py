"""Response envelope middleware.

Wraps every 2xx JSON response from the API into the standard envelope:

    {
      "success": true,
      "message": "OK",
      "data": <original body>,
      "meta": {
        "request_id": "<uuid>",
        "pagination": {}    // populated when the body is a paginated dict
      }
    }

Error responses (4xx/5xx) are produced by `app/core/exception_handlers.py`
and ALREADY carry the envelope shape — this middleware skips them.

Bypasses (return raw response unchanged):
  - non-JSON content types (text, html, octet-stream, file streams)
  - the OpenAPI surface: /openapi.json, /docs, /redoc
  - infrastructure surfaces: /health, /ready, /metrics, root /
  - already-enveloped responses (we sniff the parsed body for `success` key)

Pagination passthrough:
  If the wrapped body has `total` (and optionally `offset`/`limit`),
  those are lifted into `meta.pagination` while `data` keeps the original
  shape. This way FE can read pagination from the same place across
  every list endpoint without each route having to know the contract.
"""
from __future__ import annotations

import json
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# Path prefixes that must NOT be wrapped. These are infra / docs routes
# whose consumers (browsers, prom scrapers, FastAPI's own UI) expect a
# specific shape.
_BYPASS_PREFIXES: tuple[str, ...] = (
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
    "/ready",
    "/metrics",
    "/",  # exact-only — guarded below
)


def _should_bypass(path: str) -> bool:
    if path == "/":
        return True
    for p in _BYPASS_PREFIXES:
        if p == "/":
            continue
        if path == p or path.startswith(p + "/") or path.startswith(p + "?"):
            return True
    return False


def _extract_pagination(body: Any) -> dict[str, Any]:
    """Return a pagination dict if `body` looks like a paginated list."""
    if not isinstance(body, dict):
        return {}
    out: dict[str, Any] = {}
    # Common keys across the codebase: total, offset, limit, page, size, pages.
    for k in ("total", "offset", "limit", "page", "size", "pages"):
        if k in body and isinstance(body[k], int):
            out[k] = body[k]
    return out


class ResponseEnvelopeMiddleware(BaseHTTPMiddleware):
    """Wrap 2xx JSON responses in `{success, message, data, meta}`."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _should_bypass(path):
            return await call_next(request)

        response = await call_next(request)

        # Only wrap successful JSON responses. Errors come from
        # exception_handlers and already carry the envelope.
        if response.status_code >= 400:
            return response

        ct = response.headers.get("content-type", "")
        if "application/json" not in ct.lower():
            return response

        # Read the body. Starlette's StreamingResponse needs the iterator
        # consumed; for our routes the responses are small JSON.
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            chunks.append(chunk)
        raw = b"".join(chunks)

        # Preserve multi-value headers (notably Set-Cookie). Starlette
        # `Response(headers=...)` only accepts a dict-like, which collapses
        # duplicates — losing the access_token cookie on login. Workaround:
        # build the Response, then overwrite its raw_headers with a list
        # (which preserves every entry including duplicate Set-Cookie).
        # When the body changes we recompute content-length; otherwise we
        # pass the original through unchanged.
        def _build(content: bytes, *, media_type: str | None, body_changed: bool) -> Response:
            resp = Response(content=content, status_code=response.status_code, media_type=media_type)
            if body_changed:
                # Drop upstream content-length + content-type (we set fresh ones).
                passthrough = [
                    (k, v) for k, v in response.raw_headers
                    if k.lower() not in (b"content-length", b"content-type")
                ]
                # Keep the freshly-computed content-length + content-type that
                # Response.__init__ just produced (they're already in resp.raw_headers).
                resp.raw_headers = list(resp.raw_headers) + passthrough
            else:
                # Body unchanged → keep upstream headers verbatim.
                resp.raw_headers = list(response.raw_headers)
            return resp

        if not raw:
            # 204-style; nothing to wrap.
            return _build(b"", media_type=response.media_type, body_changed=False)

        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Not JSON despite the header — return as-is.
            return _build(raw, media_type=response.media_type, body_changed=False)

        # Already enveloped? Don't double-wrap. Either an upstream call
        # we proxied, or middleware accidentally double-mounted.
        if isinstance(body, dict) and "success" in body and "data" in body:
            return _build(raw, media_type=response.media_type, body_changed=False)

        request_id = getattr(request.state, "request_id", None)
        wrapped: dict[str, Any] = {
            "success": True,
            "message": "OK",
            "data": body,
            "meta": {
                "request_id": request_id,
                "pagination": _extract_pagination(body),
            },
        }
        new_body = json.dumps(wrapped, separators=(",", ":")).encode("utf-8")
        return _build(new_body, media_type="application/json", body_changed=True)
