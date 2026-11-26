"""Tests for the normal ``root_waiting_children`` write path in
``ChildReportsService``.

Phase 5: the legacy ``_has_no_active_message_job`` carve-out guard
(``daemon/services/child_reports.py``) was REMOVED. The guard always
returned ``True`` after D13 (Phase 2) eliminated MESSAGE ``JobItem``
rows — there is no longer any MESSAGE-worker lifecycle for the guard
to observe. The bus (``DependencyBus``) is the post-D13 authoritative
completion signal, and the own-queue ``pending_count`` is the
authoritative signal that real queued work exists.

These tests verify the **normal** path: a root instance with pending
own-queue messages and an active MESSAGE job proceeds to set
``WAITING_CHILDREN`` and returns ``root_waiting_children``. The
``WAITING_CHILDREN`` status set is retained for graceful-degradation
watchers and FIFO carve-out SQL compatibility (display only — the bus
is authoritative).

The tests run against a real in-memory SQLite engine (StaticPool for
cross-thread safety) with minimal manager mocks, following the pattern
in ``tests/test_deadlock_fix.py``.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select as sm_select

# Model imports — required so SQLModel.metadata sees the tables when
# create_all() runs on the test engine.
from daemon.repositories.dependency_bus.models import DependencyWatcher, DependencyWatcherState  # noqa: F401  (for SQLModel.metadata.create_all)
from daemon.repositories.dependency_bus.repository import DependencyWatcherRepository
from daemon.repositories.event.models import Event, EventKind  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem, JobStatus
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import ReportInjection  # noqa: F401  (SQLModel.metadata)
from daemon.repositories.report_injection.repository import ReportInjectionRepository
from daemon.repositories.task.models import Task  # noqa: F401
from daemon.repositories.task.models import TaskStatus
from daemon.services import child_reports as _child_reports_module
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.write_pause_guard import WritePauseGuard



# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# Phase 4 cleanup removed ``status_to_admission`` from
# ``daemon.repositories.job_queue.models``. Redefined here for test
# seeds that derive ``admission_state`` from a ``status`` value.
def status_to_admission(status):  # noqa: ANN001,ANN201 — test-local re-export
    # JobStatus → AdmissionState (Phase 4 dual-write contract)
    # + AdmissionState identity (Phase 5: callers may pass either vocab).
    return {
        # JobStatus source values
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
        # AdmissionState source values (identity map — pass-through)
        "queued": "queued",
        "active": "active",
        "done": "done",
        "dead": "dead",
    }.get(status, "queued")



# ─────────────────────────────────────────────────────────────────────────────
# Engine + service helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def bus(engine: Engine):
    """Started DependencyBus bound to the test engine (autouse).

    Phase 5: ``_process_child_completion_db_sync`` raises a hard error
    when the bus singleton is ``None`` (bus is the sole completion
    authority — see A8 in ``child_reports.py``). The carve-out tests
    need a wired bus so the ``root_skipped_terminal_job`` /
    ``root_waiting_children`` branches can resolve the bus state
    without raising. Autouse so every test in this module gets the
    wired bus; the other tests do not call into the bus-authority
    code path, so the wiring is a no-op for them.
    """
    import asyncio
    repo = DependencyWatcherRepository(engine)
    b = DependencyBus(repo)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                ex.submit(asyncio.run, b.start()).result()
        else:
            loop.run_until_complete(b.start())
    except RuntimeError:
        asyncio.run(b.start())
    set_dependency_bus(b)
    # Harness fix (revive-wedge C2a): ``DependencyWatcherRepository.
    # count_pending_for_target`` opens its OWN ``Session`` on the test
    # engine. Under ``StaticPool`` (one shared connection) that session's
    # close() rolls back the production transaction on the same
    # connection, so a minted report row vanishes before the test can
    # observe it. In production (Postgres) every session has its own
    # connection and this never happens. These tests seed no watchers, so
    # the correct value is 0; returning it without opening a session
    # keeps the production transaction intact.
    b._repo.count_pending_for_target = lambda target_instance_id: 0
    try:
        yield b
    finally:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    ex.submit(asyncio.run, b.stop()).result()
            else:
                loop.run_until_complete(b.stop())
        except RuntimeError:
            asyncio.run(b.stop())
        set_dependency_bus(None)


@pytest.fixture(autouse=True)
def _reset_dependency_bus():
    """Ensure no DependencyBus singleton leaks between tests.

    The ``bus`` fixture above wires a real bus; this autouse fixture
    is a safety net that clears any leftover singleton from a previous
    test that did not use ``bus`` (e.g. direct ``set_dependency_bus``
    calls in test bodies).
    """
    set_dependency_bus(None)
    yield
    set_dependency_bus(None)


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    """Build a real ``ChildReportsService`` with a mock manager that
    exposes only the attributes ``_process_child_completion_db_sync`` needs.

    Mirrors the helper in tests/test_deadlock_fix.py — uses ``__new__`` to
    skip ``__init__`` and bind attributes manually.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._checkpointer = None
    manager._live_hub = None  # SSE no-op (guarded on truthiness)
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None  # lifecycle event publish is guarded
    service._strand_heal_inflight = set()
    return service


def _seed_root_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Insert a root Instance row (parent_id=None)."""
    iid = instance_id or f"root-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        inst = Instance(
            instance_id=iid,
            agent_id="leader",
            agent_name="leader",
            agent_dir="/tmp/leader",
            parent_id=None,
            status=status,
            version=1,
            instance_metadata={},
        )
        session.add(inst)
        session.commit()
    return iid


def _seed_message(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str | None = None,
    status: str = MessageStatus.READY.value,
) -> str:
    """Insert a MessageQueue row for the given instance."""
    mid = message_id or f"msg-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        msg = MessageQueue(
            message_id=mid,
            instance_id=instance_id,
            content="stale duplicate message",
            type=MessageType.HUMAN.value,

        )
        session.add(msg)
        session.commit()
    return mid


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def _seed_dependency_watcher(
    engine: Engine,
    *,
    target_instance_id: str,
    source_task_id: str | None = None,
    state: str = DependencyWatcherState.PENDING.value,
) -> str:
    """Insert a DependencyWatcher row targeting the given instance."""
    sid = source_task_id or f"task-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        watcher = DependencyWatcher(
            source_task_id=sid,
            target_instance_id=target_instance_id,
            follow_up_payload={"kind": "follow_up"},
            watcher_metadata={"child_id": sid},
            state=state,
        )
        session.add(watcher)
        session.commit()
    return sid


def _seed_message_job_item(
    engine: Engine,
    *,
    instance_id: str,
    job_id: str | None = None,
    status: str = AdmissionState.ACTIVE.value,
) -> str:
    """Insert a JobItem row of ``job_type='message'`` for the given instance.

    Used by the post-Phase-5 regression tests to verify that residual
    MESSAGE JobItem rows (no longer created by the post-D13 dispatch
    path) do NOT block the WAITING_CHILDREN write — the legacy
    ``_has_no_active_message_job`` carve-out guard was removed.
    """
    jid = job_id or f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        job = JobItem(
            job_id=jid,
            agent_id="leader",
            agent_dir="/tmp/leader",
            message="stale message job",
            source="api",
            instance_id=instance_id,
            job_type="message",

            admission_state=status_to_admission(status),
        )
        session.add(job)
        session.commit()
    return jid


class TestRootPendingMessagesNormalPath:
    """When the root instance has pending messages in its own queue, the
    WAITING_CHILDREN status write proceeds (was previously gated by
    ``_has_no_active_message_job`` — removed in Phase 5). The bus is the
    authoritative completion signal; this status set is retained for
    graceful-degradation watchers and FIFO carve-out SQL compatibility
    (display only)."""

    def test_root_with_pending_message_task_transitions_to_waiting_children(
        self, engine: Engine
    ):
        """A root instance with pending_count > 0 AND a non-terminal
        (processing) MESSAGE job should proceed to set WAITING_CHILDREN
        and return ``root_waiting_children``.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        _seed_message(engine, instance_id=root_id)

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_waiting_children"
        assert result.instance_id == root_id

        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.WAITING_CHILDREN.value


# ─────────────────────────────────────────────────────────────────────────────
# Tests for deferred_waiting_children (newly-unconditional path)
#
# After Phase 5 removed the ``_has_no_active_message_job`` guard, the
# ``deferred_waiting_children`` outcome (around line ~1149 / ~1195 / ~1300
# in ``daemon/services/child_reports.py``) is reachable whenever the bus
# has PENDING watchers for the instance — no carve-out gating.
#
# Test A documents the newly-unconditional path:
#   - A parent instance with PENDING bus watchers correctly gets the
#     ``deferred_waiting_children`` outcome.
#
# Test B documents the post-removal invariant:
#   - A stale MESSAGE ``JobItem`` row (if any residual exists) does NOT
#     block the WAITING_CHILDREN write — the guard is gone, so the
#     pending own-queue message alone drives the outcome.
# ─────────────────────────────────────────────────────────────────────────────


class TestDeferredWaitingChildrenNewlyUnconditionalPath:
    """Tests for the ``deferred_waiting_children`` outcome — newly
    unconditionally reachable after the Phase 5 removal of the
    ``_has_no_active_message_job`` guard.

    The bus (``DependencyBus``) is the post-D13 authoritative completion
    signal; when the bus has PENDING watchers for an instance, the
    completion gate defers with ``deferred_waiting_children`` and the
    instance stays in its current status (no WAITING_CHILDREN transition
    on the early-return path at line ~1149)."""

    def test_root_with_pending_bus_watcher_returns_deferred_waiting_children(
        self, engine: Engine
    ):
        """A root instance with a PENDING ``DependencyWatcher`` row
        correctly yields the ``deferred_waiting_children`` outcome.

        This documents that the newly-unconditional path (no longer
        gated by ``_has_no_active_message_job``) works as expected:
        the bus pending-children count is the authoritative signal and
        the completion gate defers without transitioning status.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        _seed_dependency_watcher(
            engine, target_instance_id=root_id, state=DependencyWatcherState.PENDING.value
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "deferred_waiting_children"
        assert result.instance_id == root_id
        assert result.parent_id is None

        # The early-return deferred path does NOT transition status
        # (Phase 4: instances stay in their current status while
        # children run; the bus is authoritative).
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.RUNNING.value

    def test_root_with_fired_bus_watcher_proceeds_to_completion_path(
        self, engine: Engine
    ):
        """A root instance whose bus watcher has already FIRED is no
        longer blocked by a residual PENDING watcher — the gate
        consults the live bus state, not the historical record.

        This documents that the deferred path is conditional on the
        CURRENT bus state (PENDING watchers), not on whether watchers
        EVER existed for the instance.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        # All watchers FIRED — the bus sees zero pending children.
        _seed_dependency_watcher(
            engine,
            target_instance_id=root_id,
            state=DependencyWatcherState.FIRED.value,
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        # No pending bus watchers + no pending own-queue messages →
        # root_completed. (This is the control case that demonstrates
        # the deferred path is conditional, not unconditional.)
        assert result.outcome == "root_completed"
        assert result.instance_id == root_id

        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.COMPLETED.value


class TestStaleMessageJobDoesNotBlockWaitingChildren:
    """Post-removal invariant: a residual MESSAGE ``JobItem`` row must
    NOT block the WAITING_CHILDREN write.

    Before Phase 5, ``_has_no_active_message_job`` queried
    ``job_queue_items`` for active MESSAGE jobs (``job_type='message'``
    with status IN (PENDING, PROCESSING) and ``deleted_at IS NULL``)
    and could gate the WAITING_CHILDREN write. After Phase 5 removed
    the guard, the WAITING_CHILDREN write proceeds purely on the
    own-queue ``pending_count`` signal — residual MESSAGE JobItem rows
    are ignored.

    These tests insert a MESSAGE JobItem row alongside a pending
    own-queue message and assert that the write proceeds with
    ``root_waiting_children``. If someone re-introduces a MESSAGE-
    job-active guard in the future, these tests will fail loudly.
    """

    def test_stale_processing_message_job_does_not_block_waiting_children(
        self, engine: Engine
    ):
        """A residual PROCESSING MESSAGE ``JobItem`` row does NOT block
        the WAITING_CHILDREN write. Outcome must be
        ``root_waiting_children`` (driven by the pending own-queue
        message alone — the MESSAGE-job guard is gone).
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        # Pending own-queue message — drives the own-queue pending_count > 0 path.
        _seed_message(engine, instance_id=root_id)
        # Residual MESSAGE JobItem — would have been an active MESSAGE worker
        # before D13. After Phase 5 guard removal, this row is invisible to
        # the completion gate.
        _seed_message_job_item(
            engine,
            instance_id=root_id,
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        # Stale MESSAGE JobItem must NOT have blocked the write.
        assert result.outcome == "root_waiting_children"
        assert result.instance_id == root_id

        # WAITING_CHILDREN write committed — the post-removal invariant.
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.WAITING_CHILDREN.value

            # The residual MESSAGE JobItem is still in the table —
            # untouched by the completion gate.
            from sqlmodel import select as _sa_select
            jobs = session.exec(
                _sa_select(JobItem).where(
                    JobItem.instance_id == root_id,
                    JobItem.job_type == "message",
                )
            ).all()
            assert len(jobs) == 1
            # Phase 5 cleanup: ``JobItem.status`` mirror column was
            # dropped; the queue-side authority is ``admission_state``.
            assert jobs[0].admission_state == AdmissionState.ACTIVE.value

    def test_stale_pending_message_job_does_not_block_waiting_children(
        self, engine: Engine
    ):
        """A residual PENDING MESSAGE ``JobItem`` row (the other
        non-terminal state the legacy guard checked) also does NOT
        block the WAITING_CHILDREN write.

        Mirrors the PROCESSING case above — covers both
        ``JobStatus.PENDING`` and ``JobStatus.PROCESSING`` (the two
        non-terminal states the legacy ``_has_no_active_message_job``
        guard scanned for).
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        _seed_message(engine, instance_id=root_id)
        _seed_message_job_item(
            engine,
            instance_id=root_id,
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "root_waiting_children"
        assert result.instance_id == root_id

        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.WAITING_CHILDREN.value


def _seed_child_instance(
    engine: Engine,
    *,
    parent_id: str,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Insert a child Instance row whose parent_id links it to the root."""
    cid = f"child-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        inst = Instance(
            instance_id=cid,
            agent_id="tester",
            agent_name="tester",
            agent_dir="/tmp/tester",
            parent_id=parent_id,
            status=status,
            version=1,
            instance_metadata={},
        )
        session.add(inst)
        session.commit()
    return cid


class TestLiveChildrenDefenseInDepth:
    """Regression for Inc 2026-08-02 "leader completed while tester child
    still running".

    The bus gate (``count_pending_for_target_sync == 0``) trusts the bus
    ``dependency_watchers`` rows. A silent raw-SQL writer (the
    ``reconcile_turn_mirror`` cancel guard) can zero the bus count while a
    child instance is genuinely still running. This defense-in-depth gate
    consults the ``instances`` table directly: a root with any non-terminal
    child must NEVER reach ``COMPLETED``, even when the bus reports zero
    pending watchers.
    """

    def test_live_child_blocks_completion_despite_empty_bus(
        self, engine: Engine
    ):
        """Bus reports 0 pending watchers, but a child instance is still
        running — the root must defer (``deferred_waiting_children``),
        NOT complete. This is the exact incident scenario.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        # No PENDING bus watchers — the bus would report zero pending
        # (mirrors the post-cancel state from reconcile_turn_mirror).
        # A live (non-terminal) child remains in the instances table.
        _seed_child_instance(
            engine, parent_id=root_id, status=InstanceStatus.RUNNING.value
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        assert result.outcome == "deferred_waiting_children"
        assert result.instance_id == root_id

        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.WAITING_CHILDREN.value

    def test_terminal_child_does_not_block_completion(
        self, engine: Engine
    ):
        """A child that has reached a terminal status (completed/error/
        terminated/failed) must NOT trip the live-children gate — the root
        is free to complete. Confirms the gate only fires on genuinely
        live children.
        """
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        _seed_child_instance(
            engine,
            parent_id=root_id,
            status=InstanceStatus.COMPLETED.value,
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        # No pending bus watchers + no live children + no own-queue
        # messages -> root_completed.
        assert result.outcome == "root_completed"
        assert result.instance_id == root_id

        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.COMPLETED.value


# ─────────────────────────────────────────────────────────────────────────────
# Regression: ghost-child exclusion (Inc 2026-08-03
# tester-stuck-waiting-children-orphaned-idle-worker)
#
# A ghost child is one spawned but whose dispatch FAILED — it sits at
# ``status='idle'``, ``version=1``, with ZERO ``message_queue`` and
# ``task`` rows (no work ever queued, no turn ever ran). Because ``idle``
# is not in ``TERMINAL_STATUSES``, both completion guards counted such a
# child as active forever and permanently wedged the parent at
# ``waiting_children``. The fix (``ChildReportsService._ghost_child_filter``)
# excludes ghosts from both the root live-children gate
# (``child_reports.py:~1531``) and the non-root active-children guard
# (``child_reports.py:~1900``).
#
# Verified against production ``ensemble_prod`` (PG): the reported ghost
# ``33477fe4`` (idle/v1/0-msgs/0-tasks) AND 8 additional stale wedges
# (April–May, all idle/v1 developer ghosts) matched this exact fingerprint.
# ─────────────────────────────────────────────────────────────────────────────


def _seed_child_instance_full(
    engine: Engine,
    *,
    parent_id: str,
    status: str = InstanceStatus.RUNNING.value,
    version: int = 1,
    seed_message: bool = False,
    seed_task: bool = False,
) -> str:
    """Insert a child Instance row with fine-grained control over ghost
    characteristics (version) + optional work rows (message_queue / task).

    Used by the ghost-exclusion tests to construct (a) a true ghost
    (idle, v1, no work rows) and (b) an idle child that HAS queued work
    (NOT a ghost — must still block its parent).
    """
    cid = f"child-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        inst = Instance(
            instance_id=cid,
            agent_id="worker",
            agent_name="worker",
            agent_dir="/tmp/worker",
            parent_id=parent_id,
            status=status,
            version=version,
            instance_metadata={},
        )
        session.add(inst)
        if seed_message:
            session.add(MessageQueue(
                message_id=f"msg-{uuid.uuid4().hex[:8]}",
                instance_id=cid,
                content="queued task",
                type=MessageType.AGENT.value,
            ))
        if seed_task:
            session.add(Task(instance_id=cid))
        session.commit()
    return cid


class TestGhostChildExclusion:
    """Regression for Inc 2026-08-03
    ``tester-stuck-waiting-children-orphaned-idle-worker``.

    See the module-level docstring above for the full incident summary.
    """

    # ── Root live-children gate (child_reports.py live_children_stmt) ──

    def test_root_idle_ghost_child_does_not_block_completion(self, engine: Engine):
        """A root with ONLY an idle ghost child (v1, no msgs, no tasks)
        must NOT defer — the ghost's dispatch already failed and it can
        never produce a completion report, so the root is free to
        complete. This is the exact incident scenario (leader wedged by
        a ghost worker)."""
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        _seed_child_instance_full(
            engine, parent_id=root_id, status=InstanceStatus.IDLE.value,
            version=1, seed_message=False, seed_task=False,
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-x",
            last_content="assistant text",
        )

        assert result.outcome == "root_completed", (
            f"idle ghost must not block root completion; got {result.outcome}"
        )
        with Session(engine) as session:
            assert session.get(Instance, root_id).status == InstanceStatus.COMPLETED.value

    def test_root_idle_child_with_queued_message_still_blocks(self, engine: Engine):
        """An idle child that HAS a ``message_queue`` row is NOT a ghost —
        work is genuinely queued and the child will run a turn. It must
        still block the root (guards against blanket-excluding ``idle``)."""
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        _seed_child_instance_full(
            engine, parent_id=root_id, status=InstanceStatus.IDLE.value,
            version=1, seed_message=True, seed_task=False,
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-x",
            last_content="assistant text",
        )

        assert result.outcome == "deferred_waiting_children", (
            f"idle child with queued work must block root; got {result.outcome}"
        )

    def test_root_idle_child_with_queued_task_still_blocks(self, engine: Engine):
        """Same as above but the dispatched work is a ``task`` row rather
        than a raw message — the ghost filter requires BOTH tables empty,
        so a queued task keeps the idle child blocking."""
        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        _seed_child_instance_full(
            engine, parent_id=root_id, status=InstanceStatus.IDLE.value,
            version=1, seed_message=False, seed_task=True,
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-x",
            last_content="assistant text",
        )

        assert result.outcome == "deferred_waiting_children", (
            f"idle child with queued task must block root; got {result.outcome}"
        )

    # ── Non-root active-children guard (child_reports.py active_children) ──

    def test_nonroot_idle_ghost_child_completes(self, engine: Engine):
        """The reported incident's exact dispatch site: a non-root tester
        with one idle ghost child (spawn failed, never dispatched) must
        NOT defer its completion_report to the leader. Without the fix
        this fired on every turn and permanently wedged both the tester
        and the leader at ``waiting_children``."""
        service = _build_child_reports_service(engine)
        leader_id = _seed_root_instance(engine)
        tester_id = _seed_child_instance_full(
            engine, parent_id=leader_id, status=InstanceStatus.RUNNING.value,
        )
        # The ghost that wedged the tester pre-fix.
        _seed_child_instance_full(
            engine, parent_id=tester_id, status=InstanceStatus.IDLE.value,
            version=1, seed_message=False, seed_task=False,
        )

        result = service._process_child_completion_db_sync(
            instance_id=tester_id,
            completed_message_id="msg-current",
            last_content="Testing Complete: all tests green",
        )

        assert result.outcome == "regular_child_completed", (
            f"idle ghost must not defer non-root completion; got {result.outcome}"
        )

    def test_nonroot_running_child_still_defers(self, engine: Engine):
        """Sanity: a genuinely-active (running) child still defers the
        non-root parent — the ghost filter must not over-exclude."""
        service = _build_child_reports_service(engine)
        leader_id = _seed_root_instance(engine)
        tester_id = _seed_child_instance_full(
            engine, parent_id=leader_id, status=InstanceStatus.RUNNING.value,
        )
        _seed_child_instance_full(
            engine, parent_id=tester_id, status=InstanceStatus.RUNNING.value,
        )

        result = service._process_child_completion_db_sync(
            instance_id=tester_id,
            completed_message_id="msg-x",
            last_content="...",
        )

        assert result.outcome == "child_still_running_defer"


# =============================================================================
# DiD — natural completion racing a recovered PENDING marker
# (deep-review C-DiD, 2026-08-20)
# =============================================================================


class TestNaturalCompletionRacingRecoveredMarker:
    """Defense-in-depth regression: when the Phase 2 sweep / router
    has just transitioned a DEFERRED marker to PENDING
    (``transition_deferred_to_pending``), the child's natural
    completion path would race the obligation-triple partial unique
    index (``uq_report_injections_oblig_triple``) — the recovered
    PENDING row already exists, the natural INSERT hits
    ``IntegrityError``. The fix absorbs it and returns
    ``idempotency_skip`` — the recovered row owns delivery via the
    worker pool / claim_for_task_delivery path.

    Without the fix, the natural path crashes and the recovered
    PENDING row's delivery never completes.
    """

    def test_natural_completion_races_recovered_pending_marker(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ):
        """Seed a non-terminal ReportInjection row with the same
        obligation triple; trigger the child's natural completion;
        assert ``idempotency_skip`` outcome (no crash, no duplicate
        row).

        F1 FIX (2026-08-20, de-vacuous): the previous test seeded
        the child with ``status=COMPLETED`` so the head guard at
        ``child_reports.py:1754-1768`` short-circuited with its
        OWN ``idempotency_skip`` outcome — the C-DiD INSERT was
        never reached, the discriminator was never called, and
        deleting the entire ``try/except`` left the suite green.
        Reviewer instrumentation measured discriminator reach-count
        = 0. Fix by seeding the child as ``RUNNING`` so the head
        guard falls through, AND asserting discriminator reach
        explicitly (count >= 1) — the test now FAILS if the C-DiD
        branch is removed.

        F3 REGRESSION (2026-08-20): the production code path MUST
        also leave the child COMPLETED transition intact (the
        pre-fix ``session.rollback()`` discarded the COMPLETED
        UPDATE; the F3 SAVEPOINT-scoped rollback preserves it).
        Assert ``status=COMPLETED`` on a fresh-session read.
        """
        from daemon.repositories.report_injection.models import (
            ReportInjection,
            ReportInjectionState,
        )
        from daemon.services.child_reports import (
            _is_obligation_triple_integrity_error as real_discriminator,
        )

        # Reach instrumentation — wrap the discriminator with a
        # counting shim. The C-DiD ``except`` block MUST invoke the
        # discriminator; if the try/except is removed the count
        # stays at 0 and the test fails. The shim delegates to the
        # real function so discrimination behaviour is preserved.
        discriminator_calls = {"n": 0}

        def counting_discriminator(exc):
            discriminator_calls["n"] += 1
            return real_discriminator(exc)

        monkeypatch.setattr(
            "daemon.services.child_reports._is_obligation_triple_integrity_error",
            counting_discriminator,
        )

        service = _build_child_reports_service(engine)
        parent_id = _seed_root_instance(engine)
        # F1 FIX: seed child as RUNNING so the head guard at
        # ``child_reports.py:1754-1768`` falls through and the
        # natural completion path reaches the C-DiD INSERT. Was
        # ``COMPLETED`` — short-circuited the head guard and made
        # the test vacuous.
        child_id = _seed_child_instance_full(
            engine,
            parent_id=parent_id,
            status=InstanceStatus.RUNNING.value,
        )

        # Seed a non-terminal ReportInjection row with the SAME
        # obligation triple the natural path will try to write.
        # This simulates the Phase 2 sweep having just transitioned
        # a DEFERRED marker → PENDING.
        child_msg_id = "msg-racing"
        report_msg_id = f"report-{uuid.uuid4().hex[:8]}"
        with Session(engine) as session:
            session.add(
                ReportInjection(
                    injection_id=f"inj-{uuid.uuid4().hex[:8]}",
                    parent_instance_id=parent_id,
                    child_instance_id=child_id,
                    child_message_id=child_msg_id,
                    report_message_id=report_msg_id,
                    content="previously delivered",
                    state=ReportInjectionState.PENDING.value,
                    recovery_attempted_at=datetime.now(
                        timezone.utc
                    ).isoformat(),
                )
            )
            session.commit()

        # Trigger the natural completion path — the inline INSERT
        # hits the obligation-triple partial unique index and
        # raises IntegrityError. The fix absorbs it and returns
        # ``idempotency_skip``.
        result = service._process_child_completion_db_sync(
            instance_id=child_id,
            completed_message_id=child_msg_id,
            last_content="... (natural enqueue, racing recovered row)",
        )

        # Reach assertion (F1 FIX): the discriminator MUST have
        # been called — the C-DiD ``except`` block was entered and
        # consulted ``_is_obligation_triple_integrity_error``. If
        # the try/except is removed, this count stays at 0 and the
        # test fails. The previous test only asserted the outcome,
        # which the head guard also emits, so it could not
        # distinguish head-guard short-circuit from C-DiD reach.
        assert discriminator_calls["n"] >= 1, (
            "the C-DiD IntegrityError catch was NOT entered — the "
            "discriminator was never consulted. Either the head "
            "guard short-circuited (vacuous test) or the try/except "
            "was removed. The branch must be exercised."
        )

        assert result.outcome == "idempotency_skip", (
            "natural completion × recovered PENDING race MUST return "
            "idempotency_skip (DiD defense — the recovered row owns "
            f"delivery); got outcome={result.outcome}"
        )
        assert result.instance_id == child_id
        assert result.parent_id == parent_id

        # The pre-existing PENDING row is preserved (no duplicate).
        with Session(engine) as session:
            rows = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.parent_instance_id == parent_id
                ).where(
                    ReportInjection.child_instance_id == child_id
                ).where(
                    ReportInjection.child_message_id == child_msg_id
                )
            ).all()
            assert len(rows) == 1, (
                f"expected exactly one (recovered) ReportInjection "
                f"row for the obligation triple; got {len(rows)}"
            )
            assert rows[0].state == ReportInjectionState.PENDING.value, (
                "recovered PENDING row MUST remain PENDING — the "
                "natural path no-ops via idempotency_skip"
            )

        # F3 REGRESSION (2026-08-20): the SAVEPOINT-scoped
        # rollback must preserve the child's ``status=COMPLETED``
        # UPDATE made earlier in the same transaction. The pre-fix
        # ``session.rollback()`` discarded the COMPLETED transition
        # along with the injection INSERT — the child was wedged
        # non-terminal forever, the parent deferral was permanent,
        # and the sweep re-hit the same error every cycle.
        # Fresh-session read confirms the COMPLETED transition
        # survived the SAVEPOINT-scoped rollback.
        with Session(engine) as session:
            child_row = session.get(Instance, child_id)
            assert child_row.status == InstanceStatus.COMPLETED.value, (
                "F3 REGRESSION: child instance must transition to "
                "COMPLETED even when the C-DiD race fires "
                f"(SAVEPOINT preserves the outer transaction — "
                f"only the injection INSERT was rolled back). Got "
                f"status={child_row.status!r}."
            )

    def test_unrelated_integrity_error_propagates(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST-DEEP-REVIEW (Y2, 2026-08-20): an unrelated
        ``IntegrityError`` on the obligation-triple INSERT (e.g. an
        FK violation or a non-triple UNIQUE) MUST NOT be mis-treated
        as ``idempotency_skip`` — it must propagate so the bug is
        visible. The discrimination rule
        (``_is_obligation_triple_integrity_error``) checks the
        constraint name (PG) or column set (SQLite) and re-raises
        otherwise.

        We simulate an unrelated error by patching ``Session.flush``
        SELECTIVELY — only raises the synthetic ``IntegrityError``
        when a ``ReportInjection`` is in the pending-new set (the
        C-DiD INSERT site). Earlier flushes (head-guard autoflush,
        F3 outer flush for ``message_queue`` / ``task``) pass through
        unchanged so the function can reach the C-DiD branch.

        F2 FIX (2026-08-20, de-vacuous): the previous test patched
        ``Session.flush`` to raise on EVERY call. With a
        ``COMPLETED`` child, the head guard short-circuited with
        its own ``idempotency_skip`` outcome and no flush ran;
        BUT — autoflush on ``session.get`` (head-guard read at
        ``child_reports.py:1692``) fired the patched flush and the
        synthetic error propagated from there, not from the C-DiD
        INSERT. The try/except at the C-DiD INSERT was NEVER
        entered; the discriminator was NEVER called. Fix by:

        1. Seeding the child as ``RUNNING`` so the head guard
           falls through and execution reaches the C-DiD INSERT.
        2. Using a selective flush that inspects ``session.new``
           and only raises when a ``ReportInjection`` is staged
           (the C-DiD INSERT site). The F3 outer flush (no
           ``ReportInjection`` pending) and any autoflush pass
           through.
        3. Adding a reach assertion (discriminator called ≥ 1) so
           the test FAILS if the C-DiD ``try/except`` is removed.
        """
        from sqlalchemy.exc import IntegrityError as SAIntegrityError
        from daemon.repositories.report_injection.models import ReportInjection
        from daemon.services.child_reports import (
            _is_obligation_triple_integrity_error as real_discriminator,
        )

        # Reach instrumentation — wrap the discriminator with a
        # counting shim. The C-DiD ``except`` block MUST invoke
        # the discriminator; if the try/except is removed the
        # count stays at 0 and the test fails.
        discriminator_calls = {"n": 0}

        def counting_discriminator(exc):
            discriminator_calls["n"] += 1
            return real_discriminator(exc)

        monkeypatch.setattr(
            "daemon.services.child_reports._is_obligation_triple_integrity_error",
            counting_discriminator,
        )

        service = _build_child_reports_service(engine)
        parent_id = _seed_root_instance(engine)
        # F2 FIX: seed child as RUNNING so the head guard at
        # ``child_reports.py:1754-1768`` falls through and the
        # natural completion path reaches the C-DiD INSERT. Was
        # ``COMPLETED`` — short-circuited the head guard.
        child_id = _seed_child_instance_full(
            engine,
            parent_id=parent_id,
            status=InstanceStatus.RUNNING.value,
        )

        # Build a synthetic FK-violation-style error. SQLite renders
        # FK violations as ``FOREIGN KEY constraint failed`` — this
        # message contains NO obligation-triple column names, so the
        # discriminator MUST return False and the caller MUST
        # re-raise.
        fk_orig = RuntimeError(
            "FOREIGN KEY constraint failed: report_injections.parent_instance_id"
        )
        # ``IntegrityError`` constructor requires (stmt, params, orig).
        synthetic_err = SAIntegrityError(
            "INSERT INTO report_injections (...)",
            params={},
            orig=fk_orig,
        )

        # F2 FIX (selective flush): patch ``Session.flush`` so it
        # only raises the synthetic error when a ``ReportInjection``
        # is in the pending-new set (the C-DiD INSERT site). Earlier
        # flushes (head-guard autoflush, F3 outer flush for
        # ``message_queue`` / ``task`` INSERTs) pass through to the
        # real flush so execution can reach the C-DiD INSERT. The
        # ``session.new`` set contains ORM-mapped objects that have
        # been ``session.add``-ed but not yet flushed — this is the
        # canonical SQLAlchemy hook for inspecting what a flush
        # would emit.
        from sqlmodel import Session as SQLModelSession

        original_flush = SQLModelSession.flush
        flush_calls = {"n": 0}

        def _selective_flush(self, *args, **kwargs):
            flush_calls["n"] += 1
            # Inspect pending-new objects. If any is a
            # ``ReportInjection``, this is the C-DiD INSERT site —
            # raise the synthetic FK-style error.
            new_objs = list(getattr(self, "new", set()) or set())
            if any(isinstance(obj, ReportInjection) for obj in new_objs):
                raise synthetic_err
            return original_flush(self, *args, **kwargs)

        monkeypatch.setattr(SQLModelSession, "flush", _selective_flush)
        # Keep the original available for any helper session that
        # the service may open (e.g. the bus / repo paths).
        _ = original_flush

        # The natural completion path MUST surface the unrelated
        # IntegrityError — NOT swallow it as ``idempotency_skip``.
        with pytest.raises(SAIntegrityError) as exc_info:
            service._process_child_completion_db_sync(
                instance_id=child_id,
                completed_message_id="msg-unrelated-integrity",
                last_content="... (would race if it could)",
            )

        # Reach assertion (F2 FIX): the discriminator MUST have
        # been called — the C-DiD ``except`` block was entered and
        # consulted ``_is_obligation_triple_integrity_error``. If
        # the try/except is removed, the synthetic error would
        # still propagate from the (patched) flush, but the
        # discriminator count stays at 0 and the test fails.
        assert discriminator_calls["n"] >= 1, (
            "the C-DiD IntegrityError catch was NOT entered — the "
            "discriminator was never consulted. Either the head "
            "guard short-circuited (vacuous test) or the try/except "
            "was removed. The branch must be exercised."
        )

        # The propagated error is the SAME synthetic error we
        # injected (no wrapping into a new exception type — bare
        # ``raise``).
        assert exc_info.value is synthetic_err, (
            "unrelated IntegrityError MUST be re-raised as-is (bare "
            "``raise``); wrapping would hide the original exception"
        )
        assert flush_calls["n"] >= 1, (
            "the patched Session.flush MUST have been invoked at "
            "least once before the error propagated (the selective "
            "flush only raises on ReportInjection in session.new; "
            "this confirms the outer F3 flush ran first)"
        )

        # Discrimination rule sanity check: the synthetic FK error
        # does NOT match the obligation-triple pattern. Belt and
        # braces — the re-raise already proves the caller's logic.
        assert real_discriminator(synthetic_err) is False, (
            "the synthetic FK error MUST NOT be mis-classified as "
            "obligation-triple (it does not contain all three "
            "obligation-triple column names)"
        )
    def test_non_integrity_error_rolls_back_savepoint_and_propagates(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F4 FIX (2026-08-20, SAVEPOINT exception-leak hardening): a
        non-IntegrityError raised at the inner flush (e.g. an
        ``OperationalError`` on DB disconnect, a ``RuntimeError`` from
        a downstream invariant) MUST:

        (a) roll back the SAVEPOINT (no orphaned ReportInjection row);
        (b) propagate the exception with identity preserved (bare
            ``raise`` — no wrapping into a new exception type);
        (c) trigger coarse-rollback containment: the WHOLE outer
            transaction is rolled back cleanly (no orphaned injection
            row, no half-flushed message_queue / task rows, child
            status stays non-terminal).

        Without the F4 fix, the SAVEPOINT was left open and was
        contained only by the outer ``WriteGuardSession`` close — which
        rolls back the WHOLE transaction (data-safe but coarse, and the
        leaked SAVEPOINT relied on close-time cleanup). The fix
        broadens the catch from ``except IntegrityError`` to ``except
        Exception`` so ``nested.rollback()`` fires for ANY exception
        raised inside the inner try BEFORE the SAVEPOINT leaks.

        Reach instrumentation: the C-DiD ``except Exception`` block
        must be ENTERED, so we wrap ``logger.warning`` with a counting
        shim keyed to the F4 log line. If the ``except Exception`` is
        removed or the SAVEPOINT logic is restructured to swallow the
        error, the count stays at 0 and the test fails.
        """
        from daemon.repositories.report_injection.models import ReportInjection
        from daemon.repositories.message_queue.models import MessageQueue
        from daemon.repositories.task.models import Task, TaskType
        from sqlmodel import Session as SQLModelSession

        # Reach instrumentation — count warning emissions from the C-DiD
        # ``except Exception`` block. The fix emits a logger.warning
        # with the prefix "non-IntegrityError raised during
        # ReportInjection INSERT" whenever the broadened catch fires.
        from daemon.services import child_reports as cr_module

        non_ie_warning_calls = {"n": 0}
        original_warning = cr_module.logger.warning

        def counting_warning(msg, *args, **kwargs):
            if "non-IntegrityError raised during ReportInjection INSERT" in str(msg):
                non_ie_warning_calls["n"] += 1
            return original_warning(msg, *args, **kwargs)

        monkeypatch.setattr(cr_module.logger, "warning", counting_warning)

        service = _build_child_reports_service(engine)
        parent_id = _seed_root_instance(engine)
        child_id = _seed_child_instance_full(
            engine,
            parent_id=parent_id,
            status=InstanceStatus.RUNNING.value,
        )

        # Synthetic non-IntegrityError (RuntimeError is the simplest
        # non-IE exception that models a downstream invariant failure).
        # Identity-preservation is asserted below via ``is``.
        synthetic_err = RuntimeError(
            "synthetic transient failure on connection.execute"
        )

        # F4 selective flush: only raises the synthetic error when a
        # ``ReportInjection`` is in session.new (the C-DiD INSERT
        # site). Earlier flushes (head-guard autoflush, F3 outer
        # flush for message_queue / task INSERTs) pass through to
        # the real flush so execution can reach the C-DiD INSERT.
        original_flush = SQLModelSession.flush
        flush_calls = {"n": 0}

        def _selective_flush(self, *args, **kwargs):
            flush_calls["n"] += 1
            new_objs = list(getattr(self, "new", set()) or set())
            if any(isinstance(obj, ReportInjection) for obj in new_objs):
                raise synthetic_err
            return original_flush(self, *args, **kwargs)

        monkeypatch.setattr(SQLModelSession, "flush", _selective_flush)

        # The natural completion path MUST surface the non-IntegrityError
        # — NOT swallow it. Bare ``raise`` preserves identity.
        with pytest.raises(RuntimeError) as exc_info:
            service._process_child_completion_db_sync(
                instance_id=child_id,
                completed_message_id="msg-non-ie-failure",
                last_content="... (would fail at flush if it could)",
            )

        # (b) Exception identity preserved — bare ``raise``, no wrapping.
        assert exc_info.value is synthetic_err, (
            "non-IntegrityError MUST be re-raised as-is (bare ``raise``); "
            "wrapping would hide the original exception and break the "
            "error-reporting path."
        )

        # Reach assertion: the C-DiD ``except Exception`` block MUST
        # have been entered — the broadened catch fired and emitted
        # a warning. If the except is removed (or narrowed back to
        # IntegrityError), the count stays at 0 and the test fails.
        assert non_ie_warning_calls["n"] >= 1, (
            "the C-DiD except-Exception was NOT entered — the "
            "non-IntegrityError was never caught. The broadened catch "
            "is required so nested.rollback() fires for ANY exception, "
            "not just IntegrityError. If the except Exception was "
            "removed or narrowed, the SAVEPOINT leak returns."
        )

        # Reach assertion: the patched flush MUST have been invoked at
        # least once before the error propagated. The selective flush
        # only raises on ReportInjection in session.new; this confirms
        # the outer F3 flush ran first (no early leakage).
        assert flush_calls["n"] >= 1, (
            "the patched Session.flush MUST have been invoked at "
            "least once before the error propagated (the selective "
            "flush only raises on ReportInjection in session.new; "
            "this confirms the outer F3 flush ran first)"
        )

        # (c) Coarse-rollback containment: the WHOLE outer transaction
        # was rolled back by the WriteGuardSession close (no savepoint
        # leak, and the exception propagated). Verifies:
        #   1. No orphan ReportInjection row for the obligation triple.
        #   2. No completion_report message_queue row appended.
        #   3. No PROCESS_REPORT task row appended.
        #   4. Child did NOT transition to COMPLETED.
        # The coarse rollback is data-safe: no half-flushed state.
        with Session(engine) as session:
            # (1) No orphan ReportInjection row.
            inj_rows = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.parent_instance_id == parent_id
                ).where(
                    ReportInjection.child_instance_id == child_id
                ).where(
                    ReportInjection.child_message_id == "msg-non-ie-failure"
                )
            ).all()
            assert len(inj_rows) == 0, (
                "F4 REGRESSION: no ReportInjection row should exist "
                "for the obligation triple (SAVEPOINT was rolled back). "
                f"Got {len(inj_rows)} orphan row(s)."
            )

            # (2) No completion_report message_queue row.
            # The completion_report message is keyed by source
            # ``internal_report:{child_id}:{completed_message_id}``.
            msg_rows = session.exec(
                sm_select(MessageQueue).where(
                    MessageQueue.instance_id == parent_id
                ).where(
                    MessageQueue.source.like(
                        f"internal_report:{child_id}:msg-non-ie-failure"
                    )
                )
            ).all()
            assert len(msg_rows) == 0, (
                "F4 REGRESSION: no completion_report message_queue "
                "row should exist (outer tx rolled back). Got "
                f"{len(msg_rows)} orphan row(s)."
            )

            # (3) No PROCESS_REPORT task row.
            task_rows = session.exec(
                sm_select(Task).where(
                    Task.instance_id == parent_id
                ).where(
                    Task.task_type == TaskType.PROCESS_REPORT.value
                )
            ).all()
            assert len(task_rows) == 0, (
                "F4 REGRESSION: no PROCESS_REPORT task row should "
                "exist (outer tx rolled back). Got "
                f"{len(task_rows)} orphan row(s)."
            )

            # (4) Child did NOT transition to COMPLETED.
            child_row = session.get(Instance, child_id)
            assert child_row.status == InstanceStatus.RUNNING.value, (
                "F4 REGRESSION: child must NOT transition to "
                "COMPLETED on a non-IntegrityError (outer tx rolled "
                f"back, COMPLETED transition lost). Got "
                f"status={child_row.status!r}."
            )



# ─────────────────────────────────────────────────────────────────────────────
# Wave 1 — wc-wake-report-integrity (NR-2 / NR-3 / (c) / NR-4)
#
# Report-integrity instruments per phase2-plan §3.3 + §4.0 (Seq-AB Wave 1):
#   * (c)  passive DESCRIPTIVE-ONLY report-sanity marker (C2-D2.9 LOCKED).
#   * NR-3 junk-rate counter ``report_integrity_junk_report_total``,
#     incremented BEFORE both repair short-circuits (§6 adjustment).
#   * NR-2 shared exclusion constant ``REPORT_REPAIR_EXCLUDED_AGENTS``
#     (C2-D2.15 LOCKED) — ``watcher`` included at landing (evidence:
#     ``agents/watcher/meta.json`` ``tools.allow == []`` ⇒ structurally
#     zero tool-call evidence; text-only verdicts by design).
#   * NR-4 pin: the marker fires on EXACTLY the input where
#     ``_is_likely_truncated_report`` short-circuits (C2-NR-4 CONFIRMED —
#     keep the short-circuit NARROW).
#
# The raw-fetch helpers mirror ``_make_service`` in
# ``tests/unit/test_report_repair.py`` (``__new__`` + mock manager), with
# tool_calls-aware message dicts.
# ─────────────────────────────────────────────────────────────────────────────

SANITY_MARKER_LITERAL = "[REPORT SANITY: zero tool-call evidence in source history]"


def _make_report_fetch_service(*, messages: list[dict] | None = None, report_repair=None):
    """Build a bare ChildReportsService for ``_get_last_assistant_message*``.

    The mock manager exposes a real ``Config`` (so ``report_repair``
    defaults derive from the shared constant) and a mock checkpointer
    adapter; ``get_instance_messages`` is patched per-test to return
    ``messages``.
    """
    from daemon.config import Config

    manager = MagicMock(name="InstanceManager")
    config = Config()
    if report_repair is not None:
        config.report_repair = report_repair
    manager.config = config
    checkpointer_adapter = MagicMock(name="CheckpointerAdapter")
    checkpointer_adapter.raw_saver = MagicMock(name="RawSaver")
    manager._checkpointer = checkpointer_adapter
    # Stable prefix: no instance row → no instance_name in the prefix.
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    service._pending_report_messages = messages or []
    return service


def _msg_user(content: str) -> dict:
    return {"role": "user", "content": content}


def _msg_assistant(content: str, tool_calls: list | None = None) -> dict:
    """Assistant message dict; ``tool_calls=None`` → zero-tool evidence."""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [] if tool_calls is None else tool_calls,
    }


def _tool_call(name: str = "bash", call_id: str = "call_1") -> dict:
    return {"name": name, "args": {}, "id": call_id, "type": "tool_call"}


def _junk_history(opener: str = "I'll take a look at this now.") -> list[dict]:
    """The silent-death shape: one human task + one zero-tool no-work opener.

    Same history family as the 11-hop premature-completion chain
    (phase2-plan §1 / technical-analysis §"11-Hop Premature-Completion
    Chain"): the child's ONLY assistant turn is a no-tool opener.
    """
    return [
        _msg_user("Investigate the flaky queue test and report back"),
        _msg_assistant(opener),
    ]


def _patch_fetch(messages: list[dict]):
    """Patch the checkpoint fetch inside child_reports."""
    from unittest.mock import AsyncMock, patch

    return patch(
        "daemon.services.child_reports.get_instance_messages",
        new=AsyncMock(return_value=messages),
    )


def _reset_junk_counter() -> None:
    """Zero the NR-3 counter for a clean per-test delta."""
    from daemon.services import report_integrity_metrics as rim

    rim.reset_junk_report_total()


class TestReportSanityMarker:
    """(c) passive report-sanity marker — D2.9 LOCKED (descriptive-only).

    Fires on TERMINAL reports from low-evidence histories (last assistant
    message has zero tool calls AND fewer than 2 content-bearing assistant
    messages — exactly the ``_is_likely_truncated_report`` short-circuit
    input, C2-NR-4). Never on interim (``skip_repair=True``) fetches;
    never for excluded agents (NR-2 constant).
    """

    async def test_marker_present_on_zero_tool_short_history_terminal_report(self):
        """Terminal report from the junk shape carries the marker verbatim."""
        from daemon.constants import REPORT_SANITY_MARKER

        service = _make_report_fetch_service(messages=_junk_history())
        with _patch_fetch(service._pending_report_messages):
            raw = await service._get_last_assistant_message_raw(
                "test-instance-id", agent_id="worker"
            )
        assert raw is not None
        assert SANITY_MARKER_LITERAL in raw, (
            "terminal zero-tool short-history report must carry the "
            "descriptive-only sanity marker (D2.9)"
        )
        assert raw.startswith("I'll take a look at this now."), (
            "marker is additive — original content preserved as the prefix"
        )
        assert "treat as interim" not in raw, (
            "marker is DESCRIPTIVE-ONLY — the directive half lives in (d) "
            "prompt guidance, never in the marker (D2.9)"
        )
        from daemon.constants import REPORT_SANITY_MARKER

        assert REPORT_SANITY_MARKER == SANITY_MARKER_LITERAL

    async def test_marker_composes_into_parent_report_once(self):
        """The wrapper (prefix + concat) carries the marker exactly once."""
        service = _make_report_fetch_service(messages=_junk_history())
        with _patch_fetch(service._pending_report_messages):
            report = await service._get_last_assistant_message("test-instance-id", "worker")
        assert report is not None
        assert SANITY_MARKER_LITERAL in report
        assert report.count(SANITY_MARKER_LITERAL) == 1, (
            "terminal path must mark exactly once (no duplication across "
            "prefix/concat)"
        )

    async def test_marker_absent_on_tool_bearing_last_message(self):
        """Tool evidence in the last assistant message ⇒ no marker."""
        messages = [
            _msg_user("do the task"),
            _msg_assistant("done", tool_calls=[_tool_call()]),
        ]
        service = _make_report_fetch_service(messages=messages)
        with _patch_fetch(messages):
            raw = await service._get_last_assistant_message_raw(
                "test-instance-id", agent_id="worker"
            )
        assert raw == "done"
        assert SANITY_MARKER_LITERAL not in raw

    async def test_marker_absent_on_long_history_with_zero_tool_last(self):
        """Zero-tool LAST message but ≥2 assistant messages ⇒ no marker.

        The truncation check is IN PLAY at this width (short-circuit does
        not govern) — the marker must not widen past it (C2-NR-4).
        """
        messages = [
            _msg_user("task"),
            _msg_assistant("investigated the queue"),
            _msg_assistant("found the flaky test"),
        ]
        service = _make_report_fetch_service(messages=messages)
        with _patch_fetch(messages):
            raw = await service._get_last_assistant_message_raw(
                "test-instance-id", agent_id="worker"
            )
        assert raw == "found the flaky test"
        assert SANITY_MARKER_LITERAL not in raw

    async def test_marker_absent_for_excluded_agents(self):
        """Excluded agents (NR-2 constant, watcher included) are never marked."""
        for agent_id in ("wanderer", "explorer", "watcher"):
            service = _make_report_fetch_service(messages=_junk_history())
            with _patch_fetch(service._pending_report_messages):
                raw = await service._get_last_assistant_message_raw(
                    "test-instance-id", agent_id=agent_id
                )
            assert raw == "I'll take a look at this now.", (
                f"excluded agent {agent_id!r} must get the bare content"
            )
            assert SANITY_MARKER_LITERAL not in raw, (
                f"excluded agent {agent_id!r} must NOT carry the marker"
            )

    async def test_marker_absent_on_interim_skip_repair_fetch(self):
        """``skip_repair=True`` (interim in-progress path) never marks."""
        service = _make_report_fetch_service(messages=_junk_history())
        with _patch_fetch(service._pending_report_messages):
            raw = await service._get_last_assistant_message_raw(
                "test-instance-id", skip_repair=True, agent_id="worker"
            )
        assert raw == "I'll take a look at this now."
        assert SANITY_MARKER_LITERAL not in raw


class TestJunkReportCounter:
    """NR-3 junk-rate counter ``report_integrity_junk_report_total``.

    §6 adjustment (2026-08-30): the increment sits BEFORE the
    ``skip_repair`` short-circuit AND the ``report_repair.enabled``
    short-circuit so ALL terminal completions count, not only
    repair-eligible ones.
    """

    async def test_counter_increments_on_terminal_zero_tool_short_history(self):
        _reset_junk_counter()
        try:
            service = _make_report_fetch_service(messages=_junk_history())
            with _patch_fetch(service._pending_report_messages):
                await service._get_last_assistant_message_raw(
                    "test-instance-id", agent_id="worker"
                )
            from daemon.services.report_integrity_metrics import get_junk_report_total

            assert get_junk_report_total() == 1
        finally:
            _reset_junk_counter()

    async def test_counter_increments_with_repair_disabled(self):
        """Repair disabled ⇒ the enabled short-circuit must not eat the count."""
        _reset_junk_counter()
        try:
            from daemon.config import ReportRepairConfig

            service = _make_report_fetch_service(
                messages=_junk_history(),
                report_repair=ReportRepairConfig(enabled=False),
            )
            with _patch_fetch(service._pending_report_messages):
                await service._get_last_assistant_message_raw(
                    "test-instance-id", agent_id="worker"
                )
            from daemon.services.report_integrity_metrics import get_junk_report_total

            assert get_junk_report_total() == 1, (
                "counter must increment BEFORE the report_repair.enabled "
                "short-circuit (§6 placement)"
            )
        finally:
            _reset_junk_counter()

    async def test_counter_increments_on_skip_repair_fetch(self):
        """skip_repair=True fetch still counts (before-skip_repair placement).

        Proves the §6 adjustment: the increment precedes the skip_repair
        short-circuit, so interim fetches of the junk shape count too —
        ALL fetches of the shape are observed.
        """
        _reset_junk_counter()
        try:
            service = _make_report_fetch_service(messages=_junk_history())
            with _patch_fetch(service._pending_report_messages):
                await service._get_last_assistant_message_raw(
                    "test-instance-id", skip_repair=True, agent_id="worker"
                )
            from daemon.services.report_integrity_metrics import get_junk_report_total

            assert get_junk_report_total() == 1
        finally:
            _reset_junk_counter()

    async def test_counter_not_incremented_on_tool_bearing_history(self):
        _reset_junk_counter()
        try:
            messages = [
                _msg_user("do the task"),
                _msg_assistant("done", tool_calls=[_tool_call()]),
            ]
            service = _make_report_fetch_service(messages=messages)
            with _patch_fetch(messages):
                await service._get_last_assistant_message_raw(
                    "test-instance-id", agent_id="worker"
                )
            from daemon.services.report_integrity_metrics import get_junk_report_total

            assert get_junk_report_total() == 0
        finally:
            _reset_junk_counter()

    async def test_counter_not_incremented_on_long_history(self):
        _reset_junk_counter()
        try:
            messages = [
                _msg_user("task"),
                _msg_assistant("investigated the queue"),
                _msg_assistant("found the flaky test"),
            ]
            service = _make_report_fetch_service(messages=messages)
            with _patch_fetch(messages):
                await service._get_last_assistant_message_raw(
                    "test-instance-id", agent_id="worker"
                )
            from daemon.services.report_integrity_metrics import get_junk_report_total

            assert get_junk_report_total() == 0
        finally:
            _reset_junk_counter()


class TestMarkerPredicatePinnedToTruncationShortCircuit:
    """NR-4 / C2-NR-4 CONFIRMED: keep the truncation short-circuit NARROW.

    The marker must fire on EXACTLY the input where
    ``_is_likely_truncated_report`` short-circuits
    (``child_reports.py`` ``len(messages) < 2 → False``) — widening would
    break legitimate multi-message reports and duplicate the marker's
    signal. This test pins the boundary.
    """

    @staticmethod
    async def _fire(service_messages: list[dict], agent_id: str = "worker") -> bool:
        service = _make_report_fetch_service(messages=service_messages)
        with _patch_fetch(service_messages):
            raw = await service._get_last_assistant_message_raw(
                "pin-instance", agent_id=agent_id
            )
        return raw is not None and SANITY_MARKER_LITERAL in raw

    async def test_fires_on_single_assistant_message_where_short_circuit_governs(self):
        """1 content-bearing assistant message + zero tools ⇒ short-circuit
        input ⇒ marker fires; the truncation check itself CANNOT flag this
        width (``len < 2 → False``) no matter the content."""
        history = _junk_history()
        assistant_msgs = [
            m for m in history
            if m.get("role") == "assistant" and (m.get("content", "") or "").strip()
        ]
        assert len(assistant_msgs) < 2, "fixture must be the short-circuit width"
        # The short-circuit: single-message input is never "truncated".
        huge_single = [{"role": "assistant", "content": "word " * 500}]
        assert ChildReportsService._is_likely_truncated_report(huge_single) is False
        assert ChildReportsService._is_likely_truncated_report(assistant_msgs) is False
        # And exactly there, the marker fires.
        assert await self._fire(history) is True

    async def test_does_not_fire_once_history_exits_the_short_circuit(self):
        """2 zero-tool assistant messages ⇒ short-circuit released (the
        ratio check governs) ⇒ marker absent — even though the last
        message is still zero-tool. This is the anti-widening boundary."""
        history = [
            _msg_user("task"),
            _msg_assistant("started looking"),
            _msg_assistant("nothing found yet"),
        ]
        assistant_msgs = [
            m for m in history
            if m.get("role") == "assistant" and (m.get("content", "") or "").strip()
        ]
        assert len(assistant_msgs) >= 2, "fixture must exit the short-circuit"
        assert await self._fire(history) is False

    async def test_tool_evidence_never_fires_regardless_of_width(self):
        """The zero-tool half of the predicate is independent of width."""
        assert await self._fire(
            [_msg_user("t"), _msg_assistant("d", tool_calls=[_tool_call()])]
        ) is False
        assert await self._fire(
            [
                _msg_user("t"),
                _msg_assistant("a"),
                _msg_assistant("d", tool_calls=[_tool_call()]),
            ]
        ) is False

    async def test_pure_tool_call_assistant_message_counts_as_tool_evidence(self):
        """W1 (council-verified, 2026-08-30): pure tool-call AIMessage
        (canonical shape: ``content=""``, ``tool_calls=[one]``) carries
        tool-call evidence.

        Filter ordering defect: the low-evidence computation previously
        filtered out ``content=""`` ASSISTANT messages BEFORE the tool
        check, so a legitimate minimal-tool history
        ``[task] → [AIMessage(tool_calls=[…], content="")] → [final text AIMessage]``
        was incorrectly marked as zero-evidence. Marker fired falsely and
        the NR-3 counter inflated.

        FIX (W1): the tool-evidence half now scans UNFILTERED assistant
        messages — any assistant message with tool calls ⇒ tool evidence
        present ⇒ NOT low-evidence. The WIDTH half stays exactly as
        pinned by the C2-NR-4 test class.
        """
        # Minimal-tool history: pure tool-call AIMessage (content="")
        # followed by the final text AIMessage. Filter drops the first
        # (content="") but tool_calls were real — evidence IS present.
        history = [
            _msg_user("run the script and report"),
            _msg_assistant("", tool_calls=[_tool_call("bash", "call_w1")]),
            _msg_assistant("script ran successfully — found the bug"),
        ]
        # Pre-flight: the filter does drop content="" messages — the W1
        # defect is precisely that the tool-evidence half inherits this
        # filtering and loses the tool_calls.
        filtered_content_bearing = [
            m for m in history
            if m.get("role") == "assistant" and (m.get("content", "") or "").strip()
        ]
        assert len(filtered_content_bearing) == 1, (
            "fixture sanity: pure tool-call AIMessage must drop out of the "
            "content-bearing filter — this is the shape the W1 fix "
            "depends on"
        )
        # Marker MUST NOT fire — the work signal is tool_calls, not content.
        assert await self._fire(history) is False, (
            "W1 defect: marker fires on minimal-tool history that contains "
            "a real tool call — must not (council W1, 2026-08-30)"
        )

    async def test_pure_tool_call_assistant_message_does_not_increment_counter(self):
        """W1: NR-3 junk counter must NOT increment on minimal-tool history.

        The counter share placement with the marker (``_is_zero_tool_short_history``
        ⇒ both). If the marker is absent, the counter must also stay flat.
        """
        _reset_junk_counter()
        try:
            history = [
                _msg_user("run the script and report"),
                _msg_assistant("", tool_calls=[_tool_call("bash", "call_w1c")]),
                _msg_assistant("script ran successfully"),
            ]
            service = _make_report_fetch_service(messages=history)
            with _patch_fetch(history):
                await service._get_last_assistant_message_raw(
                    "test-instance-id", agent_id="worker"
                )
            from daemon.services.report_integrity_metrics import get_junk_report_total

            assert get_junk_report_total() == 0, (
                "W1 defect: counter increments on minimal-tool history with "
                "a real tool call — must not (council W1, 2026-08-30)"
            )
        finally:
            _reset_junk_counter()


class TestSanityConstantsRegistry:
    """S8 registry pins (mirror ``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED``
    precedent): the Wave-1 constants are pinned — renaming or deleting any
    of them fails this module."""

    def test_sanity_flag_version_pinned(self):
        import daemon.constants as constants

        assert hasattr(constants, "SANITY_FLAG_VERSION"), (
            "SANITY_FLAG_VERSION must exist in daemon/constants.py — it is "
            "the separately-versioned rollback seam for the (c) marker "
            "(bumping to 0/2 suppresses the marker while code stays live)"
        )
        assert constants.SANITY_FLAG_VERSION == 1

    def test_sanity_marker_text_pinned_byte_for_byte(self):
        import daemon.constants as constants

        assert hasattr(constants, "REPORT_SANITY_MARKER")
        assert constants.REPORT_SANITY_MARKER == SANITY_MARKER_LITERAL

    def test_report_repair_excluded_agents_constant_pinned(self):
        import daemon.constants as constants

        assert hasattr(constants, "REPORT_REPAIR_EXCLUDED_AGENTS")
        assert isinstance(constants.REPORT_REPAIR_EXCLUDED_AGENTS, frozenset)
        assert constants.REPORT_REPAIR_EXCLUDED_AGENTS == frozenset(
            {"wanderer", "explorer", "watcher"}
        ), (
            "NR-2: the shared constant is the ONE source of truth; watcher "
            "included (tools.allow=[] ⇒ structurally zero tool-call evidence)"
        )

    def test_junk_metric_name_pinned(self):
        import daemon.constants as constants

        assert hasattr(constants, "REPORT_INTEGRITY_JUNK_REPORT_TOTAL")
        assert constants.REPORT_INTEGRITY_JUNK_REPORT_TOTAL == (
            "report_integrity_junk_report_total"
        )

    def test_config_default_derives_from_constant(self):
        """NR-2: config default_factory derives from the shared constant."""
        from daemon.config import ReportRepairConfig
        from daemon.constants import REPORT_REPAIR_EXCLUDED_AGENTS

        assert ReportRepairConfig().repair_excluded_agents == set(
            REPORT_REPAIR_EXCLUDED_AGENTS
        )

    def test_env_override_replaces_set_comma_separated(self):
        """NR-2: the documented ``REPORT_REPAIR_EXCLUDED_AGENTS`` env var
        (comma-separated) REPLACES the default set — operators can add AND
        remove (e.g. drop ``watcher``) without a code change."""
        import os

        from daemon.config import ReportRepairConfig

        old = os.environ.get("REPORT_REPAIR_EXCLUDED_AGENTS")
        os.environ["REPORT_REPAIR_EXCLUDED_AGENTS"] = "gamma,custom-agent"
        try:
            assert ReportRepairConfig().repair_excluded_agents == {
                "gamma",
                "custom-agent",
            }
        finally:
            if old is None:
                os.environ.pop("REPORT_REPAIR_EXCLUDED_AGENTS", None)
            else:
                os.environ["REPORT_REPAIR_EXCLUDED_AGENTS"] = old

    def test_env_override_accepts_json_list_and_spaces(self):
        import os

        from daemon.config import ReportRepairConfig

        old = os.environ.get("REPORT_REPAIR_EXCLUDED_AGENTS")
        try:
            os.environ["REPORT_REPAIR_EXCLUDED_AGENTS"] = '["wanderer"]'
            assert ReportRepairConfig().repair_excluded_agents == {"wanderer"}
            os.environ["REPORT_REPAIR_EXCLUDED_AGENTS"] = "gamma, custom "
            assert ReportRepairConfig().repair_excluded_agents == {"gamma", "custom"}
        finally:
            if old is None:
                os.environ.pop("REPORT_REPAIR_EXCLUDED_AGENTS", None)
            else:
                os.environ["REPORT_REPAIR_EXCLUDED_AGENTS"] = old

    def test_env_override_empty_string_means_no_exclusions(self):
        """Empty env string → EMPTY set (explicit "no exclusions"), never
        the default — mirrors ``reasoning_echo_disabled_models`` precedent
        (empty env string parses to ``[]``, never ``[""]``)."""
        import os

        from daemon.config import ReportRepairConfig

        old = os.environ.get("REPORT_REPAIR_EXCLUDED_AGENTS")
        os.environ["REPORT_REPAIR_EXCLUDED_AGENTS"] = ""
        try:
            assert ReportRepairConfig().repair_excluded_agents == set()
        finally:
            if old is None:
                os.environ.pop("REPORT_REPAIR_EXCLUDED_AGENTS", None)
            else:
                os.environ["REPORT_REPAIR_EXCLUDED_AGENTS"] = old

    async def test_sanity_flag_version_suppresses_marker_when_not_one(
        self, monkeypatch
    ):
        """SANITY_FLAG_VERSION != 1 is the documented rollback seam
        (child_reports.py:1658-1659, ``child_reports.py`` gate at :1665):
        any value other than 1 suppresses the (c) marker while the code
        stays live — operator's escape hatch. Pin-only tests assert the
        constant is 1; this test pins the GATE (no marker fires when the
        flag is bumped to 0/2)."""
        import daemon.services.child_reports as _cr

        monkeypatch.setattr(_cr, "SANITY_FLAG_VERSION", 0)
        service = _make_report_fetch_service(messages=_junk_history())
        with _patch_fetch(service._pending_report_messages):
            raw = await service._get_last_assistant_message_raw(
                "test-instance-id", agent_id="worker"
            )
        assert raw == "I'll take a look at this now."
        assert SANITY_MARKER_LITERAL not in raw


# ─────────────────────────────────────────────────────────────────────────────
# B.S.1-ii — (b) declared-waiting predicate-attached LOG at the
# root-COMPLETED stamp site (LOG ONLY, stage ii).
#
# Wave 2 wc-wake-report-integrity (decisions.md C2-D2.6/D2.8 LOCKED,
# phase2-plan §4.2 B.S.1-ii + B.S.6/B.S.7). The ONLY observable effect
# is the greppable ``[ReportIntegrityGuard]`` WARNING line — status
# writes, gates, and outcomes are UNCHANGED. Per D2.8 (LOCKED) the
# (b) evaluation is the LAST gate: it runs ONLY on the path where the
# bus gate (fail-CLOSED, same-tx inline COUNT) AND the pending-tasks
# gate (fail-OPEN) both reported zero — never on a short-circuit.
# ─────────────────────────────────────────────────────────────────────────────


def _seed_pending_injection(
    engine,
    *,
    parent_id: str,
    child_id: str,
) -> None:
    """Seed a PENDING report_injections row for (parent, terminal child).

    This is the PRIMARY signal of the (b) declared-waiting predicate —
    the parent owes a delivery to itself for a child that already
    reached a terminal state.
    """
    ReportInjectionRepository(engine).enqueue(
        parent_instance_id=parent_id,
        child_instance_id=child_id,
        child_message_id=f"msg-{uuid.uuid4().hex[:8]}",
        report_message_id=f"rmsg-{uuid.uuid4().hex[:8]}",
        content="junk opener body",
    )


def _guard_records(caplog) -> list:
    """Return the captured ``[ReportIntegrityGuard]`` violation records."""
    import logging as _logging

    return [
        r
        for r in caplog.records
        if r.levelno >= _logging.WARNING
        and "[ReportIntegrityGuard]" in r.getMessage()
        and "declared-waiting violation" in r.getMessage()
    ]


class TestReportIntegrityGuardStageIILog:
    """Stage-ii log behavior at the root-completion stamp site."""

    def test_incident_shape_logs_at_root_completion(
        self, engine, caplog
    ):
        """Root completes while a terminal child's report is PENDING →
        exactly one [ReportIntegrityGuard] line; stamp UNCHANGED."""
        import logging

        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        child_id = _seed_child_instance(
            engine, parent_id=root_id, status=InstanceStatus.COMPLETED.value
        )
        _seed_pending_injection(engine, parent_id=root_id, child_id=child_id)

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = service._process_child_completion_db_sync(
                instance_id=root_id,
                completed_message_id="msg-different-id",
                last_content="assistant text",
            )

        # Zero flow disruption: the completion path is IDENTICAL.
        assert result.outcome == "root_completed"
        with Session(engine) as session:
            inst = session.get(Instance, root_id)
            assert inst.status == InstanceStatus.COMPLETED.value, (
                "LOG ONLY — the stamp must still happen"
            )

        guard = _guard_records(caplog)
        assert len(guard) == 1, (
            f"expected exactly one [ReportIntegrityGuard] line, got "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        msg = guard[0].getMessage()
        assert root_id in msg, "parent (root) id missing"
        assert child_id in msg, "terminal child id missing"
        assert "PRIMARY" in msg, "evidence class missing"
        assert "root_completion" in msg, "context tag missing"
        assert "count=1" in msg

    def test_healthy_root_completion_is_silent(self, engine, caplog):
        """Delivered (claimed) injection → root completes with NO log."""
        import logging

        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        child_id = _seed_child_instance(
            engine, parent_id=root_id, status=InstanceStatus.COMPLETED.value
        )
        _seed_pending_injection(engine, parent_id=root_id, child_id=child_id)
        # Normal delivery consumed the obligation (PENDING → INJECTED).
        ReportInjectionRepository(engine).claim_for_injection(root_id)

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = service._process_child_completion_db_sync(
                instance_id=root_id,
                completed_message_id="msg-different-id",
                last_content="assistant text",
            )

        assert result.outcome == "root_completed"
        assert _guard_records(caplog) == [], (
            f"healthy path must be silent; got "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_bus_gate_short_circuit_skips_predicate(
        self, engine, monkeypatch, caplog
    ):
        """bus_pending > 0 → the bus gate short-circuits and (b) is NOT
        evaluated (D2.8 ordering + hot-path bound): no helper call, no
        log — even though the violation rows exist."""
        import logging

        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        child_id = _seed_child_instance(
            engine, parent_id=root_id, status=InstanceStatus.COMPLETED.value
        )
        _seed_pending_injection(engine, parent_id=root_id, child_id=child_id)
        # PENDING watcher → the bus gate defers the completion.
        _seed_dependency_watcher(
            engine,
            target_instance_id=root_id,
            state=DependencyWatcherState.PENDING.value,
        )

        helper_calls: list[str] = []
        monkeypatch.setattr(
            _child_reports_module,
            "log_declared_waiting_violations",
            lambda *a, **k: helper_calls.append(k.get("context_tag", "?")),
        )

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = service._process_child_completion_db_sync(
                instance_id=root_id,
                completed_message_id="msg-different-id",
                last_content="assistant text",
            )

        assert result.outcome == "deferred_waiting_children"
        assert helper_calls == [], (
            "(b) must NOT be evaluated when the bus gate short-circuits "
            f"(D2.8); helper was called with {helper_calls}"
        )
        assert _guard_records(caplog) == []

    def test_tasks_gate_short_circuit_skips_predicate(
        self, engine, monkeypatch, caplog
    ):
        """pending_tasks > 0 → the tasks gate short-circuits and (b) is
        NOT evaluated (D2.8 ordering): no helper call, no log."""
        import logging

        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        child_id = _seed_child_instance(
            engine, parent_id=root_id, status=InstanceStatus.COMPLETED.value
        )
        _seed_pending_injection(engine, parent_id=root_id, child_id=child_id)
        # PENDING task for the root → the pending-tasks gate defers.
        with Session(engine) as session:
            session.add(
                Task(
                    task_type="process_report",
                    instance_id=root_id,
                    status=TaskStatus.PENDING.value,
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.commit()

        helper_calls: list[str] = []
        monkeypatch.setattr(
            _child_reports_module,
            "log_declared_waiting_violations",
            lambda *a, **k: helper_calls.append(k.get("context_tag", "?")),
        )

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = service._process_child_completion_db_sync(
                instance_id=root_id,
                completed_message_id="msg-different-id",
                last_content="assistant text",
            )

        assert result.outcome == "deferred_waiting_children"
        assert helper_calls == [], (
            "(b) must NOT be evaluated when the tasks gate short-circuits "
            f"(D2.8); helper was called with {helper_calls}"
        )
        assert _guard_records(caplog) == []

    def test_predicate_evaluates_last_only_on_both_zero(
        self, engine, monkeypatch, caplog
    ):
        """ORDERING (B.S.6/B.S.7 share, stage-ii portion): (b) evaluates
        LAST and ONLY when both prior gates report zero.

        Instrumented sequence: the pending-tasks gate probe appends
        ``tasks_gate``; the (b) helper probe appends ``(b):<tag>``. On
        the both-zero path the (b) probe must fire exactly once, AFTER
        the tasks gate (the bus gates passed — outcome root_completed
        and no bus-defer logs). On every short-circuit the (b) probe
        must never fire (pinned by the two tests above).
        """
        import logging

        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        child_id = _seed_child_instance(
            engine, parent_id=root_id, status=InstanceStatus.COMPLETED.value
        )
        _seed_pending_injection(engine, parent_id=root_id, child_id=child_id)

        order: list[str] = []
        real_count = ChildReportsService._count_actionable_pending_tasks

        def _count_spy(self, session, instance_id):
            order.append("tasks_gate")
            return real_count(self, session, instance_id)

        monkeypatch.setattr(
            ChildReportsService, "_count_actionable_pending_tasks", _count_spy
        )
        monkeypatch.setattr(
            _child_reports_module,
            "log_declared_waiting_violations",
            lambda session, pid, **k: order.append(
                f"(b):{k.get('context_tag', '?')}"
            ),
        )

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = service._process_child_completion_db_sync(
                instance_id=root_id,
                completed_message_id="msg-different-id",
                last_content="assistant text",
            )

        # Both prior gates reported zero → (b) ran exactly once, LAST.
        # (outcome == root_completed is itself the both-zero proof: any
        # bus/tasks short-circuit returns a deferred outcome instead of
        # reaching the stamp — see the two skip tests above.)
        assert result.outcome == "root_completed"
        assert order == ["tasks_gate", "(b):child_reports.root_completion"], (
            f"(b) must evaluate LAST and only on the both-zero path; "
            f"got {order}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# B.S.6/B.S.7 EXTENSION (stage iii) — ENFORCEMENT ordering with flag ON.
#
# The stage-ii tests above pin bus > tasks > (b) for the LOG. Stage iii adds
# the flag-gated ENFORCEMENT (adjudication notice) which consumes the SAME
# same-tx evaluation (B.S.7 — never re-evaluated) AFTER the stamp commits.
# These tests prove, with WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED=1:
#
#   * the ENFORCEMENT evaluation preserves the stage-ii ordering — (b) still
#     evaluates LAST, only on the both-counts-zero path (bus > tasks > (b));
#   * the both-counts-zero bound still holds with the flag ON (any gate
#     short-circuit → (b) not evaluated at all → no report attached);
#   * the post-commit enforcement fires from the async dispatch AFTER the
#     stamp (outcome root_completed), via the durable enqueue with the
#     system source — and the stamp itself is untouched (fail-OPEN).
# ═════════════════════════════════════════════════════════════════════════════


class TestReportIntegrityGuardStageIIIEnforcement:
    """Flag-ON ordering + both-counts-zero bound for the ENFORCEMENT path."""

    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        import daemon.services.report_integrity_guard as _rig

        monkeypatch.setenv(
            "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED", "1"
        )
        monkeypatch.setattr(_rig, "_B_GUARD_ENABLED", None)
        _rig._B_NOTICE_LEDGER.clear()
        yield
        _rig._B_NOTICE_LEDGER.clear()

    def test_enforcement_evaluation_preserves_ordering(
        self, engine, monkeypatch
    ):
        """Flag ON: (b) still evaluates LAST (bus > tasks > (b), D2.8),
        the SAME evaluation is attached to the result, and the sync stamp
        proceeds BEFORE any enforcement (which only the async dispatch
        may perform).
        """
        import daemon.services.report_integrity_guard as _rig
        from unittest.mock import AsyncMock as _AsyncMock

        from daemon.services.report_integrity_guard import (
            DeclaredWaitingViolationReport as _DWR,
        )

        service = _build_child_reports_service(engine)
        service._manager.enqueue_message = _AsyncMock()
        root_id = _seed_root_instance(engine)
        child_id = _seed_child_instance(
            engine, parent_id=root_id, status=InstanceStatus.COMPLETED.value
        )
        _seed_pending_injection(engine, parent_id=root_id, child_id=child_id)

        order: list[str] = []
        real_count = ChildReportsService._count_actionable_pending_tasks

        def _count_spy(self, session, instance_id):
            order.append("tasks_gate")
            return real_count(self, session, instance_id)

        monkeypatch.setattr(
            ChildReportsService, "_count_actionable_pending_tasks", _count_spy
        )

        def _log_spy(session, pid, **k):
            order.append(f"(b):{k.get('context_tag', '?')}")
            return _DWR(
                parent_instance_id=pid,
                pending_with_terminal_child=[
                    {
                        "injection_id": "inj-1",
                        "child_instance_id": child_id,
                        "state": "PENDING",
                        "child_terminal_status": "completed",
                    }
                ],
                fired_unenqueued=[],
            )

        monkeypatch.setattr(
            _child_reports_module, "log_declared_waiting_violations", _log_spy
        )

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )

        # Ordering preserved: tasks gate FIRST, (b) LAST.
        assert order == ["tasks_gate", "(b):child_reports.root_completion"], (
            f"ENFORCEMENT evaluation must preserve bus > tasks > (b); got {order}"
        )
        # The stamp ALWAYS proceeds (fail-OPEN) and carries the SAME
        # evaluation to the post-commit enforcement.
        assert result.outcome == "root_completed"
        assert isinstance(result.b_violation_report, _DWR)
        assert result.b_violation_report.is_violation is True

        # The SYNC half performed NO enforcement: manager untouched.
        service._manager.enqueue_message.assert_not_called()

        # The ASYNC dispatch (post-commit) is where the notice fires.
        import asyncio

        asyncio.run(
            service._dispatch_post_commit_side_effects(
                result, "assistant text", "msg-different-id"
            )
        )
        service._manager.enqueue_message.assert_awaited_once()
        kwargs = service._manager.enqueue_message.await_args.kwargs
        assert kwargs["instance_id"] == root_id
        assert kwargs["source"] == "system:report-integrity-guard"
        assert kwargs["priority"] == 0

    def test_both_counts_zero_bound_holds_with_flag_on(
        self, engine, monkeypatch, caplog
    ):
        """Flag ON + bus gate short-circuit → (b) NOT evaluated at all:
        no helper call, no log, NO report attached (the both-counts-zero
        bound is flag-independent).
        """
        import logging

        service = _build_child_reports_service(engine)
        root_id = _seed_root_instance(engine)
        child_id = _seed_child_instance(
            engine, parent_id=root_id, status=InstanceStatus.COMPLETED.value
        )
        _seed_pending_injection(engine, parent_id=root_id, child_id=child_id)
        # PENDING watcher → the bus gate defers the completion.
        _seed_dependency_watcher(
            engine,
            target_instance_id=root_id,
            state=DependencyWatcherState.PENDING.value,
        )

        helper_calls: list[str] = []
        monkeypatch.setattr(
            _child_reports_module,
            "log_declared_waiting_violations",
            lambda *a, **k: helper_calls.append(k.get("context_tag", "?")),
        )

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.report_integrity_guard"
        ):
            result = service._process_child_completion_db_sync(
                instance_id=root_id,
                completed_message_id="msg-different-id",
                last_content="assistant text",
            )

        assert result.outcome == "deferred_waiting_children"
        assert helper_calls == [], (
            "(b) must NOT be evaluated when the bus gate short-circuits "
            f"— flag ON changes nothing (D2.8); got {helper_calls}"
        )
        assert result.b_violation_report is None
        assert _guard_records(caplog) == []
        service._manager.enqueue_message.assert_not_called()

    def test_flag_off_root_path_b_violation_report_is_inert(
        self, engine, monkeypatch
    ):
        """Flag OFF (explicit ``=0`` kill switch — NOT the default since
        the D2.5-FLIP promotion of 2026-10-31): the sync result may
        still carry the evaluation, but the enforcement branch in the
        async dispatch is unreachable — byte-parity with stage ii (no
        enqueue, no notice).
        """
        import asyncio
        from unittest.mock import AsyncMock

        import daemon.services.report_integrity_guard as _rig

        service = _build_child_reports_service(engine)
        service._manager.enqueue_message = AsyncMock()
        root_id = _seed_root_instance(engine)
        child_id = _seed_child_instance(
            engine, parent_id=root_id, status=InstanceStatus.COMPLETED.value
        )
        _seed_pending_injection(engine, parent_id=root_id, child_id=child_id)

        # Override the class-level flag-ON fixture: this test pins the
        # FLAG-OFF kill switch (explicit =0 — the test-body monkeypatch
        # wins over the autouse fixture's env/cache state; delenv would
        # now resolve ON, the new default).
        import daemon.services.report_integrity_guard as _rig

        monkeypatch.setenv(
            "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED", "0"
        )
        monkeypatch.setattr(_rig, "_B_GUARD_ENABLED", None)

        result = service._process_child_completion_db_sync(
            instance_id=root_id,
            completed_message_id="msg-different-id",
            last_content="assistant text",
        )
        assert result.outcome == "root_completed"

        asyncio.run(
            service._dispatch_post_commit_side_effects(
                result, "assistant text", "msg-different-id"
            )
        )
        # ZERO notice work with the flag OFF (revert-proof at the dispatch).
        service._manager.enqueue_message.assert_not_called()
        assert root_id not in _rig._B_NOTICE_LEDGER


# ---------------------------------------------------------------------------
# Negative legs for the 9f0ed4f5 terminal-row strand fix (2026-10-31)
# Canonical effect-level pins: tests/unit/services/test_terminal_row_status_strand.py
# ---------------------------------------------------------------------------


class TestTerminalRowStrandNegativeLegs:
    """Genuinely-completed / non-terminal rows keep their historical
    semantics through the strand-fix writers (no churn, no re-arm)."""

    @pytest.mark.asyncio
    async def test_hold_branch_does_not_write_on_running_root(self, engine: Engine):
        """Hold branch on a RUNNING root (live tree) writes NOTHING —
        the 4ebd7f2f1 no-churn contract. Canonical pin:
        test_terminal_row_status_strand.test_hold_running_root_no_write_no_churn.
        """
        import uuid
        from unittest.mock import AsyncMock, MagicMock, patch
        from sqlalchemy import select as _sel
        from sqlmodel import Session as _Session
        from daemon.repositories.instance.models import Instance, InstanceStatus
        from daemon.repositories.instance.repository import SQLModelInstanceRepository
        from daemon.services.child_reports import ChildReportsService
        from daemon.write_pause_guard import WritePauseGuard

        root_id = f"root-running-nl-{uuid.uuid4().hex[:6]}"
        child_id = f"child-live-nl-{uuid.uuid4().hex[:6]}"
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        with _Session(engine) as session:
            session.add(Instance(instance_id=root_id, agent_id="leader", agent_name="leader",
                                 agent_dir="/tmp/leader", parent_id=None,
                                 status=InstanceStatus.RUNNING.value, version=1,
                                 instance_metadata={}, last_activity_at=now,
                                 created_at=now.isoformat(), updated_at=now.isoformat()))
            session.add(Instance(instance_id=child_id, agent_id="worker", agent_name="worker",
                                 agent_dir="/tmp/worker", parent_id=root_id,
                                 status=InstanceStatus.RUNNING.value, version=1,
                                 instance_metadata={}, last_activity_at=now,
                                 created_at=now.isoformat(), updated_at=now.isoformat()))
            session.commit()

        with _Session(engine) as session:
            pre_version = session.get(Instance, root_id).version

        manager = MagicMock(name="InstanceManager")
        manager.engine = engine
        manager._engine = engine
        manager.write_guard = WritePauseGuard()
        manager._write_guard = manager.write_guard
        manager._live_hub = None
        manager._queue_repository = MagicMock()
        manager._instance_repository = SQLModelInstanceRepository(engine)
        manager._mission_strand_healer = None

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = manager
        service._events_service = None

        with patch("daemon.services.child_reports.get_instance_messages",
                   new=AsyncMock(return_value=[{"role": "user", "content": "hi"}])):
            await service._process_child_completion_and_notify_parent(root_id, "msg-nl")

        with _Session(engine) as session:
            post = session.get(Instance, root_id)
        assert post.status == InstanceStatus.RUNNING.value
        assert post.version == pre_version, "no write on a non-terminal root (no-churn)"

    def test_guard_all_terminal_keeps_skip_in_child_reports(self, engine: Engine):
        """Genuinely-completed (all-terminal tree) keeps idempotency_skip.
        Canonical pin: test_terminal_row_status_strand.test_guard_all_terminal_keeps_idempotency_skip.
        """
        import uuid
        from daemon.repositories.instance.models import Instance, InstanceStatus
        from daemon.services.child_reports import ChildReportsService
        from daemon.write_pause_guard import WritePauseGuard
        from sqlmodel import Session as _Session

        root_id = f"root-allterm-{uuid.uuid4().hex[:6]}"
        child_id = f"child-allterm-{uuid.uuid4().hex[:6]}"
        with _Session(engine) as session:
            session.add(Instance(instance_id=root_id, agent_id="leader", agent_name="leader",
                                 agent_dir="/tmp/leader", parent_id=None,
                                 status=InstanceStatus.COMPLETED.value, version=1,
                                 instance_metadata={}))
            session.add(Instance(instance_id=child_id, agent_id="worker", agent_name="worker",
                                 agent_dir="/tmp/worker", parent_id=root_id,
                                 status=InstanceStatus.COMPLETED.value, version=1,
                                 instance_metadata={}))
            session.commit()

        manager = MagicMock(name="InstanceManager")
        manager.engine = engine
        manager._engine = engine
        manager.write_guard = WritePauseGuard()
        manager._write_guard = manager.write_guard
        manager._live_hub = None
        manager._queue_repository = MagicMock()
        manager._instance_repository = __import__(
            "daemon.repositories.instance.repository", fromlist=["SQLModelInstanceRepository"]
        ).SQLModelInstanceRepository(engine)
        manager._mission_strand_healer = None

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = manager
        service._events_service = None

        result = service._process_child_completion_db_sync(root_id, "msg-at", last_content="r")
        assert result.outcome == "idempotency_skip"
        with _Session(engine) as session:
            assert session.get(Instance, root_id).status == InstanceStatus.COMPLETED.value


# =============================================================================
# Revive-wedge terminalization — the two-arm mint guard + the decoupling
# (lane ``fix/revive-wedge-terminalization``, 2026-11-25; base ``35ec50e3c``)
# =============================================================================
#
# Plan of record: ``.agents/shared/planning/revive-wedge-terminalization/``
# (plan.md §2/§3/§7, design.md §2/§3, gate-matrix.md §2-§4). Pins NEW-2,
# NEW-3, NEW-6a, NEW-6b and NEW-7 (limbs a-e). FAIL-HONEST: NEW-2 / NEW-3 /
# NEW-6b / NEW-7 are RED at the pre-fix base — there the lifetime-prefix
# mint guard early-returned BEFORE the terminal CAS, so a suppressed child
# stayed ``running`` (the revive-wedge). NEW-6a is GREEN at base AND at
# merge BY CONSTRUCTION: it pins the real duplicate-freedom invariant (the
# terminal-entry guard + CAS-before-mint atomicity + the per-parent lock)
# and is the L2d MUTATION target, not a red->green pin.
#
# Harness: the REAL sync DB half (``_process_child_completion_db_sync``) on
# this file's in-memory engine and service builder — nothing on the path
# under test is mocked. Prior report rows are seeded directly into
# ``message_queue`` in the production source shape
# ``internal_report:{child}:{anchor}``. New production symbols are resolved
# INSIDE the tests (never at import time), so this file still COLLECTS at
# the base tree — the tester's base leg copies the changed test files onto
# the un-fixed source and needs per-node reds, not a collection error.

_RW_PARENT = "rw-leader"
_RW_CHILD = "rw-child"

_RW_ARM_B = "arm-b-suppress"
_RW_ARM_A = "arm-a-suppress"
_RW_RETRYING = "retrying-obligation"


def _rw_seed_parent(engine: Engine) -> str:
    return _seed_root_instance(
        engine, instance_id=_RW_PARENT, status=InstanceStatus.IDLE.value,
    )


def _rw_seed_child(
    engine: Engine,
    *,
    status: str = InstanceStatus.RUNNING.value,
    instance_id: str = _RW_CHILD,
    parent_id: str = _RW_PARENT,
) -> str:
    with Session(engine) as session:
        session.add(Instance(
            instance_id=instance_id,
            agent_id="worker",
            agent_name="worker",
            agent_dir="/tmp/worker",
            parent_id=parent_id,
            status=status,
            version=1,
            instance_metadata={},
        ))
        session.commit()
    return instance_id


def _rw_seed_report(
    engine: Engine,
    *,
    anchor: str,
    status: str,
    message_id: str,
    child_id: str = _RW_CHILD,
    recipient_id: str = _RW_PARENT,
    enqueued_at: datetime | None = None,
) -> str:
    """Seed ONE report row in the production source shape."""
    with Session(engine) as session:
        session.add(MessageQueue(
            message_id=message_id,
            instance_id=recipient_id,
            content=f"report under {anchor}",
            source=f"internal_report:{child_id}:{anchor}",
            type=MessageType.COMPLETION_REPORT.value,
            status=status,
            **({"enqueued_at": enqueued_at} if enqueued_at is not None else {}),
        ))
        session.commit()
    return message_id


def _rw_report_rows(engine: Engine, *, child_id: str = _RW_CHILD) -> list[MessageQueue]:
    """Every report row for ``child_id`` — by the EXACT colon boundary.

    ``startswith(..., autoescape=True)``: a raw ``LIKE`` would treat the
    ``_`` in ``internal_report`` as a wildcard.
    """
    with Session(engine) as session:
        return session.exec(
            sm_select(MessageQueue).where(
                MessageQueue.source.startswith(
                    f"internal_report:{child_id}:", autoescape=True,
                )
            )
        ).all()


def _rw_report_sources(engine: Engine, *, child_id: str = _RW_CHILD) -> list[str]:
    return sorted(r.source for r in _rw_report_rows(engine, child_id=child_id))


def _rw_set_report_status(
    engine: Engine, *, status: str, child_id: str = _RW_CHILD,
) -> None:
    """Mark EVERY report row of the child (e.g. COMPLETED = delivered)."""
    with Session(engine) as session:
        rows = session.exec(
            sm_select(MessageQueue).where(
                MessageQueue.source.startswith(
                    f"internal_report:{child_id}:", autoescape=True,
                )
            )
        ).all()
        for row in rows:
            row.status = status
        session.commit()


def _rw_status(engine: Engine, instance_id: str) -> str | None:
    with Session(engine) as session:
        row = session.get(Instance, instance_id)
        return row.status if row else None


def _rw_set_status(engine: Engine, instance_id: str, status: str) -> None:
    with Session(engine) as session:
        session.get(Instance, instance_id).status = status
        session.commit()


def _rw_records(caplog, record_class: str) -> list[dict[str, str]]:
    """The observability records of ONE class, parsed to field dicts.

    A record reads ``child_reports|<class>: class=<class> k=v ...``. The
    class is matched on the ``class=<class>`` token, so ``len()`` counts
    RECORDS of that class — exactly ONE per ACTUAL suppression.
    """
    head = f"child_reports|{record_class}: class={record_class} "
    out: list[dict[str, str]] = []
    for rec in caplog.records:
        msg = rec.getMessage()
        if head not in msg:
            continue
        fields = dict(
            tok.split("=", 1)
            for tok in msg.split(head, 1)[1].split(" ")
            if "=" in tok
        )
        fields["_levelno"] = str(rec.levelno)
        out.append(fields)
    return out


@pytest.fixture
def rw_caplog(caplog):
    """Capture INFO and above (the ``retrying`` record is INFO)."""
    with caplog.at_level(logging.INFO):
        yield caplog


def _rw_loss_total() -> int:
    # Resolved at CALL time: the counter does not exist at the base tree.
    return getattr(_child_reports_module, "_ARM_B_SUPPRESS_LOSS_TOTAL", 0)


class TestReviveWedgeDecoupling:
    """NEW-2 / NEW-3 — terminalization no longer depends on a mint."""

    @pytest.mark.parametrize(
        ("prior_status", "prior_anchor", "incoming_anchor", "row"),
        [
            (MessageStatus.COMPLETED.value, "anchor-A", "anchor-A", "i"),
            (MessageStatus.PROCESSING.value, "anchor-A", "anchor-A", "ii"),
            (MessageStatus.READY.value, "anchor-A", "anchor-B", "iv-ready"),
            (
                MessageStatus.PROCESSING.value, "anchor-A", "anchor-B",
                "iv-processing",
            ),
        ],
    )
    def test_a_mint_suppressed_call_still_terminalizes_the_child(
        self, engine: Engine, prior_status, prior_anchor, incoming_anchor,
        row,
    ):
        """NEW-2 (truth-table rows i / ii / iv): the guard suppresses the
        mint — AND the child is stamped COMPLETED anyway (the cure).

        At the pre-fix base the mint-guard early return skipped the
        terminal CAS, so the child stayed ``running`` — RED there.
        """
        service = _build_child_reports_service(engine)
        _rw_seed_parent(engine)
        _rw_seed_child(engine)
        _rw_seed_report(
            engine, anchor=prior_anchor, status=prior_status,
            message_id="report-prior",
        )

        result = service._process_child_completion_db_sync(
            instance_id=_RW_CHILD,
            completed_message_id=incoming_anchor,
            last_content="episode-2 report",
        )

        assert _rw_report_sources(engine) == [
            f"internal_report:{_RW_CHILD}:{prior_anchor}"
        ], f"row {row}: no second report row may be minted"
        assert result.outcome == "idempotency_skip", (
            f"row {row}: the suppress outcome stays idempotency_skip "
            f"(the B4 gate keys on it); got {result.outcome!r}"
        )
        assert result.parent_id == _RW_PARENT
        assert _rw_status(engine, _RW_CHILD) == (
            InstanceStatus.COMPLETED.value
        ), f"row {row}: a suppressed child MUST still terminalize"

    @pytest.mark.asyncio
    async def test_the_suppress_outcome_reaches_the_b4_gate_on_a_completed_row(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch,
    ):
        """NEW-3: the outcome stays ``idempotency_skip`` AND the B4 cycle-2
        re-mint gate is reached with ``inst.status == COMPLETED``
        (``P-B4-GATE``).

        ``_emit_terminal_for_child_instance_via_bus`` is the ONLY call
        inside that gate — it runs iff the outcome is
        ``idempotency_skip`` AND the freshly re-read row is COMPLETED.
        At the pre-fix base the row was still ``running`` when the gate
        re-read it, so the gate never fired — the chicken-and-egg.
        """
        service = _build_child_reports_service(engine)
        # The B4 gate re-reads the row through the instance repository.
        service._manager._instance_repository = (
            SQLModelInstanceRepository(engine)
        )
        _rw_seed_parent(engine)
        _rw_seed_child(engine)
        _rw_seed_report(
            engine, anchor="anchor-A", status=MessageStatus.READY.value,
            message_id="report-outstanding",
        )

        gate_calls: list[dict] = []
        real_emit = service._emit_terminal_for_child_instance_via_bus

        async def _recording_emit(**kwargs):
            gate_calls.append({
                **kwargs, "status_at_gate": _rw_status(engine, _RW_CHILD),
            })
            return await real_emit(**kwargs)

        monkeypatch.setattr(
            service, "_emit_terminal_for_child_instance_via_bus",
            _recording_emit,
        )

        result = service._process_child_completion_db_sync(
            instance_id=_RW_CHILD,
            completed_message_id="anchor-B",
            last_content="episode-2 report",
        )
        assert result.outcome == "idempotency_skip"
        assert result.parent_id == _RW_PARENT, "the B4 gate reads parent_id"

        # The async half — exactly what ``_process_child_completion_
        # and_notify_parent`` dispatches once the DB half returns.
        await service._dispatch_post_commit_side_effects(
            result, "episode-2 report", "anchor-B",
        )

        assert len(gate_calls) == 1, (
            "the B4 re-mint gate MUST be reached exactly once with a "
            f"COMPLETED row; got {gate_calls!r}"
        )
        assert gate_calls[0]["parent_instance_id"] == _RW_PARENT
        assert gate_calls[0]["child_instance_id"] == _RW_CHILD
        assert gate_calls[0]["status"] == "completed"
        assert gate_calls[0]["status_at_gate"] == (
            InstanceStatus.COMPLETED.value
        )
        assert _rw_status(engine, _RW_CHILD) == (
            InstanceStatus.COMPLETED.value
        )


class TestReviveWedgeDuplicateFreedom:
    """NEW-6 — the REAL duplicate-freedom invariant.

    A non-terminal child carries at most ONE report row across chain
    invocations; a second row appears only after an explicit REVIVE (a new
    episode — which the new policy intends to report). The invariant is
    delivered by the terminal-entry guard + CAS-before-mint atomicity +
    the per-parent lock — NOT by ARM A.
    """

    def test_a_second_invocation_without_a_revive_mints_no_second_row(
        self, engine: Engine,
    ):
        """NEW-6a — GREEN at base AND at merge; RED under mutation L2d
        (BOTH status-based mechanisms neutralized ⇒ the second invocation
        reaches the mint ⇒ ``expected 1 row, got 2``).

        The COMPLETED-prior mark is LOAD-BEARING: a READY/PROCESSING prior
        sets ``suppress_mint`` via ARM B and the suppress branch returns
        before the mint even under L2d — the mutation would be vacuous.
        """
        service = _build_child_reports_service(engine)
        _rw_seed_parent(engine)
        _rw_seed_child(engine)

        # Invocation 1 — mints the report AND stamps the child COMPLETED.
        first = service._process_child_completion_db_sync(
            instance_id=_RW_CHILD,
            completed_message_id="anchor-1",
            last_content="episode-1 report",
        )
        assert first.outcome == "regular_child_completed"
        assert _rw_report_sources(engine) == [
            f"internal_report:{_RW_CHILD}:anchor-1"
        ]
        assert _rw_status(engine, _RW_CHILD) == (
            InstanceStatus.COMPLETED.value
        )

        # The prior report is DELIVERED (COMPLETED) — so ARM A misses on a
        # different anchor and ARM B misses outright (COMPLETED is not in
        # S-B): only the status-based mechanisms stand in the way.
        _rw_set_report_status(engine, status=MessageStatus.COMPLETED.value)

        # Invocation 2 — a DIFFERENT anchor, NO revive.
        second = service._process_child_completion_db_sync(
            instance_id=_RW_CHILD,
            completed_message_id="anchor-2",
            last_content="stale re-entry",
        )

        assert _rw_report_sources(engine) == [
            f"internal_report:{_RW_CHILD}:anchor-1"
        ], "NEW-6a: a second invocation without a revive must NOT mint"
        assert second.outcome == "idempotency_skip"
        assert _rw_status(engine, _RW_CHILD) == (
            InstanceStatus.COMPLETED.value
        )

    def test_an_explicit_revive_then_a_new_anchor_mints_a_second_report(
        self, engine: Engine,
    ):
        """NEW-6b (the new episode) — RED at base (the lifetime-prefix
        guard early-returns and mints nothing — the wedge), GREEN at
        merge (truth-table row iii), GREEN under L2d.

        The prior report is marked COMPLETED BEFORE the revive half — a
        READY/PROCESSING prior would be suppressed by ARM B (row iv).
        """
        service = _build_child_reports_service(engine)
        _rw_seed_parent(engine)
        _rw_seed_child(engine)

        first = service._process_child_completion_db_sync(
            instance_id=_RW_CHILD,
            completed_message_id="anchor-1",
            last_content="episode-1 report",
        )
        assert first.outcome == "regular_child_completed"
        _rw_set_report_status(engine, status=MessageStatus.COMPLETED.value)

        # The explicit revive — what ``send_message`` does to a terminal
        # child (status -> RUNNING; the new episode).
        _rw_set_status(engine, _RW_CHILD, InstanceStatus.RUNNING.value)

        second = service._process_child_completion_db_sync(
            instance_id=_RW_CHILD,
            completed_message_id="anchor-2",
            last_content="episode-2 report",
        )

        assert _rw_report_sources(engine) == [
            f"internal_report:{_RW_CHILD}:anchor-1",
            f"internal_report:{_RW_CHILD}:anchor-2",
        ], "NEW-6b: the revived episode's report MUST reach the parent"
        assert second.outcome == "regular_child_completed"
        assert _rw_status(engine, _RW_CHILD) == (
            InstanceStatus.COMPLETED.value
        )


class TestReviveWedgeObservability:
    """NEW-7 — the R-K2 observability condition (review C1).

    Exactly ONE suppress record per ACTUAL suppression, emitted in the
    SUPPRESS BRANCH after the fall-through CAS — never at the guard,
    never on the defer path — and the loss counter moves ONLY on an R-K2
    loss. RED at base (no record and no counter exist there).
    """

    @pytest.mark.parametrize(
        ("incoming_anchor", "expected_loss", "expected_delta"),
        [
            ("anchor-B", 1, 1),
            ("anchor-A", 0, 0),
        ],
        ids=["different-anchor-is-a-loss", "same-anchor-loses-nothing"],
    )
    def test_arm_b_suppression_emits_exactly_one_record_and_counts_only_a_loss(
        self, engine: Engine, rw_caplog, incoming_anchor, expected_loss,
        expected_delta,
    ):
        """(a) ARM-B: child + BOTH anchors + the outstanding marker id +
        ``loss`` + ``total``; the counter moves by exactly +1 on a loss
        and by 0 otherwise."""
        before = _rw_loss_total()
        service = _build_child_reports_service(engine)
        _rw_seed_parent(engine)
        _rw_seed_child(engine)
        _rw_seed_report(
            engine, anchor="anchor-A", status=MessageStatus.READY.value,
            message_id="report-outstanding",
        )

        result = service._process_child_completion_db_sync(
            instance_id=_RW_CHILD,
            completed_message_id=incoming_anchor,
            last_content="episode-2 report",
        )

        arm_b = _rw_records(rw_caplog, _RW_ARM_B)
        assert len(arm_b) == 1, (
            f"exactly ONE arm-b-suppress record per ACTUAL suppression; "
            f"got {[r.getMessage() for r in rw_caplog.records]!r}"
        )
        rec = arm_b[0]
        assert rec["child"] == _RW_CHILD
        assert rec["incoming_anchor"] == incoming_anchor
        # P-ANCHOR-PARSE — the blocking row's anchor, prefix-stripped.
        assert rec["outstanding_anchor"] == "anchor-A"
        assert rec["outstanding_message_id"] == "report-outstanding"
        assert rec["loss"] == str(expected_loss)
        assert rec["_levelno"] == str(logging.WARNING)

        after = _rw_loss_total()
        assert after - before == expected_delta, (
            f"the counter moves by exactly {expected_delta}; "
            f"{before} -> {after}"
        )
        assert rec["total"] == str(after), (
            "the running counter value is carried in the record"
        )
        # An ARM-B-classified suppression emits NO arm-a record.
        assert _rw_records(rw_caplog, _RW_ARM_A) == []
        assert result.outcome == "idempotency_skip"
        assert _rw_status(engine, _RW_CHILD) == (
            InstanceStatus.COMPLETED.value
        )

    def test_an_arm_a_only_suppression_emits_exactly_one_arm_a_record(
        self, engine: Engine, rw_caplog,
    ):
        """(b) ARM-A-only (truth-table row i — a DELIVERED same-anchor
        re-entry): exactly one ``arm-a-suppress`` record, never an
        ``arm-b-suppress`` one, and the loss counter does not move."""
        before = _rw_loss_total()
        service = _build_child_reports_service(engine)
        _rw_seed_parent(engine)
        _rw_seed_child(engine)
        _rw_seed_report(
            engine, anchor="anchor-A", status=MessageStatus.COMPLETED.value,
            message_id="report-delivered",
        )

        result = service._process_child_completion_db_sync(
            instance_id=_RW_CHILD,
            completed_message_id="anchor-A",
            last_content="router re-entry of a delivered obligation",
        )

        assert _rw_records(rw_caplog, _RW_ARM_B) == []
        arm_a = _rw_records(rw_caplog, _RW_ARM_A)
        assert len(arm_a) == 1, (
            f"exactly ONE arm-a-suppress record on an ARM-A-only "
            f"suppression; got {[r.getMessage() for r in rw_caplog.records]!r}"
        )
        rec = arm_a[0]
        assert rec["child"] == _RW_CHILD
        assert rec["incoming_anchor"] == "anchor-A"
        assert rec["matched_anchor"] == "anchor-A"
        assert rec["matched_message_id"] == "report-delivered"
        assert rec["matched_status"] == MessageStatus.COMPLETED.value
        assert rec["_levelno"] == str(logging.WARNING)
        assert _rw_loss_total() - before == 0, "ARM A never moves the counter"
        assert result.outcome == "idempotency_skip"
        assert _rw_status(engine, _RW_CHILD) == (
            InstanceStatus.COMPLETED.value
        )

    def test_a_mint_over_a_retrying_obligation_emits_exactly_one_info_record(
        self, engine: Engine, rw_caplog,
    ):
        """(c) ``retrying`` is EXCLUDED from ARM B (leader ruling 6): the
        mint PROCEEDS over a ``retrying`` obligation and logs exactly ONE
        INFO record — the OLDEST ``retrying`` row + the count."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        service = _build_child_reports_service(engine)
        _rw_seed_parent(engine)
        _rw_seed_child(engine)
        _rw_seed_report(
            engine, anchor="anchor-old", status=MessageStatus.RETRYING.value,
            message_id="report-retry-old",
            enqueued_at=now - timedelta(hours=2),
        )
        _rw_seed_report(
            engine, anchor="anchor-newer", status=MessageStatus.RETRYING.value,
            message_id="report-retry-newer",
            enqueued_at=now - timedelta(hours=1),
        )

        result = service._process_child_completion_db_sync(
            instance_id=_RW_CHILD,
            completed_message_id="anchor-3",
            last_content="episode-3 report",
        )

        assert result.outcome == "regular_child_completed"
        assert _rw_report_sources(engine) == [
            f"internal_report:{_RW_CHILD}:anchor-3",
            f"internal_report:{_RW_CHILD}:anchor-newer",
            f"internal_report:{_RW_CHILD}:anchor-old",
        ], "a retrying obligation must NOT block the mint (R-K4)"
        retrying = _rw_records(rw_caplog, _RW_RETRYING)
        assert len(retrying) == 1, (
            f"exactly ONE retrying-obligation record per such mint; got "
            f"{[r.getMessage() for r in rw_caplog.records]!r}"
        )
        rec = retrying[0]
        assert rec["child"] == _RW_CHILD
        assert rec["incoming_anchor"] == "anchor-3"
        assert rec["retrying_anchor"] == "anchor-old"
        assert rec["retrying_message_id"] == "report-retry-old"
        assert rec["retrying_count"] == "2"
        assert rec["_levelno"] == str(logging.INFO)
        assert _rw_records(rw_caplog, _RW_ARM_B) == []
        assert _rw_records(rw_caplog, _RW_ARM_A) == []

    def test_no_retrying_record_without_an_obligation_or_on_a_suppression(
        self, engine: Engine, rw_caplog,
    ):
        """(c, negative half) no ``retrying`` obligation ⇒ no record; and
        a SUPPRESSED mint over a ``retrying`` row ⇒ no record either (the
        probe is mint-path only)."""
        service = _build_child_reports_service(engine)
        _rw_seed_parent(engine)
        _rw_seed_child(engine)

        # (1) A plain mint — nothing sits in ``retrying``.
        first = service._process_child_completion_db_sync(
            instance_id=_RW_CHILD,
            completed_message_id="anchor-1",
            last_content="episode-1 report",
        )
        assert first.outcome == "regular_child_completed"
        assert _rw_records(rw_caplog, _RW_RETRYING) == []

        # (2) A revived episode whose mint is SUPPRESSED by an outstanding
        #     obligation while a ``retrying`` row exists.
        _rw_set_report_status(engine, status=MessageStatus.COMPLETED.value)
        _rw_set_status(engine, _RW_CHILD, InstanceStatus.RUNNING.value)
        _rw_seed_report(
            engine, anchor="anchor-retry", status=MessageStatus.RETRYING.value,
            message_id="report-retry",
        )
        _rw_seed_report(
            engine, anchor="anchor-A", status=MessageStatus.READY.value,
            message_id="report-outstanding",
        )
        rw_caplog.clear()
        second = service._process_child_completion_db_sync(
            instance_id=_RW_CHILD,
            completed_message_id="anchor-2",
            last_content="episode-2 report",
        )
        assert second.outcome == "idempotency_skip"
        assert len(_rw_records(rw_caplog, _RW_ARM_B)) == 1
        assert _rw_records(rw_caplog, _RW_RETRYING) == [], (
            "the retrying probe never runs on a suppression"
        )

    def test_concurrent_counter_increments_lose_no_update(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        """(d) the lock: N threads x M increments lose no update, and
        every increment runs under ``_ARM_B_SUPPRESS_LOCK`` (the chain
        increments from ``asyncio.to_thread`` workers)."""
        bump = getattr(
            _child_reports_module, "_bump_arm_b_suppress_loss_total",
        )
        real_lock = _child_reports_module._ARM_B_SUPPRESS_LOCK
        lock_entries: list[int] = []

        class _CountingLock:
            def __enter__(self):
                lock_entries.append(1)
                return real_lock.__enter__()

            def __exit__(self, *exc):
                return real_lock.__exit__(*exc)

        monkeypatch.setattr(
            _child_reports_module, "_ARM_B_SUPPRESS_LOCK", _CountingLock(),
        )
        baseline = _rw_loss_total()
        # Restored to ``baseline`` at teardown (monkeypatch owns it).
        monkeypatch.setattr(
            _child_reports_module, "_ARM_B_SUPPRESS_LOSS_TOTAL", baseline,
        )

        n_threads, per_thread = 8, 400
        barrier = threading.Barrier(n_threads)

        def _worker() -> None:
            barrier.wait(timeout=30)
            for _ in range(per_thread):
                bump()

        threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not any(t.is_alive() for t in threads), "worker hung"

        expected = n_threads * per_thread
        assert _rw_loss_total() - baseline == expected, (
            f"a bare ``+= 1`` from worker threads loses updates: expected "
            f"{expected}, got {_rw_loss_total() - baseline}"
        )
        assert len(lock_entries) == expected, (
            "every increment must run under _ARM_B_SUPPRESS_LOCK"
        )

    @pytest.mark.parametrize("defer_guard", ["active_children", "pending_task"])
    def test_a_defer_emits_no_suppress_record_and_leaves_the_counter_untouched(
        self, engine: Engine, rw_caplog, defer_guard,
    ):
        """(e) THE DEFER CASE (repair 006 — approver iteration-003
        BLOCKING 2): the guard sets ``suppress_mint`` and the call then
        takes a defer guard (``outcome="child_still_running_defer"``) —
        nothing was suppressed, so NO suppress record (neither ARM-B nor
        ARM-A), no ``retrying`` record, and the counter is UNCHANGED
        (+0). Records are captured with ``caplog``."""
        before = _rw_loss_total()
        service = _build_child_reports_service(engine)
        _rw_seed_parent(engine)
        _rw_seed_child(engine)
        # The guard WILL suppress: an outstanding obligation under another
        # anchor (ARM B). Plus a ``retrying`` row — the INFO probe must
        # stay silent on a defer too.
        _rw_seed_report(
            engine, anchor="anchor-A", status=MessageStatus.READY.value,
            message_id="report-outstanding",
        )
        _rw_seed_report(
            engine, anchor="anchor-retry", status=MessageStatus.RETRYING.value,
            message_id="report-retry",
        )
        if defer_guard == "active_children":
            # The active-children guard: a live (non-ghost) direct child.
            _seed_child_instance_full(
                engine, parent_id=_RW_CHILD,
                status=InstanceStatus.RUNNING.value,
            )
        else:
            # The pending-task guard: an actionable PENDING task of the
            # child's own (it will run next and may spawn more children).
            with Session(engine) as session:
                session.add(Task(instance_id=_RW_CHILD))
                session.commit()

        result = service._process_child_completion_db_sync(
            instance_id=_RW_CHILD,
            completed_message_id="anchor-B",
            last_content="episode-2 report",
        )

        assert result.outcome == "child_still_running_defer", (
            f"the {defer_guard} guard must defer; got {result.outcome!r}"
        )
        assert result.parent_id == _RW_PARENT
        assert _rw_records(rw_caplog, _RW_ARM_B) == [], (
            "a defer suppresses nothing — no arm-b record"
        )
        assert _rw_records(rw_caplog, _RW_ARM_A) == [], (
            "a defer suppresses nothing — no arm-a record"
        )
        assert _rw_records(rw_caplog, _RW_RETRYING) == [], (
            "the retrying probe is mint-path only — nothing on a defer"
        )
        assert _rw_loss_total() - before == 0, "a defer must not move the counter"
        # A defer is a defer: the row is untouched until a later re-entry.
        assert _rw_status(engine, _RW_CHILD) == (
            InstanceStatus.RUNNING.value
        )
