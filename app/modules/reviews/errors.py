"""Domain errors for the reviews module."""

from __future__ import annotations

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)


class ReviewNotFoundError(NotFoundError):
    code = "review_not_found"
    public_message = "Review not found."


class ReviewBadStateError(ConflictError):
    code = "review_bad_state"
    public_message = "Review is not in a state that allows this action."


class ReviewNotVerifiedPurchaserError(ForbiddenError):
    """Customer has not bought this product within the eligibility window.

    Phase 1 enforces verified-purchase reviews only — anti-spam plus
    builds buyer trust. The window is configured in
    ``codes.VERIFIED_PURCHASE_WINDOW_DAYS``.
    """

    code = "review_not_verified_purchaser"
    public_message = (
        "Only customers who have purchased this product can leave a review."
    )


class ReviewAlreadyExistsError(ConflictError):
    """One review per (customer, product) — phase 1 keeps it simple.

    A future phase could allow a follow-up review after a re-purchase;
    that's a separate product decision, not in scope here.
    """

    code = "review_already_exists"
    public_message = "You have already reviewed this product."


class ReviewEditWindowExpiredError(ConflictError):
    code = "review_edit_window_expired"
    public_message = "The 24-hour edit window has elapsed."


class ReviewRatingOutOfRangeError(ValidationError):
    code = "review_rating_out_of_range"
    public_message = "Rating must be between 1 and 5."


class ReviewHelpfulSelfVoteError(ConflictError):
    """Customer can't upvote their own review."""

    code = "review_helpful_self_vote"
    public_message = "You cannot mark your own review as helpful."


# ---------------- Phase-2 review media errors ----------------


class ReviewMediaTooManyError(ConflictError):
    code = "review_media_too_many"
    public_message = "This review already has the maximum number of attached photos."


class ReviewMediaUnsupportedTypeError(ValidationError):
    code = "review_media_unsupported_type"
    public_message = "Uploaded file is not a supported image format (JPEG / PNG / WebP)."


class ReviewMediaTooLargeError(ValidationError):
    code = "review_media_too_large"
    public_message = "Uploaded image exceeds the configured size limit."


class ReviewMediaNotFoundError(NotFoundError):
    code = "review_media_not_found"
    public_message = "Review media not found."
