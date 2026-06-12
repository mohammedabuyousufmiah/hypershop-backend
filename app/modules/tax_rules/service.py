"""TaxRuleService — resolve + preview."""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.tax_rules.models import TaxRule


class TaxRuleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(
        self, *, offset: int = 0, limit: int = 100
    ) -> tuple[Sequence[TaxRule], int]:
        items = (
            await self.session.execute(
                select(TaxRule).order_by(TaxRule.created_at.desc())
                .offset(offset).limit(limit)
            )
        ).scalars().all()
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(TaxRule)
                )
            ).scalar_one()
        )
        return items, total

    async def get(self, rule_id: UUID) -> TaxRule | None:
        return (
            await self.session.execute(
                select(TaxRule).where(TaxRule.id == rule_id)
            )
        ).scalar_one_or_none()

    async def create(self, **fields) -> TaxRule:
        row = TaxRule(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete(self, rule_id: UUID) -> None:
        row = await self.get(rule_id)
        if row is None:
            raise NotFoundError("Tax rule not found.")
        await self.session.delete(row)
        await self.session.flush()

    async def _resolve(
        self, *, country: str, category_slug: str | None
    ) -> TaxRule | None:
        """Pick the most-specific active rule for (country, category)."""
        # Try category-specific first.
        if category_slug is not None:
            row = (
                await self.session.execute(
                    select(TaxRule).where(
                        TaxRule.is_active.is_(True),
                        TaxRule.country == country,
                        TaxRule.category_slug == category_slug,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                return row
        # Fall back to country-default (category null).
        row = (
            await self.session.execute(
                select(TaxRule).where(
                    TaxRule.is_active.is_(True),
                    TaxRule.country == country,
                    TaxRule.category_slug.is_(None),
                )
            )
        ).scalar_one_or_none()
        return row

    async def preview(
        self,
        *,
        subtotal_minor: int,
        country: str,
        category_slug: str | None,
    ) -> dict:
        rule = await self._resolve(country=country, category_slug=category_slug)
        if rule is None:
            return {
                "subtotal_minor": subtotal_minor,
                "rate_bps": 0,
                "tax_minor": 0,
                "total_minor": subtotal_minor,
                "matched_rule": None,
            }
        tax_minor = int(
            (Decimal(subtotal_minor) * Decimal(rule.rate_bps))
            / Decimal(10_000)
        )
        return {
            "subtotal_minor": subtotal_minor,
            "rate_bps": rule.rate_bps,
            "tax_minor": tax_minor,
            "total_minor": subtotal_minor + tax_minor,
            "matched_rule": rule.id,
        }
