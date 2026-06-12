"""Fuzzy-match image filenames in a brand press-kit folder to
products.mother_sku in the catalog. Outputs a photos.csv ready
for upload_product_images.py.

Strategy:
  * Walk --photo-dir for *.jpg, *.png, *.webp
  * For each file, tokenize filename (split on _-.) and match against
    product.name + brand.name + mother_sku
  * Best-match score ≥ threshold → emit (sku, image_path, alt, position)
  * Multiple images per product → position increments by file order

NO synthetic placeholders. Files that don't match a real product are
written to <out>.unmatched.csv so the operator can rename or skip them.

Usage:
    .venv/Scripts/python -m scripts.photo_match \\
        --brand-slug baseus \\
        --photo-dir /staging/baseus/raw/ \\
        --out scripts/baseus_photos.csv \\
        --threshold 0.45
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _tokens(s: str) -> list[str]:
    s = re.sub(r"[^a-zA-Z0-9]+", " ", (s or "").lower())
    return [t for t in s.split() if len(t) >= 2]


def _score(file_tokens: list[str], product_tokens: list[str]) -> float:
    """Token overlap + sequence similarity hybrid score."""
    if not file_tokens or not product_tokens:
        return 0.0
    ft, pt = set(file_tokens), set(product_tokens)
    jaccard = len(ft & pt) / max(1, len(ft | pt))
    seq = SequenceMatcher(
        None, " ".join(file_tokens), " ".join(product_tokens),
    ).ratio()
    return 0.6 * jaccard + 0.4 * seq


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand-slug", required=True,
                        help="brands.slug to scope products against")
    parser.add_argument("--photo-dir", required=True)
    parser.add_argument("--out", required=True, help="output CSV path")
    parser.add_argument("--threshold", type=float, default=0.45)
    args = parser.parse_args()

    photo_dir = Path(args.photo_dir)
    if not photo_dir.is_dir():
        print(f"photo dir not found: {photo_dir}", file=sys.stderr)
        return 2

    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://hypershop:hypershop@localhost:5432/hypershop",
    )
    eng = create_async_engine(url)
    async with eng.connect() as c:
        rows = (await c.execute(text("""
          SELECT p.mother_sku, p.name
          FROM products p
          JOIN brands b ON b.id = p.brand_id
          WHERE b.slug = :slug AND p.mother_sku IS NOT NULL
        """), {"slug": args.brand_slug})).all()
    await eng.dispose()
    if not rows:
        print(f"no products for brand slug={args.brand_slug!r}",
              file=sys.stderr)
        return 2

    catalog = [(r[0], r[1], _tokens(r[1])) for r in rows]
    print(f"brand={args.brand_slug} catalog_size={len(catalog)}")

    # Walk + match
    matches: list[tuple[str, str, str, int]] = []   # sku, path, alt, pos
    unmatched: list[tuple[str, float, str]] = []    # path, best_score, best_sku
    per_sku_position: dict[str, int] = {}
    files = sorted(
        p for p in photo_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in _IMG_EXT
    )
    print(f"images found: {len(files)}")
    for fp in files:
        rel = str(fp.relative_to(photo_dir)).replace("\\", "/")
        f_tokens = _tokens(fp.stem)
        best_sku = ""
        best_name = ""
        best_score = 0.0
        for sku, name, p_tokens in catalog:
            sc = _score(f_tokens, p_tokens)
            if sc > best_score:
                best_score = sc
                best_sku = sku
                best_name = name
        if best_score >= args.threshold:
            pos = per_sku_position.get(best_sku, 0)
            matches.append((best_sku, rel, best_name, pos))
            per_sku_position[best_sku] = pos + 1
        else:
            unmatched.append((rel, best_score, best_sku))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sku", "image_path", "alt", "position"])
        for sku, path, alt, pos in matches:
            w.writerow([sku, path, alt, pos])

    unmatched_path = out_path.with_suffix(out_path.suffix + ".unmatched.csv")
    with unmatched_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["image_path", "best_score", "best_guess_sku"])
        for path, sc, sku in unmatched:
            w.writerow([path, f"{sc:.2f}", sku])

    print(f"matched:   {len(matches)} → {out_path}")
    print(f"unmatched: {len(unmatched)} → {unmatched_path}")
    print()
    print("next:")
    print(f"  python -m scripts.upload_product_images "
          f"--csv {out_path} --image-root {photo_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
