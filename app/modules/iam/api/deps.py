from __future__ import annotations

from fastapi import Request

from app.modules.iam.service import RequestContext


def request_context(request: Request) -> RequestContext:
    """Extract per-request meta for audit + session creation."""
    client_ip: str | None = None
    if request.client is not None:
        client_ip = request.client.host
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",", 1)[0].strip() or client_ip
    user_agent = request.headers.get("user-agent")
    return RequestContext(
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip,
        user_agent=user_agent,
    )
