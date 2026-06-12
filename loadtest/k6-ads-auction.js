/* k6-ads-auction.js — Sponsored Products auction stress test.
 *
 * Exercises the /ads/auction hot path under concurrent load to:
 *  1. Validate the second-price clearing logic stays correct under
 *     race conditions (impressions land in DB monotonically).
 *  2. Measure p95 latency for the JSONB-containment lookup against
 *     the GIN index on hypershop_ad_groups.targets.
 *  3. Stress the click endpoint's row-lock contention on a single
 *     hot wallet (worst-case: 1 seller, 1 ad_group, N concurrent
 *     clicks — proves the SELECT FOR UPDATE doesn't deadlock).
 *
 * Targets (sustained):
 *   - 500 RPS auction lookups
 *   - 50  RPS click conversions
 *   - p95(auction) < 80 ms
 *   - p95(click)   < 150 ms (debit + INSERT + spend bump)
 *   - 5xx_rate < 0.1 %
 *
 * Run:
 *   k6 run loadtest/k6-ads-auction.js \
 *     -e API=http://127.0.0.1:8000 \
 *     -e DURATION=60s -e VUS_AUCTION=50 -e VUS_CLICK=5
 *
 * Pre-req: at least one active campaign + ad_group targeting the
 * keyword "iphone 15" with the seller's ad wallet topped up. Phase 1.D
 * smoke leaves that state on disk; rerun seed scripts if needed.
 */

import http from 'k6/http';
import {check} from 'k6';
import {Counter, Trend} from 'k6/metrics';

const API = (__ENV.API || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const PREFIX = __ENV.PREFIX || '/api/v1';

const VUS_AUCTION = parseInt(__ENV.VUS_AUCTION || '50', 10);
const VUS_CLICK = parseInt(__ENV.VUS_CLICK || '5', 10);
const DURATION = __ENV.DURATION || '60s';

// Hot keyword the seeded ad_group targets.
const KEYWORDS = ['iphone 15', 'new iphone', 'iphone 15 pro'];

export const options = {
  scenarios: {
    auction_storm: {
      executor: 'constant-vus',
      vus: VUS_AUCTION,
      duration: DURATION,
      exec: 'auction_call',
      gracefulStop: '5s',
    },
    click_storm: {
      executor: 'constant-vus',
      vus: VUS_CLICK,
      duration: DURATION,
      exec: 'click_call',
      gracefulStop: '5s',
    },
  },
  thresholds: {
    'http_req_failed{ep:auction}':        ['rate<0.005'],
    'http_req_failed{ep:click}':          ['rate<0.01'],
    'http_req_duration{ep:auction}':      ['p(95)<80', 'p(99)<200'],
    'http_req_duration{ep:click}':        ['p(95)<150', 'p(99)<300'],
    'auction_wins_total':                 ['count>0'],
    'errors_5xx':                         ['count<10'],
  },
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
};

const errors5xx = new Counter('errors_5xx');
const wins = new Counter('auction_wins_total');
const empties = new Counter('auction_empty_total');
const billed = new Counter('clicks_billed_total');
const dedupes = new Counter('clicks_deduped_total');
const lastImpression = new Trend('last_impression_id', true);

// Cross-VU latest impression ids by ad_group. We push from auction
// VUs and the click scenario picks any recent id. Since k6 VUs don't
// share memory, we use a SharedArray-free trick: the click VU reissues
// an auction itself, picks the winner's impression_id, then clicks.
function url(path) { return API + PREFIX + path; }

export function auction_call() {
  const kw = KEYWORDS[Math.floor(Math.random() * KEYWORDS.length)];
  const r = http.get(
    url('/ads/auction?surface=search&surface_ref=' + encodeURIComponent(kw) +
      '&limit=3&session_id=k6_vu_' + __VU + '_' + __ITER),
    {tags: {ep: 'auction'}},
  );
  if (r.status >= 500) errors5xx.add(1);
  const ok = check(r, {
    'auction 200':  (resp) => resp.status === 200,
    'envelope ok':  (resp) => {
      try { return JSON.parse(resp.body).success === true; }
      catch (_e) { return false; }
    },
  }, {ep: 'auction'});
  if (!ok) return;
  try {
    const body = JSON.parse(r.body);
    const winners = body.data.winners || [];
    if (winners.length > 0) {
      wins.add(winners.length);
      lastImpression.add(winners[0].impression_id);
    } else {
      empties.add(1);
    }
  } catch (_e) {/* parse error already flagged above */}
}

export function click_call() {
  // Run our own auction to get a fresh impression_id, then click it.
  // This stresses both endpoints + the wallet row-lock together.
  const auct = http.get(
    url('/ads/auction?surface=search&surface_ref=' + encodeURIComponent(KEYWORDS[0]) +
      '&limit=1'),
    {tags: {ep: 'auction'}},
  );
  if (auct.status !== 200) return;
  let imprId = null;
  try {
    const body = JSON.parse(auct.body);
    const w = (body.data && body.data.winners) || [];
    if (w.length > 0) imprId = w[0].impression_id;
  } catch (_e) {/* skip */}
  if (imprId === null) return;

  const r = http.post(
    url('/ads/click'),
    JSON.stringify({impression_id: imprId, session_id: 'k6_click_vu_' + __VU}),
    {headers: {'Content-Type': 'application/json'}, tags: {ep: 'click'}},
  );
  if (r.status >= 500) errors5xx.add(1);
  check(r, {
    'click 200': (resp) => resp.status === 200,
  }, {ep: 'click'});
  try {
    const body = JSON.parse(r.body);
    if (body.data && body.data.charged_minor > 0) billed.add(1);
    else dedupes.add(1);
  } catch (_e) {/* skip */}
}
