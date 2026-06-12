"""Mobile auth-security service — per-device PIN / biometric quick-login.

Security posture:
  * PIN stored as argon2 hash (never plaintext).
  * Brute-force lockout: after MAX_ATTEMPTS wrong PINs the device is locked
    for LOCK_SECONDS; counter resets on success.
  * reauth/check: a successful PIN/biometric unlock is valid for
    REAUTH_WINDOW_SECONDS; sensitive actions past that window must re-auth.
  * The caller must be the authenticated bearer user; we cross-check the
    body user_id against the principal so one user can't touch another's
    device rows.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.security.passwords import hash_password, verify_password
from app.core.security.principal import Principal
from app.core.time import utc_in, utc_now
from app.modules.mobile_auth.models import MobileDeviceSession

MAX_ATTEMPTS = 5
LOCK_SECONDS = 300  # 5 min lockout after MAX_ATTEMPTS
REAUTH_WINDOW_SECONDS = 300  # an unlock is "fresh" for 5 min


class MobileAuthError(Exception):
    """Caller mismatch / forbidden (body user_id != bearer principal)."""


class MobileAuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _assert_owner(self, principal: Principal, user_id_str: str) -> None:
        if str(principal.user_id) != str(user_id_str):
            raise MobileAuthError("user_id does not match the authenticated session.")

    async def _get(self, user_id: UUID, device_id: str, app_type: str) -> MobileDeviceSession | None:
        return (
            await self.session.execute(
                select(MobileDeviceSession).where(
                    MobileDeviceSession.user_id == user_id,
                    MobileDeviceSession.device_id == device_id,
                    MobileDeviceSession.app_type == app_type,
                ),
            )
        ).scalar_one_or_none()

    async def pin_setup(
        self, *, principal: Principal, user_id: str, device_id: str, app_type: str,
        platform: str, device_name: str | None, app_version: str | None, pin: str,
    ) -> MobileDeviceSession:
        self._assert_owner(principal, user_id)
        row = await self._get(principal.user_id, device_id, app_type)
        if row is None:
            row = MobileDeviceSession(
                user_id=principal.user_id, device_id=device_id, app_type=app_type,
            )
            self.session.add(row)
        row.platform = platform
        row.device_name = device_name
        row.app_version = app_version
        row.pin_hash = hash_password(pin)
        row.pin_enabled = True
        row.failed_attempts = 0
        row.locked_until = None
        row.is_active = True
        row.last_used_at = utc_now()
        await self.session.flush()
        await self.session.refresh(row)
        await record_audit(
            actor=principal, action="mobile_auth.pin.setup",
            resource_type="mobile_device_session", resource_id=row.id,
            metadata={"device_id": device_id, "app_type": app_type},
        )
        return row

    async def pin_verify(
        self, *, principal: Principal, user_id: str, device_id: str, app_type: str, pin: str,
    ) -> tuple[str, MobileDeviceSession | None]:
        """Returns (outcome, row). outcome ∈ success|wrong_pin|locked|device_not_found."""
        self._assert_owner(principal, user_id)
        row = await self._get(principal.user_id, device_id, app_type)
        if row is None or not row.pin_enabled or not row.pin_hash:
            return "device_not_found", None
        if row.locked_until is not None and row.locked_until > utc_now():
            return "locked", row
        if verify_password(row.pin_hash, pin):
            row.failed_attempts = 0
            row.locked_until = None
            row.last_unlock_at = utc_now()
            row.last_used_at = utc_now()
            await self.session.flush()
            await self.session.refresh(row)
            await record_audit(
                actor=principal, action="mobile_auth.pin.verify.success",
                resource_type="mobile_device_session", resource_id=row.id,
            )
            return "success", row
        row.failed_attempts = (row.failed_attempts or 0) + 1
        if row.failed_attempts >= MAX_ATTEMPTS:
            row.locked_until = utc_in(LOCK_SECONDS)
        await self.session.flush()
        await self.session.refresh(row)
        await record_audit(
            actor=principal, action="mobile_auth.pin.verify.fail",
            resource_type="mobile_device_session", resource_id=row.id,
            outcome="failure", metadata={"attempts": row.failed_attempts},
        )
        return ("locked" if row.locked_until else "wrong_pin"), row

    async def biometric_enable(
        self, *, principal: Principal, user_id: str, device_id: str, app_type: str,
        platform: str, device_name: str | None, pin: str,
    ) -> MobileDeviceSession:
        """Enabling biometric requires a correct PIN (proves possession)."""
        self._assert_owner(principal, user_id)
        outcome, row = await self.pin_verify(
            principal=principal, user_id=user_id, device_id=device_id,
            app_type=app_type, pin=pin,
        )
        if outcome != "success" or row is None:
            raise MobileAuthError(f"PIN check failed ({outcome}); cannot enable biometric.")
        row.biometric_enabled = True
        row.platform = platform
        if device_name:
            row.device_name = device_name
        await self.session.flush()
        await self.session.refresh(row)
        await record_audit(
            actor=principal, action="mobile_auth.biometric.enable",
            resource_type="mobile_device_session", resource_id=row.id,
        )
        return row

    async def biometric_disable(
        self, *, principal: Principal, user_id: str, device_id: str, app_type: str,
    ) -> bool:
        self._assert_owner(principal, user_id)
        row = await self._get(principal.user_id, device_id, app_type)
        if row is None:
            return False
        row.biometric_enabled = False
        await self.session.flush()
        await record_audit(
            actor=principal, action="mobile_auth.biometric.disable",
            resource_type="mobile_device_session", resource_id=row.id,
        )
        return True

    async def biometric_unlock(
        self, *, principal: Principal, user_id: str, device_id: str, app_type: str,
    ) -> tuple[bool, str | None, MobileDeviceSession | None]:
        """Biometric is verified on-device (secure element). The server records
        the unlock for the reauth window. Returns (ok, reason, row)."""
        self._assert_owner(principal, user_id)
        row = await self._get(principal.user_id, device_id, app_type)
        if row is None or not row.biometric_enabled:
            return False, "biometric_not_enabled", None
        if row.locked_until is not None and row.locked_until > utc_now():
            return False, "locked", row
        row.last_unlock_at = utc_now()
        row.last_used_at = utc_now()
        await self.session.flush()
        await self.session.refresh(row)
        await record_audit(
            actor=principal, action="mobile_auth.biometric.unlock",
            resource_type="mobile_device_session", resource_id=row.id,
        )
        return True, None, row

    async def list_devices(self, *, principal: Principal, app_type: str | None = None) -> list[MobileDeviceSession]:
        stmt = select(MobileDeviceSession).where(
            MobileDeviceSession.user_id == principal.user_id,
            MobileDeviceSession.is_active.is_(True),
        ).order_by(MobileDeviceSession.last_used_at.desc().nullslast())
        if app_type:
            stmt = stmt.where(MobileDeviceSession.app_type == app_type)
        return list((await self.session.execute(stmt)).scalars().all())

    async def logout_device(
        self, *, principal: Principal, user_id: str, device_id: str, app_type: str,
    ) -> bool:
        self._assert_owner(principal, user_id)
        row = await self._get(principal.user_id, device_id, app_type)
        if row is None:
            return False
        row.is_active = False
        row.pin_enabled = False
        row.biometric_enabled = False
        await self.session.flush()
        await record_audit(
            actor=principal, action="mobile_auth.logout_device",
            resource_type="mobile_device_session", resource_id=row.id,
        )
        return True

    async def reauth_check(
        self, *, principal: Principal, user_id: str, device_id: str, app_type: str,
    ) -> tuple[bool, str | None]:
        """needs_reauth=True when there is no fresh unlock within the window."""
        self._assert_owner(principal, user_id)
        row = await self._get(principal.user_id, device_id, app_type)
        if row is None or row.last_unlock_at is None:
            return True, "no_unlock_on_record"
        if row.last_unlock_at < utc_in(-REAUTH_WINDOW_SECONDS):
            return True, "unlock_expired"
        return False, None
