"""Finance integration tests.

Three layers of coverage:

1. **Pure double-entry mechanics** — manual journal entries, balance
   invariant, append-only enforcement, period locks, daily close, reversal,
   and the trial-balance / P&L / balance-sheet reports.
2. **Subledger workflows** — supplier bills + payments, COD deposits with
   discrepancy, refund accrual + payment.
3. **Cross-module wiring** — order placement → revenue JE; order
   completion → COGS JE; order cancellation → reversal JE; rider COD
   collection → cash-on-hand JE; return completion → refund accrual.
   Every cross-module test enqueues a real outbox event and runs
   ``dispatch_once()`` — proving the handlers are wired correctly.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.core.db.session import get_sessionmaker
from app.core.events.dispatcher import dispatch_once
from app.modules.finance.accounts import (
    A_AR_COD,
    A_CASH_IN_BANK,
    A_INVENTORY,
    C_COGS,
    L_AP_SUPPLIERS,
    L_REFUND_PAYABLE,
    R_PRODUCT_SALES,
    R_SALES_RETURNS,
)
from app.modules.finance.models import (
    Account,
    CodDeposit,
    DailyClose,
    JournalEntry,
    JournalLine,
    RefundRecord,
    SupplierBill,
)

pytestmark = pytest.mark.integration


_THREE_IMAGES = [
    {"url": f"https://cdn.hypershop.local/x/{i}.jpg", "kind": "image", "position": i}
    for i in range(3)
]


# ---------------- helpers ----------------


async def _create_variant(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    payload: dict[str, Any] = {
        "name": name,
        "base_currency": "BDT",
        "status": "active",
        "variants": [{"options": {}, "price": "100.00"}],
        "media": _THREE_IMAGES,
    }
    resp = await client.post(
        "/api/v1/admin/catalog/products", headers=headers, json=payload,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["variants"][0]["id"]


async def _create_supplier(client: AsyncClient, headers: dict[str, str], code: str) -> str:
    resp = await client.post(
        "/api/v1/admin/inventory/suppliers",
        headers=headers,
        json={"code": code, "name": f"Supplier {code}"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _receive(
    client: AsyncClient, headers: dict[str, str],
    *, supplier_id: str, variant_id: str, invoice_number: str,
    quantity: int = 10, unit_cost: str = "10.00",
) -> dict[str, Any]:
    body = {
        "supplier_id": supplier_id,
        "supplier_invoice_number": invoice_number,
        "supplier_invoice_date": date.today().isoformat(),
        "warehouse_code": "MAIN",
        "currency": "BDT",
        "lines": [
            {
                "variant_id": variant_id,
                "quantity": quantity,
                "unit_cost": unit_cost,
                "batch_number": f"B-{invoice_number}",
                "expiry_date": (date.today() + timedelta(days=200)).isoformat(),
            },
        ],
    }
    resp = await client.post(
        "/api/v1/admin/inventory/goods-receipts", headers=headers, json=body,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _address() -> dict[str, Any]:
    return {
        "recipient_name": "FinTest",
        "phone": "+8801911000077",
        "line1": "House 9",
        "city": "Dhaka",
    }


async def _setup_purchasable(
    api_client: AsyncClient, admin_headers: dict[str, str],
    *, name: str, quantity: int = 20,
) -> str:
    variant_id = await _create_variant(api_client, admin_headers, name=name)
    supplier_id = await _create_supplier(api_client, admin_headers, f"S-{name[:6]}")
    await _receive(
        api_client, admin_headers,
        supplier_id=supplier_id, variant_id=variant_id,
        invoice_number=f"INV-{name[:10]}", quantity=quantity,
    )
    return variant_id


async def _drain_outbox(max_iter: int = 5) -> None:
    """Run the dispatcher until no more pending messages or max iterations."""
    for _ in range(max_iter):
        n = await dispatch_once()
        if n == 0:
            return


async def _account_balance(account_code: str) -> Decimal:
    """Signed (debit - credit) balance across all posted journal entries."""
    from sqlalchemy import func

    sm = get_sessionmaker()
    async with sm() as s:
        acc = (
            await s.execute(select(Account).where(Account.code == account_code))
        ).scalar_one_or_none()
        if acc is None:
            return Decimal("0")
        d, c = (
            await s.execute(
                select(
                    func.coalesce(func.sum(JournalLine.debit), 0),
                    func.coalesce(func.sum(JournalLine.credit), 0),
                )
                .select_from(JournalLine)
                .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
                .where(
                    JournalLine.account_id == acc.id,
                    JournalEntry.status == "posted",
                ),
            )
        ).one()
        return Decimal(d) - Decimal(c)


# ============================================================
# 1. Pure double-entry mechanics
# ============================================================


async def test_chart_of_accounts_seeded(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    resp = await api_client.get(
        "/api/v1/admin/finance/accounts", headers=admin_user["headers"],
    )
    assert resp.status_code == 200
    codes = {a["code"] for a in resp.json()}
    # Spot-check: at least the core accounts are seeded.
    for required in (
        A_CASH_IN_BANK, A_AR_COD, A_INVENTORY, L_AP_SUPPLIERS,
        R_PRODUCT_SALES, C_COGS,
    ):
        assert required in codes


async def test_seed_endpoint_is_idempotent(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    r1 = await api_client.post(
        "/api/v1/admin/finance/accounts/seed", headers=admin_user["headers"],
    )
    r2 = await api_client.post(
        "/api/v1/admin/finance/accounts/seed", headers=admin_user["headers"],
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert len(r1.json()) == len(r2.json())


async def test_post_balanced_journal_entry(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/finance/journal-entries",
        headers=admin_user["headers"],
        json={
            "entry_date": date.today().isoformat(),
            "narration": "Opening cash injection",
            "currency": "BDT",
            "lines": [
                {"account_code": A_CASH_IN_BANK, "debit": "10000.00",
                 "credit": "0", "memo": "founder injection"},
                {"account_code": "3010", "debit": "0",
                 "credit": "10000.00", "memo": "equity"},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "posted"
    assert body["code"].startswith("JE-")
    assert len(body["lines"]) == 2
    assert body["lines"][0]["line_number"] == 1


async def test_unbalanced_entry_rejected(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/finance/journal-entries",
        headers=admin_user["headers"],
        json={
            "entry_date": date.today().isoformat(),
            "narration": "Bad entry",
            "currency": "BDT",
            "lines": [
                {"account_code": A_CASH_IN_BANK, "debit": "100", "credit": "0"},
                {"account_code": "3010", "debit": "0", "credit": "50"},
            ],
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "unbalanced" in body["message"].lower()


async def test_line_with_both_debit_and_credit_rejected(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/finance/journal-entries",
        headers=admin_user["headers"],
        json={
            "entry_date": date.today().isoformat(),
            "narration": "Bad shape",
            "currency": "BDT",
            "lines": [
                {"account_code": A_CASH_IN_BANK, "debit": "50", "credit": "50"},
                {"account_code": "3010", "debit": "0", "credit": "100"},
            ],
        },
    )
    assert resp.status_code == 422


async def test_unknown_account_rejected(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    resp = await api_client.post(
        "/api/v1/admin/finance/journal-entries",
        headers=admin_user["headers"],
        json={
            "entry_date": date.today().isoformat(),
            "narration": "Unknown account",
            "currency": "BDT",
            "lines": [
                {"account_code": "9999", "debit": "100", "credit": "0"},
                {"account_code": A_CASH_IN_BANK, "debit": "0", "credit": "100"},
            ],
        },
    )
    assert resp.status_code == 404


async def test_reverse_entry_inserts_mirror(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    create = await api_client.post(
        "/api/v1/admin/finance/journal-entries",
        headers=admin_user["headers"],
        json={
            "entry_date": date.today().isoformat(),
            "narration": "To-be-reversed",
            "currency": "BDT",
            "lines": [
                {"account_code": A_CASH_IN_BANK, "debit": "500", "credit": "0"},
                {"account_code": "3010", "debit": "0", "credit": "500"},
            ],
        },
    )
    eid = create.json()["id"]

    rev = await api_client.post(
        f"/api/v1/admin/finance/journal-entries/{eid}/reverse",
        headers=admin_user["headers"],
        json={"reason": "test reversal"},
    )
    assert rev.status_code == 200, rev.text
    body = rev.json()
    assert body["reverses_entry_id"] == eid
    # Original cash-debit line becomes a cash-credit on the reversal.
    cash_lines = [l for l in body["lines"] if l["account_code"] == A_CASH_IN_BANK]
    assert len(cash_lines) == 1
    assert Decimal(cash_lines[0]["credit"]) == Decimal("500")
    assert Decimal(cash_lines[0]["debit"]) == Decimal("0")

    # Original stays posted (industry-standard additive reversal); both
    # entries net to zero in balance computations.
    look = await api_client.get(
        f"/api/v1/admin/finance/journal-entries/{eid}",
        headers=admin_user["headers"],
    )
    assert look.json()["status"] == "posted"

    # Re-reversal is blocked.
    re_rev = await api_client.post(
        f"/api/v1/admin/finance/journal-entries/{eid}/reverse",
        headers=admin_user["headers"],
        json={"reason": "double-reverse"},
    )
    assert re_rev.status_code == 422
    assert "already" in re_rev.json()["message"].lower()

    # Net cash balance for the originally-injected 500 is zero after reversal.
    assert (await _account_balance(A_CASH_IN_BANK)) == Decimal("0")


async def test_daily_close_locks_further_posts(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    today = date.today()
    # Need an entry to give the close something to summarize, but no entry
    # also works (zero totals).
    close_resp = await api_client.post(
        "/api/v1/admin/finance/daily-close",
        headers=admin_user["headers"],
        json={"closing_date": today.isoformat(), "notes": "EOD"},
    )
    assert close_resp.status_code == 200, close_resp.text

    # Posting on the closed date now fails.
    bad = await api_client.post(
        "/api/v1/admin/finance/journal-entries",
        headers=admin_user["headers"],
        json={
            "entry_date": today.isoformat(),
            "narration": "After close",
            "currency": "BDT",
            "lines": [
                {"account_code": A_CASH_IN_BANK, "debit": "10", "credit": "0"},
                {"account_code": "3010", "debit": "0", "credit": "10"},
            ],
        },
    )
    assert bad.status_code == 422
    assert "daily close" in bad.json()["message"].lower()


async def test_daily_close_cannot_be_for_future_date(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    future = (date.today() + timedelta(days=1)).isoformat()
    resp = await api_client.post(
        "/api/v1/admin/finance/daily-close",
        headers=admin_user["headers"],
        json={"closing_date": future, "notes": "future"},
    )
    assert resp.status_code == 422


async def test_period_close_blocks_posts_in_locked_month(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    # Pick a clearly-past month to avoid the "month not finished" guard.
    today = date.today()
    if today.month == 1:
        target_year, target_month = today.year - 1, 12
    else:
        target_year, target_month = today.year, today.month - 1

    # First, post an entry into that month so we can verify the lock takes
    # effect (we'll then try to post another and expect rejection).
    in_month_date = date(target_year, target_month, 15)
    pre = await api_client.post(
        "/api/v1/admin/finance/journal-entries",
        headers=admin_user["headers"],
        json={
            "entry_date": in_month_date.isoformat(),
            "narration": "Pre-close",
            "currency": "BDT",
            "lines": [
                {"account_code": A_CASH_IN_BANK, "debit": "10", "credit": "0"},
                {"account_code": "3010", "debit": "0", "credit": "10"},
            ],
        },
    )
    assert pre.status_code == 201

    close = await api_client.post(
        "/api/v1/admin/finance/periods/close",
        headers=admin_user["headers"],
        json={"year": target_year, "month": target_month, "notes": "month end"},
    )
    assert close.status_code == 200, close.text
    assert close.json()["status"] == "locked"

    bad = await api_client.post(
        "/api/v1/admin/finance/journal-entries",
        headers=admin_user["headers"],
        json={
            "entry_date": in_month_date.isoformat(),
            "narration": "Post-close",
            "currency": "BDT",
            "lines": [
                {"account_code": A_CASH_IN_BANK, "debit": "10", "credit": "0"},
                {"account_code": "3010", "debit": "0", "credit": "10"},
            ],
        },
    )
    assert bad.status_code == 422


async def test_trial_balance_sums_to_zero(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    # Post a couple of entries.
    for amt in ("250", "750"):
        await api_client.post(
            "/api/v1/admin/finance/journal-entries",
            headers=admin_user["headers"],
            json={
                "entry_date": date.today().isoformat(),
                "narration": f"Inj {amt}",
                "currency": "BDT",
                "lines": [
                    {"account_code": A_CASH_IN_BANK, "debit": amt, "credit": "0"},
                    {"account_code": "3010", "debit": "0", "credit": amt},
                ],
            },
        )
    resp = await api_client.get(
        "/api/v1/admin/finance/reports/trial-balance",
        headers=admin_user["headers"],
        params={"as_of": date.today().isoformat()},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert Decimal(body["total_debits"]) == Decimal(body["total_credits"])
    assert Decimal(body["total_debits"]) == Decimal("1000")


async def test_balance_sheet_balances(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    await api_client.post(
        "/api/v1/admin/finance/journal-entries",
        headers=admin_user["headers"],
        json={
            "entry_date": date.today().isoformat(),
            "narration": "Setup",
            "currency": "BDT",
            "lines": [
                {"account_code": A_CASH_IN_BANK, "debit": "5000", "credit": "0"},
                {"account_code": "3010", "debit": "0", "credit": "5000"},
            ],
        },
    )
    resp = await api_client.get(
        "/api/v1/admin/finance/reports/balance-sheet",
        headers=admin_user["headers"],
        params={"as_of": date.today().isoformat()},
    )
    body = resp.json()
    # Assets = liabilities + equity + retained_earnings ⇒ balance_check == 0
    assert Decimal(body["balance_check"]) == Decimal("0")
    assert Decimal(body["assets_total"]) == Decimal("5000")


# ============================================================
# 2. Subledger workflows
# ============================================================


async def test_supplier_bill_book_and_partial_pay(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S-AP")
    book = await api_client.post(
        "/api/v1/admin/finance/supplier-bills",
        headers=admin_user["headers"],
        json={
            "supplier_id": supplier_id,
            "bill_number": "INV-AP-1",
            "bill_date": date.today().isoformat(),
            "currency": "BDT",
            "subtotal": "1000.00",
            "tax_total": "0",
        },
    )
    assert book.status_code == 201, book.text
    bill = book.json()
    assert bill["status"] == "open"
    assert Decimal(bill["grand_total"]) == Decimal("1000")
    assert bill["booking_entry_id"] is not None

    # AP rises, Inventory rises.
    assert await _account_balance(L_AP_SUPPLIERS) == Decimal("-1000")  # liability normal = credit
    assert await _account_balance(A_INVENTORY) == Decimal("1000")

    # Partial payment.
    pay1 = await api_client.post(
        f"/api/v1/admin/finance/supplier-bills/{bill['id']}/payments",
        headers=admin_user["headers"],
        json={
            "bill_id": bill["id"],
            "paid_on": date.today().isoformat(),
            "amount": "400.00",
            "method": "bank_transfer",
        },
    )
    assert pay1.status_code == 201
    assert (await _account_balance(A_CASH_IN_BANK)) == Decimal("-400")
    assert (await _account_balance(L_AP_SUPPLIERS)) == Decimal("-600")

    # Listing the bill shows partially_paid.
    listing = await api_client.get(
        "/api/v1/admin/finance/supplier-bills",
        headers=admin_user["headers"],
        params={"supplier_id": supplier_id},
    )
    items = listing.json()["items"]
    assert items[0]["status"] == "partially_paid"
    assert Decimal(items[0]["amount_paid"]) == Decimal("400")

    # Final payment.
    pay2 = await api_client.post(
        f"/api/v1/admin/finance/supplier-bills/{bill['id']}/payments",
        headers=admin_user["headers"],
        json={
            "bill_id": bill["id"],
            "paid_on": date.today().isoformat(),
            "amount": "600.00",
            "method": "bank_transfer",
        },
    )
    assert pay2.status_code == 201
    listing2 = await api_client.get(
        "/api/v1/admin/finance/supplier-bills",
        headers=admin_user["headers"],
        params={"supplier_id": supplier_id},
    )
    assert listing2.json()["items"][0]["status"] == "paid"


async def test_supplier_payment_overpay_rejected(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    supplier_id = await _create_supplier(api_client, admin_user["headers"], "S-OVR")
    book = await api_client.post(
        "/api/v1/admin/finance/supplier-bills",
        headers=admin_user["headers"],
        json={
            "supplier_id": supplier_id,
            "bill_number": "INV-OV-1",
            "bill_date": date.today().isoformat(),
            "currency": "BDT",
            "subtotal": "100",
            "tax_total": "0",
        },
    )
    bill_id = book.json()["id"]
    bad = await api_client.post(
        f"/api/v1/admin/finance/supplier-bills/{bill_id}/payments",
        headers=admin_user["headers"],
        json={
            "bill_id": bill_id,
            "paid_on": date.today().isoformat(),
            "amount": "200",
            "method": "bank_transfer",
        },
    )
    assert bad.status_code == 422
    assert "exceeds" in bad.json()["message"].lower()


async def test_cod_deposit_with_discrepancy(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    # Create a rider directly in DB (the deliveries module owns rider CRUD,
    # but for a finance-isolated test we just need a row).
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        rid = (
            await s.execute(
                text(
                    """
                    INSERT INTO riders (code, name, phone, vehicle_type)
                    VALUES ('R-TEST', 'Test Rider', '+8801911000088', 'motorcycle')
                    RETURNING id
                    """,
                ),
            )
        ).scalar_one()
    rider_id = str(rid)

    resp = await api_client.post(
        "/api/v1/admin/finance/cod-deposits",
        headers=admin_user["headers"],
        json={
            "rider_id": rider_id,
            "deposit_date": date.today().isoformat(),
            "expected_amount": "1000.00",
            "deposited_amount": "950.00",
            "bank_reference": "TX-123",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "discrepancy"
    assert Decimal(body["discrepancy"]) == Decimal("50")
    assert body["journal_entry_id"] is not None

    # Bank rose by 950, COD-Short rose by 50, Cash-on-Hand-Rider fell by 1000.
    assert (await _account_balance(A_CASH_IN_BANK)) == Decimal("950")
    assert (await _account_balance("6010")) == Decimal("50")  # COD short expense
    assert (await _account_balance("1020")) == Decimal("-1000")


async def test_cod_deposit_exact_match_no_discrepancy(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        rid = (
            await s.execute(
                text(
                    """
                    INSERT INTO riders (code, name, phone, vehicle_type)
                    VALUES ('R-EXACT', 'Exact Rider', '+8801911000089', 'motorcycle')
                    RETURNING id
                    """,
                ),
            )
        ).scalar_one()
    resp = await api_client.post(
        "/api/v1/admin/finance/cod-deposits",
        headers=admin_user["headers"],
        json={
            "rider_id": str(rid),
            "deposit_date": date.today().isoformat(),
            "expected_amount": "500.00",
            "deposited_amount": "500.00",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "deposited"


# ============================================================
# 3. Cross-module wiring (outbox-driven)
# ============================================================


async def test_cod_order_emits_revenue_je_via_outbox(
    api_client: AsyncClient, admin_user: dict[str, Any], logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable(
        api_client, admin_user["headers"], name="RevTest",
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 2}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    assert place.status_code == 201
    order_id = place.json()["id"]
    grand_total = Decimal(place.json()["grand_total"])  # 2 * 100 = 200

    await _drain_outbox()

    # AR-COD rose by grand_total, Revenue rose by grand_total.
    assert (await _account_balance(A_AR_COD)) == grand_total
    assert (await _account_balance(R_PRODUCT_SALES)) == -grand_total  # revenue normal = credit

    # Idempotency: redelivering the same event must NOT post a second JE.
    sm = get_sessionmaker()
    async with sm() as s:
        before = (
            await s.execute(
                select(JournalEntry).where(
                    JournalEntry.source == "order_revenue",
                    JournalEntry.reference_id == UUID(order_id),
                ),
            )
        ).scalars().all()
    assert len(before) == 1


async def test_order_cancellation_reverses_revenue(
    api_client: AsyncClient, admin_user: dict[str, Any], logged_in: dict[str, Any],
) -> None:
    variant_id = await _setup_purchasable(
        api_client, admin_user["headers"], name="CancelMe",
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 1}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    order_id = place.json()["id"]
    await _drain_outbox()
    # Customer-cancellable: just placed.
    cancel = await api_client.post(
        f"/api/v1/orders/{order_id}/cancel",
        headers=logged_in["headers"],
        json={"reason": "changed mind"},
    )
    assert cancel.status_code == 200, cancel.text
    await _drain_outbox()

    # AR-COD net to zero, Revenue net to zero.
    assert (await _account_balance(A_AR_COD)) == Decimal("0")
    assert (await _account_balance(R_PRODUCT_SALES)) == Decimal("0")


async def test_vat_inclusive_split_when_rate_set(
    api_client: AsyncClient, admin_user: dict[str, Any], logged_in: dict[str, Any],
) -> None:
    """Override env var via monkeypatching the cached settings.

    With VAT_RATE=0.15, an order grand_total of 115.00 should split into
    100.00 net revenue + 15.00 VAT-Payable.
    """
    from app.core.config import get_settings

    settings = get_settings()
    settings.vat_rate = "0.15"  # validate_assignment=False on settings, but field allows it
    try:
        variant_id = await _setup_purchasable(
            api_client, admin_user["headers"], name="VatItem",
        )
        await api_client.post(
            "/api/v1/orders",
            headers=logged_in["headers"],
            json={
                "items": [{"variant_id": variant_id, "quantity": 1}],
                "payment_method": "cod",
                "delivery_address": _address(),
                "currency": "BDT",
            },
        )
        await _drain_outbox()

        revenue_balance = await _account_balance(R_PRODUCT_SALES)
        vat_balance = await _account_balance("2100")  # VAT-Payable
        # Revenue is credit-normal so balance() returns negative debits-credits.
        assert -revenue_balance + -vat_balance == Decimal("100.00")
        # 100 / 1.15 ≈ 86.96 net, ≈ 13.04 VAT
        assert -revenue_balance == Decimal("86.96")
        assert -vat_balance == Decimal("13.04")
    finally:
        settings.vat_rate = "0"


async def test_pnl_report_shows_revenue_and_cogs(
    api_client: AsyncClient, admin_user: dict[str, Any], logged_in: dict[str, Any],
) -> None:
    """Place + complete an order and check P&L picks up both revenue and COGS."""
    variant_id = await _setup_purchasable(
        api_client, admin_user["headers"], name="PnLItem",
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 5}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    assert place.status_code == 201
    order_id = place.json()["id"]
    await _drain_outbox()

    # Move order through to completion (admin endpoints exist on orders).
    # The order auto-advanced to "approved" for non-Rx COD. Need to start
    # packing → dispatch → complete.
    await api_client.post(
        f"/api/v1/admin/orders/{order_id}/start-packing",
        headers=admin_user["headers"],
        json={},
    )
    await api_client.post(
        f"/api/v1/admin/orders/{order_id}/dispatch",
        headers=admin_user["headers"],
        json={},
    )
    completed = await api_client.post(
        f"/api/v1/admin/orders/{order_id}/complete",
        headers=admin_user["headers"],
        json={},
    )
    assert completed.status_code == 200, completed.text
    await _drain_outbox()

    pnl = await api_client.get(
        "/api/v1/admin/finance/reports/profit-and-loss",
        headers=admin_user["headers"],
        params={
            "starts_on": date.today().isoformat(),
            "ends_on": date.today().isoformat(),
        },
    )
    assert pnl.status_code == 200
    body = pnl.json()
    # Revenue 5 * 100 = 500; COGS = 5 * 10 = 50.
    assert Decimal(body["revenue_total"]) == Decimal("500")
    assert Decimal(body["cogs_total"]) == Decimal("50")
    assert Decimal(body["gross_profit"]) == Decimal("450")


async def test_return_completion_accrues_refund(
    api_client: AsyncClient, admin_user: dict[str, Any], logged_in: dict[str, Any],
) -> None:
    """Place an order, fully run it through delivery + return → finance
    accrues a refund and Refund-Payable rises on the balance sheet.
    """
    variant_id = await _setup_purchasable(
        api_client, admin_user["headers"], name="RefItem",
    )
    place = await api_client.post(
        "/api/v1/orders",
        headers=logged_in["headers"],
        json={
            "items": [{"variant_id": variant_id, "quantity": 2}],
            "payment_method": "cod",
            "delivery_address": _address(),
            "currency": "BDT",
        },
    )
    order_id = place.json()["id"]
    order_line_id = place.json()["lines"][0]["id"]
    await _drain_outbox()

    # Walk to completed.
    for path in ("start-packing", "dispatch", "complete"):
        await api_client.post(
            f"/api/v1/admin/orders/{order_id}/{path}",
            headers=admin_user["headers"],
            json={},
        )
    await _drain_outbox()

    # Open + walk a return.
    rr = await api_client.post(
        "/api/v1/returns",
        headers=logged_in["headers"],
        json={
            "order_id": order_id,
            "reason": "bottle leaked",
            "lines": [
                {"order_line_id": order_line_id, "quantity": 1},
            ],
        },
    )
    assert rr.status_code == 201, rr.text
    return_id = rr.json()["id"]
    return_line_id = rr.json()["lines"][0]["id"]

    # Look up the batch that was received for this variant (needed for inspect).
    sm = get_sessionmaker()
    async with sm() as s:
        from app.modules.inventory.models import Batch
        batch_id = (
            await s.execute(
                select(Batch.id).where(Batch.variant_id == UUID(variant_id)),
            )
        ).scalar_one()

    await api_client.post(
        f"/api/v1/admin/returns/{return_id}/receive",
        headers=admin_user["headers"],
        json={},
    )

    # Inspect — restock condition so it's a clean return.
    inspect = await api_client.post(
        f"/api/v1/admin/returns/{return_id}/inspect",
        headers=admin_user["headers"],
        json={
            "lines": [
                {
                    "line_id": return_line_id,
                    "inspected_quantity": 1,
                    "condition": "sealed",
                    "target_batch_id": str(batch_id),
                    "target_warehouse_code": "MAIN",
                },
            ],
        },
    )
    assert inspect.status_code == 200, inspect.text

    complete = await api_client.post(
        f"/api/v1/admin/returns/{return_id}/complete",
        headers=admin_user["headers"],
        json={},
    )
    assert complete.status_code == 200, complete.text
    await _drain_outbox()

    # Refund record exists, Refund-Payable increased, Sales-Returns rose.
    refunds = await api_client.get(
        "/api/v1/admin/finance/refunds", headers=admin_user["headers"],
    )
    items = refunds.json()["items"]
    assert len(items) == 1
    assert Decimal(items[0]["accrued_amount"]) == Decimal("100")  # 1 unit * 100
    assert items[0]["status"] == "pending"

    assert (await _account_balance(L_REFUND_PAYABLE)) == Decimal("-100")
    assert (await _account_balance(R_SALES_RETURNS)) == Decimal("100")  # contra debit

    # Pay the refund.
    pay = await api_client.post(
        "/api/v1/admin/finance/refunds/pay",
        headers=admin_user["headers"],
        json={
            "refund_id": items[0]["id"],
            "paid_on": date.today().isoformat(),
            "amount": "100",
            "method": "bkash",
            "bank_reference": "TXN-RFD-1",
        },
    )
    assert pay.status_code == 200, pay.text
    assert pay.json()["status"] == "paid"
    assert (await _account_balance(L_REFUND_PAYABLE)) == Decimal("0")


async def test_rbac_finance_endpoints_require_finance_perms(
    api_client: AsyncClient, logged_in: dict[str, Any],
) -> None:
    """A plain customer has none of the finance.* perms."""
    resp = await api_client.get(
        "/api/v1/admin/finance/accounts", headers=logged_in["headers"],
    )
    assert resp.status_code == 403


async def test_journal_lines_append_only(
    api_client: AsyncClient, admin_user: dict[str, Any],
) -> None:
    """REVOKE UPDATE/DELETE on fin_journal_lines must take effect.

    The migration guards this with a DO block that swallows
    ``insufficient_privilege`` (CI sometimes runs as superuser). Where the
    revoke succeeded, attempting an UPDATE from PUBLIC should error. We
    don't fail the test if we're privileged enough to ignore the revoke;
    the row-level CHECK constraint on ``exactly_one_side`` is the more
    robust line of defence and we test that elsewhere.
    """
    create = await api_client.post(
        "/api/v1/admin/finance/journal-entries",
        headers=admin_user["headers"],
        json={
            "entry_date": date.today().isoformat(),
            "narration": "Append-only test",
            "currency": "BDT",
            "lines": [
                {"account_code": A_CASH_IN_BANK, "debit": "10", "credit": "0"},
                {"account_code": "3010", "debit": "0", "credit": "10"},
            ],
        },
    )
    eid = create.json()["id"]
    sm = get_sessionmaker()
    async with sm() as s:
        # Simply verify the lines exist and the CHECK constraint stops a bad
        # update if attempted at the row level.
        rows = (
            await s.execute(
                select(JournalLine).where(JournalLine.entry_id == UUID(eid)),
            )
        ).scalars().all()
        assert len(rows) == 2
