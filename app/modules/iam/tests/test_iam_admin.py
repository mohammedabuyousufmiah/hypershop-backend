from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_admin_can_list_users(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    resp = await api_client.get("/api/v1/admin/users", headers=admin_user["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    emails = [u["email"] for u in body["items"]]
    assert admin_user["email"] in emails
    assert logged_in["email"] in emails


async def test_customer_cannot_list_users(
    api_client: AsyncClient,
    logged_in: dict[str, Any],
) -> None:
    resp = await api_client.get("/api/v1/admin/users", headers=logged_in["headers"])
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"


async def test_admin_can_get_user(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    resp = await api_client.get(
        f"/api/v1/admin/users/{logged_in['user']['id']}",
        headers=admin_user["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == logged_in["email"]


async def test_admin_can_update_user(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    resp = await api_client.patch(
        f"/api/v1/admin/users/{logged_in['user']['id']}",
        headers=admin_user["headers"],
        json={"full_name": "Renamed By Admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Renamed By Admin"


async def test_admin_can_suspend_user_and_revoke_sessions(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    suspend = await api_client.patch(
        f"/api/v1/admin/users/{logged_in['user']['id']}",
        headers=admin_user["headers"],
        json={"status": "suspended"},
    )
    assert suspend.status_code == 200

    refresh = await api_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": logged_in["refresh"]},
    )
    assert refresh.status_code == 401


async def test_admin_can_assign_and_revoke_role(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    assign = await api_client.post(
        f"/api/v1/admin/users/{logged_in['user']['id']}/roles",
        headers=admin_user["headers"],
        json={"role": "manager"},
    )
    assert assign.status_code == 204

    fetched = await api_client.get(
        f"/api/v1/admin/users/{logged_in['user']['id']}",
        headers=admin_user["headers"],
    )
    role_names = {r["name"] for r in fetched.json()["roles"]}
    assert "manager" in role_names

    revoke = await api_client.delete(
        f"/api/v1/admin/users/{logged_in['user']['id']}/roles/manager",
        headers=admin_user["headers"],
    )
    assert revoke.status_code == 204

    fetched_again = await api_client.get(
        f"/api/v1/admin/users/{logged_in['user']['id']}",
        headers=admin_user["headers"],
    )
    assert "manager" not in {r["name"] for r in fetched_again.json()["roles"]}


async def test_admin_assign_unknown_role_returns_404(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    resp = await api_client.post(
        f"/api/v1/admin/users/{logged_in['user']['id']}/roles",
        headers=admin_user["headers"],
        json={"role": "no-such-role"},
    )
    assert resp.status_code == 404


async def test_customer_cannot_assign_role(
    api_client: AsyncClient,
    logged_in: dict[str, Any],
) -> None:
    resp = await api_client.post(
        f"/api/v1/admin/users/{logged_in['user']['id']}/roles",
        headers=logged_in["headers"],
        json={"role": "manager"},
    )
    assert resp.status_code == 403


async def test_admin_can_delete_user(
    api_client: AsyncClient,
    admin_user: dict[str, Any],
    logged_in: dict[str, Any],
) -> None:
    resp = await api_client.delete(
        f"/api/v1/admin/users/{logged_in['user']['id']}",
        headers=admin_user["headers"],
    )
    assert resp.status_code == 204

    after = await api_client.get(
        f"/api/v1/admin/users/{logged_in['user']['id']}",
        headers=admin_user["headers"],
    )
    assert after.json()["status"] == "deleted"
