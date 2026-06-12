"""In-process proof of the social-login config gate (no live server / reload race).

Builds the ASGI app twice via get_settings cache-clear: once with NO client IDs
(expect 503 disabled) and once WITH a dummy client ID (expect 401 — past the gate,
real JWKS verification runs and rejects the junk id_token)."""
import asyncio, os
os.environ.setdefault("PYTHONPATH", "/install/lib/python3.12/site-packages:/app")


async def hit() -> int:
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/google", json={"id_token": "junk.token.long.enough.aaaaaaaaaaaa"})
    return r.status_code


async def main() -> int:
    from app.core.config import get_settings

    os.environ.pop("GOOGLE_OAUTH_CLIENT_IDS", None)
    get_settings.cache_clear()
    disabled = await hit()
    print(f"  disabled (no client id)  -> {disabled}  (expect 503)")

    os.environ["GOOGLE_OAUTH_CLIENT_IDS"] = "dummy-test.apps.googleusercontent.com"
    get_settings.cache_clear()
    enabled = await hit()
    print(f"  enabled (dummy client id)-> {enabled}  (expect 401 — gate passed, verify failed)")

    ok = disabled == 503 and enabled == 401
    print("RESULT:", "PASS" if ok else f"FAIL ({disabled},{enabled})")
    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
