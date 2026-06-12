"""Return request state machine + condition → action mapping.

Lifecycle: ``requested → received → inspected → completed``. The
inspection step records a per-line ``condition``; the completion step
applies the matching inventory action:

| Condition           | Action  | Lands in bucket |
|---------------------|---------|-----------------|
| sealed              | restock | available       |
| opened              | block   | blocked         |
| cold_chain_broken   | block   | blocked         |
| expired             | dispose | expired         |

The mapping is deterministic and lives in :func:`action_for_condition`.
"""

from __future__ import annotations

from enum import StrEnum

from app.modules.inventory.models import StockBucket


class ReturnStatus(StrEnum):
    REQUESTED = "requested"
    RECEIVED = "received"  # warehouse acknowledged the package arrived
    INSPECTED = "inspected"  # per-line conditions recorded
    COMPLETED = "completed"  # inventory movements applied — terminal
    REJECTED = "rejected"  # admin refused the return — terminal
    CANCELLED = "cancelled"  # customer/admin voided — terminal


ALLOWED_TRANSITIONS: dict[ReturnStatus, frozenset[ReturnStatus]] = {
    ReturnStatus.REQUESTED: frozenset(
        {ReturnStatus.RECEIVED, ReturnStatus.REJECTED, ReturnStatus.CANCELLED},
    ),
    ReturnStatus.RECEIVED: frozenset(
        {ReturnStatus.INSPECTED, ReturnStatus.CANCELLED},
    ),
    ReturnStatus.INSPECTED: frozenset({ReturnStatus.COMPLETED}),
    ReturnStatus.COMPLETED: frozenset(),
    ReturnStatus.REJECTED: frozenset(),
    ReturnStatus.CANCELLED: frozenset(),
}


CUSTOMER_CANCELLABLE = frozenset({ReturnStatus.REQUESTED})


class ReturnCondition(StrEnum):
    SEALED = "sealed"
    OPENED = "opened"
    COLD_CHAIN_BROKEN = "cold_chain_broken"
    EXPIRED = "expired"


class ReturnAction(StrEnum):
    RESTOCK = "restock"
    BLOCK = "block"
    DISPOSE = "dispose"


_CONDITION_TO_ACTION: dict[ReturnCondition, ReturnAction] = {
    ReturnCondition.SEALED: ReturnAction.RESTOCK,
    ReturnCondition.OPENED: ReturnAction.BLOCK,
    ReturnCondition.COLD_CHAIN_BROKEN: ReturnAction.BLOCK,
    ReturnCondition.EXPIRED: ReturnAction.DISPOSE,
}


_ACTION_TO_BUCKET: dict[ReturnAction, StockBucket] = {
    ReturnAction.RESTOCK: StockBucket.AVAILABLE,
    ReturnAction.BLOCK: StockBucket.BLOCKED,
    ReturnAction.DISPOSE: StockBucket.EXPIRED,
}


def action_for_condition(condition: ReturnCondition) -> ReturnAction:
    return _CONDITION_TO_ACTION[condition]


def bucket_for_action(action: ReturnAction) -> StockBucket:
    return _ACTION_TO_BUCKET[action]


class TransitionError(Exception):
    def __init__(self, current: ReturnStatus, target: ReturnStatus) -> None:
        super().__init__(
            f"Cannot transition return from {current.value!r} to {target.value!r}",
        )
        self.current = current
        self.target = target


def assert_can_transition(
    current: ReturnStatus, target: ReturnStatus,
) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise TransitionError(current, target)
