"""Customer mobile endpoints (auth required).

Mounted under ``/me/*`` and ``/mobile/*`` (aggregated home).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.iam.models import User
from app.modules.mobile.models import (
    CustomerAddress,
    CustomerPreferences,
    DeviceToken,
)
from app.modules.mobile.schemas import (
    CustomerAddressCreate,
    CustomerAddressResponse,
    CustomerAddressUpdate,
    CustomerHomeResponse,
    CustomerLocationAck,
    CustomerLocationConsentIn,
    CustomerLocationPingIn,
    CustomerProfileResponse,
    CustomerProfileUpdate,
    DevicePushAck,
    DevicePushRegisterIn,
    DevicePushUnregisterIn,
    DeviceRegisterRequest,
    DeviceTokenResponse,
    PreferencesResponse,
    PreferencesUpdate,
)
from app.modules.mobile.service import MobileService

# Two routers because they sit at different prefixes.
profile_router = APIRouter(prefix="/me", tags=["customer-self"])
home_router = APIRouter(prefix="/mobile", tags=["customer-mobile"])

_READ_SELF = "iam.user.read.self"
_UPDATE_SELF = "iam.user.update.self"


def _user_to_profile(u: User) -> CustomerProfileResponse:
    return CustomerProfileResponse(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        phone=u.phone,
        phone_verified_at=u.phone_verified_at,
        email_verified_at=u.email_verified_at,
        status=u.status if isinstance(u.status, str) else u.status.value,
    )


def _addr_to_response(a: CustomerAddress) -> CustomerAddressResponse:
    return CustomerAddressResponse.model_validate(a)


def _device_to_response(d: DeviceToken) -> DeviceTokenResponse:
    return DeviceTokenResponse.model_validate(d)


# ---------------- Profile ----------------


@profile_router.get(
    "/profile",
    response_model=CustomerProfileResponse,
    dependencies=[Depends(requires_permission(_READ_SELF))],
)
async def get_profile(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CustomerProfileResponse:
    async with uow.transactional() as session:
        svc = MobileService(session)
        u = await svc.get_profile(principal.user_id)
    return _user_to_profile(u)


@profile_router.patch(
    "/profile",
    response_model=CustomerProfileResponse,
    dependencies=[Depends(requires_permission(_UPDATE_SELF))],
)
async def update_profile(
    payload: CustomerProfileUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CustomerProfileResponse:
    fields = payload.model_dump(exclude_unset=True)
    async with uow.transactional() as session:
        svc = MobileService(session)
        u = await svc.update_profile(principal=principal, fields=fields)
    return _user_to_profile(u)


# ---------------- Devices ----------------


@profile_router.post(
    "/devices",
    response_model=DeviceTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register / refresh a push-notification token",
    description=(
        "Idempotent on (user_id, token). Re-registering the same token "
        "from the same user updates ``last_seen_at`` + metadata."
    ),
    dependencies=[Depends(requires_permission(_UPDATE_SELF))],
)
async def register_device(
    payload: DeviceRegisterRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DeviceTokenResponse:
    async with uow.transactional() as session:
        svc = MobileService(session)
        d = await svc.register_device(
            principal=principal,
            kind=payload.kind,
            token=payload.token,
            app_version=payload.app_version,
            locale=payload.locale,
        )
    return _device_to_response(d)


@profile_router.get(
    "/devices",
    response_model=list[DeviceTokenResponse],
    dependencies=[Depends(requires_permission(_READ_SELF))],
)
async def list_devices(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> list[DeviceTokenResponse]:
    async with uow.transactional() as session:
        svc = MobileService(session)
        rows = await svc.list_devices(principal.user_id)
    return [_device_to_response(d) for d in rows]


@profile_router.delete(
    "/devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_UPDATE_SELF))],
)
async def deactivate_device(
    device_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = MobileService(session)
        await svc.deactivate_device(principal=principal, device_id=device_id)


# ---------------- Addresses ----------------


@profile_router.get(
    "/addresses",
    response_model=list[CustomerAddressResponse],
    dependencies=[Depends(requires_permission(_READ_SELF))],
)
async def list_addresses(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> list[CustomerAddressResponse]:
    async with uow.transactional() as session:
        svc = MobileService(session)
        rows = await svc.list_addresses(principal.user_id)
    return [_addr_to_response(a) for a in rows]


@profile_router.post(
    "/addresses",
    response_model=CustomerAddressResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_UPDATE_SELF))],
)
async def add_address(
    payload: CustomerAddressCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CustomerAddressResponse:
    async with uow.transactional() as session:
        svc = MobileService(session)
        a = await svc.add_address(
            principal=principal, fields=payload.model_dump(),
        )
    return _addr_to_response(a)


@profile_router.patch(
    "/addresses/{address_id}",
    response_model=CustomerAddressResponse,
    dependencies=[Depends(requires_permission(_UPDATE_SELF))],
)
async def update_address(
    address_id: UUID,
    payload: CustomerAddressUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CustomerAddressResponse:
    fields = payload.model_dump(exclude_unset=True)
    async with uow.transactional() as session:
        svc = MobileService(session)
        a = await svc.update_address(
            principal=principal, address_id=address_id, fields=fields,
        )
    return _addr_to_response(a)


@profile_router.delete(
    "/addresses/{address_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_UPDATE_SELF))],
)
async def delete_address(
    address_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = MobileService(session)
        await svc.delete_address(principal=principal, address_id=address_id)


# ---------------- Aggregated home ----------------


@home_router.get(
    "/home",
    response_model=CustomerHomeResponse,
    summary="Single-call payload for the customer-app home screen",
    description=(
        "Combines profile, default address, and recent orders in one "
        "round trip. Each block is capped at 5 items so the response "
        "stays under ~10 KB."
    ),
    dependencies=[Depends(requires_permission(_READ_SELF))],
)
async def home(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CustomerHomeResponse:
    async with uow.transactional() as session:
        svc = MobileService(session)
        data = await svc.build_home(principal)
    return CustomerHomeResponse(
        profile=_user_to_profile(data["profile"]),
        default_address=_addr_to_response(data["default_address"])
            if data["default_address"] else None,
        recent_orders=data["recent_orders"],
        counters=data["counters"],
    )


# ---------------- Rider/customer push-device alias ----------------
# Mobile apps (rider PushDeviceService) call POST /rider/devices/register and
# /rider/devices/unregister with a {token, provider} body. These reuse the
# DeviceToken store via MobileService — no new table. `provider` maps to the
# backend `kind` (fcm/apns/web; "gms"/"hms"→fcm). Auth = the caller's own self
# permission, so any logged-in rider/customer can manage their own tokens.
rider_devices_router = APIRouter(prefix="/rider/devices", tags=["rider-devices"])

_PROVIDER_TO_KIND = {"gms": "fcm", "fcm": "fcm", "hms": "fcm", "apns": "apns", "web": "web"}


@rider_devices_router.post(
    "/register",
    response_model=DevicePushAck,
    summary="Register / refresh a rider push token (idempotent on token)",
    dependencies=[Depends(requires_permission(_UPDATE_SELF))],
)
async def rider_register_device(
    payload: DevicePushRegisterIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DevicePushAck:
    kind = _PROVIDER_TO_KIND.get(payload.provider.lower(), "fcm")
    async with uow.transactional() as session:
        svc = MobileService(session)
        d = await svc.register_device(
            principal=principal,
            kind=kind,
            token=payload.token,
            app_version=payload.app_version,
            locale=payload.locale,
        )
    return DevicePushAck(ok=True, device_id=str(d.id))


@rider_devices_router.post(
    "/unregister",
    response_model=DevicePushAck,
    summary="Unregister a rider push token (idempotent; no-op if unknown)",
    dependencies=[Depends(requires_permission(_UPDATE_SELF))],
)
async def rider_unregister_device(
    payload: DevicePushUnregisterIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DevicePushAck:
    async with uow.transactional() as session:
        svc = MobileService(session)
        await svc.unregister_device_by_token(principal=principal, token=payload.token)
    return DevicePushAck(ok=True, device_id=None)


# ---------------- Combined router export ----------------

router = APIRouter()
router.include_router(profile_router)
router.include_router(home_router)
router.include_router(rider_devices_router)

# Compatibility alias: customer-web's api-client calls
# ``/api/v1/customers/<path>`` for profile + addresses (matches the
# pre-2026 backend that lived at `/customers`). Backend now mounts
# the same handlers under `/me`. We hand-roll thin proxy routes
# (rather than mount profile_router twice — that double-prefixes
# under FastAPI) so both URL shapes resolve to the same handlers.
customers_alias = APIRouter(prefix="/customers", tags=["customer-self-alias"])
# profile_router stores full paths like ``/me/addresses`` (prefix is
# baked in). Strip the leading /me and re-register under /customers
# so the customer-web's api-client (which expects ``/customers/...``)
# resolves to the same handlers.
for _route in list(profile_router.routes):
    new_path = _route.path
    if new_path.startswith("/me"):
        new_path = new_path[len("/me"):] or "/"
    customers_alias.add_api_route(
        new_path,
        _route.endpoint,
        methods=list(_route.methods) if _route.methods else None,
        response_model=getattr(_route, "response_model", None),
        status_code=getattr(_route, "status_code", None),
        summary=getattr(_route, "summary", None),
        description=getattr(_route, "description", None),
        name=f"alias_{_route.name}" if _route.name else None,
    )


# ---------------- Customer location consent + ping ----------------
# Mobile CustomerLocationService posts here. Privacy-by-design: we audit-log
# the consent decision / ping (no raw-GPS tracking table) and return an ack.
@customers_alias.post(
    "/location/consent",
    response_model=CustomerLocationAck,
    summary="Record customer location-sharing consent (granted/revoked)",
    dependencies=[Depends(requires_permission(_UPDATE_SELF))],
)
async def customer_location_consent(
    payload: CustomerLocationConsentIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CustomerLocationAck:
    async with uow.transactional():
        await record_audit(
            actor=principal,
            action="customer.location.consent",
            resource_type="customer",
            resource_id=principal.user_id,
            metadata={"consent_granted": payload.consent_granted, "source": payload.source},
        )
    return CustomerLocationAck(
        accepted=True,
        decision="granted" if payload.consent_granted else "revoked",
    )


@customers_alias.post(
    "/location/current",
    response_model=CustomerLocationAck,
    summary="Submit the customer's current location (e.g. for checkout address)",
    dependencies=[Depends(requires_permission(_UPDATE_SELF))],
)
async def customer_location_current(
    payload: CustomerLocationPingIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> CustomerLocationAck:
    async with uow.transactional():
        await record_audit(
            actor=principal,
            action="customer.location.ping",
            resource_type="customer",
            resource_id=principal.user_id,
            metadata={
                "lat": round(payload.latitude, 5),
                "lon": round(payload.longitude, 5),
                "accuracy_m": payload.accuracy_meters,
                "captured_for": payload.captured_for,
            },
        )
    return CustomerLocationAck(accepted=True, decision="accepted")


# ---------------- Customer preferences ----------------
def _prefs_to_response(p: CustomerPreferences) -> PreferencesResponse:
    return PreferencesResponse(
        locale=p.locale,
        currency=p.currency,
        email_marketing=p.email_marketing,
        sms_marketing=p.sms_marketing,
        push_marketing=p.push_marketing,
        preferred_categories=list(p.preferred_categories or []),
        updated_at=p.updated_at,
    )


@customers_alias.get(
    "/preferences",
    response_model=PreferencesResponse,
    summary="Get the customer's preferences (auto-created with defaults)",
    dependencies=[Depends(requires_permission(_READ_SELF))],
)
async def get_preferences(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PreferencesResponse:
    async with uow.transactional() as session:
        svc = MobileService(session)
        p = await svc.get_preferences(principal.user_id)
        return _prefs_to_response(p)


@customers_alias.patch(
    "/preferences",
    response_model=PreferencesResponse,
    summary="Update the customer's preferences (partial)",
    dependencies=[Depends(requires_permission(_UPDATE_SELF))],
)
async def update_preferences(
    payload: PreferencesUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PreferencesResponse:
    fields = payload.model_dump(exclude_unset=True)
    async with uow.transactional() as session:
        svc = MobileService(session)
        if not fields:
            p = await svc.get_preferences(principal.user_id)
        else:
            p = await svc.update_preferences(principal=principal, fields=fields)
        return _prefs_to_response(p)


router.include_router(customers_alias)
