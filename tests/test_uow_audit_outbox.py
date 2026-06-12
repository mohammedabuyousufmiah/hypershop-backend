from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import select

from app.core.audit.models import AuditLog
from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork
from app.core.errors import DomainError
from app.core.events.dispatcher import dispatch_once, register_handler
from app.core.events.models import OutboxMessage, OutboxStatus
from app.core.events.outbox import enqueue_outbox
from app.core.security.principal import SystemPrincipal

pytestmark = pytest.mark.integration


async def test_audit_row_persists_on_commit() -> None:
    uow = UnitOfWork()
    async with uow.transactional():
        await record_audit(
            actor=SystemPrincipal(),
            action="kernel.test.audit",
            resource_type="test",
            metadata={"k": "v", "password": "should-be-redacted"},
        )

    async with uow._sessionmaker() as s:
        stmt = select(AuditLog).where(AuditLog.action == "kernel.test.audit")
        rows = (await s.execute(stmt)).scalars().all()
    assert len(rows) == 1
    assert rows[0].outcome == "success"
    assert rows[0].metadata_["k"] == "v"
    assert rows[0].metadata_["password"] == "***"


async def test_audit_row_rolled_back_on_exception() -> None:
    uow = UnitOfWork()
    with pytest.raises(DomainError):
        async with uow.transactional():
            await record_audit(actor=None, action="kernel.test.rollback")
            raise DomainError("boom")

    async with uow._sessionmaker() as s:
        stmt = select(AuditLog).where(AuditLog.action == "kernel.test.rollback")
        rows = (await s.execute(stmt)).scalars().all()
    assert rows == []


async def test_outbox_enqueue_inside_uow() -> None:
    uow = UnitOfWork()
    async with uow.transactional():
        msg = await enqueue_outbox(
            type="kernel.test.echo",
            payload={"hello": "world"},
        )
        assert msg.status == OutboxStatus.PENDING

    async with uow._sessionmaker() as s:
        stmt = select(OutboxMessage).where(OutboxMessage.type == "kernel.test.echo")
        rows = (await s.execute(stmt)).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload == {"hello": "world"}


async def test_outbox_dispatcher_marks_sent_when_handler_succeeds() -> None:
    handled: list[str] = []

    async def _handler(message: OutboxMessage) -> None:
        handled.append(message.payload["x"])

    # The dispatcher's registry is module-global; register only once per type.
    with contextlib.suppress(ValueError):
        register_handler("kernel.test.dispatch", _handler)

    uow = UnitOfWork()
    async with uow.transactional():
        await enqueue_outbox(type="kernel.test.dispatch", payload={"x": "ok"})

    processed = await dispatch_once()
    assert processed == 1
    assert handled == ["ok"]

    async with uow._sessionmaker() as s:
        stmt = select(OutboxMessage).where(OutboxMessage.type == "kernel.test.dispatch")
        msg = (await s.execute(stmt)).scalar_one()
    assert msg.status == OutboxStatus.SENT
    assert msg.dispatched_at is not None


async def test_outbox_dispatcher_retries_on_handler_failure() -> None:
    calls = {"n": 0}

    async def _flaky(_msg: OutboxMessage) -> None:
        calls["n"] += 1
        raise RuntimeError("nope")

    with contextlib.suppress(ValueError):
        register_handler("kernel.test.flaky", _flaky)

    uow = UnitOfWork()
    async with uow.transactional():
        await enqueue_outbox(type="kernel.test.flaky", payload={})

    await dispatch_once()
    async with uow._sessionmaker() as s:
        stmt = select(OutboxMessage).where(OutboxMessage.type == "kernel.test.flaky")
        msg = (await s.execute(stmt)).scalar_one()
    assert msg.status == OutboxStatus.PENDING
    assert msg.attempts == 1
    assert msg.last_error and "RuntimeError" in msg.last_error


async def test_nested_transactional_uses_savepoint() -> None:
    uow = UnitOfWork()
    async with uow.transactional() as outer:
        await record_audit(actor=None, action="kernel.test.outer")
        with contextlib.suppress(DomainError):
            async with uow.transactional() as inner:
                assert inner is outer  # same session
                await record_audit(actor=None, action="kernel.test.inner")
                raise DomainError("undo inner only")

    async with uow._sessionmaker() as s:
        outer_stmt = select(AuditLog).where(AuditLog.action == "kernel.test.outer")
        outer_rows = (await s.execute(outer_stmt)).scalars().all()
        inner_stmt = select(AuditLog).where(AuditLog.action == "kernel.test.inner")
        inner_rows = (await s.execute(inner_stmt)).scalars().all()
    assert len(outer_rows) == 1
    # Inner audit row was rolled back via the SAVEPOINT.
    assert inner_rows == []
