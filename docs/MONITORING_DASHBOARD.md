# Hypershop — Module 35 Grafana Dashboard

**Companion to:** `docs/MONITORING_MODULE_35.md` (the source-of-truth for which signals exist + why)
**Template file:** `docs/grafana/module_35_dashboard.json`

---

## What it gives you

A 12-panel dashboard, 4 rows × 3 columns, that mirrors §5 of the monitoring playbook one-for-one:

| Row | Panels |
|---|---|
| 1 · Pipeline health | status distribution · uploaded queue + oldest age · worker in-flight |
| 2 · Performance    | FFmpeg p50/p95 · Bunny PUT p95 · time-to-publish proxy |
| 3 · Errors         | FFmpeg failures (24h) · Bunny error rate % · R2 error rate % |
| 4 · Business       | moderation actions (rate) · approval cap hits (24h) · public list QPS |

Default time range: **last 6 hours**. Default auto-refresh: **30 seconds**. UID: `hypershop-module-35`.

---

## Pre-requisites

1. A running Grafana 10+ instance.
2. A Prometheus data source already configured in Grafana, scraping the api process's `GET /metrics` endpoint (see `docs/MONITORING_MODULE_35.md` §1 for scrape config).
3. The api has had non-zero traffic — empty-time-series panels render as "No data" until at least one request has hit each metric.

---

## Import

### Via UI (one-off, recommended for first-time setup)

1. In Grafana, **Dashboards → New → Import**.
2. Either upload `docs/grafana/module_35_dashboard.json` OR paste its contents.
3. Grafana will prompt for the **`DS_PROMETHEUS`** input — pick your existing Prometheus data source.
4. Click **Import**.

### Via API (for IaC / automated provisioning)

```bash
GRAFANA_URL="https://grafana.example.com"
GRAFANA_TOKEN="<service-account-token>"

# Substitute the data source UID into the template (replace ${DS_PROMETHEUS}
# with the literal UID of your Prometheus data source).
DS_UID="<your-prom-ds-uid>"
sed "s|\${DS_PROMETHEUS}|$DS_UID|g" docs/grafana/module_35_dashboard.json > /tmp/dash.json

# Wrap in the Grafana import envelope and POST.
jq -n --slurpfile d /tmp/dash.json \
  '{ dashboard: $d[0], folderUid: "", overwrite: true, message: "Module 35 dashboard import" }' \
  | curl -fsS -X POST \
      -H "Authorization: Bearer $GRAFANA_TOKEN" \
      -H "Content-Type: application/json" \
      -d @- \
      "$GRAFANA_URL/api/dashboards/db"
```

The response includes the dashboard's `url` — open it in a browser and pin the dashboard to your home folder.

---

## Per-panel PromQL reference

Every query is in the JSON as `targets[].expr`. Quick lookup:

| Panel | PromQL |
|---|---|
| Status distribution | `product_video_status_count` |
| Uploaded queue + oldest age | `product_video_status_count{status="uploaded"}` + `product_video_uploaded_oldest_seconds` |
| Worker in-flight | `product_video_processing_in_flight` |
| FFmpeg p50/p95 | `histogram_quantile(0.5\|0.95, sum by (le) (rate(product_video_ffmpeg_duration_seconds_bucket[5m])))` |
| Bunny PUT p95 | `histogram_quantile(0.95, sum by (le) (rate(product_video_bunny_upload_duration_seconds_bucket{outcome="success"}[5m])))` |
| Time-to-publish (proxy) | FFmpeg p95 over 1h — see Caveats below |
| FFmpeg failures 24h | `sum(increase(product_video_failed_total[24h]))` |
| Bunny error rate % | `rate(...{outcome="error"}_count) / rate(..._count)` × 100 |
| R2 error rate % | `rate(product_video_r2_upload_errors_total) / rate(http_requests_total{route="/api/v1/product-videos/products/{product_id}/upload"})` × 100 |
| Moderation actions (rate) | `rate(http_requests_total{route="/api/v1/admin/product-videos/{video_id}/<approve\|reject\|disable\|reopen>", status="200"}[1h])` |
| Cap hits 24h | `sum(increase(product_video_approve_cap_hit_total[24h]))` |
| Public list QPS | `rate(http_requests_total{route="/api/v1/products/{product_id}/videos"}[1m])` |

---

## Caveats

### "Time-to-publish proxy" is a lower bound, not the real metric

The accurate definition is `audit_logs.uploaded_at → audit_logs.approved_at` — that's a Postgres SQL question, not a Prometheus question, because moderator-side wait time isn't visible to the api process.

Two ways to add the real metric:

1. **Postgres data source in Grafana.** Add a Postgres data source pointing at the read-replica. Use the SQL from `MONITORING_MODULE_35.md` §3 ("Time-to-moderation"). This is the cleanest path.
2. **Periodic exporter.** Cron a small job that runs the SQL every 5 min, writes the result to a Prometheus pushgateway. More moving parts; only worth it if you don't have a Postgres data source.

The proxy panel uses FFmpeg p95 as a **floor** — actual time-to-publish is always longer (moderation latency dominates).

### `route` label values are templated

PromQL uses literal strings like `"/api/v1/admin/product-videos/{video_id}/approve"`. Do NOT substitute UUIDs in here — Starlette emits the templated path so the metric label is the same across requests. Substituting concrete UUIDs in PromQL would match zero series.

### Cardinality

The dashboard's queries already aggregate via `sum by (le)` / `sum(rate(...))` so per-panel rendering is fast. If your Prometheus instance retains long history, adjust the per-panel `[5m]` / `[1h]` / `[24h]` windows up if you see aggregation cost dominating dashboard load.

### Empty panels right after deploy

A freshly deployed app emits zero traffic until the first request hits each metric. If you see "No data":
- For `product_video_*` push metrics — fire one upload + approve to populate.
- For DB-derived gauges (`product_video_status_count` etc.) — they render only after the first scrape that runs the `PipelineStateCollector`. Scrapes happen at the configured Prometheus interval (typically 30 s).
- For `http_*` metrics — a single curl to `/api/v1/health` is enough to populate `http_requests_total{route="/api/v1/health"}`.

---

## Customisation

The JSON is editable in-place. Common tweaks:

- **Change the default time range** — top of the JSON, `"time": { "from": "now-6h", "to": "now" }`.
- **Pin the data source UID** instead of prompting on import — replace every `"${datasource}"` with the literal UID; remove the `__inputs` block.
- **Add deploy annotations** — change the Annotations entry's `enable` to `true`. Then push annotation events to Grafana from your CI on each deploy with `tags: ["deploy", "module-35"]`. Vertical lines appear on every panel marking when each release went out — invaluable for "did this regression start at the v0.43 deploy?".
- **Add a "current state" Postgres panel** — wire your Postgres data source and drop in any of the SQL queries from `MONITORING_MODULE_35.md` §2 / §3 / §8.

---

## Versioning + edits

The JSON's `"version": 1` field is the dashboard's own version, not the file. When you edit panels in Grafana and "Save dashboard", export the JSON via **Share → Export → Save to file** and replace `docs/grafana/module_35_dashboard.json`. Bump `"version"` so future imports overwrite cleanly.

For team review, treat dashboard changes the same as code: PR them with a description of "what changed and why" so future engineers understand the intent.

---

## Out of scope (for now)

- **Alerting rules** — Grafana 10 supports unified alerting in the same JSON, but mixing alert config with dashboard config gets messy. We recommend a separate alerts repo / file. The thresholds documented in `MONITORING_MODULE_35.md` §6 are the source-of-truth; wire them as Prometheus alerting rules (preferred) or Grafana alerts (acceptable).
- **Multi-tenant filters** — single-instance Hypershop today, no `tenant` label needed. If you ever multi-tenant, add a `tenant` template variable + propagate the label through the metric definitions.
- **Mobile / Safari iOS RUM panels** — frontend RUM is a separate roadmap item (see `MONITORING_MODULE_35.md` §7 P3).
