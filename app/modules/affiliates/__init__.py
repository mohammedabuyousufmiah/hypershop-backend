"""Affiliates — share-and-earn (built 2026-05-11).

Locked spec:
  * Every authenticated customer gets a permanent affiliate record on
    first ``GET /affiliates/me`` (lazy allocation).
  * Per-product share link = ``${origin}/product/{slug}?aff=CODE``.
  * Commission rate default 5 % of subtotal_minor, paid as a loyalty
    ADJUST (+points) on the affiliate's account when the order reaches
    ``payment_confirmed``.
  * 1 BDT = 2 loyalty points (matches the redeem ratio from loyalty
    module). So 5 % of a ৳1000 order = ৳50 = 100 loyalty points.

Out of scope phase 1:
  * Cookie ``?aff=`` attribution at order time — handled FE-side: the
    storefront reads the cookie and POSTs ``affiliate_code`` on order
    confirm; the FE doesn't pass that today (no order-create payload
    field), so the backend exposes ``POST /affiliates/credit`` for the
    checkout-confirm hook to call with the user's own code. Per-order
    explicit attribution lands in phase 2.
"""
