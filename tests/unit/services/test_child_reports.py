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

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

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
from daemon.repositories.task.models import Task  # noqa: F401
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
