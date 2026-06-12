"""Schemas for mobile auth-security endpoints — mirror the rider/customer
MobileAuthService Kotlin DTOs exactly (snake_case wire)."""
from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field

from app.core.validation import StrictModel

_PIN_RE = r"^\d{4,8}$"
_APP_RE = r"^[a-z_]{3,24}$"


class PinSetupIn(StrictModel):
    model_config = ConfigDict(extra="ignore")
    user_id: str
    device_id: str = Field(..., min_length=1, max_length=128)
    app_type: str = Field(default="rider_app", pattern=_APP_RE)
    platform: str = Field(default="android", max_length=16)
    device_name: str | None = Field(default=None, max_length=160)
    app_version: str | None = Field(default=None, max_length=32)
    pin: str = Field(..., pattern=_PIN_RE)


class PinSetupOut(StrictModel):
    ok: bool = True
    device_id: str
    app_type: str
    pin_enabled: bool = True


class PinVerifyIn(StrictModel):
    model_config = ConfigDict(extra="ignore")
    user_id: str
    device_id: str = Field(..., min_length=1, max_length=128)
    app_type: str = Field(default="rider_app", pattern=_APP_RE)
    pin: str = Field(..., pattern=_PIN_RE)
    action_code: str | None = Field(default=None, max_length=64)


class PinVerifyOut(StrictModel):
    ok: bool
    outcome: str  # success | wrong_pin | locked | device_not_found
    reason: str | None = None
    remaining_attempts: int | None = None
    locked_until: datetime | None = None
    last_unlock_at: datetime | None = None


class BiometricEnableIn(StrictModel):
    model_config = ConfigDict(extra="ignore")
    user_id: str
    device_id: str = Field(..., min_length=1, max_length=128)
    app_type: str = Field(default="rider_app", pattern=_APP_RE)
    platform: str = Field(default="android", max_length=16)
    device_name: str | None = Field(default=None, max_length=160)
    pin: str = Field(..., pattern=_PIN_RE)  # re-confirm PIN to enable biometric


class BiometricEnableOut(StrictModel):
    ok: bool = True
    biometric_enabled: bool
    device_id: str
    app_type: str


class BiometricDisableIn(StrictModel):
    model_config = ConfigDict(extra="ignore")
    user_id: str
    device_id: str = Field(..., min_length=1, max_length=128)
    app_type: str = Field(default="rider_app", pattern=_APP_RE)


class BiometricUnlockIn(StrictModel):
    model_config = ConfigDict(extra="ignore")
    user_id: str
    device_id: str = Field(..., min_length=1, max_length=128)
    app_type: str = Field(default="rider_app", pattern=_APP_RE)
    action_code: str | None = Field(default=None, max_length=64)


class BiometricUnlockOut(StrictModel):
    ok: bool
    reason: str | None = None
    last_unlock_at: datetime | None = None


class DeviceSessionOut(StrictModel):
    id: str
    user_id: str
    device_id: str
    app_type: str
    platform: str
    biometric_enabled: bool = False
    pin_enabled: bool = False
    device_name: str | None = None
    last_used_at: datetime | None = None
    last_unlock_at: datetime | None = None
    created_at: datetime


class LogoutDeviceIn(StrictModel):
    model_config = ConfigDict(extra="ignore")
    user_id: str
    device_id: str = Field(..., min_length=1, max_length=128)
    app_type: str = Field(default="rider_app", pattern=_APP_RE)


class ReauthCheckIn(StrictModel):
    model_config = ConfigDict(extra="ignore")
    user_id: str
    device_id: str = Field(..., min_length=1, max_length=128)
    app_type: str = Field(default="rider_app", pattern=_APP_RE)
    action_code: str = Field(..., max_length=64)


class ReauthCheckOut(StrictModel):
    needs_reauth: bool = False
    reason: str | None = None
