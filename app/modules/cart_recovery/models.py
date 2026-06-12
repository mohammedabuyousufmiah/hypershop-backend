"""ORM mappings for cart_recovery — dispatches + suppressions."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class HypershopCartRecoveryDispatch(Base):
    __tablename__ = "hypershop_cart_recovery_dispatches"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
    )
    # Nullable after migration 0071 so win-back rows can persist (those
    # are keyed by customer, not cart).
    cart_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("carts.id", ondelete="CASCADE"),
        nullable=True,
    )
    customer_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    milestone: Mapped[str] = mapped_column(String(24), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    template_code: Mapped[str] = mapped_column(String(64), nullable=False)
    locale: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default=text("'bn'"),
    )
    recipient: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    suppression_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    recovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    recovered_order_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "milestone IN ('cart_1h','cart_6h','cart_24h','winback_7d','winback_30d')",
            name="ck_cart_recovery_dispatch_milestone",
        ),
        CheckConstraint(
            "channel IN ('whatsapp','email','push','sms')",
            name="ck_cart_recovery_dispatch_channel",
        ),
        CheckConstraint(
            "status IN ('queued','sent','suppressed','failed','log_only')",
            name="ck_cart_recovery_dispatch_status",
        ),
        # Partial-unique indexes (created in migration 0071) replace
        # the old NOT-NULL UNIQUE so winback rows can persist:
        #   ux_cart_recovery_dispatches_cart_milestone WHERE cart_id IS NOT NULL
        #   ux_cart_recovery_dispatches_winback_customer_milestone
        #     WHERE cart_id IS NULL AND customer_user_id IS NOT NULL
        # SQLAlchemy can't express partial-unique directly via
        # UniqueConstraint, so we mirror them as Index(unique=True,
        # postgresql_where=...) for declarative parity.
        Index(
            "ux_cart_recovery_dispatches_cart_milestone",
            "cart_id", "milestone",
            unique=True,
            postgresql_where=text("cart_id IS NOT NULL"),
        ),
        Index(
            "ux_cart_recovery_dispatches_winback_customer_milestone",
            "customer_user_id", "milestone",
            unique=True,
            postgresql_where=text(
                "cart_id IS NULL AND customer_user_id IS NOT NULL",
            ),
        ),
        Index(
            "ix_cart_recovery_dispatch_customer_at",
            "customer_user_id", text("created_at DESC"),
        ),
        Index(
            "ix_cart_recovery_dispatch_status_at",
            "status", "created_at",
        ),
    )


class HypershopCartRecoverySuppression(Base):
    __tablename__ = "hypershop_cart_recovery_suppressions"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    customer_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    key_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    key_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "channel IN ('whatsapp','email','push','sms','all')",
            name="ck_cart_recovery_supp_channel",
        ),
        CheckConstraint(
            "reason IN ('opted_out','bounced','complained','frequency_cap',"
            "'recent_purchase','manual')",
            name="ck_cart_recovery_supp_reason",
        ),
        Index(
            "uq_cart_recovery_supp_user_channel",
            "customer_user_id", "channel",
            unique=True,
            postgresql_where=text("customer_user_id IS NOT NULL"),
        ),
        Index(
            "ix_cart_recovery_supp_email_channel",
            "key_email", "channel",
            postgresql_where=text("key_email IS NOT NULL"),
        ),
        Index(
            "ix_cart_recovery_supp_phone_channel",
            "key_phone", "channel",
            postgresql_where=text("key_phone IS NOT NULL"),
        ),
    )


__all__ = [
    "HypershopCartRecoveryDispatch",
    "HypershopCartRecoverySuppression",
]
