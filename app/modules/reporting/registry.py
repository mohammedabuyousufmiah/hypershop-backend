"""In-memory registry of report builders.

A *builder* is an async callable that produces rows for one report
code. The registry sits between:

  - the DB ``report_definitions`` table (metadata: name, columns,
    allowed_roles, default_filters), and
  - the actual query logic (a Python function in ``builders/``).

This split lets ops add a new report row to the DB *and* have it
work — provided the ``code`` matches a builder registered here. If
no builder is found for a registered code, ``/run`` returns 503
(misconfiguration), rather than silently returning empty rows.

Builders are registered at import-time in ``builders/__init__.py``.
The registry is process-local; the worker and api processes both
populate it during startup.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

# Builder signature:
#   async def builder(
#       *,
#       session: AsyncSession,
#       filters: dict[str, Any],
#       scope_type: str,
#       current_user_id,  # UUID | None — None for system runs
#       max_rows: int,
#   ) -> list[dict[str, Any]]
ReportBuilder = Callable[..., Awaitable[list[dict[str, Any]]]]


@dataclass(slots=True)
class RegisteredBuilder:
    code: str
    builder: ReportBuilder
    # Default columns for the report — used by bootstrap.py when a row
    # is missing from report_definitions on first boot. The DB row
    # wins after seeding so ops can re-order via admin API later.
    default_columns: list[dict[str, str]]
    # The category the bootstrap row will use if no DB row exists yet.
    default_category: str
    default_name: str
    # Roles that get can_view by default at bootstrap. Empty = locked.
    default_allowed_roles: tuple[str, ...]
    default_export_formats: tuple[str, ...] = ("csv", "xlsx")


class _Registry:
    def __init__(self) -> None:
        self._builders: dict[str, RegisteredBuilder] = {}

    def register(self, entry: RegisteredBuilder) -> None:
        if entry.code in self._builders:
            raise ValueError(
                f"Report builder already registered: {entry.code}",
            )
        self._builders[entry.code] = entry

    def get(self, code: str) -> RegisteredBuilder | None:
        return self._builders.get(code)

    def all(self) -> Iterable[RegisteredBuilder]:
        return self._builders.values()

    def codes(self) -> list[str]:
        return sorted(self._builders.keys())


report_registry = _Registry()


__all__ = [
    "RegisteredBuilder",
    "ReportBuilder",
    "report_registry",
]
