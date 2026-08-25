"""Regression tests for the paused-race terminal-write guard in
``TaskRepository.reconcile_turn_mirror(work_id)``.

Incident (2026-08-24, "resume stamps live Job cancelled"): during
``resume_processing_job``, ``cancel_task`` on the superseded task fires
``reconcile_turn_mirror`` post-commit while the parent instance is still
``paused`` (the instance UPDATE in ``_resume_cascade_db_sync`` runs later
— or has already flipped the instance back to ``running`` before the
replacement task is scheduled via the outbox). The OLD terminal-write
guards excluded the write ONLY when the instance was
``waiting_children``, so the race stamped the live Job
``admission_state='done'``, ``terminal_reason='cancelled'``,
``failed_at=now`` and deleted its ``job_locks`` row. Natural completion
could never repair it (job finalize only writes when the current
admission_state is ``active``/``queued``/``paused``).

Fix: every ``NOT EXISTS`` exclusion of the TERMINAL-WRITE path only —
the three ``job_queue_items`` CASE branches (admission_state,
terminal_reason, failed_at) and the ``job_locks`` DELETE — now suppress
when the instance status is ``waiting_children``, ``paused``, OR
``running``. Terminal instance statuses
(``completed``/``failed``/``terminated``/``error``) still let the
terminal write through.

Coverage (all via the public ``reconcile_turn_mirror`` entry point,
following the sibling ``test_turn_reconciler.py`` harness — in-memory
SQLite, full 8-mirror schema, helpers kept local for zero cross-file
coupling):

  1. instance ``paused``   → NO job terminal write, NO job_locks delete.
  2. instance ``running``  → NO job terminal write, NO job_locks delete.
  3. instance ``waiting_children`` → NO write (existing D13 behavior,
     now pinned explicitly for the extended guard).
  4. instance terminal (``completed``) → write DOES happen:
     ``done``/``cancelled``/``failed_at`` stamped, lock deleted.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register every model so ``SQLModel.metadata.create_all`` builds the
# full 8-mirror schema (mirrors test_turn_reconciler.py).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
)
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


# ---------------------------------------------------------------------------
# Fixtures (sibling pattern: tests/repositories/test_turn_reconciler.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine with the full 8-mirror schema."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

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
# Seed helpers (local, mirroring the sibling file's zero-coupling style)
# ---------------------------------------------------------------------------


def _seed_instance(
    engine: Engine,
    instance_id: str,
    status: str,
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
) -> int:
    task = Task(
        work_id=work_id,
        task_type=TaskType.PROCESS_MESSAGE.value,
        instance_id=instance_id,
        message_id=message_id,
        status=TaskStatus.RUNNING.value,
        created_at=datetime.now(timezone.utc),
    )
    with Session(engine) as session:
        session.add(task)
        session.commit()
        session.refresh(task)
    return int(task.id)


def _set_task_status(engine: Engine, work_id: str, status: str) -> None:
    """Update Task status directly via raw SQL (bypasses repo guards)."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE task SET status = :status WHERE work_id = :work_id"),
            {"status": status, "work_id": work_id},
        )


def _seed_message(engine: Engine, *, message_id: str, instance_id: str) -> None:
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="paused-race-regression-seed",
                type=MessageType.AGENT.value,
                source="unit-test",
                status=MessageStatus.PROCESSING.value,
                enqueued_at=now,
                last_activity_at=now,
            )
        )
        session.commit()


def _seed_job_item(engine: Engine, *, work_id: str, instance_id: str) -> None:
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
                admission_state=AdmissionState.ACTIVE.value,
                instance_id=instance_id,
                created_at=now_iso,
            )
        )
        session.commit()


def _seed_job_lock(engine: Engine, *, work_id: str, instance_id: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            JobLock(
                job_id=work_id,
                project_id="test-project",
                queue_id="system_parallel_queue",
                instance_id=instance_id,
                lock_slot=0,
                acquired_at=now_iso,
            )
        )
        session.commit()


def _read_job_row(engine: Engine, work_id: str) -> dict[str, str | None]:
    """Read the terminal-write surface of ``job_queue_items``.

    Includes ``failed_at`` — the incident stamped it via the third CASE
    branch, so the regression must pin it explicitly.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT admission_state, terminal_reason, failed_at "
                "FROM job_queue_items WHERE job_id = :work_id"
            ),
            {"work_id": work_id},
        ).mappings().first()
    if row is None:
        return {
            "admission_state": None,
            "terminal_reason": None,
            "failed_at": None,
        }
    return dict(row)


def _read_lock_count(engine: Engine, work_id: str) -> int:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) FROM job_locks WHERE job_id = :work_id"),
            {"work_id": work_id},
        ).first()
    return int(row[0]) if row else 0


def _new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# The paused-race regression matrix
# ---------------------------------------------------------------------------


class TestPausedRaceTerminalWriteGuard:
    """Instance-status suppression matrix for the reconciler's job
    terminal-write path (admission_state/terminal_reason/failed_at CASE
    branches + job_locks DELETE)."""

    def _seed_incident_shape(
        self,
        engine: Engine,
        *,
        instance_status: str,
    ) -> str:
        """Seed the prod-incident shape: a PROCESS_MESSAGE Job whose
        backing task is cancelled (superseded during resume) while the
        owning instance sits in ``instance_status``.

        Returns the work_id to reconcile.
        """
        instance_id = _new_id("inst")
        work_id = _new_id("work")
        message_id = _new_id("msg")
        _seed_instance(engine, instance_id, instance_status)
        _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
        )
        _seed_job_item(engine, work_id=work_id, instance_id=instance_id)
        _seed_job_lock(engine, work_id=work_id, instance_id=instance_id)
        _seed_message(engine, message_id=message_id, instance_id=instance_id)
        # The superseded-task transition: RUNNING -> CANCELLED (this is
        # what cancel_task does during resume_processing_job).
        _set_task_status(engine, work_id, TaskStatus.CANCELLED.value)
        return work_id

    def test_paused_instance_suppresses_job_terminal_write(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Instance 'paused' → NO job terminal write, NO job_locks delete.

        This is the exact prod-incident interleaving: the reconciler
        fires post-commit from cancel_task while the parent instance is
        still 'paused' (the resume cascade's instance UPDATE lands
        later). The Job must NOT be stamped done/cancelled.
        """
        work_id = self._seed_incident_shape(
            engine, instance_status=InstanceStatus.PAUSED.value
        )

        result = repo.reconcile_turn_mirror(work_id)

        row = _read_job_row(engine, work_id)
        assert row["admission_state"] == AdmissionState.ACTIVE.value, (
            f"paused-race: admission_state should stay 'active', "
            f"got {row['admission_state']}"
        )
        assert row["terminal_reason"] is None, (
            f"paused-race: terminal_reason should stay NULL, "
            f"got {row['terminal_reason']}"
        )
        assert row["failed_at"] is None, (
            f"paused-race: failed_at should stay NULL, "
            f"got {row['failed_at']}"
        )
        assert _read_lock_count(engine, work_id) == 1, (
            "paused-race: job_locks row must survive"
        )
        assert result["updated_counts"]["job_locks"] == 0
        # JobItem 'active' + lock present is the consistent invariant
        # state — the reconciler must not raise.
        assert "InvalidTransitionError" not in str(result)

    def test_running_instance_suppresses_job_terminal_write(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Instance 'running' → NO job terminal write, NO job_locks delete.

        Sibling interleaving of the same race: the resume cascade flips
        the instance to RUNNING before the replacement task is scheduled
        (the documented drift window), so the superseded task's
        reconcile fires against a 'running' instance. A live instance
        must keep its Job row and lock intact.
        """
        work_id = self._seed_incident_shape(
            engine, instance_status=InstanceStatus.RUNNING.value
        )

        result = repo.reconcile_turn_mirror(work_id)

        row = _read_job_row(engine, work_id)
        assert row["admission_state"] == AdmissionState.ACTIVE.value, (
            f"running-instance guard: admission_state should stay "
            f"'active', got {row['admission_state']}"
        )
        assert row["terminal_reason"] is None
        assert row["failed_at"] is None
        assert _read_lock_count(engine, work_id) == 1
        assert result["updated_counts"]["job_locks"] == 0
        assert "InvalidTransitionError" not in str(result)

    def test_waiting_children_instance_suppresses_job_terminal_write(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Instance 'waiting_children' → NO write (existing D13 carve-out).

        Pins the pre-existing WAITING_CHILDREN suppression explicitly
        for the extended guard — the IN-list must not have regressed
        the original exception.
        """
        work_id = self._seed_incident_shape(
            engine, instance_status=InstanceStatus.WAITING_CHILDREN.value
        )

        result = repo.reconcile_turn_mirror(work_id)

        row = _read_job_row(engine, work_id)
        assert row["admission_state"] == AdmissionState.ACTIVE.value
        assert row["terminal_reason"] is None
        assert row["failed_at"] is None
        assert _read_lock_count(engine, work_id) == 1
        assert result["updated_counts"]["job_locks"] == 0
        assert "InvalidTransitionError" not in str(result)

    def test_terminal_instance_writes_job_terminal_state(
        self, engine: Engine, repo: TaskRepository
    ) -> None:
        """Instance terminal ('completed') → the write DOES happen.

        Positive control: when the owning instance has actually reached
        a terminal status, the reconciler still stamps the Job terminal
        state (done + cancelled + failed_at) and releases the lock —
        the guard extension must not swallow genuine finalization.
        """
        work_id = self._seed_incident_shape(
            engine, instance_status=InstanceStatus.COMPLETED.value
        )

        result = repo.reconcile_turn_mirror(work_id)

        row = _read_job_row(engine, work_id)
        assert row["admission_state"] == AdmissionState.DONE.value, (
            f"terminal instance: admission_state should be 'done', "
            f"got {row['admission_state']}"
        )
        assert row["terminal_reason"] == TaskStatus.CANCELLED.value
        assert row["failed_at"] is not None, (
            "terminal instance + cancelled task must stamp failed_at"
        )
        assert _read_lock_count(engine, work_id) == 0
        assert result["updated_counts"]["job_locks"] >= 1
