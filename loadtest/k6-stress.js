/*  k6-stress.js — find the breaking point.
 *
 *  Ramps from 50 → 500 VUs over 12 minutes, then holds 500 for 3 min,
 *  then ramps down. The point is to learn:
 *    1. What VU count makes p95 latency cross the SLO (500ms reads /
 *       1500ms writes)?
 *    2. What VU count makes the error rate exceed 5%?
 *    3. What VU count saturates a single api container's gunicorn
 *       worker pool (triggers connection refused)?
 *
 *  These three numbers tell you exactly when to scale horizontally
 *  (add another api container) or vertically (more workers per
 *  container).
 *
 *  How to run:
 *    k6 run loadtest/k6-stress.js -e API=https://api.your-domain.com
 *
 *  This is INTENSIVE — 500 VUs hitting a $5 droplet will likely
 *  break it. Run against staging or scale up the host first.
 *
 *  No SLO thresholds — this test is meant to FIND limits, not fail
 *  on them. Read the JSON output for the breaking-point numbers.
 */

import http from 'k6/http';
import {check, sleep} from 'k6';
import {Counter} from 'k6/metrics';
import {randomIntBetween} from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

const API = (__ENV.API || 'http://localhost:8000').replace(/\/+$/, '');
const PREFIX = __ENV.PREFIX || '/api/v1';

export const options = {
  scenarios: {
    ramp_to_break: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        {duration: '2m', target: 50},    // warmup
        {duration: '3m', target: 100},   // baseline
        {duration: '3m', target: 200},   // 2x baseline
        {duration: '2m', target: 350},   // 3.5x
        {duration: '2m', target: 500},   // 5x — this is where things break
        {duration: '3m', target: 500},   // hold for breakage
        {duration: '2m', target: 0},     // ramp down
      ],
      gracefulRampDown: '30s',
    },
  },
  // No thresholds — the point is to NOT fail on broken metrics, just
  // to record where they break.
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

const errors5xx = new Counter('errors_5xx');
const errorsConnRefused = new Counter('errors_conn_refused');

function url(p) { return API + PREFIX + p; }

export default function () {
  // Pure read-load — stress reveals server limits faster on reads
  // than writes, and we don't want to fill the orders table.
  const dice = Math.random();
  let r;
  if (dice < 0.5)        r = http.get(url('/health'),                  {tags: {endpoint: 'health'}});
  else if (dice < 0.85)  r = http.get(url('/catalog/products?limit=24'), {tags: {endpoint: 'catalog'}});
  else                   r = http.get(url('/delivery/zones'),          {tags: {endpoint: 'zones'}});

  if (r.error_code === 1212 || r.error_code === 1211) {
    // 1212 = ECONNRESET, 1211 = ECONNREFUSED — the server is full
    errorsConnRefused.add(1);
  }
  if (r.status >= 500) errors5xx.add(1);

  check(r, {
    'response received': x => x.status > 0,
    'no 5xx': x => x.status < 500,
  });

  sleep(randomIntBetween(0, 1));
}

export function handleSummary(data) {
  return {
    'stdout': textSummary(data),
    'results/stress-summary.json': JSON.stringify(data, null, 2),
  };
}

function textSummary(data) {
  const dur = data.metrics.http_req_duration?.values || {};
  const failed = data.metrics.http_req_failed?.values?.rate ?? 0;
  let out = '\n';
  out += '═══════════════════════════════════════════════════════════════════════\n';
  out += '  Hypershop STRESS test summary (no SLO thresholds — find limits)\n';
  out += '═══════════════════════════════════════════════════════════════════════\n';
  out += `  target:               ${API}${PREFIX}\n`;
  out += `  total reqs:           ${data.metrics.http_reqs?.values?.count ?? 0}\n`;
  out += `  reqs/sec (peak):      ${(data.metrics.http_reqs?.values?.rate ?? 0).toFixed(1)}\n`;
  out += `  error rate:           ${(failed * 100).toFixed(2)}%\n`;
  out += `  5xx errors:           ${data.metrics.errors_5xx?.values?.count ?? 0}\n`;
  out += `  connection refused:   ${data.metrics.errors_conn_refused?.values?.count ?? 0}\n`;
  out += `  latency avg:          ${(dur.avg ?? 0).toFixed(0)}ms\n`;
  out += `  latency p95:          ${(dur['p(95)'] ?? 0).toFixed(0)}ms\n`;
  out += `  latency p99:          ${(dur['p(99)'] ?? 0).toFixed(0)}ms\n`;
  out += `  latency max:          ${(dur.max ?? 0).toFixed(0)}ms\n`;
  out += '\n';
  out += '  Read the JSON for the per-stage breakdown — the VU count\n';
  out += '  where p(95) crosses 500ms is your safe scaling ceiling.\n';
  out += '═══════════════════════════════════════════════════════════════════════\n';
  return out;
}
