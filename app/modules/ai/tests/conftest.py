"""AI test fixtures.

Reset the provider binding back to ``NotConfiguredProvider`` after every
test so a test that binds a fake provider can't leak into the next test.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_ai_provider_binding() -> Iterator[None]:
    from app.modules.ai.providers import reset_provider_binding

    reset_provider_binding()
    yield
    reset_provider_binding()
