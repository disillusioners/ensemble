"""Unit tests for concurrent ``SharedMetaKVRepository.set_many`` access.

The repository uses dialect-aware ``INSERT ... ON CONFLICT DO UPDATE``
to make overlapping writes race-free: every concurrent upsert on the
same ``(context_key, meta_key)`` composite key is serialized by the
unique constraint and the loser observes the winner's value rather
than triggering a unique-violation error.

These tests pin the contract from outside — they exercise the public
surface (``set_many`` / ``get_all_as_dict`` / ``get_all``) from
multiple threads simultaneously and assert:

* no ``IntegrityError`` is raised under any interleaving,
* the final state is consistent (no torn writes, no missing rows),
* concurrent reads do not race with concurrent writes.

The shared engine uses ``StaticPool`` (per the project's standard
test pattern) so the in-memory SQLite database is shared across
threads — same setup as ``tests/unit/test_shared_meta_kv_repo.py``.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from daemon.repositories.shared_meta_kv.models import SharedMetaKV
from daemon.repositories.shared_meta_kv.repository import (
    SharedMetaKVRepository,
)


# ─── Fixtures (mirror test_shared_meta_kv_repo.py) ────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine with the ``shared_meta_kv`` table.

    Uses ``StaticPool`` so the in-memory database is shared across
    threads — required for the concurrent tests below. SQLAlchemy's
    ``check_same_thread=False`` plus a single connection in the pool
    means every ``Session()`` block reuses the same underlying
    connection, which is safe under SQLite's coarse-grained writer
    lock for this test surface.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _ = SharedMetaKV
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repo(engine):
    """A :class:`SharedMetaKVRepository` bound to the test engine."""
    return SharedMetaKVRepository(engine)


@pytest.fixture
def context_key() -> str:
    """Default ``context_key`` used by the concurrent tests."""
    return "ctx-concurrent"


# ─── Concurrent writes ─────────────────────────────────────────────────────────


class TestConcurrentSetMany:
    """Race-free upserts under overlapping-key concurrency."""

    def test_set_many_concurrent_overlapping_keys_no_integrity_error(
        self, repo, context_key
    ):
        """2-5 concurrent ``set_many`` calls with overlapping keys complete cleanly.

        Each call writes a distinct ``meta_key`` but several calls share
        keys with each other (e.g. ``shared_a``). The dialect-aware
        ``INSERT ... ON CONFLICT DO UPDATE`` must serialize every pair
        on the ``(context_key, meta_key)`` composite unique constraint
        — no ``IntegrityError`` may surface to the caller.

        The test fires up to 5 writers in parallel and verifies all
        futures complete successfully, then checks the final state
        contains every key from the union of all writes.
        """
        # Each writer's payload shares "shared_a" with the others,
        # so concurrent upserts target the same row.
        payloads = [
            {"writer": 1, "shared_a": "from-1"},
            {"writer": 2, "shared_a": "from-2"},
            {"writer": 3, "shared_a": "from-3"},
            {"writer": 4, "shared_a": "from-4", "extra_4": "v4"},
            {"writer": 5, "shared_a": "from-5", "extra_5": "v5"},
        ]

        # Barrier so threads fire roughly simultaneously.
        barrier = threading.Barrier(len(payloads))

        def _worker(payload: dict) -> int:
            barrier.wait()
            rows = repo.set_many(context_key, payload)
            return len(rows)

        with ThreadPoolExecutor(max_workers=len(payloads)) as ex:
            futures = [ex.submit(_worker, p) for p in payloads]
            results = [f.result() for f in as_completed(futures)]

        # No exception propagated — every writer succeeded.
        assert all(r > 0 for r in results)

        # Final state must contain every key from the union of payloads.
        snapshot = repo.get_all_as_dict(context_key)
        expected_keys = set()
        for p in payloads:
            expected_keys.update(p.keys())
        assert set(snapshot.keys()) == expected_keys

        # The contested key ``shared_a`` is one of the five values.
        assert snapshot["shared_a"] in {f"from-{i}" for i in range(1, 6)}
        # Non-contested exclusive keys round-tripped.
        assert snapshot.get("extra_4") == "v4"
        assert snapshot.get("extra_5") == "v5"

    def test_set_many_concurrent_same_key_last_writer_wins(self, repo, context_key):
        """Concurrent upserts on the SAME key produce a consistent final state.

        All workers write to the same single ``meta_key``. The
        composite unique constraint serializes the writes so the
        final value is exactly one of the values the workers tried
        to write — not a torn or partial value, and not a missing
        row. The "last writer wins" terminology refers to whichever
        upsert commits last under SQLite's writer lock; the contract
        is just "exactly one value persists".
        """
        n_workers = 10
        values = [f"value-{i}" for i in range(n_workers)]
        barrier = threading.Barrier(n_workers)

        def _worker(v: str) -> None:
            barrier.wait()
            repo.set_many(context_key, {"hot_key": v})

        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futures = [ex.submit(_worker, v) for v in values]
            for f in as_completed(futures):
                f.result()  # surfaces any exception

        snapshot = repo.get_all_as_dict(context_key)
        # Exactly one row for the single key — no torn writes, no duplicates.
        assert list(snapshot.keys()) == ["hot_key"]
        # The persisted value is exactly one of the values workers wrote
        # (no garbled concatenation, no partial strings).
        assert snapshot["hot_key"] in values

    def test_get_all_concurrent_with_writes(self, repo, context_key):
        """Concurrent reads and writes don't raise.

        Half the threads write ``set_many``; the other half read
        ``get_all_as_dict`` and ``get_all``. Both sides share the same
        engine (``StaticPool``); the contract is that no read
        surfaces an ``OperationalError`` or stale-session error
        during a concurrent write.
        """
        n_writers = 4
        n_readers = 4
        total = n_writers + n_readers
        barrier = threading.Barrier(total)

        write_errors: list[BaseException] = []
        read_errors: list[BaseException] = []

        def _writer(i: int) -> None:
            try:
                barrier.wait()
                for j in range(20):
                    repo.set_many(context_key, {f"k{i}_{j}": j})
            except BaseException as e:  # noqa: BLE001
                write_errors.append(e)

        def _reader() -> None:
            try:
                barrier.wait()
                for _ in range(20):
                    _ = repo.get_all_as_dict(context_key)
                    _ = repo.get_all(context_key)
            except BaseException as e:  # noqa: BLE001
                read_errors.append(e)

        with ThreadPoolExecutor(max_workers=total) as ex:
            futs = []
            for i in range(n_writers):
                futs.append(ex.submit(_writer, i))
            for _ in range(n_readers):
                futs.append(ex.submit(_reader))
            for f in as_completed(futs):
                f.result()

        assert write_errors == [], f"Writer failures: {write_errors}"
        assert read_errors == [], f"Reader failures: {read_errors}"

        # Every write committed: 20 keys per writer, 4 writers = 80 rows.
        snapshot = repo.get_all_as_dict(context_key)
        assert len(snapshot) == n_writers * 20