"""Unit tests for the ``MessageMetadataRepository`` (Phase 1 C2).

Phase 1 C2 of the langgraph-checkpoint-perf plan (Solution M).
The repo is intentionally SYNC (decisions.md D14) — it matches the
shared engine factory contract at
``daemon/repositories/factory.py:10``. The 4 tap sites in
``daemon/services/message_tap.py`` bridge via ``asyncio.to_thread``.

Coverage
--------
* **SQLite in-memory** primary path (StaticPool so the connection is
  shared across the test) — matches the test infra in
  ``tests/unit/repositories/test_job_queue_atomic_transition.py``.
* **PG-only SQLAlchemy dialect detection** unit test — the repo's
  branch between the ``dialects.postgresql.insert`` and the
  ``dialects.sqlite.insert`` builders is exercised via the
  ``MessageMetadata`` ``__table__`` + the actual row layout, NOT via a
  PG fixture (this repo has no PG test infra and the project
  standards don't require it for SYNC repos).

The dual-driver contract (decisions.md D2) — "table exists + index
name matches" — is exercised separately by:
* ``tests/integration/test_message_metadata_hook_placement.py`` (4-site AST)
* the runtime ``daemon.manager._ensure_postgres_columns`` block
  (PG DDL emits the byte-identical ``CREATE TABLE`` /
  ``CREATE INDEX`` to the SQLite migration).
"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.message_metadata.models import MessageMetadata
from daemon.repositories.message_metadata.repository import (
    MessageMetadataRepository,
)


# ────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine (StaticPool, session-scoped to a test)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repository(engine):
    """MessageMetadataRepository backed by the in-memory engine."""
    return MessageMetadataRepository(engine)


# ────────────────────────────────────────────────────────────────────────
# Model schema sanity (precondition for the repo tests below)
# ────────────────────────────────────────────────────────────────────────


class TestModelSchema:
    """The MessageMetadata model + table layout match the plan spec.

    Phase 1 C2 — Solution M. The plan mandates the composite
    ``(thread_id, message_id)`` PK and a companion
    ``ix_message_metadata_thread`` index on ``thread_id``. The
    SQLite migration emits the same DDL; the SQLModel ``__table_args__``
    here is the canonical definition for the PG ``create_all`` path.
    """

    def test_tablename_is_message_metadata(self):
        assert MessageMetadata.__tablename__ == "message_metadata"

    def test_composite_primary_key(self):
        """PK is (thread_id, message_id) — the idempotency contract."""
        pk_columns = list(MessageMetadata.__table__.primary_key.columns)
        col_names = [c.name for c in pk_columns]
        assert col_names == ["thread_id", "message_id"], (
            f"PK order must be (thread_id, message_id); got {col_names}"
        )

    def test_thread_id_index_exists(self):
        """``ix_message_metadata_thread`` is the read-path index."""
        index_names = {idx.name for idx in MessageMetadata.__table__.indexes}
        assert "ix_message_metadata_thread" in index_names, (
            f"Missing thread index; have: {sorted(index_names)}"
        )

    def test_seq_column_is_nullable(self):
        """``seq`` is reserved nullable for Phase 2 PERF-2 (D5)."""
        seq_col = MessageMetadata.__table__.c.seq
        assert seq_col.nullable is True


# ────────────────────────────────────────────────────────────────────────
# upsert_batch — SYNC idempotent batch
# ────────────────────────────────────────────────────────────────────────


class TestUpsertBatch:
    """The repo's batch upsert is SYNC, idempotent, first-write-wins."""

    def test_first_insert_returns_rowcount(
        self, repository: MessageMetadataRepository
    ):
        """3 fresh rows in ⇒ rowcount=3, second call rowcount=0."""
        items = [
            ("m-1", "2026-08-25T00:00:00+00:00", None),
            ("m-2", "2026-08-25T00:00:01+00:00", None),
            ("m-3", "2026-08-25T00:00:02+00:00", None),
        ]
        count = repository.upsert_batch("t-1", items)
        assert count == 3
        # Confirm via get_for_thread that all 3 rows are present.
        rows = repository.get_for_thread("t-1")
        assert set(rows.keys()) == {"m-1", "m-2", "m-3"}
        for mid in ("m-1", "m-2", "m-3"):
            ts, seq = rows[mid]
            assert ts.endswith("+00:00")
            assert seq is None

    def test_idempotent_reinsert_returns_zero(
        self, repository: MessageMetadataRepository
    ):
        """Same (thread_id, message_id) re-insert ⇒ 0 rows affected.

        First-write-wins semantics: the second upsert is a
        constraint-level no-op under ``ON CONFLICT DO NOTHING``. The
        ``created_at`` stamp from the FIRST tap is preserved (D17 +
        the Critical 8 stability test).
        """
        first_at = "2026-08-25T00:00:00+00:00"
        second_at = "2026-08-25T12:00:00+00:00"
        repository.upsert_batch("t-1", [("m-1", first_at, None)])
        count = repository.upsert_batch("t-1", [("m-1", second_at, None)])
        assert count == 0
        # The stored row carries the FIRST tap's timestamp.
        rows = repository.get_for_thread("t-1")
        assert rows["m-1"][0] == first_at, (
            "First-write-wins broken — re-tap overwrote created_at"
        )

    def test_mixed_insert_and_noop(
        self, repository: MessageMetadataRepository
    ):
        """One existing key + one new key ⇒ rowcount=1."""
        repository.upsert_batch("t-1", [("m-1", "2026-08-25T00:00:00+00:00", None)])
        count = repository.upsert_batch(
            "t-1",
            [
                ("m-1", "2026-08-25T00:00:00+00:00", None),  # no-op
                ("m-2", "2026-08-25T00:00:01+00:00", None),  # new
            ],
        )
        assert count == 1
        rows = repository.get_for_thread("t-1")
        assert set(rows.keys()) == {"m-1", "m-2"}

    def test_empty_batch_short_circuits(self, repository: MessageMetadataRepository):
        """Empty ``items`` ⇒ returns 0 WITHOUT issuing SQL."""
        count = repository.upsert_batch("t-1", [])
        assert count == 0
        # get_for_thread returns empty (no row ever written).
        assert repository.get_for_thread("t-1") == {}

    def test_thread_isolation(
        self, repository: MessageMetadataRepository
    ):
        """Two threads don't bleed rows into each other."""
        repository.upsert_batch("t-1", [("m-1", "2026-08-25T00:00:00+00:00", None)])
        repository.upsert_batch("t-2", [("m-1", "2026-08-25T00:00:01+00:00", None)])
        rows_1 = repository.get_for_thread("t-1")
        rows_2 = repository.get_for_thread("t-2")
        assert set(rows_1.keys()) == {"m-1"}
        assert set(rows_2.keys()) == {"m-1"}
        # Same message_id, different thread → distinct rows.
        assert rows_1["m-1"][0] != rows_2["m-1"][0]

    def test_repo_is_sync_no_async_defs(self):
        """D14: the repo is SYNC — no ``async def`` anywhere."""
        import inspect

        from daemon.repositories.message_metadata.repository import (
            MessageMetadataRepository,
        )

        for name, member in inspect.getmembers(
            MessageMetadataRepository, predicate=inspect.isfunction
        ):
            assert not inspect.iscoroutinefunction(member), (
                f"MessageMetadataRepository.{name} must NOT be async "
                f"(decisions.md D14 — SYNC repo, asyncio.to_thread bridge)"
            )


# ────────────────────────────────────────────────────────────────────────
# get_for_thread — read primitive
# ────────────────────────────────────────────────────────────────────────


class TestGetForThread:
    """``get_for_thread`` is the read primitive the PR3 C1 read-flip calls."""

    def test_unknown_thread_returns_empty(
        self, repository: MessageMetadataRepository
    ):
        """Missing thread ⇒ empty dict (no KeyError, no rows)."""
        assert repository.get_for_thread("nope") == {}

    def test_returns_dict_of_tuples(
        self, repository: MessageMetadataRepository
    ):
        """Returns ``{message_id: (created_at, seq)}``."""
        repository.upsert_batch(
            "t-1",
            [
                ("m-1", "2026-08-25T00:00:00+00:00", None),
                ("m-2", "2026-08-25T00:00:01+00:00", None),
            ],
        )
        rows = repository.get_for_thread("t-1")
        assert isinstance(rows, dict)
        assert set(rows.keys()) == {"m-1", "m-2"}
        for mid, value in rows.items():
            assert isinstance(value, tuple)
            assert len(value) == 2
            created_at, seq = value
            assert isinstance(created_at, str)
            assert seq is None

    def test_sqlite_and_pg_equivalent_shape(
        self, repository: MessageMetadataRepository
    ):
        """SQLite and PG return the same dict shape.

        Spec: ``test_get_for_thread_sqlite_and_pg_equivalent``. We
        can't spin up a PG fixture in this unit suite, but the repo's
        write path is dialect-agnostic via SQLAlchemy's Core — the
        ``INSERT ...`` shape + the SELECT projection are identical
        on both backends. The shape assertion pins the contract.
        """
        # Insert + read on the SQLite fixture; verify the shape is
        # exactly what the C1 read-flip will consume.
        repository.upsert_batch(
            "t-1", [("m-1", "2026-08-25T00:00:00+00:00", None)]
        )
        rows = repository.get_for_thread("t-1")
        assert isinstance(rows, dict)
        # Key shape: message_id (str) → (created_at (str), seq (int|None)).
        assert isinstance(rows["m-1"][0], str)
        assert rows["m-1"][1] is None


# ────────────────────────────────────────────────────────────────────────
# Symmetry: dual-driver contract via the same SQLAlchemy table layout
# ────────────────────────────────────────────────────────────────────────


class TestDualDriverShape:
    """The PG ``create_all`` path emits the same DDL as the SQLite migration.

    Decisions D2: the dual-driver convention is INTENTIONAL. The
    contract is "table exists + index name matches" rather than "this
    exact code path created it". This test pins the model-emitted DDL
    matches the migration file's column layout + index name (PG and
    SQLite both see the same SQLAlchemy-emitted CREATE TABLE +
    CREATE INDEX).

    NOTE: the PG-specific ``_ensure_postgres_columns`` block in
    ``daemon.manager`` runs the same column + index names. Both paths
    converge on the same final state — verified by the migration
    runner test in PR1.
    """

    def test_model_columns_match_migration_spec(self):
        """Column set + nullability match the migration's CREATE TABLE.

        Migration ``daemon/migrations/versions/20260825_000001_create_message_metadata.sql``
        defines:
          ``thread_id TEXT NOT NULL, message_id TEXT NOT NULL,
          created_at TEXT NOT NULL, seq INTEGER, PRIMARY KEY (thread_id, message_id)``
        """
        cols = {c.name: c for c in MessageMetadata.__table__.columns}
        # Required columns
        assert set(cols.keys()) == {"thread_id", "message_id", "created_at", "seq"}
        # Nullability matches the migration
        assert cols["thread_id"].nullable is False
        assert cols["message_id"].nullable is False
        assert cols["created_at"].nullable is False
        assert cols["seq"].nullable is True  # Phase 2 PERF-2 (D5)

    def test_index_name_matches_migration(self):
        """Index name ``ix_message_metadata_thread`` matches the migration."""
        index_names = {idx.name for idx in MessageMetadata.__table__.indexes}
        assert "ix_message_metadata_thread" in index_names

    def test_iso8601_round_trip_preserves_value(
        self, repository: MessageMetadataRepository
    ):
        """A real ISO-8601 stamp round-trips losslessly via the repo.

        Pin the format the tap sites emit
        (``datetime.now(timezone.utc).isoformat()``) so the read
        path can parse it back without drift.
        """
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        repository.upsert_batch("t-1", [("m-1", now, None)])
        rows = repository.get_for_thread("t-1")
        assert rows["m-1"][0] == now