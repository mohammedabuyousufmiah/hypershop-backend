from __future__ import annotations

import re

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.ids import new_id
from app.core.logging import bind_contextvars, clear_contextvars

_HEADER = "x-request-id"
_VALID = re.compile(r"^[A-Za-z0-9._\-]{8,128}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Mints a request id (or echoes a trusted client one), exposes it on
    ``request.state.request_id``, binds it to the structured-logging context,
    and emits it in the response header.

    Inbound IDs are accepted only if they match a strict shape — we never
    log arbitrary client-provided strings.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        incoming = request.headers.get(_HEADER)
        request_id = incoming if incoming and _VALID.match(incoming) else str(new_id())
        request.state.request_id = request_id
        bind_contextvars(request_id=request_id, path=request.url.path, method=request.method)
        try:
            response = await call_next(request)
        finally:
            clear_contextvars()
        response.headers[_HEADER] = request_id
        return response
