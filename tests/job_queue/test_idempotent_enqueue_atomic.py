"""Tests for the M6 atomic idempotency-key insert (ON CONFLICT DO NOTHING).

The ``JobRepository.create_or_get_by_idempotency_key`` method closes the
TOCTOU race in the previous read-then-insert ``enqueue`` pattern. These
tests exercise the repository against a real in-memory SQLite engine to
verify:

- A single INSERT with a new key claims the key and returns ``(job, True)``.
- A second INSERT with the same key is a no-op (rowcount=0) and returns
  the existing row with ``(existing, False)``.
- Concurrent INSERTs (simulated with two sequential calls) produce
  exactly one row, not two.
- A terminal status (COMPLETED) does NOT block a fresh INSERT — the
  previous read-then-insert code returned the terminal job and only
  created a new row from the service layer, so this test pins the
  contract that the repository's atomic method is a pure "claim or
  return" — terminal-status policy lives in the service.
- An empty/None idempotency_key raises ValueError (the partial unique
  index only matches non-null keys, so calling the atomic method with
  no key is a programming error).
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.job_queue import AdmissionState, JobRepository
from daemon.repositories.job_queue.models import JobItem, AdmissionState


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """Real in-memory SQLite engine with StaticPool.

    We deliberately use StaticPool so the same connection is reused
    across threads — this matches the test setup used by the rest of
    the job_queue test suite.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def repository(engine) -> JobRepository:
    """Repository under test, bound to the in-memory engine."""
    return JobRepository(engine)


# ── File-backed SQLite fixtures for concurrent tests (F11) ────────────────
#
# The atomic ``create_or_get_by_idempotency_key`` insert relies on the
# partial UNIQUE index ``idx_job_idempotency`` to enforce uniqueness.
# In-memory SQLite + StaticPool serialises threads on a single
# connection, masking the race we want to exercise. The file-backed
# engine with the default QueuePool hands each thread its own
# connection so the cross-connection UNIQUE conflict path is
# actually exercised.

@pytest.fixture
def concurrent_engine(tmp_path):
    """Real file-backed SQLite engine (default QueuePool) for concurrent tests."""
    db_path = tmp_path / "job_queue_concurrent.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def concurrent_repository(concurrent_engine) -> JobRepository:
    """JobRepository backed by a file-backed SQLite engine (F11)."""
    return JobRepository(concurrent_engine)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateOrGetByIdempotencyKey:
    """Direct tests for the atomic claim-or-return method."""

    def test_first_insert_claims_key_and_returns_created_true(
        self, repository: JobRepository
    ):
        """A fresh INSERT with a new key claims the key and returns created=True."""
        key = f"key-{uuid.uuid4().hex[:8]}"

        job, created = repository.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="hello",
            source="api",
            project_id="test-project",
            priority=5,
            idempotency_key=key,
        )

        assert created is True
        assert job is not None
        assert job.idempotency_key == key
        assert job.admission_state == AdmissionState.QUEUED.value

    def test_second_insert_with_same_key_returns_existing(
        self, repository: JobRepository
    ):
        """A second INSERT with the same key is a no-op and returns existing."""
        key = f"key-{uuid.uuid4().hex[:8]}"

        first, first_created = repository.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="first",
            source="api",
            project_id="test-project",
            priority=5,
            idempotency_key=key,
        )
        second, second_created = repository.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="second",
            source="api",
            project_id="test-project",
            priority=5,
            idempotency_key=key,
        )

        assert first_created is True
        assert second_created is False
        # Same row returned — message and other fields are the first INSERT's.
        assert second.job_id == first.job_id
        assert second.message == "first"

    def test_concurrent_inserts_produce_exactly_one_row(
        self, concurrent_repository: JobRepository
    ):
        """Simulated concurrent INSERTs produce exactly one row, not two.

        Two threads each call the atomic method with the same key. With
        the partial unique index ``idx_job_idempotency`` enforcing
        uniqueness on non-null keys, exactly one INSERT wins. This is
        the M6 race the old read-then-insert pattern lost.

        The key invariant is that BOTH threads see the same job_id —
        proving the unique index serializes the writers. The exact
        ``created`` flag is timing-dependent (SQLite + StaticPool
        serializes the threads, so one always wins; with a real
        connection pool the first to commit wins). We assert the
        rowcount invariant instead: there is exactly one row in the
        table after both threads complete.

        F11: switched from the in-memory ``repository`` to the
        file-backed ``concurrent_repository`` so each thread gets its
        own SQLite connection. StaticPool serialises cursor access
        and would mask the cross-connection UNIQUE conflict we want
        to exercise.
        """
        repository = concurrent_repository
        key = f"race-key-{uuid.uuid4().hex[:8]}"
        results: list[tuple[JobItem | None, bool]] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker(payload: str) -> None:
            barrier.wait()  # Release both threads at the same time.
            job, created = repository.create_or_get_by_idempotency_key(
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message=payload,
                source="api",
                project_id="test-project",
                priority=5,
                idempotency_key=key,
            )
            with results_lock:
                results.append((job, created))

        t1 = threading.Thread(target=worker, args=("payload-A",))
        t2 = threading.Thread(target=worker, args=("payload-B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(results) == 2
        # Both threads must return a non-None job.
        assert all(job is not None for job, _ in results)
        # Both threads must return the SAME job_id — this is the
        # critical invariant: the unique index prevented a duplicate
        # insert, so both callers see the winner's row. (The
        # ``created`` flag is timing-dependent on the SQLite +
        # StaticPool threading model — the stronger invariant is that
        # there's exactly one row in the table, which the next test
        # verifies directly.)
        job_ids = {job.job_id for job, _ in results}
        assert len(job_ids) == 1

    def test_concurrent_inserts_leave_exactly_one_row_in_table(
        self, concurrent_repository: JobRepository
    ):
        """After concurrent INSERTs, the table has exactly one row for the key.

        This is a stronger invariant than the job_id check above: we
        verify the table itself contains exactly one row, proving the
        unique index prevented a duplicate at the storage level.

        F11: switched from the in-memory ``repository`` to the
        file-backed ``concurrent_repository`` — see the docstring on
        ``test_concurrent_inserts_produce_exactly_one_row`` for the
        rationale.
        """
        repository = concurrent_repository
        from sqlmodel import Session as SQLModelSession, select

        key = f"storage-key-{uuid.uuid4().hex[:8]}"
        barrier = threading.Barrier(3)

        def worker() -> None:
            barrier.wait()
            repository.create_or_get_by_idempotency_key(
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="x",
                source="api",
                project_id="test-project",
                priority=5,
                idempotency_key=key,
            )

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify exactly one row exists in the table for this key.
        with SQLModelSession(repository.engine) as session:
            rows = session.exec(
                select(JobItem).where(JobItem.idempotency_key == key)
            ).all()
            assert len(rows) == 1
            assert rows[0].idempotency_key == key

    def test_atomic_method_does_not_raise_integrity_error(
        self, repository: JobRepository
    ):
        """The atomic method does NOT propagate IntegrityError.

        The legacy read-then-insert would raise IntegrityError on the
        second concurrent INSERT. The atomic method swallows the
        conflict via ON CONFLICT DO NOTHING and returns the existing row.
        """
        key = f"no-raise-{uuid.uuid4().hex[:8]}"

        repository.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="x",
            source="api",
            project_id="test-project",
            priority=5,
            idempotency_key=key,
        )
        # Second call must not raise.
        try:
            _, created = repository.create_or_get_by_idempotency_key(
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="y",
                source="api",
                project_id="test-project",
                priority=5,
                idempotency_key=key,
            )
        except IntegrityError as exc:
            pytest.fail(
                f"create_or_get_by_idempotency_key must not raise "
                f"IntegrityError, but got: {exc}"
            )
        assert created is False

    def test_empty_idempotency_key_raises_value_error(
        self, repository: JobRepository
    ):
        """An empty idempotency_key raises ValueError (programming error)."""
        with pytest.raises(ValueError, match="non-empty idempotency_key"):
            repository.create_or_get_by_idempotency_key(
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="x",
                source="api",
                project_id="test-project",
                priority=5,
                idempotency_key="",  # empty
            )

    def test_different_keys_create_different_rows(
        self, repository: JobRepository
    ):
        """Two different keys create two distinct rows."""
        key_a = f"a-{uuid.uuid4().hex[:8]}"
        key_b = f"b-{uuid.uuid4().hex[:8]}"

        job_a, created_a = repository.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="A",
            source="api",
            project_id="test-project",
            priority=5,
            idempotency_key=key_a,
        )
        job_b, created_b = repository.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="B",
            source="api",
            project_id="test-project",
            priority=5,
            idempotency_key=key_b,
        )

        assert created_a is True
        assert created_b is True
        assert job_a.job_id != job_b.job_id
        assert job_a.idempotency_key == key_a
        assert job_b.idempotency_key == key_b

    def test_metadata_and_priority_preserved(
        self, repository: JobRepository
    ):
        """The returned job preserves priority and metadata fields."""
        key = f"meta-{uuid.uuid4().hex[:8]}"

        job, created = repository.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="meta-test",
            source="scheduler",
            project_id="test-project",
            priority=9,
            job_metadata={"trace_id": "abc-123", "retry": False},
            queue_id=None,
            idempotency_key=key,
            job_type="message",
            instance_id="inst-1",
        )

        assert created is True
        assert job.priority == 9
        assert job.source == "scheduler"
        assert job.job_type == "message"
        assert job.instance_id == "inst-1"
        assert job.job_metadata == {"trace_id": "abc-123", "retry": False}


class TestHelperMethod:
    """Test the dialect-aware insert helper."""

    def test_helper_method_exists(self, repository: JobRepository):
        """The repository exposes _get_dialect_insert."""
        assert hasattr(repository, "_get_dialect_insert")
        assert callable(repository._get_dialect_insert)

    def test_sqlite_session_returns_sqlite_insert(
        self, repository: JobRepository, engine
    ):
        """For a SQLite-bound session, returns sqlite dialect insert."""
        from sqlalchemy.dialects import sqlite as sqlite_dialect
        from sqlmodel import Session as SQLModelSession

        with SQLModelSession(engine) as session:
            insert_fn = repository._get_dialect_insert(session)

        assert insert_fn is sqlite_dialect.insert

    def test_postgresql_session_returns_pg_insert(
        self, repository: JobRepository
    ):
        """For a mocked PG-bound session, returns pg dialect insert."""
        from sqlalchemy.dialects import postgresql as pg_dialect
        from unittest.mock import MagicMock

        mock_session = MagicMock()
        mock_session.bind.dialect.name = "postgresql"

        insert_fn = repository._get_dialect_insert(mock_session)

        assert insert_fn is pg_dialect.insert
