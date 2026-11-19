"""Reconciler wedge-fix regression tests (T1, T2, T3, T4).

Module-size rationale (band 1000-3000): cohesive single module; not split.

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
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session as SQLModelSessionLib
from sqlmodel import SQLModel
from sqlmodel import select as sqlmodel_select

from daemon.repositories.dependency_bus import (
    DependencyWatcher,
    DependencyWatcherRepository,
)
from daemon.repositories.event.models import Event
from daemon.repositories.instance.models import (
    Instance,
    InstanceStatus,
)
from daemon.repositories.instance.repository import SQLModelInstanceRepository
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
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.services.error_reporting import ErrorReportingService
from daemon.services.waiting_children_watchdog import (
    WEDGE_NOTICE_EMISSION_KEY,
    WEDGE_REASON_NO_LIVE_CARRIER,
    WEDGE_SOURCE,
    WaitingChildrenWatchdog,
    _build_wedge_notice,
)
from daemon.write_pause_guard import WritePauseGuard


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
        # Phase2 wake-site widening (``daemon/manager.py:6805``)
        # — sub-shape (c) revival calls ``self._notify_all_pools()``
        # at manager.py:8916 AFTER the explicit commit. The bare
        # ``type(...)`` holder has no such attribute pre-fix; bind
        # a fan-out stub that delegates to the single
        # ``_worker_pool`` the test holds.
        def _notify(self) -> None:
            if self._worker_pool is not None:
                try:
                    self._worker_pool.notify_work()
                except Exception:
                    pass
        holder._notify_all_pools = MethodType(_notify, holder)
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
        # Phase2 wake-site widening — see SyncHolder sibling for
        # the rationale. Async mirror of the c_revival wake call.
        def _notify(self) -> None:
            if self._worker_pool is not None:
                try:
                    self._worker_pool.notify_work()
                except Exception:
                    pass
        holder._notify_all_pools = MethodType(_notify, holder)
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

        S4: the notice is COMPOSED from the reason codes — the
        builder asserts ONLY the codes it is handed (the old template
        asserted a queue state the watchdog never read).
        """
        notice = _build_wedge_notice(
            "parent-abc12345-test",
            reason_codes=[WEDGE_REASON_NO_LIVE_CARRIER],
        )
        assert "wedge notice" in notice.lower()
        # Parent is truncated to 8 chars in the message body
        # (matches the production log convention at manager.py:7178
        # for sub-shape c).
        assert "parent-a" in notice
        assert "PROCESS_REPORT" in notice
        assert "playbook" in notice.lower()
        # S4: a code that was NOT observed is NOT claimed.
        assert "queued_report" not in notice
        assert "pending dependency watchers" not in notice


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


# ══════════════════════════════════════════════════════════════════════════
# S1 (wake-path-carrier-fix) — the target-agnostic child-keyed fire
#
# T1 / T1b / T1c / T1d / T1e / T1f / T7.
#
# EVERY assertion below is OBSERVABLE ROW STATE — watcher rows
# (``state`` / ``fired_at`` / ``enqueued_at`` / ``watcher_metadata``),
# ``message_queue`` rows, ``instances.status`` — NEVER a log line and
# never a string grep on the source.
#
# The harness drives the REAL producer paths:
#   * COMPLETED  → ``ChildReportsService._process_child_completion_and_notify_parent``
#                  (the real async entry point → the real DB-sync helper →
#                  the real ``_dispatch_post_commit_side_effects``)
#   * TOOL-INVOCATION → the same real entry point, with the tool-lane
#                  fixture (``invoked_as_tool``)
#   * ROOT       → the same real entry point, with a root child
#                  (``parent_id is None``)
#   * ERROR      → ``ErrorReportingService._send_error_report``
#
# The watchers are armed through the REAL arm lanes
# (``daemon.tools.instance_ops._register_nudge_completion_watcher`` for the
# nudge lane, ``daemon.tools.instance._register_child_completion_watcher``
# for the spawn lane) — never by inserting rows by hand.
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def s1_bus(engine):
    """A REAL ``DependencyBus`` over the real repository.

    ``bus.start()`` is deliberately NOT called: the S1 fire and the
    delivery both read the DB directly (the in-memory ``_pending``
    cache is a hot-path optimization only), and the boot-time orphan
    sweep would cancel the seeded watchers (their source task is
    already terminal at the moment of the child's transition — the
    production shape). The singleton is registered so the production
    code paths find it, and cleared after the test.
    """
    bus = DependencyBus(DependencyWatcherRepository(engine))
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


class _RecordingEnqueue:
    """A fake ``manager.enqueue_message`` that writes REAL rows.

    Two jobs:

    1. Write a real ``message_queue`` row so the tests can assert on
       OBSERVABLE ROW STATE (the bus-sourced delivery rows) instead of
       on mock call counts.
    2. Snapshot the delivery-marker state (``watcher_metadata``
       ``delivered_watch_id``) of every watcher row AT ENQUEUE TIME.
       That snapshot is what proves the two-transaction ordering of
       the DELIVERY-ONCE marker: the marker must already be committed
       when the enqueue happens (the marker is committed BEFORE the
       enqueue, in its own transaction).
    """

    def __init__(self, engine):
        self.engine = engine
        self.calls: list[dict] = []
        self.marker_snapshots: list[dict[str, bool]] = []

    def _next_message_id(self) -> str:
        """The message id factory (a hook — S2 overrides it to make
        the enqueue lane's message id deterministic so a Task row can
        be pre-seeded for the watcher mint's ``get_by_message``
        resolution)."""
        return f"mq-{uuid.uuid4().hex[:12]}"

    async def __call__(
        self,
        *,
        instance_id: str,
        message: str,
        source: str = "api",
        priority: int = 1,
        images=None,
        metadata=None,
        **kwargs,
    ):
        meta = dict(metadata or {})
        with SQLModelSessionLib(self.engine) as s:
            markers = {
                row.watch_id: bool(
                    (row.watcher_metadata or {}).get("delivered_watch_id")
                )
                for row in s.exec(sqlmodel_select(DependencyWatcher))
            }
            message_id = self._next_message_id()
            s.add(
                MessageQueue(
                    message_id=message_id,
                    instance_id=instance_id,
                    content=message,
                    source=source,
                    type=MessageType.COMPLETION_REPORT.value,
                    status=MessageStatus.READY.value,
                    priority=priority,
                    enqueued_at=datetime.now(timezone.utc),
                    message_metadata=meta,
                )
            )
            s.commit()
        self.calls.append(
            {
                "message_id": message_id,
                "instance_id": instance_id,
                "source": source,
                "metadata": meta,
                "message": message,
            }
        )
        self.marker_snapshots.append(markers)
        return SimpleNamespace(
            message_id=message_id,
            instance_id=instance_id,
            status=MessageStatus.READY.value,
        )


@pytest.fixture
def s1_manager(engine):
    """A manager mock wired to the REAL engine + repositories.

    Mirrors ``tests/test_child_completion_pending_task_guard.py``'s
    minimal manager (engine + write_guard) and adds the pieces the
    completion / error lanes read: the instance + task repositories,
    the live hub, the queue repository (the error lane's dedup read)
    and the recording enqueue.
    """
    mgr = MagicMock()
    mgr.engine = engine
    mgr._engine = engine
    mgr.write_guard = WritePauseGuard()
    mgr._instance_repository = SQLModelInstanceRepository(engine=engine)
    mgr._task_repo = TaskRepository(engine, on_pending_task=lambda: None)
    mgr._live_hub = MagicMock()
    mgr._live_hub.stream_status_change = AsyncMock()
    mgr._live_hub.stream_lifecycle = AsyncMock()
    mgr._checkpointer = None
    mgr.config = MagicMock()
    mgr.config.report_repair = MagicMock()
    mgr.config.report_repair.enabled = False
    mgr.config.report_repair.repair_excluded_agents = []
    mgr.config.report_repair.size_ratio_threshold = 5.0
    mgr.config.report_repair.lookback_messages = 3
    mgr._report_injection_pending = set()
    # No mission-record service: the spawn arm lane would otherwise mint a
    # MagicMock commission id and the FollowUp payload could not be
    # JSON-serialized (the bus stores the payload as JSONB).
    mgr.mission_record_service = None
    queue_repo = MagicMock()
    queue_repo.list = MagicMock(return_value=[])
    mgr._queue_repository = queue_repo
    mgr.enqueue_message = _RecordingEnqueue(engine)
    return mgr


@pytest.fixture
def s1_service(s1_manager):
    svc = ChildReportsService(manager=s1_manager, events_service=None)
    s1_manager._child_reports_service = svc
    return svc


@pytest.fixture
def s1_error_service(s1_manager):
    return ErrorReportingService(manager=s1_manager, events_service=None)


def _s1_seed_instance(
    engine,
    instance_id: str,
    *,
    parent_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    invoked_as_tool: bool = False,
) -> str:
    """Insert one Instance row directly via SQLModel."""
    meta = {"invoked_as_tool": True} if invoked_as_tool else {}
    with SQLModelSessionLib(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="tester",
                agent_dir="/tmp/tester",
                agent_name="tester",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata=meta,
            )
        )
        s.commit()
    return instance_id


def _s1_seed_task(
    engine,
    *,
    instance_id: str,
    message_id: str,
    status: str = TaskStatus.RUNNING.value,
) -> None:
    """Insert one Task row (the message→task key the arm lanes resolve)."""
    with SQLModelSessionLib(engine) as s:
        s.add(
            Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=instance_id,
                message_id=message_id,
                status=status,
                created_at=datetime.now(timezone.utc),
            )
        )
        s.commit()


async def _s1_arm_nudge(
    manager,
    engine,
    *,
    nudger_instance_id: str,
    worker_instance_id: str,
    task_message_id: str = "nudge-msg-1",
) -> str:
    """Arm a watcher through the REAL nudge lane.

    ``_register_nudge_completion_watcher`` is the production mint for
    the nudge lane: the watcher's TARGET is the NUDGER (not the
    child's structural parent) and its payload carries
    ``metadata.child_id`` — the key the S1 matcher keys on.

    The arm uses the ``injection`` lane with a RUNNING task on the
    worker: that is the production shape at the moment the worker
    reaches its terminal transition (its turn has run), and a RUNNING
    task does not trip the completion producer's pending-task guard.
    """
    from daemon.tools.instance_ops import _register_nudge_completion_watcher

    _s1_seed_task(
        engine,
        instance_id=worker_instance_id,
        message_id=task_message_id,
        status=TaskStatus.RUNNING.value,
    )
    minted = await _register_nudge_completion_watcher(
        manager,
        nudger_instance_id=nudger_instance_id,
        worker_instance_id=worker_instance_id,
        message_id=task_message_id,
        lane="injection",
    )
    assert minted is True, (
        f"T1 harness: the nudge arm lane must mint a watcher "
        f"(nudger={nudger_instance_id!r}, worker={worker_instance_id!r})"
    )
    return task_message_id


async def _s1_arm_spawn(
    manager,
    engine,
    *,
    parent_instance_id: str,
    child_instance_id: str,
    message_id: str = "spawn-msg-1",
) -> str:
    """Arm a watcher through the REAL spawn lane (the healthy path).

    ``_register_child_completion_watcher`` is the production mint for
    the ``send_message`` / spawn lane: the watcher's TARGET is the
    child's STRUCTURAL parent — the waiter the report lane owns.
    """
    from daemon.tools.instance import _register_child_completion_watcher

    _s1_seed_task(
        engine,
        instance_id=child_instance_id,
        message_id=message_id,
        status=TaskStatus.RUNNING.value,
    )
    result = await _register_child_completion_watcher(
        manager,
        parent_instance_id=parent_instance_id,
        child_instance_id=child_instance_id,
        message_id=message_id,
    )
    assert result is None, (
        f"T1b harness: the spawn arm lane must mint a watcher without "
        f"error (got {result!r})"
    )
    return message_id


def _s1_watcher_rows(engine) -> list:
    """Every watcher row, as plain rows (state / fired_at / metadata)."""
    with SQLModelSessionLib(engine) as s:
        return list(s.exec(sqlmodel_select(DependencyWatcher)))


def _s1_watcher_for(engine, target_instance_id: str, child_id: str):
    """The watcher row armed for ``child_id`` whose target is ``target``."""
    for row in _s1_watcher_rows(engine):
        meta = (row.follow_up_payload or {}).get("metadata") or {}
        if (
            row.target_instance_id == target_instance_id
            and meta.get("child_id") == child_id
        ):
            return row
    return None


def _s1_bus_delivery_rows(engine, watch_id: str) -> list:
    """The message_queue rows the BUS delivered for ``watch_id``.

    A bus delivery is identifiable by the S1 enrichment the fire
    stamps onto the FollowUp metadata (``watch_id``) — the report
    lane's own rows never carry it.
    """
    with SQLModelSessionLib(engine) as s:
        return [
            row
            for row in s.exec(sqlmodel_select(MessageQueue))
            if (row.message_metadata or {}).get("watch_id") == watch_id
        ]


def _s1_rows_for_instance(engine, instance_id: str) -> list:
    with SQLModelSessionLib(engine) as s:
        return [
            row
            for row in s.exec(sqlmodel_select(MessageQueue))
            if row.instance_id == instance_id
        ]


def _s1_event_rows(engine, instance_id: str) -> list:
    """The Event rows written for ``instance_id`` (lifecycle footprint).

    The lane discriminator for T1c / T1f: the REGULAR completion
    lane writes an ``instance_completed`` event for the child (and a
    ``child_completed`` event for the structural parent); the TOOL and
    ROOT lanes write NEITHER (they skip the parent notification). The
    reader SEES producer-written event rows through this file's
    in-memory engine (the regular lane's two events are visible), so
    their absence is observable evidence — not a blind assertion.
    """
    with SQLModelSessionLib(engine) as s:
        return [
            row
            for row in s.exec(sqlmodel_select(Event))
            if row.instance_id == instance_id
        ]


class TestS1TerminalChildFire:
    """S1 — the target-agnostic child-keyed fire on the terminal lanes.

    The defect being closed: the bus's three fire primitives were
    keyed on the child's FINAL task (``emit_terminal``) or on the
    (STRUCTURAL parent, child) PAIR (``emit_terminal_for_child_instance``),
    and the only target-agnostic child-keyed primitive
    (``fire_for_terminated_target``) was reachable ONLY from the
    TERMINATE path. A watcher armed by a NUDGER therefore could never
    be fired on the COMPLETED / TOOL-INVOCATION / ERROR lanes — its
    target's pending count never dropped and the waiter stayed parked
    forever.
    """

    @pytest.mark.asyncio
    async def test_t1_completed_lane_fires_the_nudge_armed_watcher(
        self, engine, s1_bus, s1_manager, s1_service,
    ):
        """T1 — a COMPLETED child fires the nudge-armed watcher.

        Harness: the REAL entry point
        ``_process_child_completion_and_notify_parent`` (→ the real
        DB-sync helper → the real
        ``_dispatch_post_commit_side_effects``).

        Asserted OBSERVABLE STATE: the watcher's ``state`` /
        ``fired_at``, the watcher's target's PENDING count, the
        delivered ``message_queue`` row, and the DELIVERY-ONCE marker.
        """
        # The waiter (the nudger) and the child's STRUCTURAL parent
        # are DIFFERENT instances — that difference is the whole point:
        # the pair-keyed emit (F2) can never reach this watcher.
        _s1_seed_instance(engine, "s1-nudger")
        _s1_seed_instance(engine, "s1-structural-parent")
        _s1_seed_instance(engine, "s1-child", parent_id="s1-structural-parent")

        await _s1_arm_nudge(
            s1_manager,
            engine,
            nudger_instance_id="s1-nudger",
            worker_instance_id="s1-child",
        )

        watcher = _s1_watcher_for(engine, "s1-nudger", "s1-child")
        assert watcher is not None, (
            "T1 harness: the nudge lane must have armed a watcher for "
            "the child"
        )
        watch_id = watcher.watch_id
        assert watcher.state == "PENDING", (
            f"T1 harness: the watcher must start PENDING (got {watcher.state!r})"
        )

        # ── Drive the REAL producer path.
        await s1_service._process_child_completion_and_notify_parent(
            "s1-child", "s1-final-msg"
        )

        fired = _s1_watcher_for(engine, "s1-nudger", "s1-child")
        assert fired is not None, "T1: the watcher row must still exist"
        assert fired.state == "FIRED", (
            f"T1: a COMPLETED child must FIRE the nudge-armed watcher "
            f"(state={fired.state!r})"
        )
        assert fired.fired_at is not None, (
            "T1: the fired watcher must carry a fired_at timestamp"
        )
        # The park gate: the watcher's target no longer counts this
        # watcher ⇒ its PENDING count dropped to zero.
        assert s1_bus.count_pending_for_target_sync("s1-nudger") == 0, (
            "T1: the watcher's target must have zero PENDING watchers "
            "after the fire"
        )
        # The delivery: exactly ONE bus-sourced message_queue row for
        # this watcher.
        delivered = _s1_bus_delivery_rows(engine, watch_id)
        assert len(delivered) == 1, (
            f"T1: exactly ONE bus delivery expected for the fired "
            f"watcher (got {len(delivered)})"
        )
        assert delivered[0].instance_id == "s1-nudger", (
            "T1: the delivery must land on the watcher's target"
        )
        # The DELIVERY-ONCE marker (the distinct ``delivered_watch_id``
        # key in the EXISTING watcher_metadata JSONB).
        marker = (fired.watcher_metadata or {}).get("delivered_watch_id")
        assert marker == watch_id, (
            f"T1: the delivery marker must be keyed to the watch_id "
            f"(got {marker!r})"
        )

    @pytest.mark.asyncio
    async def test_t1b_healthy_spawn_path_delivers_nothing_to_the_parent(
        self, engine, s1_bus, s1_manager, s1_service,
    ):
        """T1b — the healthy spawn path stays bus-free for the parent.

        The structural parent armed the watcher (the REAL spawn arm
        lane) ⇒ the watcher's target IS the child's structural parent
        ⇒ ``DELIVER(w)`` is false ⇒ the bus must NOT deliver a
        FollowUp to it (the report lane owns that waiter).
        """
        _s1_seed_instance(engine, "s1b-structural-parent")
        _s1_seed_instance(engine, "s1b-child", parent_id="s1b-structural-parent")

        await _s1_arm_spawn(
            s1_manager,
            engine,
            parent_instance_id="s1b-structural-parent",
            child_instance_id="s1b-child",
        )

        watcher = _s1_watcher_for(engine, "s1b-structural-parent", "s1b-child")
        assert watcher is not None, (
            "T1b harness: the spawn lane must have armed a watcher for "
            "the child"
        )
        watch_id = watcher.watch_id

        await s1_service._process_child_completion_and_notify_parent(
            "s1b-child", "s1b-final-msg"
        )

        fired = _s1_watcher_for(engine, "s1b-structural-parent", "s1b-child")
        assert fired is not None, "T1b: the watcher row must still exist"
        assert fired.state == "FIRED", (
            f"T1b: the healthy path must still fire the parent's watcher "
            f"(state={fired.state!r})"
        )
        assert fired.fired_at is not None, (
            "T1b: the fired watcher must carry a fired_at timestamp"
        )
        # The fence: NO bus-sourced delivery to the structural parent.
        assert _s1_bus_delivery_rows(engine, watch_id) == [], (
            "T1b: the structural parent must receive NO bus FollowUp on "
            "the healthy path (DELIVER(w) is false)"
        )
        bus_sourced = [
            row
            for row in _s1_rows_for_instance(engine, "s1b-structural-parent")
            if (row.message_metadata or {}).get("watch_id")
        ]
        assert bus_sourced == [], (
            "T1b: no message_queue row carrying the bus enrichment may "
            f"exist for the structural parent (got {len(bus_sourced)})"
        )
        # The supplementary fire did not claim a delivery for that row
        # (the row was already FIRED by the healthy path's own emits).
        assert not (fired.watcher_metadata or {}).get("delivered_watch_id"), (
            "T1b: the structural parent's watcher must NOT carry the "
            "delivery marker (the bus never delivered it)"
        )

    @pytest.mark.asyncio
    async def test_t1c_tool_invocation_lane_fires_the_watcher(
        self, engine, s1_bus, s1_manager, s1_service,
    ):
        """T1c — the TOOL-INVOCATION lane fires the watcher too.

        THE CENSUS GAP: that branch emits NO bus terminal of its own,
        so before S1 a watcher waiting on a tool-invocation child could
        never be fired at all.

        LANE SELECTION vs LANE EVIDENCE — they are not the same thing,
        and only the first one is available here:

        * The lane is SELECTED by the fixture: ``invoked_as_tool=True``
          is the producer's tool-lane selector
          (``child_reports.py:4603``) and the harness drives the REAL
          entry point, so the tool branch is the one that ran.
        * ``instances.status`` is NOT lane-discriminating: the REGULAR
          branch stamps COMPLETED too (``child_reports.py:4806``). The
          status assert below is therefore a HARNESS SANITY CHECK (the
          child reached its terminal stamp), NOT evidence of which
          producer branch ran. (Measured on a file-backed SQLite: the
          regular lane's child ends COMPLETED *with* a parent-report
          message, a PROCESS_REPORT task and a report-injection
          obligation for the structural parent; the tool lane's child
          ends COMPLETED *without* any of them.)
        * The lane IS evidenced by the LIFECYCLE-EVENT footprint: the
          regular lane writes an ``instance_completed`` event for the
          child and a ``child_completed`` event for the structural
          parent; the tool lane writes NEITHER (it skips the parent
          notification). The absence asserted below is therefore a real
          discriminator in this harness — the reader demonstrably SEES
          producer-written event rows (the regular lane's two are
          visible through the same in-memory engine).
        """
        _s1_seed_instance(engine, "s1c-nudger")
        _s1_seed_instance(engine, "s1c-structural-parent")
        _s1_seed_instance(
            engine,
            "s1c-child",
            parent_id="s1c-structural-parent",
            invoked_as_tool=True,
        )

        await _s1_arm_nudge(
            s1_manager,
            engine,
            nudger_instance_id="s1c-nudger",
            worker_instance_id="s1c-child",
        )
        watcher = _s1_watcher_for(engine, "s1c-nudger", "s1c-child")
        assert watcher is not None, "T1c harness: the watcher must exist"
        watch_id = watcher.watch_id

        await s1_service._process_child_completion_and_notify_parent(
            "s1c-child", "s1c-final-msg"
        )

        with SQLModelSessionLib(engine) as s:
            child = s.get(Instance, "s1c-child")
        assert child.status == InstanceStatus.COMPLETED.value, (
            f"T1c harness: the child must have reached its terminal stamp "
            f"(COMPLETED, got {child.status!r})"
        )
        # LANE DISCRIMINATOR — NOT the status (the regular lane stamps
        # COMPLETED too, ``child_reports.py:4806``). The tool branch
        # writes NO lifecycle event: the REGULAR lane writes
        # ``instance_completed`` for the child and ``child_completed``
        # for the structural parent, the tool lane writes NEITHER (it
        # skips the parent notification). The reader sees the regular
        # lane's events through this same engine, so their absence is
        # observable evidence that the TOOL branch ran.
        assert _s1_event_rows(engine, "s1c-child") == [], (
            f"T1c: the tool lane must write NO lifecycle event for the "
            f"child (the regular lane writes instance_completed) — got "
            f"{_s1_event_rows(engine, 's1c-child')!r}"
        )
        assert _s1_event_rows(engine, "s1c-structural-parent") == [], (
            "T1c: the tool lane must write NO child_completed event for "
            "the structural parent (it skips the parent notification)"
        )
        fired = _s1_watcher_for(engine, "s1c-nudger", "s1c-child")
        assert fired is not None, "T1c: the watcher row must still exist"
        assert fired.state == "FIRED", (
            f"T1c: a tool-invocation child must FIRE the nudge-armed "
            f"watcher (state={fired.state!r})"
        )
        assert fired.fired_at is not None, (
            "T1c: the fired watcher must carry a fired_at timestamp"
        )
        assert s1_bus.count_pending_for_target_sync("s1c-nudger") == 0, (
            "T1c: the watcher's target must have zero PENDING watchers"
        )
        assert len(_s1_bus_delivery_rows(engine, watch_id)) == 1, (
            "T1c: exactly ONE bus delivery expected for the fired watcher"
        )

    @pytest.mark.asyncio
    async def test_t1d_two_nudgers_both_fire_and_a_stale_row_fires_once(
        self, engine, s1_bus, s1_manager, s1_service,
    ):
        """T1d — the plain matcher's semantics.

        Two nudgers on the SAME child are two genuine waiters ⇒ BOTH
        must wake (two waiters, two wakes). The stale-but-PENDING row
        (an earlier episode's watcher on the same child) fires
        EXACTLY ONCE: the guarded ``WHERE state='PENDING'`` UPDATE
        makes every later fire a no-op, so its ``fired_at`` is stable
        and it never delivers twice.
        """
        _s1_seed_instance(engine, "s1d-nudger-a")
        _s1_seed_instance(engine, "s1d-nudger-b")
        _s1_seed_instance(engine, "s1d-stale-nudger")
        _s1_seed_instance(engine, "s1d-structural-parent")
        _s1_seed_instance(engine, "s1d-child", parent_id="s1d-structural-parent")

        await _s1_arm_nudge(
            s1_manager,
            engine,
            nudger_instance_id="s1d-nudger-a",
            worker_instance_id="s1d-child",
            task_message_id="s1d-msg-a",
        )
        await _s1_arm_nudge(
            s1_manager,
            engine,
            nudger_instance_id="s1d-nudger-b",
            worker_instance_id="s1d-child",
            task_message_id="s1d-msg-b",
        )
        # The STALE row: armed in an earlier episode on the same child
        # (its source task is already terminal — the live row 8a1f60d7
        # shape). It is a genuine PENDING waiter by the plain matcher.
        await _s1_arm_nudge(
            s1_manager,
            engine,
            nudger_instance_id="s1d-stale-nudger",
            worker_instance_id="s1d-child",
            task_message_id="s1d-msg-stale",
        )

        row_a = _s1_watcher_for(engine, "s1d-nudger-a", "s1d-child")
        row_b = _s1_watcher_for(engine, "s1d-nudger-b", "s1d-child")
        row_stale = _s1_watcher_for(engine, "s1d-stale-nudger", "s1d-child")
        assert row_a and row_b and row_stale, (
            "T1d harness: all three watchers must be armed"
        )

        await s1_service._process_child_completion_and_notify_parent(
            "s1d-child", "s1d-final-msg"
        )

        for label, row in (
            ("nudger-a", row_a),
            ("nudger-b", row_b),
            ("stale", row_stale),
        ):
            fresh = _s1_watcher_for(engine, row.target_instance_id, "s1d-child")
            assert fresh.state == "FIRED", (
                f"T1d: the {label} waiter must be FIRED "
                f"(state={fresh.state!r})"
            )
            assert fresh.fired_at is not None, (
                f"T1d: the {label} waiter must carry a fired_at timestamp"
            )

        # Each waiter got exactly ONE delivery.
        for label, row in (
            ("nudger-a", row_a),
            ("nudger-b", row_b),
            ("stale", row_stale),
        ):
            delivered = _s1_bus_delivery_rows(engine, row.watch_id)
            assert len(delivered) == 1, (
                f"T1d: the {label} waiter must receive exactly ONE "
                f"delivery (got {len(delivered)})"
            )

        # The stale row fires EXACTLY ONCE: a second terminal transition
        # must not re-stamp it and must not deliver again.
        stale_fired_at = _s1_watcher_for(
            engine, "s1d-stale-nudger", "s1d-child"
        ).fired_at
        await s1_service._fire_terminal_child_and_deliver(
            "s1d-child", "completed"
        )
        stale_after = _s1_watcher_for(engine, "s1d-stale-nudger", "s1d-child")
        assert stale_after.fired_at == stale_fired_at, (
            "T1d: the stale row must fire exactly once (its fired_at "
            "must not be re-stamped by a later fire)"
        )
        assert len(
            _s1_bus_delivery_rows(engine, stale_after.watch_id)
        ) == 1, (
            "T1d: the stale row must never deliver twice"
        )

    @pytest.mark.asyncio
    async def test_t1e_error_lane_fires_the_nudge_armed_watcher(
        self, engine, s1_bus, s1_manager, s1_service, s1_error_service,
    ):
        """T1e — the ERROR lane fires the watcher too.

        Harness: the REAL ``ErrorReportingService._send_error_report``
        path (not a direct bus call) — the ERROR lane emits F1 + F2
        only, so before S1 a nudge-armed watcher on an erroring child
        could never be fired (strictly worse than the completion
        wedge: the mission-HOLD guards block every commission
        abandonment while a PENDING watcher exists).
        """
        _s1_seed_instance(engine, "s1e-nudger")
        _s1_seed_instance(engine, "s1e-structural-parent")
        _s1_seed_instance(engine, "s1e-child", parent_id="s1e-structural-parent")

        await _s1_arm_nudge(
            s1_manager,
            engine,
            nudger_instance_id="s1e-nudger",
            worker_instance_id="s1e-child",
        )
        watcher = _s1_watcher_for(engine, "s1e-nudger", "s1e-child")
        assert watcher is not None, "T1e harness: the watcher must exist"
        watch_id = watcher.watch_id

        await s1_error_service._send_error_report(
            "s1e-child",
            "boom — the child failed",
            error_type="max_retries_exceeded",
            message_id="s1e-err-msg",
        )

        fired = _s1_watcher_for(engine, "s1e-nudger", "s1e-child")
        assert fired is not None, "T1e: the watcher row must still exist"
        assert fired.state == "FIRED", (
            f"T1e: an erroring child must FIRE the nudge-armed watcher "
            f"(state={fired.state!r})"
        )
        assert fired.fired_at is not None, (
            "T1e: the fired watcher must carry a fired_at timestamp"
        )
        assert s1_bus.count_pending_for_target_sync("s1e-nudger") == 0, (
            "T1e: the watcher's target must have zero PENDING watchers"
        )
        assert len(_s1_bus_delivery_rows(engine, watch_id)) == 1, (
            "T1e: exactly ONE bus delivery expected for the fired watcher"
        )
        assert (fired.watcher_metadata or {}).get(
            "delivered_watch_id"
        ) == watch_id, (
            "T1e: the delivery marker must be keyed to the watch_id"
        )

    @pytest.mark.asyncio
    async def test_t1f_root_child_lane_fires_the_nudge_armed_watcher(
        self, engine, s1_bus, s1_manager, s1_service,
    ):
        """T1f — a ROOT child's nudge-armed watcher fires.

        The root lane emits NO bus terminal of its own (the producer
        stamps ``parent_id=None``), so before S1 a nudge-armed watcher
        on a root child had no fire anywhere on that lane.

        LANE SELECTION vs LANE EVIDENCE:

        * The lane is SELECTED by the fixture: ``parent_id=None`` is the
          producer's root-lane selector (``child_reports.py:4274``) and
          the harness drives the REAL entry point, so the root branch is
          the one that ran.
        * ``instances.status`` is NOT lane-discriminating: the REGULAR
          branch stamps COMPLETED too (``child_reports.py:4806``), so
          the status assert below is a HARNESS SANITY CHECK (the child
          reached its terminal stamp), NOT evidence of which producer
          branch ran.
        * The lane IS evidenced by the LIFECYCLE-EVENT footprint: the
          regular lane writes an ``instance_completed`` event for the
          child (and a ``child_completed`` event for the parent); the
          root lane writes NEITHER — it has no parent to notify. NOTE:
          the root lane's observable footprint is IDENTICAL to the tool
          lane's (T1c), so this discriminator separates the root lane
          from the REGULAR lane, not from the tool lane.
        """
        _s1_seed_instance(engine, "s1f-nudger")
        _s1_seed_instance(engine, "s1f-root-child", parent_id=None)

        await _s1_arm_nudge(
            s1_manager,
            engine,
            nudger_instance_id="s1f-nudger",
            worker_instance_id="s1f-root-child",
        )
        watcher = _s1_watcher_for(engine, "s1f-nudger", "s1f-root-child")
        assert watcher is not None, "T1f harness: the watcher must exist"
        watch_id = watcher.watch_id

        await s1_service._process_child_completion_and_notify_parent(
            "s1f-root-child", "s1f-final-msg"
        )

        with SQLModelSessionLib(engine) as s:
            child = s.get(Instance, "s1f-root-child")
        assert child.status == InstanceStatus.COMPLETED.value, (
            f"T1f harness: the child must have reached its terminal stamp "
            f"(COMPLETED, got {child.status!r})"
        )
        # LANE DISCRIMINATOR — NOT the status (the regular lane stamps
        # COMPLETED too, ``child_reports.py:4806``). The root branch
        # writes NO lifecycle event: the REGULAR lane writes
        # ``instance_completed`` for the child, the root lane writes
        # NEITHER (it has no parent to notify). The reader sees the
        # regular lane's events through this same engine, so their
        # absence is observable evidence that the ROOT branch ran.
        assert _s1_event_rows(engine, "s1f-root-child") == [], (
            f"T1f: the root lane must write NO lifecycle event for the "
            f"child (the regular lane writes instance_completed) — got "
            f"{_s1_event_rows(engine, 's1f-root-child')!r}"
        )
        fired = _s1_watcher_for(engine, "s1f-nudger", "s1f-root-child")
        assert fired is not None, "T1f: the watcher row must still exist"
        assert fired.state == "FIRED", (
            f"T1f: a ROOT child's nudge-armed watcher must be FIRED "
            f"(state={fired.state!r})"
        )
        assert fired.fired_at is not None, (
            "T1f: the fired watcher must carry a fired_at timestamp"
        )
        assert s1_bus.count_pending_for_target_sync("s1f-nudger") == 0, (
            "T1f: the watcher's target must have zero PENDING watchers"
        )
        assert len(_s1_bus_delivery_rows(engine, watch_id)) == 1, (
            "T1f: exactly ONE bus delivery expected for the fired watcher"
        )

    @pytest.mark.asyncio
    async def test_t7_healthy_path_fence_and_delivery_once_ordering(
        self, engine, s1_bus, s1_manager, s1_service,
    ):
        """T7 — the fence: the healthy path is observably unchanged.

        Two waiters on one child: the STRUCTURAL parent (the healthy
        spawn lane) and a NUDGER. The healthy path must:

        * still fire the parent's watcher (the parent's park gate is
          released — ``count_pending_for_target`` drops to 0);
        * deliver NOTHING bus-sourced to the structural parent
          (``DELIVER(w)`` is false — the report lane owns it);
        * deliver EXACTLY ONE bus-sourced row to the nudger;
        * carry the DELIVERY-ONCE marker on the delivered row, keyed to
          its watch_id, and the marker must ALREADY be committed when
          the enqueue happens (the two-transaction ordering: the
          marker is committed BEFORE the enqueue, in its own
          transaction).
        """
        _s1_seed_instance(engine, "t7-structural-parent")
        _s1_seed_instance(engine, "t7-child", parent_id="t7-structural-parent")
        _s1_seed_instance(engine, "t7-nudger")

        await _s1_arm_spawn(
            s1_manager,
            engine,
            parent_instance_id="t7-structural-parent",
            child_instance_id="t7-child",
        )
        await _s1_arm_nudge(
            s1_manager,
            engine,
            nudger_instance_id="t7-nudger",
            worker_instance_id="t7-child",
            task_message_id="t7-nudge-msg",
        )

        spawn_row = _s1_watcher_for(engine, "t7-structural-parent", "t7-child")
        nudge_row = _s1_watcher_for(engine, "t7-nudger", "t7-child")
        assert spawn_row and nudge_row, (
            "T7 harness: both the spawn-lane and the nudge-lane watcher "
            "must be armed"
        )

        await s1_service._process_child_completion_and_notify_parent(
            "t7-child", "t7-final-msg"
        )

        # (i) The healthy path still fires the parent's watcher.
        spawn_fired = _s1_watcher_for(engine, "t7-structural-parent", "t7-child")
        assert spawn_fired.state == "FIRED", (
            f"T7: the healthy path must still fire the parent's watcher "
            f"(state={spawn_fired.state!r})"
        )
        assert spawn_fired.fired_at is not None, (
            "T7: the parent's fired watcher must carry a fired_at timestamp"
        )
        # The park gate that holds the parent in WAITING_CHILDREN is
        # released (the bus no longer counts the parent as waiting).
        assert s1_bus.count_pending_for_target_sync("t7-structural-parent") == 0, (
            "T7: the parent's PENDING count must be 0 after the child's "
            "completion (the park gate is released)"
        )

        # (ii) NO bus-sourced row to the structural parent.
        parent_bus_rows = [
            row
            for row in _s1_rows_for_instance(engine, "t7-structural-parent")
            if (row.message_metadata or {}).get("watch_id")
        ]
        assert parent_bus_rows == [], (
            "T7: the structural parent must receive NO bus-sourced row "
            f"(got {len(parent_bus_rows)})"
        )
        assert _s1_bus_delivery_rows(engine, spawn_fired.watch_id) == [], (
            "T7: the spawn-lane watcher must not be delivered by the bus"
        )

        # (iii) EXACTLY ONE delivery for the nudger's watcher.
        nudge_fired = _s1_watcher_for(engine, "t7-nudger", "t7-child")
        assert nudge_fired.state == "FIRED", (
            f"T7: the nudge-armed watcher must be FIRED "
            f"(state={nudge_fired.state!r})"
        )
        assert nudge_fired.fired_at is not None, (
            "T7: the nudge-armed watcher must carry a fired_at timestamp"
        )
        delivered = _s1_bus_delivery_rows(engine, nudge_fired.watch_id)
        assert len(delivered) == 1, (
            f"T7: EXACTLY ONE bus delivery expected for the nudge-armed "
            f"watcher (got {len(delivered)})"
        )
        assert delivered[0].instance_id == "t7-nudger", (
            "T7: the delivery must land on the nudger"
        )

        # (iv) The DELIVERY-ONCE marker: keyed to the watch_id and
        # committed BEFORE the message_queue row appeared.
        marker = (nudge_fired.watcher_metadata or {}).get("delivered_watch_id")
        assert marker == nudge_fired.watch_id, (
            f"T7: the delivery marker must be keyed to the watch_id "
            f"(got {marker!r})"
        )
        # The ordering proof: at the moment the enqueue happened, the
        # marker was ALREADY committed on the row (the fake enqueue
        # snapshotted the marker state of every watcher row at enqueue
        # time).
        enqueue_snapshots = s1_manager.enqueue_message.marker_snapshots
        bus_enqueues = [
            (call, snapshot)
            for call, snapshot in zip(
                s1_manager.enqueue_message.calls, enqueue_snapshots
            )
            if (call["metadata"] or {}).get("watch_id")
        ]
        assert bus_enqueues, (
            "T7 harness: the bus delivery must have called enqueue_message"
        )
        for call, snapshot in bus_enqueues:
            assert snapshot.get(nudge_fired.watch_id) is True, (
                "T7: the delivery marker must be committed BEFORE the "
                "enqueue (two-transaction ordering)"
            )


# ═════════════════════════════════════════════════════════════════════
# S2 (wake-path-carrier-fix, phase 2) — the nudge arm-time self-surgery
# guard + the C-1 mint-ordering pin. Real act lane
# (``create_instance_ops_tools`` → ``instance_nudge``), observable ROW
# STATE only (dependency_watchers + message_queue rows).
# ═════════════════════════════════════════════════════════════════════


class _ActEnqueue(_RecordingEnqueue):
    """``_RecordingEnqueue`` adapted for the ACT path.

    Two deltas:

    1. The act calls ``manager.enqueue_message(target, message,
       source=...)`` POSITIONALLY — the recorder's keyword-only
       signature is widened here.
    2. The message id is FIXED (so a ``Task`` row can be pre-seeded
       under it for the watcher mint's ``get_by_message`` resolution)
       and the enqueue can be made to RAISE (the C-1 ordering pin).
    """

    def __init__(self, engine, message_id: str = "mq-s2-act-1",
                 raises: Exception | None = None):
        super().__init__(engine)
        self._fixed_id = message_id
        self._raises = raises

    def _next_message_id(self) -> str:
        return self._fixed_id

    async def __call__(self, instance_id=None, message=None,
                       source="api", **kwargs):
        if self._raises is not None:
            raise self._raises
        return await super().__call__(
            instance_id=instance_id, message=message, source=source,
            **kwargs,
        )


@pytest.fixture
def s2_manager(engine, s1_manager, s1_bus):
    """``s1_manager`` + the two seams the ACT path needs that the S1
    service-level harness never touched: id resolution (``get_instance``
    resolving every seeded id exactly — the fuzzy lane stays unused)
    and a graph-task registry with no live task (the act's enqueue
    lane, not the injection lane)."""
    s1_manager.get_instance = AsyncMock(return_value=object())
    s1_manager.find_near_instance = MagicMock(return_value=[])
    s1_manager._graph_tasks = {}
    return s1_manager


def _s2_nudge_tool(manager, current_instance_id: str):
    """The REAL ``instance_nudge`` act, built for one caller."""
    import daemon.tools.instance_ops as io

    tools = io.create_instance_ops_tools(
        manager,
        current_instance_id=current_instance_id,
        agent_id="ari",
    )
    return next(t for t in tools if t.name == "instance_nudge")


class TestS2NudgeArmGuardAndOrdering:
    """S2 — the nudge arm-time self-surgery guard.

    T2 pins the refusal (self-nudge ⇒ act refuses AND ZERO watcher
    rows — the row-state assertion is the point); T2-preview pins the
    dry-run path refusing identically; T2b pins the value PRESERVED
    (a non-child nudge still mints the watcher and delivers the
    message — the guard is a SELF guard, not a tree guard); C-1 pins
    the mint ordering (a failed enqueue leaves ZERO watcher rows).
    """

    async def test_t2_root_self_nudge_refused_zero_watcher_rows(
        self, engine, s2_manager
    ):
        """T2 — the live 17efd2ef shape: a ROOT instance nudging
        ITSELF. The act refuses (self-surgery token) and NO
        dependency_watchers row exists — the refusal fires BEFORE the
        arm, so the unfulfillable self-watcher is never minted."""
        root = _s1_seed_instance(engine, "ari-17efd2ef-root", parent_id=None)
        s2_manager.enqueue_message = _ActEnqueue(engine)

        out = await _s2_nudge_tool(s2_manager, root).ainvoke(
            {"id": root, "message": "resume the sweep",
             "reason": "self re-entry", "dry_run": False}
        )

        assert out.startswith("Error: INSTANCE_NUDGE REFUSED"), out
        assert "reason=self-surgery-refused" in out, out
        assert "send_message to yourself" in out, out
        # The row-state point: ZERO watcher rows, ZERO delivered rows.
        assert _s1_watcher_rows(engine) == [], (
            "T2: a refused self-nudge must mint ZERO watcher rows"
        )
        assert _s1_rows_for_instance(engine, root) == [], (
            "T2: a refused self-nudge must deliver NOTHING"
        )
        assert s2_manager.enqueue_message.calls == []

    async def test_t2_dry_run_preview_refuses_identically(
        self, engine, s2_manager
    ):
        """T2 (preview) — the dry-run path hits the SAME guard: a
        preview of a refused act is a refusal, not a preview."""
        root = _s1_seed_instance(engine, "ari-17efd2ef-root", parent_id=None)
        s2_manager.enqueue_message = _ActEnqueue(engine)

        out = await _s2_nudge_tool(s2_manager, root).ainvoke(
            {"id": root, "message": "resume the sweep",
             "reason": "self re-entry", "dry_run": True}
        )

        assert out.startswith("Error: INSTANCE_NUDGE REFUSED"), out
        assert "reason=self-surgery-refused" in out, out
        # It is a REFUSAL, not a preview envelope.
        assert '"dry_run": true' not in out.lower(), out
        assert "NO mutation happened" not in out, out
        assert _s1_watcher_rows(engine) == []

    async def test_t2b_non_child_nudge_still_mints_and_delivers(
        self, engine, s2_manager
    ):
        """T2b — the guard is a SELF guard, NOT a tree guard: a nudge
        to a NON-CHILD target (the ari → sonic-leader shape — two
        roots, no tree relation) still mints the PENDING watcher AND
        delivers the message (a message_queue row exists)."""
        nudger = _s1_seed_instance(engine, "ari-op-root", parent_id=None)
        worker = _s1_seed_instance(
            engine, "sonic-leader-worker", parent_id=None
        )
        # The enqueue lane's fixed message id, with a REAL Task under
        # it — the watcher mint resolves its source task by message id.
        _s1_seed_task(
            engine, instance_id=worker, message_id="mq-s2-act-1"
        )
        s2_manager.enqueue_message = _ActEnqueue(engine, "mq-s2-act-1")

        out = await _s2_nudge_tool(s2_manager, nudger).ainvoke(
            {"id": worker, "message": "please report the lane status",
             "reason": "operational ping", "dry_run": False}
        )

        assert '"delivered": true' in out.lower(), out
        row = _s1_watcher_for(engine, nudger, worker)
        assert row is not None, (
            "T2b: a non-child nudge MUST mint the completion watcher "
            "(the guard is not a tree guard)"
        )
        assert row.state == "PENDING", (
            f"T2b: the minted watcher must be PENDING (got {row.state!r})"
        )
        assert _s1_rows_for_instance(engine, worker), (
            "T2b: the nudge message MUST be delivered (message_queue row)"
        )

    async def test_c1_failed_enqueue_leaves_zero_watcher_rows(
        self, engine, s2_manager
    ):
        """C-1 ordering pin — the nudge act's enqueue → early-return →
        commission → watcher ordering: when the enqueue RAISES, the
        act returns the error envelope at the early return (AC-9b,
        audit row left pending) and ZERO watcher rows exist — the
        commission and the watcher mint are strictly AFTER the
        committed enqueue."""
        nudger = _s1_seed_instance(engine, "ari-c1-root", parent_id=None)
        worker = _s1_seed_instance(engine, "c1-worker", parent_id=None)
        s2_manager.enqueue_message = _ActEnqueue(
            engine, raises=RuntimeError("enqueue lane exploded")
        )

        out = await _s2_nudge_tool(s2_manager, nudger).ainvoke(
            {"id": worker, "message": "please finish",
             "reason": "c1 ordering pin", "dry_run": False}
        )

        assert out.startswith("ERROR:"), out
        assert "RuntimeError" in out, out
        assert _s1_watcher_rows(engine) == [], (
            "C-1: a FAILED enqueue must mint ZERO watcher rows — the "
            "watcher mint is strictly after the committed enqueue"
        )
        assert _s1_rows_for_instance(engine, worker) == []


# ═════════════════════════════════════════════════════════════════════
# S3 (wake-path-carrier-fix, phase 3) — the QUALIFIED park count.
#
# PARK(parent) = count_fulfillable_pending_for_target(parent) > 0.
# The raw count could not tell an unfulfillable backlog (child already
# terminal, or self-referential) from a genuine in-flight wait, so a
# stale watcher held the park forever.
#
# FULFILLABLE(w) = NOT (child_id == target_instance_id)
#                  AND (child_id IS NULL
#                       OR status-of(child_id) NOT IN INSTANCE_TERMINAL_SET)
#
# Fail-CLOSED: every uncertainty counts the row as fulfillable (the
# park HOLDS; releasing a park requires proof).
# ═════════════════════════════════════════════════════════════════════


def _s3_seed_watcher(
    engine,
    *,
    target_instance_id: str,
    child_id: str | None = None,
    source_task_id: str = "s3-src-task",
    state: str = "PENDING",
) -> str:
    """Insert one ``dependency_watchers`` row directly (row state only)."""
    metadata = {} if child_id is None else {"child_id": child_id}
    watch_id = f"w-{uuid.uuid4().hex[:12]}"
    with SQLModelSessionLib(engine) as s:
        row = DependencyWatcher(
            watch_id=watch_id,
            source_task_id=source_task_id,
            target_instance_id=target_instance_id,
            follow_up_payload={
                "message": "nudge: the child finished",
                "source": "internal_agent:s3-nudger",
                "metadata": metadata,
            },
            state=state,
            created_at=datetime.now(timezone.utc),
        )
        s.add(row)
        s.commit()
    return watch_id


def _s3_instance_status(engine, instance_id: str) -> str | None:
    with SQLModelSessionLib(engine) as s:
        row = s.get(Instance, instance_id)
        return row.status if row is not None else None


class TestS3QualifiedParkCount:
    """S3 — the qualified park count + the fail-closed table."""

    async def test_failclosed_child_id_null_is_fulfillable(
        self, engine, s1_bus
    ):
        """Row 1 — ``child_id`` IS NULL ⇒ FULFILLABLE (fail-open here
        would release a park while a child may be in flight)."""
        _s3_seed_watcher(engine, target_instance_id="s3-null", child_id=None)

        assert s1_bus.count_fulfillable_pending_for_target_sync(
            "s3-null"
        ) == 1, (
            "S3: a watcher with no child_id must count as fulfillable "
            "(the park holds)"
        )

    async def test_failclosed_missing_child_row_is_fulfillable(
        self, engine, s1_bus
    ):
        """Row 2 — the child's instance row is MISSING ⇒ FULFILLABLE."""
        _s3_seed_watcher(
            engine, target_instance_id="s3-missing", child_id="s3-gone"
        )

        assert s1_bus.count_fulfillable_pending_for_target_sync(
            "s3-missing"
        ) == 1, (
            "S3: a watcher whose child row is missing must count as "
            "fulfillable (fail-closed)"
        )

    async def test_failclosed_ghost_child_is_fulfillable(
        self, engine, s1_bus
    ):
        """Row 3 — a GHOST child (idle, version=1, no msgs/tasks) ⇒
        FULFILLABLE (``idle`` ∉ INSTANCE_TERMINAL_SET)."""
        _s1_seed_instance(
            engine, "s3-ghost-child", status="idle"
        )
        _s3_seed_watcher(
            engine, target_instance_id="s3-ghost", child_id="s3-ghost-child"
        )

        assert s1_bus.count_fulfillable_pending_for_target_sync(
            "s3-ghost"
        ) == 1, (
            "S3: a ghost child (idle) must count as fulfillable"
        )

    async def test_failclosed_status_read_raises_is_fulfillable(
        self, engine, s1_bus, monkeypatch
    ):
        """Row 4 — the status read RAISES ⇒ every row counts as
        FULFILLABLE (fail-closed: the park holds)."""
        _s1_seed_instance(
            engine, "s3-raise-child", status="running"
        )
        _s3_seed_watcher(
            engine, target_instance_id="s3-raise", child_id="s3-raise-child"
        )

        def _boom(self, instance_ids):
            raise RuntimeError("status read exploded")

        monkeypatch.setattr(
            SQLModelInstanceRepository, "get_statuses", _boom
        )

        assert s1_bus.count_fulfillable_pending_for_target_sync(
            "s3-raise"
        ) == 1, (
            "S3: a failing status read must count the row as fulfillable "
            "(fail-closed — the park holds)"
        )

    async def test_self_referential_watcher_is_excluded(
        self, engine, s1_bus
    ):
        """Row 5 — ``child_id == target_instance_id`` ⇒ NOT FULFILLABLE
        — the ONLY positive exclusion."""
        _s1_seed_instance(engine, "s3-self", status="running")
        _s3_seed_watcher(
            engine, target_instance_id="s3-self", child_id="s3-self"
        )

        assert s1_bus.count_fulfillable_pending_for_target_sync(
            "s3-self"
        ) == 0, (
            "S3: a self-referential watcher must NOT count (it can never "
            "be fulfilled — the park must not be held by it)"
        )
        # The RAW count still sees it — the qualification is what changed.
        assert s1_bus.count_pending_for_target_sync("s3-self") == 1

    async def test_terminal_child_is_not_fulfillable(self, engine, s1_bus):
        """The positive exclusion's counterpart — a TERMINAL child makes
        the row unfulfillable (the park is released)."""
        _s1_seed_instance(
            engine, "s3-dead-child", status="completed"
        )
        _s3_seed_watcher(
            engine, target_instance_id="s3-dead", child_id="s3-dead-child"
        )

        assert s1_bus.count_pending_for_target_sync("s3-dead") == 1, (
            "S3 harness: the RAW count must still see the row"
        )
        assert s1_bus.count_fulfillable_pending_for_target_sync(
            "s3-dead"
        ) == 0, (
            "S3: a watcher whose child is already terminal must NOT "
            "count (the park is released)"
        )


class TestS3ParkDoors:
    """S3 — the park DOORS: the qualified predicate switched at every
    park site, with the fence (a genuinely-pending FULFILLABLE watcher
    still parks)."""

    async def test_t3_root_completion_releases_unfulfillable_backlog(
        self, engine, s1_bus, s1_manager, s1_service
    ):
        """T3 — the NON-VACUOUS case: the REAL root completion chain.

        Fixture: a nudge-armed, NON-tree-linked watcher whose child is
        already TERMINAL; RAW_PARK(root) is still true; the root has
        zero non-terminal children.

        Chain driven (all REAL):
            ``_process_child_completion_and_notify_parent`` (producer)
              → ``_process_child_completion_db_sync`` (R2/R3/R4 gates)
              → the ``root_completed`` emission branch
              → ``_root_completion_gate`` (the D-1 leg)
              → the WAITING_CHILDREN downgrade write.

        WITHOUT the qualified count the D-1 leg reads raw>0 (backlog)
        and downgrades the root back to WAITING_CHILDREN — the park
        persists. WITH it, the root COMPLETES.
        """
        root = "t3-root"
        _s1_seed_instance(engine, root, parent_id=None)
        # The watched child is TERMINAL and NOT tree-linked to the root
        # (a nudge watcher is not a tree edge).
        _s1_seed_instance(
            engine, "t3-dead-child", parent_id=None, status="completed"
        )
        _s3_seed_watcher(
            engine, target_instance_id=root, child_id="t3-dead-child"
        )

        # The RAW park precondition — the defect's input (the RAW
        # count is the pre-S3 shape, so this assert holds in BOTH the
        # fixed and the counter-mutated code: it documents the
        # fixture, it does not carry the test).
        assert s1_bus.count_pending_for_target_sync(root) == 1, (
            "T3 harness: RAW_PARK(root) must still be true (the raw "
            "count sees the unfulfillable backlog)"
        )

        await s1_service._process_child_completion_and_notify_parent(
            root, "t3-root-final-msg"
        )

        # THE CHAIN ASSERTIONS (first, deliberately): the root must
        # NOT be parked and MUST have completed through the REAL
        # chain. Under the counter-mutation (the qualified count
        # reverted to the raw count) this is the assert that reddens
        # — the chain itself parks the root.
        status = _s3_instance_status(engine, root)
        assert status != "waiting_children", (
            f"T3: an unfulfillable backlog must NOT park the root "
            f"(status={status!r})"
        )
        assert status == "completed", (
            f"T3: the root must COMPLETE through the full chain "
            f"(status={status!r})"
        )
        # The fixture fact (post-chain): the backlog is unfulfillable,
        # so the qualified count is zero.
        assert s1_bus.count_fulfillable_pending_for_target_sync(root) == 0, (
            "T3 harness: the qualified count must be zero (the backlog "
            "is unfulfillable — its child is already terminal)"
        )
        # The backlog is still on the books — the release came from the
        # QUALIFICATION, not from the watcher disappearing.
        assert s1_bus.count_pending_for_target_sync(root) == 1, (
            "T3: the watcher row must still exist (the park was "
            "released by the qualified count, not by a delete)"
        )

    async def test_t3_fence_r1_park_holds_for_fulfillable_watcher(
        self, engine, s1_bus, s1_manager
    ):
        """T3 fence (R1) — a genuinely-pending FULFILLABLE watcher still
        parks: the REAL ``_maybe_transition_to_waiting_children`` writes
        WAITING_CHILDREN."""
        from daemon.services.message_processing_pipeline import (
            MessageProcessingPipeline,
        )

        root = "t3-fence-root"
        _s1_seed_instance(engine, root, parent_id=None)
        # A LIVE (running) child ⇒ the watcher IS fulfillable.
        _s1_seed_instance(
            engine, "t3-live-child", parent_id=None, status="running"
        )
        _s3_seed_watcher(
            engine, target_instance_id=root, child_id="t3-live-child"
        )

        pipeline = MessageProcessingPipeline(
            execution_gate=MagicMock(), manager=s1_manager
        )
        transitioned = await pipeline._maybe_transition_to_waiting_children(
            root
        )

        assert transitioned is True, (
            "T3 fence: a FULFILLABLE pending watcher must still park "
            "the parent (R1)"
        )
        assert _s3_instance_status(engine, root) == "waiting_children", (
            "T3 fence: the parent must be WAITING_CHILDREN"
        )

    async def test_t3_fence_r1_no_park_for_unfulfillable_backlog(
        self, engine, s1_bus, s1_manager
    ):
        """T3 fence (R1, negative) — an UNFULFILLABLE-only backlog does
        NOT park: the same REAL helper returns False and the status
        stays RUNNING."""
        from daemon.services.message_processing_pipeline import (
            MessageProcessingPipeline,
        )

        root = "t3-nopark-root"
        _s1_seed_instance(engine, root, parent_id=None)
        _s1_seed_instance(
            engine, "t3-nopark-dead", parent_id=None, status="completed"
        )
        _s3_seed_watcher(
            engine, target_instance_id=root, child_id="t3-nopark-dead"
        )

        pipeline = MessageProcessingPipeline(
            execution_gate=MagicMock(), manager=s1_manager
        )
        transitioned = await pipeline._maybe_transition_to_waiting_children(
            root
        )

        assert transitioned is False, (
            "T3: an unfulfillable backlog must NOT park the parent (R1)"
        )
        assert _s3_instance_status(engine, root) == "running", (
            "T3: the parent must stay RUNNING (no park written)"
        )

    async def test_t3_fence_r4_same_tx_gate_still_parks(
        self, engine, s1_bus, s1_manager, s1_service
    ):
        """T3 fence (R4) — the in-transaction gate still parks on a
        FULFILLABLE watcher: driving the REAL producer leaves the root
        un-completed (deferred), never COMPLETED."""
        root = "t3-r4-root"
        _s1_seed_instance(engine, root, parent_id=None)
        _s1_seed_instance(
            engine, "t3-r4-live", parent_id=None, status="running"
        )
        _s3_seed_watcher(
            engine, target_instance_id=root, child_id="t3-r4-live"
        )

        assert s1_bus.count_fulfillable_pending_for_target_sync(root) == 1

        await s1_service._process_child_completion_and_notify_parent(
            root, "t3-r4-final-msg"
        )

        status = _s3_instance_status(engine, root)
        assert status != "completed", (
            f"T3 fence (R4): a FULFILLABLE pending watcher must still "
            f"hold the root (status={status!r})"
        )



class TestS3HealerBusLegs:
    """S3 — the healer's bus legs (R5 refusal + R6 reconcile-zone skip).

    The healer's legs read the SAME qualified count, so an
    unfulfillable-only backlog must not hold the heal.
    """

    @staticmethod
    def _healer(manager):
        from daemon.services.mission_strand_healer import (
            MissionStrandHealer,
        )

        return MissionStrandHealer(
            manager=manager, interval_seconds=300
        )

    async def test_r5_healer_does_not_refuse_on_unfulfillable_backlog(
        self, engine, s1_bus, s1_manager
    ):
        """R5 — the healer's zero-obligation bus leg refuses with
        ``reason=bus_pending=N`` on the RAW count. With the qualified
        count an unfulfillable-only backlog must NOT refuse the heal."""
        root = "s3-heal-root"
        _s1_seed_instance(engine, root, parent_id=None)
        _s1_seed_instance(
            engine, "s3-heal-dead", parent_id=None, status="completed"
        )
        _s3_seed_watcher(
            engine, target_instance_id=root, child_id="s3-heal-dead"
        )

        # Fixture shape proof: the RAW count WOULD have refused.
        assert s1_bus.count_pending_for_target_sync(root) == 1, (
            "R5 harness: the raw count must see the backlog"
        )
        assert s1_bus.count_fulfillable_pending_for_target_sync(root) == 0

        healer = self._healer(s1_manager)
        outcome: dict = {}
        try:
            outcome = await healer._heal_instance_locked(root)
        except Exception as exc:  # noqa: BLE001 — the drive leg may fail
            outcome = {"raised": type(exc).__name__}

        assert not str(outcome.get("reason", "")).startswith(
            "bus_pending"
        ), (
            f"R5: the healer must not refuse on an unfulfillable "
            f"backlog (outcome={outcome!r})"
        )

    async def test_r6_reconcile_zone_does_not_skip_on_unfulfillable_backlog(
        self, engine, s1_bus, s1_manager
    ):
        """R6 — the reconcile-zone bus probe skips with
        ``reason=bus-pending`` on the RAW count; the qualified count
        must not produce that skip."""
        root = "s3-rz-root"
        _s1_seed_instance(engine, root, parent_id=None)
        _s1_seed_instance(
            engine, "s3-rz-dead", parent_id=None, status="completed"
        )
        _s3_seed_watcher(
            engine, target_instance_id=root, child_id="s3-rz-dead"
        )

        assert s1_bus.count_pending_for_target_sync(root) == 1
        assert s1_bus.count_fulfillable_pending_for_target_sync(root) == 0

        healer = self._healer(s1_manager)
        outcome: dict = {}
        try:
            outcome = await healer._heal_running_zombie_locked(root)
        except Exception as exc:  # noqa: BLE001
            outcome = {"raised": type(exc).__name__}

        assert "bus" not in str(outcome.get("reason", "")).lower(), (
            f"R6: the reconcile-zone probe must not skip on an "
            f"unfulfillable backlog (outcome={outcome!r})"
        )


# ═════════════════════════════════════════════════════════════════════
# S4 (wake-path-carrier-fix, phase 4) — the NOTICE SURFACE.
#
# T4 — the notice CONTENT is composed from row-checked reason codes
#      (the old template claimed a queued PROCESS_REPORT that the
#      watchdog never read — FALSE in 100% of notices ever minted);
#      a legitimately-parked parent (only FULFILLABLE waits) gets ZERO
#      notices (the ~9x notice-volume regression guard).
# T5 — the episode semantics: dedupe on (parent, reason_code), NO TTL,
#      the episode ends on the ROW-CHECK (not on WC membership).
# T6 — the kill-switch: EMISSION ONLY, read PER TICK, default ON.
#
# Observable state: message_queue rows (the notices), instance status,
# settings rows. No log/grep assertions.
# ═════════════════════════════════════════════════════════════════════


def _s4_watchdog(manager):
    """A watchdog over the real repos: no live carriers, no hang gate."""
    from daemon.services.waiting_children_watchdog import (
        WaitingChildrenWatchdog,
    )

    return WaitingChildrenWatchdog(
        manager._instance_repository,
        manager,
        interval_seconds=1,
        hang_threshold_seconds=10 ** 9,
        task_repository=manager._task_repo,
    )


def _s4_notice_rows(engine) -> list:
    """The wedge-notice rows the watchdog enqueued (row state)."""
    with SQLModelSessionLib(engine) as s:
        return [
            row
            for row in s.exec(sqlmodel_select(MessageQueue))
            if (row.message_metadata or {}).get("wedge_notice") is True
        ]


def _s4_queue_repo(engine):
    from daemon.repositories.message_queue.repository import (
        SQLModelMessageQueueRepository,
    )

    return SQLModelMessageQueueRepository(engine)


class _StubSettingsService:
    """A settings-service double with a MUTABLE override map.

    The watchdog reads the switch PER TICK through
    ``get_overrides()`` — the stub's map can be flipped mid-run to
    prove the read is per-tick (no restart).
    """

    def __init__(self, overrides: dict | None = None):
        self.overrides: dict = dict(overrides or {})
        self.reads = 0

    def get_overrides(self) -> dict:
        self.reads += 1
        return dict(self.overrides)


class TestS4NoticeSurface:
    """S4 — the composed notice, the subject population, the episode
    dedupe and the emission-only kill-switch."""

    # ── T4: the notice content ──────────────────────────────────────

    async def test_t4a_no_queued_report_row_means_no_claim(
        self, engine, s1_bus, s1_manager
    ):
        """T4(a) — with NO queued ``completion_report`` row the notice
        MUST NOT claim one (the old template always claimed it)."""
        parent = _s1_seed_instance(
            engine, "s4-root", parent_id=None,
            status="waiting_children",
        )
        _s1_seed_instance(
            engine, "s4-dead-child", parent_id=None, status="completed"
        )
        s1_manager._queue_repository = _s4_queue_repo(engine)
        w = _s4_watchdog(s1_manager)

        await w.run_once()

        notices = _s4_notice_rows(engine)
        assert len(notices) == 1, (
            f"T4(a): the classic wedge must still notify (got "
            f"{len(notices)})"
        )
        text = notices[0].content
        assert "completion_report" not in text, (
            f"T4(a): the notice must not claim a completion_report row "
            f"that does not exist — text={text!r}"
        )
        assert "no_live_carrier" in text, text
        assert parent[:8] in text, text

    async def test_t4b_queued_report_row_is_claimed_with_its_id(
        self, engine, s1_bus, s1_manager
    ):
        """T4(b) — WITH a queued ``completion_report`` row the notice
        claims it AND cites the row id."""
        parent = _s1_seed_instance(
            engine, "s4-root-q", parent_id=None,
            status="waiting_children",
        )
        _s1_seed_instance(
            engine, "s4-dead-child-q", parent_id=None, status="completed"
        )
        queue_repo = _s4_queue_repo(engine)
        s1_manager._queue_repository = queue_repo
        queue_repo.enqueue(
            instance_id=parent,
            content="child completion report",
            source="internal_report:child",
            message_metadata={"type": "completion_report"},
        )
        # The row's ``type`` column is what the queued_report check
        # reads — set it explicitly (the repo helper has no type
        # parameter).
        with SQLModelSessionLib(engine) as s:
            for _row in s.exec(
                sqlmodel_select(MessageQueue).where(
                    MessageQueue.instance_id == parent
                )
            ):
                _row.type = "completion_report"
                s.add(_row)
            s.commit()
        w = _s4_watchdog(s1_manager)

        await w.run_once()

        notices = _s4_notice_rows(engine)
        assert len(notices) == 1
        text = notices[0].content
        assert "queued_report" in text, text
        assert "completion_report" in text, text
        # The row id MUST be cited (the claim is evidence-backed).
        # The SEEDED report row (NOT the watchdog's own notice row —
        # that one is also completion_report-typed, which is exactly
        # why the queued_report check excludes the watchdog's sources).
        queued_ids = [
            row.message_id
            for row in queue_repo.get_by_instance(parent)
            if row.type == "completion_report"
            and row.source == "internal_report:child"
        ]
        assert queued_ids, "T4(b) harness: the queued report row vanished"
        assert queued_ids[0] in text, (
            f"T4(b): the notice must cite the queued row id "
            f"({queued_ids[0]!r}) — text={text!r}"
        )

    async def test_t4c_fulfillable_only_parent_gets_zero_notices(
        self, engine, s1_bus, s1_manager
    ):
        """T4(c) — the ~9x notice-volume regression guard: a parent
        whose only PENDING watchers are FULFILLABLE (its watched child
        is still running) is LEGITIMATELY parked ⇒ ZERO notices."""
        parent = _s1_seed_instance(
            engine, "s4-healthy", parent_id=None,
            status="waiting_children",
        )
        child = _s1_seed_instance(
            engine, "s4-live-child", parent_id=None, status="running"
        )
        s1_manager._queue_repository = _s4_queue_repo(engine)
        _s3_seed_watcher(
            engine, target_instance_id=parent, child_id=child
        )
        w = _s4_watchdog(s1_manager)

        await w.run_once()

        assert _s4_notice_rows(engine) == [], (
            "T4(c): a legitimately-parked parent (only FULFILLABLE "
            "waits) must get ZERO notices"
        )

    # ── T5: the episode semantics ───────────────────────────────────

    async def test_t5a_same_episode_notifies_exactly_once(
        self, engine, s1_bus, s1_manager
    ):
        """T5(a) — two passes, the same (parent, reason_code) and the
        condition still true ⇒ EXACTLY ONE notice row (no TTL, no
        duplicate).

        SHIPPED SEMANTICS (S4/B2): the episode is the per-code set —
        the entry holds the REASON-CODE FINGERPRINT, and while every
        stamped code still holds the fingerprint matches ⇒ no second
        notice. There is NO TTL: the old suppression window was
        withdrawn (the episode ends on the row-check, not on a timer)."""
        parent = _s1_seed_instance(
            engine, "s4-ep", parent_id=None,
            status="waiting_children",
        )
        _s1_seed_instance(
            engine, "s4-ep-dead", parent_id=None, status="completed"
        )
        s1_manager._queue_repository = _s4_queue_repo(engine)
        w = _s4_watchdog(s1_manager)

        await w.run_once()
        await w.run_once()

        assert len(_s4_notice_rows(engine)) == 1, (
            "T5(a): the same episode must notify exactly once"
        )
        assert parent in w.wedge_episodes

    async def test_t5b_wc_to_running_flip_does_not_end_the_episode(
        self, engine, s1_bus, s1_manager
    ):
        """T5(b) — the notice's OWN WC→RUNNING→WC flip does NOT end
        the episode ⇒ still exactly ONE notice row (a membership-based
        end would restore the hourly re-notification loop).

        SHIPPED SEMANTICS (S4/B2): the flip moves the parent OUT of
        the WC scan set, but the episode's stamped codes all still
        hold ⇒ the entry is neither narrowed nor dropped ⇒ the next
        pass fingerprint-matches ⇒ no second notice."""
        parent = _s1_seed_instance(
            engine, "s4-flip", parent_id=None,
            status="waiting_children",
        )
        _s1_seed_instance(
            engine, "s4-flip-dead", parent_id=None, status="completed"
        )
        s1_manager._queue_repository = _s4_queue_repo(engine)
        w = _s4_watchdog(s1_manager)

        await w.run_once()
        assert len(_s4_notice_rows(engine)) == 1

        # The notice's own effect: the parent is no longer parked.
        with SQLModelSessionLib(engine) as s:
            row = s.get(Instance, parent)
            row.status = "running"
            s.add(row)
            s.commit()
        # ... and it re-parks (WC again).
        with SQLModelSessionLib(engine) as s:
            row = s.get(Instance, parent)
            row.status = "waiting_children"
            s.add(row)
            s.commit()

        await w.run_once()

        assert len(_s4_notice_rows(engine)) == 1, (
            "T5(b): the WC→RUNNING→WC flip must NOT end the episode"
        )

    async def test_t5c_row_check_turning_false_ends_the_episode(
        self, engine, s1_bus, s1_manager
    ):
        """T5(c) — the ROW-CHECK turning false (the subject empties:
        the watched child is running again ⇒ the wait is fulfillable
        again) ends THAT CODE's episode ⇒ a later recurrence notifies
        again.

        SHIPPED SEMANTICS (S4/B2, per-code — NOT the withdrawn
        drop-the-whole-episode rule): the ``pending_watchers`` code
        stops holding ⇒ its episode ends and the entry NARROWS to the
        still-holding codes (here: ``no_live_carrier``) WITHOUT
        re-notifying for them. The entry is dropped outright only when
        the LAST stamped code stops holding (the single-code classic
        shape — pinned by the B3 test in
        ``tests/unit/services/test_waiting_children_watchdog.py``).
        A code that REAPPEARS is a new episode ⇒ a new notice."""
        parent = _s1_seed_instance(
            engine, "s4-recur", parent_id=None,
            status="waiting_children",
        )
        child = _s1_seed_instance(
            engine, "s4-recur-child", parent_id=None, status="completed"
        )
        s1_manager._queue_repository = _s4_queue_repo(engine)
        _s3_seed_watcher(
            engine, target_instance_id=parent, child_id=child
        )
        w = _s4_watchdog(s1_manager)

        await w.run_once()
        assert len(_s4_notice_rows(engine)) == 1

        # The row-check turns false — AND (the B2 re-derivation) the
        # parent has LEFT the WC scan set first (its own notice's flip
        # posture): the episode must still END on the condition-false
        # tick. Under the pre-B2 code the end-evaluation never ran for
        # a parent outside the scan set and the old purge required
        # ``total_pending > 0``, so the episode NEVER ended and the
        # parent was PERMANENTLY silenced on recurrence.
        with SQLModelSessionLib(engine) as s:
            row = s.get(Instance, parent)
            row.status = "running"
            s.add(row)
            s.commit()
        with SQLModelSessionLib(engine) as s:
            row = s.get(Instance, child)
            row.status = "running"  # the watcher becomes FULFILLABLE
            s.add(row)
            s.commit()
        await w.run_once()
        assert len(_s4_notice_rows(engine)) == 1, (
            "T5(c): the subject is empty now — no new notice"
        )
        # Per-code episode semantics (B2): the ``pending_watchers``
        # code's episode ENDED (its row-check stopped holding) — the
        # entry NARROWS to the still-holding ``no_live_carrier`` code
        # instead of being dropped outright. (In the classic single-
        # code shape the entry drops entirely — pinned by the B3 test
        # in tests/unit/services/test_waiting_children_watchdog.py.)
        assert w._wedge_notified.get(parent) == ("no_live_carrier",), (
            "T5(c)/B2: the pending_watchers episode must END when its "
            f"row-check stops holding — entry={w._wedge_notified.get(parent)!r}"
        )

        # The recurrence: the child goes terminal again ⇒ the subject
        # is back ⇒ a later pass notifies again.
        with SQLModelSessionLib(engine) as s:
            row = s.get(Instance, child)
            row.status = "completed"
            s.add(row)
            s.commit()
        with SQLModelSessionLib(engine) as s:
            row = s.get(Instance, parent)
            row.status = "waiting_children"
            s.add(row)
            s.commit()
        await w.run_once()
        assert len(_s4_notice_rows(engine)) == 2, (
            "T5(c)/B2: a later recurrence must notify again — a "
            "permanently-silenced parent is the B2 defect"
        )

    # ── T6: the kill-switch (emission only, per tick) ──────────────

    async def test_t6_switch_off_suppresses_only_the_emission(
        self, engine, s1_bus, s1_manager
    ):
        """T6 — SWITCH OFF ⇒ ZERO notice rows, while the DETECTION and
        CLASSIFICATION still run AND the healing path still sees the
        backlog (the switch must NOT reach the healer: a switch wired
        into the healer would make this assertion fail)."""
        from daemon.services.mission_strand_healer import (
            MissionStrandHealer,
        )

        parent = _s1_seed_instance(
            engine, "s4-switch", parent_id=None,
            status="waiting_children",
        )
        _s1_seed_instance(
            engine, "s4-switch-dead", parent_id=None, status="completed"
        )
        s1_manager._queue_repository = _s4_queue_repo(engine)
        s1_manager._settings_service = _StubSettingsService(
            {WEDGE_NOTICE_EMISSION_KEY: False}
        )
        w = _s4_watchdog(s1_manager)

        await w.run_once()

        assert _s4_notice_rows(engine) == [], (
            "T6: SWITCH_OFF must suppress the notice emission"
        )
        # DETECTION still ran: the parent was scanned.
        assert w.wedge_parents_scanned >= 1, (
            "T6: the switch must NOT disable the detection"
        )
        # CLASSIFICATION still ran — but the reason CODES have NO public
        # surface (the watchdog exposes only ``wedge_episodes``,
        # ``wedge_parents_scanned`` and ``wedge_notices_enqueued``), so
        # there is nothing observable to assert about the codes
        # themselves. The reachable public observable of the
        # classification's CONSEQUENCE is the EPISODE SET: the episode
        # stamp rides with the EMISSION, so a suppressed emission must
        # leave the parent's episode UNSTAMPED (flipping the switch back
        # ON then notifies again without a restart).
        assert parent not in w.wedge_episodes, (
            f"T6: SWITCH_OFF must not stamp a wedge episode for the "
            f"parent (the episode stamp rides with the emission) — got "
            f"{sorted(w.wedge_episodes)!r}"
        )
        assert w.wedge_notices_enqueued == 0, (
            f"T6: SWITCH_OFF must not enqueue a wedge notice (counted "
            f"{w.wedge_notices_enqueued})"
        )
        # THE HEALING PATH still sees the backlog: the healer's bus leg
        # refuses on a FULFILLABLE backlog — the switch must not reach
        # it. (A switch wired into the healer would make this FAIL.)
        _s1_seed_instance(
            engine, "s4-switch-live", parent_id=None, status="running"
        )
        _s3_seed_watcher(
            engine, target_instance_id=parent, child_id="s4-switch-live"
        )
        healer = MissionStrandHealer(
            manager=s1_manager, interval_seconds=300
        )
        outcome = await healer._heal_instance_locked(parent)
        assert str(outcome.get("reason", "")).startswith("bus_pending"), (
            f"T6: the switch must NOT reach the healer's bus leg — the "
            f"healer must still see the backlog (outcome={outcome!r})"
        )

    async def test_t6_switch_on_by_default_notifies(
        self, engine, s1_bus, s1_manager
    ):
        """T6 — the DEFAULT is NOTICES ON: with no override row the
        notice IS created."""
        _s1_seed_instance(
            engine, "s4-default", parent_id=None,
            status="waiting_children",
        )
        _s1_seed_instance(
            engine, "s4-default-dead", parent_id=None, status="completed"
        )
        s1_manager._queue_repository = _s4_queue_repo(engine)
        s1_manager._settings_service = _StubSettingsService({})
        w = _s4_watchdog(s1_manager)

        await w.run_once()

        assert len(_s4_notice_rows(engine)) == 1, (
            "T6: the default must be NOTICES ON (absence = on)"
        )

    async def test_t6_mid_run_flip_takes_effect_without_restart(
        self, engine, s1_bus, s1_manager
    ):
        """T6 — the PER-TICK read: a mid-run flip takes effect with NO
        restart (the registry key alone is void — the watchdog reads
        the settings service on every tick)."""
        from daemon.services.waiting_children_watchdog import (
            WEDGE_NOTICE_EMISSION_KEY,
        )

        _s1_seed_instance(
            engine, "s4-flip-switch", parent_id=None,
            status="waiting_children",
        )
        _s1_seed_instance(
            engine, "s4-flip-switch-dead", parent_id=None, status="completed"
        )
        s1_manager._queue_repository = _s4_queue_repo(engine)
        settings = _StubSettingsService({WEDGE_NOTICE_EMISSION_KEY: False})
        s1_manager._settings_service = settings
        w = _s4_watchdog(s1_manager)

        await w.run_once()
        assert _s4_notice_rows(engine) == [], (
            "T6: the switch must be OFF in the first tick"
        )

        # The operator flips it ON — the NEXT tick must notice, with no
        # restart and no re-construction of the watchdog.
        settings.overrides[WEDGE_NOTICE_EMISSION_KEY] = True
        await w.run_once()

        assert len(_s4_notice_rows(engine)) == 1, (
            "T6: the per-tick read must pick the mid-run flip up "
            "(no restart)"
        )


class TestS3BlipPaths:
    """R3 — the BLIP paths: a READ FAILURE must never end an episode,
    never admit a classic-wedge notice, and never crash the tick.

    House rule r2: observable ROW STATE (the notice rows) + the
    episode map — never a string grep.

    SELF-GUARDING (the closing-commit doctrine): mutation-proven is
    NOT self-guarding — an inert monkeypatch leaves a leg green. Every
    injected failure in this class carries a CALL-COUNTER control that
    asserts the injection actually FIRED (was consulted and raised).
    """

    @staticmethod
    def _counting_boom(message: str):
        """A raising double that RECORDS its calls (the self-guard)."""
        calls: list[str] = []

        def _boom(*args, **kwargs):
            calls.append(args[0] if args else "?")
            raise RuntimeError(message)

        return _boom, calls

    async def test_r3a_subject_read_failure_leaves_the_episode_untouched(
        self, engine, s1_bus, s1_manager, monkeypatch
    ):
        """R3(a) — a subject-read FAILURE ⇒ the episode map is
        UNCHANGED and ZERO new notice rows (the read failure may
        postpone the verdict, never decide it)."""
        parent = _s1_seed_instance(
            engine, "s4-blip-subject", parent_id=None,
            status="waiting_children",
        )
        # LOAD-BEARING SEED (F7 note): the watched child's row MUST
        # exist (a MISSING child row counts as fulfillable), so
        # without this terminal child the subject would be EMPTY and
        # the stamped codes would not include ``pending_watchers``.
        _s1_seed_instance(
            engine, "s4-blip-subject-dead", parent_id=None,
            status="completed",
        )
        _s3_seed_watcher(
            engine, target_instance_id=parent,
            child_id="s4-blip-subject-dead",
        )
        s1_manager._queue_repository = _s4_queue_repo(engine)
        w = _s4_watchdog(s1_manager)

        # Tick 1 — the wedge fires and opens the episode.
        await w.run_once()
        assert len(_s4_notice_rows(engine)) == 1
        stamped = w._wedge_notified.get(parent)
        assert stamped, "R3(a) harness: the episode must be open"
        assert "pending_watchers" in stamped, (
            f"R3(a) harness: the stamped codes must include the "
            f"subject-read leg (got {stamped!r})"
        )

        # The subject read now FAILS — with a CALL-COUNTER control.
        _boom, _calls = self._counting_boom("subject read exploded")
        monkeypatch.setattr(
            s1_bus, "fetch_pending_subject_for_targets_sync", _boom
        )

        await w.run_once()

        # SELF-GUARD (F1): the injection must have actually FIRED —
        # an inert patch would leave this leg green for nothing.
        assert _calls, (
            "R3(a) SELF-GUARD: the subject-read injection never fired "
            "(an inert monkeypatch is not evidence)"
        )
        assert len(_s4_notice_rows(engine)) == 1, (
            "R3(a): a subject-read failure must emit ZERO new notices"
        )
        assert w._wedge_notified.get(parent) == stamped, (
            "R3(a): a subject-read failure must leave the episode map "
            f"UNCHANGED (got {w._wedge_notified.get(parent)!r}, "
            f"expected {stamped!r})"
        )

    async def test_r3b_subject_read_failure_emits_no_classic_wedge_row(
        self, engine, s1_bus, s1_manager, monkeypatch
    ):
        """R3(b) — a subject-read failure on a CHILDLESS parent ⇒ ZERO
        classic-wedge rows: the totals are fabricated on a failed read,
        so the classic arm must not be admitted."""
        _s1_seed_instance(
            engine, "s4-blip-classic", parent_id=None,
            status="waiting_children",
        )
        # (F7: the old inert ``s4-blip-classic-dead`` seed — an
        # instance with parent_id=None that this parent never sees —
        # was DELETED; the parent is childless by construction.)
        s1_manager._queue_repository = _s4_queue_repo(engine)
        w = _s4_watchdog(s1_manager)

        _boom, _calls = self._counting_boom("subject read exploded")
        monkeypatch.setattr(
            s1_bus, "fetch_pending_subject_for_targets_sync", _boom
        )

        await w.run_once()

        assert _calls, (
            "R3(b) SELF-GUARD: the subject-read injection never fired"
        )
        assert _s4_notice_rows(engine) == [], (
            "R3(b): a failed subject read must NOT admit the classic-"
            "wedge arm (no false wedge notice on a DB hiccup)"
        )

    async def test_r3c_carrier_read_exception_completes_the_tick(
        self, engine, s1_bus, s1_manager, monkeypatch
    ):
        """R3(c), HARDENED (RV-1) — a carrier-read EXCEPTION on ONE
        parent while a SECOND parent's reads succeed in the SAME tick:

        * the tick COMPLETES (the sibling's notice lands — the blip
          does not kill the tick), and
        * the blipped parent emits NO new notice and its episode is
          UNCHANGED.

        FINGERPRINT-CHANGING (RV-1): a queued completion_report row is
        seeded AFTER the first notice, so the blipped parent's code set
        WOULD differ on this tick — under a pre-R1 carrier arm (an
        UNKNOWN carrier read as "no carrier") the recomputed
        fingerprint would differ from the stamped one and a SECOND
        notice would fire. The assertion "still exactly ONE row" is
        therefore discriminating against that reversion.
        """
        parent = _s1_seed_instance(
            engine, "s4-blip-carrier", parent_id=None,
            status="waiting_children",
        )
        sibling = _s1_seed_instance(
            engine, "s4-blip-carrier-sibling", parent_id=None,
            status="waiting_children",
        )
        # (F7: the old inert ``s4-blip-carrier-dead`` seed was DELETED
        # — both parents are childless by construction.)
        queue_repo = _s4_queue_repo(engine)
        s1_manager._queue_repository = queue_repo
        w = _s4_watchdog(s1_manager)

        # Tick 1 — both parents notice (two episodes open).
        await w.run_once()
        assert len(_s4_notice_rows(engine)) == 2
        stamped = w._wedge_notified.get(parent)
        assert stamped

        # The FINGERPRINT-CHANGING event: a queued completion_report
        # row appears for the blipped parent (its code set on this
        # tick would now include ``queued_report``).
        queue_repo.enqueue(
            instance_id=parent,
            content="child completion report",
            source="internal_report:child",
            message_metadata={"type": "completion_report"},
        )
        with SQLModelSessionLib(engine) as s:
            for _row in s.exec(
                sqlmodel_select(MessageQueue).where(
                    MessageQueue.instance_id == parent
                )
            ):
                if _row.source == "internal_report:child":
                    _row.type = "completion_report"
                    s.add(_row)
            s.commit()

        # The carrier probe now RAISES for the blipped parent ONLY —
        # the sibling's reads succeed (the multi-parent control).
        _boom, _calls = self._counting_boom("carrier probe exploded")

        def _selective_carrier(instance_id):
            if instance_id == parent:
                return _boom(instance_id)
            return []

        monkeypatch.setattr(
            s1_manager._task_repo,
            "list_live_process_report_carriers_for_instance",
            _selective_carrier,
        )

        await w.run_once()  # the tick must COMPLETE

        assert _calls and all(c == parent for c in _calls), (
            "R3(c) SELF-GUARD: the carrier injection must have fired "
            f"for the blipped parent and NEVER for the sibling "
            f"(calls={_calls!r})"
        )
        rows_for_parent = [
            row
            for row in _s4_notice_rows(engine)
            if row.instance_id == parent
        ]
        assert len(rows_for_parent) == 1, (
            "R3(c): an unknown carrier state must emit ZERO new "
            "notices for the blipped parent — and (RV-1) the "
            "fingerprint-changing queue row means a pre-R1 carrier "
            "arm WOULD have re-notified here"
        )
        assert w._wedge_notified.get(parent) == stamped, (
            "R3(c): a carrier-read failure must leave the episode map "
            f"UNCHANGED (got {w._wedge_notified.get(parent)!r})"
        )
        # The sibling's notice landed ⇒ the tick COMPLETED.
        rows_for_sibling = [
            row
            for row in _s4_notice_rows(engine)
            if row.instance_id == sibling
        ]
        assert len(rows_for_sibling) == 1, (
            "R3(c): the sibling parent's reads succeeded — its notice "
            "must land (the blip must not kill the tick)"
        )

    async def test_r3d_fresh_episode_unknown_carrier_emits_no_notice(
        self, engine, s1_bus, s1_manager, monkeypatch
    ):
        """R3(d) (F4) — a FRESH episode (no prior notice) with an
        UNKNOWN carrier state (the probe raises) ⇒ NO notice is
        emitted, with the boom-called control.

        This pins the IN-LOOP carrier-None guard: on a fresh episode,
        deleting that guard admits the parent with ``carrier=None``
        (falsy ⇒ read as "no carrier") and mints a FALSE WEDGE NOTICE
        on an unknown state — this lane's own harm class."""
        _s1_seed_instance(
            engine, "s4-blip-fresh", parent_id=None,
            status="waiting_children",
        )
        s1_manager._queue_repository = _s4_queue_repo(engine)
        w = _s4_watchdog(s1_manager)

        _boom, _calls = self._counting_boom("carrier probe exploded")
        monkeypatch.setattr(
            s1_manager._task_repo,
            "list_live_process_report_carriers_for_instance",
            _boom,
        )

        await w.run_once()

        assert _calls, (
            "R3(d) SELF-GUARD: the carrier injection never fired"
        )
        assert _s4_notice_rows(engine) == [], (
            "R3(d): an UNKNOWN carrier state on a fresh episode must "
            "emit NO notice (a false wedge notice is the harm)"
        )

    async def test_f5_queued_report_read_failure_leaves_episode_untouched(
        self, engine, s1_bus, s1_manager, monkeypatch
    ):
        """F5 — an episode stamped with ``queued_report`` + an injected
        QUEUE-READ failure ⇒ the episode is UNCHANGED and ZERO new
        notices (the tri-state in ``_queued_completion_report_id``:
        a swallowed error is distinguishable from "no queued report",
        so the code's episode cannot end on a queue-read blip)."""
        parent = _s1_seed_instance(
            engine, "s4-blip-queue", parent_id=None,
            status="waiting_children",
        )
        queue_repo = _s4_queue_repo(engine)
        s1_manager._queue_repository = queue_repo
        # The queued completion_report row — the reason the episode is
        # stamped with the ``queued_report`` code.
        queue_repo.enqueue(
            instance_id=parent,
            content="child completion report",
            source="internal_report:child",
            message_metadata={"type": "completion_report"},
        )
        with SQLModelSessionLib(engine) as s:
            for _row in s.exec(
                sqlmodel_select(MessageQueue).where(
                    MessageQueue.instance_id == parent
                )
            ):
                if _row.source == "internal_report:child":
                    _row.type = "completion_report"
                    s.add(_row)
            s.commit()
        w = _s4_watchdog(s1_manager)

        # Tick 1 — the notice stamps the queued_report code.
        await w.run_once()
        assert len(_s4_notice_rows(engine)) == 1
        stamped = w._wedge_notified.get(parent)
        assert stamped and "queued_report" in stamped, (
            f"F5 harness: the episode must be stamped with the "
            f"queued_report code (got {stamped!r})"
        )

        # The parent leaves the WC scan set — the production posture
        # (the notice's own enqueue flips WC→RUNNING), so tick 2
        # exercises the END-EVALUATION consumer of the tri-state (the
        # ``_wedge_code_still_holds`` queued_report leg).
        with SQLModelSessionLib(engine) as s:
            row = s.get(Instance, parent)
            row.status = "running"
            s.add(row)
            s.commit()

        # The queue read now FAILS — with the call-counter control.
        _boom, _calls = self._counting_boom("queue read exploded")
        monkeypatch.setattr(
            s1_manager._queue_repository, "get_by_instance", _boom
        )

        await w.run_once()

        assert _calls, (
            "F5 SELF-GUARD: the queue-read injection never fired"
        )
        assert len(_s4_notice_rows(engine)) == 1, (
            "F5: a queue-read failure must emit ZERO new notices"
        )
        assert w._wedge_notified.get(parent) == stamped, (
            "F5: a queue-read failure must leave the episode map "
            f"UNCHANGED (got {w._wedge_notified.get(parent)!r}, "
            f"expected {stamped!r})"
        )
