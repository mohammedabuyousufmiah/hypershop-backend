"""Funnel API aggregator. Mounts the 4 sub-routers under their
canonical prefixes so ``app/main.py`` only needs a single
``include_router(funnel_router, prefix=settings.api_prefix)`` call.

Final paths (with the project's ``/api/v1`` prefix):

    POST /api/v1/funnel/events/track
    GET  /api/v1/funnel/customers
    GET  /api/v1/funnel/customers/hot-leads
    GET  /api/v1/funnel/customers/followup-tasks
    GET  /api/v1/funnel/retargeting/export
    GET  /api/v1/funnel/kpi/{overview,social,website,retargeting,
                            followups,privacy,products,categories}
"""
from fastapi import APIRouter

from app.modules.funnel.api.customers import router as customers_router
from app.modules.funnel.api.events import router as events_router
from app.modules.funnel.api.kpi import router as kpi_router
from app.modules.funnel.api.retargeting import router as retargeting_router

funnel_router = APIRouter()
funnel_router.include_router(events_router, prefix="/funnel/events", tags=["Funnel Events"])
funnel_router.include_router(customers_router, prefix="/funnel/customers", tags=["Funnel Customers"])
funnel_router.include_router(retargeting_router, prefix="/funnel/retargeting", tags=["Funnel Retargeting"])
funnel_router.include_router(kpi_router, prefix="/funnel/kpi", tags=["Funnel KPI Dashboard"])
