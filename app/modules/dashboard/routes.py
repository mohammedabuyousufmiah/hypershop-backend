"""Drop-in router re-export for the dashboard module.

Lets callers mount the dashboard module with the standard one-liner:

    from app.modules.dashboard.routes import router as dashboard_router
    app.include_router(dashboard_router)

The underlying router is composed in ``app.modules.dashboard.api``
(``dashboard_router`` aggregates ``admin_router`` + any future
sub-routers). This shim exposes it under the conventional ``router``
name + ``routes.py`` path so the module follows the same drop-in
mounting pattern as every other Hypershop module.

Note: ``app/main.py`` already mounts this router under
``settings.api_prefix`` (``/api/v1``) inside ``create_app()``. Importing
the same router and calling ``include_router`` a second time would
duplicate every route — use this shim only when bootstrapping a
*separate* FastAPI instance (tests, embedded harness, etc.), not from
the main factory.
"""
from __future__ import annotations

from app.modules.dashboard.api import dashboard_router as router

__all__ = ["router"]
