"""4 marketplace fulfillment roles + perm grants

Revision ID: 0089_marketplace_fulfillment_roles
Revises: 0088_order_audit_logs
Create Date: 2026-05-24

Seeds 4 spec-required roles + binds them to fulfillment perms:

    marketplace_dispatcher  — can move orders, cannot confirm/approve/edit
    finance_officer         — COD + payment + refund visibility, can
                              settle/edit
    hub_manager             — return/hub stage + sorting + return-to-hub
    fulfillment_manager     — fulfillment-wide oversight + escalations

CORE RULE (enforced via perms NOT granted to marketplace_dispatcher):
  Dispatcher can move orders but CANNOT:
   - confirm orders            (order.confirm)
   - approve payments          (payment.mark.success)
   - approve refunds           (refund.approve)
   - edit COD collected        (cod.collected.edit)
   - edit rider wallet         (rider.wallet.edit)
   - edit seller payout        (seller.payout.edit)
   - delete orders             (order.delete)
   - mark delivered w/o proof  (delivery.mark.no_proof)
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0089_mp_fulfillment_roles"
down_revision = "0088_order_audit_logs"
branch_labels = None
depends_on = None


# Per-role perm grants. Each role gets a curated set.
ROLE_PERMS: dict[str, list[str]] = {
    "marketplace_dispatcher": [
        # Movement + read access — NO financial/destructive perms
        "fulfillment.queue.view", "fulfillment.pickup.assign",
        "fulfillment.delivery.assign", "fulfillment.rider.reassign",
        "fulfillment.batch.create", "fulfillment.fail.mark",
        "fulfillment.reschedule", "fulfillment.return.hub",
        "fulfillment.notify.customer", "fulfillment.escalate.support",
        "fulfillment.sla.alerts.view", "fulfillment.rider.capacity.view",
        "fulfillment.cod.exposure.view",
    ],
    "finance_officer": [
        # COD/payment/refund full access
        "fulfillment.queue.view", "fulfillment.cod.exposure.view",
        "payment.mark.success", "refund.approve",
        "cod.collected.edit", "cod.settlement.approve",
        "rider.wallet.edit", "seller.payout.edit",
        "fulfillment.escalate.finance",
    ],
    "hub_manager": [
        # Hub-leg + return processing
        "fulfillment.queue.view", "fulfillment.return.hub",
        "fulfillment.return.seller", "fulfillment.batch.create",
        "fulfillment.fail.mark", "fulfillment.reschedule",
        "fulfillment.escalate.manager",
    ],
    "fulfillment_manager": [
        # Oversight + escalation authority — most perms except destructive
        "fulfillment.queue.view", "fulfillment.pickup.assign",
        "fulfillment.delivery.assign", "fulfillment.rider.reassign",
        "fulfillment.batch.create", "fulfillment.fail.mark",
        "fulfillment.reschedule", "fulfillment.return.hub",
        "fulfillment.return.seller", "fulfillment.notify.customer",
        "fulfillment.escalate.support", "fulfillment.escalate.finance",
        "fulfillment.escalate.manager",
        "fulfillment.sla.alerts.view", "fulfillment.rider.capacity.view",
        "fulfillment.cod.exposure.view",
        "order.confirm", "order.cancel.high_value",
        "delivery.mark.no_proof",
    ],
}


def upgrade() -> None:
    bind = op.get_bind()
    for role_name, perms in ROLE_PERMS.items():
        # Insert role (idempotent on name)
        bind.execute(sa.text("""
            INSERT INTO roles (name, description, is_system)
            VALUES (:n, :d, true)
            ON CONFLICT (name) DO NOTHING
        """), {
            "n": role_name,
            "d": f"Spec-required role for marketplace fulfillment ({role_name})",
        })
        role_id = bind.execute(sa.text(
            "SELECT id FROM roles WHERE name = :n"
        ), {"n": role_name}).scalar()
        # Grant each perm (idempotent — skip if already granted)
        for perm in perms:
            bind.execute(sa.text("""
                INSERT INTO role_permissions (role_id, permission_id)
                SELECT :rid, p.id FROM permissions p
                WHERE p.name = :pn
                ON CONFLICT DO NOTHING
            """), {"rid": role_id, "pn": perm})


def downgrade() -> None:
    bind = op.get_bind()
    for role_name in ROLE_PERMS:
        bind.execute(sa.text("""
            DELETE FROM role_permissions
            WHERE role_id IN (SELECT id FROM roles WHERE name = :n)
        """), {"n": role_name})
        bind.execute(sa.text("DELETE FROM roles WHERE name = :n"),
                     {"n": role_name})
