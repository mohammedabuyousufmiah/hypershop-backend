"""In-process verify for mobile_auth (PIN/biometric/devices/reauth) + social-login gating."""
import asyncio, os, uuid
os.environ.setdefault("PYTHONPATH", "/install/lib/python3.12/site-packages:/app")


async def main() -> int:
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app
    from app.core.db.session import get_sessionmaker
    from app.core.security.passwords import hash_password
    from app.core.time import utc_now
    from app.modules.iam.models import User, UserStatus
    from sqlalchemy import text

    email = f"ma-{uuid.uuid4().hex[:8]}@hypershop.dev"
    pw = "MobileAuthP@ss1"
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        u = User(email=email, full_name="MA Test", password_hash=hash_password(pw),
                 status=UserStatus.ACTIVE, email_verified_at=utc_now())
        s.add(u); await s.flush()
        rid = (await s.execute(text("SELECT id FROM roles WHERE name='customer'"))).scalar_one()
        await s.execute(text("INSERT INTO user_roles (user_id, role_id) VALUES (:u,:r)"), {"u": u.id, "r": rid})
        uid = str(u.id)

    app = create_app()
    results = {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        lg = await c.post("/api/v1/auth/login", json={"email": email, "password": pw})
        payload = lg.json().get("data", {})
        tok = payload["tokens"]["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        dev = "device-123"
        body = lambda extra: {"user_id": uid, "device_id": dev, "app_type": "rider_app", **extra}

        results["login"] = lg.status_code
        results["pin/setup"] = (await c.post("/api/v1/auth/pin/setup", json=body({"pin": "1234", "platform": "android"}), headers=h)).status_code
        r_ok = await c.post("/api/v1/auth/pin/verify", json=body({"pin": "1234"}), headers=h)
        results["pin/verify(correct)"] = (r_ok.status_code, r_ok.json().get("data", {}).get("outcome"))
        r_bad = await c.post("/api/v1/auth/pin/verify", json=body({"pin": "9999"}), headers=h)
        results["pin/verify(wrong)"] = (r_bad.status_code, r_bad.json().get("data", {}).get("outcome"), r_bad.json().get("data", {}).get("remaining_attempts"))
        results["biometric/enable"] = (await c.post("/api/v1/auth/biometric/enable", json=body({"pin": "1234", "platform": "android"}), headers=h)).status_code
        r_unlock = await c.post("/api/v1/auth/biometric/unlock", json=body({}), headers=h)
        results["biometric/unlock"] = (r_unlock.status_code, r_unlock.json().get("data", {}).get("ok"))
        r_reauth = await c.post("/api/v1/auth/reauth/check", json=body({"action_code": "withdraw"}), headers=h)
        results["reauth/check(fresh)"] = (r_reauth.status_code, r_reauth.json().get("data", {}).get("needs_reauth"))
        r_dev = await c.get("/api/v1/auth/devices", headers=h)
        results["devices"] = (r_dev.status_code, len(r_dev.json().get("data", [])))
        results["biometric/disable"] = (await c.post("/api/v1/auth/biometric/disable", json=body({}), headers=h)).status_code
        results["logout-device"] = (await c.post("/api/v1/auth/logout-device", json=body({}), headers=h)).status_code
        # cross-user guard: wrong user_id must 403
        r_guard = await c.post("/api/v1/auth/pin/verify", json={"user_id": str(uuid.uuid4()), "device_id": dev, "app_type": "rider_app", "pin": "1234"}, headers=h)
        results["pin/verify(other-user)"] = r_guard.status_code  # expect 403
        # social login disabled by default -> 503
        results["auth/google(disabled)"] = (await c.post("/api/v1/auth/google", json={"id_token": "x" * 40})).status_code
        results["auth/huawei(disabled)"] = (await c.post("/api/v1/auth/huawei", json={"id_token": "x" * 40})).status_code

    for k, v in results.items():
        print(f"  {k:28} -> {v}")
    ok = (
        results["login"] == 200
        and results["pin/setup"] == 200
        and results["pin/verify(correct)"] == (200, "success")
        and results["pin/verify(wrong)"][0] == 200 and results["pin/verify(wrong)"][1] == "wrong_pin"
        and results["biometric/enable"] == 200
        and results["biometric/unlock"] == (200, True)
        and results["reauth/check(fresh)"] == (200, False)
        and results["devices"][0] == 200 and results["devices"][1] >= 1
        and results["biometric/disable"] == 200
        and results["logout-device"] == 200
        and results["pin/verify(other-user)"] == 403
        and results["auth/google(disabled)"] == 503
        and results["auth/huawei(disabled)"] == 503
    )
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
