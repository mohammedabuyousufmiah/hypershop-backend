/*  k6-smoke.js — quick 60s × 20 VU validation run.
 *
 *  Lighter than baseline (100 VU × 5min): use this to verify the load
 *  script + the target backend are wired correctly before committing to
 *  a full baseline. Same traffic mix + same SLO thresholds, just less
 *  duration and concurrency.
 *
 *    k6 run loadtest/k6-smoke.js -e API=http://127.0.0.1:8000 \
 *         -e EMAIL=admin@hypershop.dev -e PASSWORD=adminlocal12
 */

import http from 'k6/http';
import {check, sleep} from 'k6';
import {Counter, Rate, Trend} from 'k6/metrics';
import {randomIntBetween} from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

const API = (__ENV.API || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const PREFIX = __ENV.PREFIX || '/api/v1';
const EMAIL = __ENV.EMAIL || 'admin@hypershop.dev';
const PASSWORD = __ENV.PASSWORD || '';

export const options = {
  scenarios: {
    smoke: {
      executor: 'constant-vus',
      vus: parseInt(__ENV.VUS || '20', 10),
      duration: __ENV.DURATION || '60s',
      gracefulStop: '10s',
    },
  },
  thresholds: {
    'http_req_failed':                ['rate<0.02'],
    'http_req_duration{kind:read}':   ['p(95)<800'],
    'http_req_duration{kind:write}':  ['p(95)<2000'],
    'http_req_duration{kind:auth}':   ['p(95)<1500'],
    'errors_5xx':                     ['count<1'],
  },
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
};

const errors5xx = new Counter('errors_5xx');
const loginRate = new Rate('login_success');

let authState = {token: null};

function url(path) { return API + PREFIX + path; }
function authHeaders() {
  return authState.token
    ? {Authorization: 'Bearer ' + authState.token, 'Content-Type': 'application/json'}
    : {'Content-Type': 'application/json'};
}
function record5xx(resp) { if (resp.status >= 500) errors5xx.add(1); }

function login() {
  if (!PASSWORD) return false;
  const resp = http.post(url('/auth/login'),
    JSON.stringify({email: EMAIL, password: PASSWORD}),
    {headers: {'Content-Type': 'application/json'}, tags: {kind: 'auth', endpoint: 'login'}}
  );
  record5xx(resp);
  const ok = check(resp, {
    'login: 200': r => r.status === 200,
  });
  loginRate.add(ok);
  if (ok) {
    try { authState.token = resp.json('data.tokens.access_token'); }
    catch (_) {}
  }
  return ok;
}

function pageHealth() {
  const r = http.get(url('/health'), {tags: {kind: 'read', endpoint: 'health'}});
  record5xx(r);
  check(r, {'health: 200': x => x.status === 200});
}

function pageDashboardCatalog() {
  if (!authState.token && !login()) return;
  const r = http.get(url('/admin/dashboard/widgets/catalog'),
    {headers: authHeaders(), tags: {kind: 'read', endpoint: 'widgets_catalog'}});
  record5xx(r);
  check(r, {'widgets catalog: 200': x => x.status === 200});
}

function pageWidgetData() {
  if (!authState.token && !login()) return;
  const widgets = ['orders-today', 'active-riders', 'cod-pending',
                   'orders-by-status', 'recent-orders', 'inventory-low',
                   'revenue-7d', 'backend-health'];
  const w = widgets[Math.floor(Math.random() * widgets.length)];
  const r = http.get(url(`/admin/dashboard/widget/${w}/data`),
    {headers: authHeaders(), tags: {kind: 'read', endpoint: 'widget_data'}});
  record5xx(r);
  check(r, {'widget data: 200': x => x.status === 200});
}

function pageAdminConfig() {
  if (!authState.token && !login()) return;
  const r = http.get(url('/admin/config/me'),
    {headers: authHeaders(), tags: {kind: 'read', endpoint: 'config_me'}});
  record5xx(r);
  check(r, {'config me: 200': x => x.status === 200});
}

export default function () {
  const roll = Math.random();
  if      (roll < 0.10) pageHealth();
  else if (roll < 0.20) pageAdminConfig();
  else if (roll < 0.30) pageDashboardCatalog();
  else                  pageWidgetData();
  sleep(randomIntBetween(1, 2));
}
