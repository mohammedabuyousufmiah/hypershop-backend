# Hypershop — Module 35 (Product Page Video) Production Readiness

**Version:** 1.0 (Module 35 launch)
**Owner:** _fill in deploy lead name + role_
**Pairs with:** `docs/ROLLBACK_MODULE_35.md` (must exist before any live deploy)

---

## Purpose

This document is **the only gate** between staging-deployed code and customer-live traffic for the Product Page Video pipeline. It exists because "code-complete + tests authored" is _not_ the same as "production-ready" — readiness is **green test results from a real stack**, signed off by a human.

Use this doc as a fillable checklist. Run each gate in the order below. **If any gate fails or is skipped without justification, do not flip the customer-live switch.**

## How to use

1. Copy this file into your release ticket / wiki page (one copy per environment per release).
2. Run each gate's "Verify" step in your **staging** environment first.
3. Paste/attach the actual output under "Evidence".
4. Mark `[x] PASS`, `[ ] FAIL`, or `[ ] N/A (with reason)` and sign + date.
5. **All 10 gates green → cleared for limited live release.**
6. Repeat the relevant gates against production after the limited rollout, before full traffic.

## Outcome legend

- ✅ **PASS** — verified output matches "Expected" with no caveats
- ❌ **FAIL** — verification did not produce expected output; do not proceed
- ⚠️ **PARTIAL** — passed with caveats (note them); requires lead override to proceed
- ⏸️ **N/A** — gate genuinely doesn't apply this release (justify in notes)

---

# Gates 1–10

---

## Gate 1 — End-to-end smoke test PASSES

**What it proves:** the full pipeline (upload → ARQ enqueue → FFmpeg → Bunny upload → admin approve → public list → CDN playable) works as one unit against a stack matching the deploy target.

**Pre-reqs (set in your shell):**
```bash
export API_BASE_URL=https://staging-api.hypershop.example
export SMOKE_PRODUCT_ID=<existing product UUID in this env>
export SMOKE_ADMIN_EMAIL=<staging admin>
export SMOKE_ADMIN_PASSWORD=<staging admin password>
# OR for CI:
# export SMOKE_ADMIN_TOKEN=<bearer>
```

**Verify:**
```bash
cd hypershop_fastapi_backend/hypershop-backend
bash scripts/smoke_test_video.sh
echo "exit=$?"
```

**Expected output (final block):**
```
═══════════════════════════════════════════════════
  All smoke checks passed ✓
  video_id     = <uuid>
  product_id   = <uuid>
  duration_s   = 3
  hls_url      = https://...b-cdn.net/.../master.m3u8     ← Bunny URL in prod
  thumbnail    = https://...b-cdn.net/.../poster.jpg
═══════════════════════════════════════════════════
exit=0
```

`exit=0` is the only result that counts as PASS. The 9 internal `✓` lines must all print.

**Sign-off**
- [ ] Result: ☐ PASS  ☐ FAIL  ☐ PARTIAL  ☐ N/A
- Verified by: _______________________
- Environment: _______________________
- Date (UTC): _______________________
- Evidence (paste log tail or attach file): _______________________
- Notes / caveats: _______________________

---

## Gate 2 — Backend integration tests PASS

**What it proves:** the 11 product_videos integration tests still pass against the same DB / Redis the staging stack uses. Catches schema drift, FK breakage, validation regression.

**Verify:**
```bash
cd hypershop_fastapi_backend/hypershop-backend
make test-int                                                        # full suite
# or specifically:
pytest -v app/modules/product_videos/tests/                          # 11 tests
```

**Expected output (last line of pytest):**
```
=========== 11 passed, 0 failed, 0 errors in <N>s ===========
```

Specific tests that **must** be in the passing list:
- `test_upload_rejects_invalid_extension`
- `test_upload_rejects_invalid_mime`
- `test_upload_rejects_oversized_file`
- `test_upload_creates_product_video_row`
- `test_max_three_approved_videos_per_product`
- `test_pending_video_not_in_public_list`
- `test_approved_video_in_public_list`
- `test_rejected_video_hidden_from_public`
- `test_disabled_video_hidden_from_public`
- `test_raw_object_key_never_in_public_response`
- `test_event_tracking_only_for_approved_videos`

**Sign-off**
- [ ] Result: ☐ PASS  ☐ FAIL  ☐ PARTIAL  ☐ N/A
- Verified by: _______________________
- Environment: _______________________
- Date (UTC): _______________________
- Evidence (pytest output paste): _______________________
- Notes / caveats: _______________________

---

## Gate 3 — Frontend Vitest tests PASS

**What it proves:** the customer-web `ProductVideoPlayer` renders correctly in jsdom — thumbnail-first, lazy HLS load, muted-before-play, error-safe.

**Verify:**
```bash
cd "hypershop-Frontend final"
npm install                                                          # one-time per change
npm test
echo "exit=$?"
```

**Expected output (Vitest summary):**
```
 Test Files  1 passed (1)
      Tests  9 passed (9)
   Duration  <N>s
```

`exit=0` required. All 9 tests must pass — no skipped, no todo.

**Sign-off**
- [ ] Result: ☐ PASS  ☐ FAIL  ☐ PARTIAL  ☐ N/A
- Verified by: _______________________
- Date (UTC): _______________________
- Evidence (Vitest summary paste): _______________________
- Notes / caveats: _______________________

---

## Gate 4 — Docker Compose clean start

**What it proves:** the full stack (postgres + redis + mailpit + volumes-init + api + worker) comes up healthy from a cold state. Catches volume permission regressions, image build failures, dependency-order issues.

**Verify (run from a fresh shell, no warm caches):**
```bash
cd hypershop_fastapi_backend/hypershop-backend
docker compose down -v                                               # destroys volumes — clean start
docker compose up -d --build
sleep 30                                                             # let migrations + bootstrap finish
docker compose ps
curl -fsS http://localhost:8000/api/v1/health
docker compose logs volumes-init | tail -3
docker compose logs worker | grep -E "worker_startup|ARQ"
```

**Expected output:**
- `docker compose ps` shows every service in `Up` state with `(healthy)` for postgres / redis / api
- `volumes-init` shows status `Exited (0)` (one-shot, success)
- `curl /health` returns HTTP 200 with body containing `"status":"ok"`
- `volumes-init` log tail contains `volume perms ok`
- `worker` log contains `worker_startup` and ARQ banner

**Sign-off**
- [ ] Result: ☐ PASS  ☐ FAIL  ☐ PARTIAL  ☐ N/A
- Verified by: _______________________
- Compose file used: ☐ docker-compose.yml  ☐ docker-compose.prod.yml
- Date (UTC): _______________________
- Evidence (`docker compose ps` paste + curl response): _______________________
- Notes / caveats: _______________________

---

## Gate 5 — Real R2 + Bunny credentials roundtrip

**What it proves:** the configured R2 bucket and Bunny Storage zone are reachable, accept writes, and serve reads through the configured Pull Zone. Catches misconfigured access keys, wrong bucket region, missing CORS, mis-attached pull zone.

**Pre-reqs (in `.env` of the target stack):**
```
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET_NAME=...
R2_PUBLIC_BASE_URL=https://r2.hypershop.example   # optional, for direct R2 reads
BUNNY_STORAGE_ZONE_NAME=...
BUNNY_STORAGE_ACCESS_KEY=...
BUNNY_STORAGE_REGION=sg                            # `sg` for BD
BUNNY_PULL_ZONE_HOSTNAME=hypershop-vid.b-cdn.net
```

**Verify (R2 roundtrip — runs inside worker container):**
```bash
docker compose exec -T worker python -c "
from app.modules.product_videos.storage import (
    upload_private_file, download_private_file, delete_object, private_key,
)
from pathlib import Path
import tempfile, secrets

src = Path(tempfile.mkstemp(suffix='.bin')[1])
src.write_bytes(b'r2-roundtrip-' + secrets.token_hex(8).encode())
key = private_key(f'_readiness/r2-roundtrip-{secrets.token_hex(4)}.bin')

upload_private_file(src, key, 'application/octet-stream')
dst = Path(tempfile.mkstemp()[1])
download_private_file(key, dst)
assert src.read_bytes() == dst.read_bytes(), 'R2 roundtrip MISMATCH'
delete_object(key)
print('R2 ROUNDTRIP OK')
"
```

**Expected R2 output:**
```
R2 ROUNDTRIP OK
```

**Verify (Bunny roundtrip):**
```bash
docker compose exec -T worker python -c "
from app.modules.product_videos.storage import (
    bunny_upload_public_file, bunny_delete_object, bunny_public_url,
)
from pathlib import Path
import tempfile, secrets, urllib.request

src = Path(tempfile.mkstemp(suffix='.txt')[1])
body = b'bunny-readiness-' + secrets.token_hex(8).encode()
src.write_bytes(body)
sub = f'_readiness/bunny-{secrets.token_hex(4)}.txt'

url = bunny_upload_public_file(src, sub, 'text/plain')
print('uploaded:', url)
fetched = urllib.request.urlopen(url, timeout=10).read()
assert fetched == body, 'Bunny CDN mismatch'
bunny_delete_object(sub)
print('BUNNY ROUNDTRIP OK')
"
```

**Expected Bunny output:**
```
uploaded: https://hypershop-vid.b-cdn.net/product-videos/_readiness/bunny-<hex>.txt
BUNNY ROUNDTRIP OK
```

**Sign-off**
- [ ] R2 roundtrip: ☐ PASS  ☐ FAIL
- [ ] Bunny roundtrip: ☐ PASS  ☐ FAIL
- Verified by: _______________________
- Environment: _______________________
- Date (UTC): _______________________
- Evidence (paste both outputs): _______________________
- Notes / caveats: _______________________

---

## Gate 6 — FFmpeg HLS output playable in real browser

**What it proves:** the master playlist + segments produced by the worker actually play. jsdom + curl can verify HTTP 200 and `#EXTM3U`, but only a real browser proves codec compatibility, MIME negotiation, and HLS.js / native HLS interop.

**Pre-reqs:** Gate 1 must have already produced an approved video; copy its `hls_url` from the smoke test final block.

**Verify (automated HTTP check):**
```bash
HLS_URL='<paste from gate 1 output>'
curl -fsSI "$HLS_URL" | head -5
curl -fsS "$HLS_URL" | head -3
```

**Expected automated output:**
```
HTTP/2 200
content-type: application/vnd.apple.mpegurl
...

#EXTM3U
#EXT-X-VERSION:7
#EXT-X-STREAM-INF:BANDWIDTH=1300000,RESOLUTION=1280x720,...
```

**Verify (manual browser check) — required, cannot be automated cheaply:**

| Browser | Device | Test |
|---|---|---|
| Chrome desktop (latest) | Linux/Win | Open the staging PDP for `SMOKE_PRODUCT_ID`. Verify thumbnail renders. Tap play. Verify video plays muted. Unmute via controls. Audio audible. |
| Safari iOS (real device, not simulator) | iPhone | Same flow. Native HLS path. Verify lo-power-mode unmute works. |
| Chrome Android (real device) | Pixel / Samsung | Same flow. HLS.js path on stock Android browser. |

**Sign-off**
- [ ] HTTP 200 + valid `#EXTM3U`: ☐ PASS  ☐ FAIL
- [ ] Chrome desktop play: ☐ PASS  ☐ FAIL
- [ ] Safari iOS play (real device): ☐ PASS  ☐ FAIL
- [ ] Chrome Android play (real device): ☐ PASS  ☐ FAIL
- Verified by: _______________________
- Date (UTC): _______________________
- Evidence (screenshots / video link): _______________________
- Notes / caveats: _______________________

---

## Gate 7 — Admin moderation audit log verified

**What it proves:** every approve / reject / disable / reopen call writes a complete audit row in the same DB transaction as the state change. Catches missing audit hooks, wrong actor capture, and metadata-redaction bugs.

**Pre-reqs:** at least one video each in `approved`, `rejected`, `reopened` states, freshly transitioned in the test environment within the last hour.

**Verify (run against staging Postgres):**
```bash
docker compose exec -T postgres psql -U hypershop -d hypershop -c "
SELECT
  action,
  resource_id,
  actor_kind,
  actor_id IS NOT NULL AS has_actor,
  metadata_->>'product_id' AS product_id,
  metadata_->>'reason' AS reason,
  metadata_->>'reopen_reason' AS reopen_reason,
  metadata_->>'previous_rejection_reason' AS prev_rejection_reason,
  created_at
FROM audit_logs
WHERE action LIKE 'product_video.%'
  AND created_at > now() - interval '1 hour'
ORDER BY created_at DESC
LIMIT 20;
"
```

**Expected output rows (must include all of these action codes):**
- `product_video.uploaded` — has_actor `t`, product_id present, file size + raw key in metadata
- `product_video.processed` — has_actor `f` (worker, not user), duration_seconds + hls_url present
- `product_video.approved` — has_actor `t`, product_id present
- `product_video.rejected` — has_actor `t`, reason present
- `product_video.reopened` — has_actor `t`, reopen_reason present, **prev_rejection_reason present** (the snapshot rule from turn 12)
- `product_video.disabled` — has_actor `t` (when triggered), reason optional

If `product_video.reopened` row exists but `prev_rejection_reason` is NULL → **FAIL** (regression on the audit-snapshot rule).

**Sign-off**
- [ ] All 5 lifecycle action codes present in last 1h: ☐ PASS  ☐ FAIL
- [ ] reopened row has previous_rejection_reason populated: ☐ PASS  ☐ FAIL
- [ ] No empty-actor rows for admin actions: ☐ PASS  ☐ FAIL
- Verified by: _______________________
- Date (UTC): _______________________
- Evidence (psql output paste): _______________________
- Notes / caveats: _______________________

---

## Gate 8 — Upload size / type / security validation

**What it proves:** the upload endpoint rejects every adversarial input we anticipate. Catches regressions in the validation chain that integration tests sometimes miss when they don't mirror real HTTP framing.

**Verify (against the running stack — replace `<token>` and `<product>`):**
```bash
API="https://staging-api.hypershop.example"
T="<bearer token>"
P="<product UUID>"

# 8a — no auth → 401
echo "8a:"; curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "$API/api/v1/product-videos/products/$P/upload" -F "file=@/etc/hostname"

# 8b — wrong extension → 422 product_video_unsupported_type
echo "8b:"; curl -s -X POST "$API/api/v1/product-videos/products/$P/upload" \
  -H "Authorization: Bearer $T" -F "file=@/etc/hostname" | jq -c '.code'

# 8c — wrong MIME on .mp4 → 422 product_video_unsupported_type
echo "8c:"; head -c 1024 /dev/urandom > /tmp/fake.mp4
curl -s -X POST "$API/api/v1/product-videos/products/$P/upload" \
  -H "Authorization: Bearer $T" \
  -F "file=@/tmp/fake.mp4;type=image/png" | jq -c '.code'

# 8d — oversize (set PRODUCT_VIDEO_MAX_SIZE_MB=1 in env first, then send 2 MB)
head -c 2097152 /dev/urandom > /tmp/big.mp4
echo "8d:"; curl -s -X POST "$API/api/v1/product-videos/products/$P/upload" \
  -H "Authorization: Bearer $T" \
  -F "file=@/tmp/big.mp4;type=video/mp4" | jq -c '{code,details:.details.max_mb}'

# 8e — non-existent product → 404
echo "8e:"; head -c 1024 /dev/urandom > /tmp/ok.mp4
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "$API/api/v1/product-videos/products/00000000-0000-0000-0000-000000000000/upload" \
  -H "Authorization: Bearer $T" \
  -F "file=@/tmp/ok.mp4;type=video/mp4"

# 8f — non-admin token → 403
NORMAL="<token of a customer-only user>"
echo "8f:"; curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "$API/api/v1/product-videos/products/$P/upload" \
  -H "Authorization: Bearer $NORMAL" \
  -F "file=@/tmp/ok.mp4;type=video/mp4"
```

**Expected output:**
```
8a: 401
8b: "product_video_unsupported_type"
8c: "product_video_unsupported_type"
8d: {"code":"product_video_file_too_large","details":1}
8e: 404
8f: 403
```

**Sign-off**
- [ ] 8a (no auth → 401): ☐ PASS  ☐ FAIL
- [ ] 8b (wrong ext → 422): ☐ PASS  ☐ FAIL
- [ ] 8c (wrong MIME → 422): ☐ PASS  ☐ FAIL
- [ ] 8d (oversize → 422): ☐ PASS  ☐ FAIL
- [ ] 8e (missing product → 404): ☐ PASS  ☐ FAIL
- [ ] 8f (non-admin → 403): ☐ PASS  ☐ FAIL
- Verified by: _______________________
- Date (UTC): _______________________
- Evidence (curl output paste): _______________________
- Notes / caveats: _______________________

---

## Gate 9 — Rollback plan ready and desk-checked

**What it proves:** if anything goes wrong post-deploy, an on-call engineer has a pre-written, time-bounded path back to the previous good state. Without this, the team is forced to improvise under pressure — that's how outages compound.

**Verify:**
- [ ] `docs/ROLLBACK_MODULE_35.md` exists in the repo (HEAD of release branch)
- [ ] Rollback runbook covers: alembic downgrade sequence, R2/Bunny content disposition, image revert command, frontend revert (component + page injection + lib/api/videos), endpoint removal smoke check, customer-comm template
- [ ] At least ONE engineer who is NOT the author has read it end-to-end and can describe the steps without re-reading
- [ ] Estimated rollback time (TTR) is documented and < 15 minutes for a code-only rollback (longer if data migration was non-trivial — flag explicitly)

**Verification command (existence + freshness check):**
```bash
test -f docs/ROLLBACK_MODULE_35.md && \
  grep -E "alembic downgrade|R2|Bunny|TTR" docs/ROLLBACK_MODULE_35.md | head -5
```

**Expected output:** at least one matching line for each grep token.

**Sign-off**
- [ ] Runbook exists: ☐ PASS  ☐ FAIL
- [ ] Reviewed by non-author: _______________________ (name)
- [ ] TTR documented: ___ minutes
- Verified by: _______________________
- Date (UTC): _______________________
- Notes / caveats: _______________________

> **DO NOT** mark this gate PASS if the rollback runbook only references this readiness doc — the runbook must contain the actual commands and steps, not pointers.

---

## Gate 10 — Monitoring / logging signal verified

**What it proves:** when something breaks in production, ops sees it in <5 minutes. Catches missing log fields, broken metric exporters, alert routing to a Slack channel that nobody watches.

**Verify (logs):**
```bash
# Trigger a video event via the public endpoint
curl -X POST "$API/api/v1/product-videos/<approved-video-id>/event" \
  -H "Content-Type: application/json" \
  -d '{"event_type":"impression","session_id":"readiness-probe"}'

# Confirm the structured log shows up in the aggregator (Loki / Cloudwatch /
# Datadog / whatever the env uses)
docker compose logs api --tail 20 | grep -E "product_video|video_event"
```

**Expected:**
- At least one structured log line with `product_video` or `video_event` substring
- Log is JSON-shaped (structlog) with `request_id`, `path`, `method`, `status_code` fields

**Verify (metrics / dashboards) — varies per ops stack:**
- [ ] FFmpeg failure rate metric exists OR audit-log query is bookmarked
- [ ] R2 + Bunny upload-error metric / log alert configured
- [ ] Queue-depth alert wired (ARQ pending jobs over threshold)
- [ ] On-call rotation has runbook URL

**Status note:** The detailed monitoring rollout doc is `docs/MONITORING_MODULE_35.md` (deferred to staging-pass + production-hardening phase per the launch sequencing). For initial limited release, ad-hoc log-tail via `docker compose logs api -f | grep product_video` is acceptable; full dashboard is required before broad rollout.

**Sign-off**
- [ ] Logs reach aggregator: ☐ PASS  ☐ FAIL
- [ ] On-call rotation knows about Module 35: ☐ PASS  ☐ FAIL
- [ ] Initial-launch ad-hoc log-tail acceptable: ☐ YES  ☐ NO (need dashboard before launch)
- Verified by: _______________________
- Date (UTC): _______________________
- Notes / caveats: _______________________

---

# Final Pre-Launch Summary

Fill this section ONLY after all gates 1–10 are signed.

| Gate | Result | Verified by | Date (UTC) |
|---|---|---|---|
| 1. Smoke test PASS | ___ | ___ | ___ |
| 2. Backend integration tests PASS | ___ | ___ | ___ |
| 3. Vitest tests PASS | ___ | ___ | ___ |
| 4. Compose clean start | ___ | ___ | ___ |
| 5. R2 + Bunny credentials roundtrip | ___ | ___ | ___ |
| 6. Real-browser HLS playback | ___ | ___ | ___ |
| 7. Audit log verified | ___ | ___ | ___ |
| 8. Upload validation | ___ | ___ | ___ |
| 9. Rollback runbook ready | ___ | ___ | ___ |
| 10. Monitoring / logging signal | ___ | ___ | ___ |

## Launch decision

- ☐ **CLEARED FOR LIMITED LIVE RELEASE** — at most 1% of traffic OR single-seller pilot, monitor 24 h before broader rollout.
- ☐ **CLEARED FOR FULL LIVE RELEASE** — all 10 PASS, plus 24h limited-release observation green, plus rollback rehearsed.
- ☐ **NOT CLEARED** — see notes below.

**Final sign-off (release lead):**
- Name + role: _______________________
- Date / Time UTC: _______________________
- Comments: _______________________

---

# Appendix A — Pointer to other docs

- `scripts/smoke_test_video.sh` — Gate 1 implementation
- `app/modules/product_videos/tests/` — Gate 2 implementation (11 tests)
- `hypershop-Frontend final/__tests__/ProductVideoPlayer.test.tsx` — Gate 3 implementation (9 tests)
- `docker-compose.yml` / `docker-compose.prod.yml` — Gate 4 stack definitions
- `app/modules/product_videos/storage.py` — Gates 5 (R2 + Bunny adapters)
- `app/core/audit/` — Gate 7 audit infrastructure
- `app/modules/product_videos/router.py` — Gate 8 upload validation
- `docs/ROLLBACK_MODULE_35.md` — Gate 9 (TO BE CREATED before live deploy)
- `docs/MONITORING_MODULE_35.md` — Gate 10 follow-up doc (post-staging)

# Appendix B — When to re-run this checklist

- Before EVERY production deploy that touches `app/modules/product_videos/**`
- Before EVERY production deploy that bumps the Bunny zone, R2 bucket, or CDN routing
- After ANY infrastructure change that affects the storage backends (R2 region move, Bunny pull-zone re-attach)
- After a non-trivial schema migration in the module
- Quarterly, as a hygiene check, even with no recent code change

---

# Appendix C — CI enforcement (branch protection)

The auto-checkable subset of these gates runs on every PR via:

- Backend repo: `.github/workflows/readiness.yml` → `make readiness` (covers Gates 2 + 9)
- Frontend repo: `.github/workflows/readiness.yml` → `npm test` (covers Gate 3)

**The workflow files alone do NOT block merges.** A repo admin must enable branch protection:

### Backend repo

1. GitHub → Settings → Branches → "Add branch protection rule"
2. Branch name pattern: `main` (also `develop` if you use it)
3. Tick "Require status checks to pass before merging"
4. Tick "Require branches to be up to date before merging"
5. In the search box, find and add: **`make readiness`** (the job name from `readiness.yml`)
6. Optionally also add the existing `tests (testcontainers)` check from `ci.yml` for fuller coverage
7. Save changes

### Frontend repo

1. Same Settings → Branches flow
2. Add status check: **`vitest component tests`**
3. Save changes

### What CI does NOT replace

CI green = Gates 2 + 3 + 9 pass.
**Customer-live deploy still requires the manual sign-off lines for Gates 1, 4, 5, 6, 7, 8, 10** in this document. CI gates code-quality regressions, not production readiness — that's the human's job per the locked rule (memory: `feedback_evidence_over_claims.md`).
