"""SQLAlchemy model for gift_cards."""
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


class GiftCard(Base):
    __tablename__ = "gift_cards"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Human-typeable 16-char alphanumeric token (e.g. "HYPR-AB12-CD34-EF56").
    # Stored uppercase + dashes stripped at the boundary so case + spacing
    # doesn't fragment the unique constraint.
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    face_value_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'BDT'"),
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'active'"),
    )

    purchased_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    redeemed_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','redeemed','expired','revoked')",
            name="ck_gift_cards_status",
        ),
        CheckConstraint(
            "face_value_minor > 0", name="ck_gift_cards_face_value_pos",
        ),
        CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_gift_cards_currency_iso",
        ),
        # Status = redeemed requires both redeemer + timestamp.
        CheckConstraint(
            "(status <> 'redeemed') OR "
            "(redeemed_by_user_id IS NOT NULL AND redeemed_at IS NOT NULL)",
            name="ck_gift_cards_redeemed_pair",
        ),
        Index("ix_gift_cards_status", "status"),
        Index("ix_gift_cards_redeemed_by", "redeemed_by_user_id"),
    )
