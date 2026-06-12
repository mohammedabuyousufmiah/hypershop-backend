from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, ConfigDict, Field, model_serializer

from app.core.validation import StrictModel


# Forgiving variant of StrictModel: accepts both backend canonical
# names + customer-web wire aliases, ignores unknown extras. Used
# only for /me + /customers shared schemas that the storefront's
# api-client posts to.
class _ForgivingModel(StrictModel):
    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


# ---------------- Profile (mobile-friendly subset of UserPublic) ----------------


class CustomerProfileResponse(StrictModel):
    id: UUID
    email: str
    full_name: str
    phone: str | None
    phone_verified_at: datetime | None
    email_verified_at: datetime | None
    status: str


class CustomerProfileUpdate(StrictModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)


# ---------------- Device tokens ----------------


class DeviceRegisterRequest(StrictModel):
    kind: str = Field(..., pattern=r"^(fcm|apns|web)$")
    token: str = Field(..., min_length=8, max_length=512)
    app_version: str | None = Field(default=None, max_length=32)
    locale: str | None = Field(default=None, max_length=16)


class DeviceTokenResponse(StrictModel):
    id: UUID
    kind: str
    token: str
    app_version: str | None
    locale: str | None
    last_seen_at: datetime
    is_active: bool
    created_at: datetime


# --- Rider/customer push device alias (mobile PushDeviceService contract) ---
# Mobile sends {token, provider, platform, app, device_model, os_version,
# app_version}. `provider` (fcm/hms/apns) maps to the backend `kind`. Extra
# fields are accepted and ignored. Response is the lightweight {ok, device_id}.
class DevicePushRegisterIn(StrictModel):
    model_config = ConfigDict(extra="ignore")
    token: str = Field(..., min_length=8, max_length=512)
    provider: str = Field(default="fcm", max_length=16)
    app_version: str | None = Field(default=None, max_length=32)
    locale: str | None = Field(default=None, max_length=16)


class DevicePushUnregisterIn(StrictModel):
    model_config = ConfigDict(extra="ignore")
    token: str = Field(..., min_length=8, max_length=512)
    provider: str = Field(default="fcm", max_length=16)


class DevicePushAck(StrictModel):
    ok: bool = True
    device_id: str | None = None


# --- Customer location consent + ping (mobile CustomerLocationService) ---
# Privacy-by-design: we DO NOT persist raw GPS in a profile table. Consent
# changes and pings are recorded to the immutable audit_log only (action
# customer.location.*), so the app gets a deterministic ack and ops keeps a
# compliance trail without building a tracking store.
class CustomerLocationConsentIn(StrictModel):
    model_config = ConfigDict(extra="ignore")
    consent_granted: bool
    source: str = Field(default="mobile_app", max_length=32)


class CustomerLocationPingIn(StrictModel):
    model_config = ConfigDict(extra="ignore")
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    accuracy_meters: float | None = Field(default=None, ge=0)
    captured_for: str = Field(default="checkout_address", max_length=32)


class CustomerLocationAck(StrictModel):
    accepted: bool = True
    decision: str = "accepted"
    reason: str | None = None


# --- Customer preferences (mobile ProfileService get/updatePreferences) ---
class PreferencesResponse(StrictModel):
    locale: str
    currency: str
    email_marketing: bool
    sms_marketing: bool
    push_marketing: bool
    preferred_categories: list[str]
    updated_at: datetime


class PreferencesUpdate(StrictModel):
    model_config = ConfigDict(extra="ignore")
    locale: str | None = Field(default=None, max_length=16)
    currency: str | None = Field(default=None, max_length=8)
    email_marketing: bool | None = None
    sms_marketing: bool | None = None
    push_marketing: bool | None = None
    preferred_categories: list[str] | None = None


# ---------------- Saved addresses ----------------


class CustomerAddressCreate(_ForgivingModel):
    label: str = Field(default="Home", min_length=1, max_length=32)
    recipient_name: str = Field(
        ..., min_length=1, max_length=160,
        validation_alias=AliasChoices("recipient_name", "full_name"),
    )
    phone: str = Field(..., min_length=4, max_length=32)
    line1: str = Field(
        ..., min_length=1, max_length=255,
        validation_alias=AliasChoices("line1", "address_line1"),
    )
    line2: str | None = Field(
        default=None, max_length=255,
        validation_alias=AliasChoices("line2", "address_line2"),
    )
    city: str = Field(..., min_length=1, max_length=96)
    district: str | None = Field(
        default=None, max_length=96,
        validation_alias=AliasChoices("district", "state", "region"),
    )
    division: str | None = Field(default=None, max_length=96)
    postal_code: str | None = Field(default=None, max_length=16)
    country: str = Field(
        default="BD", min_length=2, max_length=3,
        validation_alias=AliasChoices("country", "country_code"),
    )
    is_default: bool = Field(
        default=False,
        validation_alias=AliasChoices("is_default", "is_default_shipping"),
    )


class CustomerAddressUpdate(_ForgivingModel):
    label: str | None = Field(default=None, min_length=1, max_length=32)
    recipient_name: str | None = Field(
        default=None, min_length=1, max_length=160,
        validation_alias=AliasChoices("recipient_name", "full_name"),
    )
    phone: str | None = Field(default=None, min_length=4, max_length=32)
    line1: str | None = Field(
        default=None, min_length=1, max_length=255,
        validation_alias=AliasChoices("line1", "address_line1"),
    )
    line2: str | None = Field(
        default=None, max_length=255,
        validation_alias=AliasChoices("line2", "address_line2"),
    )
    city: str | None = Field(default=None, min_length=1, max_length=96)
    district: str | None = Field(
        default=None, max_length=96,
        validation_alias=AliasChoices("district", "state", "region"),
    )
    division: str | None = Field(default=None, max_length=96)
    postal_code: str | None = Field(default=None, max_length=16)
    country: str | None = Field(
        default=None, min_length=2, max_length=3,
        validation_alias=AliasChoices("country", "country_code"),
    )
    is_default: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("is_default", "is_default_shipping"),
    )


class CustomerAddressResponse(StrictModel):
    id: UUID
    customer_user_id: UUID
    label: str
    recipient_name: str
    phone: str
    line1: str
    line2: str | None
    city: str
    district: str | None
    division: str | None
    postal_code: str | None
    country: str
    is_default: bool
    created_at: datetime
    updated_at: datetime

    @model_serializer(mode="wrap")
    def _serialize_with_aliases(self, handler):
        """Emit BOTH canonical names AND customer-web wire aliases
        so the storefront's ``fromAddress`` normaliser populates
        Account → Addresses + the checkout pre-fill correctly."""
        d = handler(self)
        d["full_name"]            = d.get("recipient_name")
        d["country_code"]         = d.get("country")
        d["state"]                = d.get("district") or d.get("division")
        d["is_default_shipping"]  = d.get("is_default", False)
        d["is_default_billing"]   = d.get("is_default", False)
        return d


# ---------------- Tracking (anonymous) ----------------


class TrackOrderResponse(StrictModel):
    code: str
    status: str
    placed_at: datetime
    payment_confirmed_at: datetime | None
    approved_at: datetime | None
    dispatched_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    grand_total: str  # Decimal serialized as string
    item_count: int  # number of distinct lines


# ---------------- Aggregated home screen ----------------


class HomeOrderSummary(StrictModel):
    id: UUID
    code: str
    status: str
    grand_total: str
    placed_at: datetime
    item_count: int


class CustomerHomeResponse(StrictModel):
    """Single round-trip payload the mobile home screen renders.

    Keeps each block small (max 5 items each) so the response stays
    under ~10 KB. Drill-down screens hit the per-feature endpoints.
    """

    profile: CustomerProfileResponse
    default_address: CustomerAddressResponse | None
    recent_orders: list[HomeOrderSummary]
    counters: dict[str, int]  # active_orders
