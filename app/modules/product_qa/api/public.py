"""Public Q&A list endpoint — anonymous-safe.

  GET /products/{product_id}/qa  → questions + their approved answers
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path as PathParam, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.product_qa.schemas import (
    PublicAnswerOut,
    PublicQuestionListOut,
    PublicQuestionOut,
)
from app.modules.product_qa.service import QAService

router = APIRouter(tags=["qa-public"])


async def _hydrate_display_names(
    session: AsyncSession, customer_ids: list[UUID],
) -> dict[UUID, str]:
    """Map customer_id → first-name (or 'Customer' fallback)."""
    if not customer_ids:
        return {}
    from app.modules.iam.models import User

    rows = (await session.execute(
        select(User.id, User.full_name).where(User.id.in_(customer_ids)),
    )).all()
    out: dict[UUID, str] = {}
    for uid, name in rows:
        out[uid] = (name or "").split()[0] if name else "Customer"
    return out


@router.get(
    "/products/{product_id}/qa",
    response_model=PublicQuestionListOut,
)
async def list_qa(
    product_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> PublicQuestionListOut:
    """Approved questions + their approved answers for a product.

    Single round-trip: questions paged + bulk-fetch all answers in
    one query keyed on the returned question_ids. Display names
    redact email/last-name (first-name only).
    """
    async with uow.transactional() as session:
        svc = QAService(session)
        qs, total = await svc.list_public_questions(
            product_id, offset=offset, limit=limit,
        )
        qs_list = list(qs)
        answers = await svc.list_public_answers_for_questions(
            [q.id for q in qs_list],
        )

        # Hydrate display names for both question authors AND answer
        # authors in a single user-table query.
        all_customer_ids = (
            [q.customer_id for q in qs_list]
            + [a.customer_id for a in answers]
        )
        names = await _hydrate_display_names(session, all_customer_ids)

        answers_by_q: dict[UUID, list[PublicAnswerOut]] = {}
        for a in answers:
            view = PublicAnswerOut.model_validate(a)
            view.customer_display_name = names.get(a.customer_id, "Customer")
            answers_by_q.setdefault(a.question_id, []).append(view)

        items: list[PublicQuestionOut] = []
        for q in qs_list:
            qview = PublicQuestionOut.model_validate(q)
            qview.customer_display_name = names.get(q.customer_id, "Customer")
            qview.answers = answers_by_q.get(q.id, [])
            items.append(qview)

    return PublicQuestionListOut(items=items, total=total)
