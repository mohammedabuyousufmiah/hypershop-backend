from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from pydantic import BaseModel, Field, field_validator

_TWO_PLACES: Final = Decimal("0.01")
_ALLOWED_CURRENCIES: Final = frozenset({"BDT", "USD", "EUR", "GBP", "INR", "AED"})


def quantize_money(value: Decimal | int | str) -> Decimal:
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


class Money(BaseModel):
    # No ``decimal_places`` constraint — the ``_check_amount`` validator
    # rounds half-up to 2 places, so inputs with more places (e.g. raw
    # 0.5%-of-something arithmetic) are accepted and quantized rather
    # than rejected upstream.
    amount: Decimal = Field(..., max_digits=14)
    currency: str = Field(..., min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, v: str) -> str:
        u = v.upper()
        if u not in _ALLOWED_CURRENCIES:
            raise ValueError(f"unsupported currency: {v}")
        return u

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, v: Decimal) -> Decimal:
        if v.is_nan() or v.is_infinite():
            raise ValueError("amount must be finite")
        return quantize_money(v)

    def add(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError("cannot add money of different currencies")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def subtract(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError("cannot subtract money of different currencies")
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def multiply(self, factor: Decimal | int) -> Money:
        f = factor if isinstance(factor, Decimal) else Decimal(factor)
        return Money(amount=quantize_money(self.amount * f), currency=self.currency)
