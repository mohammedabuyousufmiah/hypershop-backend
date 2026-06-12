"""Module 30 — Reporting Platform: BI cube depth (sprint 13).

8 OLAP-style endpoints layered on top of M30's existing report
builders. All read-only aggregations — no new tables.
"""
from app.modules.bi.api import router as bi_router

__all__ = ["bi_router"]
