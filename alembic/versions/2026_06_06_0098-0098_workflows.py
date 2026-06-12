"""Workflow engine — definitions, runs, and per-step execution log.

Backs the /admin/workflows endpoints (previously stubbed):
  GET  /admin/workflows/definitions
  GET  /admin/workflows/runs, /runs/{id}
  POST /admin/workflows/{code}/trigger        — create a run + execute steps
  POST /admin/workflows/{code}/kill-switch    — block/allow new triggers
  POST /admin/workflows/runs/{id}/resolve-gate
  POST /admin/workflows/runs/{id}/steps/{step_id}/retry

A definition has an ordered list of steps; each step is an "action" (runs
immediately) or a "gate" (pauses the run until an operator resolves it). The
executor is synchronous and in-process. Three built-in definitions are seeded.

Revision: 0098_workflows
Down revision: 0097_mobile_auth_security
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0098_workflows"
down_revision = "0097_mobile_auth_security"
branch_labels = None
depends_on = None


_SEED = [
    {
        "code": "order_fulfillment_check",
        "name": "Order fulfillment check",
        "description": "Validate an order, reserve stock, optional manual review, notify warehouse.",
        "steps": [
            {"name": "validate_order", "type": "action"},
            {"name": "reserve_stock", "type": "action"},
            {"name": "manual_review", "type": "gate"},
            {"name": "notify_warehouse", "type": "action"},
        ],
    },
    {
        "code": "seller_payout_run",
        "name": "Seller payout run",
        "description": "Compute seller balances, finance approval gate, then disburse.",
        "steps": [
            {"name": "compute_balances", "type": "action"},
            {"name": "finance_approval", "type": "gate"},
            {"name": "disburse", "type": "action"},
        ],
    },
    {
        "code": "fraud_sweep",
        "name": "Nightly fraud sweep",
        "description": "Scan recent orders and flag anomalies (no gate).",
        "steps": [
            {"name": "scan_orders", "type": "action"},
            {"name": "flag_anomalies", "type": "action"},
        ],
    },
]


def upgrade() -> None:
    op.create_table(
        "workflow_definitions",
        sa.Column("code", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("steps", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("kill_switch", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("workflow_code", sa.String(64),
                  sa.ForeignKey("workflow_definitions.code", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("trigger_payload", postgresql.JSONB, nullable=True),
        sa.Column("gate_step_index", sa.Integer, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_workflow_runs_code", "workflow_runs", ["workflow_code"])
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])

    op.create_table(
        "workflow_run_steps",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.BigInteger,
                  sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_index", sa.Integer, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("type", sa.String(16), nullable=False, server_default="action"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("output", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_workflow_run_steps_run", "workflow_run_steps", ["run_id"])

    # Seed built-in definitions.
    conn = op.get_bind()
    for d in _SEED:
        conn.execute(
            sa.text(
                "INSERT INTO workflow_definitions (code, name, description, steps) "
                "VALUES (:c, :n, :d, CAST(:s AS jsonb)) ON CONFLICT (code) DO NOTHING"
            ),
            {"c": d["code"], "n": d["name"], "d": d["description"],
             "s": json.dumps(d["steps"])},
        )


def downgrade() -> None:
    op.drop_table("workflow_run_steps")
    op.drop_table("workflow_runs")
    op.drop_table("workflow_definitions")
