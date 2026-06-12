"""Smoke test: module imports + factory fallback resolves."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_couriers_module_imports() -> None:
    from app.modules.couriers.api import admin_router, webhook_router
    from app.modules.couriers.models import (
        CourierCodRemittance,
        CourierCredential,
        CourierProvider,
        CourierShipment,
        CourierStatusEvent,
    )
    from app.modules.couriers.providers import get_provider, register_provider
    from app.modules.couriers.providers.not_configured import (
        NotConfiguredCourierProvider,
    )

    nc = get_provider("pathao")
    assert isinstance(nc, NotConfiguredCourierProvider)
    assert all([
        CourierProvider, CourierCredential, CourierShipment,
        CourierStatusEvent, CourierCodRemittance, admin_router,
        webhook_router, register_provider,
    ])
