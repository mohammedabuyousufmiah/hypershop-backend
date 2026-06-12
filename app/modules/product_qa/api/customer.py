"""Customer-authored Q&A endpoints — auth required.

  POST  /products/{id}/questions       — create a question
  PATCH /questions/{id}                — edit (≤ 24h)
  POST  /questions/{id}/answers        — create an answer
  PATCH /answers/{id}                  — edit (≤ 24h)
  POST  /answers/{id}/helpful          — upvote (idempotent, no self-vote)
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path as PathParam, status

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.product_qa.codes import PERM_WRITE
from app.modules.product_qa.schemas import (
    AnswerCreateIn,
    AnswerHelpfulOut,
    AnswerUpdateIn,
    PublicAnswerOut,
    PublicQuestionOut,
    QuestionCreateIn,
    QuestionUpdateIn,
)
from app.modules.product_qa.service import QAService

router = APIRouter(tags=["qa-customer"])


@router.post(
    "/products/{product_id}/questions",
    response_model=PublicQuestionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(PERM_WRITE))],
)
async def create_question(
    product_id: Annotated[UUID, PathParam(...)],
    body: QuestionCreateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PublicQuestionOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        q = await svc.create_question(
            product_id=product_id,
            customer_id=principal.user_id,
            body=body.body,
            principal=principal,
        )
    return PublicQuestionOut.model_validate(q)


@router.patch(
    "/questions/{question_id}",
    response_model=PublicQuestionOut,
    dependencies=[Depends(requires_permission(PERM_WRITE))],
)
async def edit_question(
    question_id: Annotated[UUID, PathParam(...)],
    body: QuestionUpdateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PublicQuestionOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        q = await svc.edit_question(
            question_id=question_id,
            customer_id=principal.user_id,
            body=body.body,
        )
    return PublicQuestionOut.model_validate(q)


@router.post(
    "/questions/{question_id}/answers",
    response_model=PublicAnswerOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(PERM_WRITE))],
)
async def create_answer(
    question_id: Annotated[UUID, PathParam(...)],
    body: AnswerCreateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PublicAnswerOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        a = await svc.create_answer(
            question_id=question_id,
            customer_id=principal.user_id,
            body=body.body,
            principal=principal,
        )
    return PublicAnswerOut.model_validate(a)


@router.patch(
    "/answers/{answer_id}",
    response_model=PublicAnswerOut,
    dependencies=[Depends(requires_permission(PERM_WRITE))],
)
async def edit_answer(
    answer_id: Annotated[UUID, PathParam(...)],
    body: AnswerUpdateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PublicAnswerOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        a = await svc.edit_answer(
            answer_id=answer_id,
            customer_id=principal.user_id,
            body=body.body,
        )
    return PublicAnswerOut.model_validate(a)


@router.post(
    "/answers/{answer_id}/helpful",
    response_model=AnswerHelpfulOut,
    dependencies=[Depends(requires_permission(PERM_WRITE))],
)
async def vote_answer_helpful(
    answer_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AnswerHelpfulOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        new_count, voted = await svc.vote_answer_helpful(
            answer_id=answer_id,
            customer_id=principal.user_id,
            principal=principal,
        )
    return AnswerHelpfulOut(
        answer_id=answer_id, helpful_count=new_count, voted=voted,
    )
