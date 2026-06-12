# Hypershop Backend — Deploy to Render (managed, push-to-deploy)

This puts `https://api.hypershop.com.bd` live on Render with managed
Postgres + Redis, auto-TLS, and a background worker — no server admin.

**Stack provisioned by [`render.yaml`](./render.yaml):** FastAPI web +
ARQ worker + Postgres 16 + Redis. Region: **Singapore** (closest to BD).

> What only you can do (needs your accounts): create the GitHub repo,
> push, sign in to Render, set secret env vars, and add ONE DNS record at
> GreenGeeks. Everything else is in the committed Blueprint.

---

## Step 0 — Push the backend to GitHub  *(one-time)*

The repo is already `git init`-ed and committed locally. Create an empty
repo on GitHub (e.g. `hypershop-backend`, **private**), then:

```bash
cd /path/to/backend            # C:\hs_wh\backend
git remote add origin https://github.com/<you>/hypershop-backend.git
git branch -M main
git push -u origin main
```

`.gitignore` already excludes `.env`, secrets, `__pycache__`, and local
volumes — only source + the Blueprint go up.

---

## Step 1 — Create the Blueprint on Render

1. Sign in at <https://dashboard.render.com> (GitHub login is easiest).
2. **New → Blueprint** → connect your GitHub → pick `hypershop-backend`.
3. Render reads `render.yaml` and shows the 4 resources it will create
   (api, worker, postgres, redis). Click **Apply**.
4. First build runs the Dockerfile, `preDeployCommand` runs
   `alembic upgrade head`, then the api + worker boot. Watch the logs.

The api gets a default URL like `https://hypershop-api.onrender.com` —
use it to smoke-test before the custom domain is wired.

---

## Step 2 — Fill the secret env vars

Dashboard → **Env Groups → `hypershop-shared`** → set the `sync:false`
rows (they apply to BOTH api + worker):

| Key | Example / note |
|---|---|
| `CORS_ORIGINS` | `https://hypershop.com.bd,https://admin.hypershop.com.bd` |
| `INITIAL_ADMIN_EMAIL` | your first super-admin login |
| `INITIAL_ADMIN_PASSWORD` | strong, 12+ chars |
| `SMTP_HOST` / `SMTP_PORT` | e.g. `smtp.gmail.com` / `587` (for OTP/email) |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | mailbox creds / app password |
| `SMTP_SENDER` | `no-reply@hypershop.com.bd` |

`JWT_SECRET` is auto-generated and shared — don't set it. `DATABASE_URL`,
`DATABASE_SYNC_URL`, and `REDIS_URL` are wired automatically (the sync URL
is derived inside the app). Save → Render redeploys with the new values.

---

## Step 3 — Smoke-test on the onrender.com URL

```bash
curl -s https://hypershop-api.onrender.com/api/v1/health     # -> {"status":"ok"...}
curl -s https://hypershop-api.onrender.com/api/v1/admin/live-streams   # 401 = wired & auth-gated
```

If health is 200, the stack is good. Now attach the real domain.

---

## Step 4 — Custom domain + the ONE DNS record at GreenGeeks

1. Render: api service → **Settings → Custom Domains → Add**
   `api.hypershop.com.bd`. Render shows a target like
   `hypershop-api.onrender.com` and asks for a CNAME.
2. Log in to **GreenGeeks** (the zone for `hypershop.com.bd` is on
   `ns1/ns2.greengeekdns.com`) → cPanel **Zone Editor** for
   `hypershop.com.bd` → **Add Record**:

   | Field | Value |
   |---|---|
   | Type | **CNAME** |
   | Name | `api`  (cPanel may show it as `api.hypershop.com.bd.`) |
   | Record / Target | `hypershop-api.onrender.com` |
   | TTL | leave default (e.g. 14400) |

   > Your apex `hypershop.com.bd` (→ `107.6.142.186`, the GreenGeeks
   > shared host) is untouched — only the new `api` subdomain points to
   > Render. The frontends/storefront can stay where they are.

3. Back in Render, click **Verify**. It issues a Let's Encrypt cert
   automatically once the CNAME resolves (minutes to ~an hour).

Check propagation:
```bash
nslookup api.hypershop.com.bd      # must show the onrender target/IP
curl -s https://api.hypershop.com.bd/api/v1/health
```

When that returns 200 over HTTPS, the mobile apps + frontends (which bake
`https://api.hypershop.com.bd`) are live against production.

---

## Costs (Singapore, always-on) — approximate

| Resource | Plan in Blueprint | ~USD/mo |
|---|---|---|
| Web (api) | starter | ~7 |
| Worker | starter | ~7 |
| Postgres | basic-256mb | ~6 |
| Redis (Key Value) | starter | ~10 |

Tune plans in `render.yaml` before applying (or scale later in the
dashboard). Free tiers cold-start and expire — not for a production API.

---

## Follow-ups (not blockers for first launch)

- **File uploads / media:** Render's web filesystem is **ephemeral** —
  it resets on each deploy. Catalog images already use R2; make sure
  product-videos / delivery-POD / report PDFs also write to R2/S3 in
  production (or attach a Render **Disk** at `/var/hypershop` to the api
  service — note a disk blocks zero-downtime deploys + horizontal scale).
- **Payment + OTP adapters:** wire Bkash/SSLCommerz + WhatsApp/SMS creds
  per `CREDS_FASTPATH.md` anytime after launch — no redeploy needed.
- **Backups:** Render Postgres has daily automated backups on paid plans;
  the compose `pg_backup`→R2 cron isn't used on Render.
