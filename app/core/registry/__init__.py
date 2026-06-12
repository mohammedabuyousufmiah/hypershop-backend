"""Runtime module + nav registry.

Distinct from `app/core/db/registry.py` (which only registers ORM models
into SQLAlchemy metadata). This package declares the user-facing module
catalog: each admin surface lists its label, nav target, required
permissions, group, and version.

The catalog feeds:

  * `GET /api/v1/admin/config/me` — returns the per-caller nav set
    (filtered by permissions). The FE admin shell renders nav from
    that response instead of a hardcoded list.

Adding a new admin module = append one entry to ``ADMIN_MODULES`` and
the FE picks it up on next `/admin/config/me` fetch. No FE deploy
needed for the nav to update.

Pharmacy modules deliberately excluded per the standing
"no pharmacy in Hypershop" rule.
"""
from app.core.registry.admin_modules import (
    ADMIN_MODULES,
    AdminModule,
    AdminModuleGroup,
    visible_modules_for,
)

__all__ = [
    "ADMIN_MODULES",
    "AdminModule",
    "AdminModuleGroup",
    "visible_modules_for",
]
