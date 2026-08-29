"""Reconciler wedge-fix regression tests (T1, T2, T3, T4).

These tests pin the four contracts introduced by the wedge-fix batch:

* T1 — JobItem-less PROCESS_REPORT PENDING task + OLD terminal
  dispatch JobItem on the same instance → NOT cancelled by
  ``reconcile_drift_states`` (Pattern d Fix 1: linkage by work_id,
  not instance).
* T2 — Genuinely orphaned Task (own-work_id JobItem terminal +
  DEAD instance) → STILL cancelled (Pattern d safety net preserved).
  T2b — Alive WAITING_CHILDREN parent + terminal own-work_id JobItem
  → NOT cancelled (Pattern d Fix 2: alive-instance guard).
* T3 — Sub-shape (c) carrier-revival: alive parent, READY message,
  CANCELLED PROCESS_REPORT carrier → ``_reconcile_deferred_report``
  enqueues a fresh carrier + ``notify_work()`` (idempotent: a second
  sweep with a live carrier stays silent).
* T4 — Wedge-fix backstop in ``WaitingChildrenWatchdog``: WC parent
  + zero non-terminal children + zero live carrier → wedge notice
  enqueued via the wake path. WC parent with a live carrier stays
  silent (composition property); WC parent with live non-terminal
  children also stays silent (healthy-parent guard — carriers are
  only created at child completion, so a parent waiting on
  in-flight children has no carrier yet and must not be flagged).

A/B evidence pattern: each test must be RED on pre-fix
(``29898ee2``) and GREEN on the fixed tree (this branch). The
worktree at ``/tmp/agents-ensemble-prefix`` is the pre-fix reference;
the main worktree is the post-fix tree.

Production-incident victim: instance ``a8677e5c`` (wedged,
pending report from 18:01:54). The wedge-fix is the architectural
fix for that incident class; the watchdog backstop is the
defense-in-depth backstop.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import MethodType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select, text
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
    Task,
    TaskStatus,
    TaskType,
)
from daemon.repositories.task.repository import TaskRepository
from daemon.services.waiting_children_watchdog import (
    WEDGE_SOURCE,
    WaitingChildrenWatchdog,
    _build_wedge_notice,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """File-backed SQLite engine (per the project convention for
    tests that hold a long-lived transaction).
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def task_repository(engine) -> TaskRepository:
    return TaskRepository(engine, on_pending_task=lambda: None)


def _seed_instance(
    engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.WAITING_CHILDREN.value,
) -> str:
    """Insert a single Instance row directly via SQLModel."""
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
    """Insert a MessageQueue row directly."""
    with SQLModelSessionLib(engine) as s:
        s.add(MessageQueue(
            message_id=message_id,
            instance_id=instance_id,
            content="wedge-fix test message",
            source="wedge_test",
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
    """Insert a ReportInjection row directly."""
    with SQLModelSessionLib(engine) as s:
        s.add(ReportInjection(
            injection_id=injection_id,
            parent_instance_id=parent_instance_id,
            child_instance_id=child_instance_id,
            child_message_id="child-msg-test",
            report_message_id=report_message_id,
            content="report content",
            state=state,
        ))
        s.commit()


def _seed_process_report_task(
    engine,
    *,
    instance_id: str,
    message_id: str,
    status: str = TaskStatus.PENDING.value,
    work_id: str | None = None,
) -> int:
    """Insert a PROCESS_REPORT Task row directly."""
    import uuid as _uuid
    work_id = work_id or str(_uuid.uuid4())
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred, :is_background)
                """
            ),
            {
                "task_type": TaskType.PROCESS_REPORT.value,
                "instance_id": instance_id,
                "message_id": message_id,
                "status": status,
                "retry_count": 0,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": work_id,
                "is_deferred": False,
                "is_background": False,
            },
        )
        return result.lastrowid


# ─── T3 — Sub-shape (c) carrier-revival (sync) ───────────────────────────────


class TestSubshapeCCarrierRevivalSync:
    """T3 sync: ``_reconcile_deferred_report`` (sync) enqueues a
    fresh PROCESS_REPORT carrier when sub-shape (c) sees a READY
    message + CANCELLED carrier on an alive parent.

    The seam mirrors ``child_reports.py:2843-2852`` for the
    carrier shape. Idempotency: a second sweep with a live carrier
    stays silent (returns ``delivery_only``).
    """

    def _build_holder(self, engine, worker_pool: MagicMock):
        """Wire a minimal holder for the sync seam."""
        from daemon.write_pause_guard import WritePauseGuard
        from daemon.manager import InstanceManager

        @contextmanager
        def _session_scope():
            session = SQLModelSessionLib(engine)
            try:
                yield session
            finally:
                session.close()

        holder: Any = type("SyncHolder", (), {})()
        holder.engine = engine
        holder._worker_pool = worker_pool
        holder._write_guard = WritePauseGuard()
        holder._session_scope = _session_scope
        holder._reconcile_deferred_report = MethodType(
            InstanceManager._reconcile_deferred_report, holder,
        )
        # Wedge-fix helpers must also be bound — the sync seam now
        # calls ``self._has_live_process_report_carrier`` and
        # ``self._is_parent_alive`` from within the sub-shape (c)
        # branch (Fix 3). The MethodType trick rebinds the unbound
        # function to the holder; we need to do the same for the two
        # helper methods the seam now touches.
        holder._has_live_process_report_carrier = MethodType(
            InstanceManager._has_live_process_report_carrier, holder,
        )
        holder._is_parent_alive = MethodType(
            InstanceManager._is_parent_alive, holder,
        )
        return holder

    def test_revives_carrier_when_alive_parent_no_live_carrier(
        self, engine,
    ):
        """Sub-shape (c) + alive parent + cancelled carrier →
        fresh PROCESS_REPORT carrier enqueued + worker pool
        ``notify_work()`` called.
        """
        # Arrange — alive parent, READY message, cancelled carrier.
        parent_id = _seed_instance(
            engine, status=InstanceStatus.WAITING_CHILDREN.value,
        )
        child_id = _seed_instance(
            engine,
            instance_id="child-t3",
            status=InstanceStatus.COMPLETED.value,
        )
        report_message_id = "msg-t3-revival"
        injection_id = "inj-t3-revival"
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
        # Cancelled carrier (the wedge signature).
        _seed_process_report_task(
            engine,
            instance_id=parent_id,
            message_id=report_message_id,
            status=TaskStatus.CANCELLED.value,
        )

        # Wire a worker pool mock that records notify_work calls.
        worker_pool = MagicMock()
        worker_pool.notify_work = MagicMock()

        holder = self._build_holder(engine, worker_pool)

        # Act — invoke the sync reconcile.
        result = holder._reconcile_deferred_report(
            child_instance_id=child_id,
            child_message_id="child-msg-test",
            injection_id=injection_id,
            source="wedge_test",
        )

        # Assert — the seam returned the revival shape.
        assert result is not None
        assert result["shape"] == "c_revival", (
            f"T3 contract: sub-shape (c) with alive parent + no live "
            f"carrier MUST enqueue a fresh carrier (shape=c_revival). "
            f"Pre-fix this was a delivery_only no-op and the parent "
            f"never woke. Got shape={result['shape']!r}"
        )
        assert result["report_message_id"] == report_message_id

        # Assert — a NEW PROCESS_REPORT PENDING task was inserted.
        # Use the TaskRepository helper to avoid SQLModel session
        # connection-pool contention with the holder's
        # ``_session_scope`` (StaticPool + multiple sessions).
        repo = TaskRepository(engine, on_pending_task=lambda: None)
        live_carriers = repo.list_live_process_report_carriers_for_instance(
            instance_id=parent_id,
        )
        # Filter to the report_message_id we care about — the helper
        # returns ALL live carriers for the instance (this test
        # should only have one).
        matching = [
            c for c in live_carriers
            if c.message_id == report_message_id
        ]
        assert len(matching) == 1, (
            f"T3 contract: exactly one LIVE PROCESS_REPORT carrier "
            f"must exist after revival (the new one). Got "
            f"{len(matching)} matching live carriers."
        )
        assert matching[0].status == TaskStatus.PENDING.value

        # Assert — worker pool notified.
        worker_pool.notify_work.assert_called_once()

    def test_idempotent_when_live_carrier_already_exists(
        self, engine,
    ):
        """T3 idempotency: second sweep with a live carrier returns
        ``delivery_only`` and does NOT enqueue a duplicate.
        """
        parent_id = _seed_instance(
            engine, status=InstanceStatus.WAITING_CHILDREN.value,
        )
        child_id = _seed_instance(
            engine,
            instance_id="child-t3-idem",
            status=InstanceStatus.COMPLETED.value,
        )
        report_message_id = "msg-t3-idem"
        injection_id = "inj-t3-idem"
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
        # Pre-existing LIVE carrier (PENDING) — the wedge is NOT active.
        _seed_process_report_task(
            engine,
            instance_id=parent_id,
            message_id=report_message_id,
            status=TaskStatus.PENDING.value,
        )

        worker_pool = MagicMock()
        worker_pool.notify_work = MagicMock()
        holder = self._build_holder(engine, worker_pool)

        result = holder._reconcile_deferred_report(
            child_instance_id=child_id,
            child_message_id="child-msg-test",
            injection_id=injection_id,
            source="wedge_test",
        )

        # Assert — delivery_only, no revival.
        assert result is not None
        assert result["shape"] == "delivery_only", (
            f"T3 idempotency: a live carrier exists → seam must "
            f"return delivery_only (NOT c_revival). Got "
            f"shape={result['shape']!r}"
        )

        # Assert — only ONE PENDING carrier (the pre-existing one).
        repo = TaskRepository(engine, on_pending_task=lambda: None)
        live_carriers = repo.list_live_process_report_carriers_for_instance(
            instance_id=parent_id,
        )
        matching = [
            c for c in live_carriers
            if c.message_id == report_message_id
        ]
        assert len(matching) == 1, (
            f"T3 idempotency: no duplicate carrier should be created. "
            f"Got {len(matching)} PENDING carriers."
        )

        # Assert — worker pool NOT notified (no revival happened).
        worker_pool.notify_work.assert_not_called()


# ─── T3 — Sub-shape (c) carrier-revival (async) ──────────────────────────────


class TestSubshapeCCarrierRevivalAsync:
    """T3 async: ``_reconcile_deferred_report_async`` shares the
    revival contract — the async variant must produce the same
    c_revival shape when called from the router path.
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

        holder: Any = type("AsyncHolder", (), {})()
        holder.engine = engine
        holder._worker_pool = worker_pool
        holder._write_guard = WritePauseGuard()
        holder._session_scope = _session_scope
        holder._reconcile_deferred_report_async = MethodType(
            InstanceManager._reconcile_deferred_report_async, holder,
        )
        # Wedge-fix helpers (Fix 3 — see sync sibling).
        holder._has_live_process_report_carrier = MethodType(
            InstanceManager._has_live_process_report_carrier, holder,
        )
        holder._is_parent_alive = MethodType(
            InstanceManager._is_parent_alive, holder,
        )
        return holder

    @pytest.mark.asyncio
    async def test_async_revives_carrier_when_alive_parent_no_live_carrier(
        self, engine,
    ):
        parent_id = _seed_instance(
            engine, status=InstanceStatus.WAITING_CHILDREN.value,
        )
        child_id = _seed_instance(
            engine,
            instance_id="child-t3-async",
            status=InstanceStatus.COMPLETED.value,
        )
        report_message_id = "msg-t3-async"
        injection_id = "inj-t3-async"
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
        _seed_process_report_task(
            engine,
            instance_id=parent_id,
            message_id=report_message_id,
            status=TaskStatus.CANCELLED.value,
        )

        worker_pool = MagicMock()
        worker_pool.notify_work = MagicMock()
        holder = self._build_holder(engine, worker_pool)

        result = await holder._reconcile_deferred_report_async(
            child_instance_id=child_id,
            child_message_id="child-msg-test",
            injection_id=injection_id,
            source="router",
        )

        assert result is not None
        assert result["shape"] == "c_revival", (
            f"T3 async contract: sub-shape (c) revival must produce "
            f"shape=c_revival. Pre-fix (pre-both-variants-fix) the "
            f"async variant was a delivery_only no-op. Got "
            f"shape={result['shape']!r}"
        )

        # Worker pool notified (post-commit, OUTSIDE the txn).
        worker_pool.notify_work.assert_called_once()


# ─── T4 — Wedge-fix backstop in WaitingChildrenWatchdog ──────────────────────


class TestWedgeBackstop:
    """T4: ``WaitingChildrenWatchdog`` wedge-pass detects WC parents
    with ZERO non-terminal children AND ZERO live carriers, and
    enqueues a wedge notice via the wake path. A live carrier
    silences the backstop (composition property).
    """

    @pytest.mark.asyncio
    async def test_wedge_notice_enqueued_when_no_carrier(
        self, engine, monkeypatch,
    ):
        """WC parent + zero non-terminal children + zero live
        carrier → wedge notice enqueued + backstop episode recorded.
        """
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )

        repo = SQLModelInstanceRepository(engine=engine)
        # Seed an alive WC parent with NO children.
        parent_id = _seed_instance(
            engine,
            instance_id="parent-t4-wedged",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )

        # Manager mock that records enqueue_message calls.
        manager = AsyncMock()
        manager.enqueue_message = AsyncMock()
        manager._task_repo = TaskRepository(engine, on_pending_task=lambda: None)

        watchdog = WaitingChildrenWatchdog(
            instance_repository=repo,
            manager=manager,
            interval_seconds=3600,
            hang_threshold_seconds=3600,
            task_repository=manager._task_repo,
        )

        stats = await watchdog.run_once()

        # Assert — wedge notice was enqueued.
        assert manager.enqueue_message.await_count == 1, (
            f"T4 contract: WC parent + zero children + zero carrier "
            f"must enqueue EXACTLY ONE wedge notice. Got "
            f"{manager.enqueue_message.await_count} calls."
        )
        call_kwargs = manager.enqueue_message.await_args.kwargs
        assert call_kwargs["source"] == WEDGE_SOURCE
        assert call_kwargs["instance_id"] == parent_id
        assert "wedge" in call_kwargs["message"].lower()

        # Assert — wedge episode recorded in the cooldown set.
        assert parent_id in watchdog.wedge_episodes

        # Assert — wedge counters reflect the enqueue. Counters
        # live on the watchdog (not the run_once stats dict) so
        # the 4-key stats contract stays intact for the existing
        # hang-detection tests.
        assert watchdog.wedge_notices_enqueued == 1
        assert watchdog.wedge_parents_scanned >= 1

    @pytest.mark.asyncio
    async def test_wedge_silent_when_live_carrier_present(
        self, engine,
    ):
        """Composition property: WC parent + live PROCESS_REPORT
        carrier → backstop stays silent (no wedge notice).
        """
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )

        repo = SQLModelInstanceRepository(engine=engine)
        parent_id = _seed_instance(
            engine,
            instance_id="parent-t4-healthy",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        # Insert a live PROCESS_REPORT carrier for the parent.
        task_repo = TaskRepository(engine, on_pending_task=lambda: None)
        _seed_process_report_task(
            engine,
            instance_id=parent_id,
            message_id="msg-t4-healthy",
            status=TaskStatus.PENDING.value,
        )

        manager = AsyncMock()
        manager.enqueue_message = AsyncMock()

        watchdog = WaitingChildrenWatchdog(
            instance_repository=repo,
            manager=manager,
            interval_seconds=3600,
            hang_threshold_seconds=3600,
            task_repository=task_repo,
        )

        stats = await watchdog.run_once()

        # Assert — NO wedge notice enqueued (the live carrier
        # silences the backstop; composition property).
        assert manager.enqueue_message.await_count == 0, (
            f"T4 composition property: a live carrier must silence "
            f"the wedge backstop. Got "
            f"{manager.enqueue_message.await_count} enqueue calls."
        )
        assert watchdog.wedge_notices_enqueued == 0
        assert parent_id not in watchdog.wedge_episodes

    @pytest.mark.asyncio
    async def test_wedge_idempotent_across_ticks(
        self, engine,
    ):
        """Anti-spam: a second tick on the same wedged parent does
        NOT re-notify (cooldown is per-parent).
        """
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )

        repo = SQLModelInstanceRepository(engine=engine)
        parent_id = _seed_instance(
            engine,
            instance_id="parent-t4-idempotent",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )

        manager = AsyncMock()
        manager.enqueue_message = AsyncMock()
        manager._task_repo = TaskRepository(engine, on_pending_task=lambda: None)

        watchdog = WaitingChildrenWatchdog(
            instance_repository=repo,
            manager=manager,
            interval_seconds=3600,
            hang_threshold_seconds=3600,
            task_repository=manager._task_repo,
        )

        # First tick — wedge notice enqueued.
        stats1 = await watchdog.run_once()
        assert watchdog.wedge_notices_enqueued == 1

        # Second tick — same wedge, same parent, no fresh notice.
        stats2 = await watchdog.run_once()
        assert watchdog.wedge_notices_enqueued == 1, (
            f"T4 anti-spam: a second tick on the same wedged parent "
            f"must NOT re-notify. Lifetime wedge counter should stay "
            f"at 1. Got {watchdog.wedge_notices_enqueued}."
        )
        # Exactly ONE total enqueue across both ticks.
        assert manager.enqueue_message.await_count == 1

    @pytest.mark.asyncio
    async def test_wedge_silent_when_live_children_present(
        self, engine,
    ):
        """T4 healthy-parent guard (wedge-fix children gate): a WC
        parent with at least one NON-TERMINAL child must NOT fire
        a wedge notice, even with zero live carrier.

        Carriers are only created at child completion
        (``daemon/services/child_reports.py:2844-2852``), so a
        healthy WC parent waiting on still-running children has
        no carrier yet — without the children gate the backstop
        would fire a spurious notice whose playbook recommends
        terminate-and-respawn, which would orphan in-flight
        children.

        Pre-fix the wedge predicate implemented only 2 of the 3
        promised conditions (WC parent + no live carrier); the
        missing ``zero non-terminal children`` gate caused
        spurious wedge notices. Post-fix this test pins the
        healthy-parent guard.
        """
        from daemon.repositories.instance.models import Instance
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )

        repo = SQLModelInstanceRepository(engine=engine)
        parent_id = _seed_instance(
            engine,
            instance_id="parent-t4-healthy-children",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        # Seed a non-terminal RUNNING child whose parent_id
        # references the WC parent. The wedge-fix children gate
        # (``repository.parents_with_non_terminal_children``) must
        # classify this parent as "has non-terminal children" and
        # the watchdog must stay silent.
        child_id = "child-t4-healthy-running"
        with SQLModelSessionLib(engine) as s:
            s.add(Instance(
                instance_id=child_id,
                agent_id="leader",
                agent_dir="/tmp/leader",
                agent_name="leader",
                parent_id=parent_id,
                status=InstanceStatus.RUNNING.value,
                version=1,
                instance_metadata={},
            ))
            s.commit()

        # No PROCESS_REPORT carrier is seeded — pre-fix the wedge
        # predicate would have fired a spurious notice here (WC +
        # no carrier). Post-fix the children gate silences it
        # before the carrier check.
        task_repo = TaskRepository(engine, on_pending_task=lambda: None)
        manager = AsyncMock()
        manager.enqueue_message = AsyncMock()

        watchdog = WaitingChildrenWatchdog(
            instance_repository=repo,
            manager=manager,
            interval_seconds=3600,
            hang_threshold_seconds=3600,
            task_repository=task_repo,
        )

        stats = await watchdog.run_once()

        # Assert — NO wedge notice enqueued. The healthy parent
        # with live children must stay silent; the backstop is
        # reserved for GENUINE wedges (WC + zero non-terminal
        # children + zero live carrier).
        assert manager.enqueue_message.await_count == 0, (
            f"T4 healthy-parent guard: a WC parent with live "
            f"non-terminal children must NOT fire a wedge notice "
            f"(the children gate silences the backstop). Pre-fix "
            f"this fired because the predicate only checked "
            f"WC + no-carrier. Got "
            f"{manager.enqueue_message.await_count} enqueue calls."
        )
        assert watchdog.wedge_notices_enqueued == 0
        assert parent_id not in watchdog.wedge_episodes

    def test_wedge_notice_content(self):
        """Sanity — the wedge notice builder produces a directive,
        terse message that names the parent (truncated to 8 chars,
        matching the production log convention).
        """
        notice = _build_wedge_notice("parent-abc12345-test")
        assert "wedge notice" in notice.lower()
        # Parent is truncated to 8 chars in the message body
        # (matches the production log convention at manager.py:7178
        # for sub-shape c).
        assert "parent-a" in notice
        assert "PROCESS_REPORT" in notice
        assert "playbook" in notice.lower()


# ─── T2b alive-status membership pin ──────────────────────────────────────────


class TestAliveInstanceStatusesMembership:
    """Pin the exact membership of
    ``daemon.constants.ALIVE_INSTANCE_STATUSES``.

    The set is the canonical alive-instance guard used by the
    manager (``manager.py:6965-6982``) and the reconciler
    (``job_recovery_service.py:25, 168``). Any drift between the
    set definition and the consumers breaks the wedge-fix
    alive-instance guard (T2b), the manager's revive semantics,
    and the reconciler's Pattern (d) skip path. This test makes
    drift fail at unit-test time instead of in production.

    Companion to the behavioral T2b at
    ``tests/job_queue/test_seam_invariants.py:3413``
    (``test_reconciler_pattern_d_skips_alive_instance_with_terminal_job``):
    that test pins the BEHAVIOR (alive WC parent + terminal own-
    linkage JobItem → NOT cancelled), this test pins the MEMBERSHIP
    (the exact five statuses). Both are needed — the behavioral
    test could pass with a wrong-but-still-correct subset, and the
    membership test passes even if the behavior regresses.
    """

    def test_alive_instance_statuses_membership(self):
        from daemon.constants import ALIVE_INSTANCE_STATUSES

        assert ALIVE_INSTANCE_STATUSES == frozenset({
            "idle",
            "running",
            "paused",
            "queued",
            "waiting_children",
        }), (
            f"ALIVE_INSTANCE_STATUSES membership drifted from the "
            f"pre-hoist local definition. Any change to this set "
            f"must be made in lockstep with the consumers at "
            f"manager.py:6965-6982 and job_recovery_service.py:168 "
            f"(and reflected in the T2b behavioral test at "
            f"test_seam_invariants.py:3413). Got: "
            f"{sorted(ALIVE_INSTANCE_STATUSES)}"
        )

    def test_alive_instance_statuses_is_frozenset(self):
        """Defensive — the contract is a frozenset, not a mutable
        set. A consumer-side mutation would silently break the
        manager's ``status in ALIVE_INSTANCE_STATUSES`` check.
        """
        from daemon.constants import ALIVE_INSTANCE_STATUSES

        assert isinstance(ALIVE_INSTANCE_STATUSES, frozenset), (
            f"ALIVE_INSTANCE_STATUSES must be a frozenset to "
            f"prevent consumer-side mutation. Got: "
            f"{type(ALIVE_INSTANCE_STATUSES).__name__}"
        )


# ─── T1 + T2 — Pattern (d) regressions live in test_seam_invariants.py ───────
# (Kept in the seam-invariant file because Pattern (d) is the
# reconciler-level contract; this file focuses on the carrier-revival
# + watchdog-backstop seams which are not covered there.)
