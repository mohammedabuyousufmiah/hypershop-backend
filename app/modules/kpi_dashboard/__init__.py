"""Read-only KPI dashboard module.

Composes existing tables (orders / order_lines / order_status_history /
... whatever is reachable from the current session) into a single
role-scoped dashboard payload. Owns no tables. Performs NO writes.

The surface is one endpoint:

    GET /api/v1/kpi-dashboard

returning the standardized shape:

    {
      "kpi_cards":       [...],
      "round_bars":      [...],
      "horizontal_bars": [...],
      "donut_charts":    [...],
      "line_charts":     [...],
      "alerts":          [...],
      "deep_links":      [...]
    }

Role tiers (additive):
    staff       — own-day operational metrics
    supervisor  — staff + fleet / COD / settlement
    admin       — supervisor + financials / sellers / catalog
    super_admin — admin + system-level / break-glass alerts

Heavy aggregations are cached in Redis under a key that includes the
caller's tier and the filter hash, with a short TTL.
"""
