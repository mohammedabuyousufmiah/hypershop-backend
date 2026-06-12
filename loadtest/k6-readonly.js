/*  k6-readonly.js — pure-read smoke (no login).
 *
 *  Uses a pre-issued bearer token (passed via -e TOKEN=...) and only
 *  exercises read endpoints. Isolates the FE-shell read path from the
 *  auth-throttle that gates concurrent /auth/login.
 *
 *    TOKEN=$(curl -s ... | jq -r .data.tokens.access_token)
 *    k6 run loadtest/k6-readonly.js -e API=http://127.0.0.1:8000 -e TOKEN=$TOKEN
 */

import http from 'k6/http';
import {check, sleep} from 'k6';
import {Counter} from 'k6/metrics';
import {randomIntBetween} from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

const API = (__ENV.API || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const PREFIX = __ENV.PREFIX || '/api/v1';
const TOKEN = __ENV.TOKEN || '';

export const options = {
  scenarios: {
    reads: {
      executor: 'constant-vus',
      vus: parseInt(__ENV.VUS || '20', 10),
      duration: __ENV.DURATION || '30s',
      gracefulStop: '5s',
    },
  },
  thresholds: {
    'http_req_failed':              ['rate<0.02'],
    'http_req_duration{kind:read}': ['p(95)<500'],
    'errors_5xx':                   ['count<1'],
  },
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
};

const errors5xx = new Counter('errors_5xx');

function url(path) { return API + PREFIX + path; }
function authHeaders() {
  return TOKEN
    ? {Authorization: 'Bearer ' + TOKEN, 'Content-Type': 'application/json'}
    : {'Content-Type': 'application/json'};
}

const WIDGETS = ['orders-today', 'active-riders', 'cod-pending',
                 'orders-by-status', 'recent-orders', 'inventory-low',
                 'revenue-7d', 'backend-health', 'payment-method-mix',
                 'run-reconcile', 'sla-breaches'];

export default function () {
  const roll = Math.random();
  let path;
  if      (roll < 0.10) path = '/health';
  else if (roll < 0.20) path = '/admin/config/me';
  else if (roll < 0.30) path = '/admin/dashboard/widgets/catalog';
  else if (roll < 0.40) path = '/admin/audit-log?limit=20';
  else {
    const w = WIDGETS[Math.floor(Math.random() * WIDGETS.length)];
    path = `/admin/dashboard/widget/${w}/data`;
  }
  const r = http.get(url(path), {headers: authHeaders(), tags: {kind: 'read', endpoint: path}});
  if (r.status >= 500) errors5xx.add(1);
  check(r, {[`${path}: 200`]: x => x.status === 200});
  sleep(randomIntBetween(1, 2));
}
