"""Repository for WhatsApp delivery-status receipts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.whatsapp_webhook.models import WhatsAppMessageStatus


class WhatsAppMessageStatusRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self, *,
        wamid: str,
        status: str,
        recipient_msisdn: str,
        status_timestamp: datetime,
        error_code: str | None,
        error_title: str | None,
        error_message: str | None,
        raw_payload: dict[str, Any],
    ) -> bool:
        """Idempotent insert on (wamid, status). Returns True if a new
        row was created, False on duplicate."""
        stmt = (
            pg_insert(WhatsAppMessageStatus.__table__)
            .values(
                wamid=wamid,
                status=status,
                recipient_msisdn=recipient_msisdn,
                status_timestamp=status_timestamp,
                error_code=error_code,
                error_title=error_title,
                error_message=error_message,
                raw_payload=raw_payload,
            )
            .on_conflict_do_nothing(index_elements=["wamid", "status"])
            .returning(WhatsAppMessageStatus.__table__.c.id)
        )
        result = await self.session.execute(stmt)
        return result.first() is not None
