"""stored_watches index parity tests — e8a61da3 tidier round (2026-10-11).

Compact mirror of ``tests/migration/test_drift_events_index_parity.py``
(the drift_events v0.18.0 regression guard), scaled to the
``stored_watches`` surface: a brand-new table with NO
``_ensure_postgres_columns`` block (PostgreSQL receives it via
``SQLModel.metadata.create_all`` — the snapshot-tables precedent), so
the dual-driver parity contract is TWO-way:

  (a) the ``StoredWatch`` model's ``__table_args__`` declarations —
      the canonical source for the PG ``create_all`` path, vs
  (b) the SQLite migration's ``CREATE [UNIQUE] INDEX`` statements —
      ``daemon/migrations/versions/20261011_000001_create_stored_watches.sql``.

Pins:

* every named index declared on one side exists on the other with the
  same ordered column list (this is the guard that would have caught
  the drift_events v0.18.0 gap);
* the first-store-wins fence name parity: the model's
  ``UniqueConstraint`` vs the migration's same-named UNIQUE INDEX
  (the hook's ``ON CONFLICT`` target);
* a fresh in-memory SQLite ``create_all`` emits exactly the named
  index set — and NOT the default-named ``ix_stored_watches_*``
  family the dropped ``index=True`` field flags used to mint;
* the migration's ``CREATE ... INDEX IF NOT EXISTS`` statements are
  idempotent (re-runnable against an already-created database).

Test markers::

    pytest tests/migration/test_stored_watches_index_parity.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# Importing ``StoredWatch`` is REQUIRED to register ``stored_watches``
# on ``SQLModel.metadata`` — without this the reflections below see an
# empty metadata and fail. Same convention as the drift_events parity
# test (import every model module the assertions reflect on).
from daemon.repositories.job_queue.stored_watch_models import (  # noqa: F401
    StoredWatch,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_SQL = (
    _REPO_ROOT
    / "daemon/migrations/versions/20261011_000001_create_stored_watches.sql"
)

# Canonical index set — any change MUST be reflected in BOTH:
#   - daemon/repositories/job_queue/stored_watch_models.py
#     (``StoredWatch.__table_args__``, the canonical source)
#   - daemon/migrations/versions/20261011_000001_create_stored_watches.sql
EXPECTED_INDEXES: dict[str, tuple[str, ...]] = {
    "idx_stored_watches_original_work_id": ("original_work_id",),
    "idx_stored_watches_retry_work_id": ("retry_work_id",),
    "idx_stored_watches_watcher_instance_id": ("watcher_instance_id",),
    "idx_stored_watches_status_created": ("status", "created_at"),
}

# First-store-wins fence (the in-txn hook's ON CONFLICT target).
EXPECTED_UQ_NAME = "uq_stored_watches_original_work_watcher"
EXPECTED_UQ_COLUMNS = ("original_work_id", "watcher_instance_id")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (same shape as the drift_events parity test, minus the
# ensure-block helpers stored_watches deliberately does not have).
# ─────────────────────────────────────────────────────────────────────────────


def _migration_up_section() -> str:
    content = _MIGRATION_SQL.read_text(encoding="utf-8")
    match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)
    assert match, f"failed to parse UP section in {_MIGRATION_SQL}"
    return match.group(1)


def _migration_indexes() -> dict[str, tuple[str, ...]]:
    """name -> ordered columns for the migration's non-unique CREATE INDEX."""
    pattern = re.compile(
        r"CREATE INDEX IF NOT EXISTS (\w+)\s+ON\s+stored_watches\s*\(([^)]+)\)"
    )
    return {
        m.group(1): tuple(c.strip() for c in m.group(2).split(","))
        for m in pattern.finditer(_migration_up_section())
    }


def _migration_unique_index() -> tuple[str, tuple[str, ...]]:
    pattern = re.compile(
        r"CREATE UNIQUE INDEX IF NOT EXISTS (\w+)\s+ON"
        r"\s+stored_watches\s*\(([^)]+)\)"
    )
    match = pattern.search(_migration_up_section())
    assert match, "migration must declare the first-store-wins UNIQUE INDEX"
    return match.group(1), tuple(
        c.strip() for c in match.group(2).split(",")
    )


def _model_indexes() -> dict[str, tuple[str, ...]]:
    table = SQLModel.metadata.tables["stored_watches"]
    return {
        i.name: tuple(c.name for c in i.columns) for i in table.indexes
    }


def _model_unique_constraints() -> dict[str, tuple[str, ...]]:
    table = SQLModel.metadata.tables["stored_watches"]
    return {
        c.name: tuple(col.name for col in c.columns)
        for c in table.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }


@pytest.fixture(scope="module")
def fresh_sqlite_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


class TestStoredWatchesIndexParity:
    def test_migration_declares_expected_indexes(self):
        assert _migration_indexes() == EXPECTED_INDEXES

    def test_model_declares_expected_indexes(self):
        assert _model_indexes() == EXPECTED_INDEXES

    def test_migration_unique_index_matches_fence(self):
        name, columns = _migration_unique_index()
        assert name == EXPECTED_UQ_NAME
        assert columns == EXPECTED_UQ_COLUMNS

    def test_model_unique_constraint_matches_fence(self):
        constraints = _model_unique_constraints()
        assert EXPECTED_UQ_NAME in constraints
        assert constraints[EXPECTED_UQ_NAME] == EXPECTED_UQ_COLUMNS

    def test_fresh_create_all_emits_named_indexes_not_ix_family(
        self, fresh_sqlite_engine
    ):
        names = {
            i["name"]
            for i in inspect(fresh_sqlite_engine).get_indexes(
                "stored_watches"
            )
        }
        assert set(EXPECTED_INDEXES) <= names
        assert not any(
            n.startswith("ix_stored_watches_") for n in names
        ), (
            "default-named index=True indexes reappeared — the explicit "
            "__table_args__ declarations are the parity contract"
        )

    def test_migration_index_statements_idempotent(
        self, fresh_sqlite_engine
    ):
        statements = [
            s.strip()
            for s in _migration_up_section().split(";")
            if "CREATE" in s.upper() and "INDEX" in s.upper()
        ]
        assert len(statements) == len(EXPECTED_INDEXES) + 1  # + the UNIQUE
        with fresh_sqlite_engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))  # must not raise
