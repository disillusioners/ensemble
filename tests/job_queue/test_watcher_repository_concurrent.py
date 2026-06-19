"""Concurrent add_watch tests (H13).

Regression tests for the H13 race in JobWatcherRepository.add_watch:
N concurrent callers for the same (job_id, instance_id) pair must
result in exactly ONE row in ``job_watchers``. The previous
SELECT-then-INSERT pattern could produce duplicates; the fix is a
UNIQUE constraint plus a dialect-aware INSERT ... ON CONFLICT
DO UPDATE upsert (mirrored on the ``set_metadata_record`` /
``create_instance_mapping`` gold templates).

Test technique mirrors ``tests/unit/test_instance_mapping_upsert.py``:
- file-based SQLite with a default ``QueuePool`` (not ``StaticPool``)
  so each thread checks out its own connection — ``StaticPool`` shares
  a single connection across threads and SQLite's per-connection
  parameter binding is not safe under N concurrent statements.
- ``threading.Barrier`` so all workers fire at the same instant.
- ``ThreadPoolExecutor`` for explicit, bounded concurrency.

Run with:
    pytest tests/job_queue/test_watcher_repository_concurrent.py -v
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel, select

from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def shared_sqlite_engine(tmp_path):
    """SQLite engine safe for cross-thread use by JobWatcherRepository.

    Uses a default QueuePool (not StaticPool) so each thread checks out
    its own connection. SQLite still serializes writes via file-level
    locking, which is exactly what makes the unique-index + ON CONFLICT
    upsert path atomic.
    """
    db_path = tmp_path / "job_watchers.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    JobWatcher.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def watcher_repo(shared_sqlite_engine):
    """Single JobWatcherRepository backed by the shared engine."""
    return JobWatcherRepository(shared_sqlite_engine)


@pytest.fixture
def event_lists_5():
    """Five distinct event-list subsets used in the mixed-events race."""
    return [
        ["completed"],
        ["failed"],
        ["completed", "failed"],
        ["in_progress"],
        ["completed", "failed", "cancelled"],
    ]


# ── Helpers ────────────────────────────────────────────────────────────────────


def _row_count(engine, job_id: str, instance_id: str) -> int:
    """Count rows in job_watchers for the given pair."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM job_watchers "
                "WHERE job_id = :job_id AND instance_id = :instance_id"
            ),
            {"job_id": job_id, "instance_id": instance_id},
        ).one()
        return int(row[0])


# ── Helper method ──────────────────────────────────────────────────────────────


class TestGetDialectInsertHelper:
    """JobWatcherRepository exposes a dialect-aware insert helper."""

    def test_helper_method_exists(self):
        assert hasattr(JobWatcherRepository, "_get_dialect_insert")
        assert callable(JobWatcherRepository._get_dialect_insert)

    def test_sqlite_session_returns_sqlite_insert(self, shared_sqlite_engine):
        from sqlalchemy.dialects import sqlite as sqlite_dialect

        with Session(shared_sqlite_engine) as session:
            insert_fn = JobWatcherRepository._get_dialect_insert(session)
        assert insert_fn is sqlite_dialect.insert

    def test_postgresql_session_returns_pg_insert(self):
        from unittest.mock import MagicMock

        from sqlalchemy.dialects import postgresql as pg_dialect

        mock_session = MagicMock()
        mock_session.bind.dialect.name = "postgresql"
        assert (
            JobWatcherRepository._get_dialect_insert(mock_session) is pg_dialect.insert
        )


# ── UniqueConstraint declared on the model ─────────────────────────────────────


class TestUniqueConstraintOnModel:
    """The JobWatcher model declares the unique constraint the upsert
    depends on (covers Part 1 of the H13 fix)."""

    def test_unique_constraint_present(self):
        table = JobWatcher.__table__
        constraint_names = {c.name for c in table.constraints if hasattr(c, "name")}
        assert "uq_job_watchers_job_instance" in constraint_names

    def test_unique_constraint_columns(self):
        table = JobWatcher.__table__
        uq = next(
            c
            for c in table.constraints
            if getattr(c, "name", None) == "uq_job_watchers_job_instance"
        )
        col_names = {c.name for c in uq.columns}
        assert col_names == {"job_id", "instance_id"}


# ── Sequential upsert ──────────────────────────────────────────────────────────


class TestSequentialAddWatch:
    """Sequential upsert: two calls produce one row with the second call's values."""

    def test_first_call_inserts(self, watcher_repo):
        watch = watcher_repo.add_watch("job-seq-1", "instance-seq-1")
        assert watch.job_id == "job-seq-1"
        assert watch.instance_id == "instance-seq-1"
        # Default events are all watchable events (terminal + in_progress).
        assert watch.watch_events == [
            "completed",
            "failed",
            "cancelled",
            "dead_letter",
            "in_progress",
        ]

    def test_second_call_updates_same_row(
        self, watcher_repo, shared_sqlite_engine
    ):
        watcher_repo.add_watch("job-seq-2", "instance-seq-2", ["completed"])
        updated = watcher_repo.add_watch(
            "job-seq-2", "instance-seq-2", ["failed"]
        )
        assert updated.watch_events == ["failed"]
        with Session(shared_sqlite_engine) as session:
            rows = session.exec(
                select(JobWatcher).where(
                    JobWatcher.job_id == "job-seq-2",
                    JobWatcher.instance_id == "instance-seq-2",
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].watch_events == ["failed"]

    def test_distinct_instances_create_distinct_rows(
        self, watcher_repo, shared_sqlite_engine
    ):
        watcher_repo.add_watch("job-shared", "instance-A")
        watcher_repo.add_watch("job-shared", "instance-B")
        with Session(shared_sqlite_engine) as session:
            rows = session.exec(
                select(JobWatcher).where(JobWatcher.job_id == "job-shared")
            ).all()
        assert {r.instance_id for r in rows} == {"instance-A", "instance-B"}


# ── Concurrent add_watch — the H13 race itself ────────────────────────────────


class TestConcurrentAddWatch:
    """N threads racing on the same (job_id, instance_id) must yield one row."""

    def test_concurrent_threads_same_pair_single_row(
        self, watcher_repo, shared_sqlite_engine
    ):
        """N threads race on the same (job_id, instance_id) — exactly one
        row must remain, and no thread may raise UNIQUE-constraint or
        "bad parameter" errors from the upsert path."""
        n_threads = 8
        job_id = "job-race"
        instance_id = "instance-race"
        barrier = threading.Barrier(n_threads)
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            barrier.wait()
            try:
                watcher_repo.add_watch(job_id, instance_id, ["completed"])
            except BaseException as exc:  # noqa: BLE001 — capture all for assertion
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker, i) for i in range(n_threads)]
            for f in as_completed(futures):
                f.result()  # re-raise worker exceptions here

        assert not errors, (
            f"add_watch raised under concurrency ({len(errors)} errors): "
            f"{errors[:3]}"
        )

        assert _row_count(shared_sqlite_engine, job_id, instance_id) == 1, (
            "Expected exactly one job_watchers row — the atomic upsert "
            "did not prevent duplicates."
        )

    def test_concurrent_threads_mixed_event_lists(
        self, watcher_repo, shared_sqlite_engine, event_lists_5
    ):
        """Mixed concurrent calls with different event subsets still
        collapse to one row; the surviving ``watch_events`` is one of the
        supplied subsets (last-writer-wins, matching the legacy UPDATE)."""
        n_threads = len(event_lists_5)
        job_id = "job-mixed"
        instance_id = "instance-mixed"
        barrier = threading.Barrier(n_threads)

        def worker(i: int) -> None:
            barrier.wait()
            watcher_repo.add_watch(job_id, instance_id, event_lists_5[i])

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            list(pool.map(worker, range(n_threads)))

        assert _row_count(shared_sqlite_engine, job_id, instance_id) == 1

        with Session(shared_sqlite_engine) as session:
            rows = session.exec(
                select(JobWatcher).where(
                    JobWatcher.job_id == job_id,
                    JobWatcher.instance_id == instance_id,
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].watch_events in event_lists_5

    def test_concurrent_threads_distinct_pairs_all_persist(
        self, watcher_repo, shared_sqlite_engine
    ):
        """Threads with DISTINCT (job_id, instance_id) pairs all persist —
        the upsert must not regress the happy path of N distinct callers
        creating N rows."""
        n_threads = 6
        barrier = threading.Barrier(n_threads)

        def worker(i: int) -> None:
            barrier.wait()
            watcher_repo.add_watch(
                f"job-distinct-{i}", f"instance-distinct-{i}"
            )

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            list(pool.map(worker, range(n_threads)))

        with shared_sqlite_engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM job_watchers")).one()[
                0
            ]
        assert total == n_threads

    def test_concurrent_threads_default_events_single_row(
        self, watcher_repo, shared_sqlite_engine
    ):
        """Concurrent calls with no event list (default) collapse to one
        row, and the row keeps the default ALL_WATCHABLE_EVENTS list."""
        n_threads = 5
        job_id = "job-default"
        instance_id = "instance-default"
        barrier = threading.Barrier(n_threads)

        def worker() -> None:
            barrier.wait()
            watcher_repo.add_watch(job_id, instance_id)

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            list(pool.map(lambda _: worker(), range(n_threads)))

        assert _row_count(shared_sqlite_engine, job_id, instance_id) == 1
        with Session(shared_sqlite_engine) as session:
            watch = session.exec(
                select(JobWatcher).where(
                    JobWatcher.job_id == job_id,
                    JobWatcher.instance_id == instance_id,
                )
            ).one()
        assert watch.watch_events == [
            "completed",
            "failed",
            "cancelled",
            "dead_letter",
            "in_progress",
        ]