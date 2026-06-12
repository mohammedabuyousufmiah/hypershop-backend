"""Status + audit codes for the sellers module — phase 1."""

from __future__ import annotations

# --- seller lifecycle states (mirrored on the DB CHECK constraint) ---
STATUS_REGISTERED = "registered"        # admin created the row, KYC not yet submitted
STATUS_KYC_SUBMITTED = "kyc_submitted"  # all required KYC fields populated, awaiting admin review
STATUS_APPROVED = "approved"            # active seller — can list products (when phase 2 lands)
STATUS_REJECTED = "rejected"            # terminal — KYC failed
STATUS_SUSPENDED = "suspended"          # temporarily blocked; reversible to approved

ALL_STATUSES = (
    STATUS_REGISTERED,
    STATUS_KYC_SUBMITTED,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_SUSPENDED,
)

# --- payout cadence (per-seller enum) ---
PAYOUT_WEEKLY = "weekly"
PAYOUT_BIWEEKLY = "biweekly"
PAYOUT_MONTHLY = "monthly"
ALL_PAYOUT_CADENCES = (PAYOUT_WEEKLY, PAYOUT_BIWEEKLY, PAYOUT_MONTHLY)
DEFAULT_PAYOUT_CADENCE = PAYOUT_MONTHLY

# --- payout methods ---
PAYOUT_METHOD_BKASH = "bkash"
PAYOUT_METHOD_BANK = "bank_transfer"
PAYOUT_METHOD_NAGAD = "nagad"
ALL_PAYOUT_METHODS = (PAYOUT_METHOD_BKASH, PAYOUT_METHOD_BANK, PAYOUT_METHOD_NAGAD)
DEFAULT_PAYOUT_METHOD = PAYOUT_METHOD_BKASH

# --- seller_user role within a seller account ---
SELLER_ROLE_OWNER = "owner"      # primary contact, can add/remove other seller_users
SELLER_ROLE_MANAGER = "manager"  # can manage products + read payouts
SELLER_ROLE_STAFF = "staff"      # can manage products only, no payout visibility
ALL_SELLER_ROLES = (SELLER_ROLE_OWNER, SELLER_ROLE_MANAGER, SELLER_ROLE_STAFF)

# --- audit actions ---
ACTION_SELLER_CREATED = "sellers.seller.created"
ACTION_SELLER_KYC_SUBMITTED = "sellers.seller.kyc_submitted"
ACTION_SELLER_APPROVED = "sellers.seller.approved"
ACTION_SELLER_REJECTED = "sellers.seller.rejected"
ACTION_SELLER_SUSPENDED = "sellers.seller.suspended"
ACTION_SELLER_REINSTATED = "sellers.seller.reinstated"
ACTION_SELLER_USER_LINKED = "sellers.user_link.created"
ACTION_SELLER_USER_UNLINKED = "sellers.user_link.removed"

# --- defaults ---
# Per-seller commission percent — overridable per row by admin.
# Until the CFO settles on a tier model (phase 5), this is the
# placeholder. 10% is mid-range for BD pharmacy marketplaces.
DEFAULT_COMMISSION_PERCENT = "10.00"

# Special seller representing first-party Hypershop direct sales.
# The phase-1 migration seeds this row so phase 2 can backfill
# existing products against it without a separate setup step.
HYPERSHOP_DIRECT_BUSINESS_NAME = "Hypershop Direct"
