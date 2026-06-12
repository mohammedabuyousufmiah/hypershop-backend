"""Admin-v3 realization — generic persistence for the remaining stub modules.

Creates a single ``av3_records`` table that backs the previously-stubbed
security-hardening, order-trust, seller-hardening, customer-security,
rider-hardening, rider-routing, rider-wallet(+hardening), BI cohorts/funnels/
saved-reports/fraud-cases, mobile-auth, and advertisement endpoints. Rows are
partitioned by ``kind``; ``ref`` is an optional owner key (customer/seller/
rider id), ``payload`` holds the JSON body.

Revision: 0100_admin_v3_realization
Down revision: 0099_automation
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0100_admin_v3_realization"
down_revision = "0099_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "av3_records",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("ref", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("payload", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_av3_kind_ref", "av3_records", ["kind", "ref"])
    op.create_index("ix_av3_kind_status", "av3_records", ["kind", "status"])

    conn = op.get_bind()

    def seed(kind, ref, status, payload):
        conn.execute(sa.text(
            "INSERT INTO av3_records (kind, ref, status, payload) "
            "VALUES (:k, :r, :s, CAST(:p AS jsonb))"),
            {"k": kind, "r": ref, "s": status, "p": json.dumps(payload)})

    # Security defaults + a couple of sample records so lists render.
    seed("security_headers", "_singleton", "active", {
        "csp": "default-src 'self'", "hsts_max_age": 31536000,
        "x_frame_options": "DENY", "x_content_type_options": "nosniff",
        "referrer_policy": "strict-origin-when-cross-origin"})
    seed("security_incident", None, "open", {
        "title": "Suspicious admin login burst", "severity": "medium"})
    seed("vulnerability", None, "open", {
        "package": "example-lib", "cve": "CVE-2025-0001", "severity": "high"})
    # Order-trust samples.
    seed("order_blacklist", None, "active", {
        "value": "01700000000", "type": "phone", "reason": "chargeback abuse"})
    seed("trust_zone", None, "active", {
        "name": "Dhaka high-risk", "postcode_prefix": "12", "risk": "elevated"})
    # Seller-hardening sample.
    seed("seller_reserve", "seller_demo", "held", {
        "amount_minor": 50000, "reason": "rolling reserve"})
    # Customer-security sample.
    seed("gdpr_request", None, "pending", {
        "user_id": "cust_001", "request_type": "export"})
    # Rider-wallet sample.
    seed("cod_settlement", "rider_001", "pending", {
        "amount_minor": 120000, "orders": 4})
    # BI sample.
    seed("bi_fraud_case", None, "open", {
        "customer_id": "cust_001", "reason": "velocity + COD"})
    # Advertisement sample.
    seed("ad_campaign", None, "draft", {
        "name": "Eid push", "daily_budget_minor": 200000})


def downgrade() -> None:
    op.drop_table("av3_records")
