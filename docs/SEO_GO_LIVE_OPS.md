# SEO Go-Live Operator Checklist

Date: 2026-05-25
Owner: Yousuf
Backend: `_serve_final/hypershop-backend`

## A. Catalog activation — DONE ✅

| Check | Result |
|---|---|
| Products status='active' | 12,236 / 12,236 |
| seo_meta_overrides rows | 12,236 (1:1) |
| seo_meta_translations BN rows | 12,112 |
| seo_url_redirects (dedupe) | ~47,888 |
| `/sitemap.xml` index live | 4 child shards (static/products/categories/brands) |
| `/sitemap-products-0.xml` | 12,236 URLs, lastmod set, priority=0.9, changefreq=daily |
| Breadcrumb JSON-LD on PDP | 3-level (Home → Category → Product) live |
| Image namespace declared | Yes — populated for products with media |

## A.1 IndexNow — CREDS PENDING ⚠️

IndexNow is the free Bing/Yandex/Naver protocol that pings search engines on every URL change. Currently disabled (default).

**Operator action (one-time):**

1. Go to Bing Webmaster Tools → https://www.bing.com/webmasters → IndexNow → copy the auto-generated key.
2. Add to `.env`:
   ```
   seo_indexnow_enabled=true
   seo_indexnow_key=<paste-key-here>
   seo_indexnow_host=hypershop.com.bd
   ```
3. Restart backend.
4. Verify the key file is served:
   ```
   curl https://hypershop.com.bd/<key>.txt
   # → should return the key as plain text
   ```
5. Trigger one-time bulk flood:
   ```python
   from app.modules.seo.jobs import indexnow_bulk_publish_job
   await indexnow_bulk_publish_job({})
   # Enqueues every active product/category/brand/blog URL → ARQ ping job
   # drains 10k URLs per batch every 60s.
   ```
6. Monitor in Bing Webmaster Tools → IndexNow → Submission History.

ARQ cron `indexnow_ping_job` runs every 60s automatically after that — every product activate / blog publish auto-pings within 1 minute.

## B. FAQ seed — see task B below

## C. Google Search Console + Bing — CREDS PENDING ⚠️

Verification meta tags are wired into `frontend/apps/customer-web/app/layout.tsx`
via `metadata.verification`. Empty env value = tag is dropped entirely (no
placeholder ship — wrong token can brick GSC ownership).

**Operator action:**

1. **Google Search Console**
   - Open https://search.google.com/search-console
   - Add property `https://hypershop.com.bd`
   - Choose verification method: **HTML tag**
   - Copy the `<meta name="google-site-verification" content="XXX">` tag
   - Set the FE env on the deployment target (Vercel / docker):
     ```
     NEXT_PUBLIC_SEO_VERIFY_GOOGLE=<content-value-only>
     ```
   - Redeploy customer-web; view-source → see the meta tag.
   - Back in GSC → click "Verify".
   - GSC → Sitemaps → enter `sitemap.xml` → Submit.

2. **Bing Webmaster Tools**
   - Open https://www.bing.com/webmasters
   - Add site → HTML meta verification
   - Set:
     ```
     NEXT_PUBLIC_SEO_VERIFY_BING=<content-value>
     ```
   - Bing → Sitemaps → enter `https://hypershop.com.bd/sitemap.xml`.

3. **Yandex Webmaster** (optional, ~1% BD traffic but free)
   - https://webmaster.yandex.com
   - Set:
     ```
     NEXT_PUBLIC_SEO_VERIFY_YANDEX=<content>
     ```

4. **Facebook + Pinterest** (commerce surfaces)
   - Facebook Commerce Manager → domain verification → HTML tag flow.
   - Pinterest → claim website → HTML tag flow.
   - Set:
     ```
     NEXT_PUBLIC_SEO_VERIFY_FACEBOOK=<content>
     NEXT_PUBLIC_SEO_VERIFY_PINTEREST=<content>
     ```

Daily sitemap auto-submit cron (`sitemap_submit_job` in
`app/modules/seo/jobs.py`) fires nightly 02:00 UTC; pings
`bing.com/ping?sitemap=...` (Google's ping endpoint is deprecated since
2023 — submit once via GSC and rely on lastmod after that).

**Verify locally before pushing to prod:**

```bash
# After setting NEXT_PUBLIC_SEO_VERIFY_GOOGLE=test123 in .env.local
cd frontend/apps/customer-web && pnpm dev
curl -s http://localhost:3000/ | grep "google-site-verification"
# → <meta name="google-site-verification" content="test123"/>
```

## D. Real product photo bulk-upload — PIPELINE READY ✅

`scripts/upload_product_images.py` ingests a CSV of (sku, image_path,
alt, position) and:
1. Resolves sku → product_id (via `products.mother_sku` or `product_variants.sku`)
2. Resizes image to ≤ 1600×1600 + re-encodes JPEG q85
3. Uploads to R2 (`<bucket>/<r2_image_prefix>products/<product_id>/<position>.jpg`)
   OR local fallback `uploads/products/<product_id>/<position>.jpg`
4. INSERTs ProductMedia row (skips if same product_id+position exists)

**NO synthetic placeholders** — failed file = row skipped, never invents URLs.

### CSV template

See `scripts/product_images_template.csv`.

### Usage

```bash
# Dry-run first to validate SKUs + image paths
.venv/Scripts/python -m scripts.upload_product_images \
    --csv photos.csv \
    --image-root /path/to/image/folder \
    --dry-run

# Real run (writes to R2 if configured, else uploads/products/ locally)
.venv/Scripts/python -m scripts.upload_product_images \
    --csv photos.csv \
    --image-root /path/to/image/folder
```

### R2 env (for production)

```
R2_BUCKET_NAME=hypershop-catalog
R2_ACCOUNT_ID=<cloudflare-account-id>
R2_ACCESS_KEY_ID=<key>
R2_SECRET_ACCESS_KEY=<secret>
R2_PUBLIC_BASE_URL=https://cdn.hypershop.com.bd
R2_IMAGE_PREFIX=img/catalog/
```

### Once images are uploaded

- Sitemap regenerates automatically on next `/sitemap-products-*.xml` fetch.
- PDP `og:image` populates from the first ProductMedia row.
- `<image:image>` tag in product sitemap publishes Google Image Search.

### Source-photo strategy for 12k bulk products

| Tier | Source | Effort |
|---|---|---|
| **A — Brand catalog** | Pull brand-official press kits (Apple/Samsung/Lenovo etc.) | low |
| **B — Seller drop** | Each seller uploads via /admin/sellers → bulk-image batch UI | medium |
| **C — Reseller scrape** | Daraz / Pickaboo allow scraping product images per legal usage clauses; verify per brand | medium |
| **D — Studio shoot** | Hire BD product photographer for top 500 high-traffic SKUs | high but premium |

Recommended order: A (free, brand official) → B (seller responsibility) → D (top sellers only).


## Day-0 ranking timeline (realistic)

| Signal | When |
|---|---|
| GSC sitemap accepted | < 24h |
| First crawl on bulk URLs | 1-3 days |
| Long-tail BN queries indexed | 7-30 days |
| Brand+product ranking | 1-3 months |
| Category-head ranking | 6-12 months (Daraz/Pickaboo wall) |
| "online shopping bangladesh" SERP | 18-36 months |
