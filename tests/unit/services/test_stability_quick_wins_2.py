"""Stability Quick-Wins #2 — backlog items 1 + 2 (2026-08-29 tester 77ab8ab2).

Two new guards close the stranded-carrier class at the boundaries the
gate-tester incident exposed:

  * Item 1 — Send-gate: ``InstanceMessagingService.get_queue_stats``
    must NOT count message_queue rows whose owning instance is in a
    terminal status. The ``send_message`` in-progress gate consumes
    these counts; a stranded carrier on a TERMINATED instance must not
    block a later revive forever ("Pending: 1, Processing: 0").

  * Item 2 — Bus-fire TOCTOU: the dependency-bus fire path
    (``_cancel_bus_watchers_for`` in ``instance_lifecycle.py``) must
    re-purge just-enqueued message_queue/task rows whose target
    instance flips terminal AFTER the fire's pre-enqueue check passed.
    The incident order (fire .404 → enqueue .437 → carrier .448 →
    TERMINATED .453) demonstrates a bare pre-enqueue check has a
    residual race window; this test pins the post-fire re-purge shape.

Both items target only files within the task's allowed surface —
``daemon/services/instance_messaging.py`` for Item 1 and
``daemon/services/instance_lifecycle.py`` for Item 2 — and use the
canonical ``TERMINAL_INSTANCE_STATUSES`` constant from
``daemon.constants`` (no invented status set).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register every table the helper / repository touches before create_all().
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
)
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.repositories.task.models import Task, TaskStatus


# ---------------------------------------------------------------------------
# Shared fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    """Real file-backed SQLite (StaticPool) with all tables created.

    File-backed (rather than :memory:) matches the production-closer
    ``tests/conftest.py`` discipline for StaticPool + WriteGuardSession
    — the silent-write incidents that bit us in 2026-08-25 came from
    in-memory + interleaved sessions; this fixture follows the
    recommended workaround.
    """
    import tempfile
    import os

    tmp_dir = tempfile.mkdtemp(prefix="q2-strand-guard-")
    db_path = os.path.join(tmp_dir, "test.db")
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()
        try:
            os.unlink(db_path)
            os.rmdir(tmp_dir)
        except OSError:
            pass


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Insert an Instance row with the requested status."""
    instance_id = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="test",
                agent_name="test",
                agent_dir="/tmp",
                parent_id=None,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_message(
    engine: Engine,
    *,
    instance_id: str,
    status: str = MessageStatus.READY.value,
    message_id: str | None = None,
) -> str:
    """Insert a MessageQueue row. Returns the message_id."""
    message_id = message_id or str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="stranded message",
                source="internal_agent:test",
                type="completion_report",
                status=status,
                priority=0,
                enqueued_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    return message_id


def _seed_task(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str,
    status: str = TaskStatus.PENDING.value,
) -> int:
    """Insert a Task row tied to the given message_id. Returns task.id."""
    with Session(engine) as session:
        task = Task(
            task_type="process_report",
            instance_id=instance_id,
            message_id=message_id,
            status=status,
            created_at=datetime.now(timezone.utc),
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


def _count_messages(engine: Engine, instance_id: str) -> int:
    with Session(engine) as session:
        return len(
            session.exec(
                SQLModel.metadata.tables["message_queue"]
                .select()
                .where(
                    SQLModel.metadata.tables["message_queue"].c.instance_id
                    == instance_id
                )
            ).all()
        )


def _count_tasks(engine: Engine, instance_id: str) -> int:
    with Session(engine) as session:
        return len(
            session.exec(
                SQLModel.metadata.tables["task"]
                .select()
                .where(
                    SQLModel.metadata.tables["task"].c.instance_id
                    == instance_id
                )
            ).all()
        )


# ---------------------------------------------------------------------------
# Item 1 — Send-gate ignores stranded carriers on terminal instances
# ---------------------------------------------------------------------------


class TestSendGateIgnoresTerminalInstanceCarriers:
    """Item 1: ``get_queue_stats`` must report 0 in-progress for instances
    in a terminal status — the gate cannot block send/revive forever on a
    stranded carrier (the 2026-08-29 wedged-tester incident)."""

    def _build_service(self, engine: Engine):
        """Build an InstanceMessagingService against a real queue repo.

        The service exposes ``get_queue_stats`` via the real queue
        repository; we only stub the manager's other attributes with a
        MagicMock because the gate only touches ``_queue_repository`` and
        ``_instance_repository``.
        """
        from daemon.services.instance_messaging import InstanceMessagingService

        manager = MagicMock(name="InstanceManager")
        manager._queue_repository = SQLModelMessageQueueRepository(
            engine=engine
        )

        # Build a real InstanceRepository against the same engine so the
        # new gate can check the instance's current status. Avoid the
        # repo import-time complexity by importing here.
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )

        manager._instance_repository = SQLModelInstanceRepository(
            engine=engine
        )
        # The service's __init__ signature needs cancellation_service +
        # optional services; supply trivial mocks.
        manager.config = MagicMock()
        return InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_terminal_instance_reports_zero_pending(self, engine):
        """A TERMINATED instance with a stranded READY message returns
        pending_count=0 — the gate must NOT block send/revive."""
        instance_id = _seed_instance(
            engine, status=InstanceStatus.TERMINATED.value
        )
        _seed_message(engine, instance_id=instance_id, status=MessageStatus.READY.value)
        _seed_message(
            engine,
            instance_id=instance_id,
            status=MessageStatus.PROCESSING.value,
        )

        service = self._build_service(engine)
        stats = await service.get_queue_stats(instance_id)

        assert stats["pending_count"] == 0, (
            f"Stranded carrier on TERMINATED instance must NOT count as "
            f"pending; got pending_count={stats['pending_count']}"
        )
        assert stats["processing_count"] == 0, (
            f"Stranded carrier on TERMINATED instance must NOT count as "
            f"processing; got processing_count={stats['processing_count']}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "terminal_status",
        [
            InstanceStatus.COMPLETED.value,
            InstanceStatus.ERROR.value,
            InstanceStatus.TERMINATED.value,
            InstanceStatus.FAILED.value,
        ],
    )
    async def test_all_terminal_statuses_filter_carriers(
        self, engine, terminal_status
    ):
        """Every terminal InstanceStatus enum value must filter stranded
        carriers from the in-progress gate. Locks the canonical set —
        a new enum member would be caught by the parametrize fail."""
        instance_id = _seed_instance(engine, status=terminal_status)
        _seed_message(engine, instance_id=instance_id)

        service = self._build_service(engine)
        stats = await service.get_queue_stats(instance_id)

        assert stats["pending_count"] == 0
        assert stats["processing_count"] == 0

    @pytest.mark.asyncio
    async def test_running_instance_reports_actual_counts(self, engine):
        """A non-terminal instance must still report the actual pending
        and processing counts — the gate only filters terminal owners."""
        instance_id = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value
        )
        _seed_message(engine, instance_id=instance_id, status=MessageStatus.READY.value)
        _seed_message(
            engine,
            instance_id=instance_id,
            status=MessageStatus.PROCESSING.value,
        )

        service = self._build_service(engine)
        stats = await service.get_queue_stats(instance_id)

        assert stats["pending_count"] == 1, (
            f"Live READY message on a RUNNING instance must count as "
            f"pending; got pending_count={stats['pending_count']}"
        )
        assert stats["processing_count"] == 1

    @pytest.mark.asyncio
    async def test_missing_instance_reports_zero(self, engine):
        """An unknown instance id must NOT raise and must return zeros —
        the gate's first call after a cascade may query an already-
        deleted instance; the gate must remain non-blocking."""
        service = self._build_service(engine)
        stats = await service.get_queue_stats("inst-does-not-exist")

        assert stats["pending_count"] == 0
        assert stats["processing_count"] == 0
        assert stats["oldest_message_age_seconds"] is None


# ---------------------------------------------------------------------------
# Item 2 — Bus-fire post-enqueue re-purge
# ---------------------------------------------------------------------------


class TestBusFirePostEnqueueRepurge:
    """Item 2: ``_cancel_bus_watchers_for`` must re-purge just-enqueued
    MessageQueue + Task rows whose target instance flips terminal AFTER
    the fire's pre-enqueue check passed (the 2026-08-29 incident: fire
    .404 → enqueue .437 → carrier .448 → TERMINATED .453 — a bare
    pre-enqueue check has a residual race window).

    The shape under test is the post-fire re-purge: AFTER the
    enqueue_message call, re-check each target instance's status; if
    terminal, delete the just-enqueued rows. Implemented in
    ``_cancel_bus_watchers_for`` (the caller — the bus itself returns
    FollowUps for the caller to enqueue, per the existing
    separation-of-concerns contract).
    """

    def _build_manager(self, engine: Engine):
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )

        manager = MagicMock(name="InstanceManager")
        manager._instance_repository = SQLModelInstanceRepository(
            engine=engine
        )
        manager.engine = engine
        return manager

    def test_post_fire_repurge_deletes_just_enqueued_rows(self, engine):
        """The stranded rows for the just-enqueued message_id are
        purged when the target instance is TERMINATED."""
        target = _seed_instance(
            engine, status=InstanceStatus.TERMINATED.value
        )
        message_id = _seed_message(engine, instance_id=target)
        _seed_task(engine, instance_id=target, message_id=message_id)

        # Pre-condition: stranded row visible.
        assert _count_messages(engine, target) == 1
        assert _count_tasks(engine, target) == 1

        manager = self._build_manager(engine)
        from daemon.services.instance_lifecycle import (
            _repurge_fired_follow_ups,
        )

        purged = _repurge_fired_follow_ups(
            manager=manager,
            op="terminate_instance",
            fired_items=[(target, message_id)],
        )

        assert purged == 1
        assert _count_messages(engine, target) == 0
        assert _count_tasks(engine, target) == 0

    @pytest.mark.asyncio
    async def test_post_fire_repurge_keeps_non_terminal_rows(self, engine):
        """Live rows on a non-terminal parent must NOT be touched by the
        re-purge — the post-fire guard is terminal-only."""
        target = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value
        )
        message_id = _seed_message(engine, instance_id=target)
        _seed_task(engine, instance_id=target, message_id=message_id)

        manager = self._build_manager(engine)
        from daemon.services.instance_lifecycle import (
            _repurge_fired_follow_ups,
        )

        purged = _repurge_fired_follow_ups(
            manager=manager,
            op="terminate_instance",
            fired_items=[(target, message_id)],
        )

        assert purged == 0
        assert _count_messages(engine, target) == 1
        assert _count_tasks(engine, target) == 1

    def test_post_fire_repurge_handles_mixed_terminal_and_live(self, engine):
        """Two targets in one fire: one TERMINATED, one RUNNING. Only
        the TERMINATED target's just-enqueued rows are purged."""
        terminal_target = _seed_instance(
            engine, status=InstanceStatus.TERMINATED.value
        )
        live_target = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value
        )
        stranded_msg = _seed_message(engine, instance_id=terminal_target)
        _seed_task(
            engine, instance_id=terminal_target, message_id=stranded_msg
        )
        live_msg = _seed_message(engine, instance_id=live_target)
        _seed_task(engine, instance_id=live_target, message_id=live_msg)

        manager = self._build_manager(engine)
        from daemon.services.instance_lifecycle import (
            _repurge_fired_follow_ups,
        )

        purged = _repurge_fired_follow_ups(
            manager=manager,
            op="terminate_instance",
            fired_items=[
                (terminal_target, stranded_msg),
                (live_target, live_msg),
            ],
        )

        # One stranded row purged, one live row kept.
        assert purged == 1
        assert _count_messages(engine, terminal_target) == 0
        assert _count_tasks(engine, terminal_target) == 0
        assert _count_messages(engine, live_target) == 1
        assert _count_tasks(engine, live_target) == 1

    @pytest.mark.asyncio
    async def test_repurge_runs_in_finally_when_enqueue_raises_mid_loop(
        self, engine
    ):
        """Review Finding #1 — the post-fire re-purge must still run when
        ``manager.enqueue_message`` raises mid-loop on iteration N+1.

        The bug (pre-fix): the re-purge was called AFTER the for-loop
        with a guard ``if fired and _enqueued_targets``. If
        ``manager.enqueue_message`` raised on iteration 2, control
        skipped the post-loop re-purge — the (target, message_id)
        pair successfully enqueued on iteration 1 was stranded until
        the watchdog cleaned it up later.

        The fix: wrap the for-loop in ``try/finally`` and move the
        re-purge into ``finally``. The finally runs with whatever
        ``_enqueued_targets`` accumulated (iteration 1's row IS in
        the set), so the first target's stranded rows get purged
        even though the loop raised. The original exception
        propagates to the outer ``except Exception as e:`` that
        was already in place for the bus-fire failure shape — the
        caller still sees a return, NOT a raise, exactly like
        today.

        Test shape: two targets, the FIRST is TERMINATED with
        pre-seeded stranded rows matching the message_id the mock
        will return. The mock's first enqueue_message call returns
        a mock with that message_id (recording it in
        ``_enqueued_targets``); the second raises ``RuntimeError``.
        Post-call: the first target's rows are gone, the call
        returned normally (no raise), and ``enqueue_message`` was
        invoked exactly twice.
        """
        from daemon.services.dependency_bus import (
            FollowUp,
            Outcome,
            set_dependency_bus,
        )
        from daemon.services.instance_lifecycle import (
            _cancel_bus_watchers_for,
        )

        # --- arrange: two instance rows. Both start RUNNING so
        # the pre-enqueue parent-liveness check passes for both.
        # target_a will be flipped to TERMINATED by the
        # enqueue_message side_effect AFTER the pre-enqueue check
        # has run — that is the residual race window the post-fire
        # re-purge is designed to close (the 2026-08-29 incident
        # ordering: fire .404 → enqueue .437 → carrier .448 →
        # TERMINATED .453). target_b is the second-FollowUp
        # target whose enqueue will fail with RuntimeError.
        target_a = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value
        )
        # Pre-seed the message_id that the mock will return on
        # the first enqueue. After target_a flips to TERMINATED,
        # the re-purge must find and delete these rows.
        target_a_msg = _seed_message(engine, instance_id=target_a)
        _seed_task(
            engine, instance_id=target_a, message_id=target_a_msg
        )
        # Second target — status stays RUNNING throughout (the
        # second enqueue raises before anything is enqueued).
        target_b = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value
        )

        # Pre-condition: target_a's rows are visible.
        assert _count_messages(engine, target_a) == 1
        assert _count_tasks(engine, target_a) == 1

        # --- arrange: mock the bus singleton. fire_for_terminated_target
        # returns two FollowUps (one per target); mark_enqueued and
        # cancel_for_target are no-op AsyncMocks so the orchestrator
        # can call through them.
        fired_fu_a = FollowUp(
            target_instance_id=target_a,
            message="child complete (target_a)",
            source="internal_agent:test",
            metadata={"source_task_id": "task-a"},
        )
        fired_fu_b = FollowUp(
            target_instance_id=target_b,
            message="child complete (target_b)",
            source="internal_agent:test",
            metadata={"source_task_id": "task-b"},
        )

        mock_bus = MagicMock(name="DependencyBus")
        mock_bus.fire_for_terminated_target = AsyncMock(
            return_value=[fired_fu_a, fired_fu_b]
        )
        mock_bus.mark_enqueued_by_source_target = AsyncMock(return_value=0)
        mock_bus.cancel_for_target = AsyncMock(return_value=0)
        set_dependency_bus(mock_bus)

        try:
            # --- arrange: manager with real instance repo + engine,
            # and an enqueue_message AsyncMock that succeeds for
            # target_a (flipping target_a to TERMINATED in the DB
            # to simulate the post-enqueue TERMINATED stamp that
            # the post-fire re-purge is designed to close), then
            # raises for target_b.
            manager = self._build_manager(engine)

            enq_result_a = MagicMock(name="enq_result_a")
            enq_result_a.message_id = target_a_msg

            async def _enqueue_side_effect(
                *, instance_id, message, source, metadata
            ):
                # First call (target_a): flip the row to TERMINATED
                # in the DB (the residual race-window stamp) and
                # return a mock with the matching message_id so
                # _enqueued_targets records (target_a, target_a_msg).
                if instance_id == target_a:
                    with Session(engine) as session:
                        row = session.get(Instance, target_a)
                        row.status = InstanceStatus.TERMINATED.value
                        session.add(row)
                        session.commit()
                    return enq_result_a
                # Second call (target_b): raise.
                raise RuntimeError(
                    "synthetic enqueue failure on second target"
                )

            manager.enqueue_message = AsyncMock(
                side_effect=_enqueue_side_effect
            )

            # --- act: call the orchestrator. The outer
            # ``except Exception as e:`` catches the RuntimeError
            # so the call returns normally (the existing
            # caller-facing contract).
            await _cancel_bus_watchers_for(
                manager, "child-T", "terminate_instance"
            )

            # --- assert: enqueue_message was called for both
            # FollowUps (the loop did not short-circuit).
            assert manager.enqueue_message.call_count == 2, (
                f"enqueue_message must be invoked for each FollowUp; "
                f"got {manager.enqueue_message.call_count} call(s)"
            )

            # --- assert: the first target's stranded rows are
            # GONE — the post-fire re-purge ran in the finally
            # block despite the RuntimeError on iteration 2.
            assert _count_messages(engine, target_a) == 0, (
                f"target_a's stranded message must be purged by the "
                f"finally re-purge; got {_count_messages(engine, target_a)}"
            )
            assert _count_tasks(engine, target_a) == 0, (
                f"target_a's stranded task must be purged by the "
                f"finally re-purge; got {_count_tasks(engine, target_a)}"
            )
        finally:
            # Always clear the bus singleton so other tests are
            # not affected by our mock registration.
            set_dependency_bus(None)