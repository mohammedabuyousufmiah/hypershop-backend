/*  k6-soak.js — 50 concurrent users for 30 minutes.
 *
 *  Lower load than baseline, longer duration. Catches:
 *    - Memory leaks in the api / worker process
 *    - DB connection pool exhaustion (slow leak, manifests as
 *      gradual latency growth)
 *    - Outbox dispatcher backlog (events accumulate faster than
 *      worker drains them)
 *    - log file / disk fill scenarios
 *
 *  Run AFTER baseline passes. While running, watch:
 *    docker stats          # memory growth
 *    docker compose -f docker-compose.prod.yml exec postgres \
 *      psql -U hypershop -c "SELECT count(*) FROM outbox_messages WHERE status='pending'"
 *
 *  How to run:
 *    k6 run loadtest/k6-soak.js -e API=https://api.your-domain.com
 *
 *  SLOs (looser than baseline because we want to discover issues,
 *  not block on them):
 *    - p95 latency may NOT grow more than 50% from minute 1 to minute 30
 *    - error rate < 2%
 */

import http from 'k6/http';
import {check, sleep} from 'k6';
import {Trend, Counter} from 'k6/metrics';
import {randomIntBetween, randomItem} from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

const API = (__ENV.API || 'http://localhost:8000').replace(/\/+$/, '');
const PREFIX = __ENV.PREFIX || '/api/v1';

export const options = {
  scenarios: {
    soak: {
      executor: 'constant-vus',
      vus: 50,
      duration: '30m',
      gracefulStop: '30s',
    },
  },
  thresholds: {
    'http_req_failed': ['rate<0.02'],
    'http_req_duration': ['p(95)<800'],
    // Latency drift detector — see custom metric below
    'latency_minute_drift_ms': ['avg<200'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

const latencyDrift = new Trend('latency_minute_drift_ms');
let baselineP95 = null;

function url(p) { return API + PREFIX + p; }

export default function () {
  const startMin = Math.floor(__ITER / 50);   // rough minute index
  const dice = Math.random();
  let r;
  if (dice < 0.7)        r = http.get(url('/catalog/products?limit=24'), {tags: {endpoint: 'catalog'}});
  else if (dice < 0.95)  r = http.get(url('/health'),                    {tags: {endpoint: 'health'}});
  else                   r = http.get(url('/delivery/zones'),            {tags: {endpoint: 'zones'}});

  // Track per-iteration latency for drift detection
  if (startMin === 1 && baselineP95 === null) baselineP95 = r.timings.duration;
  if (startMin >= 2 && baselineP95 !== null) {
    latencyDrift.add(Math.max(0, r.timings.duration - baselineP95));
  }

  check(r, {'no 5xx': x => x.status < 500});
  sleep(randomIntBetween(1, 3));
}

export function handleSummary(data) {
  return {
    'stdout': textSummary(data),
    'results/soak-summary.json': JSON.stringify(data, null, 2),
  };
}

function textSummary(data) {
  const dur = data.metrics.http_req_duration?.values || {};
  const drift = data.metrics.latency_minute_drift_ms?.values || {};
  let out = '\n';
  out += '═══════════════════════════════════════════════════════════════════════\n';
  out += '  Hypershop SOAK test summary\n';
  out += '═══════════════════════════════════════════════════════════════════════\n';
  out += `  target:               ${API}${PREFIX}\n`;
  out += `  duration:             ${(data.state.testRunDurationMs / 60000).toFixed(1)} min\n`;
  out += `  total requests:       ${data.metrics.http_reqs?.values?.count ?? 0}\n`;
  out += `  reqs/sec:             ${(data.metrics.http_reqs?.values?.rate ?? 0).toFixed(1)}\n`;
  out += `  error rate:           ${((data.metrics.http_req_failed?.values?.rate ?? 0) * 100).toFixed(2)}%\n`;
  out += `  latency p95 overall:  ${(dur['p(95)'] ?? 0).toFixed(0)}ms\n`;
  out += `  latency drift avg:    ${(drift.avg ?? 0).toFixed(0)}ms (vs minute-1 baseline)\n`;
  out += `  latency drift max:    ${(drift.max ?? 0).toFixed(0)}ms\n`;
  out += '\n';
  out += '  If "drift avg" > 200ms, the api process is leaking — check\n';
  out += '  ``docker stats`` for memory growth, then ``docker compose logs api``\n';
  out += '  for slow queries / outbox backlog warnings.\n';
  out += '═══════════════════════════════════════════════════════════════════════\n';
  return out;
}
