# Hypershop — Production Deployment Guide (v25)

This is the **single source of truth** for shipping Hypershop to a real
server. Supersedes earlier per-sprint deploy notes.

> 49 modules · 58 migrations · 20 sprints · Python 3.12 · FastAPI · Postgres 16 · Redis 7

## 0 · One-page TL;DR

```bash
# 1. Server bootstrap (one-time on a fresh box)
ssh root@your-host
curl -fsSL https://get.docker.com | sh
git clone <your fork> /opt/hypershop && cd /opt/hypershop

# 2. Secrets — copy + edit
cp .env.prod.example .env
$EDITOR .env                # fill JWT_SECRET, POSTGRES_PASSWORD, etc.

# 3. Build + boot
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
docker compose -f docker-compose.prod.yml up -d

# 4. Verify
curl -fsS http://localhost:8000/api/v1/health
docker compose -f docker-compose.prod.yml logs --tail=50 api
```

If `boot_preflight` exits non-zero, the api container won't start —
read the red `FAIL` lines and fill in the missing env vars before
retrying. WARN lines are advisory; the container boots anyway.

---

## 1 · What ships inside the image

| Path | What | Where it ends up at runtime |
|---|---|---|
| `app/` | FastAPI + service code (49 modules) | `/app/app/` |
| `alembic/` + `alembic.ini` | 58 migrations | `/app/alembic/` |
| `app/modules/sellers/_frontend_src/dist/` | Seller PWA prod build | static-served at `/seller/` |
| `app/modules/customer_care/_frontend_src/dist/` | Customer-care PWA prod build | static-served at `/customercare/` |
| `boot_preflight.py` | Fail-fast env validator | runs before gunicorn |
| pinned deps (see `Dockerfile`) | 50+ pinned packages | `/install/` |

The image runs as `app:app` (uid 1000, non-root) and writes only to
`/var/hypershop/*` (which docker-compose mounts as named volumes).

---

## 2 · Required environment variables (hard checks)

These are validated by `boot_preflight.py`. Container refuses to start
if any of these are missing or weak.

| Var | Rule | Example |
|---|---|---|
| `ENVIRONMENT` | `production` or `staging` | `production` |
| `JWT_SECRET` | ≥ 32 chars | `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | ≥ 16 chars | `openssl rand -hex 16` |
| `DATABASE_URL` | `postgresql+asyncpg://…` | `postgresql+asyncpg://hypershop:$PG@db:5432/hypershop` |
| `REDIS_URL` | `redis://…` | `redis://redis:6379/0` |
| `CORS_ORIGINS` | comma-list; in prod HTTPS only | `https://hypershop.com.bd,https://admin.hypershop.com.bd` |

## 3 · Optional environment variables (warn-only)

`boot_preflight` warns about these but boots anyway. Set them as you
turn on each capability.

| Var | Purpose | Default behaviour if unset |
|---|---|---|
| `OTP_CHANNEL_WHATSAPP_*` | Meta Cloud creds | log-only (dev) WhatsApp sender |
| `BULKSMS_BD_*` | Bangladesh SMS API | log-only SMS |
| `SMTP_*` | Email | log-only email |
| `BKASH_*` / `SSLCOMMERZ_*` | Payment provider creds | `/payments/*` writes the order but no real charge |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Tracing | tracing OFF |
| `BACKUP_INTERVAL_SECONDS` | Postgres backup cadence | 24h |
| `INITIAL_ADMIN_EMAIL` + `INITIAL_ADMIN_PASSWORD` | First-boot admin seeding | no admin auto-seed |
| `OPENAI_API_KEY` | SEO agents AI path (v23) | deterministic-fallback content (still usable) |
| `SEO_OPENAI_MODEL` | OpenAI model name | `gpt-4.1-mini` |
| `SEO_SITE_BASE_URL` | Used by SEO agent fallbacks | falls back to `settings.seo_site_url` |
| `SEO_DEFAULT_COUNTRY` | Used in SEO prompts | `Bangladesh` |
| `WEB_CONCURRENCY` | gunicorn worker count | 4 |

---

## 4 · First-time DB setup

Run **once** when bootstrapping a new database:

```bash
# Inside the api container:
docker compose -f docker-compose.prod.yml run --rm api \
    alembic upgrade head
```

This applies all **58 migrations** from `0001_init_audit_outbox_idem`
through `0058_seo_agents`. Migrations are linear (one head, one root)
— no merge surgery required.

To verify after:

```bash
docker compose -f docker-compose.prod.yml exec api \
    alembic current
# expected: 0058_seo_agents (head)
```

---

## 5 · Daily ops

### Logs

```bash
docker compose -f docker-compose.prod.yml logs -f api          # api
docker compose -f docker-compose.prod.yml logs -f worker       # arq jobs
docker compose -f docker-compose.prod.yml logs -f db           # postgres
```

JSON-structured via `structlog`; pipe to your log aggregator (Loki,
ELK, Datadog).

### Healthcheck

```bash
curl -fsS https://api.hypershop.com.bd/api/v1/health
# {"status":"ok","db":"ok","redis":"ok",...}
```

Docker's HEALTHCHECK is wired to this; `docker ps` shows healthy /
unhealthy / starting.

### Migrations (rolling update)

```bash
git pull
docker compose -f docker-compose.prod.yml build api worker
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
docker compose -f docker-compose.prod.yml up -d --no-deps api worker
```

`gunicorn --max-requests 1000 --max-requests-jitter 100 --graceful-timeout 30`
ensures workers drain in-flight requests during the swap.

### Worker (ARQ) cron jobs

The worker container runs the same image (different `CMD`). It runs
all cron-registered jobs:
- payout recalculation
- subscription renewal
- product-video FFmpeg encode
- BI snapshot refresh
- (and the rest)

To see what's registered:

```bash
docker compose -f docker-compose.prod.yml exec worker \
    python -c "from app.worker import WorkerSettings; \
               print([j.__name__ for j in WorkerSettings.cron_jobs])"
```

### Backups

`docker-compose.prod.yml` includes a `backup` service that runs
`pg_dump` on a `BACKUP_INTERVAL_SECONDS` (default 24h) cadence into a
volume mounted at `/var/hypershop/backups/`. Ship to S3 / R2 / Bunny
via a sidecar cron of your choosing.

---

## 6 · v25 optimization checklist

These were applied in sprint 21 (this release). Worth understanding
before customising:

- ✅ `.dockerignore` fixed — was excluding shipped PWA `dist/` folders
  via a bare `dist` rule; now anchored to `/dist` (project root only)
- ✅ `openai>=1.40,<2` added to both Dockerfiles — needed when
  `OPENAI_API_KEY` is set (silent fallback before)
- ✅ `gunicorn --workers ${WEB_CONCURRENCY:-4}` — env-driven; override
  with `-e WEB_CONCURRENCY=N` on the compose service for `(2 × CPU) + 1`
- ✅ `gunicorn --worker-tmp-dir /dev/shm` — tmpfs heartbeat avoids
  slow-disk false-positive worker kills
- ✅ Pre-compiled `.pyc` cache during image build — faster cold start
  on autoscale-up
- ✅ `boot_preflight` now warns when SEO agents are running fallback
  mode and when the seller PWA `dist/` is missing
- ✅ Three-phase orchestrator on SEO agents (v24) — no DB transaction
  pinned during OpenAI HTTP calls

---

## 7 · Production sanity checks (post-deploy smoke)

```bash
BASE="https://api.hypershop.com.bd"

# Health
curl -fsS $BASE/api/v1/health

# SEO meta (M34 multi-language)
curl -fsS "$BASE/api/v1/seo/meta/home"           | jq .locale          # "en"
curl -fsS "$BASE/api/v1/seo/meta/home?lang=bn"   | jq .locale          # "bn"
curl -fsS "$BASE/sitemap.xml" | head -10                              # xhtml namespace present

# Seller PWA serves real HTML (not the placeholder)
curl -fsS "$BASE/seller/" | head -5                                   # <html lang="en">

# Customer-care PWA
curl -fsS "$BASE/customercare/" | head -5
```

If any of these come back 404 / placeholder HTML, see the
"WARN" lines in `boot_preflight` output during container start.

---

## 8 · Rollback

```bash
# Image rollback
docker compose -f docker-compose.prod.yml down api worker
docker compose -f docker-compose.prod.yml up -d \
    --no-deps api worker --image hypershop-backend:<previous-sha>

# Schema rollback (per-migration)
docker compose -f docker-compose.prod.yml run --rm api \
    alembic downgrade -1
```

Every migration in `alembic/versions/` has a working `downgrade()`.
Verify the target migration matches the previous image's expectation
before rolling back schema.

---

## 9 · Where things live (cheat sheet)

```
/opt/hypershop/
├── docker-compose.prod.yml         # the only file you run
├── .env                            # secrets (gitignored)
├── Dockerfile / Dockerfile.worker  # build inputs
├── app/                            # source — read-only at runtime
└── /var/lib/docker/volumes/        # named volumes:
    ├── hypershop_pgdata/                # postgres
    ├── hypershop_redis/                 # redis
    ├── hypershop_uploads/               # /var/hypershop/* mounts
    └── hypershop_backups/               # pg_dump artefacts
```

Inside the api container:

```
/app/
├── app/                            # service code
│   └── modules/sellers/_frontend_src/dist/   ⭐ shipped PWA
│   └── modules/customer_care/_frontend_src/dist/
├── alembic/
├── boot_preflight.py
└── /var/hypershop/                 # writable (bind-mounted volume)
```

---

## 10 · Known good versions

| Tier | Version | Source |
|---|---|---|
| Python | 3.12.x | `Dockerfile FROM python:3.12-slim` |
| FastAPI | 0.115.6 | pin in Dockerfile |
| SQLAlchemy | 2.0.36 | pin |
| asyncpg | 0.30.0 | pin |
| Postgres | 16 | docker-compose.prod.yml |
| Redis | 7.x | docker-compose.prod.yml |
| OpenAI SDK | >=1.40,<2 | pin (v25 new) |
| gunicorn | 23.0.0 | pin |
| uvicorn | 0.34.0 | pin |

Built and verified 2026-05-14 in Bangladesh.
