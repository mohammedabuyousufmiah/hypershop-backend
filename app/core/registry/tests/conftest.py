"""Tests in this directory are pure-function — they don't touch the DB.

The repo-root ``app/conftest.py`` declares THREE ``autouse=True`` DB
fixtures (truncate, seed_seller, seed_iam) that fire before every test.
For pure-function tests this adds ~5s of unnecessary DB I/O per test
AND the pool occasionally exhausts, causing transient setup ERRORs
that look like real failures.

This local conftest overrides all three with no-ops so the pure-function
tests run in milliseconds with zero DB dependency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest


@pytest.fixture(autouse=True)
async def _truncate_app_tree_between_tests() -> AsyncIterator[None]:
    """No-op override — pure-function tests don't dirty any tables."""
    yield


@pytest.fixture(autouse=True)
async def _seed_hypershop_direct_seller() -> AsyncIterator[None]:
    """No-op override — pure-function tests don't need the seller seed."""
    yield


@pytest.fixture(autouse=True)
async def _seed_iam_roles_and_default_license() -> AsyncIterator[None]:
    """No-op override — pure-function tests read RoleSpec from Python, not DB."""
    yield
