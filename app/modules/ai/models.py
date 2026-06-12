"""AI proposal record + per-call audit ledger.

- :class:`AIProposal` — the durable record of an AI capability call.
  Every call writes one row with the input, the AI output, a confidence
  score, and a status (``draft`` until a human reviews). A pharmacist /
  ops user later resolves the proposal by ACCEPT / AMEND / REJECT;
  acceptance never auto-flips the underlying business resource — it
  only marks the proposal as actioned.
- :class:`AIUsageEvent` — append-only ledger of every provider call
  (success or failure). Drives cost reporting + per-user rate-limit
  visibility. **REVOKE UPDATE/DELETE** so cost data can't be rewritten.

The proposal table holds the raw provider response in a JSONB column
for full audit reproducibility — if a regulator asks "what did the AI
actually return when you decided X?", the answer is one ``SELECT``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class AIProposal(Base, TimestampMixin):
    __tablename__ = "ai_proposals"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="draft",
    )

    # Originator. SystemPrincipal calls (e.g. background fraud job) leave
    # both nulls and rely on the audit_log row for context.
    requested_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Optional cross-module link — what business resource this proposal
    # is about. e.g. ('prescription', <prescription_id>) for OCR,
    # ('order', <order_id>) for fraud detection,
    # ('product_variant', <variant_id>) for stock prediction.
    reference_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    reference_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )

    # Provider snapshot.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(96), nullable=True)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, server_default=text("0"),
    )

    # Bodies. ``input_payload`` redacts file bytes — only metadata
    # (mime, size, hint). ``ai_payload`` is the structured response.
    # ``decision_payload`` is set when a human accepts/amends; if equal
    # to ai_payload, status='accepted', else 'amended'.
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ai_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decision_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Review trail.
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    review_notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('ocr_prescription','suggest_medicines',"
            "'predict_stock','detect_fraud')",
            name="kind_enum",
        ),
        CheckConstraint(
            "status IN ('draft','accepted','amended','rejected','expired')",
            name="status_enum",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_unit_interval",
        ),
        Index("ix_ai_proposals_kind_status", "kind", "status"),
        Index(
            "ix_ai_proposals_reference",
            "reference_type",
            "reference_id",
        ),
        Index("ix_ai_proposals_requested_by", "requested_by"),
        Index("ix_ai_proposals_created_at", "created_at"),
    )


class AIUsageEvent(Base):
    """Append-only ledger of every AI provider call.

    Captures cost (provider's billing units), latency, and outcome
    (success / error). Used by the dashboard / finance layer for
    cost-by-capability reports. REVOKE UPDATE/DELETE — usage data must
    not be rewritable so we can reconcile against the vendor invoice.
    """

    __tablename__ = "ai_usage_events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(96), nullable=True)
    proposal_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ai_proposals.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    success: Mapped[bool] = mapped_column(nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    cost_units: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 6), nullable=True,
    )
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('ocr_prescription','suggest_medicines',"
            "'predict_stock','detect_fraud')",
            name="kind_enum",
        ),
        Index("ix_ai_usage_events_kind_at", "kind", "occurred_at"),
        Index("ix_ai_usage_events_provider", "provider"),
    )
