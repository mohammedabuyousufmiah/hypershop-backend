"""Authentication primitives.

Token issuance/decoding/blacklist live in `app.jwt_keys`. This file owns
password hashing, admin seeding, and the FastAPI dependency exports that
existing routes import from `app.security`.
"""
from __future__ import annotations

import logging
import secrets

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.jwt_keys import (
    admin_user,
    current_user,
    dashboard_user,
    decode_token,
    is_revoked,
    issue_token,
    revoke_jti,
)
from app.models import User

logger = logging.getLogger(__name__)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

__all__ = [
    "hash_password",
    "verify_password",
    "token_for",
    "issue_token_pair",
    "decode_token",
    "revoke_jti",
    "is_revoked",
    "current_user",
    "dashboard_user",
    "admin_user",
    "seed_admin",
]


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def token_for(user: User, token_type: str = "access") -> str:
    """Backward-compatible single-token issuance (drops the jti)."""
    token, _jti = issue_token(user, token_type)
    return token


def issue_token_pair(user: User) -> dict[str, str]:
    access, _ = issue_token(user, "access")
    refresh, refresh_jti = issue_token(user, "refresh")
    return {"access_token": access, "refresh_token": refresh, "refresh_jti": refresh_jti, "token_type": "bearer"}


def seed_admin(db: Session) -> None:
    cfg = settings()
    # Startup runs before any request → tenant context is unset. Use bypass
    # so the User-existence check sees the row regardless of tenant filter.
    from app.tenancy import with_tenant_bypass

    with with_tenant_bypass():
        if db.scalar(select(User).where(User.username == cfg.admin_bootstrap_username)):
            return

    bootstrap = cfg.admin_bootstrap_password
    if not bootstrap:
        if cfg.is_production:
            raise RuntimeError(
                "ADMIN_BOOTSTRAP_PASSWORD must be set in production. Refusing to seed admin "
                "with a default password."
            )
        # Generate a strong, random one-time password and log it (dev only).
        bootstrap = secrets.token_urlsafe(16)
        logger.warning(
            "admin_seed_generated_random_password username=%s password=%s "
            "(SAVE THIS — it will not be shown again, and you MUST change it on first login)",
            cfg.admin_bootstrap_username,
            bootstrap,
        )

    db.add(
        User(
            name="Super Admin",
            username=cfg.admin_bootstrap_username,
            password_hash=hash_password(bootstrap),
            role="super_admin",
            must_change_password=cfg.admin_force_change_on_first_login,
            language_skill="bangla",
        )
    )
    db.commit()
