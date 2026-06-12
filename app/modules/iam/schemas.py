from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.core.config import get_settings
from app.core.validation import StrictModel

_PHONE_RE = re.compile(r"^\+?[1-9]\d{6,18}$")


def _validate_password_strength(value: str) -> str:
    cfg = get_settings()
    if len(value) < cfg.password_min_length:
        raise ValueError(f"password must be at least {cfg.password_min_length} characters")
    if len(value) > 128:
        raise ValueError("password must be at most 128 characters")
    classes = sum(bool(re.search(p, value)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 3:
        raise ValueError(
            "password must include at least 3 of: lowercase, uppercase, digit, symbol",
        )
    return value


# ---------- registration & login ----------


class RegisterRequest(StrictModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _validate_password_strength(v)

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _PHONE_RE.match(v):
            raise ValueError("phone must be E.164 format, e.g. +8801712345678")
        return v


class RegisterResponse(StrictModel):
    user_id: UUID
    email: EmailStr
    status: str
    verification_required: bool


class VerifyEmailRequest(StrictModel):
    email: EmailStr
    code: str = Field(..., min_length=4, max_length=10)

    @field_validator("code")
    @classmethod
    def _digits_only(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("OTP code must be numeric")
        return v


class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class OtpRequestSmsRequest(StrictModel):
    """Customer requests an SMS OTP for phone-based login.

    The phone MUST already be on a registered + active user (we don't
    silently auto-create accounts here — that's a separate signup flow).
    For privacy, the response is the same shape regardless of whether
    the phone is registered, so an attacker can't enumerate users.
    """

    phone: str = Field(..., min_length=8, max_length=20)

    @field_validator("phone")
    @classmethod
    def _e164(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("+") and v[1:].isdigit() and 7 < len(v) < 18):
            raise ValueError("phone must be in E.164 format (e.g. +8801911740672)")
        return v


class OtpRequestSmsResponse(StrictModel):
    """Privacy-preserving — shape never reveals account existence."""

    sent: bool = True
    ttl_seconds: int


class OtpVerifySmsRequest(StrictModel):
    """Customer verifies the SMS OTP they received and gets a token pair."""

    phone: str = Field(..., min_length=8, max_length=20)
    code: str = Field(..., min_length=4, max_length=10)

    @field_validator("phone")
    @classmethod
    def _e164(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("+") and v[1:].isdigit() and 7 < len(v) < 18):
            raise ValueError("phone must be in E.164 format (e.g. +8801911740672)")
        return v

    @field_validator("code")
    @classmethod
    def _digits_only(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("OTP code must be numeric")
        return v


class TokenPair(StrictModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    access_expires_in: int
    refresh_expires_in: int


class LoginResponse(StrictModel):
    user: UserResponse
    tokens: TokenPair


class SocialSignInRequest(StrictModel):
    """Provider id_token from Google / Huawei Sign-In (mobile AuthService)."""
    id_token: str = Field(..., min_length=16, max_length=8192)


class RefreshRequest(StrictModel):
    refresh_token: str = Field(..., min_length=10, max_length=4096)


class LogoutRequest(StrictModel):
    refresh_token: str | None = Field(default=None, max_length=4096)


# ---------- password ----------


class PasswordForgotRequest(StrictModel):
    email: EmailStr


class PasswordResetRequest(StrictModel):
    token: str = Field(..., min_length=16, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _validate_password_strength(v)


class PasswordChangeRequest(StrictModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _validate_password_strength(v)


# ---------- user output ----------


class RoleResponse(StrictModel):
    name: str
    description: str | None = None


class UserResponse(StrictModel):
    id: UUID
    email: EmailStr
    full_name: str
    phone: str | None = None
    status: str
    email_verified: bool
    phone_verified: bool
    last_login_at: datetime | None = None
    created_at: datetime
    roles: list[RoleResponse]


class UserUpdateSelf(StrictModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _PHONE_RE.match(v):
            raise ValueError("phone must be E.164 format, e.g. +8801712345678")
        return v


# ---------- admin ----------


class AdminUserUpdate(StrictModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, pattern=r"^(active|suspended|deleted)$")

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _PHONE_RE.match(v):
            raise ValueError("phone must be E.164 format, e.g. +8801712345678")
        return v


class RoleAssignRequest(StrictModel):
    role: str = Field(..., min_length=1, max_length=48)


class AdminUserCreate(StrictModel):
    """Super-admin / system-admin creates an internal user + assigns
    exactly one role (RBAC). Powers POST /admin/iam/users."""

    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=120)
    password: str = Field(..., min_length=8, max_length=128)
    role: str = Field(..., min_length=1, max_length=48)
    phone: str | None = Field(default=None, max_length=32)
    force_password_reset: bool = Field(default=True)

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _validate_password_strength(v)


class RoleListResponse(StrictModel):
    roles: list[RoleResponse]


class SessionResponse(StrictModel):
    id: UUID
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


# Forward-ref resolution
LoginResponse.model_rebuild()
