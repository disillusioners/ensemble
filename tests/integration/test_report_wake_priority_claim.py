"""Integration: parent wake INDEPENDENT of FIFO position — the
PROCESS_REPORT wake lane in ``TaskRepository.claim_pending_task``.

Debug Phase 4 fix #1 (terminal-report wake). Incident 7807e521
(diagnosed 2026-09-07): a child's completion report reached the parent
14m49s late. The bus is a pure state machine (FIRED watchers are
bookkeeping only — they are never enqueued), so the parent's wake IS
the PROCESS_REPORT task's claim: a worker must pick the report task up
ahead of older queued work when the pool frees. Under strict
``ORDER BY created_at ASC`` FIFO with a saturated pool
(WORKER_POOL_SIZE=4, 16 in-flight partition tasks), the younger report
task starved behind every older PENDING partition task.

The fix adds a two-tier claim ranking inside the SAME atomic
UPDATE...RETURNING:

    ORDER BY (task_type = 'process_report') DESC, created_at ASC

Every existing gate (defer / background / queue-awareness /
per-instance / pause / cross-system) still filters candidates BEFORE
ranking — this file pins that dominance too.

These tests run the REAL repository SQL (not mocks). Scenario 1 fails
on pre-fix code (strict FIFO claims the oldest process_message) — it
is the bug-exercising proof for the wake lane.

Fixture: file-backed SQLite at ``tmp_path`` with NullPool,
``PRAGMA journal_mode=WAL``, ``PRAGMA busy_timeout=10000`` (project
Testing & QC conventions — StaticPool + WriteGuardSession is
forbidden).

Run with::

    pytest tests/integration/test_report_wake_priority_claim.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401 — table registration
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite: NullPool + WAL + busy_timeout=10000."""
    db_path = tmp_path / "report_wake_priority_claim.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    """Real repository — the SQL under test, not a mock."""
    return TaskRepository(engine=engine)


# ─── Seed helpers ────────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    instance_id: str,
    status: str = InstanceStatus.RUNNING.value,
) -> None:
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                status=status,
            )
        )
        s.commit()


def _seed_task(
    engine: Engine,
    *,
    task_type: str,
    instance_id: str,
    created_min_ago: float,
    status: str = TaskStatus.PENDING.value,
    message_id: str | None = None,
) -> int:
    """Insert a Task with a deterministic created_at (minutes ago)."""
    created_at = datetime.now(timezone.utc) - timedelta(minutes=created_min_ago)
    with Session(engine) as s:
        task = Task(
            task_type=task_type,
            instance_id=instance_id,
            status=status,
            created_at=created_at,
            message_id=message_id,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return int(task.id)


def _saturated_pool(engine: Engine, *, workers: int = 4) -> list[int]:
    """Simulate pool saturation: ``workers`` tasks RUNNING on DISTINCT
    instances (each occupies one worker; the per-instance guard is not
    involved since every instance has exactly one RUNNING task)."""
    ids = []
    for w in range(workers):
        iid = f"inst-busy-{w}"
        _seed_instance(engine, iid)
        ids.append(
            _seed_task(
                engine,
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=iid,
                created_min_ago=30.0,
                status=TaskStatus.RUNNING.value,
            )
        )
    return ids


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestReportWakeLane:
    def test_report_task_claims_ahead_of_older_pending_under_saturation(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """THE INCIDENT SHAPE (bug-exercising proof).

        Pool saturated (4 workers occupied), 8 OLDER PENDING
        process_message partition tasks queued on distinct instances,
        and the child's PROCESS_REPORT task created LAST (youngest).
        A freeing worker must claim the REPORT task — not starve
        behind the older queue.

        Pre-fix (strict ``ORDER BY created_at ASC``) this claims the
        oldest process_message and the parent's wake is stuck behind
        the whole queue — exactly the 7807e521 14m49s delay.
        """
        parent_iid = "inst-parent"
        _seed_instance(engine, parent_iid)

        # Occupied workers.
        _saturated_pool(engine, workers=4)

        # Older queued partition tasks — each on its own RUNNING
        # instance so the per-instance guard never blocks them; all
        # OLDER than the report task.
        older_ids = [
            _seed_task(
                engine,
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=f"inst-old-{i}",
                created_min_ago=20.0 - i,  # 20, 19, ..., 13 minutes ago
                message_id=f"old-msg-{i}",
            )
            for i in range(8)
        ]
        for i in range(8):
            _seed_instance(engine, f"inst-old-{i}")

        # The report task — created LAST (youngest in the queue).
        report_id = _seed_task(
            engine,
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=parent_iid,
            created_min_ago=1.0,
            message_id="report-msg-1",
        )

        claimed = task_repo.claim_pending_task(worker_id="worker-free-1")

        assert claimed is not None
        assert claimed.id == report_id, (
            "the PROCESS_REPORT task must claim FIRST despite being the "
            "youngest PENDING task — its claim IS the parent wake "
            "(7807e521: report starved 14m49s under strict FIFO)"
        )
        assert claimed.task_type == TaskType.PROCESS_REPORT.value
        assert claimed.status == TaskStatus.RUNNING.value

        # Every older task is untouched and still PENDING.
        with Session(engine) as s:
            for tid in older_ids:
                row = s.get(Task, tid)
                assert row.status == TaskStatus.PENDING.value

    def test_fifo_preserved_within_the_process_message_tier(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """No report tasks pending → plain FIFO across process_message
        tasks (oldest first). The wake lane must not disturb the
        existing ordering among non-report work."""
        for i in range(3):
            _seed_instance(engine, f"inst-fifo-{i}")
        oldest = _seed_task(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-fifo-0",
            created_min_ago=15.0,
        )
        _seed_task(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-fifo-1",
            created_min_ago=10.0,
        )
        middle = oldest

        claimed = task_repo.claim_pending_task(worker_id="w1")
        assert claimed is not None
        assert claimed.id == middle, (
            "with no report task pending, the oldest process_message "
            "claims (FIFO within tier unchanged)"
        )

    def test_fifo_preserved_between_two_report_tasks(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """Two report tasks pending → FIFO among THEM (wake lane is a
        tier, not a re-roll of dice): the older report claims first."""
        _seed_instance(engine, "inst-parent-a")
        _seed_instance(engine, "inst-parent-b")
        older_report = _seed_task(
            engine,
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id="inst-parent-a",
            created_min_ago=5.0,
        )
        _seed_task(
            engine,
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id="inst-parent-b",
            created_min_ago=2.0,
        )

        claimed = task_repo.claim_pending_task(worker_id="w1")
        assert claimed is not None
        assert claimed.id == older_report, (
            "within the wake lane, report tasks keep created_at FIFO"
        )

    def test_pause_gate_still_dominates_the_wake_lane(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """A report task whose parent instance is PAUSED must NOT claim —
        the pause gate filters candidates BEFORE the wake-lane ranking.
        A YOUNGER process_message task on a RUNNING instance claims
        instead (proving the lane did not merely win by age).
        """
        paused_parent = "inst-paused-parent"
        _seed_instance(engine, paused_parent, status=InstanceStatus.PAUSED.value)
        _seed_instance(engine, "inst-live")

        # OLDER report task on the PAUSED parent — gate-blocked.
        blocked_report = _seed_task(
            engine,
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=paused_parent,
            created_min_ago=10.0,
        )
        # YOUNGER process_message on a live instance.
        live_task = _seed_task(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-live",
            created_min_ago=1.0,
        )

        claimed = task_repo.claim_pending_task(worker_id="w1")
        assert claimed is not None
        assert claimed.id == live_task, (
            "the pause gate must dominate the wake lane — a report task "
            "on a paused parent never claims (deferred/resume semantics "
            "preserved)"
        )
        with Session(engine) as s:
            assert s.get(Task, blocked_report).status == TaskStatus.PENDING.value

    def test_busy_instance_still_blocks_sibling_report_claim(
        self, engine: Engine, task_repo: TaskRepository
    ):
        """Per-instance serialization: a report task whose instance has a
        RUNNING task is not claimable, even with the wake lane — one
        graph turn per instance is the invariant the lane must not
        break."""
        iid = "inst-serial"
        _seed_instance(engine, iid)
        # RUNNING task occupies the instance (a live graph turn).
        running_id = _seed_task(
            engine,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=iid,
            created_min_ago=9.0,
            status=TaskStatus.RUNNING.value,
        )
        # The report task for the SAME instance — younger.
        _seed_task(
            engine,
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id=iid,
            created_min_ago=1.0,
        )

        claimed = task_repo.claim_pending_task(worker_id="w1")
        assert claimed is None, f"per-instance guard must block the wake-lane report over a RUNNING turn, got id={getattr(claimed, 'id', None)} type={getattr(claimed, 'task_type', None)}"
