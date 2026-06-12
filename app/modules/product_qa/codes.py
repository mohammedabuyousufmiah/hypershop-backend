"""Status + audit codes for the product Q&A module."""

from __future__ import annotations

# --- shared lifecycle states (mirror DB CHECK constraint) ---
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_DISABLED = "disabled"

ALL_STATUSES = (
    STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_DISABLED,
)

# --- audit actions ---
ACTION_QUESTION_CREATED = "qa.question.created"
ACTION_QUESTION_APPROVED = "qa.question.approved"
ACTION_QUESTION_REJECTED = "qa.question.rejected"
ACTION_QUESTION_DISABLED = "qa.question.disabled"
ACTION_QUESTION_REENABLED = "qa.question.reenabled"

ACTION_ANSWER_CREATED = "qa.answer.created"
ACTION_ANSWER_APPROVED = "qa.answer.approved"
ACTION_ANSWER_REJECTED = "qa.answer.rejected"
ACTION_ANSWER_DISABLED = "qa.answer.disabled"
ACTION_ANSWER_REENABLED = "qa.answer.reenabled"
ACTION_ANSWER_HELPFUL = "qa.answer.helpful_voted"

# --- bounds ---
QUESTION_MIN = 5
QUESTION_MAX = 1000
ANSWER_MIN = 5
ANSWER_MAX = 4000
EDIT_WINDOW_HOURS = 24

# --- permissions reused from reviews ---
# Q&A intentionally piggybacks on the reviews permission set so a
# customer/admin granted ``reviews.write`` / ``reviews.admin``
# automatically gets the parallel Q&A capability. Splitting them
# would force operators to grant two permissions for a feature pair
# that operationally moves together.
PERM_WRITE = "reviews.write"
PERM_ADMIN = "reviews.admin"
