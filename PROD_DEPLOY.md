# Hypershop — Production Deploy Guide

**Stack:** FastAPI + Postgres 16 + Redis 7 + ARQ worker + Caddy TLS.
**Method:** Docker Compose (3 files merged) on a single Linux host.
**Estimated time:** 30-60 minutes if all DNS + creds in hand.

> All critical-blocker items from the go-live audit are resolved by
> following this guide. The two adapter-pending items (OTP channel +
> payment gateways) live in [`CREDS_FASTPATH.md`](../CREDS_FASTPATH.md)
> and can be wired anytime after launch without redeploying.

---

## ✅ What's Already Solved (the audit's RED items)

| Audit item | Resolution in this zip |
|---|---|
| `ENVIRONMENT=production` | Default in `.env.prod.example`; preflight script enforces |
| TLS for `*.hypershop.com.bd` | `docker-compose.tls.yml` + Caddy auto-Let's Encrypt |
| Postgres 16 + backups | `docker-compose.prod.yml` ships `postgres:16-alpine` + nightly `pg_dump` cron in volume |
| Redis 7 | `docker-compose.prod.yml` ships `redis:7-alpine` |
| `alembic upgrade head` | `migrate` service runs it automatically before `api` starts |
| Plaintext `.env` removed | Only `.env.example` (dev) + `.env.prod.example` (template) ship; you generate the real `.env` on the deploy host |
| Secrets vault handoff | This doc shows 3 patterns (Docker Secrets / SOPS / SSM) |

Two yellow items left (adapter-ready, creds-pending — see `CREDS_FASTPATH.md`):
- ⚠️ OTP channel (WhatsApp / SMS / SMTP)
- ⚠️ Payment gateways (Bkash + SSLCommerz)

---

## 🛠 Step 1 — Provision Linux host

**Minimum spec:** 2 vCPU / 4 GB RAM / 40 GB SSD / Ubuntu 22.04 or Debian 12.

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER && newgrp docker
```

Open firewall ports (ufw example):
```bash
sudo ufw allow 22/tcp     # SSH
sudo ufw allow 80/tcp     # ACME HTTP-01
sudo ufw allow 443/tcp    # HTTPS
sudo ufw allow 443/udp    # HTTP/3
sudo ufw enable
```

---

## 🌐 Step 2 — DNS

Point both A records to your host's public IPv4:

```
api.hypershop.com.bd          → <HOST_IP>
hypershop.com.bd              → <HOST_IP>        (or Vercel CNAME if you use Vercel for the frontends)
admin.hypershop.com.bd        → <HOST_IP>        (optional — only if you self-host admin-panel)
seller.hypershop.com.bd       → <HOST_IP>        (optional — only if you self-host seller-panel)
```

Wait for DNS to propagate (`dig api.hypershop.com.bd` must return your IP) before step 4.

---

## 🔐 Step 3 — Generate `.env` from template

```bash
cd /opt/hypershop/backend     # or wherever you extracted the zip
cp .env.prod.example .env

# Generate secrets
echo "JWT_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" >> .env
echo "POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env

# Edit .env and fill:
nano .env
```

**Minimum required fields to set:**

```ini
ENVIRONMENT=production
POSTGRES_PASSWORD=<from above>
JWT_SECRET=<from above>

CORS_ORIGINS=https://hypershop.com.bd,https://admin.hypershop.com.bd,https://seller.hypershop.com.bd

API_DOMAIN=api.hypershop.com.bd
ACME_EMAIL=ops@hypershop.com.bd            # for Let's Encrypt notices

# Constructed automatically from POSTGRES_PASSWORD by compose:
DATABASE_URL=postgresql+asyncpg://hypershop:${POSTGRES_PASSWORD}@postgres:5432/hypershop
DATABASE_SYNC_URL=postgresql+psycopg2://hypershop:${POSTGRES_PASSWORD}@postgres:5432/hypershop
REDIS_URL=redis://redis:6379/0

# First-deploy admin login (idempotent — set, deploy once, then unset)
INITIAL_ADMIN_EMAIL=admin@hypershop.com.bd
INITIAL_ADMIN_PASSWORD=<strong-password>
```

**Validate before deploying:**
```bash
docker compose -f docker-compose.prod.yml run --rm api python boot_preflight.py
# Expected: "All hard checks passed — safe to start api + worker."
```

---

## 🔒 Step 4 — Stand up the stack

```bash
docker compose \
  -f docker-compose.prod.yml \
  -f docker-compose.tls.yml \
  up -d --build
```

What happens:
1. `postgres:16-alpine` starts → healthy
2. `redis:7-alpine` starts → healthy
3. `migrate` service runs `alembic upgrade head` (42 migrations → DB schema)
4. `bootstrap` service seeds initial admin (if `INITIAL_ADMIN_*` set)
5. `api` starts (4 uvicorn workers) → healthy on `127.0.0.1:8000`
6. `worker` starts (ARQ cron jobs) → consuming Redis queue
7. `caddy` requests Let's Encrypt cert for `${API_DOMAIN}` and starts reverse-proxying

Verify:
```bash
docker compose ps                              # all services 'healthy'
curl -sf https://api.hypershop.com.bd/health   # → "ok"
docker compose logs api | grep providers_bound # shows which adapters bound
```

---

## 🚦 Step 5 — Post-deploy smoke tests

```bash
# 1. Health + version
curl -s https://api.hypershop.com.bd/health
curl -s https://api.hypershop.com.bd/api/v1/health

# 2. Admin login (uses INITIAL_ADMIN credentials)
curl -X POST https://api.hypershop.com.bd/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@hypershop.com.bd","password":"<strong-password>"}'
# Expected: 200 with {access_token, refresh_token}

# 3. Public catalog
curl -s https://api.hypershop.com.bd/api/v1/catalog/products?limit=5 | jq '.total'

# 4. Run the in-zip audit against live stack
python audit_go_live.py
# Target: 43 green / yellow only for adapter-pending creds / 0 red
```

---

## 🔑 Step 6 — Secrets vault (recommended hardening)

Plaintext `.env` on disk is acceptable for a single-host deploy as long as:
- The file is `chmod 600 .env`
- Only the `docker` group can read it
- It's NOT committed to git

For team / multi-host / regulatory:

### Option A — Docker Secrets
`docker-compose.prod.yml` already templates secret mounts. Move
`POSTGRES_PASSWORD` + `JWT_SECRET` to Docker Swarm secrets and remove
from `.env`. Compose reads them from `/run/secrets/`.

### Option B — SOPS + age (gitops-friendly)
```bash
# Install sops + age once
brew install sops age

# Generate keypair
age-keygen -o ~/.config/sops/age/keys.txt

# Encrypt
sops --encrypt --age <recipient> --in-place .env
# Commit .env (now encrypted) to git
```

Deploy script:
```bash
sops --decrypt .env > /run/hypershop.env
docker compose --env-file /run/hypershop.env up -d
shred -u /run/hypershop.env
```

### Option C — AWS Systems Manager Parameter Store / Secrets Manager
- Store each var as a SecureString
- `docker compose` reads via `aws ssm get-parameter` in entrypoint
- IAM role on the EC2 / ECS task grants access

---

## 🗄️ Step 7 — Backups

`docker-compose.prod.yml` includes a `postgres-backup` sidecar that runs
`pg_dump` every `BACKUP_INTERVAL_SECONDS` (default 86400 = 24h) and
retains for `BACKUP_RETENTION_DAYS` (default 14).

**Offsite copy** (recommended): mount the backup volume to a host path,
add a cron to `aws s3 sync` (or `rclone`) to R2 / S3 / Backblaze:

```cron
# /etc/cron.d/hypershop-backup
0 3 * * * root rclone sync /var/lib/docker/volumes/hypershop_postgres_backups/_data r2:hypershop-pg-backups
```

**Restore drill** (do this BEFORE you need it):
```bash
docker compose exec postgres pg_restore -U hypershop -d hypershop /backups/<dump>.sql
```

---

## 📊 Step 8 — Observability (optional but recommended)

```bash
docker compose \
  -f docker-compose.prod.yml \
  -f docker-compose.tls.yml \
  -f docker-compose.observability.prod.yml \
  up -d --build
```

Adds:
- Jaeger UI at `https://jaeger.hypershop.com.bd` (set `JAEGER_DOMAIN` in `.env`)
- Prometheus scraping `/metrics`
- Grafana with pre-provisioned dashboards from `ops/grafana/provisioning/`

---

## 🔄 Step 9 — Rolling updates

```bash
# Pull latest code (or extract a new zip)
git pull origin main          # or: unzip hypershop-...-latest.zip

# Rebuild only the changed services
docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml \
  up -d --build api worker

# Migrations re-run automatically by the `migrate` service
# at the next stack restart.
```

Zero-downtime alternative:
```bash
docker compose up -d --no-deps --build api    # rolling restart of api only
```

---

## 🚨 Rollback procedure

```bash
# 1. Stop new version
docker compose -f docker-compose.prod.yml down api worker

# 2. Restore previous image tag (CI tags as :latest + :<sha>)
docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml \
  up -d api worker

# 3. Roll DB back if a migration broke
docker compose exec api alembic downgrade -1
```

Module-35-specific rollback: see `docs/ROLLBACK_MODULE_35.md`.

---

## 📋 Pre-launch sign-off checklist

Run through this BEFORE pointing real customer traffic at the deploy:

- [ ] DNS resolves `api.hypershop.com.bd` to host IP
- [ ] `docker compose ps` shows all services healthy
- [ ] `boot_preflight.py` exits 0 (all hard checks pass)
- [ ] `audit_go_live.py` reports 0 red items
- [ ] HTTPS cert valid (`curl -v https://api.hypershop.com.bd/health` shows valid chain)
- [ ] HSTS header present
- [ ] CORS preflight: customer-web origin allowed; everything else rejected
- [ ] Initial admin can log in
- [ ] At least one OTP channel works end-to-end (see `CREDS_FASTPATH.md`)
- [ ] Cash-on-delivery checkout works end-to-end (without payment creds)
- [ ] Postgres backup file written to backup volume after first cycle
- [ ] Log aggregation reachable (`docker compose logs api | grep ERROR` is empty after warmup)
- [ ] Rollback drill succeeded in staging

When all are checked: **soft-launch with a small internal cohort → 24h soak → open to public.**

---

## 📞 Common deploy issues

| Symptom | Cause | Fix |
|---|---|---|
| `caddy` keeps retrying ACME | DNS not propagated yet | Wait for `dig API_DOMAIN` to return host IP |
| `migrate` exits 1 with "FATAL: role does not exist" | POSTGRES_PASSWORD mismatch between init + DSN | Re-create the postgres volume |
| `api` boots but routes return 502 | `boot_preflight.py` not run; required env missing | Run preflight; check `docker compose logs api` |
| `api` log: "provider_binding_failed" | Provider env vars partially set | Set all keys for that provider OR leave all blank (NotConfigured fallback is OK) |
| OTP returns 502 ServiceUnavailable | No OTP channel configured | See `CREDS_FASTPATH.md` |
| Checkout returns "no payment providers" | Bkash + SSLCommerz creds empty | See `CREDS_FASTPATH.md`, or accept COD-only until creds arrive |
| Slow `/products` queries | Postgres needs `ANALYZE` after seed | `docker compose exec postgres psql -U hypershop -c "ANALYZE;"` |
| Worker not picking up jobs | Redis URL mismatch | Verify `REDIS_URL=redis://redis:6379/0` (service hostname, not localhost) |

---

**END — single source of truth for production deploy.**
