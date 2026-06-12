# Hypershop production deployment checklist

Tick each box as you go. **Do not skip ahead** — each stage's exit
criteria validates that you can safely start the next.

The whole sequence is 2-4 weeks if providers respond promptly. The
temptation will be to compress it. The real cost of skipping a step
shows up at 3am during a launch-week incident, not now.

---

## Stage 0 — Push + CI green

- [ ] Repo created at https://github.com/yousufmiah/hypershop-backend
      (Private, no README/license/.gitignore init)
- [ ] `git push -u origin main` succeeds (PAT pasted in YOUR terminal,
      never in chat)
- [ ] All 4 CI jobs green at
      https://github.com/yousufmiah/hypershop-backend/actions
  - [ ] `static` (ruff + mypy + bandit + pip-audit)
  - [ ] `test` (testcontainers; full pytest including 4 e2e tests +
        39 smoke tests; coverage >= 80%)
  - [ ] `build` (api + worker Docker images build cleanly)
  - [ ] `prod_smoke` (full prod stack boots; migrations apply;
        bootstrap seeds 12 reports + 2 workflows; 12 auth-gated routes
        return 401/403; 6 SEO public endpoints return correct shapes)

**Exit criteria:** ✅ all 4 jobs green. If `prod_smoke` fails, paste
the failing step output into chat and I'll fix.

---

## Stage 1 — Deploy to STAGING (provider creds = placeholder)

A staging server is a real Linux box (DigitalOcean droplet, AWS EC2
small, your office desktop running Linux — anything with Docker).
Provider-dependent endpoints will return `502 NotConfigured` — that's
correct behavior, not a bug.

### Server prep
- [ ] Linux host with at least 4 vCPU, 8 GB RAM, 80 GB disk
- [ ] Docker + docker-compose installed
- [ ] Domain `staging-api.daily-life-pharmacy.com.bd` (or whatever)
      pointed at the server IP
- [ ] Port 80 + 443 open inbound; SSH (22) restricted to your IPs

### Initial deploy
- [ ] Clone the repo: `git clone https://github.com/yousufmiah/hypershop-backend.git`
- [ ] `cp .env.prod.example .env` and fill in:
  - [ ] `JWT_SECRET=$(openssl rand -base64 48)` (strong random)
  - [ ] `POSTGRES_PASSWORD=$(openssl rand -base64 32)` (strong random)
  - [ ] `INITIAL_ADMIN_EMAIL=admin@daily-life-pharmacy.com.bd`
  - [ ] `INITIAL_ADMIN_PASSWORD=$(openssl rand -base64 24)` (write
        down once, change on first login)
  - [ ] `CORS_ORIGINS=https://staging.daily-life-pharmacy.com.bd`
  - [ ] `API_DOMAIN=staging-api.daily-life-pharmacy.com.bd`
  - [ ] `ACME_EMAIL=admin@daily-life-pharmacy.com.bd` (for Let's Encrypt)
  - [ ] `SEO_SITE_URL=https://staging.daily-life-pharmacy.com.bd`
  - [ ] All other provider creds = leave empty / placeholder
- [ ] `make prod-up-tls` (boots stack with Caddy TLS terminator)
- [ ] `curl https://staging-api.daily-life-pharmacy.com.bd/api/v1/health`
      returns `{"status":"live"}`
- [ ] Log in to `/docs` (admin email + password from env)
- [ ] **Rotate admin password immediately** via the API (don't keep
      env-set password)

### One-week staging soak
- [ ] Walk every workflow end-to-end (browse `/docs`, hit endpoints):
  - [ ] Customer signup → OTP login flow (will fail at SMS — note
        the 502 in logs to confirm the integration point is wired)
  - [ ] Browse catalog → cart → place order → confirm prescription
        gate triggers
  - [ ] Doctor onboarding → issue prescription → wallet credit
  - [ ] Admin assigns delivery → rider scans → delivers (with COD)
  - [ ] Confirm rider wallet ledger updated (`/admin/rider-wallets`)
  - [ ] Submit MFS settlement → finance verifies → wallet cleared
  - [ ] Receive a supplier bill → 3-step approval → mark ready → pay
  - [ ] Run a report (`/api/v1/reports/{code}/run`) → export to CSV
  - [ ] Hit `/sitemap.xml` and `/robots.txt` from the public domain

**Exit criteria:** ✅ no crashes, no 5xx errors except documented 502
NotConfigured, no data corruption. If anything else breaks, fix
before continuing.

---

## Stage 2 — Get all 8 provider creds (sandbox first)

This is the slowest stage. Start applications NOW even if you're not
yet at this checklist item — Bangladesh providers can take 1-3 weeks
to approve merchant accounts.

### Payment gateways (BD)
- [ ] **Bkash merchant account** approved
      → https://merchant.bka.sh/
      → save: `BKASH_APP_KEY`, `BKASH_APP_SECRET`,
              `BKASH_USERNAME`, `BKASH_PASSWORD`,
              `BKASH_WEBHOOK_SECRET`
- [ ] **SSLCommerz merchant account** approved
      → https://www.sslcommerz.com/
      → save: `SSL_STORE_ID`, `SSL_STORE_PASSWORD`,
              `SSL_IS_SANDBOX=true` (initially)
- [ ] **Nagad merchant** approved (optional; SSLCommerz routes Nagad)
- [ ] **Rocket merchant** approved (optional; SSLCommerz routes Rocket)

### Communication providers
- [ ] **BulkSMSBD** (or SSL Wireless or Twilio) account approved
      → https://bulksmsbd.net/
      → save: `SMS_PROVIDER=bulksmsbd`, `BULKSMSBD_API_KEY`,
              `BULKSMSBD_SENDER_ID` (your registered brand name —
              Bangladesh BTRC requires registration)
- [ ] **Meta WhatsApp Cloud API** account + verified business phone
      → https://business.facebook.com/wa/manage/
      → save: `WHATSAPP_PHONE_NUMBER_ID`,
              `WHATSAPP_BUSINESS_ACCOUNT_ID`,
              `WHATSAPP_PERMANENT_ACCESS_TOKEN`,
              `WHATSAPP_WEBHOOK_VERIFY_TOKEN`
- [ ] **SMTP** for transactional email (SendGrid free tier / Amazon
      SES / Mailgun)
      → save: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`,
              `SMTP_PASSWORD`, `SMTP_USE_TLS=true`,
              `SMTP_SENDER=noreply@daily-life-pharmacy.com.bd`

### Mobile push
- [ ] **Firebase project** for FCM (Android push)
      → https://console.firebase.google.com/
      → download service-account JSON, store as
        `FCM_SERVICE_ACCOUNT_JSON_PATH=/run/secrets/fcm.json`
- [ ] **Apple Developer account** for APNS (iOS push) — only if you
      have an iOS rider app
      → save: `APNS_KEY_ID`, `APNS_TEAM_ID`,
              `APNS_PRIVATE_KEY_P8`, `APNS_BUNDLE_ID`,
              `APNS_IS_SANDBOX=true` initially

### Optional / later
- [ ] **AI provider** (OpenAI / Anthropic API key) for OCR + suggest
      flows — optional for v1; module returns 502 NotConfigured if
      not set
- [ ] **Search reranker** (Cohere Rerank / Voyage / your own ML) —
      Module 28 falls back to Postgres ts_rank if not configured

**Exit criteria:** ✅ every provider's sandbox/test environment
authenticates successfully via `curl` or the provider's own dashboard.
DON'T move to Stage 3 with one provider not yet sandbox-tested.

---

## Stage 3 — Wire creds in staging + smoke test each integration

For each provider you wired:

### Bkash
- [ ] Add Bkash creds to staging `.env`, restart api + worker
- [ ] Place a sandbox order via API → confirm Bkash redirect URL
      generated
- [ ] Complete sandbox payment → confirm webhook hits
      `/api/v1/payments/bkash/callback` → confirm order status flips
      to `payment_confirmed` in DB

### SSLCommerz
- [ ] Same dance: order → redirect → webhook → status update

### SMS
- [ ] Trigger an OTP login → confirm SMS arrives at your test phone
      (`+8801911740672`) → confirm OTP works

### WhatsApp
- [ ] Approve a prescription → confirm invoice WhatsApp message
      arrives at your test number → confirm wamid recorded in
      `audit_log`

### SMTP
- [ ] Trigger a password reset for a test user → confirm email arrives
- [ ] Submit a supplier bill → approve through level 1 → confirm
      level-2 approver email lands (set
      `SUPPLIER_PAYMENT_APPROVER_EMAILS_L2=your.test@email.bd`)

### FCM
- [ ] Register a test device token via API → trigger an order-update
      push → confirm push lands on the device

### Settlement SMS (Module 32)
- [ ] Drive a rider through delivery → settlement submit → finance
      verify → confirm rider's phone receives the verify SMS

**Exit criteria:** ✅ every provider integration verified end-to-end
in staging. If any returns a successful API call but the message
doesn't arrive, dig into the provider's delivery logs before
continuing.

---

## Stage 4 — Backup recovery test (DON'T SKIP)

The single most-skipped stage. The first time you'll find out your
backup strategy doesn't work is during a real disaster.

- [ ] Confirm `pg_backup` cron is running:
      `docker compose -f docker-compose.prod.yml ps pg_backup`
- [ ] Trigger a manual backup: `make prod-backup`
- [ ] Verify backup file exists in the `hypershop_pg_backups` volume:
      `docker volume inspect hypershop_pg_backups`
- [ ] **Spin up a fresh test machine** (NOT the staging box —
      a clean DO droplet for $5)
- [ ] Copy the latest `*.dump` to the test machine
- [ ] Run `pg_restore` against a fresh empty Postgres
- [ ] Boot the API against the restored DB:
      `make prod-up` with `DATABASE_URL` pointing at restored DB
- [ ] Confirm `/api/v1/health` returns 200
- [ ] Confirm a known order from staging is queryable
- [ ] Confirm rider wallet balances match what staging showed
- [ ] Document the recovery steps in `OPS-RUNBOOK.md`

**Exit criteria:** ✅ you have a written runbook that another person
could follow to restore the DB in <30 minutes. **Without this, you
do not have backups — you have wishful thinking.**

---

## Stage 5 — Log shipping + uptime monitoring

### Log aggregation (pick ONE)
- [ ] **Option A — Grafana Loki** (self-hosted, free): add Promtail
      sidecar to ship `docker logs` to a Loki instance
- [ ] **Option B — CloudWatch Logs** (AWS): use the awslogs Docker
      driver in `docker-compose.prod.yml`
- [ ] **Option C — Datadog/New Relic** (paid, easiest): install
      their agent on the host
- [ ] Confirm `structlog` JSON output appears in your aggregator
- [ ] Set up dashboards for: error rate per minute, p95 latency,
      DB connection pool utilization, ARQ queue depth

### Uptime monitoring (pick ONE)
- [ ] **UptimeRobot** (free, 5-min checks): hit
      `https://api.daily-life-pharmacy.com.bd/api/v1/health` every
      5 min, alert via email + SMS on failure
- [ ] **Better Stack / Pingdom** (paid, 30-sec checks): same idea,
      tighter intervals + a status page
- [ ] Confirm alerts fire when you intentionally `docker compose stop api`
- [ ] Configure escalation: who gets paged at what time?

**Exit criteria:** ✅ you find out about an outage within 5 minutes,
and you can debug it from logs in your aggregator without SSH-ing
into the container.

---

## Stage 6 — Seed real catalog data + rerun load test

- [ ] Import real supplier list (CSV → `inventory.suppliers` table)
- [ ] Import real product catalog (10K+ rows) via the catalog admin
      API or direct SQL with proper relationships
- [ ] Verify SEO data: `/api/v1/seo/meta/product/{real_product_id}`
      returns a complete bundle with price + image + InStock
- [ ] Verify sitemap: `/sitemap.xml` lists all the products
- [ ] Re-run k6 baseline: `make loadtest-baseline API=https://api... EMAIL=... PASSWORD=...`
- [ ] Confirm SLO thresholds met: p95 < 500ms, error rate < 1%
- [ ] If thresholds fail, EXPLAIN_ANALYZE the slow queries and add
      indexes BEFORE going further

**Exit criteria:** ✅ load test passes against a populated DB
(not the empty one CI tested). Real query plans differ.

---

## Stage 7 — Closed pilot (2 weeks, 10-50 real customers)

- [ ] Pick a single Dhaka neighborhood / a small set of doctors who
      know they're piloting
- [ ] Set up a separate "pilot" environment OR keep using staging
      under a single-flag rollout
- [ ] Daily ops standup: any errors? any customer complaints? any
      financial discrepancies?
- [ ] Watch the rider wallet ledger every morning — does the
      yesterday-COD-collected match yesterday's actual collections?
- [ ] Watch the supplier-payment audit feed — every approval logged?
      Every payment proof uploaded?
- [ ] Confirm reporting works: run weekly P&L, weekly delivery
      throughput, weekly COD outstanding — do the numbers reconcile
      with what your accountant expects?
- [ ] Confirm SEO: `site:daily-life-pharmacy.com.bd` in Google after
      sitemap submission shows your URLs being indexed within a week

**Exit criteria:** ✅ 2 weeks of real operation with no money lost,
no customer wronged, no audit-trail gap. If ANYTHING wrong happens,
fix it before scaling up.

---

## Stage 8 — Production launch

Now and only now:
- [ ] Set `BKASH_IS_SANDBOX=false` (and same for SSL, APNS, etc.)
- [ ] Update `CORS_ORIGINS` to include the real customer domain
- [ ] Set `ENVIRONMENT=production` in `.env`
- [ ] Final restart: `make prod-down && make prod-up-tls`
- [ ] Announce the launch
- [ ] Pin a small ops team to watch the first 48 hours

---

## Per-incident playbook

Keep this near at hand:

- API 5xx spike → Datadog/Loki dashboard, look for `error.code`
- Worker stalled (jobs stuck in `pending`) →
  `docker compose logs worker | tail -200`, restart worker
- Migration needed → `make prod-migrate` (idempotent)
- IAM seed drifted → `make prod-bootstrap` (idempotent)
- Disk filling up → check `hypershop_pg_backups` volume size,
  prune old `*.dump` files (keep last 30 days)

---

## Honest cost estimate

If you compress every stage to its minimum viable execution:
- Stage 0 (push + CI): 30 min
- Stage 1 (staging soak): 1 week (calendar time, not work hours)
- Stage 2 (provider creds): 1-3 weeks (depends on Bkash + SMS gateway)
- Stage 3 (cred wiring + smoke): 1 day
- Stage 4 (backup test): half-day
- Stage 5 (logs + uptime): half-day
- Stage 6 (data + load): 1-2 days (depends on data volume)
- Stage 7 (pilot): 2 weeks (calendar)
- Stage 8 (launch): 1 day

**Realistic total: 5-7 weeks from "code in main" to "real customers".**

Don't compress this. The compression is what creates the 3am incidents.
