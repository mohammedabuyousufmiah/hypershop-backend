"""Seed storefront-facing demo data: a buyer login + product videos.

Two features, both used by the customer storefront (the wired
``Hypershop-Storefront`` preview that talks to this backend):

1. **Demo customer** — ``customer@hypershop.dev`` / ``customerlocal12``
   (active + email-verified, NO admin role — a pure marketplace buyer).
   Powers the storefront "Sign in", authed cart, and orders.

2. **Product videos** (module 35 ``product_videos``) — up to 6 *approved*
   videos mapped onto the first active products, so the storefront
   "▶ Watch" reels feed (``GET /api/v1/products/{id}/videos``) shows
   content out of the box. Uses public sample HLS streams; replace with
   real uploads via Admin → Product Videos in production.

Idempotent: the customer is upserted by email; demo videos are tagged
``[demo]`` in the title and purged + re-inserted on every run, so it is
safe to run repeatedly and never duplicates.

Usage::

    python -m scripts.seed_storefront_demo

Pre-req: run ``scripts.seed_catalog_demo`` first so products exist
(this script no-ops the video step gracefully if the catalog is empty).
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.core.db.session import get_sessionmaker
from app.core.security.passwords import hash_password
from app.core.time import utc_now

# --- demo buyer -------------------------------------------------------------
CUSTOMER_EMAIL = "customer@hypershop.dev"
CUSTOMER_PASSWORD = "customerlocal12"  # >= 12 chars
CUSTOMER_NAME = "Demo Customer"
CUSTOMER_PHONE = "+8801700000001"

# --- demo product videos ----------------------------------------------------
# Public sample HLS streams + vertical poster thumbnails. Cycled across the
# first N products. Titles are prefixed ``[demo]`` so re-runs purge cleanly.
SAMPLE_HLS = (
    "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
    "https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_ts/master.m3u8",
    "https://test-streams.mux.dev/test_001/stream.m3u8",
    "https://bitdash-a.akamaihd.net/content/sintel/hls/playlist.m3u8",
)
VIDEO_COUNT = 6
VIDEO_DURATIONS = (30, 22, 18, 27, 35, 24)


async def _seed_customer(s) -> str:
    email = CUSTOMER_EMAIL.lower().strip()
    pwd = hash_password(CUSTOMER_PASSWORD)
    now = utc_now()
    existing = (
        await s.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
    ).scalar_one_or_none()
    if existing is None:
        await s.execute(
            text(
                """
                INSERT INTO users (email, full_name, password_hash, status, email_verified_at, phone)
                VALUES (:e, :n, :p, 'active', :t, :ph)
                """
            ),
            {"e": email, "n": CUSTOMER_NAME, "p": pwd, "t": now, "ph": CUSTOMER_PHONE},
        )
        return "created"
    await s.execute(
        text(
            """
            UPDATE users
            SET password_hash = :p, full_name = :n, status = 'active',
                email_verified_at = COALESCE(email_verified_at, :t)
            WHERE id = :id
            """
        ),
        {"p": pwd, "n": CUSTOMER_NAME, "t": now, "id": existing},
    )
    return "updated"


async def _seed_videos(s) -> int:
    rows = (
        await s.execute(
            text(
                "SELECT id, name FROM products WHERE status = 'active' "
                "ORDER BY created_at LIMIT :n"
            ),
            {"n": VIDEO_COUNT},
        )
    ).all()
    if not rows:
        print("  (no products yet — skipped product videos; run seed_catalog_demo first)")
        return 0
    # purge previous demo videos so re-runs stay idempotent
    await s.execute(text("DELETE FROM product_videos WHERE title LIKE '[demo]%'"))
    now = utc_now()
    for i, (product_id, name) in enumerate(rows):
        await s.execute(
            text(
                """
                INSERT INTO product_videos (
                    id, product_id, title, status, hls_url, thumbnail_url,
                    duration_seconds, approved_at, created_at, updated_at
                )
                VALUES (
                    gen_random_uuid(), :pid, :title, 'approved', :hls, :thumb,
                    :dur, :t, :t, :t
                )
                """
            ),
            {
                "pid": product_id,
                "title": f"[demo] {name} — product reel",
                "hls": SAMPLE_HLS[i % len(SAMPLE_HLS)],
                "thumb": f"https://picsum.photos/seed/hsvid{i + 1}/600/900",
                "dur": VIDEO_DURATIONS[i % len(VIDEO_DURATIONS)],
                "t": now,
            },
        )
    return len(rows)


async def _run() -> int:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        outcome = await _seed_customer(s)
        n_videos = await _seed_videos(s)
    print(f"  {outcome:<7} {CUSTOMER_EMAIL}  (password: {CUSTOMER_PASSWORD})")
    print(f"  seeded  {n_videos} approved product video(s)")
    print("\nseed_storefront_demo: buyer login + product videos ready.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
