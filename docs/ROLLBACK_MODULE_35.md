# Hypershop — Module 35 (Product Page Video) Rollback Runbook

**Version:** 1.0
**Pairs with:** `docs/PRODUCTION_READINESS.md` (Gate 9 references this file)
**Audience:** on-call engineer at 2 AM. Optimised for fast, copy-paste execution under pressure.

---

## TL;DR — pick a scenario

| Symptom | Scenario | TTR target |
|---|---|---|
| API errors / 5xx on Module 35 endpoints, but DB schema is fine | **A — Code-only revert** | < 10 min |
| Schema drift / migration broken / DB rows corrupting | **B — Full revert incl. DB** | < 20 min |
| External CDN (Bunny) outage only — code is fine | **C — Soft-disable feature** (router-include comment-out) | < 5 min |

If you can't decide in 60 seconds, do **A first**. It's safe, reversible, and buys you time to think about whether B is needed.

---

## Section 1 — When to invoke this runbook

Hit rollback if **any one** of these is true:

- Module 35 endpoints returning ≥ 5% 5xx rate for > 5 minutes
- Customer reports of broken product pages where videos render
- Worker container crash-looping (FFmpeg / R2 / Bunny error spam)
- Auditor / compliance flag on customer data exposed via the wrong endpoint
- A migration left the DB in a half-applied state (alembic_version ≠ HEAD)
- Bunny / R2 bill spike caused by a runaway job

Do NOT hit rollback for:
- A single 5xx burst (transient — page the on-call, watch 5 min, decide)
- Frontend visual glitch only (frontend-only revert is usually enough)
- Cron-job miss (`cleanup_raw_originals_job` skipped a tick — reschedule, not rollback)

---

## Section 2 — Pre-rollback checklist (90 seconds)

Before touching anything, capture state. Future-you in the postmortem needs this.

```bash
# 1. Snapshot the alembic revision
docker compose exec -T api alembic current > /tmp/rollback-alembic-pre.txt

# 2. Snapshot ARQ queue depth
docker compose exec -T redis redis-cli -n 0 LLEN arq:queue:hypershop:jobs > /tmp/rollback-queue-pre.txt

# 3. Snapshot Module 35 row counts
docker compose exec -T postgres psql -U hypershop -d hypershop -t -c "
SELECT 'product_videos:' AS k, status, count(*) FROM product_videos GROUP BY status
UNION ALL
SELECT 'video_events:'  AS k, event_type, count(*) FROM video_events GROUP BY event_type
ORDER BY 1, 2;
" > /tmp/rollback-counts-pre.txt

# 4. Save logs (last 500 lines per service)
docker compose logs --tail 500 api    > /tmp/rollback-api-log.txt
docker compose logs --tail 500 worker > /tmp/rollback-worker-log.txt

# 5. Note the broken release tag
git -C hypershop_fastapi_backend/hypershop-backend rev-parse HEAD > /tmp/rollback-bad-sha.txt
```

Tar these up and attach to the incident ticket — even if the rollback succeeds, the postmortem needs them.

---

## Section 3 — Identify the previous good release

Pick ONE of these depending on your CI/CD setup.

**Git-tag based (recommended):**
```bash
cd hypershop_fastapi_backend/hypershop-backend
git tag --sort=-committerdate | head -5
# Output e.g.:
#   v0.43.0     ← current (broken)
#   v0.42.1     ← previous good, target for rollback
#   v0.42.0
PREV_TAG=v0.42.1
```

**Image-registry based:**
```bash
docker images hypershop-api --format "table {{.Tag}}\t{{.CreatedAt}}" | head -5
PREV_IMAGE_TAG=v0.42.1
```

**No tags (hotfix branch):**
Find the last commit before the Module 35 merge:
```bash
git log --oneline --grep="Module 35\|product_video" | tail -1
PREV_SHA=<that commit>
```

Write the chosen target into the ticket. Every command below assumes `PREV_TAG` (or `PREV_IMAGE_TAG` / `PREV_SHA`) is exported.

---

## Section 4 — Scenario A: Code-only revert

**Use when:** the code is broken but the schema is fine. The 0030 + 0031 migrations stay applied; the unused tables sit idle until a forward fix.

### A.1 — Revert the running images

**With registry-pushed images:**
```bash
cd hypershop_fastapi_backend/hypershop-backend
sed -i.bak "s/image: hypershop-api:.*/image: hypershop-api:${PREV_IMAGE_TAG}/"     docker-compose.prod.yml
sed -i.bak "s/image: hypershop-worker:.*/image: hypershop-worker:${PREV_IMAGE_TAG}/" docker-compose.prod.yml
docker compose -f docker-compose.prod.yml pull api worker
docker compose -f docker-compose.prod.yml up -d --no-deps api worker
```

**Local-build setup (compose `build:` instead of `image:`):**
```bash
cd hypershop_fastapi_backend/hypershop-backend
git stash       # save uncommitted work
git checkout ${PREV_TAG}
docker compose -f docker-compose.prod.yml up -d --build api worker
```

### A.2 — Revert the frontend

The customer-web build is independent. If you deploy via Vercel / static export / your own pipeline:

```bash
cd "hypershop-Frontend final"
git checkout ${PREV_TAG}
npm install
npm run build
# Deploy via your normal frontend pipeline (vercel deploy, rsync, etc.)
```

If frontend rollback is delayed, the fallback is graceful:
- `videosApi.listForProduct()` will hit the now-removed endpoint → returns ApiError
- `ProductVideoPlayer` catches the error → renders nothing
- The PDP stays functional; the video rail just disappears

### A.3 — Smoke verify

After image + frontend revert, confirm the system is back. **All these MUST be true.**

```bash
# Module 35 endpoints should now return 404 (route gone)
for url in \
  "/api/v1/products/00000000-0000-0000-0000-000000000000/videos" \
  "/api/v1/product-videos/products/00000000-0000-0000-0000-000000000000/upload" \
  "/api/v1/product-videos/00000000-0000-0000-0000-000000000000/event" \
  "/api/v1/admin/product-videos/pending"
do
  code=$(curl -s -o /dev/null -w "%{http_code}" "${API_BASE_URL}${url}")
  echo "$code  $url"
done
# Expected: 404 / 405 on every line (NOT 200, NOT 500)

# Other endpoints still healthy
curl -fsS "${API_BASE_URL}/api/v1/health" | head -1   # must contain "ok"
curl -fsS "${API_BASE_URL}/api/v1/catalog/products?size=1" >/dev/null && echo "catalog OK"
```

### A.4 — Storage disposition

**Default: KEEP everything on R2 + Bunny.** Storage is cheap, re-uploads are expensive, and if the rollback turns out to be temporary you'll need the data back.

The `hypershop_product_videos` Docker volume also stays — no `docker volume rm`.

Only purge if compliance / data-sensitivity demands it:

```bash
# DANGER — irreversible. Read twice before running.
docker compose exec -T worker python -c "
import boto3, os
c = boto3.client('s3',
    endpoint_url=f\"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com\",
    aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
    region_name='auto')
print('listing under raw prefix...')
# (List + DELETE loop — left as exercise; not in default runbook because
#  irreversible. Page the data-protection lead before running.)
"
```

For Bunny: dashboard → Storage Zones → Files → bulk delete. Do NOT script unless you have explicit ACL on the storage zone.

---

## Section 5 — Scenario B: Full revert including DB

**Use when:** Scenario A isn't enough — the schema itself is the problem (corrupted rows, broken FK, half-applied migration).

### B.1 — Drain the worker first

You don't want a half-rolled-back system to leave jobs stranded.

```bash
# Stop the worker; api stays up so customers can still use other features
docker compose stop worker

# Drain ARQ queue — jobs that are mid-processing will get reaped on
# next worker start; new jobs queue up but go nowhere
docker compose exec -T redis redis-cli -n 0 DEL arq:queue:hypershop:jobs

# Stop the api too (brief outage on Module 35 routes — they'll 404 anyway)
docker compose stop api
```

### B.2 — Run the alembic downgrade

```bash
# We want to drop both 0031 (timeline columns) and 0030 (product_videos +
# video_events tables). Going by explicit revision is safer than -2.

# Spin up a one-shot api container against the same DB just for migration:
docker compose run --rm --entrypoint "" api \
    alembic downgrade 0030_product_videos      # drops 0031: approved_at, disabled_at, reopened_at, ix_product_videos_updated_at

docker compose run --rm --entrypoint "" api \
    alembic downgrade 0029_rider_cod_recharge  # drops 0030: product_videos + video_events tables (CASCADE on FKs)

# Confirm:
docker compose run --rm --entrypoint "" api alembic current
# Expected: 0029_rider_cod_recharge (head)
```

**Data loss: confirmed and accepted.** Both migrations have working `downgrade()` blocks (verified at write time). The rows are gone forever — they're recreated only by re-running upgrades + re-uploading videos.

### B.3 — Now do Scenario A.1, A.2, A.3

The image revert + frontend revert + smoke verify steps are identical to Scenario A. Run them after the downgrade.

### B.4 — Confirm no orphan refs

```bash
docker compose exec -T postgres psql -U hypershop -d hypershop -c "
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('product_videos', 'video_events');
"
# Expected: 0 rows. Both tables are gone.

docker compose exec -T postgres psql -U hypershop -d hypershop -c "
SELECT count(*) AS audit_orphans FROM audit_logs
WHERE resource_type = 'product_video';
"
# Expected: count > 0 is FINE. Audit rows survive table drops on purpose
# (so the postmortem has the full transition history). They don't have a
# FK to product_videos.id, so dropping the table doesn't cascade-delete
# them.
```

---

## Section 6 — Scenario C: Soft-disable feature (no redeploy)

**Use when:** Bunny / R2 is degraded and you want to stop NEW uploads from queuing up failures, but you don't want a full code revert. This is a hold pattern, not a fix.

### C.1 — Comment out the router includes

Edit `app/main.py` in the running deployment OR push a hotfix commit:

```python
# from app.modules.product_videos.api import product_videos_router
# from app.modules.product_videos.router import router as product_videos_upload_router
# from app.modules.product_videos.admin_router import router as product_videos_admin_router
# from app.modules.product_videos.public_router import router as product_videos_public_router
```

Comment out the matching `app.include_router(...)` lines too.

### C.2 — Restart the api only

```bash
docker compose restart api
```

The worker keeps running (drains the existing queue but accepts no new uploads since the upload endpoint is gone). All Module 35 endpoints 404. DB is untouched. Storage is untouched.

### C.3 — Re-enable

When the underlying issue (Bunny outage, R2 throttling, etc.) is resolved:

```bash
git revert <the comment-out commit>
docker compose up -d --build api
```

Worker picks up any queued FFmpeg jobs; admin can resume moderation; public list resumes.

---

## Section 7 — Verification matrix

Run this after every scenario. Every line must match the expected column.

| Check | Command | Expected after rollback |
|---|---|---|
| Health | `curl ${API}/api/v1/health` | `200 {"status":"ok"}` |
| Module 35 public list | `curl ${API}/api/v1/products/<any>/videos` | `404` |
| Module 35 upload | `curl -X POST ${API}/api/v1/product-videos/products/<any>/upload` | `404` or `405` |
| Module 35 event | `curl -X POST ${API}/api/v1/product-videos/<any>/event` | `404` |
| Admin pending | `curl -H "Authorization: Bearer <admin>" ${API}/api/v1/admin/product-videos/pending` | `404` |
| Catalog list | `curl ${API}/api/v1/catalog/products?size=1` | `200` (proves rollback only removed Module 35) |
| Login | `curl -X POST ${API}/api/v1/auth/login -d '...'` | `200 / 401` (must NOT be 5xx) |
| ARQ worker | `docker compose ps worker` | `Up (healthy)` |
| Migrations | `alembic current` | `0029_rider_cod_recharge` (B) or `0031_product_videos_timeline` (A) |
| Frontend PDP | open `https://hypershop.example/products/<id>` | renders, no console errors, no video rail |

If any row fails, **the rollback is not complete** — escalate before declaring the incident over.

---

## Section 8 — Customer communication template

Post these on the customer-facing channels (status page, Facebook, in-app banner). One short message; do not over-explain.

**English:**
> 🛠️ Product video previews are temporarily unavailable. We expect to restore them within 24 hours. All other shopping features are working normally — your orders, cart, payments, and delivery are unaffected. Thank you for your patience.

**Bangla:**
> 🛠️ প্রোডাক্ট ভিডিও প্রিভিউ সাময়িকভাবে দেখা যাচ্ছে না। ২৪ ঘণ্টার মধ্যে ঠিক করে দিব। অর্ডার, কার্ট, পেমেন্ট, ডেলিভারি — সব স্বাভাবিকভাবে চলছে। ধৈর্য্যের জন্য ধন্যবাদ।

If the rollback is wider than just videos (rare), escalate to comms lead — do NOT freelance.

For sellers who already uploaded:
> Your uploaded video is preserved on our servers. Once we restore the video preview feature, your video will go through the same approval process. No action needed from your side.

---

## Section 9 — Post-rollback hygiene

Do these within 24 hours of rollback closing.

- [ ] **Incident ticket** — link the pre-rollback snapshot (Section 2 outputs), the chosen scenario, the rollback commit / image tag, the customer-comm timestamps.
- [ ] **Blameless postmortem** — schedule within 5 business days; include the team-member who shipped the broken release as an equal participant, not the focus.
- [ ] **Root cause** — exactly which change broke production. Was it caught by `PRODUCTION_READINESS.md` Gate N? If yes, why was the gate skipped / signed off without evidence? If no, **add a new gate** for the missed condition.
- [ ] **Storage audit** — confirm R2 / Bunny were not purged by mistake. Document size + bandwidth used during the broken-release window for cost analysis.
- [ ] **Forward-fix plan** — bug fix branch, retry of `PRODUCTION_READINESS.md` from Gate 1, target date for re-deploy.
- [ ] **Update this runbook** — if a step in this doc was unclear or wrong, fix it BEFORE the next deploy. Future-you will thank current-you.

---

## Section 10 — What this runbook does NOT cover

Out of scope, escalate to the relevant runbook:

- **General Hypershop API outage** — `docs/INCIDENT_RUNBOOK.md` (if it exists; otherwise on-call ops lead)
- **Postgres / Redis infra failure** — `docs/INFRA_RUNBOOK.md`
- **Domain / DNS / TLS** — DNS provider runbook
- **Cloudflare R2 service outage on their side** — wait + status page; rollback won't help
- **Bunny.net service outage on their side** — Scenario C (soft-disable) is the right tool; full revert is overkill
- **Other modules** (rider routing, supplier payments, etc.) — they have their own rollback runbooks (or should)

---

## Section 11 — Quick-reference card

Tear off and tape to your monitor:

```
┌────────────────────────────────────────────────────────┐
│ MODULE 35 ROLLBACK — Quick Card                        │
├────────────────────────────────────────────────────────┤
│ 1. Capture state (Section 2 — 90 sec)                  │
│ 2. Pick scenario:                                      │
│    A = code-only,     ~10 min, schema stays            │
│    B = full incl DB,  ~20 min, schema reverts          │
│    C = soft-disable,   ~5 min, holding pattern         │
│ 3. Find PREV_TAG (Section 3)                           │
│ 4. Execute scenario (Sections 4 / 5 / 6)               │
│ 5. Run verification matrix (Section 7) — all rows ✓    │
│ 6. Post customer comms (Section 8)                     │
│ 7. File hygiene tasks (Section 9)                      │
│                                                        │
│ Storage default: KEEP. Don't purge unless required.    │
│ Audit rows survive table drops — by design.            │
└────────────────────────────────────────────────────────┘
```

---

## Appendix A — Migration reference

For Section 5 / B.2 — the exact migrations being downgraded:

| Revision | File | What it does on upgrade |
|---|---|---|
| `0030_product_videos` | `alembic/versions/2026_05_10_0030-0030_product_videos.py` | Creates `product_videos` + `video_events` tables, indexes, FKs |
| `0031_product_videos_timeline` | `alembic/versions/2026_05_10_0031-0031_product_videos_timeline_fields.py` | Adds `approved_at`, `disabled_at`, `reopened_at` columns + `ix_product_videos_updated_at` |

Both downgrade scripts were authored alongside the upgrade and are tested by the project's standard alembic dry-run (re-run `alembic upgrade head` after a downgrade should be no-op-clean).

---

## Appendix B — Files touched by Module 35 (for revert review)

When reviewing what `git checkout ${PREV_TAG}` will undo:

**Backend:**
- `app/main.py` — router includes
- `app/worker.py` — function + cron registrations
- `app/core/db/registry.py` — model imports
- `app/core/config.py` — env settings
- `app/core/queue.py` — ARQ pool helper (new file)
- `app/modules/product_videos/**` — entire module (new)
- `alembic/versions/2026_05_10_0030-*.py` and `2026_05_10_0031-*.py` (new)
- `Dockerfile` + `Dockerfile.worker` — boto3 / ffmpeg installs
- `docker-compose.yml` + `docker-compose.prod.yml` — volumes-init, env vars, volume mount
- `.env.example` + `.env.prod.example` — env documentation
- `pyproject.toml` — boto3 dep
- `scripts/smoke_test_video.sh` (new)
- `docs/PRODUCTION_READINESS.md` + `docs/ROLLBACK_MODULE_35.md` (new)
- `README.md` — smoke test section

**Frontend (`hypershop-Frontend final/`):**
- `components/ProductVideoPlayer.tsx` (new)
- `app/products/[id]/page.tsx` — dynamic import + injection
- `lib/api/videos.ts` (new) + `lib/api/index.ts` — exports + facade
- `package.json` — hls.js + Vitest devDeps + scripts
- `vitest.config.ts` + `test-setup.ts` (new)
- `__tests__/ProductVideoPlayer.test.tsx` (new)

`git checkout ${PREV_TAG}` reverts every one of these atomically. No partial state.
