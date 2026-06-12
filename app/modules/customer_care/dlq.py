"""Dead-letter queue: persist webhook/job failures for replay & investigation."""
from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def write_dlq(
    db: Session,
    *,
    source: str,
    operation: str,
    payload: dict[str, Any] | bytes | str | None,
    error: BaseException,
    request_id: str | None = None,
) -> str | None:
    """Persist a failed event. Never raises — DLQ failure must not mask original error."""
    from app.models import DeadLetterEntry

    try:
        if isinstance(payload, (bytes, bytearray)):
            try:
                payload_json = payload.decode()
            except Exception:
                payload_json = repr(payload)
        elif isinstance(payload, str):
            payload_json = payload
        elif payload is None:
            payload_json = ""
        else:
            payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        payload_json = repr(payload)[:4000]

    try:
        entry = DeadLetterEntry(
            source=source,
            operation=operation,
            payload=payload_json[:65000],
            error_class=error.__class__.__name__,
            error_message=str(error)[:2000],
            traceback="".join(traceback.format_exception(error))[:8000],
            request_id=request_id or "-",
            status="pending",
            attempts=0,
            created_at=datetime.utcnow(),
        )
        db.add(entry)
        db.commit()
        return entry.id
    except Exception:
        logger.exception("dlq_write_failed source=%s operation=%s", source, operation)
        try:
            db.rollback()
        except Exception:
            pass
        return None
