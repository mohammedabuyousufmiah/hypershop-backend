from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import UnauthenticatedError
from app.core.security.jwt import decode_access_token
from app.core.security.principal import Principal
from app.core.security.rbac import _set_principal_dep

_bearer = HTTPBearer(auto_error=False, bearerFormat="JWT")


def _resolve_token(
    credentials: HTTPAuthorizationCredentials | None,
    cookie_token: str | None,
) -> str | None:
    """Pick the access token from Authorization first, then cookie.

    Bearer header wins so mobile + RSC + service-to-service callers
    (which never set browser cookies) keep working unchanged. Browser
    flows fall back to the HttpOnly access_token cookie set by
    /auth/login.
    """
    if credentials is not None and credentials.scheme.lower() == "bearer":
        return credentials.credentials
    return cookie_token or None


async def get_current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    access_cookie: Annotated[str | None, Cookie(alias="access_token")] = None,
) -> Principal:
    token = _resolve_token(credentials, access_cookie)
    if token is None:
        raise UnauthenticatedError("Missing bearer token.")

    payload = decode_access_token(token)
    principal = Principal(
        user_id=payload.sub,
        session_id=payload.sid,
        roles=frozenset(payload.roles),
        permissions=frozenset(payload.permissions),
    )
    request.state.principal = principal
    return principal


async def get_optional_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    access_cookie: Annotated[str | None, Cookie(alias="access_token")] = None,
) -> Principal | None:
    token = _resolve_token(credentials, access_cookie)
    if token is None:
        return None
    try:
        payload = decode_access_token(token)
    except UnauthenticatedError:
        return None
    principal = Principal(
        user_id=payload.sub,
        session_id=payload.sid,
        roles=frozenset(payload.roles),
        permissions=frozenset(payload.permissions),
    )
    request.state.principal = principal
    return principal


# Wire RBAC's deferred dep to the real implementation.
_set_principal_dep(get_current_principal)
