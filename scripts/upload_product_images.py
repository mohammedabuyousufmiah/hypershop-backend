"""Bulk-upload real product photos to R2 (or local fallback) and wire
them to ProductMedia rows.

Input: CSV with header `sku,image_path,alt,position` where
  - sku         : seller-side SKU OR Hypershop SKU (column on products.sku)
  - image_path  : absolute or relative path to a local image file
  - alt         : (optional) alt text — falls back to product.name
  - position    : (optional) integer, default 0

Pipeline per row:
  1. resolve sku → products.id (skip if not found)
  2. open image; if larger than 1600x1600 → resize down (keep aspect)
  3. re-encode as JPEG (quality 85) to bound file size
  4. upload to R2 at <r2_image_prefix>products/<product_id>/<position>.jpg
     OR if R2 not configured, write to /uploads/products/<product_id>/<position>.jpg
  5. INSERT ProductMedia (skip if same product_id+position exists)

NO synthetic placeholders — script fails the row when the input file
does not exist or is unreadable. This guarantees the image sitemap
only carries URLs that resolve to real bytes.

Usage:
    .venv/Scripts/python -m scripts.upload_product_images \\
        --csv "C:/path/photos.csv" \\
        --image-root "C:/path/photo_folder"

CSV example:
    sku,image_path,alt,position
    HS-1001,lenovo_thinkpad.jpg,Lenovo ThinkPad T14 front,0
    HS-1001,lenovo_thinkpad_back.jpg,,1
    HS-1002,asus_rog.png,Asus ROG laptop,0
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
import sys
from pathlib import Path

from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


MAX_DIM = 1600
JPEG_QUALITY = 85


def _resize_to_jpeg(src_path: Path) -> bytes:
    """Open + downscale + re-encode as JPEG. Fails loudly on bad input."""
    img = Image.open(src_path)
    img = img.convert("RGB")
    if max(img.size) > MAX_DIM:
        img.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def _build_r2_client_or_none():
    """Return (s3_client, bucket, public_base, prefix) or None when R2
    not configured. We use boto3 against the R2 S3-compatible endpoint."""
    bucket = os.environ.get("R2_BUCKET_NAME") or ""
    account_id = os.environ.get("R2_ACCOUNT_ID") or ""
    key = os.environ.get("R2_ACCESS_KEY_ID") or ""
    secret = os.environ.get("R2_SECRET_ACCESS_KEY") or ""
    if not (bucket and account_id and key and secret):
        return None
    import boto3
    public_base = (
        os.environ.get("R2_PUBLIC_BASE_URL")
        or f"https://{bucket}.r2.dev"
    ).rstrip("/")
    prefix = (
        os.environ.get("R2_IMAGE_PREFIX")
        or "img/catalog/"
    ).strip("/") + "/"
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name="auto",
    )
    return s3, bucket, public_base, prefix


def _local_fallback_dir() -> Path:
    """When R2 isn't configured we still need somewhere on disk to land
    the encoded JPEGs so the dev environment can render them. The path
    sits under the backend repo so a local `uvicorn` serving static
    files can expose them."""
    base = Path(os.environ.get(
        "LOCAL_UPLOAD_DIR",
        "uploads/products",
    ))
    base.mkdir(parents=True, exist_ok=True)
    return base


async def _resolve_sku(session, sku: str) -> tuple[str, str] | None:
    """Return (product_id_hex, product_name) for a SKU, or None.

    Lookup chain — first hit wins:
      1. products.mother_sku   (Hypershop master SKU on the product row)
      2. product_variants.sku  (per-variant SKU; we still return the
         parent product because images are product-level)
    """
    row = (await session.execute(
        text(
            "SELECT id::text, name FROM products "
            "WHERE mother_sku = :sku LIMIT 1"
        ),
        {"sku": sku},
    )).first()
    if row is not None:
        return row[0], row[1]
    row = (await session.execute(
        text(
            "SELECT p.id::text, p.name "
            "FROM products p JOIN product_variants v ON v.product_id = p.id "
            "WHERE v.sku = :sku LIMIT 1"
        ),
        {"sku": sku},
    )).first()
    if row is None:
        return None
    return row[0], row[1]


async def _media_exists(session, *, product_id: str, position: int) -> bool:
    row = (await session.execute(
        text(
            "SELECT 1 FROM product_media "
            "WHERE product_id = :pid AND position = :pos AND kind='image' LIMIT 1"
        ),
        {"pid": product_id, "pos": position},
    )).first()
    return row is not None


async def _insert_media(
    session, *,
    product_id: str, url: str, alt: str, position: int,
) -> None:
    await session.execute(
        text(
            "INSERT INTO product_media ("
            "  id, product_id, kind, url, alt, position"
            ") VALUES (gen_random_uuid(), :pid, 'image', :url, :alt, :pos)"
        ),
        {"pid": product_id, "url": url, "alt": alt[:255], "pos": position},
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Input CSV path")
    parser.add_argument(
        "--image-root", default=".",
        help="Base directory for resolving relative image_path values",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Process + report but skip DB insert + R2 upload",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"csv not found: {csv_path}", file=sys.stderr)
        return 2
    image_root = Path(args.image_root)

    r2 = _build_r2_client_or_none()
    fallback_dir = None
    if r2 is None:
        fallback_dir = _local_fallback_dir()
        public_base = (
            os.environ.get("LOCAL_PUBLIC_BASE")
            or "http://localhost:8000/uploads/products"
        ).rstrip("/")
        print(f"R2 not configured — falling back to local: {fallback_dir}")
        print(f"public URL base: {public_base}")
    else:
        s3, bucket, public_base, prefix = r2
        print(f"R2 ready: bucket={bucket} prefix={prefix} cdn={public_base}")

    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://hypershop:hypershop@localhost:5432/hypershop",
    )
    eng = create_async_engine(url)

    n_uploaded = 0
    n_skipped_existing = 0
    n_unknown_sku = 0
    n_failed = 0

    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    print(f"loaded {len(rows)} csv rows")

    async with eng.begin() as session:
        for idx, raw in enumerate(rows, start=1):
            sku = (raw.get("sku") or "").strip()
            img_rel = (raw.get("image_path") or "").strip()
            alt_in = (raw.get("alt") or "").strip()
            try:
                position = int(raw.get("position") or 0)
            except ValueError:
                position = 0

            if not sku or not img_rel:
                n_failed += 1
                print(f"  row {idx}: missing sku/image_path — skip")
                continue

            resolved = await _resolve_sku(session, sku)
            if resolved is None:
                n_unknown_sku += 1
                if n_unknown_sku <= 10:
                    print(f"  row {idx}: sku not found: {sku}")
                continue
            product_id, product_name = resolved

            if await _media_exists(
                session, product_id=product_id, position=position,
            ):
                n_skipped_existing += 1
                continue

            img_path = (image_root / img_rel).resolve()
            if not img_path.exists():
                n_failed += 1
                if n_failed <= 10:
                    print(f"  row {idx}: image missing: {img_path}")
                continue

            try:
                jpeg_bytes = _resize_to_jpeg(img_path)
            except Exception as exc:  # noqa: BLE001
                n_failed += 1
                if n_failed <= 10:
                    print(f"  row {idx}: encode failed: {exc}")
                continue

            object_name = f"{product_id}/{position}.jpg"
            if r2 is not None:
                s3, bucket, public_base, prefix = r2
                # If the configured prefix already includes "products" we
                # don't add it again (avoids stutter like
                # public/products/products/<id>/...). Otherwise add the
                # segment so different asset types (banner/logo/etc.)
                # stay namespaced.
                segment = "" if "products" in prefix else "products/"
                key = f"{prefix}{segment}{object_name}"
                if not args.dry_run:
                    s3.put_object(
                        Bucket=bucket, Key=key,
                        Body=jpeg_bytes,
                        ContentType="image/jpeg",
                        CacheControl="public, max-age=31536000, immutable",
                    )
                public_url = f"{public_base}/{key}"
            else:
                local_path = fallback_dir / object_name
                local_path.parent.mkdir(parents=True, exist_ok=True)
                if not args.dry_run:
                    local_path.write_bytes(jpeg_bytes)
                public_url = f"{public_base}/{object_name}"

            if not args.dry_run:
                await _insert_media(
                    session,
                    product_id=product_id,
                    url=public_url,
                    alt=alt_in or product_name,
                    position=position,
                )
            n_uploaded += 1
            if n_uploaded % 100 == 0:
                print(f"  {n_uploaded} uploaded...")

    await eng.dispose()
    print()
    print(f"uploaded:        {n_uploaded}")
    print(f"skipped (exist): {n_skipped_existing}")
    print(f"unknown sku:     {n_unknown_sku}")
    print(f"failed:          {n_failed}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
