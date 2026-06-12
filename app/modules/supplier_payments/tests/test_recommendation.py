"""Pure-function tests for the recommendation engine."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.modules.supplier_payments.recommendation import (
    BillFacts,
    recommend,
)
from app.modules.supplier_payments.state import PaymentPriority


_TODAY = date(2026, 5, 4)


def _facts(**kwargs):
    base = dict(
        bill_id="b1",
        grand_total=Decimal("1000.00"),
        due_date=_TODAY + timedelta(days=15),
        bill_date=_TODAY - timedelta(days=15),
        today=_TODAY,
    )
    base.update(kwargs)
    return BillFacts(**base)


def test_baseline_score_is_normal_band():
    rec = recommend(_facts())
    assert rec.priority == PaymentPriority.NORMAL.value
    assert 40 <= rec.score < 60


def test_overdue_10d_pushes_to_high_band():
    # Baseline 40 + 10*3 (overdue) + 5 (cash_ready) = 75 → HIGH band
    rec = recommend(_facts(due_date=_TODAY - timedelta(days=10)))
    assert rec.priority == PaymentPriority.HIGH.value


def test_long_overdue_pushes_to_critical():
    # 14d overdue: 40 + 14*3 + 5 = 87 → CRITICAL
    rec = recommend(_facts(due_date=_TODAY - timedelta(days=14)))
    assert rec.priority == PaymentPriority.CRITICAL.value
    assert rec.recommended_date == _TODAY


def test_near_due_3d_pushes_to_high():
    rec = recommend(_facts(due_date=_TODAY + timedelta(days=2)))
    assert rec.priority == PaymentPriority.HIGH.value


def test_critical_supplier_bonus_lifts_band():
    # Baseline 40 + 5 (cash_ready) = 45 → NORMAL
    # Add critical-supplier (+20) → 65 → HIGH
    rec = recommend(_facts(is_critical_supplier=True))
    assert rec.score >= 60
    assert rec.priority == PaymentPriority.HIGH.value


def test_on_hold_short_circuits_to_zero():
    rec = recommend(_facts(is_on_hold=True))
    assert rec.score == 0
    assert rec.priority == PaymentPriority.ON_HOLD.value
    assert rec.recommended_method is None


def test_dispute_floors_priority():
    # Even with overdue + critical supplier, dispute caps below CRITICAL.
    rec = recommend(_facts(
        due_date=_TODAY - timedelta(days=20),
        is_critical_supplier=True,
        is_disputed=True,
    ))
    assert rec.priority != PaymentPriority.CRITICAL.value
    assert "dispute_score" in rec.factors


def test_duplicate_suspect_lowers_score_significantly():
    no_dup = recommend(_facts())
    with_dup = recommend(_facts(is_duplicate_suspect=True))
    assert with_dup.score < no_dup.score
    assert no_dup.score - with_dup.score >= 30


def test_score_is_clamped_between_0_and_100():
    # Stack everything to push above 100.
    rec = recommend(_facts(
        due_date=_TODAY - timedelta(days=30),
        is_critical_supplier=True,
        has_early_pay_discount=True,
    ))
    assert 0 <= rec.score <= 100


def test_factors_dict_is_explainable():
    rec = recommend(_facts(
        due_date=_TODAY - timedelta(days=5),
        is_critical_supplier=True,
        cash_ready=False,
    ))
    assert "overdue_days" in rec.factors
    assert "critical_supplier_score" in rec.factors
    assert "cash_short_score" in rec.factors


def test_low_band_uses_due_date_when_available():
    rec = recommend(_facts(
        is_disputed=True, is_duplicate_suspect=True,
        due_date=_TODAY + timedelta(days=20),
    ))
    assert rec.priority == PaymentPriority.LOW.value
    # date should be on or after today
    assert rec.recommended_date >= _TODAY


def test_recommended_amount_equals_grand_total():
    rec = recommend(_facts(grand_total=Decimal("12345.67")))
    assert rec.recommended_amount == Decimal("12345.67")
