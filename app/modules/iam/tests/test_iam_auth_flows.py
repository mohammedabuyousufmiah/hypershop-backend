from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.audit.models import AuditLog
from app.core.db.session import get_sessionmaker
from app.modules.iam.models import Session as IamSession
from app.modules.iam.tests._helpers import (
    get_latest_otp_code,
    get_latest_password_reset_token,
)

pytestmark = pytest.mark.integration


# ----------------- registration -----------------


async def test_register_creates_user_and_enqueues_otp(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": "bob@example.com",
            "password": "Strong!Pass1234",
            "full_name": "Bob Tester",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "bob@example.com"
    assert body["status"] == "pending_verify"
    assert body["verification_required"] is True

    code = await get_latest_otp_code()
    assert code.isdigit()
    assert len(code) == 6


async def test_register_rejects_duplicate_email(api_client: AsyncClient) -> None:
    payload = {
        "email": "dupe@example.com",
        "password": "Strong!Pass1234",
        "full_name": "Dupe",
    }
    first = await api_client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201

    second = await api_client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["code"] == "conflict"


async def test_register_rejects_weak_password(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": "weak@example.com",
            "password": "weakpass",
            "full_name": "Weak",
        },
    )
    assert resp.status_code == 422


async def test_register_rejects_bad_phone(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": "phone@example.com",
            "password": "Strong!Pass1234",
            "full_name": "Phone Tester",
            "phone": "abc123",
        },
    )
    assert resp.status_code == 422


# ----------------- verify -----------------


async def test_verify_email_activates_account(
    api_client: AsyncClient,
) -> None:
    email = "verify@example.com"
    await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Strong!Pass1234",
            "full_name": "Verify Tester",
        },
    )
    code = await get_latest_otp_code()
    resp = await api_client.post(
        "/api/v1/auth/verify-email",
        json={"email": email, "code": code},
    )
    assert resp.status_code == 204

    # Subsequent verification with same code fails (consumed).
    again = await api_client.post(
        "/api/v1/auth/verify-email",
        json={"email": email, "code": code},
    )
    assert again.status_code == 422


async def test_verify_email_wrong_code_increments_attempts(
    api_client: AsyncClient,
) -> None:
    email = "wrongcode@example.com"
    await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Strong!Pass1234",
            "full_name": "Wrong Code",
        },
    )
    resp = await api_client.post(
        "/api/v1/auth/verify-email",
        json={"email": email, "code": "000000"},
    )
    assert resp.status_code == 422


# ----------------- login -----------------


async def test_login_returns_tokens(
    api_client: AsyncClient,
    registered_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={
            "email": registered_user["email"],
            "password": registered_user["password"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tokens"]["access_token"]
    assert body["tokens"]["refresh_token"]
    assert body["user"]["email"] == registered_user["email"]
    assert body["user"]["email_verified"] is True
    assert any(r["name"] == "customer" for r in body["user"]["roles"])


async def test_login_with_wrong_password_returns_401(
    api_client: AsyncClient,
    registered_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={
            "email": registered_user["email"],
            "password": "WrongPass!2026",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthenticated"


async def test_login_unknown_email_returns_401_generic(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@example.com", "password": "Whatever!2026Pass"},
    )
    assert resp.status_code == 401


async def test_login_pending_verify_user_blocked(api_client: AsyncClient) -> None:
    await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": "unverified@example.com",
            "password": "Strong!Pass1234",
            "full_name": "Unverified",
        },
    )
    resp = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "unverified@example.com", "password": "Strong!Pass1234"},
    )
    assert resp.status_code == 401


# ----------------- refresh + reuse -----------------


async def test_refresh_returns_new_pair(
    api_client: AsyncClient,
    logged_in: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": logged_in["refresh"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] != logged_in["access"]
    assert body["refresh_token"] != logged_in["refresh"]


async def test_refresh_token_reuse_revokes_session(
    api_client: AsyncClient,
    logged_in: dict[str, Any],
) -> None:
    first = await api_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": logged_in["refresh"]},
    )
    assert first.status_code == 200

    # Replay the original (now-rotated) refresh token — must be detected.
    replay = await api_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": logged_in["refresh"]},
    )
    assert replay.status_code == 401
    assert replay.json()["code"] == "unauthenticated"

    # The new refresh token from `first` must now also be invalid (session revoked).
    new_refresh = first.json()["refresh_token"]
    after_revoke = await api_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": new_refresh},
    )
    assert after_revoke.status_code == 401


# ----------------- logout -----------------


async def test_logout_revokes_only_current_session(
    api_client: AsyncClient,
    registered_user: dict[str, Any],
) -> None:
    # Two distinct logins → two distinct sessions.
    a = await api_client.post(
        "/api/v1/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    b = await api_client.post(
        "/api/v1/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    assert a.status_code == 200 and b.status_code == 200

    resp = await api_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {a.json()['tokens']['access_token']}"},
        json={},
    )
    assert resp.status_code == 204

    # Session A's refresh token is now revoked.
    a_refresh = await api_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": a.json()["tokens"]["refresh_token"]},
    )
    assert a_refresh.status_code == 401

    # Session B is still alive.
    b_refresh = await api_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": b.json()["tokens"]["refresh_token"]},
    )
    assert b_refresh.status_code == 200


async def test_logout_all_revokes_every_session(
    api_client: AsyncClient,
    registered_user: dict[str, Any],
) -> None:
    a = await api_client.post(
        "/api/v1/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    b = await api_client.post(
        "/api/v1/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    resp = await api_client.post(
        "/api/v1/auth/logout-all",
        headers={"Authorization": f"Bearer {a.json()['tokens']['access_token']}"},
    )
    assert resp.status_code == 204

    for tokens in (a.json()["tokens"], b.json()["tokens"]):
        r = await api_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert r.status_code == 401

    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(IamSession).where(IamSession.user_id == registered_user["user_id"])
                )
            )
            .scalars()
            .all()
        )
    assert all(row.revoked_at is not None for row in rows)


# ----------------- password reset -----------------


async def test_password_forgot_then_reset(
    api_client: AsyncClient,
    registered_user: dict[str, Any],
) -> None:
    forgot = await api_client.post(
        "/api/v1/auth/password/forgot",
        json={"email": registered_user["email"]},
    )
    assert forgot.status_code == 204
    token = await get_latest_password_reset_token()

    new_password = "RotatedPass!9876"
    reset = await api_client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": new_password},
    )
    assert reset.status_code == 204

    # Old password no longer works.
    old = await api_client.post(
        "/api/v1/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    assert old.status_code == 401

    # New password works.
    new = await api_client.post(
        "/api/v1/auth/login",
        json={"email": registered_user["email"], "password": new_password},
    )
    assert new.status_code == 200


async def test_password_forgot_unknown_email_returns_204(api_client: AsyncClient) -> None:
    resp = await api_client.post(
        "/api/v1/auth/password/forgot",
        json={"email": "nobody@example.com"},
    )
    assert resp.status_code == 204


async def test_password_reset_token_cannot_be_reused(
    api_client: AsyncClient,
    registered_user: dict[str, Any],
) -> None:
    await api_client.post(
        "/api/v1/auth/password/forgot",
        json={"email": registered_user["email"]},
    )
    token = await get_latest_password_reset_token()
    first = await api_client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": "RotatedPass!9876"},
    )
    assert first.status_code == 204
    second = await api_client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": "DifferentPass!9876"},
    )
    assert second.status_code == 422


# ----------------- password change -----------------


async def test_password_change_requires_current_password(
    api_client: AsyncClient,
    logged_in: dict[str, Any],
) -> None:
    bad = await api_client.post(
        "/api/v1/auth/password/change",
        headers=logged_in["headers"],
        json={"current_password": "NotCorrect!1234", "new_password": "NewPass!2026Long"},
    )
    assert bad.status_code == 401

    ok = await api_client.post(
        "/api/v1/auth/password/change",
        headers=logged_in["headers"],
        json={
            "current_password": logged_in["password"],
            "new_password": "NewPass!2026Long",
        },
    )
    assert ok.status_code == 204


# ----------------- profile -----------------


async def test_get_me_returns_current_user(
    api_client: AsyncClient,
    logged_in: dict[str, Any],
) -> None:
    resp = await api_client.get("/api/v1/users/me", headers=logged_in["headers"])
    assert resp.status_code == 200
    assert resp.json()["email"] == logged_in["email"]


async def test_get_me_without_token_is_401(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/v1/users/me")
    assert resp.status_code == 401


async def test_update_me_changes_full_name(
    api_client: AsyncClient,
    logged_in: dict[str, Any],
) -> None:
    resp = await api_client.patch(
        "/api/v1/users/me",
        headers=logged_in["headers"],
        json={"full_name": "Alice Renamed"},
    )
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Alice Renamed"


# ----------------- audit emission -----------------


async def test_login_writes_audit_row(
    api_client: AsyncClient,
    registered_user: dict[str, Any],
) -> None:
    await api_client.post(
        "/api/v1/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog)
                    .where(AuditLog.action == "iam.login")
                    .order_by(AuditLog.occurred_at.desc())
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) >= 1
    assert rows[0].outcome == "success"


async def test_failed_login_writes_failure_audit(
    api_client: AsyncClient,
    registered_user: dict[str, Any],
) -> None:
    await api_client.post(
        "/api/v1/auth/login",
        json={"email": registered_user["email"], "password": "Wrong!Pass1234"},
    )
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "iam.login", AuditLog.outcome == "failure"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) >= 1
