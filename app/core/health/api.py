from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.cache import get_redis
from app.core.db.session import get_engine

router = APIRouter(tags=["health"])


class LiveResponse(BaseModel):
    status: Literal["live"] = "live"


class ReadyResponse(BaseModel):
    status: Literal["ready", "degraded"]
    components: dict[str, str]


async def _check_readiness() -> ReadyResponse:
    components: dict[str, str] = {}
    overall: Literal["ready", "degraded"] = "ready"

    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
        components["postgres"] = "ok"
    except Exception as e:
        components["postgres"] = f"error: {type(e).__name__}"
        overall = "degraded"

    try:
        await get_redis().ping()
        components["redis"] = "ok"
    except Exception as e:
        components["redis"] = f"error: {type(e).__name__}"
        overall = "degraded"

    response = ReadyResponse(status=overall, components=components)
    if overall == "degraded":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.model_dump(),
        )
    return response


@router.get(
    "/health",
    response_model=LiveResponse,
    summary="Liveness probe",
    description="Returns 200 as long as the process is up. K8s livenessProbe target.",
)
async def health() -> LiveResponse:
    return LiveResponse()


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness probe",
    description=(
        "Checks Postgres and Redis. Returns 200 only when both dependencies "
        "respond; returns 503 with the same envelope when any dependency is "
        "degraded. K8s readinessProbe target — fail-closed so traffic is "
        "drained off this pod."
    ),
)
async def ready() -> ReadyResponse:
    return await _check_readiness()


@router.get(
    "/health/live",
    response_model=LiveResponse,
    include_in_schema=False,
)
async def health_live_alias() -> LiveResponse:
    return LiveResponse()


@router.get(
    "/health/ready",
    response_model=ReadyResponse,
    include_in_schema=False,
)
async def health_ready_alias() -> ReadyResponse:
    return await _check_readiness()
