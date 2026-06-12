# Hypershop — OpenTelemetry Tracing

**Code:** `app/core/tracing.py`. **Wired:** `app/main.py` + `app/worker.py`.
**Status:** scaffold complete; off by default; turns on with one env var.

---

## What gets traced when enabled

Every:
- HTTP request hitting the FastAPI app (route, status, duration, request_id)
- SQLAlchemy query (statement preview, duration, connection pool wait time)
- Outbound `httpx` call (Bunny upload, AI providers, payment gateway HTTP)
- Redis operation (ARQ enqueue/dequeue, rate-limit Lua scripts, cache get/set)

Span attribution is automatic — no per-handler code changes needed. Custom spans (e.g. wrapping an FFmpeg run) can be added later via `opentelemetry.trace.get_tracer(__name__)` if useful.

---

## Turn it on

Set in `.env`:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
OTEL_SERVICE_NAME=hypershop-api
OTEL_ENVIRONMENT=production
OTEL_TRACES_SAMPLE_RATIO=0.1   # 10% sampling, suitable for prod
```

That's it. Restart api + worker. Traces appear in your collector within ~5s.

---

## Backend choices

**Default (production):** Jaeger self-hosted with Badger persistent storage. Shipped in `docker-compose.observability.prod.yml`. See §Production setup below.

The collector endpoint can point at any OTLP/HTTP-compatible receiver if you want to swap:

| Backend | Endpoint format | Notes |
|---|---|---|
| Jaeger (self-hosted) — **default** | `http://jaeger:4318` | Layer in `docker-compose.observability.prod.yml` |
| Grafana Tempo | `http://tempo:4318` | Drop-in replacement; cheaper at high trace volume |
| Grafana Cloud Traces | `https://otlp-gateway-prod-<region>.grafana.net/otlp` | Add `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64-creds>` |
| Honeycomb | `https://api.honeycomb.io` | `OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=YOUR_API_KEY` |
| Datadog APM | `http://datadog-agent:4318` | Datadog Agent must be running with OTLP receiver enabled |
| AWS X-Ray | via OTel Collector with X-Ray exporter | Run the OTel Collector as a sidecar that re-exports |

**Migration trigger:** swap to Tempo or a managed APM when (a) trace volume exceeds ~10M spans/day OR (b) distributed-team trace search becomes a priority. Until then, self-hosted Jaeger costs $0 incremental and runs on the existing VPS.

---

## Sample-ratio guidance

| Traffic level | Recommended `OTEL_TRACES_SAMPLE_RATIO` |
|---|---|
| Dev (single user) | `1.0` (everything) |
| Staging (small team load) | `1.0` (still fine) |
| Prod < 100 RPS | `1.0` |
| Prod 100–500 RPS | `0.25` |
| Prod 500–2000 RPS | `0.1` |
| Prod > 2000 RPS | `0.05` (and consider tail-sampling at the collector) |

Lowering the ratio is the main lever for collector cost. Tail-sampling (keep 100% of error spans, sample 1% of success) at the collector level is the next step when sample ratio alone isn't enough — out of scope here.

---

## Add to compose for local dev

The overlay file `docker-compose.observability.yml` ships Jaeger + Prometheus + Grafana wired for dev traffic. Run:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up
```

That brings up the base stack plus:

| Service | URL | Purpose |
|---|---|---|
| Jaeger | http://localhost:16686 | Trace UI — pick service `hypershop-api` or `hypershop-worker` |
| Prometheus | http://localhost:9090 | Raw metrics — `up`, `http_requests_total`, etc. |
| Grafana | http://localhost:3001 (admin/admin) | Dashboards — Module 35 panel auto-provisioned from `docs/grafana/` |

The overlay sets `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318` on api + worker automatically — no extra `.env` change needed. Grafana auto-loads the Prometheus + Jaeger datasources and the dashboards in `docs/grafana/` via the provisioning files in `ops/grafana/provisioning/`.

Generate traffic by hitting endpoints in the api container, then browse Jaeger to see the spans and Grafana to see the metrics.

---

## What does NOT get traced

- Background ARQ job execution paths that don't go through the api → worker handoff (the `process_pending_videos_job` cron, for example, runs without a parent span). To add explicit tracing for ARQ jobs, wrap each job function with `tracer.start_as_current_span("job.<name>")`.
- Internal worker async tasks (FFmpeg subprocess waits) — these aren't HTTP / DB / Redis, so no auto-instrumentation matches them.
- The frontend customer-web — frontend RUM is out of scope; see the deferred row in `MONITORING_MODULE_35.md` Section 7.

---

## Cost guard

Tracing has runtime cost. The Hypershop scaffold is OFF by default for exactly this reason — turning it on at 1.0 ratio in production might surprise you on collector ingest fees.

Quick math at 100 RPS, average 5 spans per request:
- 1.0 ratio → 500 spans/sec → 1.3 GB/day at ~3KB per span
- 0.1 ratio → 50 spans/sec → 130 MB/day

Most managed APM tiers price by GB ingested. Choose the ratio with the actual bill in mind.

---

## Production setup

`docker-compose.observability.prod.yml` is the production overlay. It adds Jaeger + Prometheus + Grafana to the base prod stack, persistent + auth-protected.

```bash
make prod-up-observability
```

Internally that runs `docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml -f docker-compose.observability.prod.yml up -d --build`.

**One-time setup:**

1. **DNS** — point `OBSERVABILITY_DOMAIN` (e.g. `obs.api.hypershop.example`) A/AAAA at the same host as `API_DOMAIN`.
2. **Generate basic-auth hash:**
   ```bash
   docker run --rm caddy:2-alpine caddy hash-password --plaintext 'your-strong-password'
   ```
   Paste the output as `OBSERVABILITY_PASSWORD_HASH` in `.env.prod`. Set `OBSERVABILITY_USER=admin`.
3. **Pick `GRAFANA_ADMIN_PASSWORD`** — long random; rotate after first login via Grafana UI.
4. **Tune retention if needed:** `JAEGER_RETENTION_DAYS=7` and `PROMETHEUS_RETENTION_DAYS=30` are defaults. With sample ratio 0.1 at 100 RPS, 7 days of Jaeger Badger data is ~10–15 GB.

**What the operator gets:**

| URL | Purpose |
|---|---|
| `https://${OBSERVABILITY_DOMAIN}/jaeger` | Trace timeline |
| `https://${OBSERVABILITY_DOMAIN}/prometheus` | Raw PromQL |
| `https://${OBSERVABILITY_DOMAIN}/grafana` | Module 35 dashboard auto-loaded |

All three sit behind a single Caddy basic-auth gate. Internal ports (16686, 9090, 3000) are NOT exposed to the public internet — only the Caddy 443 ingress is.

**Capacity rough math** (Jaeger Badger):

| Sample ratio | RPS | Spans/day | Disk/day |
|---|---|---|---|
| 1.0 | 100 | ~43M | ~13 GB |
| 0.1 | 100 | ~4.3M | ~1.3 GB |
| 0.05 | 500 | ~21.6M | ~6.5 GB |

Assumes ~5 spans per request and ~3 KB per span at default attribute density. Real numbers will vary; watch `du -sh hypershop_jaeger volume` over the first week and tune `JAEGER_RETENTION_DAYS` accordingly.

**Migration to managed APM** (when self-hosted hits its limits):

1. Sign up with Honeycomb / Datadog / Grafana Cloud
2. Set `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_EXPORTER_OTLP_HEADERS` in `.env.prod` (overrides what the overlay sets)
3. `docker compose restart api worker`
4. Decommission the self-hosted Jaeger by stopping `docker-compose.observability.prod.yml` (Prometheus + Grafana can stay or move to Grafana Cloud separately)

---

## Disable in CI

The CI environment doesn't run a collector, so `OTEL_EXPORTER_OTLP_ENDPOINT` is unset there. The scaffold's "no env var = no init" behaviour means CI never tries to export. No special CI configuration needed.

---

## Honest framing

| | Status |
|---|---|
| Module authored | ✅ |
| `py_compile` clean | ✅ |
| Local-dev compose overlay (Jaeger + Prometheus + Grafana) shipped | ✅ — `docker-compose.observability.yml` |
| Tracing enabled by default | ❌ — base stack stays lean; opt in via the overlay |
| Verified against a real collector | ❌ — needs `docker compose up` with the overlay; not run in this environment |

Pre-prod verification: run the overlay command above, generate a few requests, confirm spans appear in Jaeger and the Module 35 dashboard renders in Grafana.
