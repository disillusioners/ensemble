"""W1 observer-path pins: job-scoped lock release in the finalize observer.

Site 4 of the W1 class fix: ``JobFeedbackObserver._finalize_job_db_sync``
Step 3 released locks INSTANCE-scoped (``WHERE instance_id == ...``) —
finalizing job J of instance X deleted ALL of X's ``job_locks`` rows,
including a sibling concurrent job's lock or a post-revive successor's
lock. Converted to job-scoped (``WHERE job_id == job_id``, mirroring the
three ``repository.py`` sites on the same branch):

  * ``job_id is not None`` → release ONLY this job's own lock rows.
  * ``job_id is None`` (post-D13 MESSAGE path) → release nothing: the
    finalized virtual job cannot hold a lock of its own
    (``job_locks.job_id`` is non-nullable; virtual jobs never acquire
    per-queue locks), so the old instance-wide DELETE there could only
    ever delete OTHER jobs' locks. Crash-cleanup backstops:
    ``JobLockSweepService`` (terminal-job locks, ≤90s) + F5
    ``force_finalize_orphan`` (stuck-active jobs).

Pins (two-direction discipline — own lock released AND co-instance lock
survives):

  1. sibling lock survives an observer finalize (COMPLETED path);
  2. post-revive successor lock survives an observer finalize (same
     queue, second slot — both locks coexist pre-finalize);
  3. ``job_id=None`` releases nothing (locks keyed to other jobs'
     job_ids survive) — the new None-regime contract.

Harness mirrors ``tests/unit/services/test_observer_finalize_no_job.py``
(``JobFeedbackObserver.__new__`` + real ``WritePauseGuard`` + mocked
``DependencyBus`` singleton for the A9 gate) — the minimum surface
``_finalize_job_db_sync`` needs. Engine per the W1 convention:
file-backed SQLite under ``tmp_path`` + ``NullPool`` + WAL +
``busy_timeout=10000`` (no StaticPool).

Worktree regression proof: these pins FAIL at base b5100215
(instance-scoped release) on the survives-direction, PASS at HEAD.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.event import listens_for
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Register every model on ``SQLModel.metadata`` BEFORE ``create_all`` —
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem, JobLock
from daemon.services import job_feedback_observer as _observer_module
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.write_pause_guard import WritePauseGuard


# ─────────────────────────────────────────────────────────────────────
# Fixtures — file-backed SQLite engine (NullPool + WAL) + observer harness
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout)."""
    db_path = tmp_path / f"w1-observer-{uuid.uuid4().hex[:8]}.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def _wire_bus_mock():
    """Mock ``DependencyBus`` so the A9/TOCTOU gate passes (mirrors
    ``test_observer_finalize_no_job.py::_wire_bus_mock``)."""
    bus_mock = MagicMock()
    bus_mock.count_pending_for_target_sync = lambda _iid: 0
    set_dependency_bus(bus_mock)
    yield bus_mock
    set_dependency_bus(None)


def _make_observer(engine: Engine) -> JobFeedbackObserver:
    """Build the observer with the minimum surface
    ``_finalize_job_db_sync`` needs (mirrors
    ``test_observer_finalize_no_job.py::_make_observer``: reads
    ``self._instance_manager.engine`` / ``.write_guard`` /
    ``.is_write_paused`` and the bus-gate lambda)."""
    observer = JobFeedbackObserver.__new__(JobFeedbackObserver)
    observer._instance_manager = MagicMock()
    observer._instance_manager.engine = engine
    observer._instance_manager.write_guard = WritePauseGuard()
    observer._instance_manager.is_write_paused = False
    observer._bus_count_pending_for_target_sync = lambda _iid: 0
    return observer


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _seed_instance(
    engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    instance_id = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="test",
                agent_dir="/tmp",
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_message_job(
    engine,
    *,
    job_id: str | None = None,
    instance_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    project_id: str = "p1",
    queue_id: str = "q1",
) -> JobItem:
    job_id = job_id or f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        job = JobItem(
            job_id=job_id,
            agent_id="test",
            agent_dir="/tmp",
            message="m",
            source="api",
            project_id=project_id,
            queue_id=queue_id,
            priority=1,
            admission_state=admission_state,
            created_at=datetime.now(timezone.utc).isoformat(),
            instance_id=instance_id,
            job_type="message",
            retry_count=0,
            version=1,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
    return job


def _seed_lock(
    engine,
    *,
    instance_id: str,
    job_id: str,
    project_id: str = "p1",
    queue_id: str = "q1",
    lock_slot: int = 0,
) -> JobLock:
    with Session(engine) as session:
        lock = JobLock(
            lock_id=f"lock-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            queue_id=queue_id,
            job_id=job_id,
            instance_id=instance_id,
            lock_slot=lock_slot,
            acquired_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(lock)
        session.commit()
        session.refresh(lock)
    return lock


def _lock_count_for_job(engine, job_id: str) -> int:
    with engine.begin() as conn:
        return (
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM job_locks "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            ).scalar()
            or 0
        )


def _job_admission_state(engine, job_id: str) -> str:
    with engine.begin() as conn:
        return (
            conn.execute(
                text(
                    "SELECT admission_state FROM job_queue_items "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            ).scalar()
            or ""
        )


# ─────────────────────────────────────────────────────────────────────
# Pins
# ─────────────────────────────────────────────────────────────────────


class TestObserverPathJobScopedRelease:
    """``_finalize_job_db_sync`` Step 3 must release ONLY the finalized
    job's own locks; co-instance sibling/successor locks survive."""

    def test_sibling_lock_survives_observer_finalize(
        self, engine, _wire_bus_mock
    ):
        """COMPLETED path: finalizing J1 must delete J1's own lock and
        leave sibling J2's lock (different queue) intact."""
        observer = _make_observer(engine)
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q1",
        )
        sibling = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q2",
        )
        _seed_lock(engine, instance_id=instance_id, job_id=job.job_id,
                   queue_id="q1", lock_slot=0)
        _seed_lock(engine, instance_id=instance_id, job_id=sibling.job_id,
                   queue_id="q2", lock_slot=0)

        result = observer._finalize_job_db_sync(
            job_id=job.job_id,
            instance_id=instance_id,
            terminal_status=InstanceStatus.COMPLETED.value,
            result_summary="done",
            error_message=None,
        )

        # The finalize itself must not skip (bus gate / F14 / gates).
        assert result.skip is False, f"finalize skipped: {result!r}"

        # Direction 1: the finalized job's OWN lock is gone, and the
        # released-count itself pins the scoping (exactly 1).
        assert result.locks_released == 1, (
            "W1 observer: Step 3 must release exactly the finalized "
            "job's OWN lock (instance-scoped release deleted the "
            "sibling's too → 2)."
        )
        assert _lock_count_for_job(engine, job.job_id) == 0
        assert (
            _job_admission_state(engine, job.job_id)
            == AdmissionState.DONE.value
        ), "W1 observer: the finalized job must transition to done."

        # Direction 2: the sibling's lock SURVIVES, job still ACTIVE.
        assert _lock_count_for_job(engine, sibling.job_id) == 1, (
            "W1 observer: the sibling job's lock MUST survive the "
            "co-instance finalize."
        )
        assert (
            _job_admission_state(engine, sibling.job_id)
            == AdmissionState.ACTIVE.value
        ), "W1 observer: the sibling job must remain ACTIVE (untouched)."

    def test_successor_lock_survives_observer_finalize(
        self, engine, _wire_bus_mock
    ):
        """Post-revive successor shape: old job J1 finalized LATE while
        the successor J2 (same queue, second slot) is mid-flight —
        J2's lock must survive J1's finalize."""
        observer = _make_observer(engine)
        instance_id = _seed_instance(engine)
        old_job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q1",
        )
        successor = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q1",
        )
        _seed_lock(engine, instance_id=instance_id, job_id=old_job.job_id,
                   queue_id="q1", lock_slot=0)
        _seed_lock(engine, instance_id=instance_id, job_id=successor.job_id,
                   queue_id="q1", lock_slot=1)

        result = observer._finalize_job_db_sync(
            job_id=old_job.job_id,
            instance_id=instance_id,
            terminal_status=InstanceStatus.COMPLETED.value,
            result_summary="done",
            error_message=None,
        )

        assert result.skip is False, f"finalize skipped: {result!r}"
        assert result.locks_released == 1, (
            "W1 observer: only the old job's own lock may be released."
        )
        assert _lock_count_for_job(engine, old_job.job_id) == 0
        assert (
            _job_admission_state(engine, old_job.job_id)
            == AdmissionState.DONE.value
        )
        assert _lock_count_for_job(engine, successor.job_id) == 1, (
            "W1 observer: the successor's lock MUST survive the late "
            "finalize of the old job."
        )
        assert (
            _job_admission_state(engine, successor.job_id)
            == AdmissionState.ACTIVE.value
        )

    def test_none_job_id_releases_nothing(
        self, engine, _wire_bus_mock
    ):
        """``job_id=None`` (post-D13 MESSAGE path): the finalized
        virtual job cannot hold a lock of its own, so Step 3 must
        release NOTHING — locks keyed to OTHER jobs' job_ids survive.
        Pre-fix, the instance-wide DELETE removed them all (the W1
        defect class on the None regime)."""
        observer = _make_observer(engine)
        instance_id = _seed_instance(engine)
        other_job_a = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q2",
        )
        other_job_b = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q3",
        )
        _seed_lock(engine, instance_id=instance_id, job_id=other_job_a.job_id,
                   queue_id="q2", lock_slot=0)
        _seed_lock(engine, instance_id=instance_id, job_id=other_job_b.job_id,
                   queue_id="q3", lock_slot=0)

        result = observer._finalize_job_db_sync(
            job_id=None,
            instance_id=instance_id,
            terminal_status=InstanceStatus.COMPLETED.value,
            result_summary="message turn done",
            error_message=None,
        )

        assert result.skip is False, f"finalize skipped: {result!r}"
        # Step 2 still ran (the instance transition is critical).
        with Session(engine) as session:
            inst = session.get(Instance, instance_id)
            assert inst is not None
            assert inst.status == InstanceStatus.COMPLETED.value, (
                "Step 2 (instance transition) must still fire on the "
                "job_id=None path."
            )

        # Step 3 (job-scoped): nothing of this finalize's own to
        # release — the other jobs' locks survive.
        assert result.locks_released == 0, (
            "W1 observer None-path: the virtual job holds no lock of "
            "its own; instance-wide release here could only delete "
            "OTHER jobs' locks."
        )
        assert _lock_count_for_job(engine, other_job_a.job_id) == 1, (
            "W1 observer None-path: other-job lock A must survive."
        )
        assert _lock_count_for_job(engine, other_job_b.job_id) == 1, (
            "W1 observer None-path: other-job lock B must survive."
        )
