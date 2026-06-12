# Hypershop — Single-shot build, run, test

Canonical commands for spinning up the entire Hypershop FastAPI backend
end-to-end. Run from this directory (`hypershop_fastapi_backend/`).

System has 19 modules / 19 migrations / 17 routers + health, all wired.
External provider chains (AI, formulary) default to `none` → 502 with a
clear "missing setting" message until you supply credentials.

---

## A. Bring the whole stack up with Docker (recommended)

```bash
cp .env.example .env                    # then edit JWT_SECRET to 32+ chars
make compose-up                         # builds images, starts: postgres + redis + mailpit + api + worker
docker compose exec api alembic upgrade head
curl http://localhost:8000/api/v1/health
```

- API:       http://localhost:8000
- API docs:  http://localhost:8000/docs (dev only)
- Mailpit:   http://localhost:8025

To shut down and wipe volumes:

```bash
make compose-down
```

---

## B. Run locally without Docker (Windows-friendly)

Requires Postgres + Redis already running locally on default ports.

```bash
pip install -e ".[dev]"
# Set DATABASE_URL, DATABASE_SYNC_URL, REDIS_URL, JWT_SECRET in your shell
make migrate
make run            # uvicorn with --reload
# in another shell:
make worker         # arq cron worker
```

---

## C. Tests

Two modes — pick one:

```bash
# Docker (matches CI; spins up Postgres + Redis via testcontainers)
make test-int

# OR no Docker — uses your local Postgres + Redis
PG_USER=postgres PG_PASSWORD=<yourpw> bash scripts/run_tests_local.sh
# OR
.\scripts\run_tests_local.ps1 -DbUser postgres -DbPassword <yourpw>
```

The full E2E walk (Admin → stock → customer order → prescription →
approval → packing → delivery → finance → doctor wallet) lives at
`tests/e2e/test_full_pipeline.py` — run that as the smoke check after
`make migrate` succeeds.

---

## D. Enable AI / Formulary providers (optional)

Defaults to `none` for both — system runs fine, AI/formulary endpoints
return 502 with a "missing setting" message naming the unset key.

To enable, set in `.env` (see `.env.example` for the full block):

```bash
# AI — primary + comma-separated failover chain
AI_PROVIDER=openai
AI_BACKUP_PROVIDERS=anthropic,gemini
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...

# Drug data
FORMULARY_PROVIDER=bnf            # or bd_formulary
FORMULARY_API_KEY=...
FORMULARY_BASE_URL=https://...    # provider-specific
```

Restart the api container; lifespan re-binds providers from env. No code
changes required.

---

## E. Quality gates (CI mirror)

```bash
make lint type audit            # ruff + mypy strict + bandit + pip-audit
make test-int                   # full suite incl. integration
```

Coverage floor 80% (enforced by `pytest-cov`).

---

## F. Migrations

```bash
make revision m="add foo table"
make migrate
```

Alembic uses `DATABASE_SYNC_URL` (psycopg2). Runtime uses `DATABASE_URL`
(asyncpg). Same DB, different driver.

---

## G. Production notes

- `.env` is **dev-only**. In production, env vars come from the runtime
  (k8s Secret, ECS task def, systemd EnvironmentFile).
- Required env: `JWT_SECRET` (≥32 chars), `DATABASE_URL`, `DATABASE_SYNC_URL`,
  `REDIS_URL`, `SMTP_*`. App refuses to start if any are missing.
- API docs (`/docs`, `/redoc`, `/openapi.json`) are disabled when
  `ENVIRONMENT=production`.
- CORS is closed by default — populate `CORS_ORIGINS` with comma-separated
  origins to allow specific frontends.
- Provider chains: missing AI/formulary credentials never break startup —
  they just route to `NotConfiguredProvider` which returns 502 on the
  relevant endpoints.

---

## H. Run on a Linux host without owning one — GitHub Codespaces

A `.devcontainer/` is committed. When you open the repo in Codespaces,
you get a Linux VM with Python 3.12, Docker-in-Docker, make and all
dev tools pre-installed. `make prod-up` and `make test-int` work
identically to a real Linux server.

### H.1 — Open in Codespaces (one-time, ~3 min for first boot)

1. Push this repo to GitHub (private repo is free, doesn't have to be
   public).
2. Repo page → **Code** → **Codespaces** → **Create codespace on main**.
   Default machine works; for `make test-int` prefer **4-core / 16 GB**.
3. Wait for the post-create script (~2 min): installs deps, pre-pulls
   the postgres+redis images that testcontainers will use, generates a
   fresh `.env` with a random `JWT_SECRET`.
4. The integrated terminal lands in the project root. Try:

```bash
make help                          # list every Makefile target
make lint type audit               # static checks
make test-int                      # full pytest incl. testcontainers
make prod-up                       # the FULL prod stack on Codespace ports
make prod-ps                       # health snapshot
curl -fsS http://localhost:8000/api/v1/health
```

The api port is auto-forwarded — Codespaces shows a clickable
"Open in Browser" toast when port 8000 binds.

### H.2 — CI runs the same commands on every push

`.github/workflows/ci.yml` has a **prod_smoke** job (after
`static` → `test` → `build`) that:

1. Composes a hermetic `.env` with random secrets.
2. `docker compose config` validates the YAML.
3. `docker compose up -d --build --wait` brings up the FULL prod
   stack — postgres, redis, migrate, bootstrap, api, worker, pg_backup.
4. Confirms `migrate` + `bootstrap` exited 0 (alembic chain +
   IAM/admin seed).
5. Polls `/api/v1/health` until `{"status":"live"}`.
6. Tears down with volume cleanup.

So you can answer "does `make prod-up` actually work?" by just pushing
and reading the Actions tab — no Codespace, no SSH needed.

### H.3 — Share a Codespace with someone else (e.g. for pair-debug)

```bash
# Inside any Codespace:
gh codespace ssh           # give them the resulting connection string
# OR for a temporary public-port URL:
gh codespace ports visibility 8000:public
```

---

## I. Pre-flight checks before going live

The audit confessions in earlier sessions are now resolved, but a few
items can only be validated on a real Linux+Docker host. Run these in
order before announcing go-live.

### I.1 — Smoke the bring-up
```bash
make prod-up
docker compose -f docker-compose.prod.yml ps          # all services healthy
docker compose -f docker-compose.prod.yml logs migrate
# Expect: alembic upgrade applies 0001 → 0022 cleanly.
docker compose -f docker-compose.prod.yml logs bootstrap
# Expect: "iam-bootstrap: roles + permissions synced",
#         "superuser ready: yousufmiah28@gmail.com" (if INITIAL_ADMIN_* set).
docker compose -f docker-compose.prod.yml logs api | grep providers_bound
# Expect one line listing every bound provider name.
```

### I.2 — Real provider round-trips (sandbox creds)

For each provider you've enabled, send ONE message and confirm it
arrived. These prove the credentials + adapter wiring without burning
production volume.

```bash
# Bkash sandbox: place a 10 BDT order paid online, hit the
# checkout_url returned by /payments/initiate, complete with sandbox
# wallet, confirm webhook lands at /payments/webhooks/bkash and the
# order transitions to payment_confirmed.

# WhatsApp Cloud sandbox: the bootstrap call to dispatch_invoice fires
# automatically when a prescription is approved. After approval, run:
#   make prod-logs | grep whatsapp_sent
# Expect a line with wamid=wamid....
# Then check the audit_log:
#   docker compose -f docker-compose.prod.yml exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
#     -c "SELECT action, resource_id, metadata FROM audit_log WHERE action='whatsapp.invoice.sent' ORDER BY created_at DESC LIMIT 5"
# Then confirm Meta posts a delivery receipt to /api/v1/whatsapp/webhook:
#   docker compose -f docker-compose.prod.yml exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
#     -c "SELECT wamid, status, recipient_msisdn, received_at FROM whatsapp_message_statuses ORDER BY received_at DESC LIMIT 5"

# FCM (Android): register a test device via POST /me/devices, then
# advance an order to APPROVED state. Watch the worker logs:
#   docker compose -f docker-compose.prod.yml logs worker | grep push_fanout
# Expect delivered=1.

# APNS (iOS): same as FCM but with kind="apns" + the device's APNS
# token. HTTP/2 is required — h2 is in the worker image (verified on
# build). If APNS replies "BadDeviceToken", the token is wrong (sandbox
# vs production mismatch) — re-register with a fresh token.
```

### I.3 — Visually verify the frontend widget

The widget was previewed via `demo.html`, but the patched
`pharmacy_hero_slider_realistic.html` and `hypershop_dashboard_Fainal html`
host pages were NOT visually verified end-to-end with the widget loaded.
Open both in a real browser pointing at your prod API and confirm:

- Modal overlay appears above the hero slider (z-index is set to
  `2147483646` so it should always win).
- Login modal accepts a phone number, submits, shows the OTP step.
- Logged-in state persists across page refresh (localStorage).
- Logout clears state. The host page reacts via `hypershop:logout`
  event (no auto-reload — by design, preserves mid-cart state).

If the modal renders BELOW some host element, increase its
`z-index` further or wrap in `<dialog>` with `showModal()`.

### I.4 — Check the `make test-int` suite passes

```bash
make test-int
```

Module 27 (shipping zones) makes `place_order` require a delivery zone.
Tests that previously called `place_order` without seeded zones have
been patched — the orders + e2e conftests now seed the canonical 3 BD
zones. If a test in another module also exercises `place_order`, add
the same `_seed_delivery_zones` fixture (template in
`app/modules/orders/tests/conftest.py`).

### I.5 — Frontend security hardening

Set `data-no-reload` on the widget script tag (recommended; documented
in `hypershop-frontend/README.md`). Set `data-refresh-storage="session"`
if your storefront accepts third-party scripts that could XSS the
page. For maximum security, terminate refresh tokens in an httpOnly
cookie server-side and skip the widget's refresh leg.
