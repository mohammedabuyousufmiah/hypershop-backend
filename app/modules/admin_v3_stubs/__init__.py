"""Admin v3 stub routers — wire every UI panel to a live endpoint.

The customer-facing M1-M9 deliverables and the 17 v3 admin UIs (M10-M26)
were shipped on a "UI-first, BE later" cadence. This module fills in
the backend half so every admin panel hits a real route instead of 404
and the frontend's empty-state code paths render correctly.

Each sub-router below covers one namespace from
``packages/api-client/src/admin-v3.ts``. Endpoints are intentionally
**minimal**:

  * **List** endpoints return ``{"items": [], "total": 0}``
  * **Get-single** endpoints return a mock entity shaped like the
    typed DTO in ``packages/types/src/admin-v3.ts``
  * **Mutators** (POST / PATCH / PUT / DELETE) return ``{"ok": true}``
    plus an echoed copy of the body for traceability

This is a wire-up, not a feature implementation. Each namespace gets a
TODO comment pointing at the real backend module that should later
replace the stub. The frontend doesn't change when a stub becomes a
real implementation — that's the whole point of pinning the wire
contract here.
"""

from app.modules.admin_v3_stubs.router import admin_v3_stubs_router

__all__ = ["admin_v3_stubs_router"]
