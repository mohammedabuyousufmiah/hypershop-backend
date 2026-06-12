"""Resilience engine — observability endpoint.

Exposes recent self-heal incidents + counters so ops can see what the
engine caught/retried/recovered. Read-only.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.middleware.self_heal import INCIDENTS, _STATS
from app.core.security.rbac import requires_permission

router = APIRouter(tags=["resilience"])

_GUARD = [Depends(requires_permission("audit.read"))]


@router.get("/admin/resilience/status", dependencies=_GUARD)
async def resilience_status() -> dict[str, Any]:
    incidents = list(INCIDENTS)
    return {
        "healthy": _STATS["failed"] == 0 or _STATS["recovered"] >= _STATS["failed"],
        "stats": dict(_STATS),
        "recent_incidents": incidents[-50:],
        "incident_count": len(incidents),
    }
