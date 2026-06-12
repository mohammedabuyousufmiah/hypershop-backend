"""Sequential assignment of category-tagged anonymous photos to
products that currently have NO ProductMedia row. Used when the
photo source folder has stock images without per-SKU mapping.

For each (folder, category_slug) pair:
  1. Read all image files in the folder
  2. Query products in that category with no existing media,
     ordered by created_at desc (newest first — better PDP weight)
  3. Pair them 1:1, upload to R2 (or local), insert ProductMedia row

Operator confirms the pairing post-run via /admin/seo-audit; can
re-shuffle individual mappings later.

Usage (config baked into the script — edit _ASSIGN_PLAN below):
    .venv/Scripts/python -m scripts.assign_anonymous_photos
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path

from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

MAX_DIM = 1600
JPEG_QUALITY = 85

# Edit this map to add more folders. Each entry pairs a source folder
# with the catalog category slug whose unphotographed products will
# receive the images. Optional `single_target_sku` pins a specific SKU
# (used for named hero shots like the category-cover JPGs).
_ASSIGN_PLAN: list[dict] = [
    {
        "folder": (
            r"C:\Users\imyou\OneDrive\Desktop\HYPERSHOP ADD"
            r"\Hypershop product image\WOMEN FASHTION"
        ),
        "category_slugs": ["womens-fashion", "womens-fashion-beauty"],
        "single_target_sku": None,
    },
    {
        "folder": (
            r"C:\Users\imyou\OneDrive\Desktop\HYPERSHOP ADD"
            r"\Hypershop product image\purches by categorie Image"
        ),
        "category_slugs": ["womens-fashion", "womens-fashion-beauty"],
        "single_target_sku": None,
        # `filename_overrides` pulls specific files OUT of the generic
        # pool and assigns them to a hand-picked category instead.
        "filename_overrides": {
            "Beuty.png": ["beauty-fragrance"],
            "PrgnancePM.png": ["baby-maternity"],
        },
    },
]


def _resize_to_jpeg(src: Path) -> bytes:
    img = Image.open(src).convert("RGB")
    if max(img.size) > MAX_DIM:
        img.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def _r2_client():
    bucket = os.environ.get("R2_BUCKET_NAME") or ""
    account = os.environ.get("R2_ACCOUNT_ID") or ""
    key = os.environ.get("R2_ACCESS_KEY_ID") or ""
    secret = os.environ.get("R2_SECRET_ACCESS_KEY") or ""
    if not (bucket and account and key and secret):
        return None
    import boto3
    base = (os.environ.get("R2_PUBLIC_BASE_URL")
            or f"https://{bucket}.r2.dev").rstrip("/")
    prefix = (os.environ.get("R2_IMAGE_PREFIX") or "img/catalog/").strip("/") + "/"
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name="auto",
    )
    return s3, bucket, base, prefix


async def _eligible_products(session, category_slugs: list[str], limit: int):
    """Return list of (product_id_str, name) for products in any of the
    given category slugs that currently have no ProductMedia row.
    Bulk-ingest products preferred (no images), newest first."""
    rows = (await session.execute(
        text(
            "SELECT p.id::text, p.name "
            "FROM products p "
            "JOIN categories c ON c.id = p.category_id "
            "WHERE c.slug = ANY(:slugs) "
            "  AND p.status = 'active' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM product_media m "
            "    WHERE m.product_id = p.id AND m.position = 0"
            "  ) "
            "ORDER BY p.created_at DESC "
            "LIMIT :lim"
        ),
        {"slugs": category_slugs, "lim": limit},
    )).all()
    return [(r[0], r[1]) for r in rows]


async def _insert_media(session, *, product_id: str, url: str, alt: str):
    await session.execute(
        text(
            "INSERT INTO product_media ("
            "  id, product_id, kind, url, alt, position"
            ") VALUES (gen_random_uuid(), :pid, 'image', :url, :alt, 0)"
        ),
        {"pid": product_id, "url": url, "alt": alt[:255]},
    )


async def main() -> int:
    r2 = _r2_client()
    if r2 is None:
        print("R2 not configured — set R2_* env vars first", file=sys.stderr)
        return 2
    s3, bucket, public_base, prefix = r2
    print(f"R2 ready: {bucket} prefix={prefix}")

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://hypershop:hypershop@localhost:5432/hypershop",
    )
    eng = create_async_engine(db_url)

    n_uploaded = 0
    n_skipped_no_target = 0
    n_failed = 0

    async with eng.begin() as session:
        for plan in _ASSIGN_PLAN:
            folder = Path(plan["folder"])
            if not folder.is_dir():
                print(f"  folder missing: {folder}")
                continue
            files = sorted(
                f for f in folder.iterdir()
                if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            )
            overrides = plan.get("filename_overrides") or {}

            # Split files into (override-target-slugs, generic-pool)
            overridden: list[tuple[Path, list[str]]] = []
            generic: list[Path] = []
            for f in files:
                if f.name in overrides:
                    overridden.append((f, overrides[f.name]))
                else:
                    generic.append(f)

            # 1. Handle named overrides
            for f, slugs in overridden:
                targets = await _eligible_products(session, slugs, 1)
                if not targets:
                    n_skipped_no_target += 1
                    print(f"  no target for {f.name} (slugs={slugs})")
                    continue
                pid, name = targets[0]
                try:
                    jpeg = _resize_to_jpeg(f)
                except Exception as exc:  # noqa: BLE001
                    n_failed += 1
                    print(f"  encode failed for {f.name}: {exc}")
                    continue
                segment = "" if "products" in prefix else "products/"
                key = f"{prefix}{segment}{pid}/0.jpg"
                s3.put_object(
                    Bucket=bucket, Key=key, Body=jpeg,
                    ContentType="image/jpeg",
                    CacheControl="public, max-age=31536000, immutable",
                )
                await _insert_media(
                    session, product_id=pid,
                    url=f"{public_base}/{key}",
                    alt=name,
                )
                n_uploaded += 1
                print(f"  override [{f.name:40}] → {name[:50]}")

            # 2. Pair generic pool with eligible product slice
            if generic:
                targets = await _eligible_products(
                    session, plan["category_slugs"], len(generic),
                )
                pairs = list(zip(generic, targets))
                if len(pairs) < len(generic):
                    n_skipped_no_target += len(generic) - len(pairs)
                    print(
                        f"  only {len(targets)} targets for {len(generic)} "
                        f"images in {folder.name}"
                    )
                for f, (pid, name) in pairs:
                    try:
                        jpeg = _resize_to_jpeg(f)
                    except Exception as exc:  # noqa: BLE001
                        n_failed += 1
                        print(f"  encode failed for {f.name}: {exc}")
                        continue
                    segment = "" if "products" in prefix else "products/"
                    key = f"{prefix}{segment}{pid}/0.jpg"
                    s3.put_object(
                        Bucket=bucket, Key=key, Body=jpeg,
                        ContentType="image/jpeg",
                        CacheControl="public, max-age=31536000, immutable",
                    )
                    await _insert_media(
                        session, product_id=pid,
                        url=f"{public_base}/{key}",
                        alt=name,
                    )
                    n_uploaded += 1
                    if n_uploaded % 10 == 0:
                        print(f"    {n_uploaded} uploaded...")

    await eng.dispose()
    print()
    print(f"uploaded:         {n_uploaded}")
    print(f"no target:        {n_skipped_no_target}")
    print(f"failed:           {n_failed}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
