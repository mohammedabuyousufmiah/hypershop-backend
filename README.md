# Hypershop FastAPI Backend

Production rebuild of the Hypershop e-commerce backend as a **modular monolith** on FastAPI + async SQLAlchemy + Postgres + Redis + ARQ.

## Hard rules

These are enforced at architecture, code-review, and CI level — not just convention:

- All business rules live in the **service layer**. API handlers do validation, auth, dispatch, and serialization only.
- Every mutating endpoint has a **Pydantic v2 schema** for input and output. No untyped JSON.
- Every sensitive action is **audited** in the same DB transaction it commits in.
- Every critical write runs in a **single transaction**; partial commits are not possible.
- **RBAC** (role → permission strings) is mandatory on every protected endpoint.
- **Object-level authorization** runs on top of RBAC for any resource with ownership.
- No placeholders, no fake transports, no demo logic. Adapters are interfaces; implementations are real.

## Layout

```
hypershop_fastapi_backend/
├── app/
│   ├── main.py              FastAPI app factory
│   ├── worker.py            ARQ worker entrypoint
│   ├── cli.py               admin CLI (typer)
│   ├── core/                shared kernel
│   │   ├── config.py        env-driven settings (Pydantic Settings)
│   │   ├── logging.py       JSON structured logs (structlog)
│   │   ├── db/              async SQLAlchemy + UoW + transactional scope
│   │   ├── security/        Argon2, JWT, RBAC, object-level authz
│   │   ├── audit/           audit log model + service + @audited decorator
│   │   ├── events/          in-proc bus + transactional outbox
│   │   ├── middleware/      request-id, security headers, access log
│   │   ├── errors.py        typed domain exceptions
│   │   ├── exception_handlers.py    error envelope mapper
│   │   ├── validation.py    base schemas + envelopes
│   │   ├── pagination.py    cursor + offset pagination
│   │   ├── money.py         Decimal + Currency
│   │   ├── time.py          UTC helpers
│   │   ├── ids.py           UUID7 generator
│   │   ├── cache.py         Redis client
│   │   ├── ratelimit.py     Redis token-bucket
│   │   └── idempotency.py   idempotency-key store
│   └── modules/             domain modules (Phase 1+)
│       ├── iam/
│       ├── catalog/
│       ├── inventory/
│       ├── cart/
│       ├── orders/
│       ├── payments/
│       ├── notifications/
│       └── admin_ops/
├── alembic/                 migrations
├── tests/                   integration tests (testcontainers)
├── docker-compose.yml       local dev stack
├── Dockerfile               api image
├── Dockerfile.worker        ARQ worker image
└── pyproject.toml
```

## Phase status

- [x] **Phase 0** — core kernel, project skeleton, Docker, Alembic, CI gates
- [ ] Phase 1 — IAM (users, roles, JWT, OTP, password reset, audit-on-auth)
- [ ] Phase 2 — Catalog
- [ ] Phase 3 — Inventory
- [ ] Phase 4 — Cart + Orders + Reservations
- [ ] Phase 5 — Payments (one real gateway; awaiting provider choice)
- [ ] Phase 6 — Notifications (real providers; awaiting provider choice)
- [ ] Phase 7 — Admin ops + reporting
- [ ] Phase 8 — Hardening + deploy bundle

## Local dev

```bash
cp .env.example .env                    # then edit JWT_SECRET (>= 32 chars)
docker compose up --build               # api + worker + postgres + redis + mailpit
docker compose exec api alembic upgrade head
curl http://localhost:8000/api/v1/health
```

API docs: <http://localhost:8000/docs> (only enabled outside `production`).

## Tests

The suite has two execution modes depending on whether you have Docker.

### A. With Docker (recommended; matches CI)

```bash
pip install -e ".[dev]"
make test-int      # spins up Postgres + Redis via testcontainers
```

### B. Without Docker (Windows-friendly)

Requires a Postgres + Redis already running locally on the default ports.
The conftest sees the env vars and skips testcontainers entirely.

PowerShell:
```powershell
pip install -e ".[dev]"
.\scripts\run_tests_local.ps1 -DbUser postgres -DbPassword <yourpw>
```

Bash / Git Bash:
```bash
pip install -e ".[dev]"
PG_USER=postgres PG_PASSWORD=<yourpw> bash scripts/run_tests_local.sh
```

The scripts create `hypershop_test` (idempotent), set
`HYPERSHOP_TEST_DATABASE_URL` and `HYPERSHOP_TEST_REDIS_URL` for the
session, and run `pytest`. Redis db `15` is used to keep the test traffic
away from anything else on `:6379`.

### Lint / type / audit

```bash
make lint type audit
```

CI gates: ruff, mypy strict, pytest, bandit, pip-audit. Coverage floor 80%.

### Product video pipeline smoke test (Module 35)

Unit + integration tests stub the FFmpeg + R2 + Bunny boundary. To verify
the full pipeline end-to-end against a real stack, use the smoke script:

```bash
docker compose up -d                       # stack must be running

export SMOKE_PRODUCT_ID=<existing-product-uuid>
export SMOKE_ADMIN_EMAIL=admin@hypershop.local
export SMOKE_ADMIN_PASSWORD=<password>
# OR (CI-friendly):
# export SMOKE_ADMIN_TOKEN=<bearer>

bash scripts/smoke_test_video.sh
```

The script exits non-zero on the first failed check. It walks the
canonical pipeline:

1. `GET /health`
2. Generate a 3 s test mp4 inside the worker container (uses the
   container's ffmpeg, no host install needed)
3. `POST /product-videos/products/{id}/upload` → row at status=uploaded
4. Poll worker until status=ready_for_review (default timeout 60 s)
5. Verify `hls_url` + `thumbnail_url` + `duration_seconds` populated
6. `GET /admin/product-videos/pending` lists the row
7. `POST /admin/product-videos/{id}/approve` → status=approved + `approved_at` stamped
8. `GET /products/{id}/videos` (public) returns the approved video
9. The HLS master playlist URL returns HTTP 200 + a valid `#EXTM3U`
   header (covers both Bunny CDN mode and on-disk fallback mode)

Run this before any production release, after touching anything in
`app/modules/product_videos/`, or in CI on a nightly schedule.

## Migrations

Alembic operates with the sync driver via `DATABASE_SYNC_URL`. The runtime uses `DATABASE_URL` (asyncpg).

```bash
make revision m="add foo table"
make migrate
```

## Production notes

- `.env` files are **dev-only**. Production reads env vars directly from your runtime (k8s Secret, ECS task def, systemd EnvironmentFile).
- All secrets MUST be supplied via env: `JWT_SECRET`, `DATABASE_URL`, `REDIS_URL`, `SMTP_*`. App refuses to start if any are missing.
- API docs (`/docs`, `/redoc`, OpenAPI JSON) are disabled in `production`.
- Security headers (HSTS, CSP, X-Content-Type-Options, Referrer-Policy, Permissions-Policy) are emitted by middleware.
- CORS is closed by default; populate `CORS_ORIGINS` to allow specific origins.
