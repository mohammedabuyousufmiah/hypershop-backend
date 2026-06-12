# Hypershop Authority Matrix

Source: merged from 4 role-rule packages (2026-05-26):
- hypershop-finance-manager-rules.zip
- hypershop-inventory-manager-rules.zip
- hypershop-warehouse-mother-qr-updated.zip
- hypershop-supervisor-last-mile-manager-rules.zip

Wired into V7-canonical backend at `_serve_final/hypershop-backend`.

## Core principles (non-negotiable)

1. **Money truth = Finance Manager.** Stock truth = Inventory Manager.
   Delivery/order exception = Operations Manager. Monitor + verify +
   escalate = Supervisor. System authority = Admin / Super Admin.
2. **Payment success must come from a verified payment gateway webhook.**
   No human role — including Admin / Super Admin — can `MARK_PAYMENT_SUCCESS`
   or `MANUALLY_CONFIRM_PAID_ORDER`.
3. **Audit logs are immutable.** No role (including Super Admin) can
   `DELETE_AUDIT_LOG`. Corrections happen via reversal entries.
4. **No QR scan = no stock movement.** Mother-QR + Shelf-QR + Parcel-QR
   scans are the only authority for warehouse state transitions.
5. **AI = assistive only.** AI cannot approve QC, handover, delivery,
   exception, refund, or payment. Final decision is always a human.
6. **No self-approval.** Requester ≠ approver for refunds, payouts, stock
   adjustments, exception decisions, dispute outcomes.

## Role authority summary

### Finance Manager — Money Truth

**Can do:** reconcile gateway / COD / wallet, approve/reject refund, settle
COD, approve/hold/release seller + rider payouts, approve wallet
adjustment, freeze payout, escalate suspicious transaction, submit
daily closing report, export audit.

**Must verify:** every action requires (actor, role, action, entity,
old/new state, reason, evidence URL, amount, ref ID, IP, device,
timestamp). High-value refund + bank/MFS change → escalate to Admin.

**Cannot do (red line):**
- `MARK_PAYMENT_SUCCESS`, `MANUALLY_CONFIRM_PAID_ORDER`, `BYPASS_PAYMENT_WEBHOOK`
- `CHANGE_STOCK_STATUS`, return-to-stock decisions
- `APPROVE_DELIVERY_EXCEPTION`, `RIDER_REASSIGNMENT`
- `CHANGE_PRODUCT_PRICE`, `CHANGE_COMMISSION`, `CHANGE_SYSTEM_SETTINGS`
- `DELETE_ORDER`, `DELETE_COMPLAINT`, `DELETE_PAYMENT_RECORD`,
  `DELETE_COD_SETTLEMENT_RECORD`, `DELETE_WALLET_LEDGER`, `DELETE_AUDIT_LOG`
- Approve own refund / own money request; ban seller; edit seller bank
  without verification; reduce COD silently; AI final decision

**Escalates to:** Admin / Super Admin (commission, system settings, seller
bans, suspicious transactions, high-value refunds, edit COD/wallet balance).

### Inventory Manager — Stock Truth

**Can do:** approve/reject stock adjustment, mark damaged/lost (with
evidence), approve return-to-stock after QC, block unavailable stock,
set low-stock alerts, approve warehouse exceptions (quarantine,
manual `AVAILABLE` increase, pick override), monitor seller stock
accuracy, request seller stock audit.

**Must verify:** stock adjustment needs reason + before/after + evidence
+ Inventory Manager approval (high-value → Admin). Return-to-stock needs
QC pass + Shelf-QR + matching Mother-QR scan. Damaged/lost needs evidence
+ responsible party + order/stock ref. Cannot self-approve own adjustment.

**Cannot do (red line):**
- All money actions (refund, payout, COD settle, wallet edit, payment status)
- Approve delivery exception / reassign rider
- Change commission / product price / system settings
- Delete stock ledger, movement history, order history, audit log
- Return damaged product to sellable stock
- Reserve fake-order / damaged / expired / blocked / double-reserved stock
- AI final decision on inventory

**Escalates to:** Admin / Super Admin (price edits, seller penalties,
high-value adjustments); Finance Manager (money impact of damaged / lost /
returns).

### Operations Manager / Last-Mile Manager — Delivery & Order Exception

**Can do:** view + decide operational exception requests, decide
supervisor escalations + failed-delivery disputes, manual hold release,
seller/rider review, complaint priority review, return handed/out-for-
delivery parcel ONLY when dispute evidence documented.

**Must verify:** exception approval needs requester role + entity + old/
requested status + reason + evidence URL + SLA deadline + manager note.
Cannot self-approve. Disputed return needs explicit dispute marker
(delivery_disputed, customer_dispute_raised, rider_dispute_raised, or
handover dispute ID). Failed-delivery dispute decision needs prior
supervisor + rider proof chain.

**Cannot do (red line):**
- All money actions (refund, payout, COD settle, COD adjust)
- All wallet actions
- All system-only actions (mark payment success, confirm paid order,
  edit payment status)
- All system-setting actions (commission, system settings, security rules)
- `CHANGE_PRODUCT_PRICE`
- History-destructive: `DELETE_ORDER`, `DELETE_COMPLAINT`, `DELETE_AUDIT_LOG`,
  `HIDE_COMPLAINT`
- Start `OUT_FOR_DELIVERY` or confirm final `DELIVERED` (Rider only)
- Change stock truth

**Escalates to:** Finance Manager (money impact of disputed deliveries /
COD); Admin / Super Admin (system config, commission, security rule
changes).

### Supervisor — Monitor + Verify + Escalate

**Can do:** view exception queues, add delay reasons, create escalations,
review failed deliveries, flag seller/rider/customer operational risk,
view COD risk (flag only), submit shift reports. Fulfillment Supervisor
additionally: supervise outbound fulfillment, perform dispatch-ready /
rider-handover exception scans, return handed parcel with reason+evidence,
review failed delivery and pick reschedule / return-to-hub / suspicious /
manager_review.

**Must verify:** failed-delivery review needs decision + supervisor note +
reason + evidence for return-to-hub / suspicious / manager_review. Cannot
accept failed delivery without prior rider proof (call attempt, GPS,
timestamp, rider note). Every escalation captures reason + evidence + ref ID.

**Cannot do (red line):** everything Operations Manager cannot, PLUS:
- `APPROVE_OPERATIONAL_EXCEPTION` / `REJECT_OPERATIONAL_EXCEPTION` (manager-only)
- `APPROVE_MANAGER_APPROVAL` / `APPROVE_SUPERVISOR_ESCALATION` (manager-only)
- `APPROVE_ORDER_HOLD` / `RELEASE_MANUAL_HOLD`
- `APPROVE_FAILED_DELIVERY_DISPUTE_DECISION` / `REVIEW_SHIFT_REPORT`
- Approve refund / release payout / verify COD settlement (flag only)
- Confirm final delivery; start `OUT_FOR_DELIVERY`

**Escalates to:** Operations Manager (exceptions, disputes, shift reports,
manual holds); Finance Manager (COD risk, refund cases); Admin (system
issues).

### Warehouse Operator — Mother-QR Lifecycle

Sub-roles: Receiving Staff, Warehouse Staff, QC Staff, Picker, Packing
Staff, Packing Supervisor, Dispatcher, Rider, Return Team, Inventory
Supervisor, Fulfillment Supervisor.

**Lifecycle (canonical):**
```
RECEIVED → QC_PENDING → QC_PASSED → SHELF_ASSIGNED → AVAILABLE →
RESERVED → PICKED → PACKED → DISPATCH_READY → HANDED_TO_RIDER →
OUT_FOR_DELIVERY → DELIVERED
```

**Exception / return catalog:**
```
QC_FAILED → HOLD → DAMAGED → LOST → RETURN_REQUESTED → RETURN_RECEIVED
→ RETURN_QC_PENDING → RETURNED_TO_STOCK → DISPOSED
```

**Every transition requires:**
- Mother QR scan + standard scan log (QR, type, actor, role, order,
  location, device, result, timestamp)
- Role-specific gate (e.g. only QC Staff can run QC, only Rider can
  trigger `OUT_FOR_DELIVERY`)
- Evidence for failure states (reason + photo for QC_FAILED, DAMAGED,
  WRONG_ITEM_RECEIVED, etc.)

**Cannot do (red line):**
- No QR scan = no movement; no shelf scan = no location; no parcel scan
  = no dispatch; no delivery proof = no closure
- Manually reserve fake-order / damaged / expired / blocked / double-
  reserved stock
- Picker override wrong item (requires Inventory Manager)
- Parcel QR generation without Mother QR verification
- Dispatch without zone + assigned rider
- Rider denial after accepted scan (unless immediate dispute)
- Fulfillment Supervisor + Operations Manager cannot confirm final
  delivery (Rider only)
- Operations Manager cannot start `OUT_FOR_DELIVERY`
- Damaged → sellable stock
- Return Team decides disposition; Finance receives physical returns;
  Inventory makes finance decisions; Finance makes stock disposition
- Scan log is append-only — corrections via reversal events only

**Escalates to:** Inventory Manager (overrides, quarantine, return-to-
stock disposition, manual stock increase); Fulfillment Supervisor
(dispatch / handover / failed-delivery exceptions); Operations Manager
(disputed last-mile, fake-attempt suspicion); Finance Manager (return
money impact, COD).

### Admin / Super Admin — System Authority

**Can do:** **Super Admin** holds wildcard `*`; **Admin** holds full RBAC,
seller application review, product review, order manage, all supervisor
operations + shift report review, supervisor rules manage, report export,
support ticket manage, full SEO suite. Both: final authority on
commission, product price, system settings, security rules. Approve
high-value adjustments, seller bans, suspicious transaction resolution.
Edit COD amount / wallet balance (Super Admin only, via adjustment
workflow).

**Must verify:** even Admin / Super Admin actions go through audit-first
execution (actor, role, action, entity, old/new state, reason, evidence
URL, amount, ref ID, IP, device, timestamp). Sensitive money actions
remain Finance-governed for execution even when Admin authorizes.

**Cannot do (red line):**
- **Admin:** `MARK_PAYMENT_SUCCESS`, `CONFIRM_ORDER_MANUALLY`,
  `MANUALLY_CONFIRM_PAID_ORDER`, `EDIT_PAYMENT_STATUS`, all
  `WALLET_ACTIONS` (`EDIT_WALLET`, `EDIT_CUSTOMER_WALLET`,
  `EDIT_RIDER_WALLET`, `EDIT_SELLER_BALANCE`), `DELETE_AUDIT_LOG`
- **Super Admin:** `MARK_PAYMENT_SUCCESS`, `CONFIRM_ORDER_MANUALLY`,
  `MANUALLY_CONFIRM_PAID_ORDER`, `EDIT_PAYMENT_STATUS`, `DELETE_AUDIT_LOG`
- Payment success must come from a verified gateway webhook only — even
  Admin cannot fake-confirm
- Audit logs are immutable for everyone

**Escalates to:** N/A (terminal authority). Money execution still routes
through Finance Manager workflow even after Admin authorization.

## Cross-role conflicts (must be resolved in code)

1. **Operations exception approval:** Admin needs explicit
   `operations.exception.approve` perm too (fallback when manager
   unavailable). Currently only `OPERATIONS_MANAGER` holds it.
2. **`finance.payout.manage` (coarse) vs new fine-grained
   approve/hold/release perms:** treat coarse perm as parent;
   migrations grant both during a transition window.
3. **Return-parcel return (`HANDED_TO_RIDER / OUT_FOR_DELIVERY →
   RETURN_REQUESTED`):** Fulfillment Supervisor uses exception path
   (reason + evidence); Operations Manager uses dispute path (dispute
   marker). Same state transition, two role paths — backend must
   reject the wrong-role request with 422.
4. **Manual `AVAILABLE` stock increase:** Inventory Manager approves
   (not Warehouse Manager). Grant Inventory Manager
   `warehouse.manual_available.approve` or route approval through
   Inventory Manager APIs.
5. **`finance_supervisor`:** sits in supervisor set (no money actions)
   — confirm intentional. Otherwise grant finance-read perms.
6. **Warehouse Receiving Staff / Warehouse Staff / Inventory
   Supervisor:** Mother-QR SOP needs finer-grained roles than current
   catalog. New role IDs added in `iam/permissions.py` extension
   block — see `app/modules/iam/role_extensions.py`.

## Wiring plan

| Phase | Scope | Status |
|---|---|---|
| **A** | This doc + `authority_matrix.py` + `role_extensions.py` (new perms + 4 new roles + role→blocked-action map) | In progress |
| **B** | Wire Finance Manager module (`app/modules/finance/`): tables `finance_reconciliations`, `cod_settlements`, `cod_settlement_items`, `refund_approvals`, `seller_payout_batches`, `seller_payout_items`, `rider_payout_batches`, `wallet_ledger`, `finance_adjustment_requests`, `finance_audit_logs`; router; alembic; tests | Queued |
| **C** | Wire Inventory Manager module: tables `inventory_stocks`, `stock_reservations`, `stock_movements`, `stock_adjustment_requests`, `return_stock_reviews`, `damaged_lost_inventory`, `seller_stock_accuracy`, `inventory_audit_logs`; router; alembic; tests | Queued |
| **D** | Wire Supervisor + Last-Mile Manager: `authority_matrix.py` enforcement, `manager_approvals`, `supervisor_cod_risk`, complaints, failed-delivery, rules engine | Queued |
| **E** | Wire Warehouse Mother-QR workflow: 12-state lifecycle + exception catalog + scan-control SOP. Largest module — full state machine + scan-log table | Queued |
