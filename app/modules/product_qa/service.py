"""Service layer for the product Q&A module — phase 3.

Owns:
  - question + answer create with phase-1 moderation default (pending)
  - 24h customer edit window on both
  - moderation transitions (pending → approved/rejected, approved ↔ disabled)
  - is_seller_answer auto-detect — if the answering user is linked to
    the product's owning seller, the answer is flagged so the
    frontend can render a "Seller" badge
  - helpful-vote idempotency + no-self-vote
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit
from app.core.security.principal import Principal
from app.modules.catalog.models import Product
from app.modules.product_qa.codes import (
    ACTION_ANSWER_APPROVED,
    ACTION_ANSWER_CREATED,
    ACTION_ANSWER_DISABLED,
    ACTION_ANSWER_HELPFUL,
    ACTION_ANSWER_REENABLED,
    ACTION_ANSWER_REJECTED,
    ACTION_QUESTION_APPROVED,
    ACTION_QUESTION_CREATED,
    ACTION_QUESTION_DISABLED,
    ACTION_QUESTION_REENABLED,
    ACTION_QUESTION_REJECTED,
    EDIT_WINDOW_HOURS,
    STATUS_APPROVED,
    STATUS_DISABLED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from app.modules.product_qa.errors import (
    AnswerBadStateError,
    AnswerHelpfulSelfVoteError,
    AnswerNotFoundError,
    QAEditWindowExpiredError,
    QuestionBadStateError,
    QuestionNotFoundError,
)
from app.modules.product_qa.models import ProductAnswer, ProductQuestion
from app.modules.product_qa.repository import QARepository
from app.modules.sellers.authz import seller_id_for_user


class QAService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = QARepository(session)

    # ---------- Customer-facing — questions ----------

    async def create_question(
        self,
        *,
        product_id: UUID,
        customer_id: UUID,
        body: str,
        principal: Principal,
    ) -> ProductQuestion:
        # No verified-purchase gate — pre-purchase Q's are valuable.
        q = await self.repo.create_question(
            product_id=product_id,
            customer_id=customer_id,
            body=body,
            status=STATUS_PENDING,
        )
        await record_audit(
            actor=principal,
            action=ACTION_QUESTION_CREATED,
            resource_type="product_question",
            resource_id=q.id,
            metadata={"product_id": str(product_id)},
        )
        return q

    async def edit_question(
        self,
        *,
        question_id: UUID,
        customer_id: UUID,
        body: str,
    ) -> ProductQuestion:
        q = await self.repo.get_question(question_id)
        if q is None or q.customer_id != customer_id:
            raise QuestionNotFoundError()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=EDIT_WINDOW_HOURS)
        if q.created_at < cutoff:
            raise QAEditWindowExpiredError()
        await self.repo.update_question_body(question_id, body)
        # Edits to approved questions flip back to pending so a re-pass
        # by the moderator catches gaming.
        if q.status == STATUS_APPROVED:
            await self.repo.update_question_status(
                question_id, status=STATUS_PENDING,
            )
        refreshed = await self.repo.get_question(question_id)
        assert refreshed is not None
        return refreshed

    # ---------- Customer-facing — answers ----------

    async def create_answer(
        self,
        *,
        question_id: UUID,
        customer_id: UUID,
        body: str,
        principal: Principal,
    ) -> ProductAnswer:
        q = await self.repo.get_question(question_id)
        if q is None or q.status != STATUS_APPROVED:
            # Don't let answers attach to pending/rejected/disabled
            # questions — surfaced as not-found to avoid leaking
            # moderation state.
            raise QuestionNotFoundError()

        # Detect seller authorship — auto-stamps the badge.
        is_seller_answer = await self._is_seller_for_product(
            user_id=customer_id, product_id=q.product_id,
        )

        a = await self.repo.create_answer(
            question_id=question_id,
            customer_id=customer_id,
            body=body,
            status=STATUS_PENDING,
            is_seller_answer=is_seller_answer,
        )
        await record_audit(
            actor=principal,
            action=ACTION_ANSWER_CREATED,
            resource_type="product_answer",
            resource_id=a.id,
            metadata={
                "question_id": str(question_id),
                "is_seller_answer": is_seller_answer,
            },
        )
        return a

    async def edit_answer(
        self,
        *,
        answer_id: UUID,
        customer_id: UUID,
        body: str,
    ) -> ProductAnswer:
        a = await self.repo.get_answer(answer_id)
        if a is None or a.customer_id != customer_id:
            raise AnswerNotFoundError()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=EDIT_WINDOW_HOURS)
        if a.created_at < cutoff:
            raise QAEditWindowExpiredError()
        await self.repo.update_answer_body(answer_id, body)
        if a.status == STATUS_APPROVED:
            await self.repo.update_answer_status(
                answer_id, status=STATUS_PENDING,
            )
        refreshed = await self.repo.get_answer(answer_id)
        assert refreshed is not None
        return refreshed

    async def vote_answer_helpful(
        self,
        *,
        answer_id: UUID,
        customer_id: UUID,
        principal: Principal,
    ) -> tuple[int, bool]:
        a = await self.repo.get_answer(answer_id)
        if a is None or a.status != STATUS_APPROVED:
            raise AnswerNotFoundError()
        if a.customer_id == customer_id:
            raise AnswerHelpfulSelfVoteError()
        added = await self.repo.add_answer_vote(
            answer_id=answer_id, customer_id=customer_id,
        )
        if not added:
            return a.helpful_count, False
        new_count = await self.repo.increment_answer_helpful_count(answer_id)
        await record_audit(
            actor=principal,
            action=ACTION_ANSWER_HELPFUL,
            resource_type="product_answer",
            resource_id=answer_id,
        )
        return new_count, True

    # ---------- Admin-facing ----------

    async def approve_question(
        self, *, question_id: UUID, principal: Principal,
    ) -> ProductQuestion:
        q = await self._require_question(question_id)
        if q.status not in (STATUS_PENDING, STATUS_DISABLED):
            raise QuestionBadStateError(
                details={"current_status": q.status},
            )
        await self.repo.update_question_status(
            question_id,
            status=STATUS_APPROVED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
        )
        await record_audit(
            actor=principal, action=ACTION_QUESTION_APPROVED,
            resource_type="product_question", resource_id=question_id,
        )
        return await self._require_question(question_id)

    async def reject_question(
        self, *, question_id: UUID, reason: str, principal: Principal,
    ) -> ProductQuestion:
        q = await self._require_question(question_id)
        if q.status != STATUS_PENDING:
            raise QuestionBadStateError(
                details={"current_status": q.status},
            )
        await self.repo.update_question_status(
            question_id,
            status=STATUS_REJECTED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
            rejection_reason=reason,
        )
        await record_audit(
            actor=principal, action=ACTION_QUESTION_REJECTED,
            resource_type="product_question", resource_id=question_id,
            metadata={"reason": reason},
        )
        return await self._require_question(question_id)

    async def disable_question(
        self, *, question_id: UUID, principal: Principal,
    ) -> ProductQuestion:
        q = await self._require_question(question_id)
        if q.status != STATUS_APPROVED:
            raise QuestionBadStateError(
                details={"current_status": q.status},
            )
        await self.repo.update_question_status(
            question_id,
            status=STATUS_DISABLED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
        )
        await record_audit(
            actor=principal, action=ACTION_QUESTION_DISABLED,
            resource_type="product_question", resource_id=question_id,
        )
        return await self._require_question(question_id)

    async def reenable_question(
        self, *, question_id: UUID, principal: Principal,
    ) -> ProductQuestion:
        q = await self._require_question(question_id)
        if q.status != STATUS_DISABLED:
            raise QuestionBadStateError(
                details={"current_status": q.status},
            )
        await self.repo.update_question_status(
            question_id,
            status=STATUS_APPROVED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
        )
        await record_audit(
            actor=principal, action=ACTION_QUESTION_REENABLED,
            resource_type="product_question", resource_id=question_id,
        )
        return await self._require_question(question_id)

    async def approve_answer(
        self, *, answer_id: UUID, principal: Principal,
    ) -> ProductAnswer:
        a = await self._require_answer(answer_id)
        if a.status not in (STATUS_PENDING, STATUS_DISABLED):
            raise AnswerBadStateError(
                details={"current_status": a.status},
            )
        await self.repo.update_answer_status(
            answer_id,
            status=STATUS_APPROVED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
        )
        await record_audit(
            actor=principal, action=ACTION_ANSWER_APPROVED,
            resource_type="product_answer", resource_id=answer_id,
        )
        return await self._require_answer(answer_id)

    async def reject_answer(
        self, *, answer_id: UUID, reason: str, principal: Principal,
    ) -> ProductAnswer:
        a = await self._require_answer(answer_id)
        if a.status != STATUS_PENDING:
            raise AnswerBadStateError(
                details={"current_status": a.status},
            )
        await self.repo.update_answer_status(
            answer_id,
            status=STATUS_REJECTED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
            rejection_reason=reason,
        )
        await record_audit(
            actor=principal, action=ACTION_ANSWER_REJECTED,
            resource_type="product_answer", resource_id=answer_id,
            metadata={"reason": reason},
        )
        return await self._require_answer(answer_id)

    async def disable_answer(
        self, *, answer_id: UUID, principal: Principal,
    ) -> ProductAnswer:
        a = await self._require_answer(answer_id)
        if a.status != STATUS_APPROVED:
            raise AnswerBadStateError(
                details={"current_status": a.status},
            )
        await self.repo.update_answer_status(
            answer_id,
            status=STATUS_DISABLED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
        )
        await record_audit(
            actor=principal, action=ACTION_ANSWER_DISABLED,
            resource_type="product_answer", resource_id=answer_id,
        )
        return await self._require_answer(answer_id)

    async def reenable_answer(
        self, *, answer_id: UUID, principal: Principal,
    ) -> ProductAnswer:
        a = await self._require_answer(answer_id)
        if a.status != STATUS_DISABLED:
            raise AnswerBadStateError(
                details={"current_status": a.status},
            )
        await self.repo.update_answer_status(
            answer_id,
            status=STATUS_APPROVED,
            moderated_by=principal.user_id,
            moderated_at=datetime.now(timezone.utc),
        )
        await record_audit(
            actor=principal, action=ACTION_ANSWER_REENABLED,
            resource_type="product_answer", resource_id=answer_id,
        )
        return await self._require_answer(answer_id)

    # ---------- Read paths ----------

    async def list_public_questions(
        self,
        product_id: UUID,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[Sequence[ProductQuestion], int]:
        return await self.repo.list_public_questions(
            product_id, offset=offset, limit=limit,
        )

    async def list_public_answers_for_questions(
        self, question_ids: Sequence[UUID],
    ) -> list[ProductAnswer]:
        return await self.repo.list_public_answers_for_questions(question_ids)

    async def list_admin_questions(
        self,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[ProductQuestion], int]:
        return await self.repo.list_admin_questions(
            status=status, offset=offset, limit=limit,
        )

    async def list_admin_answers(
        self,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[ProductAnswer], int]:
        return await self.repo.list_admin_answers(
            status=status, offset=offset, limit=limit,
        )

    # ---------- internals ----------

    async def _require_question(self, qid: UUID) -> ProductQuestion:
        q = await self.repo.get_question(qid)
        if q is None:
            raise QuestionNotFoundError()
        return q

    async def _require_answer(self, aid: UUID) -> ProductAnswer:
        a = await self.repo.get_answer(aid)
        if a is None:
            raise AnswerNotFoundError()
        return a

    async def _is_seller_for_product(
        self, *, user_id: UUID, product_id: UUID,
    ) -> bool:
        """Return True if the user is linked to the product's owning seller.

        Pulls the product's seller_id and the user's link in two cheap
        queries; if both resolve and match, the answer carries the
        seller badge.
        """
        sid = await seller_id_for_user(self.session, user_id)
        if sid is None:
            return False
        product = await self.session.get(Product, product_id)
        if product is None or product.seller_id is None:
            return False
        return product.seller_id == sid
