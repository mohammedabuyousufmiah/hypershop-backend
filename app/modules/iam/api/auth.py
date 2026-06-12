from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status

from app.core.cache import get_redis
from app.core.config import get_settings
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ValidationError
from app.core.ratelimit import RateLimit, RateLimiter
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.modules.iam.api.deps import request_context
from app.modules.iam.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    OtpRequestSmsRequest,
    OtpRequestSmsResponse,
    OtpVerifySmsRequest,
    PasswordChangeRequest,
    PasswordForgotRequest,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    SocialSignInRequest,
    TokenPair,
    UserResponse,
    VerifyEmailRequest,
)
from app.modules.iam.service import IamService, RequestContext, _user_response_dict
from app.modules.iam.social import (
    SocialLoginDisabled,
    SocialLoginInvalid,
    verify_google,
    verify_huawei,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _rate_limiter() -> RateLimiter:
    _ = get_redis()  # ensure redis client is initialized
    return RateLimiter()


# HttpOnly auth cookies. Browser flows rely on these; mobile/RSC flows
# keep using the JSON-body tokens (still returned alongside).
#
# Why HttpOnly: any XSS on the storefront/admin would otherwise be a
# full session takeover. The previous design wrote tokens to JS-readable
# cookies from the LoginClient — defense-in-depth gap. Now the server
# owns cookie issuance, the browser cannot read the token, and an XSS
# at most rides the existing session via fetch() (still bad, but no
# silent token exfiltration).
#
# Secure flag: gated on environment so localhost dev still works over
# http. SameSite=Lax keeps the cookie attached on top-level navigations
# (the post-login redirect needs that) while blocking 3rd-party POSTs
# that lack the X-CSRF-Token header.
def _set_auth_cookies(
    resp: Response,
    access_token: str,
    refresh_token: str,
    cfg,
) -> None:
    secure = cfg.is_production
    resp.set_cookie(
        key="access_token",
        value=access_token,
        max_age=cfg.jwt_access_ttl_seconds,
        path="/",
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    resp.set_cookie(
        key="refresh_token",
        value=refresh_token,
        max_age=cfg.jwt_refresh_ttl_seconds,
        path="/",
        httponly=True,
        secure=secure,
        samesite="lax",
    )


def _clear_auth_cookies(resp: Response) -> None:
    # Match the path used at set time so the browser actually clears.
    for name in ("access_token", "refresh_token"):
        resp.delete_cookie(key=name, path="/")


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    limiter: Annotated[RateLimiter, Depends(_rate_limiter)],
) -> RegisterResponse:
    cfg = get_settings()
    await limiter.check(
        "register",
        ctx.ip_address or "anonymous",
        RateLimit(capacity=cfg.rate_limit_register_per_hour, window_seconds=3600),
    )
    async with uow.transactional() as session:
        service = IamService(session)
        user = await service.register(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            phone=payload.phone,
            ctx=ctx,
        )
    return RegisterResponse(
        user_id=user.id,
        email=user.email,
        status=user.status,
        verification_required=True,
    )


@router.post("/verify-email", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def verify_email(
    payload: VerifyEmailRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    limiter: Annotated[RateLimiter, Depends(_rate_limiter)],
) -> None:
    cfg = get_settings()
    await limiter.check(
        "verify_email",
        payload.email.lower(),
        RateLimit(capacity=cfg.rate_limit_otp_per_hour, window_seconds=3600),
    )
    async with uow.transactional() as session:
        service = IamService(session)
        await service.verify_email(email=payload.email, code=payload.code, ctx=ctx)


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    limiter: Annotated[RateLimiter, Depends(_rate_limiter)],
) -> LoginResponse:
    cfg = get_settings()
    # Rate limit per-IP for unauthenticated traffic. Real production would also
    # limit per-email to slow credential-stuffing across many IPs.
    # Dev bypass: localhost callers (the per-panel /dev-autologin routes
    # in Next.js) burst many login calls during a single browser session
    # while RSC + prefetches fan out. Skipping the limit on loopback only
    # in non-production keeps the dev loop usable without softening prod.
    ip = ctx.ip_address or "anonymous"
    is_loopback_dev = (
        not cfg.is_production
        and ip in ("127.0.0.1", "::1", "localhost", "anonymous")
    )
    if not is_loopback_dev:
        await limiter.check(
            "login_ip",
            ip,
            RateLimit(capacity=cfg.rate_limit_login_per_minute, window_seconds=60),
        )
        await limiter.check(
            "login_email",
            payload.email.lower(),
            RateLimit(capacity=cfg.rate_limit_login_per_minute, window_seconds=60),
        )
    async with uow.transactional() as session:
        service = IamService(session)
        user, access, refresh, _sess = await service.login(
            email=payload.email,
            password=payload.password,
            ctx=ctx,
        )
        # Set HttpOnly cookies for the browser flow. Mobile + RSC keep
        # using the JSON-body tokens.
        _set_auth_cookies(response, access, refresh, cfg)
        return LoginResponse(
            user=UserResponse(**_user_response_dict(user)),
            tokens=TokenPair(
                access_token=access,
                refresh_token=refresh,
                access_expires_in=cfg.jwt_access_ttl_seconds,
                refresh_expires_in=cfg.jwt_refresh_ttl_seconds,
            ),
        )


async def _social_login(
    id_token: str,
    verifier,
    provider: str,
    response: Response,
    uow: UnitOfWork,
    ctx: RequestContext,
) -> LoginResponse:
    cfg = get_settings()
    try:
        identity = verifier(id_token)
    except SocialLoginDisabled as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{provider} sign-in is not configured.",
        ) from e
    except SocialLoginInvalid as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid {provider} id_token.",
        ) from e
    async with uow.transactional() as session:
        service = IamService(session)
        user = await service.get_or_create_social_user(
            email=identity.email, full_name=identity.name,
        )
        access, refresh, _sess = await service.issue_session_for_user(user=user, ctx=ctx)
        _set_auth_cookies(response, access, refresh, cfg)
        return LoginResponse(
            user=UserResponse(**_user_response_dict(user)),
            tokens=TokenPair(
                access_token=access,
                refresh_token=refresh,
                access_expires_in=cfg.jwt_access_ttl_seconds,
                refresh_expires_in=cfg.jwt_refresh_ttl_seconds,
            ),
        )


@router.post("/google", response_model=LoginResponse)
async def google_sign_in(
    payload: SocialSignInRequest,
    response: Response,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
) -> LoginResponse:
    """Verify a Google Sign-In id_token, then issue a Hypershop session.
    503 when GOOGLE_OAUTH_CLIENT_IDS is unset (feature off by default)."""
    return await _social_login(payload.id_token, verify_google, "Google", response, uow, ctx)


@router.post("/huawei", response_model=LoginResponse)
async def huawei_sign_in(
    payload: SocialSignInRequest,
    response: Response,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
) -> LoginResponse:
    """Verify a Huawei Account id_token, then issue a Hypershop session.
    503 when HUAWEI_OAUTH_CLIENT_IDS is unset (feature off by default)."""
    return await _social_login(payload.id_token, verify_huawei, "Huawei", response, uow, ctx)


@router.get("/me", response_model=UserResponse)
async def me(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> UserResponse:
    """Return the currently-authenticated session user.

    The admin panel + storefront both call this on every page mount to
    confirm the access_token is still valid and to read the current
    role set into the AdminAuthGate / customer header. Shape matches
    POST /auth/login's ``user`` field exactly.
    """
    from app.modules.iam.service import _user_response_dict

    async with uow.transactional() as session:
        service = IamService(session)
        user = await service.get_self(principal=principal)
        return UserResponse(**_user_response_dict(user))


@router.post(
    "/otp/request-sms",
    response_model=OtpRequestSmsResponse,
    summary="Request a one-time SMS code for phone-based login",
    description=(
        "Mints a 6-digit OTP and dispatches via the bound SMS provider. "
        "Privacy: response shape is identical regardless of whether the "
        "phone is registered — never reveals account existence. Rate-"
        "limited per-phone (3/h) AND per-IP (10/h)."
    ),
)
async def otp_request_sms(
    payload: OtpRequestSmsRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    limiter: Annotated[RateLimiter, Depends(_rate_limiter)],
) -> OtpRequestSmsResponse:
    cfg = get_settings()
    # Per-phone limit (also covers credential stuffing across IPs against
    # a known number).
    await limiter.check(
        "otp_sms_phone",
        payload.phone,
        RateLimit(capacity=3, window_seconds=3600),
    )
    # Per-IP limit (covers enumeration scans against many phones from
    # one host).
    await limiter.check(
        "otp_sms_ip",
        ctx.ip_address or "anonymous",
        RateLimit(capacity=10, window_seconds=3600),
    )
    async with uow.transactional() as session:
        service = IamService(session)
        ttl = await service.request_sms_otp(phone=payload.phone, ctx=ctx)
    return OtpRequestSmsResponse(sent=True, ttl_seconds=ttl)


@router.post(
    "/otp/verify-sms",
    response_model=LoginResponse,
    summary="Verify an SMS OTP and receive an access + refresh token pair",
    description=(
        "Same response shape as /auth/login. First successful SMS verify "
        "also marks the phone verified on the user record. Wrong codes "
        "burn an attempt; exceeding ``OTP_MAX_ATTEMPTS`` requires a "
        "fresh request-sms call."
    ),
)
async def otp_verify_sms(
    payload: OtpVerifySmsRequest,
    response: Response,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    limiter: Annotated[RateLimiter, Depends(_rate_limiter)],
) -> LoginResponse:
    cfg = get_settings()
    await limiter.check(
        "otp_sms_verify_ip",
        ctx.ip_address or "anonymous",
        RateLimit(capacity=cfg.rate_limit_login_per_minute, window_seconds=60),
    )
    await limiter.check(
        "otp_sms_verify_phone",
        payload.phone,
        RateLimit(capacity=cfg.rate_limit_login_per_minute, window_seconds=60),
    )
    async with uow.transactional() as session:
        service = IamService(session)
        user, access, refresh, _sess = await service.verify_sms_otp(
            phone=payload.phone, code=payload.code, ctx=ctx,
        )
        # Match /auth/login: HttpOnly cookies for browser flow, JSON
        # tokens still returned for mobile.
        _set_auth_cookies(response, access, refresh, cfg)
        return LoginResponse(
            user=UserResponse(**_user_response_dict(user)),
            tokens=TokenPair(
                access_token=access,
                refresh_token=refresh,
                access_expires_in=cfg.jwt_access_ttl_seconds,
                refresh_expires_in=cfg.jwt_refresh_ttl_seconds,
            ),
        )


@router.post(
    "/otp/start",
    summary="Generic OTP start (mobile alias) — dispatches by channel",
    description=(
        "Mobile customer-app entry point. Channel decides dispatch: "
        "``sms`` and ``whatsapp`` route to the SMS provider (identifier "
        "must be a phone); ``email`` routes to the email provider "
        "(identifier must be an email). With OTP_DEV_BYPASS=true the "
        "actual transport is log-only and any non-empty code verifies."
    ),
)
async def otp_start_generic(
    payload: dict,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    limiter: Annotated[RateLimiter, Depends(_rate_limiter)],
) -> dict:
    identifier = str(payload.get("identifier", "")).strip()
    channel = str(payload.get("channel", "sms")).lower()
    if not identifier:
        raise ValidationError("identifier is required.")
    cfg = get_settings()
    await limiter.check(
        f"otp_start_{channel}",
        ctx.ip_address or "anonymous",
        RateLimit(capacity=10, window_seconds=3600),
    )
    async with uow.transactional() as session:
        service = IamService(session)
        if channel == "email":
            ttl = await service.request_email_otp(email=identifier, ctx=ctx)
        else:
            # sms + whatsapp both ride the SMS provider for now (WA adapter creds-pending).
            ttl = await service.request_sms_otp(phone=identifier, ctx=ctx)
    return {"sent": True, "channel": channel, "ttl_seconds": ttl}


@router.post(
    "/otp/confirm",
    summary="Generic OTP confirm (mobile alias) — verifies by channel",
)
async def otp_confirm_generic(
    payload: dict,
    response: Response,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    limiter: Annotated[RateLimiter, Depends(_rate_limiter)],
) -> LoginResponse:
    identifier = str(payload.get("identifier", "")).strip()
    channel = str(payload.get("channel", "sms")).lower()
    code = str(payload.get("code", "")).strip()
    if not identifier or not code:
        raise ValidationError("identifier and code are required.")
    cfg = get_settings()
    await limiter.check(
        f"otp_confirm_{channel}",
        ctx.ip_address or "anonymous",
        RateLimit(capacity=cfg.rate_limit_login_per_minute, window_seconds=60),
    )
    async with uow.transactional() as session:
        service = IamService(session)
        if channel == "email":
            user, access, refresh, _sess = await service.verify_email_otp(
                email=identifier, code=code, ctx=ctx,
            )
        else:
            user, access, refresh, _sess = await service.verify_sms_otp(
                phone=identifier, code=code, ctx=ctx,
            )
        _set_auth_cookies(response, access, refresh, cfg)
        return LoginResponse(
            user=UserResponse(**_user_response_dict(user)),
            tokens=TokenPair(
                access_token=access,
                refresh_token=refresh,
                access_expires_in=cfg.jwt_access_ttl_seconds,
                refresh_expires_in=cfg.jwt_refresh_ttl_seconds,
            ),
        )


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    response: Response,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    payload: RefreshRequest | None = None,
    refresh_cookie: Annotated[str | None, Cookie(alias="refresh_token")] = None,
) -> TokenPair:
    cfg = get_settings()
    # Body-supplied token wins (mobile clients send JSON). Browser flow
    # falls back to the HttpOnly refresh_token cookie.
    token = (payload.refresh_token if payload and payload.refresh_token else refresh_cookie)
    if not token:
        raise ValidationError("Missing refresh token (cookie or body required).")
    async with uow.transactional() as session:
        service = IamService(session)
        _user, access, new_refresh = await service.refresh(
            refresh_token=token,
            ctx=ctx,
        )
        # Always rotate the cookies on refresh — keeps the browser
        # session alive without a re-login round-trip.
        _set_auth_cookies(response, access, new_refresh, cfg)
        return TokenPair(
            access_token=access,
            refresh_token=new_refresh,
            access_expires_in=cfg.jwt_access_ttl_seconds,
            refresh_expires_in=cfg.jwt_refresh_ttl_seconds,
        )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def logout(
    response: Response,
    _payload: LogoutRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        service = IamService(session)
        await service.logout(principal=principal, ctx=ctx)
    _clear_auth_cookies(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def logout_all(
    response: Response,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        service = IamService(session)
        await service.logout_all(principal=principal, ctx=ctx)
    _clear_auth_cookies(response)


@router.post("/password/forgot", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def password_forgot(
    payload: PasswordForgotRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    limiter: Annotated[RateLimiter, Depends(_rate_limiter)],
) -> None:
    await limiter.check(
        "password_forgot",
        payload.email.lower(),
        RateLimit(capacity=3, window_seconds=3600),
    )
    async with uow.transactional() as session:
        service = IamService(session)
        await service.password_forgot(email=payload.email, ctx=ctx)


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def password_reset(
    payload: PasswordResetRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
) -> None:
    if payload.token == payload.new_password:
        # Trivial defence — most clients won't submit this; still cheap to check.
        raise ValidationError("Reset token and new password must differ.")
    async with uow.transactional() as session:
        service = IamService(session)
        await service.password_reset(
            token=payload.token,
            new_password=payload.new_password,
            ctx=ctx,
        )


@router.post("/password/change", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def password_change(
    payload: PasswordChangeRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    if payload.current_password == payload.new_password:
        raise ValidationError("New password must differ from current password.")
    async with uow.transactional() as session:
        service = IamService(session)
        await service.password_change(
            principal=principal,
            current_password=payload.current_password,
            new_password=payload.new_password,
            ctx=ctx,
        )
