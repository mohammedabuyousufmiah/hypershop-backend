"""0061 voice_calls — inbound voice-call routing tables.

Two provider-neutral tables that back the ``voice_call.assign`` short-form
perm endpoint at POST /api/v1/customer-care/voice-calls/{call_id}/assign:

  cc_voice_calls         — one row per inbound call. The ``provider`` +
                           ``provider_call_id`` pair is the external
                           reference (Twilio CallSid / Exotel call_sid /
                           SignalWire call.sid / Banglalink-Hub leg-id);
                           the Hypershop UUID ``id`` is the internal
                           reference everyone else uses.
  cc_voice_call_events   — append-only state-transition + audit ledger.
                           One row per state change (ringing → assigned
                           → in_call → ended) plus any agent-rejected
                           or system-transferred events. Drives the
                           dispatcher timeline UI.

Provider-neutral design — adapter ``WhatsAppMessageAdapter`` Protocol
already exists in ``customer_care/external_adapters/base.py`` with
``VoiceCallRequest`` / ``VoiceCallResult`` dataclasses. The same shape
is reused here: each provider plugs in its own adapter; the tables only
care about the (provider, external_id) pair.

Idempotency: ``(provider, provider_call_id)`` is unique on the calls
table so the same external call can't be ingested twice. The events
table has no unique constraint — multiple rows per (call, event_type)
are valid (e.g. several rejection attempts before final assignment).

Status enum: ``ringing`` (no agent yet, still alerting) →
``assigned`` (agent claimed, softphone alerting) →
``in_call`` (agent answered) →
``ended`` (any terminal — completed, hung up, missed).
Plus ``missed`` (timed out before any agent claim) as a terminal that
skips ``assigned`` / ``in_call``.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

# revision identifiers, used by Alembic.
revision: str = "0061_voice_calls"
down_revision: str | Sequence[str] | None = "0060_payment_settlement"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "cc_voice_calls",
        sa.Column(
            "id", PgUUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_call_id", sa.String(128), nullable=False),
        sa.Column(
            "customer_id", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("from_phone", sa.String(32), nullable=False),
        sa.Column("to_number", sa.String(32), nullable=True),
        sa.Column(
            "agent_id", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default="ringing"),
        sa.Column("priority", sa.String(16), nullable=False,
                  server_default="normal"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("(now() AT TIME ZONE 'UTC')")),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("recording_url", sa.Text, nullable=True),
        sa.Column(
            "metadata", JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('ringing','assigned','in_call','ended','missed')",
            name="cc_voice_calls_status_enum",
        ),
        sa.CheckConstraint(
            "priority IN ('low','normal','high','urgent')",
            name="cc_voice_calls_priority_enum",
        ),
        sa.UniqueConstraint(
            "provider", "provider_call_id",
            name="uq_cc_voice_calls_provider_external_id",
        ),
    )
    op.create_index(
        "ix_cc_voice_calls_status_started",
        "cc_voice_calls", ["status", "started_at"],
    )
    op.create_index(
        "ix_cc_voice_calls_agent_status",
        "cc_voice_calls", ["agent_id", "status"],
    )
    op.create_index(
        "ix_cc_voice_calls_customer",
        "cc_voice_calls", ["customer_id"],
    )

    op.create_table(
        "cc_voice_call_events",
        sa.Column(
            "id", PgUUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "voice_call_id", PgUUID(as_uuid=True),
            sa.ForeignKey("cc_voice_calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=True),
        sa.Column(
            "actor_id", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "payload", JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index(
        "ix_cc_voice_call_events_call_created",
        "cc_voice_call_events", ["voice_call_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_cc_voice_call_events_call_created",
                  table_name="cc_voice_call_events")
    op.drop_table("cc_voice_call_events")
    op.drop_index("ix_cc_voice_calls_customer", table_name="cc_voice_calls")
    op.drop_index("ix_cc_voice_calls_agent_status", table_name="cc_voice_calls")
    op.drop_index("ix_cc_voice_calls_status_started", table_name="cc_voice_calls")
    op.drop_table("cc_voice_calls")
