"""Pure-function payment-priority + scheduling recommendation engine.

Score range: 0..100. Higher = more urgent.

  80+    pay_immediately   → priority=critical, recommended_date=today
  60-79  pay_soon          → priority=high, date=today+1
  40-59  schedule_normally → priority=normal, date=due_date or today+7
  0-39   hold_or_review    → priority=low, date=due_date or today+14

Scoring factors (positive → push score up = pay sooner):
  - overdue_days        +3 per day overdue (cap +30)
  - near_due_days       +20 if 0..3 days to due, +10 if 4..7
  - critical_supplier   +20
  - on_hold flag        score forced to 0, priority=on_hold
  - dispute flag        score reduced by 30, priority floored at 'low'
  - duplicate flag      score reduced by 40
  - has_recent_payment  -10 (avoid double-pay)
  - early_pay_discount  +15 (lock in the discount)
  - cash_ready          +5 (cash flow comfortable)

Engine v1 — purposely simple and explainable. Every score change is
mirrored in the returned ``factors`` dict so the admin UI can render
"why was this recommended for today?".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.modules.supplier_payments.state import PaymentPriority


ENGINE_VERSION = "v1"


@dataclass(slots=True, frozen=True)
class BillFacts:
    """Inputs to the engine — purely numeric, no DB objects.

    Caller is responsible for collecting these from
    finance.SupplierBill + module-local state + supplier metadata.
    """

    bill_id: str
    grand_total: Decimal
    due_date: date | None
    bill_date: date
    is_disputed: bool = False
    is_duplicate_suspect: bool = False
    is_on_hold: bool = False
    is_critical_supplier: bool = False
    has_early_pay_discount: bool = False
    has_recent_payment: bool = False
    cash_ready: bool = True
    today: date | None = None  # injectable for tests


@dataclass(slots=True)
class Recommendation:
    score: int                       # 0..100
    priority: str                    # PaymentPriority
    recommended_date: date
    recommended_amount: Decimal
    recommended_method: str | None
    factors: dict[str, Any] = field(default_factory=dict)


def recommend(
    facts: BillFacts,
    *,
    default_method: str | None = "bank_transfer",
) -> Recommendation:
    """Pure: facts → recommendation. No DB writes, no I/O."""
    today = facts.today or date.today()
    factors: dict[str, Any] = {}

    # ---------- on-hold short-circuit ----------
    if facts.is_on_hold:
        return Recommendation(
            score=0,
            priority=PaymentPriority.ON_HOLD.value,
            recommended_date=today + timedelta(days=30),
            recommended_amount=facts.grand_total,
            recommended_method=None,
            factors={"on_hold": True},
        )

    # ---------- start at 40 (neutral baseline) ----------
    score = 40
    factors["baseline"] = 40

    # ---------- overdue / near-due ----------
    days_to_due: int | None = None
    if facts.due_date is not None:
        days_to_due = (facts.due_date - today).days
        if days_to_due < 0:
            overdue = min(-days_to_due, 30)
            score += overdue * 3
            factors["overdue_days"] = -days_to_due
            factors["overdue_score"] = overdue * 3
        elif days_to_due <= 3:
            score += 20
            factors["near_due"] = "0-3d"
            factors["near_due_score"] = 20
        elif days_to_due <= 7:
            score += 10
            factors["near_due"] = "4-7d"
            factors["near_due_score"] = 10

    # ---------- critical supplier ----------
    if facts.is_critical_supplier:
        score += 20
        factors["critical_supplier_score"] = 20

    # ---------- early-payment discount ----------
    if facts.has_early_pay_discount:
        score += 15
        factors["early_pay_discount_score"] = 15

    # ---------- cash-flow comfort ----------
    if facts.cash_ready:
        score += 5
        factors["cash_ready_score"] = 5
    else:
        score -= 10
        factors["cash_short_score"] = -10

    # ---------- recent-payment guard ----------
    if facts.has_recent_payment:
        score -= 10
        factors["recent_payment_score"] = -10

    # ---------- dispute / duplicate ----------
    if facts.is_disputed:
        score -= 30
        factors["dispute_score"] = -30
    if facts.is_duplicate_suspect:
        score -= 40
        factors["duplicate_score"] = -40

    # Clamp to 0..100.
    score = max(0, min(100, score))

    # ---------- priority bands ----------
    if score >= 80:
        priority = PaymentPriority.CRITICAL.value
        recommended_date = today
    elif score >= 60:
        priority = PaymentPriority.HIGH.value
        recommended_date = today + timedelta(days=1)
    elif score >= 40:
        priority = PaymentPriority.NORMAL.value
        recommended_date = facts.due_date or today + timedelta(days=7)
        # Don't push past due if score is in normal band.
        if facts.due_date and recommended_date > facts.due_date:
            recommended_date = facts.due_date
    else:
        priority = PaymentPriority.LOW.value
        recommended_date = facts.due_date or today + timedelta(days=14)

    # Disputes and duplicates floor priority at LOW unless overdue
    # blew through the band logic.
    if (facts.is_disputed or facts.is_duplicate_suspect) and priority == PaymentPriority.CRITICAL.value:
        priority = PaymentPriority.HIGH.value

    factors["final_score"] = score
    factors["final_priority"] = priority

    return Recommendation(
        score=score,
        priority=priority,
        recommended_date=recommended_date,
        recommended_amount=facts.grand_total,
        recommended_method=default_method,
        factors=factors,
    )
