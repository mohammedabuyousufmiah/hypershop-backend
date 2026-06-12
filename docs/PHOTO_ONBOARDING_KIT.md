# Photo onboarding kit — top BD brands

Date: 2026-05-25  
Owner: Yousuf  
Target: image sitemap coverage for 12k bulk products

## Brand priority (by SKU count in catalog)

| Rank | Brand | SKUs | Press-kit source | License |
|---|---|---|---|---|
| 1 | Baseus | 943 | https://www.baseus.com/pages/media-kit | Authorised distributor only |
| 2 | Samsung | 938 | https://news.samsung.com/global/about-us/ci-bi | Free with attribution |
| 3 | Midea | 933 | https://www.midea.com/global/about-us/media | Distributor approval needed |
| 4 | DJI | 929 | https://www.dji.com/newsroom/media-resources | Free with attribution |
| 5 | Miyako | 928 | Local distributor (Olympic Industries BD) — request via email | BD-specific licence |
| 6 | Walton | 924 | https://waltonbd.com/press-room | Free for resellers |
| 7 | Asus | 918 | https://press.asus.com | Free with attribution |
| 8 | Vision | 909 | RFL Electronics BD — request via Vision dealer portal | BD distributor licence |
| 9 | Xiaomi | 906 | https://i01.appmifile.com/webfile/globalweb/media/ | Free with attribution |
| 10 | Sony | 901 | https://presscentre.sony.eu/ | Free with attribution |
| 11 | Lenovo | 585 | https://news.lenovo.com/press-kits/ | Free with attribution |
| 12 | Canon | 575 | https://global.canon/en/news/ | Free with attribution |
| 13 | A4Tech | 575 | https://www.a4tech.com/category/0/News/News | Free for resellers |

Skip rows with brand = "Generic" / "Premium Brand" (synthetic seed data).

## Per-brand workflow (Tier A)

**Two execution paths — manual download vs URL-manifest:**

### Path 1 — manual press-kit ZIP

```
1. Visit brand press portal in a real browser (most refuse scrapers)
2. Download press kit ZIP / unpack to /staging/<brand>/raw/
3. Run filename fuzzy-match ingest:
   .venv/Scripts/python -m scripts.ingest_photos \
       --folder "/staging/<brand>/raw/" \
       --brand-slug <brand> \
       --match-mode filename \
       --threshold 0.45 \
       --unmatched-csv /staging/<brand>/unmatched.csv
4. Review unmatched.csv → manually pin via filename_overrides in
   plan YAML if any high-value SKUs missed
```

### Path 2 — URL manifest (no browser needed)

When you have direct image CDN URLs (e.g. from the brand's spec page
right-click → "Copy image address"):

```
1. Build manifest:
   brand_slug,url,filename
   samsung,https://images.samsung.com/.../sm-s928-front.jpg,samsung_galaxy_s24_ultra_front.jpg
   ...
2. Download all:
   .venv/Scripts/python -m scripts.download_brand_photos \
       --manifest scripts/<brand>_manifest.csv \
       --dest "/staging/photos/"
3. Ingest (same as Path 1 step 3):
   .venv/Scripts/python -m scripts.ingest_photos \
       --folder "/staging/photos/<brand>/" \
       --brand-slug <brand> \
       --match-mode filename
```

### Brand press-kit reality check

Tested 2026-05-25:
- `news.samsung.com/global/category/products` → 403 to scrapers
- `news.lenovo.com/press-kits/` → 403
- Direct CDN URLs on `images.samsung.com` / `www-cdn.djiits.com` →
  some 404 (old paths), some 403 (require Referer match)

→ **Operator must visit the brand site in a browser to grab URLs.**
   The downloader script spoofs a Chrome UA + sends Referer, which
   handles most CDN gates but not page-protected portals.

## CSV format (recap)

```
sku,image_path,alt,position
HSBASEUS-PC-001,baseus_powerbank_30000mah_front.jpg,Baseus 30000mAh Power Bank front,0
HSBASEUS-PC-001,baseus_powerbank_30000mah_top.jpg,Baseus 30000mAh Power Bank top view,1
...
```

## Quick-wins for the 124 real (non-bulk) products

Real catalog already has image coverage at 124/124. Tier A free brands
above let you backfill the bulk products for free per attribution.
The 7 "free with attribution" brands cover ~4,500 SKUs (37% of bulk
catalog) at zero cost.

## Tier B (seller drop)

Once seller-panel onboarding flows live, force each seller to upload
≥1 image per SKU during product activation (`/admin/sellers/onboard`
gate). Then the catalog grows image coverage organically with new
listings.

## Tier C (Daraz/Pickaboo scrape)

Legal grey area — only do this if the brand/seller has explicit
co-listing agreement. Otherwise stick to Tier A + Tier B.

## Tier D (in-house studio)

Hire BD product photographer for the top-500 traffic SKUs after
GSC tells you which products are getting organic clicks. Skip until
analytics is showing winners.
