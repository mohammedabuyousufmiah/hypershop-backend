"""Mobile auth-security endpoints (rider/customer MobileAuthService).

Mounted at /api/v1/auth/* (alongside iam auth). All require a valid bearer
session; the body's user_id is cross-checked against the principal.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.modules.mobile_auth.models import MobileDeviceSession
from app.modules.mobile_auth.schemas import (
    BiometricDisableIn,
    BiometricEnableIn,
    BiometricEnableOut,
    BiometricUnlockIn,
    BiometricUnlockOut,
    DeviceSessionOut,
    LogoutDeviceIn,
    PinSetupIn,
    PinSetupOut,
    PinVerifyIn,
    PinVerifyOut,
    ReauthCheckIn,
    ReauthCheckOut,
)
from app.modules.mobile_auth.service import MobileAuthError, MobileAuthService

router = APIRouter(prefix="/auth", tags=["mobile-auth"])


def _dev_to_out(d: MobileDeviceSession) -> DeviceSessionOut:
    return DeviceSessionOut(
        id=str(d.id),
        user_id=str(d.user_id),
        device_id=d.device_id,
        app_type=d.app_type,
        platform=d.platform,
        biometric_enabled=d.biometric_enabled,
        pin_enabled=d.pin_enabled,
        device_name=d.device_name,
        last_used_at=d.last_used_at,
        last_unlock_at=d.last_unlock_at,
        created_at=d.created_at,
    )


def _forbidden(e: MobileAuthError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/pin/setup", response_model=PinSetupOut)
async def pin_setup(
    payload: PinSetupIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PinSetupOut:
    async with uow.transactional() as session:
        try:
            d = await MobileAuthService(session).pin_setup(
                principal=principal, user_id=payload.user_id, device_id=payload.device_id,
                app_type=payload.app_type, platform=payload.platform,
                device_name=payload.device_name, app_version=payload.app_version, pin=payload.pin,
            )
        except MobileAuthError as e:
            raise _forbidden(e) from e
        return PinSetupOut(ok=True, device_id=d.device_id, app_type=d.app_type, pin_enabled=d.pin_enabled)


@router.post("/pin/verify", response_model=PinVerifyOut)
async def pin_verify(
    payload: PinVerifyIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PinVerifyOut:
    async with uow.transactional() as session:
        try:
            outcome, d = await MobileAuthService(session).pin_verify(
                principal=principal, user_id=payload.user_id, device_id=payload.device_id,
                app_type=payload.app_type, pin=payload.pin,
            )
        except MobileAuthError as e:
            raise _forbidden(e) from e
    remaining = None
    if d is not None and outcome in ("wrong_pin", "locked"):
        from app.modules.mobile_auth.service import MAX_ATTEMPTS
        remaining = max(0, MAX_ATTEMPTS - (d.failed_attempts or 0))
    return PinVerifyOut(
        ok=(outcome == "success"),
        outcome=outcome,
        reason=None if outcome == "success" else outcome,
        remaining_attempts=remaining,
        locked_until=d.locked_until if d else None,
        last_unlock_at=d.last_unlock_at if d else None,
    )


@router.post("/biometric/enable", response_model=BiometricEnableOut)
async def biometric_enable(
    payload: BiometricEnableIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BiometricEnableOut:
    async with uow.transactional() as session:
        try:
            d = await MobileAuthService(session).biometric_enable(
                principal=principal, user_id=payload.user_id, device_id=payload.device_id,
                app_type=payload.app_type, platform=payload.platform,
                device_name=payload.device_name, pin=payload.pin,
            )
        except MobileAuthError as e:
            raise _forbidden(e) from e
        return BiometricEnableOut(ok=True, biometric_enabled=True, device_id=d.device_id, app_type=d.app_type)


@router.post("/biometric/disable")
async def biometric_disable(
    payload: BiometricDisableIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, bool]:
    async with uow.transactional() as session:
        try:
            ok = await MobileAuthService(session).biometric_disable(
                principal=principal, user_id=payload.user_id,
                device_id=payload.device_id, app_type=payload.app_type,
            )
        except MobileAuthError as e:
            raise _forbidden(e) from e
        return {"ok": ok, "biometric_enabled": False}


@router.post("/biometric/unlock", response_model=BiometricUnlockOut)
async def biometric_unlock(
    payload: BiometricUnlockIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BiometricUnlockOut:
    async with uow.transactional() as session:
        try:
            ok, reason, d = await MobileAuthService(session).biometric_unlock(
                principal=principal, user_id=payload.user_id,
                device_id=payload.device_id, app_type=payload.app_type,
            )
        except MobileAuthError as e:
            raise _forbidden(e) from e
    return BiometricUnlockOut(ok=ok, reason=reason, last_unlock_at=d.last_unlock_at if d else None)


@router.get("/devices", response_model=list[DeviceSessionOut])
async def list_devices(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    app_type: str | None = None,
) -> list[DeviceSessionOut]:
    async with uow.transactional() as session:
        rows = await MobileAuthService(session).list_devices(principal=principal, app_type=app_type)
    return [_dev_to_out(d) for d in rows]


@router.post("/logout-device")
async def logout_device(
    payload: LogoutDeviceIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, bool]:
    async with uow.transactional() as session:
        try:
            ok = await MobileAuthService(session).logout_device(
                principal=principal, user_id=payload.user_id,
                device_id=payload.device_id, app_type=payload.app_type,
            )
        except MobileAuthError as e:
            raise _forbidden(e) from e
        return {"ok": ok}


@router.post("/reauth/check", response_model=ReauthCheckOut)
async def reauth_check(
    payload: ReauthCheckIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReauthCheckOut:
    async with uow.transactional() as session:
        try:
            needs, reason = await MobileAuthService(session).reauth_check(
                principal=principal, user_id=payload.user_id,
                device_id=payload.device_id, app_type=payload.app_type,
            )
        except MobileAuthError as e:
            raise _forbidden(e) from e
    return ReauthCheckOut(needs_reauth=needs, reason=reason)
