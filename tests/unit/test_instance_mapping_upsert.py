"""Tests for C9 — atomic upsert in SQLModelSourceRepository.create_instance_mapping.

These tests cover:

1. ``_get_dialect_insert`` helper exists and returns the dialect-appropriate
   ``insert`` callable (SQLite → ``sqlite_dialect.insert``, mocked PG →
   ``pg_dialect.insert``).
2. ``UniqueConstraint`` is declared on the ``InstanceMapping`` model so
   fresh PostgreSQL databases created via ``SQLModel.metadata.create_all()``
   inherit it automatically.
3. Sequential upsert: calling ``create_instance_mapping`` twice with the
   same ``(source_id, external_user_id)`` but a different
   ``agent_instance_id`` yields exactly one row whose values reflect the
   second call (atomic upsert path).
4. Concurrent upsert: N threads race on the same ``(source_id,
   external_user_id)`` — the unique index plus ``ON CONFLICT DO UPDATE``
   guarantees a single row, with ``agent_instance_id`` matching one of the
   threads' values (no duplicate inserts).

The previous implementation used a SELECT-then-INSERT/UPDATE pattern which
produced duplicate mappings under concurrent first-message access from the
same external user (PostgreSQL concurrency audit TOP CRITICAL finding).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql as pg_dialect
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlmodel import Session, SQLModel, select

from daemon.repositories.source.models import InstanceMapping, SourceConfig
from daemon.repositories.source.repository import SQLModelSourceRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def shared_sqlite_engine(tmp_path):
    """SQLite engine safe for cross-thread use by SQLModelSourceRepository.

    Uses a default QueuePool (not StaticPool) so each thread checks out its
    own connection. SQLite still serializes writes via file-level locking
    which is exactly what makes the unique-index + ON CONFLICT upsert path
    atomic. ``check_same_thread=False`` lets SQLAlchemy hand the same file
    handle to whichever thread requests a connection next.
    """
    db_path = tmp_path / "instance_mappings.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def repo(shared_sqlite_engine):
    """SQLModelSourceRepository backed by the shared SQLite engine."""
    return SQLModelSourceRepository(shared_sqlite_engine)


@pytest.fixture
def seed_source(shared_sqlite_engine):
    """Insert a SourceConfig row to satisfy the FK on InstanceMapping.source_id."""
    with Session(shared_sqlite_engine) as session:
        source = SourceConfig(
            source_id="src-tg-1",
            source_type="telegram",
            name="Test Telegram Source",
            config={},
        )
        session.add(source)
        session.commit()
    return "src-tg-1"


# ---------------------------------------------------------------------------
# 1. Helper method
# ---------------------------------------------------------------------------


class TestGetDialectInsertHelper:
    """The source repository exposes a dialect-aware insert helper."""

    def test_helper_method_exists(self):
        assert hasattr(SQLModelSourceRepository, "_get_dialect_insert")
        assert callable(SQLModelSourceRepository._get_dialect_insert)

    def test_sqlite_session_returns_sqlite_insert(self, repo, shared_sqlite_engine):
        with Session(shared_sqlite_engine) as session:
            insert_fn = repo._get_dialect_insert(session)
        assert insert_fn is sqlite_dialect.insert

    def test_postgresql_session_returns_pg_insert(self):
        mock_session = MagicMock()
        mock_session.bind.dialect.name = "postgresql"
        repo = SQLModelSourceRepository(MagicMock())
        assert repo._get_dialect_insert(mock_session) is pg_dialect.insert

    def test_no_bind_defaults_to_sqlite_insert(self):
        mock_session = MagicMock()
        mock_session.bind = None
        repo = SQLModelSourceRepository(MagicMock())
        assert repo._get_dialect_insert(mock_session) is sqlite_dialect.insert


# ---------------------------------------------------------------------------
# 2. UniqueConstraint declared on the model
# ---------------------------------------------------------------------------


class TestUniqueConstraintOnModel:
    """The InstanceMapping model declares the unique constraint we depend on."""

    def test_unique_constraint_present(self):
        table = InstanceMapping.__table__
        constraint_names = {c.name for c in table.constraints if hasattr(c, "name")}
        assert "uq_instance_mappings_source_user" in constraint_names

    def test_unique_constraint_columns(self):
        table = InstanceMapping.__table__
        uq = next(
            c
            for c in table.constraints
            if getattr(c, "name", None) == "uq_instance_mappings_source_user"
        )
        col_names = {c.name for c in uq.columns}
        assert col_names == {"source_id", "external_user_id"}


# ---------------------------------------------------------------------------
# 3. Sequential upsert behavior
# ---------------------------------------------------------------------------


class TestCreateInstanceMappingUpsert:
    """Sequential upsert: two calls produce one row with the second call's values."""

    def test_first_call_inserts(self, repo, seed_source):
        result = repo.create_instance_mapping(
            source_id=seed_source,
            external_user_id="user-1",
            agent_instance_id="inst-A",
            agent_id="leader",
            agent_dir="/agents/leader",
            metadata={"first": True},
        )
        assert result.agent_instance_id == "inst-A"
        assert result.mapping_metadata == {"first": True}
        assert result.agent_id == "leader"
        assert result.agent_dir == "/agents/leader"

    def test_second_call_updates_same_row(self, repo, seed_source, shared_sqlite_engine):
        repo.create_instance_mapping(
            source_id=seed_source,
            external_user_id="user-1",
            agent_instance_id="inst-A",
            agent_id="leader",
            agent_dir="/agents/leader",
            metadata={"v": 1},
        )
        updated = repo.create_instance_mapping(
            source_id=seed_source,
            external_user_id="user-1",
            agent_instance_id="inst-B",
            agent_id="leader",
            agent_dir="/agents/leader",
            metadata={"v": 2},
        )
        # Upsert replaced the prior values, did not append.
        with Session(shared_sqlite_engine) as session:
            rows = session.exec(
                select(InstanceMapping).where(
                    InstanceMapping.source_id == seed_source,
                    InstanceMapping.external_user_id == "user-1",
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].agent_instance_id == "inst-B"
        assert rows[0].mapping_metadata == {"v": 2}
        assert updated.agent_instance_id == "inst-B"

    def test_distinct_external_users_create_distinct_rows(self, repo, seed_source, shared_sqlite_engine):
        repo.create_instance_mapping(
            source_id=seed_source, external_user_id="user-1",
            agent_instance_id="inst-A", agent_id="leader", agent_dir="/dir",
        )
        repo.create_instance_mapping(
            source_id=seed_source, external_user_id="user-2",
            agent_instance_id="inst-B", agent_id="leader", agent_dir="/dir",
        )
        with Session(shared_sqlite_engine) as session:
            rows = session.exec(
                select(InstanceMapping).where(InstanceMapping.source_id == seed_source)
            ).all()
        assert {r.external_user_id for r in rows} == {"user-1", "user-2"}


# ---------------------------------------------------------------------------
# 4. Concurrent upsert behavior — the actual C9 race condition
# ---------------------------------------------------------------------------


class TestConcurrentCreateInstanceMapping:
    """Multiple threads racing on the same (source_id, external_user_id) keep a single row."""

    def test_concurrent_threads_single_row(self, repo, seed_source, shared_sqlite_engine):
        """N threads race on the same key — exactly one row must remain."""
        n_threads = 8
        barrier = threading.Barrier(n_threads)

        def worker(i: int) -> str:
            # All threads block at the barrier, then attempt the upsert together.
            # SQLite serializes writes via file-level locking; the unique index
            # plus ON CONFLICT DO UPDATE guarantees a single row regardless.
            barrier.wait()
            row = repo.create_instance_mapping(
                source_id=seed_source,
                external_user_id="user-race",
                agent_instance_id=f"inst-{i}",
                agent_id="leader",
                agent_dir="/agents/leader",
                metadata={"thread": i},
            )
            return row.agent_instance_id

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(worker, i) for i in range(n_threads)]
            returned = [f.result() for f in as_completed(futures)]

        # Every thread returned a valid row id drawn from the worker set.
        assert all(r.startswith("inst-") for r in returned)

        # Exactly one row exists for (seed_source, "user-race").
        with Session(shared_sqlite_engine) as session:
            rows = session.exec(
                select(InstanceMapping).where(
                    InstanceMapping.source_id == seed_source,
                    InstanceMapping.external_user_id == "user-race",
                )
            ).all()

        assert len(rows) == 1, (
            f"Expected exactly one mapping row, got {len(rows)} — "
            "the atomic upsert did not prevent duplicates."
        )
        # The surviving row's agent_instance_id is one of the threads' inputs.
        assert rows[0].agent_instance_id in {f"inst-{i}" for i in range(n_threads)}
        # last_message_at was set by the upsert path.
        assert rows[0].last_message_at is not None

    def test_concurrent_distinct_keys_keep_all_rows(self, repo, seed_source, shared_sqlite_engine):
        """Threads with distinct (source_id, external_user_id) must produce N rows."""
        n_threads = 6
        barrier = threading.Barrier(n_threads)

        def worker(i: int):
            barrier.wait()
            repo.create_instance_mapping(
                source_id=seed_source,
                external_user_id=f"user-{i}",
                agent_instance_id=f"inst-{i}",
                agent_id="leader",
                agent_dir="/agents/leader",
            )

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            list(pool.map(worker, range(n_threads)))

        with Session(shared_sqlite_engine) as session:
            rows = session.exec(
                select(InstanceMapping).where(InstanceMapping.source_id == seed_source)
            ).all()
        assert len(rows) == n_threads
        assert {r.external_user_id for r in rows} == {f"user-{i}" for i in range(n_threads)}
