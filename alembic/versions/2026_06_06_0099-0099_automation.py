"""Automation engine — runs, decisions, proposals, and resilience drills.

Backs the /admin/automation endpoints (previously stubbed):
  GET  /admin/automation/runs
  GET  /admin/automation/decisions
  POST /admin/automation/decisions/{id}/override
  GET  /admin/automation/fraud-proposals,  POST /fraud-proposals/{id}/review
  GET  /admin/automation/alert-proposals,  POST /alert-proposals/{id}/review
  POST /admin/automation/drills/run,       GET  /drills

Four tables: automation_runs (job executions), automation_decisions (auto
decisions an operator can override), automation_proposals (fraud/alert
proposals awaiting review), automation_drills (resilience drill results).
A handful of rows are seeded so the lists render immediately.

Revision: 0099_automation
Down revision: 0098_workflows
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0099_automation"
down_revision = "0098_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="completed"),
        sa.Column("summary", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "automation_decisions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("decision_type", sa.String(48), nullable=False),
        sa.Column("subject", sa.String(160), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("overridden", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("override_outcome", sa.String(32), nullable=True),
        sa.Column("override_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_automation_decisions_type", "automation_decisions", ["decision_type"])
    op.create_table(
        "automation_proposals",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False),  # 'fraud' | 'alert'
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("review_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_automation_proposals_kind", "automation_proposals", ["kind", "status"])
    op.create_table(
        "automation_drills",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("scenario", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pass"),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO automation_runs (kind, status, summary) VALUES "
        "('nightly_sweep','completed', CAST(:s1 AS jsonb)), "
        "('repricing','completed', CAST(:s2 AS jsonb))"
    ), {"s1": json.dumps({"scanned": 124, "flagged": 1}),
        "s2": json.dumps({"products": 124, "adjusted": 0})})
    conn.execute(sa.text(
        "INSERT INTO automation_decisions (decision_type, subject, outcome, confidence) VALUES "
        "('order_auto_approve','order #1042','approved', 0.94), "
        "('payout_hold','seller payout batch','held', 0.71), "
        "('review_route','dispute #88','assigned', 0.83)"
    ))
    conn.execute(sa.text(
        "INSERT INTO automation_proposals (kind, title, severity, payload) VALUES "
        "('fraud','Repeat high-COD canceller', 'high', CAST(:p1 AS jsonb)), "
        "('fraud','Velocity spike on one device', 'medium', CAST(:p2 AS jsonb)), "
        "('alert','Completion rate dropped below 60%', 'high', CAST(:p3 AS jsonb))"
    ), {"p1": json.dumps({"customer": "cust_001", "cancel_rate": 80}),
        "p2": json.dumps({"orders_24h": 7}),
        "p3": json.dumps({"metric": "completion_rate", "value": 0})})
    conn.execute(sa.text(
        "INSERT INTO automation_drills (scenario, status, duration_ms, result) VALUES "
        "('payment_provider_failover','pass', 1240, CAST(:r AS jsonb))"
    ), {"r": json.dumps({"steps_ok": 5, "steps_total": 5})})


def downgrade() -> None:
    op.drop_table("automation_drills")
    op.drop_table("automation_proposals")
    op.drop_table("automation_decisions")
    op.drop_table("automation_runs")
