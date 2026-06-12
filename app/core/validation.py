from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base for every API schema in the project.

    - ``extra="forbid"`` rejects unknown fields (defense in depth against
      mass-assignment).
    - ``str_strip_whitespace`` normalizes inputs.
    - ``validate_assignment`` re-runs validators on attribute set so
      service layers can mutate models safely.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        populate_by_name=True,
        from_attributes=True,
    )


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ResourceRef(StrictModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
