"""Helpers for callers (storefront/admin) to compose the
``external_customer_id`` and ``idempotency_key`` fields the
``/funnel/events/track`` endpoint expects.

Verbatim from the source zip.
"""
from __future__ import annotations


def map_hypershop_customer(
    user_id: int | None, phone: str | None, guest_id: str | None,
) -> str:
    if user_id:
        return f"user_{user_id}"
    if phone:
        return f"phone_{phone}"
    if guest_id:
        return f"guest_{guest_id}"
    raise ValueError("Cannot map customer without user_id, phone, or guest_id.")


def build_idempotency_key(
    external_customer_id: str,
    event_name: str,
    source: str,
    session_id: str | None = None,
    product_id: str | None = None,
    timestamp_bucket: str | None = None,
) -> str:
    return ":".join([
        external_customer_id,
        event_name,
        source,
        session_id or "-",
        product_id or "-",
        timestamp_bucket or "-",
    ])
