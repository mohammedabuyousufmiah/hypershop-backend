"""Audit + state codes for the product_videos module."""

from __future__ import annotations

# --- video lifecycle states (mirrored on the DB CHECK constraint) ---
STATUS_UPLOADED = "uploaded"            # bytes received, ffmpeg pending
STATUS_PROCESSING = "processing"        # ffmpeg running
STATUS_READY_FOR_REVIEW = "ready_for_review"  # ffmpeg done, awaits human approval
STATUS_APPROVED = "approved"            # admin approved → public
STATUS_REJECTED = "rejected"            # admin rejected
STATUS_DISABLED = "disabled"            # admin soft-hidden (reversible)
STATUS_FAILED = "failed"                # ffmpeg crash / unrecoverable

ALL_STATUSES = (
    STATUS_UPLOADED,
    STATUS_PROCESSING,
    STATUS_READY_FOR_REVIEW,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_DISABLED,
    STATUS_FAILED,
)

# --- audit actions ---
ACTION_VIDEO_UPLOADED = "product_video.uploaded"
ACTION_VIDEO_PROCESSED = "product_video.processed"
ACTION_VIDEO_PROCESSING_FAILED = "product_video.processing_failed"
ACTION_VIDEO_APPROVED = "product_video.approved"
ACTION_VIDEO_REJECTED = "product_video.rejected"
ACTION_VIDEO_REOPENED = "product_video.reopened"
ACTION_VIDEO_DISABLED = "product_video.disabled"
ACTION_VIDEO_REENABLED = "product_video.reenabled"
ACTION_VIDEO_DELETED = "product_video.deleted"

# --- video event types ---
EVENT_IMPRESSION = "impression"
EVENT_PLAY = "play"
EVENT_PAUSE = "pause"
EVENT_COMPLETE = "complete"
EVENT_ADD_TO_CART_AFTER_VIDEO = "add_to_cart_after_video"
EVENT_BUY_NOW_AFTER_VIDEO = "buy_now_after_video"

ALL_EVENTS = (
    EVENT_IMPRESSION,
    EVENT_PLAY,
    EVENT_PAUSE,
    EVENT_COMPLETE,
    EVENT_ADD_TO_CART_AFTER_VIDEO,
    EVENT_BUY_NOW_AFTER_VIDEO,
)

# Events that should be deduped per (video_id, session_id) — fire-once
# semantics. The conversion-attribution events ARE NOT here on purpose:
# a customer who buys twice after watching the same video should
# generate two attribution rows.
DEDUPE_EVENTS = frozenset({EVENT_IMPRESSION, EVENT_PLAY, EVENT_COMPLETE})
