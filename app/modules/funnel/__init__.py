"""Funnel tracking, retargeting + KPI dashboard (merged 2026-05-13).

Lifted from the standalone `hypershop_funnel_engine_merged_ready` package
and adapted to the Master-Bundle async SQLAlchemy stack:

* models bind to `app.core.db.base.Base` (timestamptz default + naming
  convention),
* services use ``AsyncSession`` + ``await session.execute(select(...))``
  instead of sync ``Session.query``,
* deps reuse ``app.core.db.session.get_session`` so the funnel router
  participates in the same pool as the rest of the API,
* idempotency-key conflict path uses a fresh select after IntegrityError
  rollback instead of recursing.

Public API surface mounted by ``app/main.py``:

    POST /api/v1/funnel/events/track
    GET  /api/v1/funnel/customers
    GET  /api/v1/funnel/customers/hot-leads
    GET  /api/v1/funnel/customers/followup-tasks
    GET  /api/v1/funnel/retargeting/export
    GET  /api/v1/funnel/kpi/{overview,social,website,retargeting,
                            followups,privacy,products,categories}

Auth: header ``X-HYPERSHOP-FUNNEL-KEY`` + ``X-HYPERSHOP-ROLE`` — demo RBAC
matching the standalone package. Production must replace with real admin
JWT/RBAC (see ``docs/MERGE_INTO_EXISTING_BACKEND.md`` in the source zip).
"""

from app.modules.funnel.api import funnel_router  # noqa: F401
