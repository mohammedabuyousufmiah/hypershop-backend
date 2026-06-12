from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from sqlalchemy import DateTime, MetaData, text
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Postgres-friendly naming convention so all constraints have predictable names
# (matters for Alembic autogenerate diffs and for `DROP CONSTRAINT` ops).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Project-wide default: any `Mapped[datetime]` (or `Mapped[datetime | None]`)
    # compiles to TIMESTAMP WITH TIME ZONE — matching the timestamptz columns
    # actually created by the migrations. Without this every module would have
    # to repeat DateTime(timezone=True), which is forgotten in several places
    # and causes asyncpg to reject tz-aware values bound against naive columns.
    type_annotation_map = {
        datetime: DateTime(timezone=True),
    }


PrimaryKey = Annotated[
    UUID,
    mapped_column(primary_key=True, server_default=text("gen_random_uuid()")),
]

CreatedAt = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    ),
]

UpdatedAt = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
        onupdate=text("(now() AT TIME ZONE 'UTC')"),
    ),
]
