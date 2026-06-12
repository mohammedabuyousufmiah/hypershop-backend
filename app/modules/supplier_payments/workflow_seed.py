"""Idempotent seed of default approval workflows.

Two seeded workflows:
  - "standard"     — 3 approval levels, no super-admin. Default for
                     bills under the high-value threshold.
  - "high_value"   — 3 mandatory + level-4 super-admin gate. Binds
                     bills with grand_total >= threshold (default
                     500,000 BDT; override via setting).

Run from the lifespan in main.py at startup. Re-runs are no-ops since
the repo's ``upsert`` keys on ``workflow_code``.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.config import get_settings
from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.supplier_payments.repository import (
    ApprovalWorkflowRepository,
)

_log = get_logger("hypershop.supplier_payments.seed")


async def seed_default_workflows() -> dict[str, int]:
    """Idempotent. Returns ``{"created": N, "updated": M}`` (loose —
    we always upsert, so the call sites just want a count).
    """
    settings = get_settings()
    threshold = Decimal(str(getattr(
        settings,
        "supplier_payment_high_value_threshold_bdt",
        "500000.00",
    )))

    written = 0
    async with UnitOfWork().transactional() as session:
        repo = ApprovalWorkflowRepository(session)
        await repo.upsert(
            workflow_code="standard",
            name="Standard 3-step approval",
            description=(
                "Default workflow for routine supplier bills. "
                "Level 1 (AP) → Level 2 (Procurement/Ops) → "
                "Level 3 (Finance final)."
            ),
            threshold_amount=None,
            requires_super_admin=False,
            min_approval_steps=3,
            is_active=True,
        )
        written += 1
        await repo.upsert(
            workflow_code="high_value",
            name="High-value 4-step approval",
            description=(
                f"Mandatory super-admin gate for bills >= "
                f"{threshold} BDT. Level 1 → 2 → 3 → 4 (super-admin)."
            ),
            threshold_amount=threshold,
            requires_super_admin=True,
            min_approval_steps=4,
            is_active=True,
        )
        written += 1
    _log.info("supplier_payment_workflows_seeded", count=written)
    return {"workflows_seeded": written}
