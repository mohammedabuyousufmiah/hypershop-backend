from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars
from structlog.processors import CallsiteParameter, CallsiteParameterAdder
from structlog.types import EventDict, Processor

from app.core.config import get_settings

_REDACT_KEYS = frozenset(
    {
        "password",
        "current_password",
        "new_password",
        "old_password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "set-cookie",
        "api_key",
        "private_key",
        "otp",
        "otp_code",
        "card",
        "card_number",
        "cvv",
        "cvc",
        "pin",
    }
)


def _redact(_logger: object, _name: str, event: EventDict) -> EventDict:
    for key in list(event.keys()):
        if key.lower() in _REDACT_KEYS:
            event[key] = "***redacted***"
    return event


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access", "asyncio", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    shared_processors: list[Processor] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        CallsiteParameterAdder(
            parameters=[CallsiteParameter.MODULE, CallsiteParameter.FUNC_NAME],
        ),
        _redact,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None, **bindings: Any) -> structlog.stdlib.BoundLogger:
    log = structlog.get_logger(name) if name else structlog.get_logger()
    if bindings:
        log = log.bind(**bindings)
    return log


__all__ = ["bind_contextvars", "clear_contextvars", "configure_logging", "get_logger"]
