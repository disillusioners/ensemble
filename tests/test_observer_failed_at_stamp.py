"""Regression tests for the failed_at stamp added to the
observer's terminal-write path (paused-race amendment, 2026-08-25).

Incident context: the reconciler's alive-instance guard
(paused/running/waiting_children) suppresses the ``job_queue_items``
CASE branches, which removed the only live writer of ``failed_at``.
A failed task on a RUNNING instance ended
``done`` / ``terminal_reason='failed'`` / ``failed_at=NULL`` and
``atomic_retry`` rejected such rows (it requires
``failed_at IS NOT NULL`` per ``JobRepository.atomic_retry``).

Amendment: the OBSERVER now stamps ``failed_at`` on the FAILED
branch only (``_finalize_job_db_sync`` Step 1, ``finalize_active_to_done``,
and the W3 fail-safe path). Cancelled finalizes keep NULL per retry
semantics — cancelled rows are not retryable.

These tests exercise the **real** ``_finalize_job_db_sync`` end-to-end
over an in-memory SQLite engine (mirroring the harness pattern in
``tests/test_finalize_job_h15.py``) so the stamp lands in the DB
column, then assert retry acceptance via the **real**
``JobRepository.atomic_retry`` API — the strongest form of the
amendment's contract (column present + retry accepts the row).

Red/green technique (proved in the report):

  git stash push -- daemon/services/job_feedback_observer.py
    → T1 FAILS (atomic_retry returns None; failed_at never stamped)
  git stash pop
    → T1 PASSES (stamp back in; atomic_retry accepts)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register every model so ``SQLModel.metadata.create_all`` builds the
# full 8-mirror schema (mirrors the h15 harness).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.models import JobItem, JobLock
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.write_pause_guard import WritePauseGuard


# ---------------------------------------------------------------------------
# Fixtures / harness (minimal subset of tests/test_finalize_job_h15.py
# needed to drive the sync helper end-to-end against a real engine)
# ---------------------------------------------------------------------------

# The in-session gate inside ``_finalize_job_db_sync`` requires the
# DependencyBus singleton to be wired (A9 invariant — raises RuntimeError
# when None). Mirror the h15 harness's autouse bus mock with zero pending
# watchers so the gate passes.
import asyncio

from daemon.services.dependency_bus import set_dependency_bus

_BUS_PENDING = [0]


@pytest.fixture(autouse=True)
def _wire_bus_mock():
    bus_mock = MagicMock()
    bus_mock.count_pending_for_target_sync = lambda iid: _BUS_PENDING[0]
    bus_mock._get_parent_lock = AsyncMock(side_effect=lambda parent_id: asyncio.Lock())
    bus_mock.get_generation = MagicMock(return_value=0)
    set_dependency_bus(bus_mock)
    yield
    set_dependency_bus(None)
    _BUS_PENDING[0] = 0


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _seed_instance(engine: Engine, *, status: str) -> str:
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="developer",
                agent_dir="/tmp/agent",
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        s.commit()
    return iid


def _seed_job(engine: Engine, *, instance_id: str) -> JobItem:
    jid = f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        item = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agent",
            message="unit-test",
            source="api",
            job_type="task",
            admission_state="active",
            instance_id=instance_id,
            project_id="test-project",
        )
        s.add(item)
        s.commit()
        s.refresh(item)
    return item


def _seed_lock(engine: Engine, *, instance_id: str) -> str:
    lid = f"lock-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        s.add(
            JobLock(
                lock_id=lid,
                project_id="test-project",
                queue_id="default",
                job_id=f"job-{uuid.uuid4().hex[:8]}",
                instance_id=instance_id,
                lock_slot=0,
            )
        )
        s.commit()
    return lid


def _get_job(engine: Engine, job_id: str) -> JobItem | None:
    with Session(engine) as s:
        return s.get(JobItem, job_id)


def _count_locks(engine: Engine, instance_id: str) -> int:
    from sqlmodel import select

    with Session(engine) as s:
        return len(
            list(s.exec(select(JobLock).where(JobLock.instance_id == instance_id)).all())
        )


def _make_observer(engine: Engine) -> JobFeedbackObserver:
    """Build a ``JobFeedbackObserver`` with a real engine + mocked side deps.

    The instance_manager uses the REAL engine so that
    ``_finalize_job_db_sync`` (which calls ``Session(engine)``
    internally) exercises end-to-end DB writes. The async wrappers
    (handle_correlation_complete, notify_watchers, etc.) are not
    exercised by T1 — it calls the sync helper directly.
    """
    guard = WritePauseGuard()
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = guard
    manager.is_write_paused = False

    mock_lock_repo = MagicMock()
    mock_lock_repo.release_by_instance = MagicMock(return_value=0)

    return JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=MagicMock(),
        job_repo=MagicMock(),
        lock_repo=mock_lock_repo,
        project_repo=MagicMock(),
        instance_manager=manager,
    )


# ---------------------------------------------------------------------------
# T1 — failed_at stamp + retry acceptance (red/green)
# ---------------------------------------------------------------------------


class TestFailedAtStamp:
    """The observer's failed-path stamp restores retryability for
    failed jobs whose instance was alive when the reconciler fired
    (paused/running/waiting_children)."""

    def test_failed_path_stamps_failed_at_and_row_is_retryable(
        self, engine: Engine
    ) -> None:
        """Failed finalize on a RUNNING instance stamps failed_at AND
        the row is accepted by the real ``atomic_retry`` API.

        Pre-amendment: failed_at stayed NULL → ``atomic_retry``
        rejected the row (rowcount=0) because its SQL guard requires
        ``failed_at IS NOT NULL``. This test exercises the full
        end-to-end stamp→retry chain.
        """
        # Mirror the prod incident shape: RUNNING instance + active
        # JobItem + lock held by the worker (the observer finalizes
        # while the instance is mid-turn, exactly the interleaving
        # the reconciler used to wrongly stamp).
        instance_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        job = _seed_job(engine, instance_id=instance_id)
        _seed_lock(engine, instance_id=instance_id)

        observer = _make_observer(engine)

        # Drive the FAILED finalize via the real sync helper —
        # the same code path ``handle_correlation_complete("error")``
        # uses in production (terminal_status='error' → to_status='failed').
        result = observer._finalize_job_db_sync(
            job_id=job.job_id,
            instance_id=instance_id,
            terminal_status=InstanceStatus.ERROR.value,
            result_summary=None,
            error_message="unit-test error",
        )
        assert result.skip is False

        # Strong form 1: the stamp landed in the DB column.
        row = _get_job(engine, job.job_id)
        assert row is not None
        assert row.admission_state == "done"
        assert row.terminal_reason == "failed"
        assert row.failed_at is not None, (
            "failed_at must be stamped on the FAILED branch — pre-amendment "
            "this stayed NULL and atomic_retry rejected the row"
        )

        # Side-effects still happened (lock released, instance updated).
        assert _count_locks(engine, instance_id) == 0

        # Strong form 2: the retry acceptance contract — the strongest
        # assertion possible per the amendment's intent ("rows must
        # be retryable", not just "column must be non-null"). Use
        # the REAL ``JobRepository.atomic_retry`` API (the one that
        # is broken pre-amendment).
        retry_repo = JobRepository(engine)
        retried = retry_repo.atomic_retry(
            job_id=job.job_id,
            max_retries=3,
            next_retry_at="2099-01-01T00:00:00+00:00",
        )
        assert retried is not None, (
            "atomic_retry must accept the failed-stamped row — this is "
            "the contract the amendment restores; pre-amendment this "
            "returns None because failed_at is NULL"
        )
        assert retried.admission_state == "queued"
        assert retried.retry_count == 1
        assert retried.failed_at is None
        assert retried.terminal_reason is None

    def test_completed_path_does_not_stamp_failed_at(
        self, engine: Engine
    ) -> None:
        """Completed finalize must NOT stamp failed_at — only the
        FAILED branch does (cancelled/completed are not retryable).

        Negative control for the stamp's specificity: protects
        against future regressions that stamp unconditionally.
        """
        instance_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        job = _seed_job(engine, instance_id=instance_id)
        _seed_lock(engine, instance_id=instance_id)

        observer = _make_observer(engine)
        result = observer._finalize_job_db_sync(
            job_id=job.job_id,
            instance_id=instance_id,
            terminal_status=InstanceStatus.COMPLETED.value,
            result_summary="unit-test result",
            error_message=None,
        )
        assert result.skip is False

        row = _get_job(engine, job.job_id)
        assert row is not None
        assert row.admission_state == "done"
        assert row.terminal_reason == "completed"
        assert row.failed_at is None, (
            "completed path must NOT stamp failed_at — only failed does"
        )


class TestFinalizeActiveToDoneStamp:
    """Site 3: ``JobRepository.finalize_active_to_done`` failed branch
    (``set_values["failed_at"] = now`` when ``terminal_reason == "failed"``).

    T1/T2 cover Site 1 (``_finalize_job_db_sync``); this pins the
    repository-level finalize path with the same acceptance contract.
    """

    def test_finalize_active_to_done_failed_branch_stamps_and_row_is_retryable(
        self, engine: Engine
    ) -> None:
        """``terminal_reason='failed'`` finalize stamps failed_at and the
        row is accepted by the real ``atomic_retry`` API — mirroring T1's
        assertions through the Site-3 path."""
        instance_id = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        job = _seed_job(engine, instance_id=instance_id)  # admission_state='active'

        finalized = JobRepository(engine).finalize_active_to_done(
            job_id=job.job_id,
            derived_status="failed",
            terminal_reason="failed",
        )
        assert finalized is not None

        row = _get_job(engine, job.job_id)
        assert row is not None
        assert row.admission_state == "done"
        assert row.terminal_reason == "failed"
        assert row.failed_at is not None, (
            "Site 3: terminal_reason='failed' finalize must stamp failed_at"
        )

        retried = JobRepository(engine).atomic_retry(
            job_id=job.job_id,
            max_retries=3,
            next_retry_at="2099-01-01T00:00:00+00:00",
        )
        assert retried is not None
        assert retried.admission_state == "queued"
        assert retried.retry_count == 1
        assert retried.failed_at is None
        assert retried.terminal_reason is None
