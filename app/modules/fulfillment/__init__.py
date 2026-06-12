"""Marketplace Fulfillment — aggregation views on top of existing modules.

This module owns ZERO new tables. Every endpoint is a read-only join
across catalog / orders / deliveries / rider_routing / sellers, shaped
for the 4 admin surfaces that didn't fit cleanly into any single
existing module:

- **Seller Pickup Queue**  — orders the rider hub needs to pick up
                              from sellers within next N hours
- **Reschedule Queue**     — failed-delivery orders awaiting next slot
- **SLA Breach Alerts**    — orders past their dispatch/delivery SLA
- **Seller Delay Monitor** — sellers whose orders are stuck in
                              ``ready_to_ship`` past their pack SLA

All read-only; writes happen via the existing module endpoints
(rider_routing for reassignment, returns_v2 for hub-leg toggle, etc.).
"""
from app.modules.fulfillment.api import router as fulfillment_router
from app.modules.fulfillment.marketplace_api import (
    router as marketplace_fulfillment_router,
)

__all__ = ["fulfillment_router", "marketplace_fulfillment_router"]
