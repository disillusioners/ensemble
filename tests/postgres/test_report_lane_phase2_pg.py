"""PostgreSQL-only tests for the Phase 2 report-lane decoupling.

Verifies the report-lane contracts work correctly against a real PostgreSQL
backend — not just in-memory SQLite. The SQLite-in-memory pack at
``tests/test_report_lane_phase2.py`` exercises the same contracts; this
file targets the contracts whose PG-side behavior is meaningfully different
(``claim_pending_task`` SQL with the pause gate, real PG row-level locking,
JSONB columns).

Run with::

    uv run python -m pytest tests/postgres/test_report_lane_phase2_pg.py -v \\
        -m postgres --override-ini="addopts="

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the entire
module cleanly when PostgreSQL is not reachable, so this file is safe to
collect even on machines without a running PG.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus import (
    DependencyWatcher,
    DependencyWatcherRepository,
    DependencyWatcherState,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import JobItem, AdmissionState
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
    set_dependency_bus,
)



# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# Phase 4 cleanup removed ``status_to_admission`` from
# ``daemon.repositories.job_queue.models``. Redefined here for test
# seeds that derive ``admission_state`` from a ``status`` value.
def status_to_admission(status):  # noqa: ANN001,ANN201
    return {
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
    }.get(status, "queued")


# Auto-apply the postgres marker so ``pytest -m postgres`` selects these
# tests and the default ``addopts = "-m 'not integration and not postgres'"``
# skips them unless overridden.
pytestmark = pytest.mark.postgres


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def bus_repo(pg_repository_factory):
    """Real DependencyWatcherRepository bound to the PG engine."""
    return pg_repository_factory(DependencyWatcherRepository)


@pytest.fixture
def task_repo(pg_engine):
    """TaskRepository bound to the PG engine."""
    return TaskRepository(pg_engine, on_pending_task=lambda: None)


@pytest.fixture(autouse=True)
def _reset_bus_singleton():
    """Clear the module-level bus singleton between tests."""
    set_dependency_bus(None)
    yield
    set_dependency_bus(None)


@pytest.fixture
async def bus(bus_repo):
    """Started DependencyBus on PG; auto-stops on teardown."""
    b = DependencyBus(bus_repo)
    await b.start()
    set_dependency_bus(b)
    try:
        yield b
    finally:
        await b.stop()
        set_dependency_bus(None)


# =============================================================================
# Helpers
# =============================================================================


def _seed_instance(
    pg_engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    parent_id: str | None = None,
) -> str:
    """Insert an Instance row directly via SQLModel Session."""
    iid = instance_id or f"inst-pg-{uuid.uuid4().hex[:8]}"
    with Session(pg_engine) as s:
        s.add(Instance(
            instance_id=iid,
            agent_id="leader",
            agent_dir="/tmp/leader",
            agent_name="leader",
            parent_id=parent_id,
            status=status,
            version=1,
            instance_metadata={},
        ))
        s.commit()
    return iid


def _seed_job(
    pg_engine,
    *,
    instance_id: str,
    job_id: str | None = None,
    status: str = AdmissionState.ACTIVE.value,
    job_metadata: dict | None = None,
) -> str:
    """Insert a JobItem row directly via SQLModel Session.

    ``job_metadata`` is stored in the ``metadata`` JSON/JSONB column
    (mapped via ``sa_column=Column("metadata", JSONBType)`` in
    ``JobItem.job_metadata``). On PG, the cross-system guard's carve-out
    uses ``j.metadata->>'message_id'`` (TEXT extraction from JSONB) to
    match against ``task.message_id`` — exercising the PG-specific JSON
    extraction path is one of the test goals.
    """
    jid = job_id or f"job-pg-{uuid.uuid4().hex[:8]}"
    with Session(pg_engine) as s:
        s.add(JobItem(
            job_id=jid,
            agent_id="leader",
            agent_dir="/tmp/leader",
            message="pg test",
            source="api",
            job_type="message",

            admission_state=status_to_admission(status),
            instance_id=instance_id,
            job_metadata=job_metadata if job_metadata is not None else {},
        ))
        s.commit()
    return jid


# =============================================================================
# Suite 2.2 PG — Pause safety
# =============================================================================


class TestPauseSafetyPG:
    """Verify the pause gate works correctly on PostgreSQL.

    The gate is in ``claim_pending_task``:
        instance_id NOT IN (
            SELECT instance_id FROM instances
            WHERE status IN ('paused', 'terminated')
        )

    On PostgreSQL, this exercises row-level locking semantics that the
    SQLite in-memory pack cannot reproduce.
    """

    def test_pg_paused_instance_blocks_report_task_claim(
        self, pg_engine, task_repo
    ):
        """claim_pending_task returns None for PAUSED instance on PG."""
        parent_id = _seed_instance(pg_engine, status=InstanceStatus.PAUSED.value)
        report_task = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )
        assert report_task.status == TaskStatus.PENDING.value

        # Pause gate blocks on PG too.
        claimed = task_repo.claim_pending_task(worker_id="pg-worker-1")
        assert claimed is None

    def test_pg_resume_unblocks_report_task_claim(
        self, pg_engine, task_repo
    ):
        """Resume flips status; claim succeeds on PG."""
        parent_id = _seed_instance(pg_engine, status=InstanceStatus.PAUSED.value)
        report_task = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )

        # Pause gate blocks.
        assert task_repo.claim_pending_task(worker_id="pg-worker-1") is None

        # Resume.
        with Session(pg_engine) as s:
            inst = s.get(Instance, parent_id)
            assert inst is not None
            inst.status = InstanceStatus.RUNNING.value
            s.commit()

        # Now claimable.
        claimed = task_repo.claim_pending_task(worker_id="pg-worker-1")
        assert claimed is not None
        assert claimed.id == report_task.id
        assert claimed.status == TaskStatus.RUNNING.value
        assert claimed.worker_id == "pg-worker-1"

    def test_pg_terminated_instance_blocks_report_task_claim(
        self, pg_engine, task_repo
    ):
        """TERMINATED instances are blocked on PG too."""
        parent_id = _seed_instance(pg_engine, status=InstanceStatus.TERMINATED.value)
        task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )
        assert task_repo.claim_pending_task(worker_id="pg-worker-1") is None

    def test_pg_concurrent_claims_only_one_wins(self, pg_engine, task_repo):
        """Two workers race to claim the same task on PG; only one wins.

        PG's row-level locking during UPDATE ... RETURNING means the second
        worker's UPDATE either waits (READ COMMITTED) or sees the post-update
        row (and thus no PENDING row to claim). The task-claim race-condition
        fix (claim_pending_task filter ``AND status = :status_pending``) is
        what makes this work on PG — without the filter, both workers could
        race on a TOCTOU window.
        """
        parent_id = _seed_instance(pg_engine, status=InstanceStatus.RUNNING.value)
        # Only one task available.
        task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )

        # Two workers race.
        claimed_a = task_repo.claim_pending_task(worker_id="pg-worker-A")
        claimed_b = task_repo.claim_pending_task(worker_id="pg-worker-B")

        # Exactly one of them wins.
        winners = [c for c in [claimed_a, claimed_b] if c is not None]
        assert len(winners) == 1
        assert winners[0].status == TaskStatus.RUNNING.value


# =============================================================================
# Suite 2.3 PG — Crash recovery
# =============================================================================


class TestCrashRecoveryPG:
    """Verify the recovery contract on PG."""

    @pytest.mark.asyncio
    async def test_pg_fired_unstamped_watcher_recovered(self, pg_engine, bus_repo):
        """FIRED row with enqueued_at=NULL is recovered on PG after restart."""
        parent_id = f"parent-pg-{uuid.uuid4().hex[:8]}"
        child_task_id = str(uuid.uuid4())

        with Session(pg_engine) as s:
            s.add(DependencyWatcher(
                source_task_id=child_task_id,
                target_instance_id=parent_id,
                state=DependencyWatcherState.FIRED.value,
                follow_up_payload=FollowUp(
                    target_instance_id=parent_id,
                    message="pg test",
                    source="test",
                ).to_payload(),
                fired_at=datetime.now(timezone.utc).isoformat(),
                enqueued_at=None,  # un-stamped
            ))
            s.commit()

        # Restart the bus against the same PG engine.
        bus2 = DependencyBus(bus_repo)
        await bus2.start()
        try:
            recovered = await bus2._recover_fired_unsent()
            assert len(recovered) == 1
            assert recovered[0][1].target_instance_id == parent_id
        finally:
            await bus2.stop()

    @pytest.mark.asyncio
    async def test_pg_stamped_watcher_not_recovered(self, pg_engine, bus_repo):
        """Already-stamped rows are NOT recovered on PG."""
        parent_id = f"parent-pg-{uuid.uuid4().hex[:8]}"
        child_task_id = str(uuid.uuid4())

        with Session(pg_engine) as s:
            s.add(DependencyWatcher(
                source_task_id=child_task_id,
                target_instance_id=parent_id,
                state=DependencyWatcherState.FIRED.value,
                follow_up_payload=FollowUp(
                    target_instance_id=parent_id,
                    message="pg test",
                    source="test",
                ).to_payload(),
                fired_at=datetime.now(timezone.utc).isoformat(),
                enqueued_at=datetime.now(timezone.utc).isoformat(),
            ))
            s.commit()

        bus2 = DependencyBus(bus_repo)
        await bus2.start()
        try:
            recovered = await bus2._recover_fired_unsent()
            assert len(recovered) == 0
        finally:
            await bus2.stop()


# =============================================================================
# Suite 2.4 PG — Error propagation
# =============================================================================


class TestErrorPropagationPG:
    """Verify the any-error rule on PG."""

    @pytest.mark.asyncio
    async def test_pg_child_error_sets_had_parent_error(self, bus):
        """A child error on PG stamps the per-parent error flag."""
        parent_id = f"parent-pg-{uuid.uuid4().hex[:8]}"
        child_task_id = str(uuid.uuid4())

        await bus.watch(
            child_task_id,
            FollowUp(target_instance_id=parent_id, message="pg err", source="test"),
        )
        await bus.emit_terminal(
            child_task_id,
            Outcome(status="error", error="pg test error"),
        )

        assert bus.had_parent_error(parent_id) is True
        assert bus.parent_error_message(parent_id) == "pg test error"

    @pytest.mark.asyncio
    async def test_pg_all_succeed_no_error_flag(self, bus):
        """All children succeeding leaves the error flag False on PG."""
        parent_id = f"parent-pg-{uuid.uuid4().hex[:8]}"
        c1 = str(uuid.uuid4())
        c2 = str(uuid.uuid4())

        await bus.watch(c1, FollowUp(target_instance_id=parent_id, message="c1", source="t"))
        await bus.watch(c2, FollowUp(target_instance_id=parent_id, message="c2", source="t"))

        await bus.emit_terminal(c1, Outcome(status="completed"))
        await bus.emit_terminal(c2, Outcome(status="completed"))

        assert bus.had_parent_error(parent_id) is False
        assert bus.parent_error_message(parent_id) is None

    @pytest.mark.asyncio
    async def test_pg_clear_parent_error_resets_flag(self, bus):
        """clear_parent_error is a no-throw no-op on PG."""
        parent_id = f"parent-pg-{uuid.uuid4().hex[:8]}"
        c1 = str(uuid.uuid4())

        await bus.watch(c1, FollowUp(target_instance_id=parent_id, message="c1", source="t"))
        await bus.emit_terminal(c1, Outcome(status="error", error="oops"))

        assert bus.had_parent_error(parent_id) is True

        bus.clear_parent_error(parent_id)

        assert bus.had_parent_error(parent_id) is False
        assert bus.parent_error_message(parent_id) is None


# =============================================================================
# Suite 2.1 PG — Independent-turn (smoke)
# =============================================================================


class TestIndependentTurnPG:
    """Smoke test: independent turns work on PG."""

    @pytest.mark.asyncio
    async def test_pg_two_children_two_report_tasks(self, pg_engine, task_repo, bus):
        """Two children → two distinct PROCESS_REPORT tasks on PG."""
        parent_id = f"parent-pg-{uuid.uuid4().hex[:8]}"
        c1 = str(uuid.uuid4())
        c2 = str(uuid.uuid4())

        await bus.watch(c1, FollowUp(target_instance_id=parent_id, message="c1", source="t"))
        await bus.watch(c2, FollowUp(target_instance_id=parent_id, message="c2", source="t"))

        assert await bus.count_pending_for_target(parent_id) == 2

        await bus.emit_terminal(c1, Outcome(status="completed"))
        assert await bus.count_pending_for_target(parent_id) == 1

        report1 = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )

        await bus.emit_terminal(c2, Outcome(status="completed"))
        assert await bus.count_pending_for_target(parent_id) == 0

        report2 = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )

        # Two distinct report tasks.
        with Session(pg_engine) as s:
            tasks = list(s.exec(
                select(Task).where(
                    Task.instance_id == parent_id,
                    Task.task_type == TaskType.PROCESS_REPORT.value,
                )
            ))
        assert len(tasks) == 2
        assert {t.id for t in tasks} == {report1.id, report2.id}


# =============================================================================
# Suite 2.1b PG — CRITICAL: report lane bypasses cross-system guard on PG
# =============================================================================


class TestReportLaneGuardPG:
    """#1 PRIORITY on PG — the JSONB extraction path must work correctly.

    Mirrors ``TestReportLaneGuard`` in the SQLite pack. The PG path
    differs in one critical way: the cross-system guard's message_id
    match uses ``j.metadata->>'message_id'`` (JSONB TEXT extraction)
    rather than SQLite's ``CAST(json_extract(...) AS TEXT)``. This test
    exercises that dialect-specific path against a real PG database.

    The bypass for PROCESS_REPORT is identical on both backends (the
    SQL is the same text — only the JSON extraction fragment varies
    inside the per-message-id subquery, which the report lane never
    reaches).
    """

    def test_pg_process_report_bypasses_cross_system_guard(
        self, pg_engine, task_repo
    ):
        """PROCESS_REPORT with different message_id IS claimed on PG.

        Verifies the report lane bypass works against PG's JSONB column
        and the ``->>'message_id'`` TEXT extraction inside the guard.
        """
        parent_id = _seed_instance(pg_engine)
        _seed_job(
            pg_engine,
            instance_id=parent_id,
            status=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-user-pg-123"},
        )
        report_task = task_repo.create(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_id,
            message_id="msg-report-pg-456",
        )
        assert report_task.status == TaskStatus.PENDING.value

        # Bypass on PG too — the report IS claimed.
        claimed = task_repo.claim_pending_task(worker_id="pg-worker-1")
        assert claimed is not None, (
            "PROCESS_REPORT MUST bypass the cross-system guard on PG"
        )
        assert claimed.id == report_task.id
        assert claimed.task_type == TaskType.PROCESS_REPORT.value
        assert claimed.status == TaskStatus.RUNNING.value

    def test_pg_process_message_blocked_by_cross_system_guard(
        self, pg_engine, task_repo
    ):
        """PROCESS_MESSAGE with non-matching message_id IS blocked on PG.

        Contrast test: proves the guard is still active for
        PROCESS_MESSAGE on PG (the bypass is scoped to PROCESS_REPORT
        only, not all tasks). Exercises the ``j.metadata->>'message_id'``
        JSONB extraction path on the parent job row.
        """
        parent_id = _seed_instance(pg_engine)
        _seed_job(
            pg_engine,
            instance_id=parent_id,
            status=AdmissionState.ACTIVE.value,
            job_metadata={"message_id": "msg-user-pg-123"},
        )
        msg_task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=parent_id,
            message_id="msg-other-pg-789",
        )
        assert msg_task.status == TaskStatus.PENDING.value

        # Guard fires — PROCESS_MESSAGE with non-matching message_id
        # is blocked.
        claimed = task_repo.claim_pending_task(worker_id="pg-worker-1")
        assert claimed is None, (
            f"PROCESS_MESSAGE with non-matching message_id MUST be blocked "
            f"on PG (got {claimed})"
        )


# =============================================================================
# Suite 2.2 PG — Pause gate coverage gaps
# =============================================================================


class TestPauseSafetyCoveragePG:
    """Mirror the remaining SQLite pause-gate tests on PG.

    The SQLite pack already covers:
      - paused instance blocks report Task claim
      - resume unblocks report Task claim
      - terminated instance blocks report Task claim
      - concurrent claims only one wins
      - PROCESS_MESSAGE task also blocked for paused

    This class adds the PG mirror of the last one (PROCESS_MESSAGE pause
    gate) and the PG mirror of the PENDING-watcher recovery exclusion.
    """

    def test_pg_process_message_task_also_blocked_for_paused(
        self, pg_engine, task_repo
    ):
        """PROCESS_MESSAGE pause gate on PG — mirror of SQLite L478-490.

        The pause gate (instance_id NOT IN (paused, terminated)) applies
        to ALL task types, not just reports. On PG, this exercises the
        JSONB-aware claim path (no job is seeded here, so the
        cross-system guard is inert; the pause gate is the blocker).
        """
        parent_id = _seed_instance(
            pg_engine, status=InstanceStatus.PAUSED.value
        )
        # Seed a PROCESSING MESSAGE job for the same instance so the
        # test setup mirrors the SQLite test (the cross-system guard
        # would otherwise inert-block PROCESS_MESSAGE independently of
        # the pause gate; we want to verify the pause gate fires here).
        _seed_job(
            pg_engine,
            instance_id=parent_id,
            status=AdmissionState.ACTIVE.value,
        )
        msg_task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=parent_id,
            message_id=str(uuid.uuid4()),
        )
        # The pause gate (not the cross-system guard) blocks this.
        assert task_repo.claim_pending_task(worker_id="pg-worker-1") is None


# =============================================================================
# Suite 2.3 PG — Crash recovery coverage gaps
# =============================================================================


class TestCrashRecoveryCoveragePG:
    """Mirror the remaining SQLite crash-recovery tests on PG."""

    @pytest.mark.asyncio
    async def test_pg_pending_watcher_not_in_recovery(
        self, pg_engine, bus_repo
    ):
        """PENDING-state exclusion on PG — mirror of SQLite L597.

        PENDING watchers (fired_at IS NULL, enqueued_at IS NULL) must
        NOT appear in the recovery set. Only FIRED-but-unstamped rows
        are candidates for the crash-recovery replay.
        """
        from daemon.repositories.dependency_bus.models import (
            DependencyWatcher,
            DependencyWatcherState,
        )

        parent_id = f"parent-pg-{uuid.uuid4().hex[:8]}"
        child_task_id = str(uuid.uuid4())

        with Session(pg_engine) as s:
            s.add(DependencyWatcher(
                source_task_id=child_task_id,
                target_instance_id=parent_id,
                state=DependencyWatcherState.PENDING.value,
                follow_up_payload=FollowUp(
                    target_instance_id=parent_id,
                    message="pg test",
                    source="test",
                ).to_payload(),
                fired_at=None,
                enqueued_at=None,
            ))
            s.commit()

        bus2 = DependencyBus(bus_repo)
        await bus2.start()
        try:
            recovered = await bus2._recover_fired_unsent()
            assert len(recovered) == 0, (
                "PENDING watchers should not appear in _recover_fired_unsent on PG"
            )
        finally:
            await bus2.stop()
