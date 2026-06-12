"""In-process verification of the new /rider/devices/* endpoints.

Runs against the real ASGI app via httpx ASGITransport — no live server,
no --reload race. Uses the external test DB (alembic-migrated).
"""
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

    email = f"rd-{uuid.uuid4().hex[:8]}@hypershop.dev"
    pw = "RiderDevP@ss123"
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        u = User(email=email, full_name="RD Test", password_hash=hash_password(pw),
                 status=UserStatus.ACTIVE, email_verified_at=utc_now())
        s.add(u); await s.flush()
        rid = (await s.execute(text("SELECT id FROM roles WHERE name='customer'"))).scalar_one()
        await s.execute(text("INSERT INTO user_roles (user_id, role_id) VALUES (:u,:r)"), {"u": u.id, "r": rid})

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        lg = await c.post("/api/v1/auth/login", json={"email": email, "password": pw})
        body = lg.json(); payload = body.get("data", body)
        tok = payload["tokens"]["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        rr = await c.post("/api/v1/rider/devices/register", json={"token": "tok-abcdef123456", "provider": "hms"}, headers=h)
        ur = await c.post("/api/v1/rider/devices/unregister", json={"token": "tok-abcdef123456", "provider": "hms"}, headers=h)
        ur2 = await c.post("/api/v1/rider/devices/unregister", json={"token": "never-seen-xyz", "provider": "fcm"}, headers=h)
        lc = await c.post("/api/v1/customers/location/consent", json={"consent_granted": True, "source": "mobile_app"}, headers=h)
        lp = await c.post("/api/v1/customers/location/current", json={"latitude": 23.78, "longitude": 90.41, "accuracy_meters": 12.5, "captured_for": "checkout_address"}, headers=h)
        pg = await c.get("/api/v1/customers/preferences", headers=h)
        pp = await c.patch("/api/v1/customers/preferences", json={"currency": "USD", "email_marketing": True, "preferred_categories": ["electronics", "books"]}, headers=h)
        pg2 = await c.get("/api/v1/customers/preferences", headers=h)
    print(f"login            -> {lg.status_code}")
    print(f"register         -> {rr.status_code}  {rr.text[:120]}")
    print(f"unregister       -> {ur.status_code}")
    print(f"unregister(noop) -> {ur2.status_code}")
    print(f"location/consent -> {lc.status_code}")
    print(f"location/current -> {lp.status_code}")
    print(f"prefs GET(default)-> {pg.status_code}  {pg.text[:140]}")
    print(f"prefs PATCH      -> {pp.status_code}  {pp.text[:140]}")
    print(f"prefs GET(persist)-> {pg2.status_code}  {pg2.text[:140]}")
    codes = [lg.status_code, rr.status_code, ur.status_code, ur2.status_code, lc.status_code, lp.status_code, pg.status_code, pp.status_code, pg2.status_code]
    # also assert PATCH persisted
    import json as _j
    persisted = False
    try:
        d = _j.loads(pg2.text).get("data", {})
        persisted = d.get("currency") == "USD" and d.get("email_marketing") is True and "electronics" in (d.get("preferred_categories") or [])
    except Exception:
        pass
    ok = all(x == 200 for x in codes) and persisted
    print("PERSISTED:", persisted)
    print("RESULT:", "PASS" if ok else f"FAIL {codes}")
    return 0 if ok else 1

raise SystemExit(asyncio.run(main()))
