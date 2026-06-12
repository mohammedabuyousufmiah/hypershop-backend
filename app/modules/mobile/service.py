"""Customer mobile service.

Owns three concerns:

- :class:`MobileService.profile_*` — read + update of the calling
  customer's own row in ``users``.
- :class:`MobileService.device_*` — push-token registration. Tokens
  are upserted on (user_id, token) so a re-launching app re-registers
  cleanly without orphaning old rows.
- :class:`MobileService.address_*` — saved-address CRUD. Setting
  ``is_default=True`` on one address atomically demotes any current
  default for that user (the partial unique index would otherwise
  collide).
- :func:`build_home` — the aggregated home-screen payload. Reads
  recent orders and address state in one round trip; each block
  is capped at 5 items so the response stays small.
- :func:`track_by_code` — anonymous order tracking. Requires the order
  code AND the last 4 digits of the recipient phone, so possessing the
  code alone (which is short and could be guessed) is not enough.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.errors import (
    BusinessRuleError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.core.security.principal import Principal
from app.core.time import utc_now
from app.modules.iam.models import User
from app.modules.mobile.models import (
    CustomerAddress,
    CustomerPreferences,
    DeviceToken,
)
from app.modules.mobile.repository import (
    CustomerAddressRepository,
    CustomerPreferencesRepository,
    DeviceTokenRepository,
)


_HOME_BLOCK_LIMIT = 5


class MobileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.devices = DeviceTokenRepository(session)
        self.addresses = CustomerAddressRepository(session)
        self.preferences = CustomerPreferencesRepository(session)

    # ---------------- Profile ----------------

    async def get_profile(self, user_id: UUID) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def update_profile(
        self, *, principal: Principal, fields: dict[str, Any],
    ) -> User:
        user = await self.get_profile(principal.user_id)
        if "phone" in fields and fields["phone"] is not None:
            new_phone = fields["phone"].strip()
            if new_phone != (user.phone or ""):
                # Changing phone resets the verification timestamp; phone-OTP
                # verification (paused on SMS provider creds) will rebind it.
                user.phone = new_phone or None
                user.phone_verified_at = None
        if "full_name" in fields and fields["full_name"]:
            user.full_name = fields["full_name"]
        await self.session.flush()
        await record_audit(
            actor=principal,
            action="customer.profile.update",
            resource_type="user",
            resource_id=user.id,
            metadata={"changed": [k for k in fields if fields[k] is not None]},
        )
        return user

    # ---------------- Device tokens ----------------

    async def register_device(
        self,
        *,
        principal: Principal,
        kind: str,
        token: str,
        app_version: str | None,
        locale: str | None,
    ) -> DeviceToken:
        device = await self.devices.upsert(
            user_id=principal.user_id,
            kind=kind,
            token=token,
            app_version=app_version,
            locale=locale,
            last_seen_at=utc_now(),
        )
        await record_audit(
            actor=principal,
            action="customer.device.register",
            resource_type="device_token",
            resource_id=device.id,
            metadata={"kind": kind, "app_version": app_version},
        )
        return device

    async def list_devices(self, user_id: UUID) -> list[DeviceToken]:
        return list(await self.devices.list_for_user(user_id))

    async def deactivate_device(
        self, *, principal: Principal, device_id: UUID,
    ) -> None:
        await self.devices.deactivate(user_id=principal.user_id, device_id=device_id)
        await record_audit(
            actor=principal,
            action="customer.device.deactivate",
            resource_type="device_token",
            resource_id=device_id,
        )

    async def unregister_device_by_token(
        self, *, principal: Principal, token: str,
    ) -> bool:
        """Token-based unregister (mobile push de-registration on logout).

        Idempotent: unknown/already-inactive token → no-op returns False.
        """
        removed = await self.devices.deactivate_by_token(
            user_id=principal.user_id, token=token,
        )
        await record_audit(
            actor=principal,
            action="customer.device.unregister",
            resource_type="device_token",
            resource_id=None,
            metadata={"matched": removed},
        )
        return removed

    # ---------------- Preferences ----------------

    async def get_preferences(self, user_id: UUID) -> CustomerPreferences:
        return await self.preferences.get_or_create(user_id)

    async def update_preferences(
        self, *, principal: Principal, fields: dict[str, Any],
    ) -> CustomerPreferences:
        row = await self.preferences.update(principal.user_id, fields)
        await record_audit(
            actor=principal,
            action="customer.preferences.update",
            resource_type="customer_preferences",
            resource_id=row.id,
            metadata={"keys": sorted(fields.keys())},
        )
        return row

    # ---------------- Addresses ----------------

    async def list_addresses(self, user_id: UUID) -> list[CustomerAddress]:
        return list(await self.addresses.list_for_user(user_id))

    async def add_address(
        self, *, principal: Principal, fields: dict[str, Any],
    ) -> CustomerAddress:
        if fields.get("is_default"):
            await self.addresses.clear_default(principal.user_id)
        addr = await self.addresses.add(
            customer_user_id=principal.user_id, **fields,
        )
        await record_audit(
            actor=principal,
            action="customer.address.add",
            resource_type="customer_address",
            resource_id=addr.id,
            metadata={"is_default": addr.is_default},
        )
        return addr

    async def update_address(
        self,
        *,
        principal: Principal,
        address_id: UUID,
        fields: dict[str, Any],
    ) -> CustomerAddress:
        addr = await self.addresses.get(
            address_id=address_id, user_id=principal.user_id,
        )
        if addr is None:
            raise NotFoundError("Address not found.")
        if fields.get("is_default") is True and not addr.is_default:
            await self.addresses.clear_default(principal.user_id)
        addr = await self.addresses.update(address=addr, fields=fields)
        await record_audit(
            actor=principal,
            action="customer.address.update",
            resource_type="customer_address",
            resource_id=address_id,
        )
        return addr

    async def delete_address(
        self, *, principal: Principal, address_id: UUID,
    ) -> None:
        addr = await self.addresses.get(
            address_id=address_id, user_id=principal.user_id,
        )
        if addr is None:
            raise NotFoundError("Address not found.")
        await self.addresses.delete(addr)
        await record_audit(
            actor=principal,
            action="customer.address.delete",
            resource_type="customer_address",
            resource_id=address_id,
        )

    # ---------------- Tracking (anonymous) ----------------

    async def track_by_code(
        self, *, code: str, phone_last4: str,
    ) -> dict[str, Any]:
        """Anonymous track. Caller must provide the order code AND the
        last 4 digits of the recipient phone — possessing the code
        alone is not enough to reveal order details.
        """
        from app.modules.orders.models import Order

        if not phone_last4 or len(phone_last4) != 4 or not phone_last4.isdigit():
            raise ValidationError("phone_last4 must be exactly 4 digits.")
        order = (
            await self.session.execute(
                select(Order).where(Order.code == code),
            )
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError("Order not found.")
        addr = order.delivery_address or {}
        recipient_phone = str(addr.get("phone") or "").replace(" ", "")
        if not recipient_phone or recipient_phone[-4:] != phone_last4:
            # Same 404 message — don't disclose whether the order code
            # exists if the phone check fails.
            raise NotFoundError("Order not found.")
        return {
            "code": order.code,
            "status": order.status,
            "placed_at": order.placed_at,
            "payment_confirmed_at": order.payment_confirmed_at,
            "approved_at": order.approved_at,
            "dispatched_at": order.dispatched_at,
            "completed_at": order.completed_at,
            "cancelled_at": order.cancelled_at,
            "cancellation_reason": order.cancellation_reason,
            "grand_total": str(order.grand_total),
            "item_count": len(order.lines),
        }

    # ---------------- Aggregated home ----------------

    async def build_home(self, principal: Principal) -> dict[str, Any]:
        from app.modules.orders.models import Order
        user = await self.get_profile(principal.user_id)
        default_addr = await self.addresses.get_default(principal.user_id)

        # Recent orders.
        recent_orders_stmt = (
            select(Order)
            .where(Order.customer_user_id == principal.user_id)
            .order_by(Order.placed_at.desc())
            .limit(_HOME_BLOCK_LIMIT)
        )
        recent_orders = (await self.session.execute(recent_orders_stmt)).scalars().all()

        active_states = ('pending_payment', 'payment_confirmed', 'stock_reserved',
                         'approved', 'packing', 'out_for_delivery')
        active_orders_stmt = (
            select(func.count(Order.id))
            .where(
                Order.customer_user_id == principal.user_id,
                Order.status.in_(active_states),
            )
        )
        active_orders_count = int(
            (await self.session.execute(active_orders_stmt)).scalar_one() or 0,
        )

        return {
            "profile": user,
            "default_address": default_addr,
            "recent_orders": [
                {
                    "id": o.id, "code": o.code, "status": o.status,
                    "grand_total": str(o.grand_total), "placed_at": o.placed_at,
                    "item_count": len(o.lines),
                }
                for o in recent_orders
            ],
            "counters": {
                "active_orders": active_orders_count,
            },
        }
