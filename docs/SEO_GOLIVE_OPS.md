# SEO go-live operator checklist

When `https://hypershop.com.bd` (or whichever production hostname) goes
live, walk through this list in order. Each step is independent —
nothing breaks if one is skipped, but each one earns indexing coverage
on a different search surface.

## Prereqs (must be true before any verification)

- `SEO_SITE_URL` set to the real https hostname (NOT `*.example`)
- DNS A/AAAA records pointing at the production load balancer
- Valid TLS cert (Let's Encrypt or paid) — search consoles refuse
  to verify self-signed hosts
- `/robots.txt` returns 200 with a `Sitemap:` line pointing at the
  real sitemap
- `/sitemap.xml` returns 200 with valid XML

Smoke them with:

```bash
curl -I https://hypershop.com.bd/robots.txt
curl -I https://hypershop.com.bd/sitemap.xml
```

## 1. Google Search Console

1. Sign in at <https://search.google.com/search-console>
2. **Add property** → **URL prefix** → paste `https://hypershop.com.bd/`
3. Pick **HTML tag** verification → copy the `content="..."` value
4. Backend `.env`: `SEO_VERIFY_GOOGLE=<token>` → restart
5. FE rebuild (`pnpm --filter customer-web build`)
6. Click **Verify** in GSC — succeeds within seconds
7. **Sitemaps** tab → submit `https://hypershop.com.bd/sitemap.xml`
8. **Indexing → Pages** to track coverage growth

## 2. Bing Webmaster Tools

1. Sign in at <https://www.bing.com/webmasters/>
2. **Add a site** → enter the URL
3. Pick **Add a meta tag** → copy the `content="..."` from
   `<meta name="msvalidate.01" content="...">`
4. Backend `.env`: `SEO_VERIFY_BING=<token>` → restart
5. FE rebuild
6. Click **Verify**
7. **Sitemaps** → submit `https://hypershop.com.bd/sitemap.xml`
8. **Configure My Site → IndexNow** — paste the key from
   `SEO_INDEXNOW_KEY` (or generate a new one and copy back into env)

## 3. Yandex Webmaster

Russia + Bangladesh diaspora traffic — small but cheap to claim.

1. Sign in at <https://webmaster.yandex.com/>
2. **Add site** → URL
3. Pick **Meta tag** → copy `content="..."` from
   `<meta name="yandex-verification" content="...">`
4. `.env`: `SEO_VERIFY_YANDEX=<token>` → restart + rebuild
5. Verify → submit `sitemap.xml`

## 4. Naver Search Advisor

Korea. Optional but harmless. Uses `naver-site-verification`.

1. Sign in at <https://searchadvisor.naver.com/>
2. **Add a site** → pick `HTML tag` verification
3. `.env`: `SEO_VERIFY_NAVER=<token>` → restart + rebuild
4. Verify → submit `sitemap.xml`

## 5. Seznam Webmaster

Czech. Skip unless targeting EU expansion. `seznam-wmt`.

1. <https://www.seznam.cz/wmt> → Add site → HTML tag
2. `.env`: `SEO_VERIFY_SEZNAM=<token>` → restart + rebuild

## 6. Facebook Domain Verification

Required before Commerce Manager will ingest the catalog.

1. <https://business.facebook.com/> → **Business Settings** →
   **Brand Safety → Domains** → add domain
2. Pick **Meta-tag** verification → copy `content="..."` from
   `<meta name="facebook-domain-verification" content="...">`
3. `.env`: `SEO_VERIFY_FACEBOOK=<token>` → restart + rebuild
4. **Verify Domain**
5. Then in Commerce Manager: **Catalogs → Data Sources → Website**
   → paste `https://hypershop.com.bd/sitemap.xml`

## 7. Pinterest Rich Pins + Verified Merchant

1. <https://www.pinterest.com/business/verify/> → add `hypershop.com.bd`
2. Pick **Add HTML tag** → copy `content="..."` from
   `<meta name="p:domain_verify" content="...">`
3. `.env`: `SEO_VERIFY_PINTEREST=<token>` → restart + rebuild
4. Verify → Pinterest Rich Pins activate automatically because the
   storefront already emits `og:price:amount` / `product:availability`
   on every PDP (see `productOgMeta()` in
   `packages/utils/src/seo.ts`)
5. Apply for **Verified Merchant** under Business → Settings

## 8. IndexNow activation (Bing + Yandex + Naver + Seznam)

1. Generate a hex key — `python -c "import secrets; print(secrets.token_hex(16))"`
2. `.env`: `SEO_INDEXNOW_KEY=<the-key>` + `SEO_INDEXNOW_ENABLED=true`
   → restart
3. Verify the key file is reachable:
   ```bash
   curl https://hypershop.com.bd/<KEY>.txt
   # should echo the same key
   ```
4. Trigger a one-shot bulk push to seed the engines:
   ```python
   # In a Python shell against the backend
   import asyncio
   from app.modules.seo.jobs import indexnow_bulk_publish_job
   asyncio.run(indexnow_bulk_publish_job({}))
   ```
5. ARQ worker config: ensure these cron jobs are registered:
   - `indexnow_ping_job` — every 60s (drains the per-publish queue)
   - `indexnow_bulk_publish_job` — weekly Sunday 04:00 UTC
   - `sitemap_submit_job` — daily 03:30 UTC (Bing + Google + Yandex
     ping URLs)

## 9. Schema validation

Before declaring victory, validate the structured-data on the live
PDP + home + blog + AMP:

```bash
# Pick a real product
curl -sS https://hypershop.com.bd/en/product/<slug> \
  | grep -oP '(?<=<script type="application/ld\+json">)[^<]+' \
  | jq .
```

- Paste each block into <https://search.google.com/test/rich-results>
- Validator URL: <https://validator.schema.org/>
- AMP validator: <https://validator.amp.dev>

The PR-time `seo-audit.yml` GitHub Action does this on every push, so
green CI = no schema regressions before they land.

## 10. Post-go-live monitoring

- GSC **Coverage** report — should crawl 80%+ of sitemap URLs in 7-14
  days; investigate excluded URLs (often noindex meta on cart/checkout
  which is intentional)
- GSC **Page experience** — Core Web Vitals from real-user telemetry.
  Target: 75%+ URLs in "Good" for LCP + INP + CLS
- Bing Webmaster **Site Explorer** — IndexNow submissions should show
  in the "URL submission" report within minutes of an admin publish
- Merchant Center **Diagnostics** — watch for `disapproved` items;
  the admin SEO audit dashboard at `/admin/seo-audit` shows the
  attribute backlog (gtin, material, energy_class) that drives the
  bulk of disapprovals

## Quick env reference

```ini
# Site identity
SEO_SITE_URL=https://hypershop.com.bd
SEO_SITE_NAME=Hypershop

# NAP for LocalBusiness
SEO_ORG_PHONE=+8809612345678
SEO_ORG_STREET="House 12, Road 4, Banani"
SEO_ORG_REGION=Dhaka
SEO_ORG_POSTAL_CODE=1213
SEO_ORG_LAT=23.7937
SEO_ORG_LNG=90.4066
SEO_ORG_OPENING_HOURS="Mo-Su 09:00-22:00"

# ContactPoint
SEO_CONTACT_SUPPORT_PHONE=+8809612000001
SEO_CONTACT_SALES_PHONE=+8809612000002

# Webmaster verification (fill in as each console issues a token)
SEO_VERIFY_GOOGLE=
SEO_VERIFY_BING=
SEO_VERIFY_YANDEX=
SEO_VERIFY_NAVER=
SEO_VERIFY_SEZNAM=
SEO_VERIFY_FACEBOOK=
SEO_VERIFY_PINTEREST=

# IndexNow
SEO_INDEXNOW_ENABLED=true
SEO_INDEXNOW_KEY=<hex>
```
