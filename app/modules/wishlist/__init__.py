"""Wishlist — customer-saved products for later (Daraz / Noon parity).

One table:
  * ``wishlist_items`` — composite-unique on ``(user_id, product_id)``.
    No quantity, no notes; the cart is the right place for both. The
    PDP heart-icon and the ``/account/wishlist`` page are the only
    surfaces in customer-web.

Side-effects: none. Adding to wishlist does NOT trigger a notification,
loyalty point, or analytics fan-out. The analytics module fires its
own ``wishlist.added`` event independently from customer-web.
"""
