from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.ids import new_id
from app.core.time import utc_now


class DomainEvent(BaseModel):
    """Base class for in-process domain events.

    Use ``EventBus.publish`` for events that should fire side effects within
    the SAME request/transaction boundary (e.g. invalidate a cache).
    Use the outbox for cross-module work that must survive a crash and run
    eventually exactly-once (e.g. send an email, call a webhook).
    """

    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=new_id)
    occurred_at: datetime = Field(default_factory=utc_now)
    type: str

    def topic(self) -> str:
        return self.type


class OutboxEnvelope(BaseModel):
    """Serialised event payload as written to the outbox table."""

    model_config = ConfigDict(extra="forbid")

    type: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
