"""Pagination primitives.

Two styles supported, both ship with FastAPI dependencies:

  * **Page-based** (original) — `?page=1&size=20`, paired with `Page[T]`
    that carries `(items, page, size, total, total_pages)`. Used by
    every existing list endpoint; do NOT change this surface.

  * **Offset-based** (new, 2026-05-16) — `?offset=0&limit=50`, paired
    with `Paginated[T]` that carries `(items, offset, limit, total)`.
    Use this for new endpoints; envelope middleware sniffs the
    `total + offset + limit` keys and lifts them into `meta.pagination`.

A `paginate_query()` async helper runs the count + slice pattern once
so list endpoints stop duplicating ~10 lines of SQLAlchemy.
"""
from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


# ─── Page-based (original) ─────────────────────────────────────────────

class PageParams(BaseModel):
    page: int = Field(default=1, ge=1, le=10_000)
    size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size

    @property
    def limit(self) -> int:
        return self.size


class Page[T](BaseModel):
    items: list[T]
    page: int
    size: int
    total: int

    @property
    def total_pages(self) -> int:
        if self.size == 0:
            return 0
        full, rem = divmod(self.total, self.size)
        return full + (1 if rem else 0)

    @classmethod
    def build(cls, items: list[T], total: int, params: PageParams) -> Page[T]:
        return cls(items=items, page=params.page, size=params.size, total=total)


# ─── Offset-based (new, 2026-05-16) ────────────────────────────────────

DEFAULT_LIMIT: int = 50
MAX_LIMIT: int = 200

T = TypeVar("T")


class OffsetParams(BaseModel):
    """FastAPI dependency for `?offset=&limit=` query params.

    Use as ``page: Annotated[OffsetParams, Depends()]``. Override the
    bounds per endpoint by subclassing.
    """

    offset: Annotated[int, Query(ge=0)] = 0
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT


class Paginated(BaseModel, Generic[T]):
    """Offset-based list response. The envelope middleware lifts the
    counters into `meta.pagination`, so the FE can read pagination
    state from either `body.data` or `body.meta.pagination`."""

    items: list[T]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)


# ─── async helpers ─────────────────────────────────────────────────────

async def paginate_query(
    session: AsyncSession,
    query: Select,
    *,
    offset: int = 0,
    limit: int = DEFAULT_LIMIT,
) -> tuple[list, int]:
    """Run a count + slice against an async session.

    Returns (rows, total). The caller passes the un-paginated SELECT;
    the helper:
      1. Wraps it in `select(func.count()).select_from(query.subquery())`
         to compute the total (works with joins, filters, etc.)
      2. Applies `.offset(offset).limit(limit)` for the slice
      3. Returns both — caller wraps in `Paginated[T]` or `Page[T]`.
    """
    total_q = select(func.count()).select_from(query.subquery())
    total = (await session.execute(total_q)).scalar_one()

    sliced = query.offset(offset).limit(limit)
    rows = (await session.execute(sliced)).scalars().all()

    return list(rows), int(total)


def to_paginated(
    items: list,
    *,
    total: int,
    offset: int,
    limit: int,
) -> Paginated:
    """Build `Paginated[T]` from a (rows, total) pair + offset/limit."""
    return Paginated(items=items, total=total, offset=offset, limit=limit)


__all__ = [
    # Page-based (legacy, kept stable)
    "PageParams",
    "Page",
    # Offset-based (new)
    "OffsetParams",
    "Paginated",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "paginate_query",
    "to_paginated",
]
