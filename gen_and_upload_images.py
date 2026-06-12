"""
Phase A image generation + upload.

Generates one 800x800 PNG per product using Pillow, then POSTs each
into the admin image-upload endpoint so it flows through R2 +
product_media exactly the same path an admin drag-drop would take.

Card layout:
  • Category-specific gradient background (one of 8 brand colors)
  • Brand name capsule top-right (or "HYPERSHOP DIRECT" if no brand)
  • Product name centered, wrapped to ~3 lines
  • Subtle SKU line at the bottom
  • Lightweight pattern overlay so the card doesn't look flat

These are PRODUCT-AWARE placeholders, not stock photos. Each is unique
and tied to the product name so the storefront grid stops showing
broken-image icons. Replace any individual image later via the admin
Product Image Manager (upload overwrites position 0 by drop order).

Run:
    .venv/Scripts/python.exe gen_and_upload_images.py
"""
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import psycopg2
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BACKEND = os.environ.get("BACKEND_BASE", "http://127.0.0.1:8000")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@hypershop.com.bd")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMe2026Admin!")

# Postgres connection — read straight from DATABASE_SYNC_URL
PG_DSN = "dbname=hypershop user=hypershop password=hypershop host=127.0.0.1 port=5432"

# Map root-category slug → (top color, bottom color) for the gradient.
# Each one is a deep, on-brand colour the storefront grid can live with.
CATEGORY_COLORS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "electronics": ((20, 33, 61), (15, 76, 117)),
    "fashion": ((142, 36, 76), (227, 119, 159)),
    "home-kitchen": ((52, 78, 65), (135, 168, 124)),
    "beauty": ((220, 130, 130), (255, 200, 180)),
    "grocery": ((201, 142, 56), (242, 196, 116)),
    "baby-kids": ((73, 124, 178), (170, 207, 230)),
    "mobile": ((30, 40, 80), (76, 110, 200)),
    "health": ((34, 102, 102), (110, 187, 187)),
}
DEFAULT_COLORS = ((40, 40, 40), (90, 90, 90))


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Try common Windows fonts in order; fall back to Pillow's default."""
    candidates = [
        ("arialbd.ttf" if bold else "arial.ttf"),
        "segoeuib.ttf" if bold else "segoeui.ttf",
        "calibrib.ttf" if bold else "calibri.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _gradient(size: int, top_rgb: tuple[int, int, int], bottom_rgb: tuple[int, int, int]) -> Image.Image:
    """Vertical gradient. Slightly faster than per-pixel by drawing one
    horizontal stripe per row."""
    img = Image.new("RGB", (size, size), top_rgb)
    draw = ImageDraw.Draw(img)
    tr, tg, tb = top_rgb
    br, bg, bb = bottom_rgb
    for y in range(size):
        t = y / (size - 1)
        r = int(tr + (br - tr) * t)
        g = int(tg + (bg - tg) * t)
        b = int(tb + (bb - tb) * t)
        draw.line([(0, y), (size, y)], fill=(r, g, b))
    return img


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Word-wrap to multiple lines that each fit max_width."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if (bbox[2] - bbox[0]) > max_width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _root_slug_for(category_path: list[str]) -> str:
    """Given the path of category slugs from the product up to root, return the root slug."""
    return category_path[0] if category_path else "default"


def make_card(
    *,
    product_name: str,
    brand_name: str | None,
    root_category_slug: str,
    category_name: str,
    sku: str,
) -> bytes:
    """Render one 800x800 PNG card. Returns the bytes (PNG)."""
    SIZE = 800
    PAD = 60

    top, bot = CATEGORY_COLORS.get(root_category_slug, DEFAULT_COLORS)
    img = _gradient(SIZE, top, bot)

    # Subtle darkening near edges via radial mask for a focal centre
    vignette = Image.new("L", (SIZE, SIZE), 0)
    vd = ImageDraw.Draw(vignette)
    vd.ellipse((-150, -150, SIZE + 150, SIZE + 150), fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(120))
    dark = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
    img = Image.composite(img, dark, vignette)

    draw = ImageDraw.Draw(img)

    # ---- top: category badge ----
    cat_font = _load_font(22, bold=True)
    cat_text = category_name.upper()
    bbox = draw.textbbox((0, 0), cat_text, font=cat_font)
    cat_w = bbox[2] - bbox[0]
    cat_h = bbox[3] - bbox[1]
    cat_pad = 14
    cat_x = (SIZE - cat_w) // 2
    cat_y = PAD
    draw.rounded_rectangle(
        (cat_x - cat_pad, cat_y - 8, cat_x + cat_w + cat_pad, cat_y + cat_h + 16),
        radius=999,
        fill=(255, 255, 255, 230),
    )
    draw.text((cat_x, cat_y), cat_text, fill=(20, 20, 20), font=cat_font)

    # ---- center: product name wrapped ----
    name_font = _load_font(48, bold=True)
    lines = _wrap_text(product_name, name_font, max_width=SIZE - 2 * PAD, draw=draw)
    if len(lines) > 4:
        lines = lines[:4]
        lines[-1] = lines[-1][:30].rstrip() + "..."
    line_h = name_font.size + 14
    total_h = line_h * len(lines)
    start_y = (SIZE - total_h) // 2
    for i, line in enumerate(lines):
        b = draw.textbbox((0, 0), line, font=name_font)
        lw = b[2] - b[0]
        # subtle shadow
        draw.text(
            ((SIZE - lw) // 2 + 2, start_y + i * line_h + 2),
            line,
            fill=(0, 0, 0, 120),
            font=name_font,
        )
        draw.text(
            ((SIZE - lw) // 2, start_y + i * line_h),
            line,
            fill=(255, 255, 255),
            font=name_font,
        )

    # ---- bottom: brand + SKU ----
    brand_label = (brand_name or "HYPERSHOP DIRECT").upper()
    brand_font = _load_font(28, bold=True)
    b = draw.textbbox((0, 0), brand_label, font=brand_font)
    bw = b[2] - b[0]
    bh = b[3] - b[1]
    bx = (SIZE - bw) // 2
    by = SIZE - PAD - bh - 30
    draw.text((bx, by), brand_label, fill=(255, 255, 255), font=brand_font)

    sku_font = _load_font(18)
    sb = draw.textbbox((0, 0), sku, font=sku_font)
    sw = sb[2] - sb[0]
    draw.text(((SIZE - sw) // 2, by + bh + 14), sku, fill=(255, 255, 255, 200), font=sku_font)

    # Slight inner border for polish
    draw.rectangle((10, 10, SIZE - 11, SIZE - 11), outline=(255, 255, 255, 80), width=2)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def login_admin() -> str:
    """POST /auth/login → access_token."""
    r = requests.post(
        f"{BACKEND}/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["tokens"]["access_token"]


def load_products() -> list[dict]:
    """Read 80 products with brand + (sub-)category + (root-)category slug."""
    conn = psycopg2.connect(PG_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH RECURSIVE cat_path AS (
                    SELECT id, parent_id, slug, name,
                           1 AS depth,
                           slug AS root_slug
                    FROM categories WHERE parent_id IS NULL
                    UNION ALL
                    SELECT c.id, c.parent_id, c.slug, c.name,
                           cp.depth + 1,
                           cp.root_slug
                    FROM categories c JOIN cat_path cp ON c.parent_id = cp.id
                )
                SELECT
                    p.id::text,
                    p.slug,
                    p.name,
                    p.mother_sku,
                    b.name AS brand_name,
                    cp.name AS sub_cat_name,
                    cp.root_slug AS root_cat_slug
                FROM products p
                LEFT JOIN brands b ON b.id = p.brand_id
                LEFT JOIN cat_path cp ON cp.id = p.category_id
                ORDER BY cp.root_slug NULLS LAST, p.created_at;
                """
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def upload(token: str, product_id: str, filename: str, png_bytes: bytes, alt: str) -> dict:
    """POST the generated PNG to /admin/catalog/products/{id}/media/upload."""
    r = requests.post(
        f"{BACKEND}/api/v1/admin/catalog/products/{product_id}/media/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, png_bytes, "image/png")},
        data={"alt": alt, "position": "0"},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"upload failed for {filename}: {r.status_code} {r.text[:200]}")
    return r.json()


def main() -> None:
    print("logging in as admin...")
    token = login_admin()
    print("loading products...")
    products = load_products()
    print(f"  → {len(products)} products to process")

    okc = 0
    fail: list[tuple[str, str]] = []
    t0 = time.monotonic()
    for i, p in enumerate(products, start=1):
        try:
            png = make_card(
                product_name=p["name"],
                brand_name=p["brand_name"],
                root_category_slug=p["root_cat_slug"] or "default",
                category_name=p["sub_cat_name"] or "Marketplace",
                sku=p["mother_sku"],
            )
            res = upload(
                token=token,
                product_id=p["id"],
                filename=f"{p['slug']}.png",
                png_bytes=png,
                alt=p["name"],
            )
            okc += 1
            url = res.get("url", "")
            storage = "R2" if url.startswith("http") else "LOCAL"
            print(f"  [{i:3d}/{len(products)}] OK {storage} {p['slug'][:50]}")
        except Exception as e:
            fail.append((p["slug"], str(e)))
            print(f"  [{i:3d}/{len(products)}] FAIL {p['slug'][:40]} → {e}")

    dur = time.monotonic() - t0
    print()
    print(f"DONE in {dur:.1f}s  ok={okc}  fail={len(fail)}")
    if fail:
        print("Failures:")
        for slug, err in fail[:10]:
            print(f"  - {slug}: {err}")


if __name__ == "__main__":
    main()
