"""storefront_cms — admin control surface for the public storefront.

Three small concerns:

- nav items     — top-nav links (label/href/order)
- featured cats — homepage featured category cards
- static pages  — about, terms, privacy, etc.

HomepageBanner lives in the seo module (Module 34); the unified
``GET /storefront/layout`` joins across both.

Save flow:
  admin save → DB write → revalidation webhook fired to the storefront
  → Next's revalidateTag('storefront') flushes the unstable_cache slot
  → next request to ``/storefront/layout`` hits the DB once + repopulates
  → all subsequent requests serve from the warm cache until the next save.

That's the "single hop" — one admin save propagates to every cached
storefront page that depends on the layout, no manual cache flush.
"""

from app.modules.storefront_cms.api import (
    public_router as storefront_public_router,
    admin_router as storefront_admin_router,
)

__all__ = ["storefront_public_router", "storefront_admin_router"]
