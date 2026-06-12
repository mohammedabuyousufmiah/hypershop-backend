"""AI module enums + invariants.

Hard rule
---------
AI is **assistive**. Every AI output is a :class:`AIProposal` with
``status='draft'``. Approving the underlying business action (a
prescription, a refund, a stock reorder) is done by a human via the
*existing* approval endpoints in those modules — there is no AI path
into those endpoints. The constants in :data:`HUMAN_ONLY_ACTIONS` are
the action kinds that MUST NOT be auto-flipped from an AI proposal; the
service layer asserts this via :func:`assert_ai_cannot_decide` at every
public entry point.
"""

from __future__ import annotations

from enum import StrEnum


class AIProposalKind(StrEnum):
    OCR_PRESCRIPTION = "ocr_prescription"  # extract Rx items from an uploaded image
    SUGGEST_MEDICINES = "suggest_medicines"  # rank candidate SKUs from symptoms / generic
    PREDICT_STOCK = "predict_stock"  # forecast depletion + reorder qty for a variant
    DETECT_FRAUD = "detect_fraud"  # risk-score an order for review


class AIProposalStatus(StrEnum):
    DRAFT = "draft"  # AI returned a proposal; awaiting human review
    ACCEPTED = "accepted"  # human reviewer accepted the proposal as-is
    AMENDED = "amended"  # human reviewer accepted with edits (decision_payload != ai_payload)
    REJECTED = "rejected"  # human reviewer discarded the proposal
    EXPIRED = "expired"  # past TTL without action


class AIProviderKind(StrEnum):
    """Symbolic names for the wired provider. The actual binding lives in
    settings (see ``ai_provider`` env var).
    """

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AZURE_OPENAI = "azure_openai"
    GOOGLE_VERTEX = "google_vertex"
    NONE = "none"  # no provider configured — calls return IntegrationError


# ---------------- Hard policy boundary ----------------

HUMAN_ONLY_ACTIONS: frozenset[str] = frozenset(
    {
        # Prescription approval — only the pharmacist on duty can approve.
        # AI may PROPOSE the parsed structure, but cannot flip status.
        "prescriptions.prescription.approve",
        "prescriptions.prescription.reject",
        # Refund decisions — only finance.settle holders pay refunds.
        "finance.refund.pay",
        "finance.refund.cancel",
        # Order overrides AI must never make.
        "orders.order.cancel.any",
        "orders.order.complete",
    },
)


class AIPolicyError(Exception):
    """Raised by :func:`assert_ai_cannot_decide` when caller code attempts
    to use the AI module to perform a human-only action. Wrapped in
    ``ForbiddenError`` at the API boundary.
    """

    def __init__(self, action: str) -> None:
        super().__init__(
            f"AI is not authorized to perform '{action}'. "
            "Use the human-driven endpoint in the owning module."
        )
        self.action = action


def assert_ai_cannot_decide(action: str) -> None:
    """Defensive guard. Service code that builds an AI flow should call
    this with the action it is about to take, so an accidental future
    refactor that pipes AI output into an approval call fails loudly
    rather than silently.
    """
    if action in HUMAN_ONLY_ACTIONS:
        raise AIPolicyError(action)
