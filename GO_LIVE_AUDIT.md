# Hypershop — Go-Live Audit

_Generated 2026-05-11 05:23:57 against `http://127.0.0.1:8000` + `http://localhost:3200` + `http://localhost:3002`._

**Tally:** ✅ 43 green · ⚠️ 19 yellow · ❌ 0 red · **62 checks**

## Status by layer

| Layer | ✅ | ⚠️ | ❌ |
|---|---:|---:|---:|
| **Infra** | 14 | 0 | 0 |
| **IAM** | 6 | 0 | 0 |
| **Catalog** | 8 | 0 | 0 |
| **Cart / Checkout** | 2 | 0 | 0 |
| **Orders** | 1 | 1 | 0 |
| **Storefront SSR** | 7 | 1 | 0 |
| **CORS** | 3 | 0 | 0 |
| **Providers** | 1 | 10 | 0 |
| **Prod hygiene** | 1 | 7 | 0 |

## Infra

| Status | Check | Detail |
|---|---|---|
| ✅ GREEN | Postgres port | 127.0.0.1:5432 reachable |
| ✅ GREEN | Redis port | 127.0.0.1:6379 reachable |
| ✅ GREEN | Backend API port | 127.0.0.1:8000 reachable |
| ✅ GREEN | Storefront port | localhost:3200 reachable |
| ✅ GREEN | Admin panel port | localhost:3002 reachable |
| ✅ GREEN | DB rows / products | 80 rows (>= 80) |
| ✅ GREEN | DB rows / categories | 48 rows (>= 48) |
| ✅ GREEN | DB rows / brands | 12 rows (>= 12) |
| ✅ GREEN | DB rows / users | 1 rows (>= 1) |
| ✅ GREEN | R2 product images | 80 images on Cloudflare R2 |
| ✅ GREEN | Backend /health | live |
| ✅ GREEN | Backend /docs (Swagger UI) | reachable |
| ✅ GREEN | R2 CDN delivery | https://pub-174156d224644ae589a6b3450c6f81c4.r2.dev/public/products/20... → 200 image/png |
| ✅ GREEN | ARQ worker process | 2 python.exe process(es) running |

## IAM

| Status | Check | Detail |
|---|---|---|
| ✅ GREEN | Anonymous /auth/me 401 | bearer-required enforced |
| ✅ GREEN | Admin login | user=admin@hypershop.com.bd roles=['admin'] |
| ✅ GREEN | Bearer /auth/me | returns current user |
| ✅ GREEN | Wrong-password rejection | status=401 |
| ✅ GREEN | Admin endpoint requires auth | unauthed → 401 |
| ✅ GREEN | Admin can call /admin/* with bearer | 200 OK |

## Catalog

| Status | Check | Detail |
|---|---|---|
| ✅ GREEN | /catalog/categories | 8 root categories |
| ✅ GREEN | /catalog/brands | 12 brands |
| ✅ GREEN | /catalog/products list | total=80 sample has image: True |
| ✅ GREEN | Filter category=smartphones | total=2 |
| ✅ GREEN | Filter brand=walton | total=5 |
| ✅ GREEN | Product detail shape | keys: {'name': True, 'brand': True, 'category': True, 'media': True, 'variants': True} |
| ✅ GREEN | Detail has R2 image | https://pub-174156d224644ae589a6b3450c6f81c4.r2.dev/public/products/20... |
| ✅ GREEN | Full-text search | total_hits=5 latency=21ms |

## Cart / Checkout

| Status | Check | Detail |
|---|---|---|
| ✅ GREEN | Cart module routes | 8/8 present |
| ✅ GREEN | Checkout module routes | 2/2 present |

## Orders

| Status | Check | Detail |
|---|---|---|
| ✅ GREEN | GET /orders (customer) | 200 — shape: empty |
| ⚠️  YELLOW | Public order tracking | got no_resp (expected 404) |

## Storefront SSR

| Status | Check | Detail |
|---|---|---|
| ⚠️  YELLOW | Homepage | 200, size=164949, no R2 image refs |
| ✅ GREEN | PDP | 200, size=44834, r2.dev refs=15 |
| ✅ GREEN | PDP Walton | 200, size=45200, r2.dev refs=15 |
| ✅ GREEN | Category mobile | 200, size=87294, r2.dev refs=0 |
| ✅ GREEN | Category smartphones | 200, size=87467, r2.dev refs=0 |
| ✅ GREEN | Deals | 200, size=79441, r2.dev refs=0 |
| ✅ GREEN | Cart | 200, size=19121, r2.dev refs=0 |
| ✅ GREEN | Login | 200, size=17995, r2.dev refs=0 |

## CORS

| Status | Check | Detail |
|---|---|---|
| ✅ GREEN | Preflight http://localhost:3200 | allow-origin=http://localhost:3200 |
| ✅ GREEN | Preflight http://localhost:3002 | allow-origin=http://localhost:3002 |
| ✅ GREEN | Preflight http://localhost:3100 | allow-origin=http://localhost:3100 |

## Providers

| Status | Check | Detail |
|---|---|---|
| ✅ GREEN | Cloudflare R2 | 5/5 env vars set |
| ⚠️  YELLOW | Bunny.net (video CDN) | no creds — provider will be skipped at boot (no live integration) |
| ⚠️  YELLOW | Bkash payment | no creds — provider will be skipped at boot (no live integration) |
| ⚠️  YELLOW | SSLCommerz payment | no creds — provider will be skipped at boot (no live integration) |
| ⚠️  YELLOW | Nagad payment | no creds — provider will be skipped at boot (no live integration) |
| ⚠️  YELLOW | Rocket payment | no creds — provider will be skipped at boot (no live integration) |
| ⚠️  YELLOW | SMTP email | no creds — provider will be skipped at boot (no live integration) |
| ⚠️  YELLOW | WhatsApp (Meta Cloud) | no creds — provider will be skipped at boot (no live integration) |
| ⚠️  YELLOW | SMS (BulkSMS BD) | no creds — provider will be skipped at boot (no live integration) |
| ⚠️  YELLOW | FCM push | no creds — provider will be skipped at boot (no live integration) |
| ⚠️  YELLOW | APNS push | no creds — provider will be skipped at boot (no live integration) |

## Prod hygiene

| Status | Check | Detail |
|---|---|---|
| ✅ GREEN | JWT_SECRET strength | 64 chars, random |
| ⚠️  YELLOW | ENVIRONMENT | ='dev' — not production |
| ⚠️  YELLOW | CORS_ORIGINS HTTPS | still allow-listing http://localhost* |
| ⚠️  YELLOW | Postgres backup cron | no cron / pg_dump scheduled outside Docker compose stack |
| ⚠️  YELLOW | OpenTelemetry tracing | OTEL_EXPORTER_OTLP_ENDPOINT not set → tracing OFF |
| ⚠️  YELLOW | TLS termination | uvicorn on plain HTTP :8000 — production needs Caddy / nginx / Cloudflare in front |
| ⚠️  YELLOW | Frontend hosting | Next.js dev servers (:3200, :3002) — production needs Vercel/own host with HTTPS |
| ⚠️  YELLOW | Redis version | v3.0.504 — outdated; Lua HMSET workaround applied; upgrade to Redis 7 (Memurai) for production |
