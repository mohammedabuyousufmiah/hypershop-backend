"""Reporting-module exceptions.

All subclass :class:`DomainError` (or one of its specific subclasses)
so the global exception handler maps them to consistent HTTP responses
with stable ``code`` strings.
"""

from __future__ import annotations

from app.core.errors import (
    DomainError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)


class ReportNotFoundError(NotFoundError):
    code = "reporting.report_not_found"


class ReportDeniedError(ForbiddenError):
    code = "reporting.access_denied"


class ReportInvalidFiltersError(ValidationError):
    code = "reporting.invalid_filters"


class ReportExportFormatNotAllowedError(ValidationError):
    code = "reporting.export_format_not_allowed"


class ReportFileNotFoundError(NotFoundError):
    code = "reporting.file_not_found"


class ReportFileExpiredError(DomainError):
    code = "reporting.file_expired"
    status_code = 410


class ReportSignatureInvalidError(ForbiddenError):
    code = "reporting.signature_invalid"


class ReportScheduleNotFoundError(NotFoundError):
    code = "reporting.schedule_not_found"


class ReportSavedFilterNotFoundError(NotFoundError):
    code = "reporting.saved_filter_not_found"
