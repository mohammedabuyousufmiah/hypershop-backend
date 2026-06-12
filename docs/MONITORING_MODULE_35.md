# Hypershop — Module 35 (Product Page Video) Monitoring Playbook

**Version:** 1.0
**Status:** post-staging-pass hardening doc
**Pairs with:** `docs/PRODUCTION_READINESS.md` Gate 10, `docs/ROLLBACK_MODULE_35.md`

---

## Purpose

This doc lists everything ops needs to **see what Module 35 is doing in production**, alert on regressions, and answer common questions ("how many videos pending? why did upload X fail?"). It's stack-agnostic — translate the queries to whatever you run (Loki / Grafana, Datadog, Cloudwatch Insights, Honeycomb, etc.).

The pipeline owns three classes of signal:

1. **Structured logs** — already emitted by code via structlog, JSON-shaped
2. **Audit log rows** — every state transition writes a row in `audit_logs`
3. **DB-derived state** — current row counts by status, derived metrics

Use logs for **incident investigation** (what happened to one row), audit for **compliance / business analytics** (who did what when), DB-derived for **dashboards** (queue depth, success rate over time).

---

## Section 1 — Existing structlog signals (catalog)

These log events are already emitted by the code. The "How to use" column tells you what to alert on or panel up.

| Event name | Emitted from | Level | Fields | How to use |
|---|---|---|---|---|
| `product_video_ffmpeg_unavailable` | `jobs.py` worker startup of every tick | WARN | `ffmpeg`, `ffprobe` (booleans) | Page on first occurrence — worker image is broken |
| `process_product_video_skipped` | `jobs.py` direct-dispatch claim | INFO | `video_id`, `reason` | Diagnostic only — high count means cron + dispatch racing (expected) |
| `process_product_video_bad_id` | `jobs.py` direct-dispatch entry | WARN | `video_id_hex` | Page if > 0 — means corrupted enqueue payload |
| `product_video_unexpected_error` | `jobs.py` pipeline catch-all | EXCEPTION | `video_id`, `error` (type + message) | Alert on rate > 1/hr — indicates non-FFmpeg crash path |
| `raw_originals_purged` | `jobs.py` cleanup cron | INFO | `count`, `cutoff`, `backend` (`r2`/`disk`) | Daily heartbeat — alert if count==0 for > 7 days (cron probably stuck) |
| `raw_original_delete_failed` | `jobs.py` cleanup cron | WARN | `video_id`, `raw_object_key`, `error` | Alert on rate > 5% of attempted deletes — R2 IAM issue likely |
| `bunny_upload_failed` | `storage.py` Bunny PUT | WARN | `remote_path`, `status_code`, `body` (≤500 chars) | Alert on rate > 1% over 10 min — Bunny side or auth issue |
| `bunny_delete_failed` | `storage.py` Bunny DELETE | WARN | `remote_path`, `status_code`, `body` | Diagnostic only — DELETE is rare |
| `process_product_video_enqueue_failed` | `router.py` upload handler | WARN | `video_id`, `error` | Alert on rate > 1/min — Redis connectivity from api process |
| `ffmpeg_step_failed` | `ffmpeg.py` `_run` | WARN | `label` (poster / hls_720p / hls_360p), `returncode` | Diagnostic — surfaced with full stderr tail in `processing_error` column |

All events also carry the **standard request-context fields** added by `app.core.middleware.RequestIdMiddleware`:
`request_id`, `path`, `method`, `status_code`, `actor_id` (when authenticated), `actor_kind`.

### Sample query — Loki / Grafana

```logql
{service="hypershop-api"} | json | event="bunny_upload_failed"
| line_format "{{.remote_path}} → {{.status_code}}: {{.body}}"
```

### Sample query — CloudWatch Logs Insights

```
fields @timestamp, event, video_id, status_code, body
| filter event = "bunny_upload_failed"
| sort @timestamp desc
| limit 50
```

### Sample query — Datadog

```
service:hypershop-* @event:bunny_upload_failed
```

---

## Section 2 — Audit log signals (DB-backed)

Every moderation transition writes a row in `audit_logs`. These are the source of truth for "did the human approve this video, when, why".

### Action codes (from `app/modules/product_videos/codes.py`)

```
product_video.uploaded
product_video.processed
product_video.processing_failed
product_video.approved
product_video.rejected
product_video.reopened
product_video.disabled
product_video.reenabled
product_video.deleted
```

### Useful queries

**Approval throughput (last 24 h):**
```sql
SELECT date_trunc('hour', created_at) AS hr,
       count(*) FILTER (WHERE action = 'product_video.approved')        AS approved,
       count(*) FILTER (WHERE action = 'product_video.rejected')        AS rejected,
       count(*) FILTER (WHERE action = 'product_video.reopened')        AS reopened,
       count(*) FILTER (WHERE action = 'product_video.disabled')        AS disabled,
       count(*) FILTER (WHERE action = 'product_video.processing_failed') AS ffmpeg_fail
FROM audit_logs
WHERE action LIKE 'product_video.%'
  AND created_at > now() - interval '24 hours'
GROUP BY hr ORDER BY hr;
```

**Time-to-moderation (uploaded → approved/rejected):**
```sql
WITH paired AS (
  SELECT a.resource_id,
         a.created_at AS uploaded_at,
         b.created_at AS resolved_at,
         b.action AS outcome
  FROM audit_logs a
  JOIN audit_logs b
    ON a.resource_id = b.resource_id
    AND b.action IN ('product_video.approved', 'product_video.rejected')
  WHERE a.action = 'product_video.uploaded'
    AND a.created_at > now() - interval '7 days'
)
SELECT outcome,
       count(*),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY resolved_at - uploaded_at) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY resolved_at - uploaded_at) AS p95
FROM paired GROUP BY outcome;
```

**Sellers hitting the per-product approval cap:**
```sql
SELECT (metadata_->>'product_id') AS product_id,
       count(*) AS cap_hits_24h
FROM audit_logs
-- We don't audit-log cap hits explicitly, so the indicator is
-- successive approve attempts on the same product that came back 409.
-- Instead, watch the metric "approve_cap_hit_total" via API access logs:
-- requests to /admin/product-videos/{id}/approve returning 409.
WHERE 1 = 0;  -- placeholder; see Section 4 for the access-log query
```

**Reopen-after-rejection rate (false-positive moderation indicator):**
```sql
SELECT date_trunc('day', created_at) AS day,
       count(*) AS reopens
FROM audit_logs
WHERE action = 'product_video.reopened'
  AND created_at > now() - interval '30 days'
GROUP BY day ORDER BY day;
```

A high or growing reopen rate suggests moderators are over-rejecting on first pass — feed this into seller-feedback or moderation training.

---

## Section 3 — DB-derived state metrics

Cheap-to-run queries that should back the main dashboard. Refresh every 30–60 s.

**Pipeline state distribution (the "current world" panel):**
```sql
SELECT status, count(*)
FROM product_videos
GROUP BY status
ORDER BY status;
```

Expected steady-state shape (relative scale):
- `approved` — large, grows monotonically
- `ready_for_review` — small (< 100); grows = admin moderation queue building up
- `processing` — single digits ideally; large = worker stuck or FFmpeg slow
- `uploaded` — single digits; large = worker not draining
- `failed` — small; spike = systemic FFmpeg / source issue
- `rejected` / `disabled` — moderation outcomes, monotonic

**Worker drain rate (uploaded queue depth):**
```sql
SELECT count(*) AS uploaded_pending,
       extract(epoch FROM (now() - min(created_at))) AS oldest_secs
FROM product_videos WHERE status = 'uploaded';
```

`oldest_secs > 90` for > 5 minutes → page (worker fell behind by more than 3 cron ticks).

**FFmpeg time-to-ready p50 / p95 (last 24 h):**
```sql
WITH ffmpeg_times AS (
  SELECT (
    SELECT b.created_at FROM audit_logs b
    WHERE b.resource_id = a.resource_id AND b.action = 'product_video.processed'
    LIMIT 1
  ) - a.created_at AS dt
  FROM audit_logs a
  WHERE a.action = 'product_video.uploaded'
    AND a.created_at > now() - interval '24 hours'
)
SELECT percentile_cont(0.5)  WITHIN GROUP (ORDER BY dt) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY dt) AS p95,
       count(*)
FROM ffmpeg_times WHERE dt IS NOT NULL;
```

Production target: p95 ≤ 90 s for a 30-second video on the default 2-CPU worker.

**Moderation queue age:**
```sql
SELECT count(*) AS pending,
       extract(epoch FROM (now() - min(updated_at))) / 3600 AS oldest_hours
FROM product_videos WHERE status = 'ready_for_review';
```

`oldest_hours > 24` → poke moderation team (SLO breach).

---

## Section 4 — Recommended metrics (with collection notes)

These are the metrics worth exporting to Prometheus / Datadog. Each row tells you the metric name (suggested), where it comes from, and a starting alert rule.

| Metric | Type | Source | Suggested alert |
|---|---|---|---|
| `product_video_uploaded_pending_total` | gauge | DB query (Section 3) | > 50 for > 5 min OR oldest > 90 s for > 5 min |
| `product_video_processing_in_flight` | gauge | DB query | > 5 (concurrent jobs setting + 3 buffer) |
| `product_video_ready_for_review_oldest_seconds` | gauge | DB query | > 86400 (24 h SLO) |
| `product_video_failed_total` | counter | DB count of status=failed | rate > 5/hr |
| `product_video_ffmpeg_duration_seconds` | histogram | computed from `audit_logs` time delta uploaded→processed | p95 > 120 s |
| `product_video_bunny_upload_errors_total` | counter | structlog `bunny_upload_failed` count | rate > 1% of total bunny PUTs over 10 min |
| `product_video_r2_upload_errors_total` | counter | API access log: 5xx on `/product-videos/products/*/upload` | rate > 1% over 5 min |
| `product_video_arq_enqueue_failures_total` | counter | structlog `process_product_video_enqueue_failed` | any > 0 in last 5 min |
| `product_video_approve_cap_hit_total` | counter | API access log: 409 + `code=product_video_bad_state` + `details.cap=3` | rate > 10/hr (sellers frustrated) |
| `product_video_event_post_total{type=...}` | counter | API access log: 204 on `/product-videos/*/event` | drop > 50% week-over-week (frontend instrumentation broken) |

For projects that run **structlog → JSON → log shipper → metric**, the cleanest path is a log-based metric. For projects that already have Prometheus client code, expose these via `app/core/metrics.py` (not yet present — see gap Section 7).

### Access-log derivation example (Datadog logs → metrics)

```
service:hypershop-api status:409 @http.url_path:*\/admin\/product-videos\/*\/approve
| measure_count
```

---

## Section 5 — Suggested dashboard layout

> **Importable template:** `docs/grafana/module_35_dashboard.json`
> See `docs/MONITORING_DASHBOARD.md` for import steps + per-panel PromQL.

Single page, 4 rows × 3 columns.

| | Col 1 | Col 2 | Col 3 |
|---|---|---|---|
| **Row 1: Pipeline health** | Status distribution (stacked area) | Uploaded queue depth + oldest-age (line + threshold) | Worker concurrency in-flight (line) |
| **Row 2: Performance** | FFmpeg p50 / p95 (line) | Bunny upload latency p95 (line, derived from PUT-time) | Time-to-publish p95 (uploaded → approved, line) |
| **Row 3: Errors** | FFmpeg failures last 24 h (count) | Bunny upload error rate (% line) | R2 upload error rate (% line) |
| **Row 4: Business** | Approvals vs rejections vs reopens (stacked bar, 30 d) | Per-product cap hits (count) | Public list query rate (line) |

Add a top-of-dashboard text panel pinning:
- Link to this doc
- Link to `PRODUCTION_READINESS.md`
- Link to `ROLLBACK_MODULE_35.md`
- Current on-call rotation

---

## Section 6 — Alert thresholds + escalation

Recommended starting thresholds. Tune after 2 weeks of staging + 1 week of production observation — these are first-pass guardrails, not SLOs.

| Severity | Trigger | Page who | Rationale |
|---|---|---|---|
| **P0** (page immediately) | `product_video_ffmpeg_unavailable` once | on-call eng | worker image is broken; pipeline hard-down |
| **P0** | uploaded_pending oldest > 5 min | on-call eng | worker down or wedged |
| **P0** | api 5xx rate > 5% on Module 35 endpoints for > 2 min | on-call eng | likely consider rollback (see runbook) |
| **P1** (next business hour) | bunny upload error rate > 5% over 30 min | on-call eng | Bunny side OR auth rotation; degrade to disk fallback if extended |
| **P1** | FFmpeg failure rate > 10% over 1 h | platform eng | worker image / codec / hardware issue |
| **P1** | `process_product_video_enqueue_failed` rate > 1/min for > 5 min | on-call eng | Redis or ARQ pool issue |
| **P2** (next morning) | ready_for_review oldest_hours > 24 | moderation team lead | SLO breach on moderator response time |
| **P2** | approval cap_hit > 50/day | product manager | sellers are frustrated; consider raising the cap or surfacing a clearer error |
| **P2** | reopens > 10% of rejections | moderation team lead | first-pass over-rejection signal |
| **P3** (weekly review) | raw_originals_purged count == 0 for > 7 days | platform eng | retention cron stalled |
| **P3** | event-write rate dropped > 50% w/w | analytics + frontend | frontend instrumentation regressed |

Escalation: P0 escalates to platform lead after 15 min unresolved. P1 escalates after 2 h. P2 / P3 reviewed in weekly ops standup.

---

## Section 7 — Gap analysis (what's NOT instrumented)

**Honest list of things this module doesn't currently emit, with priority for adding:**

| Gap | Priority | Suggested fix |
|---|---|---|
| ~~No Prometheus / OpenMetrics exporter wired into the app~~ | ~~P1~~ ✅ **CLOSED** | Wired in turn 28: `app/core/metrics.py` exports the registry + `/metrics` endpoint mounted at app root; `app/modules/product_videos/metrics.py` defines the 10 metrics from Section 4 + a `PipelineStateCollector` for the 4 DB-derived gauges. Endpoint protection = network isolation (Caddy doesn't proxy `/metrics`); no app-level allowlist by design. |
| ~~No project-wide HTTP request duration / count metrics~~ | ~~P3~~ ✅ **CLOSED** | Wired in turn 29: `PrometheusMetricsMiddleware` in `app/core/middleware/metrics.py` emits `http_requests_total{method, route, status}` and `http_request_duration_seconds{method, route, status}` for every endpoint in the project (not just Module 35). Route label uses Starlette's matched route TEMPLATE (e.g. `/api/v1/products/{product_id}/videos`) — UUIDs collapse correctly; unmatched 404s land under `route="<unmatched>"`. Added LAST in the middleware chain so it's outermost — captures full request lifecycle. |
| FFmpeg duration not directly logged | ✅ **CLOSED** | Histogram `product_video_ffmpeg_duration_seconds` recorded by `_process_one` (success + failure paths both observed). Bucket boundaries 5–300 s + +Inf. |
| ~~Bunny PUT latency not measured~~ | ~~P2~~ ✅ **CLOSED** | Wired in turn 30: `product_video_bunny_upload_duration_seconds{outcome}` histogram, recorded around the PUT call in `storage.bunny_upload_public_file` — both success path AND exception/non-2xx path observed (separate `outcome` label values) so connection-level errors still contribute to the latency picture. Buckets 0.1s..30s tightened low for sub-second drift detection. |
| No tracing (OpenTelemetry spans) across api → ARQ → worker | P3 | Wire OTEL once the broader Hypershop traces are wired (this module follows the project's standard) |
| Frontend RUM (real user metrics) for HLS playback | P3 | Out of scope for backend doc; covered when Playwright e2e + RUM lands |
| Cost telemetry (Bunny bandwidth, R2 storage) | P3 | Pull from provider billing API daily; correlate with `audit_logs` for per-deploy cost-attribution |

The first item (P1) is the only one that materially blocks "real production hardening". Everything else is incremental.

---

## Section 8 — On-call quick lookups

Common questions on-call gets, with the one-liner that answers each.

**"Why didn't this video get approved?"**
```sql
SELECT status, processing_error, rejection_reason, created_at, updated_at,
       approved_at, disabled_at, reopened_at
FROM product_videos WHERE id = '<video-uuid>';

SELECT action, actor_kind, actor_id, metadata_, created_at
FROM audit_logs
WHERE resource_id = '<video-uuid>'
ORDER BY created_at;
```

**"Is the worker draining?"**
```sql
SELECT count(*) AS uploaded,
       extract(epoch FROM (now() - min(created_at))) AS oldest_secs
FROM product_videos WHERE status IN ('uploaded', 'processing');
```

**"Did Bunny accept the upload for this video?"**
Search logs for the video's HLS path:
```
{service="hypershop-worker"} |= "<video_id_hex>" | json
```

If you see `bunny_upload_failed` events → Bunny side problem; check `body` field for the Bunny error message.

**"Which sellers are hitting the approval cap most?"**
```sql
SELECT product_id, count(*) AS approved
FROM product_videos
WHERE status = 'approved'
GROUP BY product_id
HAVING count(*) >= 3
ORDER BY approved DESC LIMIT 50;
```

(Indirect, but if `product_id` shows up here repeatedly + sellers are uploading more → they're capped.)

**"Is the cleanup cron running?"**
```
{service="hypershop-worker"} |= "raw_originals_purged" | json
| line_format "purged {{.count}} ({{.backend}}) at {{.cutoff}}"
```

Should fire daily at 02:15 UTC. Absent for > 30 hours → cron stalled.

---

## Section 9 — Rollout plan

How to get from "code emits these signals" to "ops actually sees them":

### Phase A — staging-pass (immediate)

- [ ] Confirm structlog JSON output reaches the existing aggregator (Hypershop-wide)
- [ ] Bookmark Section 8 SQL queries in the Postgres console / DB tool
- [ ] Add the 4 P0 alerts from Section 6 (`ffmpeg_unavailable`, queue stuck, 5xx rate, enqueue failures)
- [ ] Run `PRODUCTION_READINESS.md` Gate 10 against staging

### Phase B — limited live release (< 7 days post-launch)

- [ ] Build the dashboard (Section 5) with whatever metrics already collect via logs
- [ ] Wire the P1 alerts (Bunny, FFmpeg failure, ffmpeg duration once derived)
- [ ] Daily report on Section 2 audit queries, sent to product + moderation lead

### Phase C — production hardening (< 30 days post-launch)

- [ ] Close the Section 7 P1 gap — add the `prometheus_client` `/metrics` endpoint
- [ ] Wire P2 + P3 alerts
- [ ] Review thresholds against actual 2-week production baselines, retune
- [ ] Schedule quarterly retrospective on this doc — anything missed in real incidents goes in

---

## Appendix A — Cross-references

- **What signals exist** — Sections 1, 2, 3
- **What signals SHOULD exist but don't** — Section 7
- **What to alert on** — Sections 4, 6
- **What to look at when paged** — Section 8
- **What to ship before declaring monitoring "done"** — Section 9

If a signal you need isn't here, the gap is real — file an issue, point it at this doc, and add a row to Section 7 with priority.
