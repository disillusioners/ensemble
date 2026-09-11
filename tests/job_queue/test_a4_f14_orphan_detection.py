"""Tests for the A4 F14-gate orphan-detection fix (Batch A of WC wake/resilience).

A4 (Batch A, 2026-09-11): the F14 in-session pending-tasks gate
(``daemon/services/job_feedback_observer.py:_finalize_job_db_sync``)
previously parked the parent UNCONDITIONALLY when any PENDING task
existed for the instance — including orphaned rows that should have
been claimed but never were (P1 incident 2026-09-11: task 33231
born is_deferred=True via revival enqueue 11:48:46 → autopromote
flip 11:58:42 → STILL PENDING 12:32:14 → F14 parks the parent at
completion → "indefinite park").

The A4 fix partitions the PENDING set into three categories:

* **Live tasks** — recent enqueue (``created_at`` within the
  60-second orphan-age threshold). Orchestrator's deliberate
  opt-in; the parent must wait.
* **Deferred tasks** — ``is_deferred=True`` rows. Pattern (g)
  autopromote handles the defer lane; the parent must still wait
  for the cycle to resolve.
* **Orphans** — ``is_deferred=False`` + aged past 60s. The P1
  class: a row that should have been claimed but never was
  (notify lost, pool-busy, etc.). These are eligible NOW — the
  worker pool just has no signal.

Conservative A4 rule: park when ANY live or deferred task is
present (genuine work). When the ONLY PENDING rows are orphans,
dispatch ``worker_pool.notify_work()`` so the pool picks them up;
fall through to the JobItem UPDATE (the next completion attempt
sees the orphans claimed and proceeds cleanly).

Test surface (this file):

* **in_session_f14_pure_orphan_skips_park_and_notifies_pool** —
  the P1 incident shape: a single aged PENDING task with
  ``is_deferred=False`` → ``notify_work()`` fires once, the
  result is ``gate_deferred=False`` (the UPDATE proceeds).
* **in_session_f14_live_task_still_parks** — fresh PENDING task
  (recent created_at) → gate fires, result is
  ``gate_deferred=True`` (preserved behavior; A4 does not
  drive-by override healthy parents).
* **in_session_f14_mixed_live_and_orphan_still_parks** — a mix
  of recent live tasks and aged orphans → gate fires (live
  tasks win; the orphan notify is intentionally NOT dispatched
  here — the live claim cycle is the natural wake signal).
* **in_session_f14_deferred_only_task_still_parks** — a single
  ``is_deferred=True`` PENDING task → gate fires (deferred
  tasks are NOT orphans — Pattern (g) owns the defer lane).
* **in_session_f14_no_pending_tasks_falls_through** — empty
  task set → ``gate_deferred=False``, ``skip=False`` (no change
  to the healthy path).
* **in_session_f14_orphan_notify_failure_does_not_block** —
  ``notify_work()`` raising is caught; the gate still falls
  through (A3 sweep is the systemic backstop).
* **in_session_f14_orphan_signature_log_emitted** — the A4
  partition emits a WARNING log when only orphans are found, so
  the operator sees the orphan signature distinctly from the
  INFO live-task log.
* **census_stays_23_1_0** — the A4 fix is read-only at the
  JobItem level; the JobItem UPDATE is unchanged. Census stays.

Harness: file-backed SQLite per the recipe in
``tests/job_queue/test_observer_hardening_f13_f14_f15.py``. The
observer is real (against the test engine); the worker pool is a
``MagicMock`` shim attached via
``observer._instance_manager._worker_pool``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import TaskStatus
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.write_pause_guard import WritePauseGuard


# ─── Fixtures / helpers (mirror the F14 hardening file) ─────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite with all tables (per the F14 test fixture)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _insert_instance(
    engine: Engine,
    instance_id: str,
    project_id: str = "test-project",
    status: str = "running",
    agent_id: str = "developer",
) -> None:
    """Insert an Instance row directly via SQL."""
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status, :project_id,
                     :created_at, :updated_at, 1)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": project_id,
                "created_at": now,
                "updated_at": now,
            },
        )


def _insert_job_item(
    engine: Engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    admission_state: str = AdmissionState.ACTIVE.value,
    job_metadata: dict | None = None,
) -> None:
    """Insert a JobItem directly via SQL."""
    import json

    now = datetime.now(timezone.utc).isoformat()
    metadata_json = json.dumps(job_metadata or {})
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                     project_id, queue_id, priority, admission_state,
                     created_at, instance_id, job_type, retry_count,
                     metadata)
                VALUES
                    (:job_id, :agent_id, :agent_dir, :message, :source,
                     :project_id, :queue_id, :priority, :admission_state,
                     :created_at, :instance_id, :job_type, :retry_count,
                     :metadata)
                """
            ),
            {
                "job_id": job_id,
                "agent_id": "developer",
                "agent_dir": "agents/developer",
                "message": "hi",
                "source": "api",
                "project_id": project_id,
                "queue_id": None,
                "priority": 0,
                "admission_state": admission_state,
                "created_at": now,
                "instance_id": instance_id,
                "job_type": "task",
                "retry_count": 0,
                "metadata": metadata_json,
            },
        )


def _create_pending_task(
    engine: Engine,
    *,
    instance_id: str,
    created_at: datetime | None = None,
    is_deferred: bool = False,
) -> int:
    """Insert a PENDING ``task`` row directly via SQL and return its id.

    ``created_at`` defaults to NOW (fresh / "live"); the A4 tests
    backdate it past the 60s orphan-age threshold to seed the
    orphan class.
    """
    now = (created_at or datetime.now(timezone.utc))
    wid = f"wid-a4-{now.timestamp()}-{is_deferred}-{instance_id}"
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred)
                VALUES
                    (:task_type, :instance_id, :message_id, :status,
                     :retry_count, :created_at, :cancel_requested,
                     :retry_scheduled, :work_id, :is_deferred)
                """
            ),
            {
                "task_type": "process_message",
                "instance_id": instance_id,
                "message_id": None,
                "status": TaskStatus.PENDING.value,
                "retry_count": 0,
                "created_at": now,
                "cancel_requested": False,
                "retry_scheduled": False,
                "work_id": wid,
                "is_deferred": is_deferred,
            },
        )
        return result.lastrowid


def _make_observer_with_worker_pool(
    engine: Engine, worker_pool: MagicMock
) -> JobFeedbackObserver:
    """Build a real ``JobFeedbackObserver`` against the engine with a
    ``worker_pool`` shim wired through ``_instance_manager``."""
    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=MagicMock(),
        job_repo=MagicMock(spec=JobRepository),
        lock_repo=MagicMock(spec=LockRepository),
        project_repo=MagicMock(),
        instance_manager=MagicMock(),
    )
    observer._instance_manager.engine = engine
    observer._instance_manager.write_guard = WritePauseGuard()
    observer._instance_manager._worker_pool = worker_pool
    return observer


def _drive_finalize_with_orphan_shape(
    engine: Engine,
    *,
    worker_pool: MagicMock,
    pending_tasks: list[tuple[bool, datetime]],
    partition_return: tuple[int, int] | None = None,
) -> object:
    """Drive ``_finalize_job_db_sync`` with the given PENDING-task set.

    Each tuple is ``(is_deferred, created_at)``. The function seeds
    the rows, mocks the early F14 partition helper to bypass it
    (mirroring the F14 hardening file's pattern), and returns the
    ``_FinalizeJobResult`` so callers assert the gate decision.

    Pre-bypasses the EARLY F14 partition so we reach the
    in-session branch — the early helper uses its own session and
    runs BEFORE ``WriteGuardSession``; mocking it cleanly isolates
    the in-session A4 partition.

    ``partition_return`` defaults to ``(len(pending_tasks),
    len(orphans_in_pending_tasks))`` so the early check matches
    the seeded rows; callers can override to force the early
    branch into a specific shape (e.g., (0, 0) to short-circuit
    past the early gate entirely).
    """
    instance_id = "inst-a4-test"
    job_id = "job-a4-test"

    _insert_instance(engine, instance_id)
    _insert_job_item(
        engine,
        job_id=job_id,
        instance_id=instance_id,
        admission_state=AdmissionState.ACTIVE.value,
        job_metadata={"message_id": "msg-a4-test"},
    )
    for is_deferred, created_at in pending_tasks:
        _create_pending_task(
            engine,
            instance_id=instance_id,
            created_at=created_at,
            is_deferred=is_deferred,
        )

    # Compute the partition counts from the seeded rows so the
    # mocked helper matches the real data.
    #
    # By default we bypass the early F14 check by mocking the
    # partition to (0, 0) — the tests focus on the in-session
    # gate's behavior. Callers can pass ``partition_return`` to
    # force the early branch into a specific shape (e.g.,
    # ``(1, 1)`` to exercise the early gate's orphan-detection).
    now = datetime.now(timezone.utc)
    orphan_threshold = now - timedelta(seconds=60)
    total = len(pending_tasks)
    orphans = sum(
        1 for is_deferred, created_at in pending_tasks
        if (not is_deferred) and created_at < orphan_threshold
    )
    if partition_return is None:
        partition_return = (0, 0)  # bypass early gate by default

    observer = _make_observer_with_worker_pool(engine, worker_pool)
    observer._partition_pending_tasks_for_instance_sync = MagicMock(
        return_value=partition_return
    )

    bus_mock = MagicMock()
    bus_mock.count_pending_for_target_sync = MagicMock(return_value=0)
    bus_mock.count_pending_for_target = AsyncMock(return_value=0)
    bus_mock.had_parent_error = MagicMock(return_value=False)
    set_dependency_bus(bus_mock)
    try:
        result = observer._finalize_job_db_sync(
            job_id=job_id,
            instance_id=instance_id,
            terminal_status="completed",
            result_summary=None,
            error_message=None,
        )
        return result
    finally:
        set_dependency_bus(None)


# ─── A4 partition tests ──────────────────────────────────────────────────


class TestA4InSessionF14OrphanDetection:
    """A4: the F14 in-session gate partitions the PENDING set into
    live / deferred / orphan and behaves per the conservative rule.

    P1 incident shape (2026-09-11): a deferred PENDING task born
    via revival enqueue → autopromote flip → STILL PENDING at
    completion → F14 parks the parent indefinitely. A4's pure-
    orphan branch (only orphans present) fires
    ``notify_work()`` and falls through."""

    def test_in_session_f14_pure_orphan_skips_park_and_notifies_pool(
        self, engine, caplog
    ):
        """The P1 incident shape: a single aged PENDING task with
        ``is_deferred=False`` → ``notify_work()`` fires once, the
        result is ``gate_deferred=False`` (the UPDATE proceeds)."""
        worker_pool = MagicMock()
        now = datetime.now(timezone.utc)
        # Aged past 60s threshold AND is_deferred=False → orphan.
        aged_orphan = now - timedelta(seconds=120)

        with caplog.at_level(logging.WARNING):
            result = _drive_finalize_with_orphan_shape(
                engine,
                worker_pool=worker_pool,
                pending_tasks=[(False, aged_orphan)],
            )

        # The orphan branch falls through (no park).
        assert result.gate_deferred is False, (
            f"A4 pure-orphan branch must fall through with "
            f"gate_deferred=False; got gate_deferred="
            f"{result.gate_deferred!r}. Result: {result!r}"
        )
        assert result.skip is False, (
            f"A4 pure-orphan branch must NOT skip finalization; "
            f"got skip={result.skip!r}"
        )
        # The pool was notified — exactly once for the one orphan.
        assert worker_pool.notify_work.call_count == 1, (
            f"worker_pool.notify_work() must be called exactly once "
            f"for the one orphan; got call_count="
            f"{worker_pool.notify_work.call_count}"
        )
        # The orphan-signature log was emitted.
        orphan_logs = [
            r for r in caplog.records
            if "orphan" in r.getMessage().lower()
        ]
        assert orphan_logs, (
            "A4 orphan branch MUST emit a WARNING log naming the "
            "orphan signature so the operator sees the orphan "
            "distinctly from the INFO live-task log"
        )

    def test_in_session_f14_live_task_still_parks(self, engine):
        """Fresh PENDING task (recent created_at) → gate fires,
        result is ``gate_deferred=True`` (preserved behavior)."""
        worker_pool = MagicMock()
        now = datetime.now(timezone.utc)
        # Recent — within the 60s threshold → live.
        recent_live = now - timedelta(seconds=10)

        result = _drive_finalize_with_orphan_shape(
            engine,
            worker_pool=worker_pool,
            pending_tasks=[(False, recent_live)],
        )

        assert result.gate_deferred is True, (
            f"Live task MUST trigger the park (A4 is "
            f"conservative on live work); got gate_deferred="
            f"{result.gate_deferred!r}"
        )
        assert result.skip is True, (
            "Live task MUST skip finalization — A4 must not "
            "drive-by override healthy parents"
        )
        # The pool is NOT notified (live tasks don't need an extra
        # wake — the natural claim cycle is the wake).
        assert worker_pool.notify_work.call_count == 0, (
            f"Live task branch MUST NOT call notify_work (the live "
            f"claim cycle is the natural wake); got call_count="
            f"{worker_pool.notify_work.call_count}"
        )

    def test_in_session_f14_mixed_live_and_orphan_still_parks(
        self, engine
    ):
        """A mix of recent live tasks and aged orphans → park
        (live wins; the orphan notify is intentionally NOT
        dispatched here — the live claim cycle is the natural
        wake signal, and A3 sweep is the secondary backstop)."""
        worker_pool = MagicMock()
        now = datetime.now(timezone.utc)

        result = _drive_finalize_with_orphan_shape(
            engine,
            worker_pool=worker_pool,
            pending_tasks=[
                (False, now - timedelta(seconds=10)),   # live
                (False, now - timedelta(seconds=120)),  # orphan
            ],
        )

        assert result.gate_deferred is True, (
            "Mixed live + orphan MUST park — live wins, A4 is "
            "conservative on live work"
        )
        assert result.skip is True
        assert worker_pool.notify_work.call_count == 0, (
            "Mixed branch MUST NOT notify — the live task's claim "
            "cycle is the natural wake; A3 sweep is the secondary "
            "backstop for the orphan"
        )

    def test_in_session_f14_deferred_only_task_still_parks(
        self, engine
    ):
        """A single ``is_deferred=True`` PENDING task → park
        (deferred tasks are NOT orphans — Pattern (g) owns the
        defer lane; the parent must wait for the cycle)."""
        worker_pool = MagicMock()
        now = datetime.now(timezone.utc)

        result = _drive_finalize_with_orphan_shape(
            engine,
            worker_pool=worker_pool,
            pending_tasks=[(True, now - timedelta(seconds=120))],
        )

        assert result.gate_deferred is True, (
            "Deferred-only branch MUST park — Pattern (g) owns "
            "the defer lane, NOT A4"
        )
        assert result.skip is True
        assert worker_pool.notify_work.call_count == 0, (
            "Deferred-only branch MUST NOT notify — the deferred "
            "lane uses a different wake primitive (autopromote)"
        )

    def test_in_session_f14_no_pending_tasks_falls_through(
        self, engine
    ):
        """Empty task set → ``gate_deferred=False``,
        ``skip=False`` (no change to the healthy path)."""
        worker_pool = MagicMock()

        result = _drive_finalize_with_orphan_shape(
            engine,
            worker_pool=worker_pool,
            pending_tasks=[],
        )

        assert result.gate_deferred is False, (
            "Empty PENDING set MUST fall through — A4 does not "
            "regress the healthy path"
        )
        assert result.skip is False
        assert worker_pool.notify_work.call_count == 0

    def test_in_session_f14_orphan_notify_failure_does_not_block(
        self, engine, caplog
    ):
        """``notify_work()`` raising is caught; the gate still
        falls through (A3 sweep is the systemic backstop)."""
        worker_pool = MagicMock()
        worker_pool.notify_work.side_effect = RuntimeError("pool blip")
        now = datetime.now(timezone.utc)

        with caplog.at_level(logging.WARNING):
            result = _drive_finalize_with_orphan_shape(
                engine,
                worker_pool=worker_pool,
                pending_tasks=[(False, now - timedelta(seconds=120))],
            )

        # The orphan branch fell through despite the notify error.
        assert result.gate_deferred is False, (
            f"notify_work() raising MUST NOT block the orphan "
            f"branch's fall-through; got gate_deferred="
            f"{result.gate_deferred!r}"
        )
        assert result.skip is False

    def test_in_session_f14_orphan_signature_log_emitted(
        self, engine, caplog
    ):
        """The A4 partition emits a WARNING log when only orphans
        are found, so the operator sees the orphan signature
        distinctly from the INFO live-task log."""
        worker_pool = MagicMock()
        now = datetime.now(timezone.utc)

        with caplog.at_level(logging.DEBUG):
            _drive_finalize_with_orphan_shape(
                engine,
                worker_pool=worker_pool,
                pending_tasks=[(False, now - timedelta(seconds=120))],
            )

        # Look for the orphan WARNING signature — different level
        # from the live-task INFO log so observability can
        # distinguish.
        orphan_warning_logs = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "orphan" in r.getMessage().lower()
        ]
        assert orphan_warning_logs, (
            "A4 orphan branch MUST emit a WARNING log (distinct "
            "from the live-task INFO log) so observability "
            "distinguishes the orphan signature"
        )


# ─── Census static guard ────────────────────────────────────────────────


class TestA4ConstitutionStatic:
    """A4 fix is read-only at the JobItem level; the JobItem UPDATE
    is unchanged. Census stays at 23/1/0."""

    def test_census_stays_23_1_0(self):
        from daemon.job_state import constitution

        assert (
            len(constitution.KNOWN_ADMISSION_STATE_WRITERS) == 23
        ), (
            f"A4 introduced new admission_state writers — census "
            f"drifted from 23 to "
            f"{len(constitution.KNOWN_ADMISSION_STATE_WRITERS)}"
        )
        assert len(constitution.KNOWN_JOBITEM_CREATORS) == 1, (
            f"A4 introduced new JobItem creators — census drifted "
            f"from 1 to "
            f"{len(constitution.KNOWN_JOBITEM_CREATORS)}"
        )
        assert len(constitution.KNOWN_MINT_SITES) == 0, (
            f"A4 introduced new work_id mints — census drifted "
            f"from 0 to {len(constitution.KNOWN_MINT_SITES)}"
        )

    def test_a4_block_uses_canonical_seam(self):
        """The A4 partition block is wired with the canonical
        ``worker_pool`` seam (``instance_manager._worker_pool``) —
        the same primitive as A2 / A3 / A5."""
        from pathlib import Path

        prod_path = (
            Path(__file__).parent.parent.parent
            / "daemon"
            / "services"
            / "job_feedback_observer.py"
        )
        contents = prod_path.read_text()
        assert "is_deferred=False" in contents or "is_deferred.is_(False)" in contents, (
            "A4 partition MUST reference is_deferred explicitly "
            "(the orphan branch requires is_deferred=False)"
        )
        assert (
            "worker_pool.notify_work()" in contents
        ), (
            "A4 orphan-detection MUST use the canonical "
            "notify_work seam — same primitive as A2 / A3 / A5"
        )


# ─── Early F14 gate partition ─ ─ ───────────────────────────────────


class TestA4EarlyF14OrphanDetection:
    """A4 also applies to the EARLY F14 gate
    (``_partition_pending_tasks_for_instance_sync``-driven
    branch before the ``WriteGuardSession``). The early gate
    mirrors the in-session partition: pure-orphans dispatch
    ``notify_work()`` and fall through; live / deferred tasks
    still park.
    """

    def test_early_f14_pure_orphan_skips_park_and_notifies_pool(
        self, engine, caplog
    ):
        """The early F14 check also applies the A4 partition —
        pure orphans trigger ``notify_work()`` and fall through.

        Setup: early-gate forced into orphan shape via
        ``partition_return=(1, 1)``; in-session sees an empty
        PENDING set (``pending_tasks=[]``) so only the early gate
        fires ``notify_work()``. This isolates the early gate's
        behavior so the test pin is single-purpose."""
        worker_pool = MagicMock()

        with caplog.at_level(logging.WARNING):
            result = _drive_finalize_with_orphan_shape(
                engine,
                worker_pool=worker_pool,
                pending_tasks=[],  # in-session sees empty
                # Force the early gate into the orphan shape.
                partition_return=(1, 1),
            )

        # The early gate fell through (no park) and dispatched
        # notify_work ONCE.
        assert result.gate_deferred is False, (
            f"Early F14 pure-orphan branch must fall through with "
            f"gate_deferred=False; got gate_deferred="
            f"{result.gate_deferred!r}"
        )
        assert worker_pool.notify_work.call_count == 1, (
            f"Early F14 orphan branch MUST dispatch notify_work() "
            f"once; got call_count="
            f"{worker_pool.notify_work.call_count}"
        )

    def test_early_f14_live_task_still_parks(self, engine):
        """The early F14 check still parks when live tasks are
        present (A4 is conservative on live work)."""
        worker_pool = MagicMock()

        result = _drive_finalize_with_orphan_shape(
            engine,
            worker_pool=worker_pool,
            pending_tasks=[(False, datetime.now(timezone.utc)
                            - timedelta(seconds=10))],
            # Force the early gate into the live shape.
            partition_return=(1, 0),
        )

        assert result.gate_deferred is True, (
            "Live task at the early F14 check MUST park — A4 is "
            "conservative on live work"
        )
        assert worker_pool.notify_work.call_count == 0, (
            "Live task at the early F14 check MUST NOT trigger "
            "notify — the live claim cycle is the natural wake"
        )

    def test_partition_helper_returns_correct_tuple(self, engine):
        """The new ``_partition_pending_tasks_for_instance_sync``
        helper returns the (total, orphan) tuple for any seeded
        row shape — and fails open to (0, 0) on DB error."""
        observer = _make_observer_with_worker_pool(engine, MagicMock())
        instance_id = "inst-a4-partition"

        _insert_instance(engine, instance_id)
        now = datetime.now(timezone.utc)
        # Seed 4 rows: 1 live, 1 aged orphan, 1 deferred, 1 recent
        # non-defer live (within 60s threshold).
        _create_pending_task(
            engine, instance_id=instance_id,
            created_at=now - timedelta(seconds=10),
            is_deferred=False,
        )
        _create_pending_task(
            engine, instance_id=instance_id,
            created_at=now - timedelta(seconds=120),
            is_deferred=False,
        )
        _create_pending_task(
            engine, instance_id=instance_id,
            created_at=now - timedelta(seconds=120),
            is_deferred=True,
        )
        _create_pending_task(
            engine, instance_id=instance_id,
            created_at=now - timedelta(seconds=30),
            is_deferred=False,
        )

        total, orphans = (
            observer._partition_pending_tasks_for_instance_sync(
                instance_id
            )
        )
        assert total == 4, (
            f"Partition helper must count all 4 PENDING rows; "
            f"got total={total!r}"
        )
        assert orphans == 1, (
            f"Partition helper must count only the aged "
            f"is_deferred=False row as orphan; got orphans="
            f"{orphans!r}"
        )