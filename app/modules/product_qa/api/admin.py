"""Admin moderation endpoints for the product Q&A module — phase 3.

Mirrors the reviews admin surface — same approve / reject / disable /
reenable verbs, separate routes for question rows and answer rows.

  GET  /admin/qa/questions?status=&offset=&limit=
  GET  /admin/qa/answers?status=&offset=&limit=
  POST /admin/qa/questions/{id}/{approve,reject,disable,reenable}
  POST /admin/qa/answers/{id}/{approve,reject,disable,reenable}
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path as PathParam, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import ValidationError
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.product_qa.codes import ALL_STATUSES, PERM_ADMIN
from app.modules.product_qa.schemas import (
    AdminAnswerListOut,
    AdminAnswerOut,
    AdminQuestionListOut,
    AdminQuestionOut,
    ModerationRejectIn,
)
from app.modules.product_qa.service import QAService

router = APIRouter(prefix="/admin/qa", tags=["admin-qa"])


@router.get(
    "/questions",
    response_model=AdminQuestionListOut,
    dependencies=[Depends(requires_permission(PERM_ADMIN))],
)
async def list_questions_admin(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AdminQuestionListOut:
    if status and status not in ALL_STATUSES:
        raise ValidationError(
            f"Unknown status: {status}.",
            details={"allowed": list(ALL_STATUSES)},
        )
    async with uow.transactional() as session:
        svc = QAService(session)
        items, total = await svc.list_admin_questions(
            status=status, offset=offset, limit=limit,
        )
        rows = [AdminQuestionOut.model_validate(q) for q in items]
    return AdminQuestionListOut(items=rows, total=total)


@router.get(
    "/answers",
    response_model=AdminAnswerListOut,
    dependencies=[Depends(requires_permission(PERM_ADMIN))],
)
async def list_answers_admin(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AdminAnswerListOut:
    if status and status not in ALL_STATUSES:
        raise ValidationError(
            f"Unknown status: {status}.",
            details={"allowed": list(ALL_STATUSES)},
        )
    async with uow.transactional() as session:
        svc = QAService(session)
        items, total = await svc.list_admin_answers(
            status=status, offset=offset, limit=limit,
        )
        rows = [AdminAnswerOut.model_validate(a) for a in items]
    return AdminAnswerListOut(items=rows, total=total)


# ───── Question moderation ─────


def _admin_dep():
    return Depends(requires_permission(PERM_ADMIN))


@router.post(
    "/questions/{question_id}/approve",
    response_model=AdminQuestionOut,
    dependencies=[_admin_dep()],
)
async def approve_question(
    question_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminQuestionOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        q = await svc.approve_question(
            question_id=question_id, principal=principal,
        )
        await session.refresh(q)
        return AdminQuestionOut.model_validate(q)


@router.post(
    "/questions/{question_id}/reject",
    response_model=AdminQuestionOut,
    dependencies=[_admin_dep()],
)
async def reject_question(
    question_id: Annotated[UUID, PathParam(...)],
    body: ModerationRejectIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminQuestionOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        q = await svc.reject_question(
            question_id=question_id, reason=body.reason, principal=principal,
        )
        await session.refresh(q)
        return AdminQuestionOut.model_validate(q)


@router.post(
    "/questions/{question_id}/disable",
    response_model=AdminQuestionOut,
    dependencies=[_admin_dep()],
)
async def disable_question(
    question_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminQuestionOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        q = await svc.disable_question(
            question_id=question_id, principal=principal,
        )
        await session.refresh(q)
        return AdminQuestionOut.model_validate(q)


@router.post(
    "/questions/{question_id}/reenable",
    response_model=AdminQuestionOut,
    dependencies=[_admin_dep()],
)
async def reenable_question(
    question_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminQuestionOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        q = await svc.reenable_question(
            question_id=question_id, principal=principal,
        )
        await session.refresh(q)
        return AdminQuestionOut.model_validate(q)


# ───── Answer moderation ─────


@router.post(
    "/answers/{answer_id}/approve",
    response_model=AdminAnswerOut,
    dependencies=[_admin_dep()],
)
async def approve_answer(
    answer_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminAnswerOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        a = await svc.approve_answer(
            answer_id=answer_id, principal=principal,
        )
        await session.refresh(a)
        return AdminAnswerOut.model_validate(a)


@router.post(
    "/answers/{answer_id}/reject",
    response_model=AdminAnswerOut,
    dependencies=[_admin_dep()],
)
async def reject_answer(
    answer_id: Annotated[UUID, PathParam(...)],
    body: ModerationRejectIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminAnswerOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        a = await svc.reject_answer(
            answer_id=answer_id, reason=body.reason, principal=principal,
        )
        await session.refresh(a)
        return AdminAnswerOut.model_validate(a)


@router.post(
    "/answers/{answer_id}/disable",
    response_model=AdminAnswerOut,
    dependencies=[_admin_dep()],
)
async def disable_answer(
    answer_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminAnswerOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        a = await svc.disable_answer(
            answer_id=answer_id, principal=principal,
        )
        await session.refresh(a)
        return AdminAnswerOut.model_validate(a)


@router.post(
    "/answers/{answer_id}/reenable",
    response_model=AdminAnswerOut,
    dependencies=[_admin_dep()],
)
async def reenable_answer(
    answer_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminAnswerOut:
    async with uow.transactional() as session:
        svc = QAService(session)
        a = await svc.reenable_answer(
            answer_id=answer_id, principal=principal,
        )
        await session.refresh(a)
        return AdminAnswerOut.model_validate(a)
