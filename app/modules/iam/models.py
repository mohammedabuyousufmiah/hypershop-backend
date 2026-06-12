from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class UserStatus(StrEnum):
    PENDING_VERIFY = "pending_verify"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class OtpPurpose(StrEnum):
    EMAIL_VERIFY = "email_verify"
    LOGIN = "login"
    PASSWORD_RESET = "password_reset"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(CITEXT(), nullable=False, unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    phone_verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        String(16),
        nullable=False,
        server_default=UserStatus.PENDING_VERIFY.value,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)
    failed_login_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    locked_until: Mapped[datetime | None] = mapped_column(nullable=True)

    roles: Mapped[list[Role]] = relationship(
        "Role",
        secondary="user_roles",
        primaryjoin="User.id == UserRole.user_id",
        secondaryjoin="Role.id == UserRole.role_id",
        lazy="selectin",
        viewonly=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_verify','active','suspended','deleted')",
            name="status_enum",
        ),
        Index("ix_users_status", "status"),
    )


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_system: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("false"),
    )

    permissions: Mapped[list[Permission]] = relationship(
        "Permission",
        secondary="role_permissions",
        lazy="selectin",
        viewonly=True,
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    assigned_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class Session(Base):
    """A refresh-token session.

    The session id (``id``) is also the JWT ``sid`` claim. ``current_refresh_jti``
    is the only valid refresh token for this session at any moment; on rotate
    it is updated atomically. If a refresh token arrives whose jti matches
    ``prev_refresh_jti`` (already rotated), we treat it as theft, revoke the
    session, and emit an audit event.
    """

    __tablename__ = "sessions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    current_refresh_jti: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    prev_refresh_jti: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    last_used_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_active", "user_id", "revoked_at"),
        Index("ix_sessions_current_refresh_jti", "current_refresh_jti"),
    )


class OtpCode(Base):
    """One-time passcode. Only the ``code_hash`` is stored (Argon2)."""

    __tablename__ = "otp_codes"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    email: Mapped[str | None] = mapped_column(CITEXT(), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    purpose: Mapped[OtpPurpose] = mapped_column(String(32), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "purpose IN ('email_verify','login','password_reset')",
            name="purpose_enum",
        ),
        CheckConstraint(
            "user_id IS NOT NULL OR email IS NOT NULL OR phone IS NOT NULL",
            name="otp_target_present",
        ),
        Index("ix_otp_codes_email_purpose", "email", "purpose"),
        Index("ix_otp_codes_user_purpose", "user_id", "purpose"),
        Index("ix_otp_codes_expires_at", "expires_at"),
    )


class PasswordResetToken(Base):
    """A password-reset token. Only the SHA-256 hash is stored; the plaintext
    is e-mailed once and never persisted on the server.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    requested_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
        Index("ix_password_reset_tokens_user_id", "user_id"),
        Index("ix_password_reset_tokens_expires_at", "expires_at"),
    )
