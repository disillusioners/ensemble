"""Repository unit tests for ``TaskRepository.reconcile_turn_mirror(work_id)``.

These are focused unit tests for each of the 8 mirror tables
reconciled by the routine, plus the WAITING_CHILDREN JobItem
exception (D13), the two paired dependency-watcher tests (D10),
the invariant check, and idempotency.

Reference: ``.agents/shared/planning/turn-reconciler-migration/increment1-plan.md``
§4 (Routine), §7 (Property Tests), §8 (Test Strategy).

Coverage matrix (mirrors the plan §8 "New focused coverage"):

  1. ``job_queue_items`` — terminal Task → admission_state='done';
     in-flight Task preserves admission_state (e.g. 'queued' stays
     'queued', 'active' stays 'active'); missing Task →
     admission_state='done', terminal_reason='orphaned_no_task'.
  2. ``job_locks`` — terminal Task → lock deleted; in-flight Task →
     lock untouched; missing Task → lock deleted.
  3. ``message_queue`` — terminal Task → status='completed',
     processing_task_id=NULL; in-flight Task → untouched.
  4. ``dependency_watchers`` — paired tests (D10):
     (a) target instance has IN-FLIGHT task → watcher remains PENDING.
     (b) target instance has NO in-flight tasks → watcher CANCELLED.
  5. ``report_injections`` — terminal Task with pending injection →
     state='TASK_DELIVERED'.
  6. ``instances`` — running instance with no in-flight tasks →
     drift flag emitted, status NOT updated.
  7. ``job_watchers`` — terminal Task with dangling watcher → deleted.
  8. Idempotency — run twice → second call's data unchanged.
  9. Invariant check — active JobItem without lock →
     ``InvalidTransitionError``; lock without active JobItem →
     ``InvalidTransitionError``.
  10. WAITING_CHILDREN guard (Issue 2 / D13) — terminal Task whose
      instance is ``waiting_children`` → JobItem stays ``active``
      (not transitioned to ``done``).

All tests run against the in-memory SQLite ``engine`` fixture
(default for fast dev/CI), and the same seeds work against
PostgreSQL via the ``pg_engine`` fixture in ``tests/postgres/``.
The reconciler's snapshot guard and parameter binding use only
SQLAlchemy portable constructs — no SQLite-only syntax (no
``rowid``, no SQLite-specific timestamp functions).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register every model so ``SQLModel.metadata.create_all`` builds the
# full 8-mirror schema.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus.models import (
    DependencyWatcher,
    DependencyWatcherState,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
)
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_state_machine import InvalidTransitionError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine with the full 8-mirror schema.

    Mirrors the project's standard pattern (StaticPool for
    cross-thread safety, FK enforcement via the per-connection
    event listener so cascade constraints actually fire).
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from sqlalchemy import event

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_connection, _record):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> TaskRepository:
    """A :class:`TaskRepository` bound to the test engine."""
    return TaskRepository(engine)


# ---------------------------------------------------------------------------
# Seed helpers (mirror the property state machine helpers, but local so the
# unit tests have zero cross-file coupling).
# ---------------------------------------------------------------------------


def _seed_instance(
    engine: Engine,
    instance_id: str,
    status: str = InstanceStatus.RUNNING.value,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                project_id="test-project",
                status=status,
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()


def _seed_task(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    message_id: str,
    status: str = TaskStatus.PENDING.value,
) -> int:
    task = Task(
        work_id=work_id,
        task_type=TaskType.PROCESS_MESSAGE.value,
        instance_id=instance_id,
        message_id=message_id,
        status=status,
        created_at=datetime.now(timezone.utc),
    )
    with Session(engine) as session:
        session.add(task)
        session.commit()
        session.refresh(task)
    return int(task.id)


def _seed_message(
    engine: Engine,
    *,
    message_id: str,
    instance_id: str,
    status: str = MessageStatus.PROCESSING.value,
    processing_task_id: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="unit-test-seed",
                type=MessageType.AGENT.value,
                source="unit-test",
                status=status,
                enqueued_at=now,
                last_activity_at=now,
                processing_task_id=processing_task_id,
            )
        )
        session.commit()


def _seed_job_item(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    admission_state: str = AdmissionState.ACTIVE.value,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            JobItem(
                job_id=work_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="unit-test",
                source="api",
                project_id="test-project",
                job_type="message",
                admission_state=admission_state,
                instance_id=instance_id,
                created_at=now_iso,
            )
        )
        session.commit()


def _seed_job_lock(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    lock_slot: int = 0,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            JobLock(
                job_id=work_id,
                project_id="test-project",
                queue_id="system_parallel_queue",
                instance_id=instance_id,
                lock_slot=lock_slot,
                acquired_at=now_iso,
            )
        )
        session.commit()


def _seed_dependency_watcher(
    engine: Engine,
    *,
    source_task_pk: int,
    target_instance_id: str,
    state: str = DependencyWatcherState.PENDING.value,
    child_instance_id: str | None = None,
) -> str:
    watch_id = f"watch-{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    if child_instance_id is not None:
        follow_up_payload: dict = {
            "kind": "child_complete",
            "metadata": {"child_id": child_instance_id},
        }
    else:
        follow_up_payload = {"kind": "test"}
    with Session(engine) as session:
        session.add(
            DependencyWatcher(
                watch_id=watch_id,
                source_task_id=str(source_task_pk),
                target_instance_id=target_instance_id,
                follow_up_payload=follow_up_payload,
                watcher_metadata={"kind": "test"},
                created_at=now_iso,
                state=state,
            )
        )
        session.commit()
    return watch_id


def _seed_report_injection(
    engine: Engine,
    *,
    report_message_id: str,
    parent_instance_id: str,
    state: str = ReportInjectionState.PENDING.value,
) -> str:
    injection_id = f"inj-{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            ReportInjection(
                injection_id=injection_id,
                parent_instance_id=parent_instance_id,
                child_instance_id="child-1",
                child_message_id="child-msg-1",
                report_message_id=report_message_id,
                content="unit-test-report",
                created_at=now_iso,
                state=state,
            )
        )
        session.commit()
    return injection_id


def _seed_job_watcher(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
) -> str:
    watch_id = f"jobw-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(
            JobWatcher(
                watch_id=watch_id,
                job_id=work_id,
                instance_id=instance_id,
                created_at=now,
            )
        )
        session.commit()
    return watch_id


def _set_task_status(
    engine: Engine, work_id: str, status: str
) -> None:
    """Update Task status directly via raw SQL (bypasses repo guards)."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE task SET status = :status WHERE work_id = :work_id"),
            {"status": status, "work_id": work_id},
        )


def _read_admission(engine: Engine, work_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT admission_state, terminal_reason FROM job_queue_items "
                "WHERE job_id = :work_id"
            ),
            {"work_id": work_id},
        ).first()
    return row[0] if row else None


def _read_admission_full(
    engine: Engine, work_id: str
) -> dict[str, str | None]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT admission_state, terminal_reason FROM job_queue_items "
                "WHERE job_id = :work_id"
            ),
            {"work_id": work_id},
        ).mappings().first()
    return dict(row) if row else {"admission_state": None, "terminal_reason": None}


def _read_lock_count(engine: Engine, work_id: str) -> int:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) FROM job_locks WHERE job_id = :work_id"),
            {"work_id": work_id},
        ).first()
    return int(row[0]) if row else 0


def _read_message_status(engine: Engine, message_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM message_queue WHERE message_id = :mid"),
            {"mid": message_id},
        ).first()
    return row[0] if row else None


def _read_message_processing_task_id(
    engine: Engine, message_id: str
) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT processing_task_id FROM message_queue "
                "WHERE message_id = :mid"
            ),
            {"mid": message_id},
        ).first()
    return row[0] if row else None


def _read_watcher_state(engine: Engine, watch_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT state FROM dependency_watchers WHERE watch_id = :wid"),
            {"wid": watch_id},
        ).first()
    return row[0] if row else None


def _read_injection_state(engine: Engine, injection_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT state FROM report_injections WHERE injection_id = :iid"),
            {"iid": injection_id},
        ).first()
    return row[0] if row else None


def _read_instance_status(engine: Engine, instance_id: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM instances WHERE instance_id = :iid"),
            {"iid": instance_id},
        ).first()
    return row[0] if row else None


def _read_job_watcher_count(engine: Engine, work_id: str) -> int:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) FROM job_watchers WHERE job_id = :work_id"),
            {"work_id": work_id},
        ).first()
    return int(row[0]) if row else 0


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# 1. job_queue_items
# ---------------------------------------------------------------------------


class TestJobQueueItems:
    """Mirror table #2: ``job_queue_items`` (admission_state)."""

    def test_terminal_task_sets_admission_done(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Terminal Task → admission_state='done'."""
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _set_task_status(engine, work_id, TaskStatus.COMPLETED.value)

        result = repo.reconcile_turn_mirror(work_id)

        assert result["found"] is True
        assert result["snapshot_status"] == TaskStatus.COMPLETED.value
        assert result["updated_counts"]["job_queue_items"] >= 1
        full = _read_admission_full(engine, work_id)
        assert full["admission_state"] == AdmissionState.DONE.value
        assert full["terminal_reason"] == TaskStatus.COMPLETED.value

    def test_inflight_task_preserves_admission_state(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """In-flight Task preserves JobItem's admission_state (e.g. 'queued').

        A non-terminal Task's JobItem admission_state is intentionally
        preserved by the reconciler (e.g. awaiting slot admission as
        'queued'). The reconciler must NOT promote a 'queued' JobItem
        to 'active' for an in-flight Task — slot admission is the
        queue controller's responsibility, not the reconciler's.
        """
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.PENDING.value,
        )
        # JobItem stays in 'queued' admission_state (awaiting slot
        # admission) — reconciler must NOT promote it to 'active'.
        # No job_lock — 'queued' JobItems are awaiting slot admission
        # and don't have locks yet; the invariant check requires
        # is_active iff has_lock.
        _seed_job_item(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            admission_state=AdmissionState.QUEUED.value,
        )

        result = repo.reconcile_turn_mirror(work_id)

        assert result["snapshot_status"] == TaskStatus.PENDING.value
        assert _read_admission(engine, work_id) == AdmissionState.QUEUED.value

    def test_inflight_task_keeps_active_admission_state(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """In-flight Task preserves an already-'active' JobItem.

        Companion to test_inflight_task_preserves_admission_state:
        if the JobItem is already 'active', the in-flight reconciler
        must not downgrade it. The non-terminal CASE branch
        preserves the existing admission_state.
        """
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.PENDING.value,
        )
        _seed_job_item(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)

        result = repo.reconcile_turn_mirror(work_id)

        assert result["snapshot_status"] == TaskStatus.PENDING.value
        assert _read_admission(engine, work_id) == AdmissionState.ACTIVE.value

    def test_missing_task_sets_admission_done_orphan_reason(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Missing Task → admission_state='done', terminal_reason='orphaned_no_task'."""
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        _seed_instance(engine, instance_id)
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        # NOTE: no Task row seeded — this is the orphan case.

        result = repo.reconcile_turn_mirror(work_id)

        assert result["found"] is False
        assert result["snapshot_status"] is None
        full = _read_admission_full(engine, work_id)
        assert full["admission_state"] == AdmissionState.DONE.value
        assert full["terminal_reason"] == "orphaned_no_task"


# ---------------------------------------------------------------------------
# 2. job_locks
# ---------------------------------------------------------------------------


class TestJobLocks:
    """Mirror table #3: ``job_locks``."""

    def test_terminal_task_deletes_lock(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _set_task_status(engine, work_id, TaskStatus.CANCELLED.value)

        result = repo.reconcile_turn_mirror(work_id)

        assert result["updated_counts"]["job_locks"] >= 1
        assert _read_lock_count(engine, work_id) == 0

    def test_inflight_task_leaves_lock_untouched(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)

        repo.reconcile_turn_mirror(work_id)

        assert _read_lock_count(engine, work_id) == 1

    def test_missing_task_deletes_lock(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        _seed_instance(engine, instance_id)
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)

        result = repo.reconcile_turn_mirror(work_id)

        assert result["found"] is False
        assert result["updated_counts"]["job_locks"] >= 1
        assert _read_lock_count(engine, work_id) == 0


# ---------------------------------------------------------------------------
# 3. message_queue
# ---------------------------------------------------------------------------


class TestMessageQueue:
    """Mirror table #4: ``message_queue``."""

    def test_terminal_task_completes_message(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Terminal Task → status='completed', processing_task_id=NULL."""
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        # Seed message with a stale processing_task_id so the
        # defensive NULL write is exercised.
        _seed_message(
            engine,
            message_id=message_id,
            instance_id=instance_id,
            processing_task_id="stale-task-id",
        )
        _set_task_status(engine, work_id, TaskStatus.COMPLETED.value)

        result = repo.reconcile_turn_mirror(work_id)

        assert result["updated_counts"]["message_queue"] >= 1
        assert _read_message_status(engine, message_id) == (
            MessageStatus.COMPLETED.value
        )
        assert _read_message_processing_task_id(engine, message_id) is None

    def test_inflight_task_leaves_message_untouched(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _seed_message(
            engine,
            message_id=message_id,
            instance_id=instance_id,
            status=MessageStatus.PROCESSING.value,
        )

        repo.reconcile_turn_mirror(work_id)

        # In-flight Task's message must not be touched.
        assert _read_message_status(engine, message_id) == (
            MessageStatus.PROCESSING.value
        )


# ---------------------------------------------------------------------------
# 4. dependency_watchers (D10 — two paired tests)
# ---------------------------------------------------------------------------


class TestDependencyWatchers:
    """Mirror table #5: ``dependency_watchers`` (instance-scoped D10)."""

    def test_target_instance_drained_watcher_cancelled(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """D10 branch (b): target drained → watcher CANCELLED."""
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        task_pk = _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _seed_message(engine, message_id=message_id, instance_id=instance_id)
        watcher_id = _seed_dependency_watcher(
            engine,
            source_task_pk=task_pk,
            target_instance_id=instance_id,
        )
        _set_task_status(engine, work_id, TaskStatus.COMPLETED.value)

        result = repo.reconcile_turn_mirror(work_id)

        assert result["updated_counts"]["dependency_watchers"] >= 1
        assert _read_watcher_state(engine, watcher_id) == (
            DependencyWatcherState.CANCELLED.value
        )

    def test_target_instance_inflight_watcher_remains_pending(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """D10 branch (a): target instance has in-flight task → watcher PENDING.

        The watcher's source Task is terminal, but the target
        instance still has an in-flight Task (a separate turn).
        The watcher must remain PENDING because the parent
        instance may still produce work that depends on the child.
        """
        instance_id = _new_id("inst")
        # Turn 1 — the terminal source task (child).
        work_id_child = _new_id("work")
        message_id_child = _new_id("msg")
        _seed_instance(engine, instance_id)
        task_pk_child = _seed_task(
            engine,
            work_id=work_id_child,
            instance_id=instance_id,
            message_id=message_id_child,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id_child, instance_id=instance_id)
        _seed_job_lock(
            engine,
            work_id=work_id_child,
            instance_id=instance_id,
            lock_slot=1,
        )
        _seed_message(
            engine, message_id=message_id_child, instance_id=instance_id
        )
        watcher_id = _seed_dependency_watcher(
            engine,
            source_task_pk=task_pk_child,
            target_instance_id=instance_id,
        )
        _set_task_status(engine, work_id_child, TaskStatus.COMPLETED.value)

        # Turn 2 — an in-flight task on the same instance (so the
        # target instance is NOT drained).
        work_id_other = _new_id("work")
        message_id_other = _new_id("msg")
        _seed_task(
            engine,
            work_id=work_id_other,
            instance_id=instance_id,
            message_id=message_id_other,
            status=TaskStatus.RUNNING.value,
        )

        result = repo.reconcile_turn_mirror(work_id_child)

        assert result["updated_counts"]["dependency_watchers"] == 0
        assert _read_watcher_state(engine, watcher_id) == (
            DependencyWatcherState.PENDING.value
        )

    def test_child_instance_terminal_watcher_cancelled(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Child-liveness guard: child terminal → watcher CANCELLED.

        The watcher carries ``metadata.child_id`` (the production shape
        stamped by ``send_message``). When the watched child instance has
        reached a terminal status, the watcher is cancelled — the child
        can no longer produce work this watcher is tracking.
        """
        parent_id = _new_id("parent")
        child_id = _new_id("child")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, parent_id)
        _seed_instance(engine, child_id, status=InstanceStatus.COMPLETED.value)
        task_pk = _seed_task(
            engine,
            work_id=work_id,
            instance_id=child_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=child_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=child_id)
        _seed_message(engine, message_id=message_id, instance_id=child_id)
        watcher_id = _seed_dependency_watcher(
            engine,
            source_task_pk=task_pk,
            target_instance_id=parent_id,
            child_instance_id=child_id,
        )
        _set_task_status(engine, work_id, TaskStatus.COMPLETED.value)

        result = repo.reconcile_turn_mirror(work_id)

        assert result["updated_counts"]["dependency_watchers"] >= 1
        assert _read_watcher_state(engine, watcher_id) == (
            DependencyWatcherState.CANCELLED.value
        )

    def test_child_instance_alive_watcher_remains_pending(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Child-liveness guard: child still running → watcher PENDING.

        Regression for Inc 2026-08-02: the leader's first-task completion
        must NOT cancel the watcher while the child instance is still alive
        (running/waiting_children/paused) — even though the child's backing
        task is terminal and the parent is idle (waiting_children with zero
        in-flight tasks). The OLD guard cancelled here; the new child-id
        guard keeps the watcher PENDING because the child is non-terminal.
        """
        parent_id = _new_id("parent")
        child_id = _new_id("child")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, parent_id, status=InstanceStatus.WAITING_CHILDREN.value)
        _seed_instance(engine, child_id, status=InstanceStatus.RUNNING.value)
        task_pk = _seed_task(
            engine,
            work_id=work_id,
            instance_id=child_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=child_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=child_id)
        _seed_message(engine, message_id=message_id, instance_id=child_id)
        watcher_id = _seed_dependency_watcher(
            engine,
            source_task_pk=task_pk,
            target_instance_id=parent_id,
            child_instance_id=child_id,
        )
        _set_task_status(engine, work_id, TaskStatus.COMPLETED.value)

        result = repo.reconcile_turn_mirror(work_id)

        assert result["updated_counts"]["dependency_watchers"] == 0
        assert _read_watcher_state(engine, watcher_id) == (
            DependencyWatcherState.PENDING.value
        )


# ---------------------------------------------------------------------------
# 5. report_injections
# ---------------------------------------------------------------------------


class TestReportInjections:
    """Mirror table #6: ``report_injections``."""

    def test_terminal_task_delivers_pending_injection(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Terminal Task with pending injection → state='TASK_DELIVERED'."""
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _seed_message(engine, message_id=message_id, instance_id=instance_id)
        injection_id = _seed_report_injection(
            engine,
            report_message_id=message_id,
            parent_instance_id=instance_id,
        )
        _set_task_status(engine, work_id, TaskStatus.COMPLETED.value)

        result = repo.reconcile_turn_mirror(work_id)

        assert result["updated_counts"]["report_injections"] >= 1
        assert _read_injection_state(engine, injection_id) == (
            ReportInjectionState.TASK_DELIVERED.value
        )


# ---------------------------------------------------------------------------
# 6. instances (soft drift only)
# ---------------------------------------------------------------------------


class TestInstancesSoftDrift:
    """Mirror table #7: ``instances`` (verify-and-flag only)."""

    def test_running_instance_without_inflight_emits_drift_flag(
        self, engine: Engine, repo: TaskRepository, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Running instance with no in-flight tasks → drift flag, status NOT updated."""
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id, InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _seed_message(engine, message_id=message_id, instance_id=instance_id)
        _set_task_status(engine, work_id, TaskStatus.COMPLETED.value)

        with caplog.at_level("WARNING"):
            result = repo.reconcile_turn_mirror(work_id)

        # Drift flag MUST be emitted (the reconciler detected the
        # inconsistency but does NOT force a status transition).
        assert (
            "instance_running_without_inflight_task" in result["drift_flags"]
        ), f"Expected drift flag, got {result['drift_flags']}"
        # Instance status must NOT be updated by the reconciler.
        assert _read_instance_status(engine, instance_id) == (
            InstanceStatus.RUNNING.value
        )

    def test_running_instance_without_inflight_drift_log_is_debug_not_warning(
        self, engine: Engine, repo: TaskRepository, caplog: pytest.LogCaptureFixture
    ) -> None:
        """P2 (2026-08-02): drift must log at DEBUG, never WARNING.

        This is a known transient artifact of the pause→resume
        cascade window: the old Task is cancelled and the instance
        flipped to RUNNING before the new resume task is scheduled.
        The reconciler catches the gap; it self-heals within ~1s.
        Logging at WARNING was flooding production telemetry with
        non-actionable signal. Captured at WARNING level so any DEBUG
        emission is filtered out by the caplog level — if a regression
        re-raises the level, the assertion below trips.
        """
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id, InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _seed_message(engine, message_id=message_id, instance_id=instance_id)
        _set_task_status(engine, work_id, TaskStatus.COMPLETED.value)

        with caplog.at_level("WARNING"):
            result = repo.reconcile_turn_mirror(work_id)

        # Drift flag MUST still be populated (the diagnostic data
        # is unchanged; only the log level moved).
        assert (
            "instance_running_without_inflight_task" in result["drift_flags"]
        ), f"Expected drift flag, got {result['drift_flags']}"

        # No WARNING-level "Turn mirror drift" message — the fix
        # downgraded this to DEBUG (transient self-healing artifact).
        warning_drift_records = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "Turn mirror drift" in r.getMessage()
        ]
        assert warning_drift_records == [], (
            "Drift is a known transient artifact; must not log at WARNING. "
            f"Got: {[r.getMessage() for r in warning_drift_records]}"
        )


# ---------------------------------------------------------------------------
# 7. job_watchers
# ---------------------------------------------------------------------------


class TestJobWatchers:
    """Mirror table #8: ``job_watchers``."""

    def test_terminal_task_watcher_preserved(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Terminal Task's watchers are NOT deleted — they may belong to
        retry children with migrated watchers that must survive.

        Per Approver direction (Iteration 2 review), job_watchers are
        deleted ONLY when the Task row is completely gone (truly
        dangling), NOT when the Task is terminal.
        """
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _seed_message(engine, message_id=message_id, instance_id=instance_id)
        _seed_job_watcher(engine, work_id=work_id, instance_id=instance_id)
        _set_task_status(engine, work_id, TaskStatus.COMPLETED.value)

        result = repo.reconcile_turn_mirror(work_id)

        # Terminal Task watchers are NOT deleted — they may belong to
        # retry children.
        assert result["updated_counts"].get("job_watchers", 0) == 0, (
            f"Expected job_watchers to survive terminal Task, "
            f"got updated_counts={result['updated_counts']}"
        )
        assert _read_job_watcher_count(engine, work_id) == 1

    def test_hard_deleted_task_watcher_deleted(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Hard-deleted Task (row gone) → watcher IS deleted.

        The job_watchers handler only fires when the Task row no
        longer exists (NOT EXISTS check), which distinguishes truly
        dangling watchers from terminal-but-present Tasks whose
        watchers may belong to retry children.
        """
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _seed_message(engine, message_id=message_id, instance_id=instance_id)
        _seed_job_watcher(engine, work_id=work_id, instance_id=instance_id)

        # Hard-delete the Task row (simulates row completely gone).
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM task WHERE work_id = :work_id"),
                {"work_id": work_id},
            )

        result = repo.reconcile_turn_mirror(work_id)

        assert result["updated_counts"].get("job_watchers", 0) >= 1, (
            f"Expected job_watchers to be deleted for hard-deleted Task, "
            f"got updated_counts={result['updated_counts']}"
        )
        assert _read_job_watcher_count(engine, work_id) == 0


# ---------------------------------------------------------------------------
# 8. Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Idempotency: re-running with the same state is a no-op."""

    def test_second_call_data_unchanged(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Running the reconciler twice leaves the 8-mirror data unchanged.

        Rowcount on the second call is allowed to be non-zero
        (SQLAlchemy reports rows MATCHED by the WHERE clause,
        not rows whose data actually changed); the SET clauses
        are designed to be self-stabilizing.
        """
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _seed_message(engine, message_id=message_id, instance_id=instance_id)
        injection_id = _seed_report_injection(
            engine,
            report_message_id=message_id,
            parent_instance_id=instance_id,
        )
        watcher_id = _seed_dependency_watcher(
            engine,
            source_task_pk=1,
            target_instance_id=instance_id,
        )
        _set_task_status(engine, work_id, TaskStatus.COMPLETED.value)

        # First reconcile.
        repo.reconcile_turn_mirror(work_id)
        snap_first = {
            "admission": _read_admission(engine, work_id),
            "lock_count": _read_lock_count(engine, work_id),
            "message_status": _read_message_status(engine, message_id),
            "message_pid": _read_message_processing_task_id(engine, message_id),
            "watcher_state": _read_watcher_state(engine, watcher_id),
            "injection_state": _read_injection_state(engine, injection_id),
            "job_watcher_count": _read_job_watcher_count(engine, work_id),
        }
        # Second reconcile on the same state.
        repo.reconcile_turn_mirror(work_id)
        snap_second = {
            "admission": _read_admission(engine, work_id),
            "lock_count": _read_lock_count(engine, work_id),
            "message_status": _read_message_status(engine, message_id),
            "message_pid": _read_message_processing_task_id(engine, message_id),
            "watcher_state": _read_watcher_state(engine, watcher_id),
            "injection_state": _read_injection_state(engine, injection_id),
            "job_watcher_count": _read_job_watcher_count(engine, work_id),
        }

        assert snap_first == snap_second, (
            f"Idempotency violation: {snap_first} != {snap_second}"
        )


# ---------------------------------------------------------------------------
# 9. Invariant check (InvalidTransitionError)
# ---------------------------------------------------------------------------


class TestInvariantCheck:
    """Invariant check raises ``InvalidTransitionError`` on mismatch."""

    def test_active_jobitem_without_lock_raises(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Admission='active' but no job_locks row → InvalidTransitionError."""
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.RUNNING.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        # NOTE: no job_locks row seeded.

        with pytest.raises(InvalidTransitionError):
            repo.reconcile_turn_mirror(work_id)

    def test_lock_without_active_jobitem_reconciled(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """JobItem done + lock present → reconciler deletes the lock (no error).

        The "done with lock" direction of the invariant is ALWAYS
        fixed by the reconciler's ``job_locks`` handler (the
        DELETE FROM job_locks runs for terminal Tasks before the
        invariant check). So this direction cannot trigger
        ``InvalidTransitionError`` in a normal single-threaded
        flow — the reconciler removes the offending lock before
        the check sees it.

        This test documents that behavior: after a terminal Task
        is reconciled with a surviving lock, the reconciler
        deletes the lock (so the invariant check sees
        is_active=False, has_lock=False — consistent).
        """
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.COMPLETED.value,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)

        # First reconcile: terminal Task → admission='done', lock
        # deleted (consistent).
        first = repo.reconcile_turn_mirror(work_id)
        assert first["updated_counts"]["job_locks"] >= 1
        assert _read_lock_count(engine, work_id) == 0

        # Re-create the lock to simulate a race that left the lock
        # alive after the JobItem was finalized.
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        assert _read_lock_count(engine, work_id) == 1

        # Second reconcile: the reconciler's job_locks handler
        # deletes the lock again (terminal Task). No
        # InvalidTransitionError is raised because the lock is
        # removed before the invariant check.
        second = repo.reconcile_turn_mirror(work_id)
        assert second["updated_counts"]["job_locks"] >= 1
        assert _read_lock_count(engine, work_id) == 0


# ---------------------------------------------------------------------------
# 10. WAITING_CHILDREN guard (Issue 2 / D13)
# ---------------------------------------------------------------------------


class TestWaitingChildrenGuard:
    """WAITING_CHILDREN JobItem exception (D13, Approver Issue 2)."""

    def test_waiting_children_keeps_jobitem_active(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Terminal Task + ``instances.status='waiting_children'`` → JobItem stays 'active'.

        The JobItem is intentionally retained as a semaphore for
        child-completion correlation when the instance is waiting
        for children. The reconciler's job_queue_items handler
        must NOT transition it to 'done'.
        """
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id, InstanceStatus.WAITING_CHILDREN.value)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.COMPLETED.value,
        )
        _seed_job_item(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _seed_message(engine, message_id=message_id, instance_id=instance_id)

        result = repo.reconcile_turn_mirror(work_id)

        # JobItem MUST remain 'active' (the WAITING_CHILDREN
        # exception in the job_queue_items handler preserves it).
        assert _read_admission(engine, work_id) == (
            AdmissionState.ACTIVE.value
        ), (
            f"waiting_children JobItem should stay 'active', got "
            f"{_read_admission(engine, work_id)}"
        )
        # terminal_reason should NOT be set (the WAITING_CHILDREN
        # exception preserves the existing reason, which is None).
        full = _read_admission_full(engine, work_id)
        assert full["terminal_reason"] is None
        # The reconciler should NOT raise — the WAITING_CHILDREN
        # exception is a documented carve-out from the active/lock
        # invariant because the JobItem is intentionally retained
        # alongside its lock.
        assert "InvalidTransitionError" not in str(result)

    def test_non_waiting_children_transitions_jobitem_to_done(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Terminal Task + ``instances.status='running'`` → JobItem transitions to 'done'.

        The control case for the WAITING_CHILDREN exception: with
        a normal instance status, the terminal Task's JobItem must
        transition to 'done'.
        """
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id, InstanceStatus.RUNNING.value)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            status=TaskStatus.COMPLETED.value,
        )
        _seed_job_item(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            admission_state=AdmissionState.ACTIVE.value,
        )
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _seed_message(engine, message_id=message_id, instance_id=instance_id)

        result = repo.reconcile_turn_mirror(work_id)

        assert _read_admission(engine, work_id) == AdmissionState.DONE.value
        full = _read_admission_full(engine, work_id)
        assert full["terminal_reason"] == TaskStatus.COMPLETED.value
