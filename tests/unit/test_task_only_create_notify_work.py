"""Backlog item 5 — ``task_only_create`` sub-shape (b) must
``notify_work()`` after the session commit, mirroring the proven
``c_revival`` pattern (``daemon/manager.py:7312-7313`` and
``:7936-7937``).

Backlog row 5 (2026-08-29): ``task_only_create`` mints a carrier Task
WITHOUT calling ``worker_pool.notify_work()`` → delivery waits for the
next poll (delay, not wedge; backstop compensates). Fix: call
``self._worker_pool.notify_work()`` AFTER the explicit ``session.commit()``
in the sub-shape (b) ``task_only_create`` branch of both the sync
(``_reconcile_deferred_report``) and async
(``_reconcile_deferred_report_async``) reconcile seams.

This file pins the new contract:

* **Sync seam** (line ~7246): after the explicit commit, notify_work
  IS called and the call happens AFTER the commit (record-and-assert
  via a call-order recorder — the commit must come first so the
  worker doesn't pick up a row that hasn't been committed yet).
* **Async seam** (line ~7880): same contract for the router-side
  variant.

A/B evidence pattern: each test must be RED on pre-fix (base commit
``252907ae``) — no notify_work call — and GREEN after the fix lands
in ``daemon/manager.py``.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from types import MethodType
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session as SQLModelSessionLib
from sqlmodel import SQLModel

from daemon.repositories.instance.models import (
    Instance,
    InstanceStatus,
)
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.task.models import (
    TaskStatus,
    TaskType,
)
from daemon.repositories.task.repository import TaskRepository


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """File-style in-memory SQLite (StaticPool) per project convention
    for tests that hold a long-lived transaction and need
    ``check_same_thread=False``.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


class _OrderRecorder:
    """Records the relative order of ``session.commit()`` and
    ``worker_pool.notify_work()`` so the test can assert the
    commit-before-notify ordering that the c_revival pattern enforces.
    """

    def __init__(self) -> None:
        self.events: list[str] = []

    def record_commit(self) -> None:
        self.events.append("commit")

    def record_notify(self) -> None:
        self.events.append("notify_work")


def _seed_instance(
    engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.WAITING_CHILDREN.value,
) -> str:
    import uuid as _uuid
    iid = instance_id or f"inst-{_uuid.uuid4().hex[:8]}"
    with SQLModelSessionLib(engine) as s:
        s.add(Instance(
            instance_id=iid,
            agent_id="leader",
            agent_dir="/tmp/leader",
            agent_name="leader",
            status=status,
            version=1,
            instance_metadata={},
        ))
        s.commit()
    return iid


def _seed_message(
    engine,
    *,
    message_id: str,
    instance_id: str,
    status: str = MessageStatus.READY.value,
) -> None:
    """Insert a MessageQueue row directly — this is the existing
    message that sub-shape (b) sees (existing_message != None).
    """
    with SQLModelSessionLib(engine) as s:
        s.add(MessageQueue(
            message_id=message_id,
            instance_id=instance_id,
            content="task_only_create test message",
            source="task_only_create_test",
            type=MessageType.COMPLETION_REPORT.value,
            status=status,
            priority=0,
            enqueued_at=datetime.now(timezone.utc),
        ))
        s.commit()


def _seed_injection(
    engine,
    *,
    injection_id: str,
    parent_instance_id: str,
    child_instance_id: str,
    report_message_id: str,
    state: str = ReportInjectionState.DEFERRED.value,
) -> None:
    """Insert a ReportInjection row with a non-NULL
    ``report_message_id`` — the condition that routes to sub-shapes
    (b) and (c) (see ``_reconcile_deferred_report:7106``).
    """
    with SQLModelSessionLib(engine) as s:
        s.add(ReportInjection(
            injection_id=injection_id,
            parent_instance_id=parent_instance_id,
            child_instance_id=child_instance_id,
            child_message_id="child-msg-b5",
            report_message_id=report_message_id,
            content="report content",
            state=state,
        ))
        s.commit()


def _wrap_session_commit_with_recorder(engine, recorder: _OrderRecorder):
    """Install a SQLAlchemy event listener that records each
    ``commit`` event on the engine. Used by the test to assert
    commit-before-notify ordering without monkey-patching the
    SQLModelSession ``commit`` method (which would break the ORM
    commit semantics).
    """
    from sqlalchemy import event

    @event.listens_for(engine, "commit")
    def _on_commit(dbapi_connection):  # noqa: ARG001 — SQLAlchemy hook
        recorder.record_commit()

    return _on_commit


# ─── Sync seam test ──────────────────────────────────────────────────────────


class TestTaskOnlyCreateNotifyWorkSync:
    """Sync seam: ``_reconcile_deferred_report`` sub-shape (b)
    ``task_only_create`` path must call ``worker_pool.notify_work()``
    after the explicit ``session.commit()``.

    Mirrors the proven ``c_revival`` shape at
    ``daemon/manager.py:7312-7313`` (commit-then-notify ordering
    is load-bearing — notifying before commit risks a worker
    claiming a row that hasn't been persisted yet).
    """

    def _build_holder(self, engine, worker_pool: MagicMock):
        from daemon.write_pause_guard import WritePauseGuard
        from daemon.manager import InstanceManager

        @contextmanager
        def _session_scope():
            session = SQLModelSessionLib(engine)
            try:
                yield session
            finally:
                session.close()

        holder: Any = type("SyncHolderB5", (), {})()
        holder.engine = engine
        holder._worker_pool = worker_pool
        holder._write_guard = WritePauseGuard()
        holder._session_scope = _session_scope
        holder._reconcile_deferred_report = MethodType(
            InstanceManager._reconcile_deferred_report, holder,
        )
        return holder

    def test_notify_work_called_after_commit(
        self, engine,
    ):
        """Sub-shape (b) task_only_create: seam must
        (a) create the PROCESS_REPORT task,
        (b) commit it, AND
        (c) call ``worker_pool.notify_work()`` AFTER the commit.

        The pre-fix seam does (a)+(b) but NOT (c) — this test
        fails on base ``252907ae`` with ``notify_work.assert_called_once()``
        reporting 0 calls.
        """
        # Arrange — alive parent (NOT dead, NOT terminated) so the
        # dead-parent guard short-circuits away.
        parent_id = _seed_instance(
            engine, status=InstanceStatus.WAITING_CHILDREN.value,
        )
        child_id = _seed_instance(
            engine,
            instance_id="child-b5-sync",
            status=InstanceStatus.COMPLETED.value,
        )
        report_message_id = "msg-b5-sync"
        injection_id = "inj-b5-sync"

        # Sub-shape (b) signature: message exists, task missing.
        _seed_message(
            engine,
            message_id=report_message_id,
            instance_id=parent_id,
            status=MessageStatus.READY.value,
        )
        _seed_injection(
            engine,
            injection_id=injection_id,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            report_message_id=report_message_id,
        )
        # NO PROCESS_REPORT task seeded → existing_task is None →
        # sub-shape (b) ``task_only_create`` branch is taken.

        # Worker pool mock — ``notify_work`` records into our
        # order-recorder so we can assert commit-before-notify.
        recorder = _OrderRecorder()
        worker_pool = MagicMock()
        worker_pool.notify_work = MagicMock(
            side_effect=recorder.record_notify,
        )

        # Install SQLAlchemy commit event listener — records every
        # commit that flows through ``engine`` into the recorder.
        _wrap_session_commit_with_recorder(engine, recorder)

        holder = self._build_holder(engine, worker_pool)

        # Act — invoke the sync reconcile.
        result = holder._reconcile_deferred_report(
            child_instance_id=child_id,
            child_message_id="child-msg-b5",
            injection_id=injection_id,
            source="b5_sync",
        )

        # Assert — the seam returned the task_only_create shape
        # (not dead_parent_skip, not c_revival, not delivery_only).
        assert result is not None
        assert result["shape"] == "task_only_create", (
            f"B5 sync contract: sub-shape (b) message-exists-task-missing "
            f"on an alive parent MUST return shape=task_only_create. "
            f"Got shape={result['shape']!r}"
        )
        assert result["report_message_id"] == report_message_id

        # Assert — exactly one new PROCESS_REPORT PENDING carrier
        # was created (sanity: the seam actually minted the task).
        repo = TaskRepository(engine, on_pending_task=lambda: None)
        live_carriers = repo.list_live_process_report_carriers_for_instance(
            instance_id=parent_id,
        )
        matching = [
            c for c in live_carriers
            if c.message_id == report_message_id
        ]
        assert len(matching) == 1, (
            f"B5 sync contract: exactly one LIVE PROCESS_REPORT carrier "
            f"must exist after the seam runs. Got {len(matching)}."
        )
        assert matching[0].status == TaskStatus.PENDING.value
        assert matching[0].task_type == TaskType.PROCESS_REPORT.value

        # Assert — worker pool was notified.
        # PRE-FIX on base 252907ae this fails: notify_work is never
        # called from the sub-shape (b) task_only_create branch.
        assert worker_pool.notify_work.call_count == 1, (
            f"B5 sync contract: sub-shape (b) task_only_create MUST call "
            f"worker_pool.notify_work() exactly once. Pre-fix the seam "
            f"mints the carrier but never wakes the worker pool, leaving "
            f"delivery to the next poll (delay, not wedge — the backstop "
            f"compensates). Got {worker_pool.notify_work.call_count} "
            f"notify_work call(s)."
        )

        # Assert — notify_work was called AFTER the commit (the
        # commit-before-notify ordering that the c_revival pattern
        # enforces is load-bearing — notifying before commit risks
        # a worker claiming a row that hasn't been persisted).
        commit_indices = [
            i for i, e in enumerate(recorder.events) if e == "commit"
        ]
        notify_indices = [
            i for i, e in enumerate(recorder.events) if e == "notify_work"
        ]
        assert len(commit_indices) >= 1, (
            f"B5 sync contract: at least one commit must have occurred "
            f"during the seam. Got events={recorder.events!r}"
        )
        assert len(notify_indices) == 1, (
            f"B5 sync contract: exactly one notify_work must have been "
            f"recorded. Got events={recorder.events!r}"
        )
        assert notify_indices[0] > commit_indices[-1], (
            f"B5 sync contract: notify_work MUST be called AFTER the "
            f"final commit (mirrors c_revival at manager.py:7312-7313). "
            f"Got events={recorder.events!r}. commit_idx="
            f"{commit_indices[-1]}, notify_idx={notify_indices[0]}"
        )


# ─── Async seam test ─────────────────────────────────────────────────────────


class TestTaskOnlyCreateNotifyWorkAsync:
    """Async seam: ``_reconcile_deferred_report_async`` sub-shape (b)
    ``task_only_create`` path must also call
    ``worker_pool.notify_work()`` after the explicit
    ``session.commit()``.

    Mirrors the proven async ``c_revival`` shape at
    ``daemon/manager.py:7936-7937``.
    """

    def _build_holder(self, engine, worker_pool: MagicMock):
        from daemon.write_pause_guard import WritePauseGuard
        from daemon.manager import InstanceManager

        @contextmanager
        def _session_scope():
            session = SQLModelSessionLib(engine)
            try:
                yield session
            finally:
                session.close()

        holder: Any = type("AsyncHolderB5", (), {})()
        holder.engine = engine
        holder._worker_pool = worker_pool
        holder._write_guard = WritePauseGuard()
        holder._session_scope = _session_scope
        holder._reconcile_deferred_report_async = MethodType(
            InstanceManager._reconcile_deferred_report_async, holder,
        )
        return holder

    @pytest.mark.asyncio
    async def test_notify_work_called_after_commit_async(
        self, engine,
    ):
        """Async variant of the same contract — the router-side
        reconcile must also notify the worker pool after commit.
        """
        parent_id = _seed_instance(
            engine, status=InstanceStatus.WAITING_CHILDREN.value,
        )
        child_id = _seed_instance(
            engine,
            instance_id="child-b5-async",
            status=InstanceStatus.COMPLETED.value,
        )
        report_message_id = "msg-b5-async"
        injection_id = "inj-b5-async"

        _seed_message(
            engine,
            message_id=report_message_id,
            instance_id=parent_id,
            status=MessageStatus.READY.value,
        )
        _seed_injection(
            engine,
            injection_id=injection_id,
            parent_instance_id=parent_id,
            child_instance_id=child_id,
            report_message_id=report_message_id,
        )

        recorder = _OrderRecorder()
        worker_pool = MagicMock()
        worker_pool.notify_work = MagicMock(
            side_effect=recorder.record_notify,
        )

        _wrap_session_commit_with_recorder(engine, recorder)

        holder = self._build_holder(engine, worker_pool)

        result = await holder._reconcile_deferred_report_async(
            child_instance_id=child_id,
            child_message_id="child-msg-b5",
            injection_id=injection_id,
            source="b5_async",
        )

        assert result is not None
        assert result["shape"] == "task_only_create", (
            f"B5 async contract: sub-shape (b) message-exists-task-missing "
            f"on an alive parent MUST return shape=task_only_create. "
            f"Got shape={result['shape']!r}"
        )

        repo = TaskRepository(engine, on_pending_task=lambda: None)
        live_carriers = repo.list_live_process_report_carriers_for_instance(
            instance_id=parent_id,
        )
        matching = [
            c for c in live_carriers
            if c.message_id == report_message_id
        ]
        assert len(matching) == 1, (
            f"B5 async contract: exactly one LIVE PROCESS_REPORT carrier "
            f"must exist after the seam runs. Got {len(matching)}."
        )

        assert worker_pool.notify_work.call_count == 1, (
            f"B5 async contract: sub-shape (b) task_only_create MUST call "
            f"worker_pool.notify_work() exactly once. Pre-fix the seam "
            f"mints the carrier but never wakes the worker pool on the "
            f"router side either. Got {worker_pool.notify_work.call_count} "
            f"notify_work call(s)."
        )

        commit_indices = [
            i for i, e in enumerate(recorder.events) if e == "commit"
        ]
        notify_indices = [
            i for i, e in enumerate(recorder.events) if e == "notify_work"
        ]
        assert len(commit_indices) >= 1, (
            f"B5 async contract: at least one commit must have occurred. "
            f"Got events={recorder.events!r}"
        )
        assert len(notify_indices) == 1, (
            f"B5 async contract: exactly one notify_work must have been "
            f"recorded. Got events={recorder.events!r}"
        )
        assert notify_indices[0] > commit_indices[-1], (
            f"B5 async contract: notify_work MUST be called AFTER the "
            f"final commit. Got events={recorder.events!r}. commit_idx="
            f"{commit_indices[-1]}, notify_idx={notify_indices[0]}"
        )