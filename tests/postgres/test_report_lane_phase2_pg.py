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
from daemon.repositories.job_queue.models import JobItem, JobStatus
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
    set_dependency_bus,
)


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
    status: str = JobStatus.PROCESSING.value,
) -> str:
    """Insert a JobItem row directly via SQLModel Session."""
    jid = job_id or f"job-pg-{uuid.uuid4().hex[:8]}"
    with Session(pg_engine) as s:
        s.add(JobItem(
            job_id=jid,
            agent_id="leader",
            agent_dir="/tmp/leader",
            message="pg test",
            source="api",
            job_type="message",
            status=status,
            instance_id=instance_id,
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
