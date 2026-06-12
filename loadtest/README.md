# Hypershop load testing

Three k6 scenarios — baseline, stress, soak — each with explicit
SLOs (or, for stress, explicit "no SLOs, just measure" intent).

```
loadtest/
  k6-baseline.js     100 VUs × 5 min   — SLO-gated; pre-launch acceptance
  k6-stress.js       50→500 VUs ramp   — find the breaking point
  k6-soak.js         50 VUs × 30 min   — find memory leaks + drift
  README.md
  results/           generated JSON summaries (gitignored)
```

## Install k6 (one-time)

| Platform | Command |
|----------|---------|
| **macOS** | `brew install k6` |
| **Ubuntu / Debian / Codespaces** | `sudo apt-get install -y k6` (after adding the k6 deb repo — see [`loadtest.yml`](../.github/workflows/loadtest.yml) for the gpg+repo commands) |
| **Windows** | `choco install k6` or `winget install k6 --source winget` |
| **Docker** | `docker run --rm -v $(pwd):/scripts grafana/k6 run /scripts/loadtest/k6-baseline.js -e API=...` |

Verify: `k6 version` → `k6 v0.50.0+`.

## Quick start (against a local stack)

```bash
make prod-up                         # bring up the API
make loadtest-baseline               # 100 VUs, 5 min
```

## Quick start (against a real server)

```bash
make loadtest-baseline \
    API=https://api.your-domain.com \
    EMAIL=loadtest@your-domain.com \
    PASSWORD=...
```

`EMAIL` + `PASSWORD` are only needed if you want the test to exercise
auth + write paths. Without them the test sticks to public reads and
about 5% of the traffic gets skipped.

## What each scenario tests

### Baseline (`k6-baseline.js`) — pre-launch acceptance

Models a realistic Hypershop traffic mix:

| Endpoint | Share | Why |
|----------|-------|-----|
| `GET /catalog/products` | 60% | Most page views are catalog browse |
| `GET /me/orders` | 15% | Returning customers check status |
| `POST /orders` | 8% | Checkout (heaviest write) |
| `POST /payments/initiate` | 5% | Online-payment kickoff |
| `POST /auth/login` | 5% | Login spike at checkout time |
| `GET /health` | 5% | Control variable |
| `GET /me/profile` + product detail | 2% | Long tail |

**SLOs (test fails if any breaks):**

| Metric | Threshold | Why this number |
|--------|-----------|-----------------|
| Read p95 latency | `< 500ms` | Catalog browse over 500ms feels broken on mobile |
| Write p95 latency | `< 1500ms` | Checkout over 1.5s loses conversions |
| Auth p95 latency | `< 800ms` | Login that takes 1s+ is a UX hit but not fatal |
| Error rate | `< 1%` | Industry standard for "healthy" |
| 5xx errors | `0` | Any unhandled exception is a bug |
| Successful order placements | `> 10` | Sanity — we're actually exercising the write path |

### Stress (`k6-stress.js`) — find the breaking point

Ramps from 0 → 500 VUs over 12 min, holds 500 for 3 min, ramps down.
**No SLO thresholds** — the goal is to find:

1. The VU count where p95 latency crosses 500ms → your **safe scaling ceiling**
2. The VU count where error rate exceeds 5% → your **hard ceiling**
3. The VU count where you start seeing `ECONNREFUSED` → **gunicorn worker pool saturated**, time to add `--workers 8` or scale out

The output JSON has per-stage breakdowns; visualise with k6 Cloud,
Grafana, or just `jq '.metrics.http_req_duration.values' results/stress-summary.json`.

### Soak (`k6-soak.js`) — find memory leaks

50 VUs for 30 minutes. Catches:

- Memory leaks in the api / worker (latency drifts upward over time)
- DB connection pool exhaustion (similar shape, harder to see)
- Outbox dispatcher backlog (events accumulate faster than worker drains)
- Disk fill from log files

While it's running, watch:

```bash
docker stats                              # api + worker memory growth
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U $POSTGRES_USER -c "SELECT count(*) FROM outbox_messages WHERE status='pending'"
```

**SLOs:**

| Metric | Threshold |
|--------|-----------|
| Error rate | `< 2%` (looser than baseline — this is a discovery test) |
| Read p95 latency | `< 800ms` |
| Latency drift (p95 difference between minute 1 and minute 30) | `< 200ms` |

## Reading the results

Each scenario writes `loadtest/results/{baseline,stress,soak}-summary.json`
with k6's full metric set. The console summary at the end shows:

```
═══════════════════════════════════════════════════════════════════════
  Hypershop load-test summary
═══════════════════════════════════════════════════════════════════════
  target:          https://api.your-domain.com/api/v1
  duration:        300.0s
  vus (max):       100
  requests sent:   18342
  requests/sec:    61.1
  error rate:      0.12%
  latency avg:     87ms
  latency p95:     312ms
  latency p99:     589ms
  5xx errors:      0
  orders placed:   142

  thresholds:
    ✓ http_req_failed { rate<0.01 }
    ✓ http_req_duration{kind:read} { p(95)<500 }
    ✓ http_req_duration{kind:write} { p(95)<1500 }
    ✓ errors_5xx { count<1 }
    ✓ orders_placed { count>10 }
═══════════════════════════════════════════════════════════════════════
```

A `✗` in front of any threshold means the test failed and `k6` exits 1.

## Running in CI

`.github/workflows/loadtest.yml` is **manually triggered** (workflow
dispatch). On the Actions tab → **loadtest** → **Run workflow** → pick
the scenario and supply the API URL. Results JSON is uploaded as a
build artifact for 14 days.

We don't run loadtests on every push because:

1. They take 5-30 minutes (slow CI).
2. They generate noise against any shared environment.
3. They're not the right gate for a code change — broken latency is
   usually a config / data / infrastructure issue, not a code issue.

## Capacity-planning recipe

To know "what's the largest customer base I can serve on a single $5
droplet?":

1. Bring up the prod stack on the smallest box you'd consider:
   ```bash
   make prod-up
   ```
2. Run baseline against it:
   ```bash
   make loadtest-baseline
   ```
   - PASS at 100 VUs with no warnings → comfortably handles ~10k DAU
   - PASS but p95 close to threshold → start planning capacity now
   - FAIL → add resources before launch
3. Run stress to find the actual ceiling:
   ```bash
   make loadtest-stress
   ```
   - Note the VU count where p95 first crosses 500ms — that's your
     **horizontal-scale trigger**. Add an extra api container at 70%
     of that number for headroom.
4. Run soak overnight to confirm no leaks:
   ```bash
   make loadtest-soak
   ```
   - Drift < 200ms → you can leave the box running for weeks
   - Drift > 200ms → schedule weekly `make prod-down && make prod-up`
     until you find the leak (or just upgrade the api image — gunicorn
     `--max-requests 1000` should already be recycling workers)

## Common results to expect

| Scenario | Hardware | Expected outcome |
|----------|----------|------------------|
| Baseline | 1×CPU / 1GB (dev) | FAIL — hits memory ceiling around 60 VUs |
| Baseline | 2×CPU / 4GB ($12/mo droplet) | PASS comfortably, p95 ~250ms |
| Baseline | 4×CPU / 8GB ($24/mo) | PASS easily, p95 <150ms |
| Stress (find break) | 2×CPU / 4GB | Breaks around 250-300 VUs |
| Stress | 4×CPU / 8GB | Breaks around 500-600 VUs (the test ceiling) |
| Soak 30min | 4×CPU / 8GB | Drift <50ms (gunicorn `--max-requests` does its job) |

These are rough — real numbers depend on the catalog size + variant
count. Run them to know YOUR numbers.
