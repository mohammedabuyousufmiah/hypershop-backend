from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.config import get_settings
from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthenticatedError,
    ValidationError,
)
from app.core.events.outbox import enqueue_outbox
from app.core.ids import new_id
from app.core.security.authz import authorize
from app.core.security.jwt import (
    decode_refresh_token,
    issue_access_token,
    issue_refresh_token,
)
from app.core.security.passwords import hash_password, needs_rehash, verify_password
from app.core.security.principal import Principal
from app.core.time import utc_in, utc_now
from app.modules.iam import otp as otp_lib
from app.modules.iam import tokens as token_lib
from app.modules.iam.handlers import (
    EVT_OTP_EMAIL_SEND,
    EVT_OTP_SMS_SEND,
    EVT_PASSWORD_CHANGED_EMAIL_SEND,
    EVT_PASSWORD_RESET_EMAIL_SEND,
)
from app.modules.iam.models import (
    OtpPurpose,
    Session,
    User,
    UserStatus,
)
from app.modules.iam.permissions import (
    DEFAULT_ROLE_FOR_NEW_USERS,
    P_ROLE_ASSIGN,
    P_USER_CREATE,
    P_USER_DELETE_ANY,
)
from app.modules.iam.policies import user_policy
from app.modules.iam.repository import (
    OtpRepository,
    PasswordResetTokenRepository,
    RoleRepository,
    SessionRepository,
    UserRepository,
    require_user,
)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Per-request meta carried into the service for audit + session creation."""

    request_id: str | None
    ip_address: str | None
    user_agent: str | None


def _principal_perms_from_roles(user: User) -> tuple[tuple[str, ...], tuple[str, ...]]:
    role_names: list[str] = []
    perms: set[str] = set()
    for role in user.roles:
        role_names.append(role.name)
        for perm in role.permissions:
            perms.add(perm.name)
    return tuple(sorted(role_names)), tuple(sorted(perms))


def _user_response_dict(user: User) -> dict:
    role_names, _ = _principal_perms_from_roles(user)
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "phone": user.phone,
        "status": user.status,
        "email_verified": user.email_verified_at is not None,
        "phone_verified": user.phone_verified_at is not None,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
        "roles": [
            {"name": r.name, "description": r.description}
            for r in user.roles
            if r.name in role_names
        ],
    }


class IamService:
    """All IAM business logic. Every mutating method runs inside the caller's
    ``UnitOfWork.transactional()`` scope; audit + outbox writes commit
    atomically with the state change.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)
        self.sessions = SessionRepository(session)
        self.otp = OtpRepository(session)
        self.resets = PasswordResetTokenRepository(session)

    # ---------------- registration ----------------

    async def register(
        self,
        *,
        email: str,
        password: str,
        full_name: str,
        phone: str | None,
        ctx: RequestContext,
    ) -> User:
        cfg = get_settings()
        normalized_email = email.lower().strip()

        if await self.users.email_exists(normalized_email):
            await record_audit(
                actor=None,
                action="iam.register",
                outcome="failure",
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={
                    "reason": "email_exists",
                    "email_domain": normalized_email.split("@", 1)[-1],
                },
            )
            raise ConflictError("Email already registered.", details={"field": "email"})

        if phone and await self.users.phone_exists(phone):
            raise ConflictError("Phone already registered.", details={"field": "phone"})

        password_hash = hash_password(password)
        # In dev mode (email_provider=log / sms_provider=log) the
        # verification message never actually reaches the user — so
        # registering would leave the account permanently
        # `pending_verify` and login would silently 401. Auto-activate
        # and mark the email verified in dev so the local flow works
        # end-to-end. Production keeps the strict verification gate.
        env = (getattr(cfg, "environment", "dev") or "dev")
        env_str = env.value if hasattr(env, "value") else str(env)
        is_dev = env_str.lower() in ("dev", "development", "local", "test")
        from app.core.time import utc_now  # local import to avoid circular
        initial_status = UserStatus.ACTIVE if is_dev else UserStatus.PENDING_VERIFY
        verified_at = utc_now() if is_dev else None
        user = await self.users.create(
            email=normalized_email,
            full_name=full_name,
            password_hash=password_hash,
            phone=phone,
            status=initial_status,
        )
        # Persist email_verified_at when auto-activating
        if verified_at is not None:
            user.email_verified_at = verified_at
            await self.users.session.flush()

        default_role = await self.roles.get_by_name(DEFAULT_ROLE_FOR_NEW_USERS)
        if default_role is None:
            raise BusinessRuleError(
                "Default role missing. Run `iam-bootstrap` to seed roles.",
                details={"role": DEFAULT_ROLE_FOR_NEW_USERS},
            )
        await self.roles.assign_to_user(
            user_id=user.id,
            role_id=default_role.id,
            assigned_by=None,
        )

        _record, plaintext = await otp_lib.issue(
            self.otp,
            purpose=OtpPurpose.EMAIL_VERIFY,
            user_id=user.id,
            email=user.email,
        )
        await enqueue_outbox(
            type=EVT_OTP_EMAIL_SEND,
            payload={
                "purpose": OtpPurpose.EMAIL_VERIFY.value,
                "email": user.email,
                "code": plaintext,
                "ttl_seconds": cfg.otp_ttl_seconds,
            },
        )

        await record_audit(
            actor=None,
            action="iam.register",
            resource_type="user",
            resource_id=user.id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={"email_domain": user.email.split("@", 1)[-1]},
        )
        return user

    async def verify_email(
        self,
        *,
        email: str,
        code: str,
        ctx: RequestContext,
    ) -> None:
        normalized_email = email.lower().strip()
        try:
            otp = await otp_lib.verify(
                self.otp,
                purpose=OtpPurpose.EMAIL_VERIFY,
                email=normalized_email,
                candidate=code,
            )
        except (ValidationError, BusinessRuleError):
            await record_audit(
                actor=None,
                action="iam.email_verify",
                outcome="failure",
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={"email_domain": normalized_email.split("@", 1)[-1]},
            )
            raise

        user = await self.users.get_by_email(normalized_email)
        if user is None:
            # Active OTP without a user means the row was orphaned by a
            # concurrent delete. Treat as not-found, never as success.
            raise NotFoundError("User not found.")

        await self.otp.mark_consumed(otp.id)
        await self.users.mark_email_verified(user.id)

        await record_audit(
            actor=None,
            action="iam.email_verify",
            resource_type="user",
            resource_id=user.id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

    # ---------------- login ----------------

    async def login(
        self,
        *,
        email: str,
        password: str,
        ctx: RequestContext,
    ) -> tuple[User, str, str, Session]:
        cfg = get_settings()
        normalized_email = email.lower().strip()
        user = await self.users.get_by_email(normalized_email)

        # Use a constant-time fake hash compare when user is missing so the
        # response time profile of "user does not exist" matches "wrong password".
        if user is None:
            verify_password(
                "$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                password,
            )
            await record_audit(
                actor=None,
                action="iam.login",
                outcome="failure",
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={
                    "reason": "unknown_email",
                    "email_domain": normalized_email.split("@", 1)[-1],
                },
            )
            raise UnauthenticatedError("Invalid email or password.")

        if user.locked_until is not None and user.locked_until > utc_now():
            await record_audit(
                actor=None,
                action="iam.login",
                outcome="failure",
                resource_type="user",
                resource_id=user.id,
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={"reason": "locked"},
            )
            raise UnauthenticatedError("Account temporarily locked. Try again later.")

        if not verify_password(user.password_hash, password):
            await self.users.record_login_failure(
                user.id,
                lockout_threshold=cfg.failed_login_lockout_threshold,
                lockout_seconds=cfg.failed_login_lockout_seconds,
            )
            await record_audit(
                actor=None,
                action="iam.login",
                outcome="failure",
                resource_type="user",
                resource_id=user.id,
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={"reason": "bad_password"},
            )
            raise UnauthenticatedError("Invalid email or password.")

        if user.status != UserStatus.ACTIVE:
            await record_audit(
                actor=None,
                action="iam.login",
                outcome="failure",
                resource_type="user",
                resource_id=user.id,
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={"reason": "status_not_active", "status": str(user.status)},
            )
            raise UnauthenticatedError("Account not active.")

        if user.email_verified_at is None:
            raise UnauthenticatedError("Email not verified.")

        if needs_rehash(user.password_hash):
            await self.users.update_password(user.id, hash_password(password))

        roles, perms = _principal_perms_from_roles(user)

        session_id = new_id()
        refresh_jti = new_id()
        session = await self.sessions.create(
            user_id=user.id,
            current_refresh_jti=refresh_jti,
            user_agent=ctx.user_agent,
            ip_address=ctx.ip_address,
            expires_at=utc_in(cfg.jwt_refresh_ttl_seconds),
        )
        # Use the session's actual id (may differ from preallocated if FK enforced)
        session_id = session.id

        access_token, _ = issue_access_token(
            user_id=user.id,
            session_id=session_id,
            roles=roles,
            permissions=perms,
        )
        # Force the refresh JWT's jti to match the session's current_refresh_jti.
        refresh_token, refresh_payload = _issue_refresh_with_jti(
            user_id=user.id,
            session_id=session_id,
            jti=refresh_jti,
        )

        await self.users.record_login_success(user.id)

        await record_audit(
            actor=Principal(
                user_id=user.id,
                session_id=session_id,
                roles=frozenset(roles),
                permissions=frozenset(perms),
            ),
            action="iam.login",
            resource_type="user",
            resource_id=user.id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={"session_id": str(session_id)},
        )
        _ = refresh_payload
        return user, access_token, refresh_token, session

    # ---------------- social login (google / huawei) ----------------

    async def issue_session_for_user(
        self, *, user: User, ctx: RequestContext,
    ) -> tuple[str, str, object]:
        """Mint an access+refresh token pair + session row for an already-
        authenticated user (social login, OTP, etc.). Mirrors the token-mint
        tail of ``login`` without the password check.

        Returns (access_token, refresh_token, session).
        """
        cfg = get_settings()
        roles, perms = _principal_perms_from_roles(user)
        refresh_jti = new_id()
        session = await self.sessions.create(
            user_id=user.id,
            current_refresh_jti=refresh_jti,
            user_agent=ctx.user_agent,
            ip_address=ctx.ip_address,
            expires_at=utc_in(cfg.jwt_refresh_ttl_seconds),
        )
        session_id = session.id
        access_token, _ = issue_access_token(
            user_id=user.id, session_id=session_id, roles=roles, permissions=perms,
        )
        refresh_token, _payload = _issue_refresh_with_jti(
            user_id=user.id, session_id=session_id, jti=refresh_jti,
        )
        await self.users.record_login_success(user.id)
        await record_audit(
            actor=Principal(
                user_id=user.id, session_id=session_id,
                roles=frozenset(roles), permissions=frozenset(perms),
            ),
            action="iam.login.social",
            resource_type="user",
            resource_id=user.id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return access_token, refresh_token, session

    async def get_or_create_social_user(
        self, *, email: str, full_name: str,
    ) -> User:
        """Find a user by the provider-verified email, or create an active,
        email-verified customer (social identities are pre-verified)."""
        normalized = email.lower().strip()
        existing = await self.users.get_by_email(normalized)
        if existing is not None:
            return existing
        from app.core.time import utc_now  # local import (circular guard)
        user = await self.users.create(
            email=normalized,
            full_name=full_name or normalized.split("@", 1)[0],
            password_hash=hash_password(new_id().hex),  # random; social users use no password
            phone=None,
            status=UserStatus.ACTIVE,
        )
        user.email_verified_at = utc_now()
        await self.users.session.flush()
        default_role = await self.roles.get_by_name(DEFAULT_ROLE_FOR_NEW_USERS)
        if default_role is not None:
            await self.roles.assign_to_user(
                user_id=user.id,
                role_id=default_role.id,
                assigned_by=None,
            )
            await self.users.session.flush()
            await self.users.session.refresh(user)
        return user

    # ---------------- sms-otp login ----------------

    async def request_sms_otp(
        self,
        *,
        phone: str,
        ctx: RequestContext,
    ) -> int:
        """Mint + dispatch a login OTP to the given phone.

        Returns the OTP TTL in seconds so the caller can include it in the
        response. Privacy: same return shape regardless of whether the
        phone is registered. If unregistered, the OTP is NOT minted but
        we still spend a constant-time delay so attackers can't enumerate
        accounts via response timing.

        Rate-limit ENFORCEMENT lives at the API layer (per-phone +
        per-IP) — this service trusts it has been checked.
        """
        cfg = get_settings()
        normalized = phone.strip()
        user = await self.users.get_by_phone(normalized)

        if user is None:
            # Constant-time burn so timing matches the success path.
            verify_password(
                "$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                "decoy",
            )
            await record_audit(
                actor=None,
                action="iam.otp.sms.request",
                outcome="failure",
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={
                    "reason": "unknown_phone",
                    "phone_prefix": normalized[:6],
                },
            )
            return cfg.otp_ttl_seconds

        if user.locked_until is not None and user.locked_until > utc_now():
            await record_audit(
                actor=None,
                action="iam.otp.sms.request",
                outcome="failure",
                resource_type="user",
                resource_id=user.id,
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={"reason": "locked"},
            )
            return cfg.otp_ttl_seconds

        if user.status != UserStatus.ACTIVE:
            await record_audit(
                actor=None,
                action="iam.otp.sms.request",
                outcome="failure",
                resource_type="user",
                resource_id=user.id,
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={"reason": "status_not_active", "status": str(user.status)},
            )
            return cfg.otp_ttl_seconds

        _record, plaintext = await otp_lib.issue(
            self.otp,
            purpose=OtpPurpose.LOGIN,
            user_id=user.id,
            email=None,
            phone=normalized,
        )
        await enqueue_outbox(
            type=EVT_OTP_SMS_SEND,
            payload={
                "purpose": OtpPurpose.LOGIN.value,
                "phone": normalized,
                "code": plaintext,
                "ttl_seconds": cfg.otp_ttl_seconds,
            },
        )
        await record_audit(
            actor=None,
            action="iam.otp.sms.request",
            resource_type="user",
            resource_id=user.id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={"phone_prefix": normalized[:6]},
        )
        return cfg.otp_ttl_seconds

    async def verify_sms_otp(
        self,
        *,
        phone: str,
        code: str,
        ctx: RequestContext,
    ) -> tuple[User, str, str, Session]:
        """Verify the SMS OTP and issue a session + token pair.

        Mirrors :meth:`login`'s token issuance so the customer ends up with
        identical session semantics. Marks ``phone_verified_at`` if it
        wasn't already (first successful SMS login also verifies the
        phone).
        """
        cfg = get_settings()
        normalized = phone.strip()
        user = await self.users.get_by_phone(normalized)
        if user is None:
            await record_audit(
                actor=None,
                action="iam.otp.sms.verify",
                outcome="failure",
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={"reason": "unknown_phone", "phone_prefix": normalized[:6]},
            )
            raise UnauthenticatedError("Invalid phone or code.")

        if user.locked_until is not None and user.locked_until > utc_now():
            raise UnauthenticatedError("Account temporarily locked. Try again later.")
        if user.status != UserStatus.ACTIVE:
            raise UnauthenticatedError("Account not active.")

        # Raises ValidationError / BusinessRuleError on bad code / lockout.
        record = await otp_lib.verify_by_phone(
            self.otp, purpose=OtpPurpose.LOGIN, phone=normalized, candidate=code,
        )
        await self.otp.mark_consumed(record.id)
        if user.phone_verified_at is None:
            await self.users.mark_phone_verified(user.id)

        roles, perms = _principal_perms_from_roles(user)

        refresh_jti = new_id()
        session = await self.sessions.create(
            user_id=user.id,
            current_refresh_jti=refresh_jti,
            user_agent=ctx.user_agent,
            ip_address=ctx.ip_address,
            expires_at=utc_in(cfg.jwt_refresh_ttl_seconds),
        )
        session_id = session.id

        access_token, _ = issue_access_token(
            user_id=user.id,
            session_id=session_id,
            roles=roles,
            permissions=perms,
        )
        refresh_token, _ = _issue_refresh_with_jti(
            user_id=user.id,
            session_id=session_id,
            jti=refresh_jti,
        )

        await self.users.record_login_success(user.id)

        await record_audit(
            actor=Principal(
                user_id=user.id,
                session_id=session_id,
                roles=frozenset(roles),
                permissions=frozenset(perms),
            ),
            action="iam.otp.sms.verify",
            resource_type="user",
            resource_id=user.id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={
                "session_id": str(session_id),
                "phone_prefix": normalized[:6],
            },
        )
        return user, access_token, refresh_token, session

    # ---------------- email-otp login (mirror of SMS) ----------------

    async def request_email_otp(
        self,
        *,
        email: str,
        ctx: RequestContext,
    ) -> int:
        """Mint + dispatch a login OTP to the given email. Mirrors
        ``request_sms_otp`` but uses email channel. With log-only email
        transport the code is logged server-side and read out by the dev.
        """
        cfg = get_settings()
        normalized = email.lower().strip()
        user = await self.users.get_by_email(normalized)
        if user is None:
            # Constant-time burn; do not reveal account existence.
            verify_password(
                "$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                "decoy",
            )
            return cfg.otp_ttl_seconds
        if user.locked_until is not None and user.locked_until > utc_now():
            return cfg.otp_ttl_seconds
        if user.status != UserStatus.ACTIVE:
            return cfg.otp_ttl_seconds

        _record, plaintext = await otp_lib.issue(
            self.otp,
            purpose=OtpPurpose.LOGIN,
            user_id=user.id,
            email=normalized,
            phone=None,
        )
        await enqueue_outbox(
            type=EVT_OTP_EMAIL_SEND,
            payload={
                "purpose": OtpPurpose.LOGIN.value,
                "email": normalized,
                "code": plaintext,
                "ttl_seconds": cfg.otp_ttl_seconds,
            },
        )
        return cfg.otp_ttl_seconds

    async def verify_email_otp(
        self,
        *,
        email: str,
        code: str,
        ctx: RequestContext,
    ) -> tuple[User, str, str, Session]:
        """Verify the email OTP and issue a session + token pair.
        Mirrors ``verify_sms_otp``.
        """
        cfg = get_settings()
        normalized = email.lower().strip()
        user = await self.users.get_by_email(normalized)
        if user is None:
            raise UnauthenticatedError("Invalid email or code.")
        if user.locked_until is not None and user.locked_until > utc_now():
            raise UnauthenticatedError("Account temporarily locked. Try again later.")
        if user.status != UserStatus.ACTIVE:
            raise UnauthenticatedError("Account not active.")

        record = await otp_lib.verify(
            self.otp, purpose=OtpPurpose.LOGIN, email=normalized, candidate=code,
        )
        await self.otp.mark_consumed(record.id)
        if user.email_verified_at is None:
            user.email_verified_at = utc_now()
            await self.users.session.flush()

        roles, perms = _principal_perms_from_roles(user)
        refresh_jti = new_id()
        session = await self.sessions.create(
            user_id=user.id,
            current_refresh_jti=refresh_jti,
            user_agent=ctx.user_agent,
            ip_address=ctx.ip_address,
            expires_at=utc_in(cfg.jwt_refresh_ttl_seconds),
        )
        access_token, _ = issue_access_token(
            user_id=user.id,
            session_id=session.id,
            roles=roles,
            permissions=perms,
        )
        refresh_token, _ = _issue_refresh_with_jti(
            user_id=user.id,
            session_id=session.id,
            jti=refresh_jti,
        )
        await self.users.record_login_success(user.id)
        return user, access_token, refresh_token, session

    # ---------------- refresh ----------------

    async def refresh(
        self,
        *,
        refresh_token: str,
        ctx: RequestContext,
    ) -> tuple[User, str, str]:
        cfg = get_settings()
        payload = decode_refresh_token(refresh_token)
        session = await self.sessions.get_active(payload.sid)
        if session is None or session.user_id != payload.sub:
            await record_audit(
                actor=None,
                action="iam.refresh",
                outcome="failure",
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={"reason": "session_revoked_or_missing"},
            )
            raise UnauthenticatedError("Session revoked or expired.")

        if session.expires_at <= utc_now():
            await self.sessions.revoke(session.id, reason="expired")
            raise UnauthenticatedError("Session expired.")

        # Refresh-token reuse detection: matched a previously-rotated jti.
        if session.prev_refresh_jti is not None and payload.jti == session.prev_refresh_jti:
            await self.sessions.revoke(session.id, reason="refresh_reuse_detected")
            await record_audit(
                actor=None,
                action="iam.refresh.reuse_detected",
                outcome="failure",
                resource_type="session",
                resource_id=session.id,
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={"user_id": str(session.user_id)},
            )
            raise UnauthenticatedError("Refresh token reuse detected. Session revoked.")

        if payload.jti != session.current_refresh_jti:
            await self.sessions.revoke(session.id, reason="refresh_jti_mismatch")
            raise UnauthenticatedError("Invalid refresh token.")

        user = await self.users.get_by_id(session.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            await self.sessions.revoke(session.id, reason="user_inactive")
            raise UnauthenticatedError("Account not active.")

        roles, perms = _principal_perms_from_roles(user)
        new_jti = new_id()

        await self.sessions.rotate_refresh(
            session.id,
            new_jti=new_jti,
            previous_jti=session.current_refresh_jti,
            last_used_at=utc_now(),
        )

        access_token, _ = issue_access_token(
            user_id=user.id,
            session_id=session.id,
            roles=roles,
            permissions=perms,
        )
        new_refresh, _ = _issue_refresh_with_jti(
            user_id=user.id,
            session_id=session.id,
            jti=new_jti,
        )

        await record_audit(
            actor=Principal(
                user_id=user.id,
                session_id=session.id,
                roles=frozenset(roles),
                permissions=frozenset(perms),
            ),
            action="iam.refresh",
            resource_type="session",
            resource_id=session.id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        _ = cfg
        return user, access_token, new_refresh

    # ---------------- logout ----------------

    async def logout(self, *, principal: Principal, ctx: RequestContext) -> None:
        await self.sessions.revoke(principal.session_id, reason="logout")
        await record_audit(
            actor=principal,
            action="iam.logout",
            resource_type="session",
            resource_id=principal.session_id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

    async def logout_all(self, *, principal: Principal, ctx: RequestContext) -> None:
        await self.sessions.revoke_all_for_user(principal.user_id, reason="logout_all")
        await record_audit(
            actor=principal,
            action="iam.logout_all",
            resource_type="user",
            resource_id=principal.user_id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

    # ---------------- password ----------------

    async def password_forgot(
        self,
        *,
        email: str,
        ctx: RequestContext,
    ) -> None:
        cfg = get_settings()
        normalized_email = email.lower().strip()
        user = await self.users.get_by_email(normalized_email)

        if user is None:
            await record_audit(
                actor=None,
                action="iam.password_forgot",
                outcome="success",
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={
                    "user_present": False,
                    "email_domain": normalized_email.split("@", 1)[-1],
                },
            )
            return

        await self.resets.invalidate_active_for_user(user.id)
        plaintext, digest = token_lib.new_password_reset_token()
        await self.resets.create(
            user_id=user.id,
            token_hash=digest,
            expires_at=utc_in(cfg.password_reset_ttl_seconds),
            requested_ip=ctx.ip_address,
        )
        await enqueue_outbox(
            type=EVT_PASSWORD_RESET_EMAIL_SEND,
            payload={
                "email": user.email,
                "token": plaintext,
                "ttl_seconds": cfg.password_reset_ttl_seconds,
            },
        )
        await record_audit(
            actor=None,
            action="iam.password_forgot",
            outcome="success",
            resource_type="user",
            resource_id=user.id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={"user_present": True},
        )

    async def password_reset(
        self,
        *,
        token: str,
        new_password: str,
        ctx: RequestContext,
    ) -> None:
        digest = token_lib.hash_password_reset_token(token)
        record = await self.resets.get_by_token_hash(digest)
        if record is None:
            await record_audit(
                actor=None,
                action="iam.password_reset",
                outcome="failure",
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={"reason": "invalid_or_expired_token"},
            )
            raise ValidationError("Invalid or expired reset token.")

        await self.resets.mark_consumed(record.id)
        await self.users.update_password(record.user_id, hash_password(new_password))
        await self.sessions.revoke_all_for_user(record.user_id, reason="password_reset")

        user = await self.users.get_by_id(record.user_id)
        if user is not None:
            await enqueue_outbox(
                type=EVT_PASSWORD_CHANGED_EMAIL_SEND,
                payload={"email": user.email},
            )

        await record_audit(
            actor=None,
            action="iam.password_reset",
            resource_type="user",
            resource_id=record.user_id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

    async def password_change(
        self,
        *,
        principal: Principal,
        current_password: str,
        new_password: str,
        ctx: RequestContext,
    ) -> None:
        user = require_user(await self.users.get_by_id(principal.user_id))
        if not verify_password(user.password_hash, current_password):
            await record_audit(
                actor=principal,
                action="iam.password_change",
                outcome="failure",
                resource_type="user",
                resource_id=user.id,
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={"reason": "wrong_current_password"},
            )
            raise UnauthenticatedError("Current password is incorrect.")

        await self.users.update_password(user.id, hash_password(new_password))
        # Keep the current session, revoke every other session for safety.
        await self.sessions.revoke_all_for_user(user.id, reason="password_change_other_sessions")
        # Re-create the current session via a new row since revoke_all_for_user
        # also revoked it. The user remains logged in via the still-valid access
        # token until it expires; clients should call /auth/refresh which will
        # require a new login.
        await enqueue_outbox(
            type=EVT_PASSWORD_CHANGED_EMAIL_SEND,
            payload={"email": user.email},
        )
        await record_audit(
            actor=principal,
            action="iam.password_change",
            resource_type="user",
            resource_id=user.id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

    # ---------------- profile ----------------

    async def get_self(self, principal: Principal) -> User:
        return require_user(await self.users.get_by_id(principal.user_id))

    async def update_self(
        self,
        *,
        principal: Principal,
        full_name: str | None,
        phone: str | None,
        ctx: RequestContext,
    ) -> User:
        user = require_user(await self.users.get_by_id(principal.user_id))
        authorize(principal, user, "update", user_policy)
        await self.users.update_self_fields(
            principal.user_id,
            full_name=full_name,
            phone=phone,
        )
        await record_audit(
            actor=principal,
            action="iam.user.update_self",
            resource_type="user",
            resource_id=principal.user_id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={
                "fields_changed": [
                    k for k, v in (("full_name", full_name), ("phone", phone)) if v is not None
                ]
            },
        )
        return require_user(await self.users.get_by_id(principal.user_id))

    # ---------------- admin ----------------

    async def admin_get_user(
        self,
        *,
        principal: Principal,
        user_id: UUID,
    ) -> User:
        user = require_user(await self.users.get_by_id(user_id))
        authorize(principal, user, "read", user_policy)
        return user

    async def admin_list_users(
        self,
        *,
        principal: Principal,
        offset: int,
        limit: int,
    ) -> tuple[list[User], int]:
        _ = principal
        rows, total = await self.users.list_paginated(offset=offset, limit=limit)
        return list(rows), total

    async def admin_update_user(
        self,
        *,
        principal: Principal,
        user_id: UUID,
        full_name: str | None,
        phone: str | None,
        status: UserStatus | None,
        ctx: RequestContext,
    ) -> User:
        user = require_user(await self.users.get_by_id(user_id))
        authorize(principal, user, "update", user_policy)
        await self.users.admin_update(
            user_id,
            full_name=full_name,
            phone=phone,
            status=status,
        )
        if status in (UserStatus.SUSPENDED, UserStatus.DELETED):
            await self.sessions.revoke_all_for_user(user_id, reason=f"admin_{status.value}")
        await record_audit(
            actor=principal,
            action="iam.user.admin_update",
            resource_type="user",
            resource_id=user_id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={
                "status": status.value if status else None,
                "fields_changed": [
                    k
                    for k, v in (
                        ("full_name", full_name),
                        ("phone", phone),
                        ("status", status),
                    )
                    if v is not None
                ],
            },
        )
        return require_user(await self.users.get_by_id(user_id))

    async def admin_create_user(
        self,
        *,
        principal: Principal,
        email: str,
        full_name: str,
        password: str,
        role_name: str,
        phone: str | None = None,
        force_password_reset: bool = True,
        ctx: RequestContext,
    ) -> User:
        """Create an internal user and assign exactly one role (RBAC).

        Rules:
        - Requires ``iam.user.create`` (super_admin via ``*`` or system_admin).
        - The ``super_admin`` role may only be granted by a wildcard holder.
        - Email must be unique. Role must exist (run iam-bootstrap).
        - Admin-created accounts are ACTIVE + email-verified (the admin
          vouches), so the new user can log in at their role's door.
        """
        if not principal.has_permission(P_USER_CREATE):
            raise ForbiddenError("You may not create users.")

        normalized_email = email.lower().strip()
        role = await self.roles.get_by_name(role_name)
        if role is None:
            raise NotFoundError(f"Role '{role_name}' does not exist.")

        # Rule: only a wildcard holder (super_admin) may mint another
        # super_admin. Stops a system_admin from self-escalating.
        if role_name == "super_admin" and not principal.has_permission("*"):
            raise ForbiddenError("Only a super admin may grant the super_admin role.")

        if await self.users.email_exists(normalized_email):
            await record_audit(
                actor=principal,
                action="iam.user.create",
                outcome="failure",
                resource_type="user",
                request_id=ctx.request_id,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
                metadata={"reason": "email_exists", "role": role_name},
            )
            raise ConflictError("Email already registered.", details={"field": "email"})

        if phone and await self.users.phone_exists(phone):
            raise ConflictError("Phone already registered.", details={"field": "phone"})

        user = await self.users.create(
            email=normalized_email,
            full_name=full_name,
            password_hash=hash_password(password),
            phone=phone,
            status=UserStatus.ACTIVE,
        )
        user.email_verified_at = utc_now()
        await self.users.session.flush()

        # Brand-new user → assign exactly the one role (no purge needed).
        await self.roles.assign_to_user(
            user_id=user.id,
            role_id=role.id,
            assigned_by=principal.user_id,
        )

        await record_audit(
            actor=principal,
            action="iam.user.create",
            resource_type="user",
            resource_id=user.id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={
                "role": role_name,
                "email_domain": normalized_email.split("@", 1)[-1],
                "force_password_reset": force_password_reset,
            },
        )
        return user

    async def admin_assign_role(
        self,
        *,
        principal: Principal,
        user_id: UUID,
        role_name: str,
        ctx: RequestContext,
    ) -> None:
        if not principal.has_permission(P_ROLE_ASSIGN):
            raise ForbiddenError("You may not assign roles.")
        user = require_user(await self.users.get_by_id(user_id))
        role = await self.roles.get_by_name(role_name)
        if role is None:
            raise NotFoundError(f"Role '{role_name}' does not exist.")
        await self.roles.assign_to_user(
            user_id=user.id,
            role_id=role.id,
            assigned_by=principal.user_id,
        )
        await record_audit(
            actor=principal,
            action="iam.role.assign",
            resource_type="user",
            resource_id=user.id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={"role": role_name},
        )

    async def admin_revoke_role(
        self,
        *,
        principal: Principal,
        user_id: UUID,
        role_name: str,
        ctx: RequestContext,
    ) -> None:
        if not principal.has_permission(P_ROLE_ASSIGN):
            raise ForbiddenError("You may not revoke roles.")
        user = require_user(await self.users.get_by_id(user_id))
        role = await self.roles.get_by_name(role_name)
        if role is None:
            raise NotFoundError(f"Role '{role_name}' does not exist.")
        await self.roles.revoke_from_user(user_id=user.id, role_id=role.id)
        await record_audit(
            actor=principal,
            action="iam.role.revoke",
            resource_type="user",
            resource_id=user.id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={"role": role_name},
        )

    async def admin_delete_user(
        self,
        *,
        principal: Principal,
        user_id: UUID,
        ctx: RequestContext,
    ) -> None:
        if not principal.has_permission(P_USER_DELETE_ANY):
            raise ForbiddenError("You may not delete users.")
        user = require_user(await self.users.get_by_id(user_id))
        await self.users.admin_update(user_id, status=UserStatus.DELETED)
        await self.sessions.revoke_all_for_user(user_id, reason="admin_delete")
        await record_audit(
            actor=principal,
            action="iam.user.delete",
            resource_type="user",
            resource_id=user_id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={"email_domain": user.email.split("@", 1)[-1]},
        )

    async def admin_revoke_session(
        self,
        *,
        principal: Principal,
        session_id: UUID,
        ctx: RequestContext,
    ) -> None:
        sess = await self.sessions.get(session_id)
        if sess is None:
            raise NotFoundError("Session not found.")
        await self.sessions.revoke(session_id, reason="admin_revoke")
        await record_audit(
            actor=principal,
            action="iam.session.revoke",
            resource_type="session",
            resource_id=session_id,
            request_id=ctx.request_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            metadata={"target_user_id": str(sess.user_id)},
        )


def _issue_refresh_with_jti(
    *,
    user_id: UUID,
    session_id: UUID,
    jti: UUID,
) -> tuple[str, object]:
    """Thin wrapper so the service layer doesn't import core.security.jwt twice."""
    token, payload = issue_refresh_token(
        user_id=user_id,
        session_id=session_id,
        jti=jti,
    )
    return token, payload


__all__ = [
    "IamService",
    "RequestContext",
    "_user_response_dict",
]
