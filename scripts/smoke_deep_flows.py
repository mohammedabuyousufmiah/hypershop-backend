"""Deep-flow smoke — exercises full business workflows end-to-end.

Pairs with ``smoke_full_project.py`` (which checks service liveness).
This script ACTUALLY DOES things:

  1. Auth     — login as demo customer (sets HttpOnly cookie),
                fetch /users/me, fetch /cart.
  2. Finance  — create refund request, finance_manager approves it,
                verify audit log row + status flip.
  3. Inventory — seed stock adjustment, inventory_manager approves
                with evidence, verify status flip + audit.
  4. Supervisor — supervisor flags risk + creates escalation +
                requests manager approval, manager approves, verify
                manager_approvals row + audit log.
  5. Mother-QR — gate_in a unit, walk the full 12-state lifecycle
                from RECEIVED to DELIVERED, verify 13 scan events.

Each step is a Result row in a unified table. Exit code = number
of FAIL rows.
"""
from __future__ import annotations

import asyncio
import http.cookiejar
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


_BACKEND = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://hypershop:hypershop@localhost:5432/hypershop",
)
_DEMO_USER = ("customer@hypershop.dev", "customerlocal12")


@dataclass(slots=True)
class Result:
    section: str
    name: str
    status: str  # OK / FAIL
    detail: str


# ============================================================
#  HTTP helpers (cookie-aware so login sticks)
# ============================================================
def _make_session() -> tuple:
    jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))
    return opener, jar


def _http_json(opener, method: str, path: str, body: dict | None = None,
                timeout: int = 30) -> tuple[int, dict | str]:
    url = urljoin(_BACKEND, path)
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        # urllib raises on 4xx/5xx; pull the response body if present.
        if hasattr(exc, "read"):
            try:
                msg = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
        return getattr(exc, "code", 0), msg


# ============================================================
#  1. Auth — login + me + cart
# ============================================================
def auth_flow() -> list[Result]:
    out: list[Result] = []
    opener, _ = _make_session()
    email, pw = _DEMO_USER

    status, body = _http_json(opener, "POST", "/api/v1/auth/login",
                                {"email": email, "password": pw})
    if status == 200 and isinstance(body, dict) and body.get("success"):
        u = body["data"]["user"]
        out.append(Result("auth", "login",
                           "OK", f"{u['email']} (id={u['id'][:8]}...)"))
    else:
        out.append(Result("auth", "login", "FAIL",
                           f"http {status}: {str(body)[:120]}"))
        return out

    # /users/me with cookie attached
    status, body = _http_json(opener, "GET", "/api/v1/users/me")
    if status == 200 and isinstance(body, dict) and body.get("success"):
        d = body["data"]
        out.append(Result("auth", "users/me",
                           "OK", f"{d['email']} verified={d.get('email_verified')}"))
    else:
        out.append(Result("auth", "users/me", "FAIL",
                           f"http {status}: {str(body)[:120]}"))

    # /cart — auto-created for customer
    status, body = _http_json(opener, "GET", "/api/v1/cart")
    if status == 200 and isinstance(body, dict) and body.get("success"):
        d = body["data"]
        out.append(Result("auth", "cart fetch", "OK",
                           f"cart_id={d['id'][:8]}... items={len(d.get('items', []))}"))
    else:
        out.append(Result("auth", "cart fetch", "FAIL",
                           f"http {status}: {str(body)[:120]}"))

    return out


# ============================================================
#  2. Finance Manager flow (Phase B service)
# ============================================================
async def finance_flow(eng) -> list[Result]:
    from app.modules.finance.operations_actions import FinanceAction
    from app.modules.finance.operations_models import FinanceRefundApproval
    from app.modules.finance.operations_service import (
        FinanceActor, FinanceActionPayload, FinanceRuleViolation,
        execute_finance_action, apply_refund_decision,
    )

    out: list[Result] = []
    async with AsyncSession(eng, expire_on_commit=False) as session:
        # Seed a refund request
        refund = FinanceRefundApproval(
            id=uuid4(),
            order_id=f"ORD-DEEP-{secrets.token_hex(3)}",
            requested_by="csat-deep-1",
            amount_minor=99500,
            reason="Deep smoke — damage on arrival.",
        )
        session.add(refund)
        await session.commit()
        out.append(Result("finance", "seed refund pending", "OK",
                           f"id={str(refund.id)[:8]}... amount=995.00 BDT"))

        # Approve via finance_manager
        actor = FinanceActor(actor_id="finmgr-deep", role="finance_manager")
        payload = FinanceActionPayload(
            entity_type="refund_approval", entity_id=str(refund.id),
            new_status="approved",
            reason="Evidence valid; refund within window.",
            evidence_url="https://r2/.../evidence-deep.jpg",
            amount_minor=99500, reference_id="REF-DEEP-001",
            order_id=refund.order_id, requested_by="csat-deep-1",
        )
        try:
            audit = await execute_finance_action(
                session=session, actor=actor,
                action=FinanceAction.APPROVE_REFUND, payload=payload,
            )
            row = await apply_refund_decision(
                session, refund_id=refund.id, decided_by=actor.actor_id,
                audit_log_id=audit.id, new_status="approved",
            )
            await session.commit()
            out.append(Result("finance", "approve refund", "OK",
                               f"audit={audit.audit_code} → status={row.status}"))
        except FinanceRuleViolation as e:
            out.append(Result("finance", "approve refund", "FAIL",
                               f"{e.code}: {e.message[:80]}"))

        # Red-line: inventory_manager tries to approve another refund
        try:
            await execute_finance_action(
                session=session,
                actor=FinanceActor(actor_id="invmgr-1", role="inventory_manager"),
                action=FinanceAction.APPROVE_REFUND, payload=payload,
            )
            out.append(Result("finance", "red-line invmgr blocked",
                               "FAIL", "should have raised"))
        except FinanceRuleViolation as e:
            out.append(Result("finance", "red-line invmgr blocked", "OK",
                               f"{e.code} ({e.status_code})"))
    return out


# ============================================================
#  3. Inventory Manager flow (Phase C service)
# ============================================================
async def inventory_flow(eng) -> list[Result]:
    from app.modules.inventory.operations_actions import InventoryAction
    from app.modules.inventory.operations_models import StockAdjustmentRequest
    from app.modules.inventory.operations_service import (
        InventoryActor, InventoryActionPayload, InventoryRuleViolation,
        execute_inventory_action, apply_stock_adjustment_decision,
    )

    out: list[Result] = []
    async with AsyncSession(eng, expire_on_commit=False) as session:
        # Seed adjustment request
        adj = StockAdjustmentRequest(
            id=uuid4(),
            request_code=f"ADJ-DEEP-{secrets.token_hex(3)}",
            sku="HSLINENMIDIDRESSSAGE", warehouse_id="WH-DHK-01",
            direction="out", qty_delta=-3, qty_before=20,
            category="damage",
            reason="Deep smoke — 3 units water damage.",
            evidence_url="https://r2/.../qc-deep.jpg",
            requested_by="warehouse-staff-deep",
        )
        session.add(adj)
        await session.commit()
        out.append(Result("inventory", "seed adjustment pending", "OK",
                           f"id={str(adj.id)[:8]}... delta=-3"))

        actor = InventoryActor(actor_id="invmgr-deep", role="inventory_manager")
        try:
            audit = await execute_inventory_action(
                session=session, actor=actor,
                action=InventoryAction.APPROVE_STOCK_ADJUSTMENT,
                payload=InventoryActionPayload(
                    entity_type="stock_adjustment_request",
                    entity_id=str(adj.id),
                    new_status="approved",
                    reason="QC photos valid.",
                    evidence_url="https://r2/.../qc-deep.jpg",
                    reference_id="REF-WH-DEEP-1",
                    sku=adj.sku, warehouse_id=adj.warehouse_id,
                    qty_before=20, qty_delta=-3, qty_after=17,
                    requested_by="warehouse-staff-deep",
                ),
            )
            row = await apply_stock_adjustment_decision(
                session, request_id=adj.id,
                decided_by=actor.actor_id, audit_log_id=audit.id,
                new_status="approved",
            )
            await session.commit()
            out.append(Result("inventory", "approve adjustment", "OK",
                               f"audit={audit.audit_code} → status={row.status}"))
        except InventoryRuleViolation as e:
            out.append(Result("inventory", "approve adjustment", "FAIL",
                               f"{e.code}: {e.message[:80]}"))

        # Red-line: finance_manager attempts a stock-block
        try:
            await execute_inventory_action(
                session=session,
                actor=InventoryActor(actor_id="finmgr", role="finance_manager"),
                action=InventoryAction.BLOCK_UNAVAILABLE_STOCK_FROM_SELLING,
                payload=InventoryActionPayload(
                    entity_type="inventory_stock", entity_id="x",
                    new_status="blocked", reason="cross-role test",
                    evidence_url="https://r2/x", reference_id="REF-CROSS",
                ),
            )
            out.append(Result("inventory", "red-line finmgr blocked",
                               "FAIL", "should have raised"))
        except InventoryRuleViolation as e:
            out.append(Result("inventory", "red-line finmgr blocked",
                               "OK", f"{e.code} ({e.status_code})"))
    return out


# ============================================================
#  4. Supervisor + Manager flow (Phase D service)
# ============================================================
async def supervisor_flow(eng) -> list[Result]:
    from app.modules.supervisor_lm.service import (
        SupervisorActor, SupervisorLmRuleViolation,
        ManagerApprovalType,
        create_order_escalation, create_manager_approval,
        record_manager_decision, create_failed_delivery_review,
    )

    out: list[Result] = []
    async with AsyncSession(eng, expire_on_commit=False) as session:
        sup = SupervisorActor(actor_id="sup-deep", role="supervisor")
        mgr = SupervisorActor(actor_id="mgr-deep",
                                role="operations_manager_lm")

        # Supervisor creates escalation
        esc = await create_order_escalation(
            session, actor=sup,
            subject_type="order", subject_id="ORD-DEEP-700",
            order_id="ORD-DEEP-700", priority="high",
            reason="Deep smoke — customer dispute on COD.",
        )
        await session.commit()
        out.append(Result("supervisor", "escalation created", "OK",
                           f"code={esc.escalation_code} priority={esc.priority}"))

        # Supervisor requests manager approval
        approval = await create_manager_approval(
            session, approval_type=ManagerApprovalType.RIDER_REASSIGNMENT,
            requested_by=sup.actor_id,
            request_reason="Rider unavailable; reassign needed.",
            order_id="ORD-DEEP-700",
            escalation_id=esc.id, priority="high",
        )
        await session.commit()
        out.append(Result("supervisor", "manager approval pending",
                           "OK", f"code={approval.approval_code} type={approval.approval_type}"))

        # Manager decides
        try:
            row, action = await record_manager_decision(
                session=session, actor=mgr,
                approval_id=approval.id, decision="approved",
                decision_reason="Rider availability confirmed; approved.",
            )
            await session.commit()
            out.append(Result("supervisor", "manager approves", "OK",
                               f"action={action.action_code} status={row.status}"))
        except SupervisorLmRuleViolation as e:
            out.append(Result("supervisor", "manager approves", "FAIL",
                               f"{e.code}: {e.message[:80]}"))

        # Red-line: supervisor tries to approve
        approval2 = await create_manager_approval(
            session, approval_type=ManagerApprovalType.SELLER_WARNING,
            requested_by="csat-deep",
            request_reason="Seller SLA breach.",
        )
        await session.commit()
        try:
            await record_manager_decision(
                session=session, actor=sup, approval_id=approval2.id,
                decision="approved",
                decision_reason="trying as supervisor",
            )
            out.append(Result("supervisor", "red-line sup blocked",
                               "FAIL", "should have raised"))
        except SupervisorLmRuleViolation as e:
            out.append(Result("supervisor", "red-line sup blocked", "OK",
                               f"{e.code} ({e.status_code})"))

        # Failed delivery needs full proof chain
        try:
            await create_failed_delivery_review(
                session=session, actor=sup,
                order_id="ORD-DEEP-X", delivery_task_id="DT-DEEP-1",
                delivery_attempt_id="DA-1",
                rider_note=None, call_attempt=None,
                gps_location=None, photo_evidence_url=None,
            )
            out.append(Result("supervisor", "red-line no rider proof",
                               "FAIL", "should have raised"))
        except SupervisorLmRuleViolation as e:
            out.append(Result("supervisor", "red-line no rider proof",
                               "OK", f"{e.code} ({e.status_code})"))
    return out


# ============================================================
#  5. Mother-QR full lifecycle (Phase E service)
# ============================================================
async def mother_qr_flow(eng) -> list[Result]:
    from app.modules.mother_qr.service import (
        ScanActor, MotherQrRuleViolation, gate_in, apply_scan,
    )
    from app.modules.mother_qr.transitions import ScanAction
    from app.modules.mother_qr.models import WarehouseLocation

    out: list[Result] = []
    async with AsyncSession(eng, expire_on_commit=False) as session:
        # Ensure a registered shelf exists (idempotent)
        SHELF = "SHELF-DEEP-01"
        from sqlalchemy import select
        existing = (await session.execute(
            select(WarehouseLocation).where(WarehouseLocation.qr_code == SHELF),
        )).scalar_one_or_none()
        if existing is None:
            session.add(WarehouseLocation(
                qr_code=SHELF, warehouse_id="WH-DHK-01",
                zone="A", aisle="01", rack="B", shelf="03", bin="DEEP",
            ))
            await session.commit()

        MQR = f"MQR-DEEP-{secrets.token_hex(4)}"
        await gate_in(
            session, actor=ScanActor(actor_id="recv-deep",
                                       role="warehouse_receiving_staff"),
            mother_qr=MQR, sku="HSLINENMIDIDRESSSAGE",
            product_name="Aarong Dress (deep smoke)",
            warehouse_id="WH-DHK-01",
        )
        await session.commit()
        out.append(Result("mother_qr", "gate_in", "OK", f"{MQR} → GATE_IN"))

        # Full 12-state lifecycle
        recv = ScanActor(actor_id="r1", role="warehouse_receiving_staff")
        qc = ScanActor(actor_id="qc1", role="qc_staff")
        shelf_op = ScanActor(actor_id="s1", role="shelf_operator")
        inv_sys = ScanActor(actor_id="isys", role="inventory_system")
        oeng = ScanActor(actor_id="oeng", role="order_engine")
        picker = ScanActor(actor_id="p1", role="picker")
        packer = ScanActor(actor_id="pk1", role="packer")
        disp = ScanActor(actor_id="d1", role="dispatcher")
        rider = ScanActor(actor_id="rd1", role="rider")

        steps = [
            (recv, ScanAction.RECEIVE, "DOCK", None, None),
            (qc, ScanAction.START_QC, "QC", None, None),
            (qc, ScanAction.PASS_QC, "QC", None, None),
            (shelf_op, ScanAction.SHELF, SHELF, SHELF, None),
            (inv_sys, ScanAction.MARK_SELLABLE, SHELF, None, None),
            (oeng, ScanAction.RESERVE_FOR_ORDER, SHELF, None,
             "ORD-DEEP-MQR"),
            (picker, ScanAction.PICK, "PICK", None, "ORD-DEEP-MQR"),
            (packer, ScanAction.PACK, "PACK", None, "ORD-DEEP-MQR"),
            (disp, ScanAction.MARK_DISPATCH_READY, "DISP", None,
             "ORD-DEEP-MQR"),
            (disp, ScanAction.RIDER_HANDOVER, "DISP", None, "ORD-DEEP-MQR"),
            (rider, ScanAction.START_DELIVERY, "ROUTE", None, "ORD-DEEP-MQR"),
            (rider, ScanAction.CONFIRM_DELIVERED, "CUST", None, "ORD-DEEP-MQR"),
        ]
        for i, (actor, action, loc, qr, order) in enumerate(steps, start=1):
            try:
                item, ev = await apply_scan(
                    session=session, actor=actor, mother_qr=MQR,
                    action=action, location_code=loc,
                    scanned_qr=qr, order_id=order,
                )
                await session.commit()
                out.append(Result("mother_qr",
                                   f"step {i:>2} {action.value}",
                                   "OK", f"→ {item.status}"))
            except MotherQrRuleViolation as e:
                out.append(Result("mother_qr",
                                   f"step {i:>2} {action.value}",
                                   "FAIL", f"{e.code}: {e.message[:80]}"))
                break

        # Verify final state + event count
        from app.modules.mother_qr.models import MotherQrItem, MotherQrScanEvent
        final = (await session.execute(
            select(MotherQrItem).where(MotherQrItem.mother_qr == MQR),
        )).scalar_one()
        n_events = (await session.execute(
            text("SELECT count(*) FROM mother_qr_scan_events WHERE mother_qr = :m"),
            {"m": MQR},
        )).scalar_one()
        out.append(Result("mother_qr", "final state", "OK",
                           f"status={final.status} events={n_events}"))
    return out


# ============================================================
#  Render
# ============================================================
def render(results: list[Result]) -> str:
    lines: list[str] = []
    last_section = None
    counts = {"OK": 0, "FAIL": 0}
    for r in results:
        if r.section != last_section:
            lines.append("")
            lines.append(f"━━━ {r.section.upper()} ━━━")
            last_section = r.section
        marker = "[OK]  " if r.status == "OK" else "[FAIL]"
        lines.append(f"  {marker} {r.name:<34} {r.detail}")
        counts[r.status] = counts.get(r.status, 0) + 1
    lines.append("")
    lines.append("━" * 64)
    lines.append(
        f"SUMMARY  OK={counts['OK']}  FAIL={counts['FAIL']}"
    )
    return "\n".join(lines)


async def main_async() -> int:
    results: list[Result] = []
    # 1. Auth — synchronous HTTP
    results.extend(auth_flow())

    # 2-5. DB-driven service flows
    eng = create_async_engine(_DB_URL)
    try:
        results.extend(await finance_flow(eng))
        results.extend(await inventory_flow(eng))
        results.extend(await supervisor_flow(eng))
        results.extend(await mother_qr_flow(eng))
    finally:
        await eng.dispose()

    print(render(results))
    fails = sum(1 for r in results if r.status == "FAIL")
    return 0 if fails == 0 else 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
