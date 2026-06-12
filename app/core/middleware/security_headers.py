from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings


def _build_csp(extra_connect_src: list[str]) -> str:
    extras = "".join(f" {src}" for src in extra_connect_src) if extra_connect_src else ""
    connect = "'self'" + extras
    directives = [
        "default-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        f"connect-src {connect}",
        "img-src 'self' data:",
        "style-src 'self' 'unsafe-inline'",
        "script-src 'self'",
        "font-src 'self' data:",
        "object-src 'none'",
        "upgrade-insecure-requests",
    ]
    return "; ".join(directives)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Sets opinionated security headers on every response.

    HSTS is enabled only outside dev so curl/local browsers without TLS
    don't get pinned.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        settings = get_settings()
        response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(), payment=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        response.headers.setdefault(
            "Content-Security-Policy",
            _build_csp(settings.csp_extra_connect_src),
        )
        if settings.environment in ("staging", "production"):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains; preload",
            )
        return response
