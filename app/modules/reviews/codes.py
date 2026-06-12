"""Status + audit codes for the reviews module."""

from __future__ import annotations

# --- review lifecycle states (mirrored on the DB CHECK constraint) ---
STATUS_PENDING = "pending"        # awaits admin moderation
STATUS_APPROVED = "approved"      # public
STATUS_REJECTED = "rejected"      # terminal — admin said no
STATUS_DISABLED = "disabled"      # admin soft-hidden (reversible to approved)

ALL_STATUSES = (
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_DISABLED,
)

# --- audit actions (stored in audit_logs.action) ---
ACTION_REVIEW_CREATED = "reviews.review.created"
ACTION_REVIEW_APPROVED = "reviews.review.approved"
ACTION_REVIEW_REJECTED = "reviews.review.rejected"
ACTION_REVIEW_DISABLED = "reviews.review.disabled"
ACTION_REVIEW_REENABLED = "reviews.review.reenabled"
ACTION_REVIEW_HELPFUL = "reviews.review.helpful_voted"

# --- outbox event types (consumed by future search-rank integration / push) ---
EVT_REVIEW_APPROVED = "reviews.review.approved"
EVT_REVIEW_AGGREGATE_RECOMPUTED = "reviews.aggregate.recomputed"

# --- review media (phase 2) ---
MEDIA_KIND_IMAGE = "image"
ALL_MEDIA_KINDS = (MEDIA_KIND_IMAGE,)
ALLOWED_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
ALLOWED_IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp")
MAX_MEDIA_PER_REVIEW = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB

ACTION_REVIEW_MEDIA_UPLOADED = "reviews.review.media_uploaded"
ACTION_REVIEW_MEDIA_DELETED = "reviews.review.media_deleted"

# --- bounds ---
RATING_MIN = 1
RATING_MAX = 5
TITLE_MAX = 160
BODY_MAX = 4000
# Verified-purchase window — orders older than this can no longer be
# the basis for a review. 365 days lines up with most BD pharmacy
# customer-trust expectations and stops "I bought once 3 years ago"
# fatigue reviews from polluting current product pages.
VERIFIED_PURCHASE_WINDOW_DAYS = 365
# After publish, customers can edit for this long. Past that the
# review is locked — operators can still moderate.
EDIT_WINDOW_HOURS = 24
