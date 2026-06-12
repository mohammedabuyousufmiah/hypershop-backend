"""Compute seller ratings for all active sellers (demo bootstrap).

Wraps the ARQ cron job ``recompute_all_seller_ratings_job`` so a fresh demo
DB populates the Seller Ratings admin page (else it shows "No ratings").
Run: ``python -m scripts.recompute_seller_ratings``
"""
from __future__ import annotations

import asyncio

# Register EVERY model so cross-module FKs (e.g. -> sellers) resolve when this
# runs outside the app lifespan; else NoReferencedTableError.
from app.core.db.registry import import_all_models

import_all_models()
# import_all_models() doesn't pull the sellers model; the snapshot FK
# (-> sellers.id) won't resolve at flush without it.
import app.modules.sellers.models  # noqa: E402,F401

from app.modules.seller_rating.jobs import (  # noqa: E402
    recompute_all_seller_ratings_job,
)


async def _main() -> None:
    counts = await recompute_all_seller_ratings_job({})
    print(f"seller ratings recomputed: {counts}")


if __name__ == "__main__":
    asyncio.run(_main())
