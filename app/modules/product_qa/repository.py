"""Async SQLAlchemy repository for the product Q&A tables."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.product_qa.codes import STATUS_APPROVED
from app.modules.product_qa.models import (
    AnswerHelpfulVote,
    ProductAnswer,
    ProductQuestion,
)


class QARepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------- Questions ----------

    async def create_question(self, **fields: object) -> ProductQuestion:
        q = ProductQuestion(**fields)
        self.session.add(q)
        await self.session.flush()
        return q

    async def get_question(self, qid: UUID) -> ProductQuestion | None:
        return await self.session.get(ProductQuestion, qid)

    async def list_public_questions(
        self,
        product_id: UUID,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[Sequence[ProductQuestion], int]:
        base = select(ProductQuestion).where(
            ProductQuestion.product_id == product_id,
            ProductQuestion.status == STATUS_APPROVED,
        )
        items = (
            await self.session.execute(
                base.order_by(ProductQuestion.created_at.desc())
                .offset(offset).limit(limit),
            )
        ).scalars().all()
        total = int((await self.session.execute(
            select(func.count()).select_from(ProductQuestion).where(
                ProductQuestion.product_id == product_id,
                ProductQuestion.status == STATUS_APPROVED,
            ),
        )).scalar_one())
        return items, total

    async def list_admin_questions(
        self,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[ProductQuestion], int]:
        base = select(ProductQuestion)
        if status:
            base = base.where(ProductQuestion.status == status)
        items = (
            await self.session.execute(
                base.order_by(ProductQuestion.created_at.desc())
                .offset(offset).limit(limit),
            )
        ).scalars().all()
        total_stmt = select(func.count()).select_from(ProductQuestion)
        if status:
            total_stmt = total_stmt.where(ProductQuestion.status == status)
        total = int((await self.session.execute(total_stmt)).scalar_one())
        return items, total

    async def update_question_status(
        self,
        qid: UUID,
        *,
        status: str,
        moderated_by: UUID | None = None,
        moderated_at: datetime | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        values: dict[str, object] = {"status": status}
        if moderated_by is not None:
            values["moderated_by"] = moderated_by
        if moderated_at is not None:
            values["moderated_at"] = moderated_at
        if rejection_reason is not None:
            values["rejection_reason"] = rejection_reason
        await self.session.execute(
            update(ProductQuestion)
            .where(ProductQuestion.id == qid)
            .values(**values),
        )

    async def update_question_body(self, qid: UUID, body: str) -> None:
        await self.session.execute(
            update(ProductQuestion)
            .where(ProductQuestion.id == qid)
            .values(body=body),
        )

    # ---------- Answers ----------

    async def create_answer(self, **fields: object) -> ProductAnswer:
        a = ProductAnswer(**fields)
        self.session.add(a)
        await self.session.flush()
        return a

    async def get_answer(self, aid: UUID) -> ProductAnswer | None:
        return await self.session.get(ProductAnswer, aid)

    async def list_public_answers_for_questions(
        self, question_ids: Sequence[UUID],
    ) -> list[ProductAnswer]:
        if not question_ids:
            return []
        stmt = (
            select(ProductAnswer)
            .where(
                ProductAnswer.question_id.in_(question_ids),
                ProductAnswer.status == STATUS_APPROVED,
            )
            .order_by(
                ProductAnswer.question_id,
                ProductAnswer.helpful_count.desc(),
                ProductAnswer.created_at.desc(),
            )
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_admin_answers(
        self,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[ProductAnswer], int]:
        base = select(ProductAnswer)
        if status:
            base = base.where(ProductAnswer.status == status)
        items = (
            await self.session.execute(
                base.order_by(ProductAnswer.created_at.desc())
                .offset(offset).limit(limit),
            )
        ).scalars().all()
        total_stmt = select(func.count()).select_from(ProductAnswer)
        if status:
            total_stmt = total_stmt.where(ProductAnswer.status == status)
        total = int((await self.session.execute(total_stmt)).scalar_one())
        return items, total

    async def update_answer_status(
        self,
        aid: UUID,
        *,
        status: str,
        moderated_by: UUID | None = None,
        moderated_at: datetime | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        values: dict[str, object] = {"status": status}
        if moderated_by is not None:
            values["moderated_by"] = moderated_by
        if moderated_at is not None:
            values["moderated_at"] = moderated_at
        if rejection_reason is not None:
            values["rejection_reason"] = rejection_reason
        await self.session.execute(
            update(ProductAnswer)
            .where(ProductAnswer.id == aid)
            .values(**values),
        )

    async def update_answer_body(self, aid: UUID, body: str) -> None:
        await self.session.execute(
            update(ProductAnswer)
            .where(ProductAnswer.id == aid)
            .values(body=body),
        )

    # ---------- Helpful votes ----------

    async def add_answer_vote(
        self, *, answer_id: UUID, customer_id: UUID,
    ) -> bool:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(AnswerHelpfulVote)
            .values(answer_id=answer_id, customer_id=customer_id)
            .on_conflict_do_nothing(
                index_elements=["answer_id", "customer_id"],
            )
            .returning(AnswerHelpfulVote.answer_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def increment_answer_helpful_count(self, aid: UUID) -> int:
        stmt = (
            update(ProductAnswer)
            .where(ProductAnswer.id == aid)
            .values(helpful_count=ProductAnswer.helpful_count + 1)
            .returning(ProductAnswer.helpful_count)
        )
        return int((await self.session.execute(stmt)).scalar_one())
