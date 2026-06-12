"""Domain errors for the product_videos module."""

from __future__ import annotations

from app.core.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)


class ProductVideoNotFoundError(NotFoundError):
    code = "product_video_not_found"
    public_message = "Product video not found."


class ProductVideoFileTooLargeError(ValidationError):
    code = "product_video_file_too_large"
    public_message = "Uploaded video exceeds the configured size limit."


class ProductVideoUnsupportedTypeError(ValidationError):
    code = "product_video_unsupported_type"
    public_message = "Uploaded file is not a supported video container."


class ProductVideoBadStateError(ConflictError):
    code = "product_video_bad_state"
    public_message = "Video is not in a state that allows this action."


class ProductVideoProcessingError(ServiceUnavailableError):
    """Surfaces an FFmpeg failure to the admin without leaking stderr."""

    code = "product_video_processing_failed"
    public_message = "Video processing failed. Please try a different file."


class ProductVideoFFmpegMissingError(ServiceUnavailableError):
    code = "product_video_ffmpeg_missing"
    public_message = "Server is not configured for video processing."


class ProductVideoEventInvalidError(ValidationError):
    code = "product_video_event_invalid"
    public_message = "Unknown product video event type."


class R2NotConfiguredError(ServiceUnavailableError):
    """Raised when an R2 storage call is attempted without env config.

    The ``details`` payload lists every missing env var so the operator
    can fix the deployment without trial-and-error.
    """

    code = "r2_not_configured"
    public_message = "Object storage is not configured."


class R2ObjectKeyError(ValidationError):
    """Raised when an object key violates the public/private split.

    Examples:
      - upload_private_file called with a key under r2_public_prefix
      - get_public_url called for a key under r2_private_prefix
    Surfaced as 422 so a buggy caller fails loudly instead of silently
    leaking a raw upload onto the CDN.
    """

    code = "r2_object_key_invalid"
    public_message = "Object key does not match the expected R2 prefix."


class BunnyNotConfiguredError(ServiceUnavailableError):
    """Raised when a Bunny Storage call is attempted without env config.

    The ``details`` payload lists every missing env var so the operator
    can fix the deployment without trial-and-error.
    """

    code = "bunny_not_configured"
    public_message = "Bunny Storage is not configured."


class BunnyUploadError(ServiceUnavailableError):
    """Raised when Bunny Storage rejects an upload or delete.

    Bunny returns plain text bodies on errors; we surface the HTTP
    status + body tail so ops can identify auth issues vs zone issues
    without enabling DEBUG logging.
    """

    code = "bunny_upload_failed"
    public_message = "Bunny Storage rejected the request."
