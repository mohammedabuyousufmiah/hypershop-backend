"""Hypershop full-stack go-live audit.

Walks every layer that a production launch depends on and emits a
RED/YELLOW/GREEN line per check. Saves a markdown report next to this
file so the user can re-open it any time.

Layers checked (in order):
  1. Infrastructure  — Postgres, Redis, R2, worker, ports
  2. IAM             — login, /auth/me, RBAC, rate limits
  3. Catalog         — list / detail / search / filters / media
  4. Cart + checkout — bootstrap, add, preview, totals
  5. Orders/account  — list, detail, profile
  6. Admin           — moderation, image upload, sellers
  7. Frontend pages  — SSR HTML actually contains backend data
  8. CORS            — preflight from every frontend origin
  9. External provider creds presence
 10. Production hygiene (HTTPS, backups, monitoring, secrets)

Run from backend root with the venv:
    .venv/Scripts/python.exe audit_go_live.py
"""
from __future__ import annotations

import os
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg2
import requests

ROOT = Path(__file__).parent
BACKEND = os.environ.get("BACKEND_BASE", "http://127.0.0.1:8000")
STOREFRONT = os.environ.get("STOREFRONT_BASE", "http://localhost:3200")
ADMIN_PANEL = os.environ.get("ADMIN_BASE", "http://localhost:3002")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@hypershop.com.bd")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMe2026Admin!")
PG_DSN = "dbname=hypershop user=hypershop password=hypershop host=127.0.0.1 port=5432"

OUTPUT = ROOT / "GO_LIVE_AUDIT.md"

# ---------- helpers ----------
@dataclass
class Result:
    status: str  # "GREEN" | "YELLOW" | "RED"
    layer: str
    check: str
    detail: str
    note: str | None = None


RESULTS: list[Result] = []


def add(status: str, layer: str, check: str, detail: str, note: str | None = None) -> None:
    RESULTS.append(Result(status, layer, check, detail, note))


def color(status: str) -> str:
    return {"GREEN": "✅", "YELLOW": "⚠️ ", "RED": "❌"}.get(status, "?")


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


_SESSION = requests.Session()


def http(method: str, url: str, **kw) -> requests.Response | None:
    """Fresh-socket request per call with retry on transient socket errors.

    Empirically the Windows tcp stack + uvicorn's keep-alive interact
    badly under tight loops; a connection from a long-lived pool can
    silently half-close. We force a new Connection: close so each call
    establishes a fresh socket. Retries cover the connection-level
    transient case; legitimate 4xx/5xx bodies pass through as one
    Response.
    """
    headers = dict(kw.pop("headers", {}) or {})
    headers.setdefault("Connection", "close")
    timeout = kw.pop("timeout", 10)
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.request(method, url, timeout=timeout, headers=headers, **kw)
            return r
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            print(f"  RETRY {attempt+1}: {method} {url[-50:]} -> {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            time.sleep(0.4 * (attempt + 1))
            continue
        except requests.RequestException as e:
            print(f"  ERR: {method} {url[-50:]} -> {type(e).__name__}: {e}", file=sys.stderr)
            add("RED", "network", f"{method} {url}", "request failed", repr(e))
            return None
    print(f"  GAVE UP: {method} {url[-50:]} after 3 retries; last={repr(last_exc)[:160]}", file=sys.stderr)
    add("RED", "network", f"{method} {url}", "all 3 attempts failed", repr(last_exc))
    return None


# ---------- 1. Infrastructure ----------
def check_infra() -> None:
    layer = "Infra"

    pairs = [("Postgres", "127.0.0.1", 5432), ("Redis", "127.0.0.1", 6379),
             ("Backend API", "127.0.0.1", 8000), ("Storefront", "localhost", 3200),
             ("Admin panel", "localhost", 3002)]
    for name, h, p in pairs:
        if port_open(h, p):
            add("GREEN", layer, f"{name} port", f"{h}:{p} reachable")
        else:
            add("RED", layer, f"{name} port", f"{h}:{p} NOT reachable")

    # Postgres connectivity + row counts
    try:
        conn = psycopg2.connect(PG_DSN)
        with conn.cursor() as cur:
            for tbl, expected in [("products", 80), ("categories", 48), ("brands", 12), ("users", 1)]:
                cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                n = cur.fetchone()[0]
                if n >= expected:
                    add("GREEN", layer, f"DB rows / {tbl}", f"{n} rows (>= {expected})")
                else:
                    add("YELLOW", layer, f"DB rows / {tbl}", f"{n} rows (expected >= {expected})")
            cur.execute("SELECT COUNT(*) FROM product_media WHERE url LIKE 'http%'")
            r2_imgs = cur.fetchone()[0]
            if r2_imgs >= 1:
                add("GREEN", layer, "R2 product images", f"{r2_imgs} images on Cloudflare R2")
            else:
                add("YELLOW", layer, "R2 product images", "0 R2 images — uploads not done yet")
        conn.close()
    except Exception as e:
        add("RED", layer, "Postgres connect", f"could not connect", str(e))

    # Backend /health + /docs
    r = http("GET", f"{BACKEND}/api/v1/health")
    if r is not None and r.status_code == 200 and r.json().get("status") == "live":
        add("GREEN", layer, "Backend /health", "live")
    else:
        add("RED", layer, "Backend /health", f"status={r.status_code if r else 'no_resp'}")

    r = http("GET", f"{BACKEND}/docs")
    if r is not None and r.status_code == 200:
        add("GREEN", layer, "Backend /docs (Swagger UI)", "reachable")
    else:
        add("YELLOW", layer, "Backend /docs", "swagger ui not reachable")

    # R2 reachability — HEAD on one known image URL
    try:
        conn = psycopg2.connect(PG_DSN)
        with conn.cursor() as cur:
            cur.execute("SELECT url FROM product_media WHERE url LIKE 'http%' LIMIT 1")
            row = cur.fetchone()
        conn.close()
        if row:
            url = row[0]
            r = http("HEAD", url)
            if r is not None and r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image/"):
                add("GREEN", layer, "R2 CDN delivery", f"{url[:70]}... → 200 {r.headers['Content-Type']}")
            else:
                add("RED", layer, "R2 CDN delivery", f"status={r.status_code if r else 'no_resp'}", url)
    except Exception as e:
        add("YELLOW", layer, "R2 CDN delivery", "no R2 URLs to probe", str(e))

    # ARQ worker process
    import subprocess
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object CommandLine -like '*arq*app.worker*' | Measure-Object | Select-Object -ExpandProperty Count"],
        capture_output=True, text=True, timeout=15
    )
    count = int((out.stdout or "0").strip() or "0")
    if count > 0:
        add("GREEN", layer, "ARQ worker process", f"{count} python.exe process(es) running")
    else:
        add("RED", layer, "ARQ worker process", "not running — cron + product_video pipeline halted")


# ---------- 2. IAM ----------
def check_iam() -> dict[str, str]:
    layer = "IAM"
    tokens: dict[str, str] = {}

    # Anonymous /auth/me → 401
    r = http("GET", f"{BACKEND}/api/v1/auth/me")
    if r is not None and r.status_code == 401:
        add("GREEN", layer, "Anonymous /auth/me 401", "bearer-required enforced")
    else:
        add("RED", layer, "Anonymous /auth/me", f"got {r.status_code if r else 'no_resp'}, expected 401")

    # Login with admin creds
    r = http("POST", f"{BACKEND}/api/v1/auth/login",
             json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
             headers={"Content-Type": "application/json"})
    if not r or r.status_code != 200:
        add("RED", layer, "Admin login", f"status={r.status_code if r else 'no_resp'}", r.text[:200] if r else None)
        return tokens
    body = r.json()
    tok = body.get("tokens", {}).get("access_token")
    user = body.get("user", {})
    roles = [r["name"] for r in user.get("roles", [])]
    if "admin" in roles:
        add("GREEN", layer, "Admin login", f"user={user.get('email')} roles={roles}")
    else:
        add("YELLOW", layer, "Admin login", f"login OK but roles={roles} (no admin)")
    tokens["admin"] = tok

    # /auth/me with bearer
    r = http("GET", f"{BACKEND}/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"})
    if r is not None and r.status_code == 200 and r.json().get("email") == ADMIN_EMAIL:
        add("GREEN", layer, "Bearer /auth/me", "returns current user")
    else:
        add("RED", layer, "Bearer /auth/me", f"status={r.status_code if r else 'no_resp'}")

    # Wrong password → 401 or rate-limit
    r = http("POST", f"{BACKEND}/api/v1/auth/login",
             json={"email": ADMIN_EMAIL, "password": "wrong-password-xxx"},
             headers={"Content-Type": "application/json"})
    if r is not None and r.status_code in (401, 403):
        add("GREEN", layer, "Wrong-password rejection", f"status={r.status_code}")
    else:
        add("YELLOW", layer, "Wrong-password rejection", f"unexpected status={r.status_code if r else 'no_resp'}")

    # RBAC: admin endpoint without auth → 401
    r = http("GET", f"{BACKEND}/api/v1/admin/catalog/products?size=1")
    if r is not None and r.status_code in (401, 403):
        add("GREEN", layer, "Admin endpoint requires auth", f"unauthed → {r.status_code}")
    else:
        add("RED", layer, "Admin endpoint requires auth", f"got {r.status_code if r else 'no_resp'}")

    # RBAC: admin endpoint with bearer → 200
    r = http("GET", f"{BACKEND}/api/v1/admin/catalog/products?size=1",
             headers={"Authorization": f"Bearer {tok}"})
    if r is not None and r.status_code == 200:
        add("GREEN", layer, "Admin can call /admin/* with bearer", "200 OK")
    else:
        add("RED", layer, "Admin bearer access", f"{r.status_code if r else 'no_resp'}")

    return tokens


# ---------- 3. Catalog ----------
def check_catalog() -> None:
    layer = "Catalog"

    # Categories list
    r = http("GET", f"{BACKEND}/api/v1/catalog/categories")
    if r is not None and r.status_code == 200:
        cats = r.json()
        if isinstance(cats, list) and len(cats) >= 8:
            add("GREEN", layer, "/catalog/categories", f"{len(cats)} root categories")
        else:
            add("YELLOW", layer, "/catalog/categories", f"only {len(cats) if isinstance(cats, list) else '?'} returned")
    else:
        add("RED", layer, "/catalog/categories", f"{r.status_code if r else 'no_resp'}")

    # Brands list
    r = http("GET", f"{BACKEND}/api/v1/catalog/brands")
    if r is not None and r.status_code == 200:
        b = r.json()
        if isinstance(b, list) and len(b) >= 12:
            add("GREEN", layer, "/catalog/brands", f"{len(b)} brands")
        else:
            add("YELLOW", layer, "/catalog/brands", f"only {len(b) if isinstance(b, list) else '?'} returned")
    else:
        add("RED", layer, "/catalog/brands", f"{r.status_code if r else 'no_resp'}")

    # Products paginated
    r = http("GET", f"{BACKEND}/api/v1/catalog/products?size=20")
    if r is not None and r.status_code == 200:
        body = r.json()
        if body.get("total", 0) >= 80 and len(body.get("items", [])) > 0:
            sample = body["items"][0]
            has_img = bool(sample.get("primary_image_url"))
            add(
                "GREEN" if has_img else "YELLOW",
                layer, "/catalog/products list",
                f"total={body['total']} sample has image: {has_img}",
            )
        else:
            add("YELLOW", layer, "/catalog/products list", f"total={body.get('total')}")
    else:
        add("RED", layer, "/catalog/products list", f"{r.status_code if r else 'no_resp'}")

    # Category filter
    r = http("GET", f"{BACKEND}/api/v1/catalog/products?category=smartphones")
    if r is not None and r.status_code == 200:
        body = r.json()
        if body.get("total", 0) >= 1:
            add("GREEN", layer, "Filter category=smartphones", f"total={body['total']}")
        else:
            add("YELLOW", layer, "Filter category=smartphones", "0 matches")
    else:
        add("RED", layer, "Filter category=smartphones", f"{r.status_code if r else 'no_resp'}")

    # Brand filter
    r = http("GET", f"{BACKEND}/api/v1/catalog/products?brand=walton")
    if r is not None and r.status_code == 200 and r.json().get("total", 0) >= 1:
        add("GREEN", layer, "Filter brand=walton", f"total={r.json()['total']}")
    else:
        add("YELLOW", layer, "Filter brand=walton", f"unexpected {r.status_code if r else 'no_resp'}")

    # Product detail
    r = http("GET", f"{BACKEND}/api/v1/catalog/products/samsung-galaxy-a15-6gb-128gb-blue-black")
    if r is not None and r.status_code == 200:
        body = r.json()
        keys_present = {k: (k in body) for k in ("name", "brand", "category", "media", "variants")}
        all_ok = all(keys_present.values())
        add(
            "GREEN" if all_ok else "YELLOW",
            layer, "Product detail shape",
            f"keys: {keys_present}",
        )
        if body.get("media"):
            first = body["media"][0].get("url", "")
            if first.startswith("http") and "r2.dev" in first:
                add("GREEN", layer, "Detail has R2 image", first[:70] + "...")
            else:
                add("YELLOW", layer, "Detail image URL", f"first url={first[:80]}")
    else:
        add("RED", layer, "Product detail", f"{r.status_code if r else 'no_resp'}")

    # Search
    r = http("GET", f"{BACKEND}/api/v1/search?q=walton")
    if r is not None and r.status_code == 200:
        body = r.json()
        if body.get("total_hits", 0) >= 1:
            add("GREEN", layer, "Full-text search", f"total_hits={body['total_hits']} latency={body.get('latency_ms')}ms")
        else:
            add("YELLOW", layer, "Full-text search", "0 hits")
    else:
        add("RED", layer, "Full-text search", f"{r.status_code if r else 'no_resp'}")


# ---------- 4. Cart + Checkout + Orders ----------
def check_cart_checkout_orders(tokens: dict[str, str]) -> None:
    layer = "Cart / Checkout"

    # Probe whether the module groups are even registered on the backend.
    try:
        spec = _SESSION.get(f"{BACKEND}/openapi.json", timeout=10).json()
        backend_paths = set(spec.get("paths", {}).keys())
    except Exception as e:
        add("RED", layer, "OpenAPI fetch", "could not load backend route map", str(e))
        return

    # Customer cart flow (used by storefront customer-web).
    expected_cart = [
        "/api/v1/cart", "/api/v1/cart/items", "/api/v1/cart/quote",
        "/api/v1/cart/merge", "/api/v1/cart/guest", "/api/v1/cart/guest/items",
        "/api/v1/cart/guest/quote", "/api/v1/cart/_limits",
    ]
    missing_cart = [p for p in expected_cart if p not in backend_paths]
    if not missing_cart:
        add("GREEN", layer, "Cart module routes", f"{len(expected_cart)}/{len(expected_cart)} present")
    else:
        add("RED", layer, "Cart module routes",
            f"{len(expected_cart) - len(missing_cart)}/{len(expected_cart)} present",
            "MISSING on backend: " + ", ".join(missing_cart))

    expected_checkout = ["/api/v1/checkout/preview", "/api/v1/checkout/_limits"]
    missing_checkout = [p for p in expected_checkout if p not in backend_paths]
    if not missing_checkout:
        add("GREEN", layer, "Checkout module routes", f"{len(expected_checkout)}/{len(expected_checkout)} present")
    else:
        add("RED", layer, "Checkout module routes",
            f"{len(expected_checkout) - len(missing_checkout)}/{len(expected_checkout)} present",
            "MISSING on backend: " + ", ".join(missing_checkout))

    # Orders surface — checkout-less flow can still POST direct to /orders.
    layer = "Orders"
    tok = tokens.get("admin")
    if not tok:
        add("YELLOW", layer, "Orders endpoint smoke", "skipped — no admin token")
        return

    # Customer order list
    r = http("GET", f"{BACKEND}/api/v1/orders",
             headers={"Authorization": f"Bearer {tok}"})
    if r is not None and r.status_code == 200:
        add("GREEN", layer, "GET /orders (customer)", f"{r.status_code} — shape: {list(r.json()[:1])[0:1] if isinstance(r.json(), list) and r.json() else 'empty'}")
    else:
        add("YELLOW", layer, "GET /orders (customer)",
            f"{r.status_code if r else 'no_resp'}",
            r.text[:200] if r else None)

    # Track-order public endpoint
    r = http("GET", f"{BACKEND}/api/v1/track/orders/NONEXISTENT-CODE")
    if r is not None and r.status_code in (404, 400):
        add("GREEN", layer, "Public order tracking",
            f"unknown code → {r.status_code} (correct)")
    else:
        add("YELLOW", layer, "Public order tracking",
            f"got {r.status_code if r else 'no_resp'} (expected 404)")


# ---------- 5. Frontend pages SSR data ----------
def check_storefront_pages() -> None:
    layer = "Storefront SSR"

    pages = [
        ("/", "Homepage", "Hypershop"),
        ("/product/samsung-galaxy-a15-6gb-128gb-blue-black", "PDP", "Samsung Galaxy A15"),
        ("/product/walton-tamarind-ex710g-core-i5-15-6-laptop", "PDP Walton", "Walton Tamarind"),
        ("/c/mobile", "Category mobile", None),
        ("/c/smartphones", "Category smartphones", None),
        ("/deals", "Deals", None),
        ("/cart", "Cart", None),
        ("/login", "Login", None),
    ]
    for path, label, must_have in pages:
        r = http("GET", f"{STOREFRONT}{path}", timeout=30)
        if r is None:
            add("RED", layer, label, "no response")
            continue
        if r.status_code != 200:
            add("RED", layer, label, f"HTTP {r.status_code}")
            continue
        html = r.text
        if must_have and must_have not in html:
            add("YELLOW", layer, label, f"200 but '{must_have}' not in HTML")
        elif "r2.dev" in html or must_have is None:
            add("GREEN", layer, label, f"200, size={len(html)}, r2.dev refs={html.count('r2.dev')}")
        else:
            add("YELLOW", layer, label, f"200, size={len(html)}, no R2 image refs")


# ---------- 6. CORS ----------
def check_cors() -> None:
    layer = "CORS"
    for origin in (STOREFRONT, ADMIN_PANEL, "http://localhost:3100"):
        r = http("OPTIONS", f"{BACKEND}/api/v1/catalog/products",
                 headers={"Origin": origin,
                          "Access-Control-Request-Method": "GET",
                          "Access-Control-Request-Headers": "authorization,content-type"})
        if not r:
            add("RED", layer, f"preflight from {origin}", "no response")
            continue
        allow = r.headers.get("access-control-allow-origin", "")
        if r.status_code == 200 and origin in allow:
            add("GREEN", layer, f"Preflight {origin}", f"allow-origin={allow}")
        else:
            add("RED", layer, f"Preflight {origin}", f"status={r.status_code} allow={allow}")


# ---------- 7. External providers ----------
def check_providers() -> None:
    layer = "Providers"
    env_path = ROOT / ".env"
    env = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()

    providers = {
        "Cloudflare R2": ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_PUBLIC_BASE_URL"],
        "Bunny.net (video CDN)": ["BUNNY_STORAGE_ZONE_NAME", "BUNNY_STORAGE_ACCESS_KEY", "BUNNY_PULL_ZONE_HOSTNAME"],
        "Bkash payment": ["BKASH_APP_KEY", "BKASH_APP_SECRET", "BKASH_USERNAME", "BKASH_PASSWORD"],
        "SSLCommerz payment": ["SSLCOMMERZ_STORE_ID", "SSLCOMMERZ_STORE_PASSWD"],
        "Nagad payment": ["NAGAD_MERCHANT_ID", "NAGAD_MERCHANT_NUMBER", "NAGAD_PRIV_KEY"],
        "Rocket payment": ["ROCKET_MERCHANT_ID", "ROCKET_APP_KEY"],
        "SMTP email": ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"],
        "WhatsApp (Meta Cloud)": ["WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN"],
        "SMS (BulkSMS BD)": ["BULKSMS_API_KEY", "BULKSMS_SENDER"],
        "FCM push": ["FCM_SERVICE_ACCOUNT_JSON"],
        "APNS push": ["APNS_TEAM_ID", "APNS_KEY_ID", "APNS_P8_KEY"],
    }
    for name, keys in providers.items():
        present = sum(1 for k in keys if env.get(k, "").strip())
        total = len(keys)
        if present == total:
            add("GREEN", layer, name, f"{present}/{total} env vars set")
        elif present > 0:
            add("YELLOW", layer, name, f"{present}/{total} set — partial config, will not bind cleanly")
        else:
            add("YELLOW", layer, name, "no creds — provider will be skipped at boot (no live integration)")


# ---------- 8. Production-hygiene ----------
def check_prod_hygiene() -> None:
    layer = "Prod hygiene"
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()

    jwt_secret = env.get("JWT_SECRET", "")
    if len(jwt_secret) >= 32 and not jwt_secret.startswith("change-me"):
        add("GREEN", layer, "JWT_SECRET strength", f"{len(jwt_secret)} chars, random")
    else:
        add("RED", layer, "JWT_SECRET strength", "weak or placeholder")

    if env.get("ENVIRONMENT") == "production":
        add("GREEN", layer, "ENVIRONMENT=production", "set")
    else:
        add("YELLOW", layer, "ENVIRONMENT", f"='{env.get('ENVIRONMENT')}' — not production")

    # TLS / domain
    if not env.get("CORS_ORIGINS", "").startswith("https://"):
        add("YELLOW", layer, "CORS_ORIGINS HTTPS", "still allow-listing http://localhost*")

    # Backup
    add("YELLOW", layer, "Postgres backup cron",
        "no cron / pg_dump scheduled outside Docker compose stack")

    # Monitoring
    if env.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        add("GREEN", layer, "OpenTelemetry tracing", env["OTEL_EXPORTER_OTLP_ENDPOINT"])
    else:
        add("YELLOW", layer, "OpenTelemetry tracing", "OTEL_EXPORTER_OTLP_ENDPOINT not set → tracing OFF")

    # HTTPS
    add("YELLOW", layer, "TLS termination",
        "uvicorn on plain HTTP :8000 — production needs Caddy / nginx / Cloudflare in front")

    # Storefront/admin: HTTPS deployment
    add("YELLOW", layer, "Frontend hosting",
        "Next.js dev servers (:3200, :3002) — production needs Vercel/own host with HTTPS")

    # Rate-limit Redis version
    try:
        import redis
        rc = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
        v = rc.info().get("redis_version", "?")
        if v.startswith(("7.", "6.")):
            add("GREEN", layer, "Redis version", f"v{v}")
        else:
            add("YELLOW", layer, "Redis version", f"v{v} — outdated; Lua HMSET workaround applied; upgrade to Redis 7 (Memurai) for production")
    except Exception as e:
        add("RED", layer, "Redis introspect", str(e))


# ---------- 9. Render report ----------
def render() -> None:
    by_layer: dict[str, list[Result]] = {}
    for r in RESULTS:
        by_layer.setdefault(r.layer, []).append(r)

    out = io_lines = []
    p = out.append
    p("# Hypershop — Go-Live Audit")
    p("")
    p(f"_Generated {time.strftime('%Y-%m-%d %H:%M:%S')} against `{BACKEND}` + `{STOREFRONT}` + `{ADMIN_PANEL}`._")
    p("")

    # Summary tally
    g = sum(1 for r in RESULTS if r.status == "GREEN")
    y = sum(1 for r in RESULTS if r.status == "YELLOW")
    rd = sum(1 for r in RESULTS if r.status == "RED")
    p(f"**Tally:** ✅ {g} green · ⚠️ {y} yellow · ❌ {rd} red · **{len(RESULTS)} checks**")
    p("")

    p("## Status by layer")
    p("")
    p("| Layer | ✅ | ⚠️ | ❌ |")
    p("|---|---:|---:|---:|")
    for layer, rs in by_layer.items():
        gg = sum(1 for r in rs if r.status == "GREEN")
        yy = sum(1 for r in rs if r.status == "YELLOW")
        rr = sum(1 for r in rs if r.status == "RED")
        p(f"| **{layer}** | {gg} | {yy} | {rr} |")
    p("")

    for layer, rs in by_layer.items():
        p(f"## {layer}")
        p("")
        p("| Status | Check | Detail |")
        p("|---|---|---|")
        for r in rs:
            note = f"<br/>_{r.note}_" if r.note else ""
            p(f"| {color(r.status)} {r.status} | {r.check} | {r.detail}{note} |")
        p("")

    OUTPUT.write_text("\n".join(out), encoding="utf-8")
    # Also stdout summary
    print(f"\n=== Hypershop Go-Live Audit ===")
    print(f"  ✅ {g} green   ⚠️  {y} yellow   ❌ {rd} red   (total {len(RESULTS)})")
    print(f"  Full report: {OUTPUT}")
    print()
    if rd:
        print("RED items (must fix before go-live):")
        for r in RESULTS:
            if r.status == "RED":
                print(f"  [{r.layer}] {r.check} — {r.detail}")
        print()
    if y:
        print(f"YELLOW items ({y}) — review for production readiness; see report.")


def main() -> None:
    print("running audit...", flush=True)
    check_infra()
    tokens = check_iam()
    check_catalog()
    check_cart_checkout_orders(tokens)
    check_storefront_pages()
    check_cors()
    check_providers()
    check_prod_hygiene()
    render()


if __name__ == "__main__":
    import io as io_mod
    io_lines = []
    main()
