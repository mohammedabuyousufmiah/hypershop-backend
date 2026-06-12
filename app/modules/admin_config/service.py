"""Module config lookup helpers — typed setting reads + flag checks.

Callers want this:

    from app.modules.admin_config.service import ModuleConfigService
    svc = ModuleConfigService(session)
    max_pg = await svc.get_int("orders", "max_page_size", default=100)
    if await svc.is_flag_enabled("voice-calls", "auto_assign_ringing"):
        ...

Coalescing + defaults live here so endpoint code stays clean. Each
get_* coerces the JSONB ``value`` to the requested Python type and
falls back to ``default`` if the row is missing or the cast fails.

No caching yet — every read is a query. Acceptable today (config is
read once per request at the endpoint top, not in hot loops). When
load justifies it, add a TTL cache here keyed on (module_key,
setting_key) with invalidation hooked into the upsert/delete endpoint
handlers.

Flag rollout:
    ``is_flag_enabled(..., user_id=...)`` returns True if (a) flag.enabled
    and (b) the user falls inside the rollout_percent slice. Slicing is
    deterministic per-user via the low 8 bits of a hash so the same
    user always lands on the same side of the cutoff for a given flag.
"""
from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin_config.models import ModuleFeatureFlag, ModuleSetting


class ModuleConfigService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ─── settings ────────────────────────────────────────────────
    async def get_raw(self, module_key: str, setting_key: str) -> Any:
        row = (
            await self.session.execute(
                select(ModuleSetting).where(
                    ModuleSetting.module_key == module_key,
                    ModuleSetting.setting_key == setting_key,
                )
            )
        ).scalar_one_or_none()
        return row.value if row else None

    async def get_int(
        self, module_key: str, setting_key: str, *, default: int,
    ) -> int:
        v = await self.get_raw(module_key, setting_key)
        if v is None:
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    async def get_str(
        self, module_key: str, setting_key: str, *, default: str,
    ) -> str:
        v = await self.get_raw(module_key, setting_key)
        return str(v) if v is not None else default

    async def get_bool(
        self, module_key: str, setting_key: str, *, default: bool,
    ) -> bool:
        v = await self.get_raw(module_key, setting_key)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes")
        if isinstance(v, (int, float)):
            return bool(v)
        return default

    # ─── flags ───────────────────────────────────────────────────
    async def is_flag_enabled(
        self,
        module_key: str,
        flag_key: str,
        *,
        user_id: UUID | str | None = None,
        default: bool = False,
    ) -> bool:
        """True iff the flag is enabled AND the user is inside the
        rollout_percent slice. Missing flag → ``default``.

        When ``user_id`` is None and ``rollout_percent < 100`` we treat
        it as enabled (the rollout slice can't be evaluated without a
        stable identity; opting in by default is safer for ops dashboards
        that aren't user-scoped).
        """
        row = (
            await self.session.execute(
                select(ModuleFeatureFlag).where(
                    ModuleFeatureFlag.module_key == module_key,
                    ModuleFeatureFlag.flag_key == flag_key,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return default
        if not row.enabled:
            return False
        if row.rollout_percent >= 100:
            return True
        if row.rollout_percent <= 0:
            return False
        if user_id is None:
            return True  # see docstring rationale
        bucket = self._user_bucket(flag_key, user_id)
        return bucket < row.rollout_percent

    @staticmethod
    def _user_bucket(flag_key: str, user_id: UUID | str) -> int:
        """Deterministic 0-99 bucket per (flag, user). Uses MD5 (not for
        crypto — for stable hashing) + the low 16 bits to map into the
        bucket range. Mixing the flag_key in means the same user lands
        in different buckets across flags so we don't accidentally treat
        the same 25% slice as the experimental population for everything.
        """
        seed = f"{flag_key}::{user_id}".encode()
        # ``usedforsecurity=False`` — this MD5 is a stable bucketing hash
        # for feature-flag rollout, not a security primitive. Tells bandit
        # + FIPS-restricted runtimes the use is non-cryptographic.
        h = int.from_bytes(hashlib.md5(seed, usedforsecurity=False).digest()[:2], "big")
        return h % 100
