"""Canonical re-export of the RequestId middleware.

  from app.middleware.request_id import RequestIdMiddleware

is the preferred import; the original location
`app.core.middleware.request_id.RequestIdMiddleware` still works.
"""
from app.core.middleware.request_id import RequestIdMiddleware

__all__ = ["RequestIdMiddleware"]
