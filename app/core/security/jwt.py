from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError

from app.core.config import get_settings
from app.core.errors import UnauthenticatedError
from app.core.ids import new_id
from app.core.time import utc_in, utc_now

TokenKind = Literal["access", "refresh"]
_ISS: Final = "hypershop"


@dataclass(frozen=True, slots=True)
class JwtPayload:
    sub: UUID  # user id
    sid: UUID  # session id (refresh-token family identifier)
    jti: UUID  # this specific token's id
    kind: TokenKind
    roles: tuple[str, ...]
    permissions: tuple[str, ...]
    issued_at: int
    expires_at: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "iss": _ISS,
            "sub": str(self.sub),
            "sid": str(self.sid),
            "jti": str(self.jti),
            "kind": self.kind,
            "roles": list(self.roles),
            "permissions": list(self.permissions),
            "iat": self.issued_at,
            "exp": self.expires_at,
        }


def _encode(payload: JwtPayload) -> str:
    s = get_settings()
    return jwt.encode(
        payload.to_dict(),
        s.jwt_secret.get_secret_value(),
        algorithm=s.jwt_algorithm,
    )


def _decode(token: str, expected_kind: TokenKind) -> JwtPayload:
    s = get_settings()
    try:
        decoded = jwt.decode(
            token,
            s.jwt_secret.get_secret_value(),
            algorithms=[s.jwt_algorithm],
            issuer=_ISS,
            options={"require": ["exp", "iat", "sub", "sid", "jti", "kind", "iss"]},
        )
    except InvalidTokenError as e:
        raise UnauthenticatedError("Invalid or expired token.") from e

    kind = decoded.get("kind")
    if kind != expected_kind:
        raise UnauthenticatedError("Invalid token kind for this operation.")

    try:
        return JwtPayload(
            sub=UUID(decoded["sub"]),
            sid=UUID(decoded["sid"]),
            jti=UUID(decoded["jti"]),
            kind=kind,
            roles=tuple(decoded.get("roles", [])),
            permissions=tuple(decoded.get("permissions", [])),
            issued_at=int(decoded["iat"]),
            expires_at=int(decoded["exp"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise UnauthenticatedError("Malformed token.") from e


def issue_access_token(
    *,
    user_id: UUID,
    session_id: UUID,
    roles: tuple[str, ...],
    permissions: tuple[str, ...],
) -> tuple[str, JwtPayload]:
    s = get_settings()
    now = utc_now()
    payload = JwtPayload(
        sub=user_id,
        sid=session_id,
        jti=new_id(),
        kind="access",
        roles=roles,
        permissions=permissions,
        issued_at=int(now.timestamp()),
        expires_at=int(utc_in(s.jwt_access_ttl_seconds).timestamp()),
    )
    return _encode(payload), payload


def issue_refresh_token(
    *,
    user_id: UUID,
    session_id: UUID,
    jti: UUID | None = None,
) -> tuple[str, JwtPayload]:
    """Issue a refresh JWT.

    ``jti`` may be passed by callers that need it to match a Session row's
    ``current_refresh_jti`` (so token theft detection works). When omitted, a
    fresh jti is minted.
    """
    s = get_settings()
    now = utc_now()
    payload = JwtPayload(
        sub=user_id,
        sid=session_id,
        jti=jti or new_id(),
        kind="refresh",
        roles=(),
        permissions=(),
        issued_at=int(now.timestamp()),
        expires_at=int(utc_in(s.jwt_refresh_ttl_seconds).timestamp()),
    )
    return _encode(payload), payload


def decode_access_token(token: str) -> JwtPayload:
    return _decode(token, "access")


def decode_refresh_token(token: str) -> JwtPayload:
    return _decode(token, "refresh")
