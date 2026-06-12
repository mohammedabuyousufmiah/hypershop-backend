"""Auto-ingest product photos to R2 (or local) + ProductMedia.

Two modes:

1. **Single-folder** — for one brand/category drop:
       python -m scripts.ingest_photos \\
           --folder "C:/path/baseus_pressroom/" \\
           --brand-slug baseus \\
           --category-slug mobile-gadgets \\
           --position 0

2. **Plan-file** — for a batch of folders mapped to different
   brand/category targets, declared in a YAML file:
       python -m scripts.ingest_photos --plan-file plan.yaml

   plan.yaml example:
       defaults:
         position: 0
         match_mode: filename     # filename | sequential
         threshold: 0.45          # fuzzy-match cutoff
       jobs:
         - folder: "C:/photos/baseus/"
           brand_slug: baseus
           match_mode: filename     # fuzzy match brand+model in filename
         - folder: "C:/photos/women_stock/"
           category_slug: womens-fashion
           match_mode: sequential   # 1:1 to next unphotographed products
         - folder: "C:/photos/category_covers/"
           filename_overrides:
             beauty_hero.jpg:
               category_slug: beauty-fragrance
             baby_hero.jpg:
               category_slug: baby-maternity

## Matching strategies

- **filename** — tokenise each filename, fuzzy-match against
  `brand.name + product.name + mother_sku`. Requires a brand_slug
  scope. Best for branded press-kit drops where filenames carry
  product clues (e.g. `baseus_powerbank_30000mah_front.jpg`).

- **sequential** — for stock/anonymous images. Pair files (sorted
  alphabetically) 1:1 to active products in the given category that
  have no ProductMedia at the target position, newest-first.

- **filename_overrides** — explicit per-file pin. Wins over both
  strategies for the named files; everything else falls through.

## Skip / fail behavior

- Files that match no product → recorded in
  `<plan_or_folder>.unmatched.csv`. No synthetic placeholder; nothing
  inserted.
- Products that already have media at the target position are skipped.
- Failed encodes (corrupt image, EXIF rotation issue) → counted but
  don't abort the run.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_DIM = 1600
JPEG_QUALITY = 85


# ---------- image encoding ----------
def _resize_to_jpeg(src: Path) -> bytes:
    img = Image.open(src).convert("RGB")
    if max(img.size) > MAX_DIM:
        img.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


# ---------- R2 + local backends ----------
class StorageBackend:
    def put(self, *, product_id: str, position: int, jpeg: bytes) -> str:
        raise NotImplementedError


class R2Backend(StorageBackend):
    def __init__(self) -> None:
        bucket = os.environ.get("R2_BUCKET_NAME") or ""
        account = os.environ.get("R2_ACCOUNT_ID") or ""
        key = os.environ.get("R2_ACCESS_KEY_ID") or ""
        secret = os.environ.get("R2_SECRET_ACCESS_KEY") or ""
        if not (bucket and account and key and secret):
            raise RuntimeError("R2 env not set")
        import boto3
        self.bucket = bucket
        self.public = (
            os.environ.get("R2_PUBLIC_BASE_URL")
            or f"https://{bucket}.r2.dev"
        ).rstrip("/")
        self.prefix = (
            os.environ.get("R2_IMAGE_PREFIX") or "img/catalog/"
        ).strip("/") + "/"
        self.s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name="auto",
        )

    def put(self, *, product_id: str, position: int, jpeg: bytes) -> str:
        # Avoid prefix stutter (public/products/ + products/<id>/)
        seg = "" if "products" in self.prefix else "products/"
        key = f"{self.prefix}{seg}{product_id}/{position}.jpg"
        self.s3.put_object(
            Bucket=self.bucket, Key=key, Body=jpeg,
            ContentType="image/jpeg",
            CacheControl="public, max-age=31536000, immutable",
        )
        return f"{self.public}/{key}"


class LocalBackend(StorageBackend):
    def __init__(self) -> None:
        self.base = Path(os.environ.get(
            "LOCAL_UPLOAD_DIR", "uploads/products",
        ))
        self.base.mkdir(parents=True, exist_ok=True)
        self.public_base = (
            os.environ.get("LOCAL_PUBLIC_BASE")
            or "http://localhost:8000/uploads/products"
        ).rstrip("/")

    def put(self, *, product_id: str, position: int, jpeg: bytes) -> str:
        out = self.base / product_id / f"{position}.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(jpeg)
        return f"{self.public_base}/{product_id}/{position}.jpg"


def _build_backend() -> StorageBackend:
    try:
        be = R2Backend()
        print(f"R2 ready: bucket={be.bucket} prefix={be.prefix}")
        return be
    except RuntimeError:
        be = LocalBackend()
        print(f"R2 not configured — local fallback: {be.base}")
        return be


# ---------- catalog queries ----------
def _tokens(s: str) -> list[str]:
    s = re.sub(r"[^a-zA-Z0-9]+", " ", (s or "").lower())
    return [t for t in s.split() if len(t) >= 2]


def _fuzzy_score(file_tokens: list[str], product_tokens: list[str]) -> float:
    if not file_tokens or not product_tokens:
        return 0.0
    ft, pt = set(file_tokens), set(product_tokens)
    jaccard = len(ft & pt) / max(1, len(ft | pt))
    seq = SequenceMatcher(
        None, " ".join(file_tokens), " ".join(product_tokens),
    ).ratio()
    return 0.6 * jaccard + 0.4 * seq


async def _load_brand_catalog(session, brand_slug: str) -> list[tuple[str, str, list[str]]]:
    """Return (product_id_str, name, tokens) for brand's active products."""
    rows = (await session.execute(text(
        "SELECT p.id::text, p.name FROM products p "
        "JOIN brands b ON b.id = p.brand_id "
        "WHERE b.slug = :slug AND p.status='active'"
    ), {"slug": brand_slug})).all()
    return [(r[0], r[1], _tokens(r[1] + " " + (r[0] or ""))) for r in rows]


async def _eligible_in_category(
    session, category_slugs: list[str] | None, position: int, limit: int,
    *, brand_slug: str | None = None,
) -> list[tuple[str, str]]:
    """Active products with no ProductMedia at target position.

    Filter cascade — both filters optional, but at least one required:
      * ``category_slugs`` → restrict to products in those categories
      * ``brand_slug`` → restrict to products of that brand

    Newest products first (better PDP weight under sitemap lastmod)."""
    where_parts = ["p.status='active'"]
    joins = []
    params: dict[str, object] = {"pos": position, "lim": limit}
    if category_slugs:
        joins.append("JOIN categories c ON c.id = p.category_id")
        where_parts.append("c.slug = ANY(:slugs)")
        params["slugs"] = category_slugs
    if brand_slug:
        joins.append("JOIN brands b ON b.id = p.brand_id")
        where_parts.append("b.slug = :bslug")
        params["bslug"] = brand_slug
    where_parts.append(
        "NOT EXISTS (SELECT 1 FROM product_media m "
        "WHERE m.product_id = p.id AND m.position = :pos)"
    )
    sql = (
        "SELECT p.id::text, p.name FROM products p "
        f"{' '.join(joins)} "
        f"WHERE {' AND '.join(where_parts)} "
        "ORDER BY p.created_at DESC LIMIT :lim"
    )
    rows = (await session.execute(text(sql), params)).all()
    return [(r[0], r[1]) for r in rows]


async def _media_exists(session, *, product_id: str, position: int) -> bool:
    row = (await session.execute(text(
        "SELECT 1 FROM product_media WHERE product_id = :p AND position = :pos LIMIT 1"
    ), {"p": product_id, "pos": position})).first()
    return row is not None


async def _insert_media(
    session, *, product_id: str, position: int, url: str, alt: str,
) -> None:
    await session.execute(text(
        "INSERT INTO product_media (id, product_id, kind, url, alt, position) "
        "VALUES (gen_random_uuid(), :p, 'image', :u, :a, :pos)"
    ), {"p": product_id, "u": url, "a": alt[:255], "pos": position})


# ---------- per-job runner ----------
class JobStats:
    def __init__(self) -> None:
        self.uploaded = 0
        self.skipped_existing = 0
        self.no_target = 0
        self.encode_failed = 0
        self.unmatched: list[tuple[str, float, str]] = []  # path, score, guess

    def merge(self, o: "JobStats") -> None:
        self.uploaded += o.uploaded
        self.skipped_existing += o.skipped_existing
        self.no_target += o.no_target
        self.encode_failed += o.encode_failed
        self.unmatched.extend(o.unmatched)


def _list_images(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in _IMG_EXT
    )


async def _run_one_image(
    session, *, backend: StorageBackend, img: Path,
    product_id: str, product_name: str, position: int,
) -> str | None:
    if await _media_exists(session, product_id=product_id, position=position):
        return "skip-existing"
    try:
        jpeg = _resize_to_jpeg(img)
    except Exception as exc:  # noqa: BLE001
        print(f"  encode failed [{img.name}]: {exc}")
        return "encode-failed"
    url = backend.put(product_id=product_id, position=position, jpeg=jpeg)
    await _insert_media(
        session, product_id=product_id, position=position,
        url=url, alt=product_name,
    )
    return "uploaded"


async def _run_job(session, *, backend: StorageBackend, job: dict[str, Any]) -> JobStats:
    folder = Path(job["folder"])
    if not folder.is_dir():
        print(f"  folder missing: {folder}")
        return JobStats()

    position = int(job.get("position", 0))
    overrides = job.get("filename_overrides") or {}
    match_mode = (job.get("match_mode") or "sequential").lower()
    threshold = float(job.get("threshold", 0.45))
    brand_slug = job.get("brand_slug") or ""
    category_slugs: list[str] = []
    raw_cat = job.get("category_slug") or job.get("category_slugs")
    if isinstance(raw_cat, str):
        category_slugs = [raw_cat]
    elif isinstance(raw_cat, list):
        category_slugs = list(raw_cat)

    files = _list_images(folder)
    print(f"\n=== {folder.name} ({len(files)} images, mode={match_mode}) ===")

    stats = JobStats()

    # Split into override / generic pool
    overridden: list[tuple[Path, dict[str, Any]]] = []
    pool: list[Path] = []
    for f in files:
        cfg = overrides.get(f.name)
        if cfg:
            overridden.append((f, cfg))
        else:
            pool.append(f)

    # 1. Per-file overrides — each carries its own brand/category target.
    for f, cfg in overridden:
        slugs = (
            [cfg.get("category_slug")]
            if isinstance(cfg.get("category_slug"), str)
            else list(cfg.get("category_slugs") or [])
        )
        if not slugs:
            print(f"  override [{f.name}] missing category_slug — skip")
            continue
        targets = await _eligible_in_category(session, slugs, position, 1)
        if not targets:
            stats.no_target += 1
            print(f"  override [{f.name}] no eligible target in {slugs}")
            continue
        pid, name = targets[0]
        outcome = await _run_one_image(
            session, backend=backend, img=f,
            product_id=pid, product_name=name, position=position,
        )
        if outcome == "uploaded":
            stats.uploaded += 1
            print(f"  override [{f.name:35}] → {name[:55]}")
        elif outcome == "skip-existing":
            stats.skipped_existing += 1
        else:
            stats.encode_failed += 1

    # 2. Generic pool — branch on match_mode
    if not pool:
        return stats

    if match_mode == "filename":
        if not brand_slug:
            print("  filename mode requires brand_slug — skipping pool")
            return stats
        catalog = await _load_brand_catalog(session, brand_slug)
        if not catalog:
            print(f"  brand_slug={brand_slug!r} has no active products")
            return stats
        per_pid_pos: dict[str, int] = {}
        for f in pool:
            f_tokens = _tokens(f.stem)
            best = ("", "", 0.0)
            for pid, name, p_tokens in catalog:
                sc = _fuzzy_score(f_tokens, p_tokens)
                if sc > best[2]:
                    best = (pid, name, sc)
            if best[2] < threshold:
                stats.unmatched.append((str(f), best[2], best[1]))
                continue
            pid, name, _sc = best
            # Auto-bump position when matching multiple files to same SKU
            pos = position + per_pid_pos.get(pid, 0)
            per_pid_pos[pid] = per_pid_pos.get(pid, 0) + 1
            outcome = await _run_one_image(
                session, backend=backend, img=f,
                product_id=pid, product_name=name, position=pos,
            )
            if outcome == "uploaded":
                stats.uploaded += 1
            elif outcome == "skip-existing":
                stats.skipped_existing += 1
            else:
                stats.encode_failed += 1

    elif match_mode == "sequential":
        if not category_slugs and not brand_slug:
            print("  sequential mode requires category_slug or brand_slug — skipping pool")
            return stats
        targets = await _eligible_in_category(
            session, category_slugs, position, len(pool),
            brand_slug=brand_slug or None,
        )
        if len(targets) < len(pool):
            stats.no_target += len(pool) - len(targets)
            print(
                f"  only {len(targets)} eligible products in {category_slugs} "
                f"for {len(pool)} images"
            )
        for f, (pid, name) in zip(pool, targets):
            outcome = await _run_one_image(
                session, backend=backend, img=f,
                product_id=pid, product_name=name, position=position,
            )
            if outcome == "uploaded":
                stats.uploaded += 1
                if stats.uploaded % 10 == 0:
                    print(f"    {stats.uploaded} uploaded...")
            elif outcome == "skip-existing":
                stats.skipped_existing += 1
            else:
                stats.encode_failed += 1
    else:
        print(f"  unknown match_mode={match_mode!r}")

    return stats


# ---------- plan loading ----------
def _load_plan(plan_path: Path) -> list[dict[str, Any]]:
    """Accept YAML or JSON. YAML preferred — read via PyYAML if present,
    otherwise fall back to JSON parsing."""
    raw = plan_path.read_text(encoding="utf-8")
    try:
        import yaml
        data = yaml.safe_load(raw)
    except ImportError:
        import json
        data = json.loads(raw)
    defaults = data.get("defaults") or {}
    jobs = []
    for j in data.get("jobs") or []:
        merged = {**defaults, **j}
        jobs.append(merged)
    return jobs


# ---------- main ----------
async def main_async() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-file", help="YAML/JSON plan with multiple jobs")
    parser.add_argument("--folder", help="single-folder mode: source folder")
    parser.add_argument("--brand-slug", help="for filename-match mode")
    parser.add_argument(
        "--category-slug", action="append",
        help="for sequential mode (repeat for multiple)",
    )
    parser.add_argument("--match-mode", choices=["filename", "sequential"],
                        default="sequential")
    parser.add_argument("--position", type=int, default=0)
    parser.add_argument("--threshold", type=float, default=0.45)
    parser.add_argument("--unmatched-csv", default=None,
                        help="path to write unmatched files report")
    args = parser.parse_args()

    if not args.plan_file and not args.folder:
        parser.error("--plan-file OR --folder required")
    if args.folder and args.match_mode == "sequential" and not args.category_slug:
        parser.error("--category-slug required for sequential mode")
    if args.folder and args.match_mode == "filename" and not args.brand_slug:
        parser.error("--brand-slug required for filename mode")

    if args.plan_file:
        jobs = _load_plan(Path(args.plan_file))
    else:
        jobs = [{
            "folder": args.folder,
            "brand_slug": args.brand_slug,
            "category_slug": args.category_slug,
            "match_mode": args.match_mode,
            "position": args.position,
            "threshold": args.threshold,
        }]

    print(f"loaded {len(jobs)} jobs")
    backend = _build_backend()

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://hypershop:hypershop@localhost:5432/hypershop",
    )
    eng = create_async_engine(db_url)
    total = JobStats()
    async with eng.begin() as session:
        for job in jobs:
            stats = await _run_job(session, backend=backend, job=job)
            total.merge(stats)
    await eng.dispose()

    if total.unmatched and args.unmatched_csv:
        with Path(args.unmatched_csv).open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["image_path", "best_score", "best_guess"])
            for path, sc, guess in total.unmatched:
                w.writerow([path, f"{sc:.2f}", guess])
        print(f"\nunmatched report: {args.unmatched_csv}")

    print()
    print("=" * 60)
    print(f"  uploaded:         {total.uploaded}")
    print(f"  skipped (exist):  {total.skipped_existing}")
    print(f"  no target:        {total.no_target}")
    print(f"  encode failed:    {total.encode_failed}")
    print(f"  unmatched (fuzzy): {len(total.unmatched)}")
    return 0 if total.encode_failed == 0 else 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
