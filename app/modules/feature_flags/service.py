"""FeatureFlagService."""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.feature_flags.models import FeatureFlag


def _bucket_for_subject(key: str, subject_id: str) -> int:
    """Stable 0-99 bucket for ``(flag_key, subject_id)``."""
    h = hashlib.sha256(f"{key}:{subject_id}".encode()).digest()
    return h[0] % 100


class FeatureFlagService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(
        self, *, offset: int = 0, limit: int = 100
    ) -> tuple[Sequence[FeatureFlag], int]:
        items = (
            await self.session.execute(
                select(FeatureFlag).order_by(FeatureFlag.key)
                .offset(offset).limit(limit)
            )
        ).scalars().all()
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(FeatureFlag)
                )
            ).scalar_one()
        )
        return items, total

    async def get_by_key(self, key: str) -> FeatureFlag | None:
        return (
            await self.session.execute(
                select(FeatureFlag).where(FeatureFlag.key == key)
            )
        ).scalar_one_or_none()

    async def get_by_id(self, flag_id: UUID) -> FeatureFlag | None:
        return (
            await self.session.execute(
                select(FeatureFlag).where(FeatureFlag.id == flag_id)
            )
        ).scalar_one_or_none()

    async def create(
        self,
        *,
        key: str,
        description: str | None,
        is_enabled: bool,
        rollout_percent: int,
    ) -> FeatureFlag:
        if await self.get_by_key(key):
            raise ConflictError("Flag key already exists.")
        row = FeatureFlag(
            key=key,
            description=description,
            is_enabled=is_enabled,
            rollout_percent=rollout_percent,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(
        self,
        *,
        flag_id: UUID,
        description: str | None,
        is_enabled: bool | None,
        rollout_percent: int | None,
    ) -> FeatureFlag:
        row = await self.get_by_id(flag_id)
        if row is None:
            raise NotFoundError("Flag not found.")
        if description is not None:
            row.description = description
        if is_enabled is not None:
            row.is_enabled = is_enabled
        if rollout_percent is not None:
            row.rollout_percent = rollout_percent
        row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return row

    async def delete(self, flag_id: UUID) -> None:
        row = await self.get_by_id(flag_id)
        if row is None:
            raise NotFoundError("Flag not found.")
        await self.session.delete(row)
        await self.session.flush()

    async def evaluate(
        self, *, key: str, subject_id: str | None
    ) -> dict:
        row = await self.get_by_key(key)
        if row is None or not row.is_enabled:
            return {"key": key, "enabled": False, "rollout_percent": 0}
        if row.rollout_percent >= 100:
            return {"key": key, "enabled": True, "rollout_percent": 100}
        if not subject_id:
            # Without a stable subject id we can't bucket; treat as
            # disabled-for-this-caller.
            return {
                "key": key,
                "enabled": False,
                "rollout_percent": row.rollout_percent,
            }
        bucket = _bucket_for_subject(key, subject_id)
        return {
            "key": key,
            "enabled": bucket < row.rollout_percent,
            "rollout_percent": row.rollout_percent,
        }
