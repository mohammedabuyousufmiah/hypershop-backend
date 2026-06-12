"""Domain errors for the sellers module."""

from __future__ import annotations

from app.core.errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)


class SellerNotFoundError(NotFoundError):
    code = "seller_not_found"
    public_message = "Seller not found."


class SellerBadStateError(ConflictError):
    code = "seller_bad_state"
    public_message = "Seller is not in a state that allows this action."


class SellerKycIncompleteError(ValidationError):
    """Raised when a KYC submission lacks required fields.

    Phase 1 keeps the required-field set narrow (TIN + NID + bank)
    so a seller can submit even before procuring a trade license.
    Stricter rules can be layered in service code without a schema
    change.
    """

    code = "seller_kyc_incomplete"
    public_message = "KYC submission is missing required fields."


class SellerUserAlreadyLinkedError(ConflictError):
    code = "seller_user_already_linked"
    public_message = "This user is already linked to a seller account."


class SellerUserNotLinkedError(NotFoundError):
    code = "seller_user_not_linked"
    public_message = "This user is not linked to the seller account."
