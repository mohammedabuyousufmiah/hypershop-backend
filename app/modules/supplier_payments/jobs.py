"""ARQ cron jobs for the supplier-payment approval engine.

  - ``recompute_all_recommendations_job`` (nightly at 22:00 UTC =
    04:00 BDT next morning) — refreshes the recommendation score on
    every non-terminal bill so the priority queue stays accurate.

The job is bounded (max 500 bills per tick) so it can't run away on
huge backlogs.
"""

from __future__ import annotations

from typing import Any

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.core.security.principal import SystemPrincipal
from app.modules.supplier_payments.repository import (
    SupplierBillApprovalStateRepository,
)
from app.modules.supplier_payments.service import (
    RecommendationEngineRunner,
)

_log = get_logger("hypershop.supplier_payments.jobs")


async def recompute_all_recommendations_job(_ctx: dict[str, Any]) -> int:
    """Refresh recommendation scores. Returns count touched.

    Each bill gets its own recompute call inside the same UoW;
    failures on a single bill don't kill the batch.
    """
    touched = 0
    failed = 0
    principal = SystemPrincipal()
    async with UnitOfWork().transactional() as session:
        states = SupplierBillApprovalStateRepository(session)
        rows = await states.list_active_for_recompute(limit=500)
        bill_ids = [r.supplier_bill_id for r in rows]

    if not bill_ids:
        return 0

    for bid in bill_ids:
        try:
            async with UnitOfWork().transactional() as session:
                runner = RecommendationEngineRunner(session)
                await runner.recompute_for_bill(
                    bill_id=bid,
                    principal=principal,
                )
            touched += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            _log.exception(
                "supplier_payment_recompute_failed",
                bill_id=str(bid),
                error=str(e),
            )

    _log.info(
        "supplier_payment_recommendations_refreshed",
        touched=touched, failed=failed,
    )
    return touched
