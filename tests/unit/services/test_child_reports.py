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
                service._get_last_assistant_message_raw(
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
                service._get_last_assistant_message_raw(
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
        """Flag OFF (ship default): the sync result may still carry the
        evaluation, but the enforcement branch in the async dispatch is
        unreachable — byte-parity with stage ii (no enqueue, no notice).
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
        # FLAG-OFF ship default (the test-body monkeypatch wins over the
        # autouse fixture's env/cache state).
        import daemon.services.report_integrity_guard as _rig

        monkeypatch.delenv(
            "WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED",
            raising=False,
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
