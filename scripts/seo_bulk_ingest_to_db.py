"""Bulk DB ingest of the cleaned + deduped Daraz 10k SEO sheet
(task #172 step 4, 2026-05-25).

Reads ``Daraz_Style_10k_SEO_Items.cleaned.xlsx`` (output of
seo_quality_pipeline.py) and:

  1. Upserts Brand rows for every unique brand in the sheet
  2. Upserts Category rows for every unique top-level category
  3. INSERTs Product rows for every CANONICAL row only (status=draft so
     they don't leak into the public storefront until ops promotes
     them). Each product gets:
        - mother_sku = sku from sheet
        - attributes = {seo_ingest: True, source: daraz_10k_xlsx, ...}
        - is_medicine = False, requires_prescription = False
        - base_currency = "BDT"
  4. INSERTs SeoMetaOverride (en) + SeoMetaTranslation (bn) with the
     pre-built meta from the sheet, flagged auto_generated=True so the
     live engine yields to ops if they later edit by hand
  5. INSERTs UrlRedirect rows for every non-canonical slug -> canonical
     slug, status 301 (handled by /r/{path} root endpoint)

Idempotent: re-running over the same sheet skips Products whose
mother_sku already exists. Brand/Category upsert by name.

Usage:
  .venv/Scripts/python -m scripts.seo_bulk_ingest_to_db \
    --in "C:/.../Daraz_Style_10k_SEO_Items.cleaned.xlsx" \
    --limit 100         # smoke test with first 100 canonical rows
    --dry-run           # report only, no writes
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import openpyxl

from app.core.db.session import get_sessionmaker
from sqlalchemy import select, text


# Column index map for the cleaned xlsx (matches seo_quality_pipeline._NEW_COLS)
COL = {
    "sku": 0, "category": 1, "brand": 2, "core": 3, "spec": 4,
    "stock": 5, "price": 6, "cleaned_title": 7,
    "slug": 8, "canonical_url": 9, "canonical_slug": 10, "is_canonical": 11,
    "meta_title_en": 12, "meta_desc_en": 13, "meta_keywords_en": 14,
    "meta_title_bn": 15, "meta_desc_bn": 16, "meta_keywords_bn": 17,
    "jsonld_product": 18,
    "audience_fixed": 19,
}


def _slug(text_in: str) -> str:
    import re
    text_in = (text_in or "").lower().strip()
    text_in = re.sub(r"[^a-z0-9\s-]", "", text_in)
    text_in = re.sub(r"\s+", "-", text_in)
    return re.sub(r"-+", "-", text_in).strip("-")[:80]


async def _upsert_brand(session, name: str) -> str:
    """Returns UUID hex string of brand row."""
    res = await session.execute(
        text("SELECT id FROM brands WHERE name = :n"),
        {"n": name},
    )
    row = res.first()
    if row:
        return str(row[0])
    bid = uuid4()
    await session.execute(
        text(
            "INSERT INTO brands (id, name, slug, is_active) "
            "VALUES (:id, :n, :s, true)"
        ),
        {"id": bid, "n": name, "s": _slug(name)},
    )
    return str(bid)


async def _upsert_category(session, name: str) -> str:
    res = await session.execute(
        text(
            "SELECT id FROM categories WHERE name = :n "
            "AND parent_id IS NULL"
        ),
        {"n": name},
    )
    row = res.first()
    if row:
        return str(row[0])
    cid = uuid4()
    await session.execute(
        text(
            "INSERT INTO categories (id, name, slug, parent_id, "
            "sort_order, is_active) "
            "VALUES (:id, :n, :s, NULL, 0, true)"
        ),
        {"id": cid, "n": name, "s": _slug(name)},
    )
    return str(cid)


async def _product_exists(session, mother_sku: str) -> bool:
    res = await session.execute(
        text("SELECT 1 FROM products WHERE mother_sku = :s"),
        {"s": mother_sku},
    )
    return res.first() is not None


async def _slug_taken(session, slug: str) -> bool:
    res = await session.execute(
        text("SELECT 1 FROM products WHERE slug = :s"),
        {"s": slug},
    )
    return res.first() is not None


async def _disambiguate_slug(session, base_slug: str, sku: str) -> str:
    """If base_slug is already in DB (from a prior ingest of a different
    source file), append a SKU-derived suffix so the INSERT succeeds.
    The redirect map will still point the original canonical_url at the
    first-claimed product."""
    if not await _slug_taken(session, base_slug):
        return base_slug
    # SKU tail: last segment of SKU like "SKU-S1-100001" -> "100001"
    sku_tail = sku.rsplit("-", 1)[-1]
    candidate = f"{base_slug}-{sku_tail}"[:160]
    if not await _slug_taken(session, candidate):
        return candidate
    # Last resort: append a hash
    import hashlib
    h = hashlib.md5(sku.encode()).hexdigest()[:6]
    return f"{base_slug}-{h}"[:160]


async def _insert_product(
    session, *,
    sku: str, slug: str, name: str, brand_id: str, category_id: str,
    short_desc: str, price_minor: int,
) -> str:
    """Returns UUID hex of inserted product."""
    pid = uuid4()
    await session.execute(
        text(
            "INSERT INTO products ("
            "  id, slug, name, short_description, brand_id, category_id,"
            "  status, base_currency, tax_class, attributes,"
            "  mother_sku, is_medicine, requires_prescription"
            ") VALUES ("
            "  :id, :slug, :name, :sd, :bid, :cid,"
            "  'draft', 'BDT', 'standard',"
            "  CAST(:attrs AS JSONB), :sku, false, false"
            ")"
        ),
        {
            "id": pid, "slug": slug, "name": name, "sd": short_desc,
            "bid": brand_id, "cid": category_id,
            "attrs": json.dumps({
                "seo_ingest": True,
                "source": "daraz_10k_xlsx",
                "price_seed_minor": price_minor,
            }),
            "sku": sku,
        },
    )
    # MUST return hex (no dashes) — SeoBundleService.for_product uses
    # product.id.hex as the entity_key lookup. Returning the dashed
    # str() form leaves the seo_meta_overrides row unreachable at runtime.
    return pid.hex


async def _insert_seo_meta(
    session, *,
    product_id: str, canonical_url: str,
    title_en: str, desc_en: str, kw_en: str,
    title_bn: str, desc_bn: str, kw_bn: str,
) -> None:
    # EN override row
    await session.execute(
        text(
            "INSERT INTO seo_meta_overrides ("
            "  id, entity_type, entity_key, title, meta_description,"
            "  canonical_url, extra_meta_json, extra_jsonld_json,"
            "  auto_generated"
            ") VALUES ("
            "  gen_random_uuid(), 'product', :key, :t, :d,"
            "  :url, CAST(:kw AS JSONB), '[]'::jsonb, true"
            ") ON CONFLICT (entity_type, entity_key) DO NOTHING"
        ),
        {
            "key": product_id, "t": title_en, "d": desc_en[:320],
            "url": canonical_url,
            "kw": json.dumps({"keywords": kw_en}),
        },
    )
    # BN translation row
    await session.execute(
        text(
            "INSERT INTO seo_meta_translations ("
            "  id, entity_type, entity_key, locale, title,"
            "  meta_description, keywords, auto_generated"
            ") VALUES ("
            "  gen_random_uuid(), 'product', :key, 'bn', :t, :d,"
            "  :kw, true"
            ") ON CONFLICT (entity_type, entity_key, locale) DO NOTHING"
        ),
        {
            "key": product_id, "t": title_bn, "d": desc_bn[:320],
            "kw": kw_bn[:500],
        },
    )


async def _insert_redirect(
    session, *, from_slug: str, to_slug: str,
) -> None:
    """301 redirect for a deduped non-canonical slug."""
    await session.execute(
        text(
            "INSERT INTO seo_url_redirects ("
            "  id, from_path, to_path, redirect_type, is_active"
            ") VALUES ("
            "  gen_random_uuid(), :f, :t, 'permanent', true"
            ") ON CONFLICT (from_path) DO NOTHING"
        ),
        {
            "f": f"/product/{from_slug}",
            "t": f"/product/{to_slug}",
        },
    )


async def main(args: argparse.Namespace) -> int:
    in_path = Path(args.in_path)
    if not in_path.exists():
        print(f"input not found: {in_path}", file=sys.stderr)
        return 2

    print(f"reading: {in_path}")
    wb = openpyxl.load_workbook(in_path, read_only=True, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter)
    print(f"header cols: {len(header)}")

    canonical_rows: list[tuple] = []
    redirect_rows: list[tuple] = []  # (from_slug, to_slug)
    brands: set[str] = set()
    categories: set[str] = set()

    for row in rows_iter:
        if not row or not row[COL["sku"]]:
            continue
        is_canon = bool(row[COL["is_canonical"]])
        if is_canon:
            canonical_rows.append(row)
            brand_name = str(row[COL["brand"]] or "").strip()
            cat_name = str(row[COL["category"]] or "").strip()
            if brand_name:
                brands.add(brand_name)
            if cat_name:
                categories.add(cat_name)
        else:
            redirect_rows.append((
                str(row[COL["slug"]]),
                str(row[COL["canonical_slug"]]),
            ))

    if args.limit:
        canonical_rows = canonical_rows[:args.limit]
        # Filter redirects to only those whose target is in the trimmed set
        canon_slugs = {str(r[COL["slug"]]) for r in canonical_rows}
        redirect_rows = [(f, t) for (f, t) in redirect_rows if t in canon_slugs]

    print(f"plan: brands={len(brands)} categories={len(categories)} "
          f"products={len(canonical_rows)} redirects={len(redirect_rows)}")

    if args.dry_run:
        print("dry-run: no writes performed")
        return 0

    n_created = 0
    n_skipped = 0
    n_redirects = 0
    n_brands = 0
    n_categories = 0

    Session = get_sessionmaker()
    async with Session() as session:
        # Phase A: brand + category upserts
        brand_id_by_name: dict[str, str] = {}
        for b in sorted(brands):
            bid = await _upsert_brand(session, b)
            brand_id_by_name[b] = bid
            n_brands += 1
        category_id_by_name: dict[str, str] = {}
        for c in sorted(categories):
            cid = await _upsert_category(session, c)
            category_id_by_name[c] = cid
            n_categories += 1
        await session.commit()
        print(f"  brands upserted: {n_brands}, categories upserted: {n_categories}")

    # Phase B: products + SEO meta in batched commits
    BATCH = 200
    for batch_start in range(0, len(canonical_rows), BATCH):
        batch = canonical_rows[batch_start: batch_start + BATCH]
        async with Session() as session:
            for row in batch:
                sku = str(row[COL["sku"]]).strip()
                if await _product_exists(session, sku):
                    n_skipped += 1
                    continue
                brand_name = str(row[COL["brand"]] or "").strip()
                cat_name = str(row[COL["category"]] or "").strip()
                slug_base = str(row[COL["slug"]])
                slug = await _disambiguate_slug(session, slug_base, sku)
                name = str(row[COL["cleaned_title"]])
                short_desc = str(row[COL["meta_desc_en"]] or "")[:512]
                try:
                    price_minor = int(round(
                        float(row[COL["price"]] or 0) * 100
                    ))
                except (TypeError, ValueError):
                    price_minor = 0
                pid = await _insert_product(
                    session,
                    sku=sku, slug=slug, name=name,
                    brand_id=brand_id_by_name.get(brand_name, None),
                    category_id=category_id_by_name.get(cat_name, None),
                    short_desc=short_desc, price_minor=price_minor,
                )
                await _insert_seo_meta(
                    session,
                    product_id=pid,
                    canonical_url=str(row[COL["canonical_url"]]),
                    title_en=str(row[COL["meta_title_en"]])[:255],
                    desc_en=str(row[COL["meta_desc_en"]] or ""),
                    kw_en=str(row[COL["meta_keywords_en"]] or "")[:500],
                    title_bn=str(row[COL["meta_title_bn"]])[:255],
                    desc_bn=str(row[COL["meta_desc_bn"]] or ""),
                    kw_bn=str(row[COL["meta_keywords_bn"]] or "")[:500],
                )
                n_created += 1
            await session.commit()
        if (batch_start + BATCH) % 1000 == 0 or batch_start + BATCH >= len(canonical_rows):
            print(f"  products: {n_created} created, {n_skipped} skipped")

    # Phase C: redirects
    if redirect_rows:
        async with Session() as session:
            for from_slug, to_slug in redirect_rows:
                try:
                    await _insert_redirect(
                        session, from_slug=from_slug, to_slug=to_slug,
                    )
                    n_redirects += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"  redirect skip {from_slug}: {exc}", file=sys.stderr)
            await session.commit()
        print(f"  redirects inserted: {n_redirects}")

    print()
    print("=" * 60)
    print(f"  brands       : {n_brands}")
    print(f"  categories   : {n_categories}")
    print(f"  products     : {n_created} created, {n_skipped} skipped")
    print(f"  redirects    : {n_redirects}")
    return 0


def cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_path", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main(args))


if __name__ == "__main__":
    sys.exit(cli())
