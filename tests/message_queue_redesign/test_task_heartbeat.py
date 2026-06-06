"""Tests for the per-task liveness heartbeat (Option 1 of the
per-instance guard follow-up).

Covers:
- ``TaskRepository.update_heartbeat`` semantics (only RUNNING rows update)
- ``TaskRepository.backfill_heartbeats`` legacy-row handling
- ``TaskRepository.find_stale_running_tasks`` / ``find_cancellable_tasks``
  use ``COALESCE(last_heartbeat_at, started_at)`` — a live task with
  an old ``started_at`` but a fresh ``last_heartbeat_at`` is NOT stale
- ``TaskHeartbeat`` (in worker_pool.py) writes at the configured
  interval and stops cleanly
- End-to-end: a worker updating heartbeats prevents a 5-min-old task
  from being flagged; a worker that stops heartbeating causes the
  task to become stale.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text
from sqlalchemy.pool import NullPool

from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.worker_pool import TaskHeartbeat


# --------------------------------------------------------------------
# Repository-layer tests
# --------------------------------------------------------------------


@pytest.fixture
def repo_engine():
    """File-based SQLite with WAL + busy_timeout. Thread-safe for the
    threaded heartbeat test below. Separate from the in-memory fixture
    in conftest.py because NullPool + file-backed is the closest
    local approximation of Postgres MVCC for the concurrency test."""
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    engine = _create_test_engine(path)
    try:
        yield engine
    finally:
        engine.dispose()
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(path + ext)
            except FileNotFoundError:
                pass


def _create_test_engine(path):
    from sqlmodel import SQLModel

    engine = __import__("sqlalchemy").create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _):
        c = dbapi_connection.cursor()
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.close()

    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def repository(repo_engine):
    return TaskRepository(repo_engine)


def _make_running_task(repo, age_minutes: int = 0, instance_id: str = "inst") -> Task:
    """Create a PENDING task, claim it (sets started_at and
    last_heartbeat_at to now), and optionally backdate both columns
    to simulate a crashed worker."""
    t = repo.create(
        task_type=TaskType_value(),
        instance_id=instance_id,
        message_id=f"m-{instance_id}",
    )
    claimed = repo.claim_pending_task(worker_id="w1")
    assert claimed is not None
    assert claimed.id == t.id
    if age_minutes:
        stale = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        with repo.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET started_at = :s, last_heartbeat_at = :h "
                    "WHERE id = :id"
                ),
                {"s": stale, "h": stale, "id": t.id},
            )
    return repo.get(t.id)


def TaskType_value() -> str:
    from daemon.repositories.task.models import TaskType
    return TaskType.PROCESS_MESSAGE.value


class TestUpdateHeartbeat:
    def test_heartbeat_updates_running_task(self, repository):
        t = _make_running_task(repository)
        before = repository.get(t.id).last_heartbeat_at
        time.sleep(0.01)  # ensure datetime tick
        assert repository.update_heartbeat(t.id) is True
        after = repository.get(t.id).last_heartbeat_at
        assert after > before

    def test_heartbeat_rejects_completed_task(self, repository):
        t = _make_running_task(repository)
        repository.complete_task(t.id, {"ok": True})
        assert repository.update_heartbeat(t.id) is False

    def test_heartbeat_rejects_cancelled_task(self, repository):
        t = _make_running_task(repository)
        repository.cancel_task(t.id, reason="test")
        assert repository.update_heartbeat(t.id) is False

    def test_heartbeat_rejects_missing_task(self, repository):
        assert repository.update_heartbeat(99999) is False

    def test_heartbeat_atomic_under_concurrent_updates(self, repository):
        """Many threads calling update_heartbeat on the same task must
        not lose updates (each call writes a fresh now(), no lost
        write semantics because the column is just a timestamp)."""
        t = _make_running_task(repository)

        N = 20
        barrier = threading.Barrier(N)

        def beat():
            barrier.wait()
            return repository.update_heartbeat(t.id)

        with ThreadPoolExecutor(max_workers=N) as ex:
            results = [f.result() for f in as_completed([ex.submit(beat) for _ in range(N)])]
        # All beats succeed (task is RUNNING throughout)
        assert all(results), "Some heartbeats lost on a RUNNING task"
        # Final heartbeat is fresh (within last second)
        after = repository.get(t.id).last_heartbeat_at
        assert (datetime.now(timezone.utc) - after).total_seconds() < 5


class TestBackfillHeartbeats:
    def test_backfill_fills_legacy_running_rows(self, repository):
        """A RUNNING task with NULL last_heartbeat_at (legacy) gets
        backfilled from started_at, so the recovery service doesn't
        immediately flag it as stale after a deploy."""
        # Insert a task directly with status='running' but NULL
        # last_heartbeat_at (simulates a row that predates the column).
        with repository.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO task (task_type, instance_id, message_id, "
                    "status, worker_id, started_at, last_heartbeat_at, created_at, "
                    "retry_count, cancel_requested, retry_scheduled) "
                    "VALUES ('process_message', 'legacy-inst', 'legacy-msg', "
                    "'running', 'old-worker', :started, NULL, :created, 0, 0, 0)"
                ),
                {
                    "started": datetime.now(timezone.utc) - timedelta(minutes=2),
                    "created": datetime.now(timezone.utc) - timedelta(minutes=3),
                },
            )

        n = repository.backfill_heartbeats()
        assert n == 1

        with repository.engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT last_heartbeat_at, started_at FROM task "
                    "WHERE instance_id = 'legacy-inst'"
                )
            ).first()
        assert row.last_heartbeat_at is not None
        assert row.last_heartbeat_at == row.started_at

    def test_backfill_skips_completed_and_failed(self, repository):
        """Backfill only touches RUNNING rows. COMPLETED/FAILED rows
        are left alone (their started_at is historical, not a liveness
        signal)."""
        # One RUNNING with NULL heartbeat
        with repository.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO task (task_type, instance_id, message_id, "
                    "status, started_at, last_heartbeat_at, created_at, "
                    "retry_count, cancel_requested, retry_scheduled) "
                    "VALUES ('process_message', 'r', 'm', 'running', "
                    ":s, NULL, :c, 0, 0, 0)"
                ),
                {
                    "s": datetime.now(timezone.utc),
                    "c": datetime.now(timezone.utc),
                },
            )
            # One COMPLETED with NULL heartbeat
            conn.execute(
                text(
                    "INSERT INTO task (task_type, instance_id, message_id, "
                    "status, started_at, last_heartbeat_at, created_at, "
                    "retry_count, cancel_requested, retry_scheduled) "
                    "VALUES ('process_message', 'c', 'm', 'completed', "
                    ":s, NULL, :c, 0, 0, 0)"
                ),
                {
                    "s": datetime.now(timezone.utc) - timedelta(hours=1),
                    "c": datetime.now(timezone.utc) - timedelta(hours=1),
                },
            )
        n = repository.backfill_heartbeats()
        assert n == 1  # only the RUNNING row

    def test_backfill_returns_zero_when_no_legacy_rows(self, repository):
        # A fresh claim sets last_heartbeat_at — nothing to backfill
        _make_running_task(repository)
        assert repository.backfill_heartbeats() == 0


class TestRecoveryUsesHeartbeat:
    """The key invariant: a live task with old started_at but a fresh
    heartbeat must NOT be flagged as stale. A crashed task with a stale
    heartbeat must BE flagged."""

    def test_live_task_with_old_started_at_not_stale(self, repository):
        """Simulate a long-running task that has been heartbeating: old
        started_at, but freshly-updated last_heartbeat_at. The recovery
        threshold is 5 min, started_at is 30 min old, heartbeat is now."""
        t = _make_running_task(repository)
        # Backdate started_at to 30 min ago, but KEEP heartbeat fresh
        with repository.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE task SET started_at = :s WHERE id = :id"
                ),
                {
                    "s": datetime.now(timezone.utc) - timedelta(minutes=30),
                    "id": t.id,
                },
            )
        # Re-heartbeat to ensure freshness
        repository.update_heartbeat(t.id)

        # find_stale_running_tasks with threshold=5 min must NOT find it
        stale = repository.find_stale_running_tasks(threshold_minutes=5)
        stale_ids = {s.id for s in stale}
        assert t.id not in stale_ids

        # find_cancellable_tasks with threshold=5 min must NOT find it
        cancellable = repository.find_cancellable_tasks(threshold_minutes=5)
        cancellable_ids = {c.id for c in cancellable}
        assert t.id not in cancellable_ids

    def test_crashed_task_with_stale_heartbeat_is_stale(self, repository):
        """Simulate a crashed worker: both started_at AND
        last_heartbeat_at are old. Recovery should flag it."""
        t = _make_running_task(repository, age_minutes=30)
        # find_stale_running_tasks with threshold=5 min MUST find it
        stale = repository.find_stale_running_tasks(threshold_minutes=5)
        assert any(s.id == t.id for s in stale)

    def test_legacy_row_with_null_heartbeat_falls_back_to_started_at(self, repository):
        """A row inserted before the heartbeat column existed has
        last_heartbeat_at=NULL. The COALESCE falls back to started_at,
        so the recovery service behaves identically to the old code
        for these rows."""
        # Insert a legacy RUNNING row
        old_started = datetime.now(timezone.utc) - timedelta(minutes=30)
        with repository.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO task (task_type, instance_id, message_id, "
                    "status, started_at, last_heartbeat_at, created_at, "
                    "retry_count, cancel_requested, retry_scheduled) "
                    "VALUES ('process_message', 'legacy', 'm', 'running', "
                    ":s, NULL, :c, 0, 0, 0)"
                ),
                {"s": old_started, "c": old_started},
            )

        stale = repository.find_stale_running_tasks(threshold_minutes=5)
        # find_stale_running_tasks returns Task objects; filter by instance
        legacy = [s for s in stale if s.instance_id == "legacy"]
        assert len(legacy) == 1

    def test_freshly_claimed_task_not_stale(self, repository):
        """A task that was just claimed has both started_at and
        last_heartbeat_at at 'now'. It must never appear in the stale
        list, regardless of threshold."""
        t = _make_running_task(repository)  # age_minutes=0
        # Threshold of 0 minutes would still not flag a task whose
        # heartbeat is exactly at claim time (threshold < heartbeat
        # is False). The threshold of 1 minute is well within the
        # heartbeat's freshness.
        for threshold in (1, 5, 60):
            stale = repository.find_stale_running_tasks(threshold_minutes=threshold)
            assert t.id not in {s.id for s in stale}


# --------------------------------------------------------------------
# TaskHeartbeat class tests
# --------------------------------------------------------------------


class TestTaskHeartbeat:
    def test_heartbeat_thread_updates_current_task(self, repository):
        """A started heartbeat thread updates last_heartbeat_at for
        the current task at the configured interval."""
        t = _make_running_task(repository)
        hb = TaskHeartbeat(task_repo=repository, interval_seconds=0.1)
        hb.start()
        try:
            hb.set_task(t.id)
            time.sleep(0.35)  # ~3 beats at 100ms interval
            # Heartbeat should be at most ~100ms old
            last = repository.get(t.id).last_heartbeat_at
            age = (datetime.now(timezone.utc) - last).total_seconds()
            assert age < 1.0, f"heartbeat too stale: {age}s"
        finally:
            hb.stop()

    def test_heartbeat_stops_writing_when_idle(self, repository):
        """After set_task(None), the heartbeat thread does not write
        to any row. Verifies the worker doesn't accidentally heartbeat
        a previous task after completion."""
        t1 = _make_running_task(repository, instance_id="a")
        t2 = _make_running_task(repository, instance_id="b")

        hb = TaskHeartbeat(task_repo=repository, interval_seconds=0.1)
        hb.start()
        try:
            hb.set_task(t1.id)
            time.sleep(0.25)
            hb.set_task(None)
            time.sleep(0.25)

            # Capture heartbeats after the idle transition
            t1_hb = repository.get(t1.id).last_heartbeat_at
            time.sleep(0.25)
            t1_hb_later = repository.get(t1.id).last_heartbeat_at
            # t1's heartbeat should be frozen (no updates while idle)
            assert t1_hb == t1_hb_later
        finally:
            hb.stop()

    def test_heartbeat_eager_first_beat_on_set(self, repository):
        """set_task() does an immediate first beat, not a delayed one.
        This is important for the recovery service: between claim
        and the first interval tick, the heartbeat is already fresh."""
        t = _make_running_task(repository)
        # Backdate to make the difference visible
        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        with repository.engine.begin() as conn:
            conn.execute(
                text("UPDATE task SET last_heartbeat_at = :h WHERE id = :id"),
                {"h": stale, "id": t.id},
            )

        hb = TaskHeartbeat(task_repo=repository, interval_seconds=10.0)  # 10s — slow
        hb.start()
        try:
            hb.set_task(t.id)
            # Immediate: heartbeat should be refreshed now, not in 10s
            last = repository.get(t.id).last_heartbeat_at
            age = (datetime.now(timezone.utc) - last).total_seconds()
            assert age < 0.5, f"set_task() did not eager-beat: age={age}s"
        finally:
            hb.stop()

    def test_heartbeat_clears_on_set_none(self, repository):
        """set_task(None) prevents further writes even if a tick
        fires after."""
        t = _make_running_task(repository)
        hb = TaskHeartbeat(task_repo=repository, interval_seconds=0.1)
        hb.start()
        try:
            hb.set_task(t.id)
            time.sleep(0.15)
            hb.set_task(None)
            captured = repository.get(t.id).last_heartbeat_at
            time.sleep(0.3)  # several ticks would have fired
            later = repository.get(t.id).last_heartbeat_at
            assert captured == later
        finally:
            hb.stop()

    def test_heartbeat_starts_and_stops_cleanly(self, repository):
        """start() is idempotent and stop() joins the thread."""
        t = _make_running_task(repository)
        hb = TaskHeartbeat(task_repo=repository, interval_seconds=0.05)
        hb.start()
        hb.start()  # idempotent
        assert hb._thread is not None and hb._thread.is_alive()
        hb.stop()
        assert hb._thread is None
        # Second stop is a no-op
        hb.stop()


# --------------------------------------------------------------------
# End-to-end: worker keepalive vs no-keepalive
# --------------------------------------------------------------------


class TestEndToEndKeepalive:
    def test_long_running_task_with_keepalive_survives_threshold(self, repository):
        """A long-running task (10 min old) whose worker keeps
        heartbeating must NOT be flagged stale at the 5-min threshold.

        This is the key fix vs the Commit 2 regression: previously, a
        live task running >5 min would be false-positively flagged
        stale by the recovery service, force-cancel-retry would fire,
        and the original worker's result would be wasted.
        """
        # Simulate a task that started 10 min ago, with a worker
        # heartbeating every 100ms in the background.
        t = _make_running_task(repository, age_minutes=10)

        # Start a heartbeat thread that will keep refreshing
        hb = TaskHeartbeat(task_repo=repository, interval_seconds=0.1)
        hb.start()
        try:
            # Wait long enough for the stale threshold to be crossed
            # if we WERE using started_at. We're not, because the
            # heartbeat is being kept fresh.
            for _ in range(20):  # ~2 seconds
                hb.set_task(t.id)  # also eager-beats
                time.sleep(0.1)

            # Recovery at 5-min threshold must NOT find this task
            stale = repository.find_stale_running_tasks(threshold_minutes=5)
            assert t.id not in {s.id for s in stale}, (
                "Live task with active heartbeat was falsely flagged stale — "
                "this is the regression we are fixing"
            )
        finally:
            hb.stop()

    def test_task_without_keepalive_becomes_stale(self, repository):
        """A task whose worker has stopped heartbeating (crashed) is
        flagged stale within the threshold + heartbeat interval."""
        t = _make_running_task(repository)
        # Backdate the heartbeat to simulate "the worker died 6 min ago"
        with repository.engine.begin() as conn:
            conn.execute(
                text("UPDATE task SET last_heartbeat_at = :h WHERE id = :id"),
                {
                    "h": datetime.now(timezone.utc) - timedelta(minutes=6),
                    "id": t.id,
                },
            )
        # Recovery at 5-min threshold MUST find it
        stale = repository.find_stale_running_tasks(threshold_minutes=5)
        assert t.id in {s.id for s in stale}
