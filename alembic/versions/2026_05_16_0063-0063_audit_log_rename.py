"""0063 audit_log_rename — rename ``audit_log`` → ``audit_logs``.

Breaks Hypershop's previous singular convention (``notification_log``,
``outbox_log`` stay singular) but matches the more common ORM
convention used externally and reduces friction for new contributors
who assume plural. One-time decision; no further renames planned.

Rename is atomic + non-destructive — all rows preserved. Indexes
renamed for consistency (`ix_audit_log_*` → `ix_audit_logs_*`).
REVOKE on UPDATE/DELETE from PUBLIC stays applied to the new name —
``ALTER TABLE … RENAME`` preserves table-level ACLs.

Pre-rename FK survey returned zero — no other table references
``audit_log`` via foreign key, so no cross-table updates needed.

Downgrade restores the old name + index names so a roll-back is
clean. Code that refers to the old name (e.g. `app/core/audit/models.py`
`__tablename__`) MUST be updated in lockstep with the upgrade path.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "0063_audit_log_rename"
down_revision: str | Sequence[str] | None = "0062_agent_softphone"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.rename_table("audit_log", "audit_logs")
    op.execute("ALTER INDEX pk_audit_log RENAME TO pk_audit_logs")
    op.execute("ALTER INDEX ix_audit_log_occurred_at RENAME TO ix_audit_logs_occurred_at")
    op.execute("ALTER INDEX ix_audit_log_actor_id    RENAME TO ix_audit_logs_actor_id")
    op.execute("ALTER INDEX ix_audit_log_action      RENAME TO ix_audit_logs_action")
    op.execute("ALTER INDEX ix_audit_log_resource    RENAME TO ix_audit_logs_resource")


def downgrade() -> None:
    op.execute("ALTER INDEX ix_audit_logs_resource    RENAME TO ix_audit_log_resource")
    op.execute("ALTER INDEX ix_audit_logs_action      RENAME TO ix_audit_log_action")
    op.execute("ALTER INDEX ix_audit_logs_actor_id    RENAME TO ix_audit_log_actor_id")
    op.execute("ALTER INDEX ix_audit_logs_occurred_at RENAME TO ix_audit_log_occurred_at")
    op.execute("ALTER INDEX pk_audit_logs RENAME TO pk_audit_log")
    op.rename_table("audit_logs", "audit_log")
