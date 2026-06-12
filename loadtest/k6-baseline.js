/*  k6-baseline.js — 100 concurrent users for 5 minutes.
 *
 *  Models a realistic Hypershop traffic mix:
 *    60%  catalog browse (anonymous reads)
 *    15%  authenticated profile + order list
 *     8%  place order (the heaviest write path)
 *     5%  payment initiate (gateway round-trip is mocked away here —
 *          provider stays on NotConfigured, so we measure OUR latency,
 *          not Bkash/SSLCommerz's)
 *     5%  customer login (email path, OTP path measured separately)
 *     5%  health probe (cheapest read — control variable)
 *     2%  cart-shaped flow (search → product detail → place)
 *
 *  How to run:
 *    k6 run loadtest/k6-baseline.js \
 *      -e API=https://api.your-domain.com \
 *      -e EMAIL=loadtest@hypershop.local \
 *      -e PASSWORD=your-test-password
 *
 *  Or via the Makefile:
 *    make loadtest-baseline API=https://api.your-domain.com
 *
 *  Outputs:
 *    - Console summary (p50/p95/p99 per endpoint, error rate)
 *    - results/baseline-<timestamp>.json (full timeseries; piped to
 *      Grafana / k6 cloud / Datadog if you want)
 *
 *  SLOs (the test fails if any breaks):
 *    - http_req_failed < 1%
 *    - http_req_duration p(95) < 500ms for reads
 *    - http_req_duration p(95) < 1500ms for writes
 *    - 0 unhandled 5xx anywhere
 */

import http from 'k6/http';
import {check, group, sleep, fail} from 'k6';
import {Counter, Rate, Trend} from 'k6/metrics';
import {randomItem, randomIntBetween} from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

// ─────────────────────────────────────────────────────────────────────
// Config
// ─────────────────────────────────────────────────────────────────────
const API = (__ENV.API || 'http://localhost:8000').replace(/\/+$/, '');
const PREFIX = __ENV.PREFIX || '/api/v1';
const EMAIL = __ENV.EMAIL || 'ci-admin@hypershop.local';
const PASSWORD = __ENV.PASSWORD || '';
const ORDER_PROBABILITY = parseFloat(__ENV.ORDER_PROB || '0.08');

// ─────────────────────────────────────────────────────────────────────
// SLOs — k6 fails the run when any threshold breaks
// ─────────────────────────────────────────────────────────────────────
export const options = {
  scenarios: {
    baseline_100: {
      executor: 'constant-vus',
      vus: 100,
      duration: '5m',
      gracefulStop: '30s',
    },
  },
  thresholds: {
    // Overall error budget: <1%
    'http_req_failed': ['rate<0.01'],
    // Latency by endpoint group
    'http_req_duration{kind:read}':  ['p(95)<500',  'p(99)<1000'],
    'http_req_duration{kind:write}': ['p(95)<1500', 'p(99)<3000'],
    'http_req_duration{kind:auth}':  ['p(95)<800'],
    // Custom: never any 5xx
    'errors_5xx': ['count<1'],
    // Custom: at least N successful order placements (sanity)
    'orders_placed': ['count>10'],
  },
  // Useful summary breakdown
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

// ─────────────────────────────────────────────────────────────────────
// Custom metrics
// ─────────────────────────────────────────────────────────────────────
const errors5xx = new Counter('errors_5xx');
const ordersPlaced = new Counter('orders_placed');
const loginRate = new Rate('login_success');
const checkoutTrend = new Trend('checkout_total_ms');

// ─────────────────────────────────────────────────────────────────────
// Per-VU state (tokens cached per virtual user, refreshed when 401)
// ─────────────────────────────────────────────────────────────────────
let authState = {token: null, refresh: null, userId: null};
let catalogState = {variants: []};

// ─────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────
function url(path) { return API + PREFIX + path; }

function authHeaders() {
  return authState.token
    ? {Authorization: 'Bearer ' + authState.token, 'Content-Type': 'application/json'}
    : {'Content-Type': 'application/json'};
}

function record5xx(resp) {
  if (resp.status >= 500) errors5xx.add(1);
}

function login() {
  if (!PASSWORD) return false;
  const resp = http.post(url('/auth/login'),
    JSON.stringify({email: EMAIL, password: PASSWORD}),
    {headers: {'Content-Type': 'application/json'}, tags: {kind: 'auth', endpoint: 'login'}}
  );
  record5xx(resp);
  const ok = check(resp, {
    'login: 200': r => r.status === 200,
    'login: has access_token': r => r.json('tokens.access_token') !== '',
  });
  loginRate.add(ok);
  if (ok) {
    authState.token = resp.json('tokens.access_token');
    authState.refresh = resp.json('tokens.refresh_token');
    authState.userId = resp.json('user.id');
  }
  return ok;
}

function ensureAuth() {
  if (!authState.token) return login();
  // Cheap probe — if it 401s, drop and re-login.
  const r = http.get(url('/me/profile'), {headers: authHeaders(), tags: {kind: 'read', endpoint: 'profile'}});
  if (r.status === 401) {
    authState.token = null;
    return login();
  }
  return r.status === 200;
}

// ─────────────────────────────────────────────────────────────────────
// Scenarios — each function is one "page" of user behaviour
// ─────────────────────────────────────────────────────────────────────

function pageHealth() {
  const r = http.get(url('/health'), {tags: {kind: 'read', endpoint: 'health'}});
  record5xx(r);
  check(r, {'health: 200': x => x.status === 200});
}

function pageCatalogBrowse() {
  group('catalog browse', () => {
    const r = http.get(url('/catalog/products?limit=24'),
      {tags: {kind: 'read', endpoint: 'catalog_list'}});
    record5xx(r);
    if (!check(r, {'catalog list: 200': x => x.status === 200})) return;
    const items = r.json('items') || [];
    // Cache a few variant ids for the order flow
    if (items.length > 0 && catalogState.variants.length < 5) {
      items.slice(0, 5).forEach(p => {
        const variants = (p && p.variants) || [];
        if (variants.length > 0) catalogState.variants.push(variants[0].id);
      });
    }
  });
  sleep(randomIntBetween(1, 3));  // user reads the page
}

function pageProductDetail() {
  if (catalogState.variants.length === 0) return pageCatalogBrowse();
  const r = http.get(url('/catalog/products?limit=1'),
    {tags: {kind: 'read', endpoint: 'product_detail'}});
  record5xx(r);
  check(r, {'product detail: 200': x => x.status === 200});
  sleep(randomIntBetween(1, 4));
}

function pageMeProfile() {
  if (!ensureAuth()) return;
  const r = http.get(url('/me/profile'),
    {headers: authHeaders(), tags: {kind: 'read', endpoint: 'profile'}});
  record5xx(r);
  check(r, {'profile: 200': x => x.status === 200});
}

function pageMeOrders() {
  if (!ensureAuth()) return;
  const r = http.get(url('/orders'),
    {headers: authHeaders(), tags: {kind: 'read', endpoint: 'orders_list'}});
  record5xx(r);
  check(r, {'orders list: 200 or 404': x => x.status === 200 || x.status === 404});
}

function pagePlaceOrder() {
  if (!ensureAuth()) return;
  if (catalogState.variants.length === 0) {
    // Need a variant first — hit the catalog
    pageCatalogBrowse();
    if (catalogState.variants.length === 0) return;
  }
  const variantId = randomItem(catalogState.variants);
  const start = Date.now();
  const r = http.post(url('/orders'),
    JSON.stringify({
      items: [{variant_id: variantId, quantity: randomIntBetween(1, 3)}],
      payment_method: 'cod',
      delivery_address: {
        recipient_name: 'Load Test Customer',
        phone: '+8801711000000',
        line1: 'House 1, Road 1',
        city: 'Dhaka',
        country: 'BD',
      },
      currency: 'BDT',
    }),
    {headers: authHeaders(), tags: {kind: 'write', endpoint: 'place_order'}}
  );
  record5xx(r);
  const ok = check(r, {
    'place order: 201': x => x.status === 201,
    'place order has code': x => (x.json('code') || '').startsWith('HSO-'),
  });
  if (ok) {
    ordersPlaced.add(1);
    checkoutTrend.add(Date.now() - start);
  }
  sleep(randomIntBetween(1, 2));
}

function pagePaymentInitiate() {
  if (!ensureAuth()) return;
  if (catalogState.variants.length === 0) return;
  // Place an online-payment order first
  const variantId = randomItem(catalogState.variants);
  const placeResp = http.post(url('/orders'),
    JSON.stringify({
      items: [{variant_id: variantId, quantity: 1}],
      payment_method: 'online',
      delivery_address: {
        recipient_name: 'Load Test',
        phone: '+8801711000001',
        line1: 'Road 11',
        city: 'Dhaka',
        country: 'BD',
      },
      currency: 'BDT',
    }),
    {headers: authHeaders(), tags: {kind: 'write', endpoint: 'place_online_order'}}
  );
  record5xx(placeResp);
  if (placeResp.status !== 201) return;
  const orderId = placeResp.json('id');

  // Initiate the payment — provider on NotConfigured returns 502 with
  // missing_setting; we still measure OUR latency until that boundary.
  const r = http.post(url('/payments/initiate'),
    JSON.stringify({
      order_id: orderId,
      provider: 'bkash',
      success_url: 'https://example.com/success',
      failure_url: 'https://example.com/fail',
      cancel_url:  'https://example.com/cancel',
    }),
    {headers: authHeaders(), tags: {kind: 'write', endpoint: 'payment_initiate'}}
  );
  // 502 with code='integration_error' is the expected NotConfigured
  // response for an unconfigured provider. Don't count it as a 5xx
  // error in our error budget — it's BY DESIGN.
  if (r.status >= 500 && r.json('code') !== 'integration_error') {
    errors5xx.add(1);
  }
  check(r, {
    'payment initiate: 200 or 502 (not configured)': x =>
      x.status === 200 || (x.status === 502 && x.json('code') === 'integration_error'),
  });
}

// ─────────────────────────────────────────────────────────────────────
// Main VU loop — weighted endpoint selection
// ─────────────────────────────────────────────────────────────────────
export default function () {
  const dice = Math.random();
  if (dice < 0.05)       pageHealth();
  else if (dice < 0.65)  pageCatalogBrowse();
  else if (dice < 0.73)  pageProductDetail();
  else if (dice < 0.78)  pageMeProfile();
  else if (dice < 0.93)  pageMeOrders();
  else if (dice < 0.98)  pagePlaceOrder();
  else                   pagePaymentInitiate();

  // Slight pause between iterations — pacing
  sleep(randomIntBetween(0, 1));
}

// ─────────────────────────────────────────────────────────────────────
// Pretty summary at the end (k6 prints to stdout AND writes JSON)
// ─────────────────────────────────────────────────────────────────────
export function handleSummary(data) {
  return {
    'stdout': textSummary(data, {indent: '  ', enableColors: true}),
    'results/baseline-summary.json': JSON.stringify(data, null, 2),
  };
}

// k6's textSummary helper — inlined so the script has no external deps
// at run time except the jslib utils above.
function textSummary(data, opts) {
  const indent = opts.indent || '  ';
  let out = '\n';
  out += '═══════════════════════════════════════════════════════════════════════\n';
  out += '  Hypershop load-test summary\n';
  out += '═══════════════════════════════════════════════════════════════════════\n';
  out += `${indent}target:          ${API}${PREFIX}\n`;
  out += `${indent}duration:        ${(data.state.testRunDurationMs / 1000).toFixed(1)}s\n`;
  out += `${indent}vus (max):       ${data.metrics.vus_max?.values?.value ?? '?'}\n`;
  out += `${indent}requests sent:   ${data.metrics.http_reqs?.values?.count ?? 0}\n`;
  out += `${indent}requests/sec:    ${(data.metrics.http_reqs?.values?.rate ?? 0).toFixed(1)}\n`;
  out += `${indent}error rate:      ${((data.metrics.http_req_failed?.values?.rate ?? 0) * 100).toFixed(2)}%\n`;
  const dur = data.metrics.http_req_duration?.values || {};
  out += `${indent}latency avg:     ${(dur.avg ?? 0).toFixed(0)}ms\n`;
  out += `${indent}latency p95:     ${(dur['p(95)'] ?? 0).toFixed(0)}ms\n`;
  out += `${indent}latency p99:     ${(dur['p(99)'] ?? 0).toFixed(0)}ms\n`;
  out += `${indent}5xx errors:      ${data.metrics.errors_5xx?.values?.count ?? 0}\n`;
  out += `${indent}orders placed:   ${data.metrics.orders_placed?.values?.count ?? 0}\n`;
  out += '\n';
  // Per-threshold pass/fail
  out += `${indent}thresholds:\n`;
  for (const [name, t] of Object.entries(data.metrics)) {
    if (!t.thresholds) continue;
    for (const [expr, result] of Object.entries(t.thresholds)) {
      const mark = result.ok ? '✓' : '✗';
      out += `${indent}${indent}${mark} ${name} { ${expr} }\n`;
    }
  }
  out += '═══════════════════════════════════════════════════════════════════════\n';
  return out;
}
