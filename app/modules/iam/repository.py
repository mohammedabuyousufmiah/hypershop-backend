from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.core.time import utc_now
from app.modules.iam.models import (
    OtpCode,
    OtpPurpose,
    PasswordResetToken,
    Permission,
    Role,
    Session,
    User,
    UserRole,
    UserStatus,
)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        stmt = (
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.roles).selectinload(Role.permissions))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        stmt = (
            select(User)
            .where(User.email == email)
            .options(selectinload(User.roles).selectinload(Role.permissions))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def email_exists(self, email: str) -> bool:
        stmt = select(User.id).where(User.email == email)
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def phone_exists(self, phone: str) -> bool:
        stmt = select(User.id).where(User.phone == phone)
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def get_by_phone(self, phone: str) -> User | None:
        stmt = (
            select(User)
            .where(User.phone == phone)
            .options(selectinload(User.roles).selectinload(Role.permissions))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def mark_phone_verified(self, user_id: UUID) -> None:
        await self.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(phone_verified_at=utc_now()),
        )

    async def create(
        self,
        *,
        email: str,
        full_name: str,
        password_hash: str,
        phone: str | None,
        status: UserStatus,
    ) -> User:
        user = User(
            email=email,
            full_name=full_name,
            password_hash=password_hash,
            phone=phone,
            status=status,
        )
        self.session.add(user)
        try:
            await self.session.flush()
        except IntegrityError as e:
            raise ConflictError(
                "Email or phone already registered.",
                details={"field": "email_or_phone"},
            ) from e
        return user

    async def update_password(self, user_id: UUID, password_hash: str) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(password_hash=password_hash),
        )

    async def update_self_fields(
        self,
        user_id: UUID,
        *,
        full_name: str | None = None,
        phone: str | None = None,
    ) -> None:
        values: dict[str, object] = {}
        if full_name is not None:
            values["full_name"] = full_name
        if phone is not None:
            values["phone"] = phone
        if not values:
            return
        try:
            await self.session.execute(update(User).where(User.id == user_id).values(**values))
        except IntegrityError as e:
            raise ConflictError("Phone already in use.") from e

    async def admin_update(
        self,
        user_id: UUID,
        *,
        full_name: str | None = None,
        phone: str | None = None,
        status: UserStatus | None = None,
    ) -> None:
        values: dict[str, object] = {}
        if full_name is not None:
            values["full_name"] = full_name
        if phone is not None:
            values["phone"] = phone
        if status is not None:
            values["status"] = status
        if not values:
            return
        try:
            await self.session.execute(update(User).where(User.id == user_id).values(**values))
        except IntegrityError as e:
            raise ConflictError("Phone already in use.") from e

    async def mark_email_verified(self, user_id: UUID) -> None:
        await self.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                email_verified_at=utc_now(),
                status=UserStatus.ACTIVE,
            ),
        )

    async def record_login_success(self, user_id: UUID) -> None:
        await self.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                last_login_at=utc_now(),
                failed_login_count=0,
                locked_until=None,
            ),
        )

    async def record_login_failure(
        self,
        user_id: UUID,
        *,
        lockout_threshold: int,
        lockout_seconds: int,
    ) -> None:
        from app.core.time import utc_in

        result = await self.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(failed_login_count=User.failed_login_count + 1)
            .returning(User.failed_login_count),
        )
        new_count = result.scalar_one()
        if new_count >= lockout_threshold:
            await self.session.execute(
                update(User)
                .where(User.id == user_id)
                .values(locked_until=utc_in(lockout_seconds), failed_login_count=0),
            )

    async def list_paginated(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[User], int]:
        from sqlalchemy import func

        total = (await self.session.execute(select(func.count()).select_from(User))).scalar_one()
        stmt = (
            select(User)
            .order_by(User.created_at.desc())
            .offset(offset)
            .limit(limit)
            .options(selectinload(User.roles))
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return rows, int(total)


class RoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_name(self, name: str) -> Role | None:
        stmt = select(Role).where(Role.name == name).options(selectinload(Role.permissions))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> Sequence[Role]:
        stmt = select(Role).order_by(Role.name).options(selectinload(Role.permissions))
        return (await self.session.execute(stmt)).scalars().all()

    async def assign_to_user(
        self,
        *,
        user_id: UUID,
        role_id: UUID,
        assigned_by: UUID | None,
    ) -> None:
        try:
            self.session.add(
                UserRole(user_id=user_id, role_id=role_id, assigned_by=assigned_by),
            )
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            # idempotent: already assigned is not an error
            return

    async def revoke_from_user(self, *, user_id: UUID, role_id: UUID) -> None:
        await self.session.execute(
            delete(UserRole).where(
                and_(UserRole.user_id == user_id, UserRole.role_id == role_id),
            ),
        )

    async def upsert_permission(self, name: str, description: str | None = None) -> Permission:
        existing = (
            await self.session.execute(select(Permission).where(Permission.name == name))
        ).scalar_one_or_none()
        if existing is not None:
            if description and existing.description != description:
                existing.description = description
            return existing
        perm = Permission(name=name, description=description)
        self.session.add(perm)
        await self.session.flush()
        return perm

    async def upsert_role(
        self,
        name: str,
        description: str,
        is_system: bool,
    ) -> Role:
        existing = (
            await self.session.execute(select(Role).where(Role.name == name))
        ).scalar_one_or_none()
        if existing is not None:
            existing.description = description
            existing.is_system = is_system
            return existing
        role = Role(name=name, description=description, is_system=is_system)
        self.session.add(role)
        await self.session.flush()
        return role

    async def set_role_permissions(self, role: Role, permission_ids: list[UUID]) -> None:
        from app.modules.iam.models import RolePermission

        await self.session.execute(
            delete(RolePermission).where(RolePermission.role_id == role.id),
        )
        for pid in permission_ids:
            self.session.add(RolePermission(role_id=role.id, permission_id=pid))
        await self.session.flush()


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: UUID,
        current_refresh_jti: UUID,
        user_agent: str | None,
        ip_address: str | None,
        expires_at: datetime,
    ) -> Session:
        sess = Session(
            user_id=user_id,
            current_refresh_jti=current_refresh_jti,
            user_agent=(user_agent[:512] if user_agent else None),
            ip_address=ip_address,
            expires_at=expires_at,
        )
        self.session.add(sess)
        await self.session.flush()
        return sess

    async def get(self, session_id: UUID) -> Session | None:
        stmt = select(Session).where(Session.id == session_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_active(self, session_id: UUID) -> Session | None:
        stmt = (
            select(Session)
            .where(Session.id == session_id, Session.revoked_at.is_(None))
            .with_for_update()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def rotate_refresh(
        self,
        session_id: UUID,
        *,
        new_jti: UUID,
        previous_jti: UUID,
        last_used_at: datetime,
    ) -> None:
        await self.session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(
                current_refresh_jti=new_jti,
                prev_refresh_jti=previous_jti,
                last_used_at=last_used_at,
            ),
        )

    async def revoke(self, session_id: UUID, *, reason: str) -> None:
        await self.session.execute(
            update(Session)
            .where(Session.id == session_id, Session.revoked_at.is_(None))
            .values(revoked_at=utc_now(), revoked_reason=reason),
        )

    async def revoke_all_for_user(self, user_id: UUID, *, reason: str) -> None:
        await self.session.execute(
            update(Session)
            .where(Session.user_id == user_id, Session.revoked_at.is_(None))
            .values(revoked_at=utc_now(), revoked_reason=reason),
        )

    async def list_for_user(self, user_id: UUID) -> Sequence[Session]:
        stmt = select(Session).where(Session.user_id == user_id).order_by(Session.created_at.desc())
        return (await self.session.execute(stmt)).scalars().all()


class OtpRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: UUID | None,
        email: str | None,
        phone: str | None,
        purpose: OtpPurpose,
        code_hash: str,
        expires_at: datetime,
        max_attempts: int,
    ) -> OtpCode:
        otp = OtpCode(
            user_id=user_id,
            email=email,
            phone=phone,
            purpose=purpose,
            code_hash=code_hash,
            expires_at=expires_at,
            max_attempts=max_attempts,
        )
        self.session.add(otp)
        await self.session.flush()
        return otp

    async def latest_active_for_email(
        self,
        email: str,
        purpose: OtpPurpose,
    ) -> OtpCode | None:
        stmt = (
            select(OtpCode)
            .where(
                OtpCode.email == email,
                OtpCode.purpose == purpose,
                OtpCode.consumed_at.is_(None),
                OtpCode.expires_at > utc_now(),
            )
            .order_by(OtpCode.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def increment_attempts(self, otp_id: UUID) -> int:
        result = await self.session.execute(
            update(OtpCode)
            .where(OtpCode.id == otp_id)
            .values(attempts=OtpCode.attempts + 1)
            .returning(OtpCode.attempts),
        )
        return int(result.scalar_one())

    async def mark_consumed(self, otp_id: UUID) -> None:
        await self.session.execute(
            update(OtpCode).where(OtpCode.id == otp_id).values(consumed_at=utc_now()),
        )

    async def invalidate_active_for_email(
        self,
        email: str,
        purpose: OtpPurpose,
    ) -> None:
        await self.session.execute(
            update(OtpCode)
            .where(
                OtpCode.email == email,
                OtpCode.purpose == purpose,
                OtpCode.consumed_at.is_(None),
            )
            .values(consumed_at=utc_now()),
        )

    async def latest_active_for_phone(
        self,
        phone: str,
        purpose: OtpPurpose,
    ) -> OtpCode | None:
        stmt = (
            select(OtpCode)
            .where(
                OtpCode.phone == phone,
                OtpCode.purpose == purpose,
                OtpCode.consumed_at.is_(None),
                OtpCode.expires_at > utc_now(),
            )
            .order_by(OtpCode.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def invalidate_active_for_phone(
        self,
        phone: str,
        purpose: OtpPurpose,
    ) -> None:
        await self.session.execute(
            update(OtpCode)
            .where(
                OtpCode.phone == phone,
                OtpCode.purpose == purpose,
                OtpCode.consumed_at.is_(None),
            )
            .values(consumed_at=utc_now()),
        )


class PasswordResetTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: UUID,
        token_hash: bytes,
        expires_at: datetime,
        requested_ip: str | None,
    ) -> PasswordResetToken:
        record = PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            requested_ip=requested_ip,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_by_token_hash(self, token_hash: bytes) -> PasswordResetToken | None:
        stmt = (
            select(PasswordResetToken)
            .where(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.consumed_at.is_(None),
                PasswordResetToken.expires_at > utc_now(),
            )
            .with_for_update()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def mark_consumed(self, token_id: UUID) -> None:
        await self.session.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.id == token_id)
            .values(consumed_at=utc_now()),
        )

    async def invalidate_active_for_user(self, user_id: UUID) -> None:
        await self.session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.consumed_at.is_(None),
            )
            .values(consumed_at=utc_now()),
        )


def require_user(user: User | None) -> User:
    if user is None:
        raise NotFoundError("User not found.")
    return user
