"""Service-level tests for the product Q&A module — phase 3.

Covers:
  - create question (no verified-purchase gate)
  - answer can only attach to approved questions
  - is_seller_answer auto-flag when answerer is linked to product seller
  - moderation transitions (approve / reject / disable / reenable) for both
  - helpful-vote idempotent + no-self-vote
  - public list excludes pending / rejected / disabled
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.db.session import get_sessionmaker
from app.core.security.principal import Principal
from app.modules.catalog.models import (
    Product as ProductModel,
    ProductStatus,
    ProductVariant,
)
from app.modules.product_qa.codes import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from app.modules.product_qa.errors import (
    AnswerHelpfulSelfVoteError,
    QuestionBadStateError,
    QuestionNotFoundError,
)
from app.modules.product_qa.service import QAService
from app.modules.sellers.codes import SELLER_ROLE_OWNER
from app.modules.sellers.service import SellerService

pytestmark = pytest.mark.integration


def _admin(user_id) -> Principal:
    return Principal(
        user_id=user_id, session_id=uuid4(),
        roles=frozenset({"admin"}), permissions=frozenset({"*"}),
    )


def _customer(user_id) -> Principal:
    return Principal(
        user_id=user_id, session_id=uuid4(),
        roles=frozenset({"customer"}),
        permissions=frozenset({"reviews.write"}),
    )


async def _seed_product(seller_id=None):
    sm = get_sessionmaker()
    pid = uuid4()
    suffix = pid.hex[:8]
    async with sm() as s, s.begin():
        p = ProductModel(
            id=pid, slug=f"qa-{suffix}", name=f"QA {suffix}",
            mother_sku=f"QA-{suffix.upper()}",
            status=ProductStatus.ACTIVE,
            base_currency="BDT", tax_class="standard",
            seller_id=seller_id,
            is_medicine=False, requires_prescription=False,
        )
        s.add(p)
        await s.flush()
        s.add(ProductVariant(
            product_id=pid, sku=f"QA-{suffix.upper()}-V1", name="default",
            price=Decimal("10.00"), currency="BDT", is_active=True,
        ))
    return pid


# ───── 1. Create question — no verified purchase ─────


async def test_create_question_no_purchase_required(registered_user):
    pid = await _seed_product()
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = QAService(s)
        q = await svc.create_question(
            product_id=pid,
            customer_id=registered_user["user_id"],
            body="Is this product authentic?",
            principal=_customer(registered_user["user_id"]),
        )
    assert q.status == STATUS_PENDING


# ───── 2. Answer requires approved question ─────


async def test_answer_blocked_on_pending_question(registered_user):
    pid = await _seed_product()
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = QAService(s)
        q = await svc.create_question(
            product_id=pid,
            customer_id=registered_user["user_id"],
            body="Some question text here?",
            principal=_customer(registered_user["user_id"]),
        )
    # q is pending; answer should be blocked as not-found.
    async with sm() as s, s.begin():
        svc = QAService(s)
        with pytest.raises(QuestionNotFoundError):
            await svc.create_answer(
                question_id=q.id,
                customer_id=registered_user["user_id"],
                body="Trying to answer too early.",
                principal=_customer(registered_user["user_id"]),
            )


# ───── 3. Seller-authored answer flagged automatically ─────


async def test_answer_is_seller_when_user_linked_to_product_seller(
    admin_user, registered_user,
):
    # Set up: approved seller, registered_user linked to it, product
    # owned by that seller, approved question.
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        ssvc = SellerService(s)
        seller = await ssvc.create(
            business_name="QA Seller", slug="qa-seller",
            contact_email=None, contact_phone=None,
            principal=_admin(admin_user["user_id"]),
        )
        await ssvc.submit_kyc(
            seller_id=seller.id,
            tin="1", nid="2",
            bank_account_name="X", bank_account_number="3",
            bank_name="Y", bank_branch=None, trade_license_no=None,
            principal=_admin(admin_user["user_id"]),
        )
        await ssvc.approve(
            seller_id=seller.id, principal=_admin(admin_user["user_id"]),
        )
        await ssvc.link_user(
            seller_id=seller.id,
            user_id=registered_user["user_id"],
            role=SELLER_ROLE_OWNER,
            principal=_admin(admin_user["user_id"]),
        )

    pid = await _seed_product(seller_id=seller.id)

    # Customer asks a question (admin role for variety) and admin
    # approves it.
    async with sm() as s, s.begin():
        svc = QAService(s)
        q = await svc.create_question(
            product_id=pid,
            customer_id=admin_user["user_id"],
            body="Is this in stock?",
            principal=_admin(admin_user["user_id"]),
        )
        await svc.approve_question(
            question_id=q.id, principal=_admin(admin_user["user_id"]),
        )

    # Linked seller user answers — must be flagged.
    async with sm() as s, s.begin():
        svc = QAService(s)
        a = await svc.create_answer(
            question_id=q.id,
            customer_id=registered_user["user_id"],
            body="Yes, available — usually delivered in 2-3 days.",
            principal=_customer(registered_user["user_id"]),
        )
    assert a.is_seller_answer is True


async def test_answer_not_seller_when_user_unlinked(
    admin_user, registered_user,
):
    pid = await _seed_product()  # No seller_id
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = QAService(s)
        q = await svc.create_question(
            product_id=pid,
            customer_id=admin_user["user_id"],
            body="Asking something here?",
            principal=_admin(admin_user["user_id"]),
        )
        await svc.approve_question(
            question_id=q.id, principal=_admin(admin_user["user_id"]),
        )
    async with sm() as s, s.begin():
        svc = QAService(s)
        a = await svc.create_answer(
            question_id=q.id,
            customer_id=registered_user["user_id"],
            body="Random user answer here, not a seller.",
            principal=_customer(registered_user["user_id"]),
        )
    assert a.is_seller_answer is False


# ───── 4. Moderation transitions ─────


async def test_question_approve_reject_disable_reenable(admin_user, registered_user):
    pid = await _seed_product()
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = QAService(s)
        q = await svc.create_question(
            product_id=pid,
            customer_id=registered_user["user_id"],
            body="Original question text here?",
            principal=_customer(registered_user["user_id"]),
        )
        approved = await svc.approve_question(
            question_id=q.id, principal=_admin(admin_user["user_id"]),
        )
    assert approved.status == STATUS_APPROVED

    async with sm() as s, s.begin():
        svc = QAService(s)
        disabled = await svc.disable_question(
            question_id=q.id, principal=_admin(admin_user["user_id"]),
        )
    assert disabled.status == "disabled"

    async with sm() as s, s.begin():
        svc = QAService(s)
        reenabled = await svc.reenable_question(
            question_id=q.id, principal=_admin(admin_user["user_id"]),
        )
    assert reenabled.status == STATUS_APPROVED

    # Approve→reject is blocked (only pending → rejected per state machine)
    async with sm() as s, s.begin():
        svc = QAService(s)
        with pytest.raises(QuestionBadStateError):
            await svc.reject_question(
                question_id=q.id, reason="x",
                principal=_admin(admin_user["user_id"]),
            )


# ───── 5. Helpful vote idempotent + no self-vote ─────


async def test_answer_helpful_idempotent_and_no_self(
    admin_user, registered_user,
):
    pid = await _seed_product()
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = QAService(s)
        q = await svc.create_question(
            product_id=pid,
            customer_id=admin_user["user_id"],
            body="Asking something?",
            principal=_admin(admin_user["user_id"]),
        )
        await svc.approve_question(
            question_id=q.id, principal=_admin(admin_user["user_id"]),
        )
        a = await svc.create_answer(
            question_id=q.id,
            customer_id=registered_user["user_id"],
            body="Answer body that's long enough to pass min check.",
            principal=_customer(registered_user["user_id"]),
        )
        await svc.approve_answer(
            answer_id=a.id, principal=_admin(admin_user["user_id"]),
        )

    # Self-vote blocked
    async with sm() as s, s.begin():
        svc = QAService(s)
        with pytest.raises(AnswerHelpfulSelfVoteError):
            await svc.vote_answer_helpful(
                answer_id=a.id,
                customer_id=registered_user["user_id"],
                principal=_customer(registered_user["user_id"]),
            )

    # Different user votes — first lands, second is no-op
    async with sm() as s, s.begin():
        svc = QAService(s)
        c1, voted1 = await svc.vote_answer_helpful(
            answer_id=a.id,
            customer_id=admin_user["user_id"],
            principal=_admin(admin_user["user_id"]),
        )
    assert voted1 is True
    assert c1 == 1

    async with sm() as s, s.begin():
        svc = QAService(s)
        c2, voted2 = await svc.vote_answer_helpful(
            answer_id=a.id,
            customer_id=admin_user["user_id"],
            principal=_admin(admin_user["user_id"]),
        )
    assert voted2 is False
    assert c2 == 1


# ───── 6. Public list excludes non-approved ─────


async def test_public_list_excludes_pending(admin_user, registered_user):
    pid = await _seed_product()
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        svc = QAService(s)
        await svc.create_question(
            product_id=pid,
            customer_id=registered_user["user_id"],
            body="Pending question, not yet visible?",
            principal=_customer(registered_user["user_id"]),
        )
    async with sm() as s, s.begin():
        svc = QAService(s)
        items, total = await svc.list_public_questions(pid)
    assert total == 0
    assert items == []
