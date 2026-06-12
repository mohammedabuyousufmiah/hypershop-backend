"""Funnel ORM models — bind to the project-wide ``Base`` so they share
the same metadata (naming convention + timestamptz default) as every
other module. Tables are isolated (no FK to existing customers/products)
so they can be dropped without cascading damage.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base import Base


class FunnelCustomer(Base):
    __tablename__ = "funnel_customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_customer_id: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False,
    )
    hypershop_customer_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True,
    )

    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    marketing_consent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    whatsapp_consent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sms_consent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    ad_retargeting_consent: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    current_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    segment: Mapped[str] = mapped_column(String(64), default="Cold Visitor", server_default="Cold Visitor")
    last_event_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    events = relationship("FunnelEvent", back_populates="customer")

    __table_args__ = (
        Index("ix_funnel_customers_segment_score", "segment", "current_score"),
    )


class FunnelEvent(Base):
    __tablename__ = "funnel_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("funnel_customers.id"), index=True, nullable=False,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(160), unique=True, index=True, nullable=False,
    )
    source: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    event_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    product_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    category_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    campaign_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    value: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    score_delta: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )

    customer = relationship("FunnelCustomer", back_populates="events")

    __table_args__ = (
        Index("ix_funnel_events_customer_event_time", "customer_id", "event_name", "created_at"),
        Index("ix_funnel_events_event_created", "event_name", "created_at"),
        Index("ix_funnel_events_source_created", "source", "created_at"),
        Index("ix_funnel_events_product_created", "product_id", "created_at"),
        Index("ix_funnel_events_category_created", "category_id", "created_at"),
    )


class FunnelFollowUpTask(Base):
    __tablename__ = "funnel_followup_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("funnel_customers.id"), index=True, nullable=False,
    )

    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    message_template_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending")
    blocked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_funnel_followups_status_created", "status", "created_at"),
    )


class FunnelRetargetingExportLog(Base):
    __tablename__ = "funnel_retargeting_export_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    segment: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    exported_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    consent_filtered_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
