"""Tests for the dialect-aware upsert helper in daemon.repositories.project.repository (Phase 1).

The helper `_get_dialect_insert(session)` returns the right
``insert()`` callable depending on the session's bound engine dialect.
For SQLite → returns ``sqlalchemy.dialects.sqlite.insert``.
For PostgreSQL → returns ``sqlalchemy.dialects.postgresql.insert``.

We verify:
- The helper exists on SQLModelProjectRepository
- For a SQLite-bound session → returns the sqlite insert callable
- For a mocked PostgreSQL-bound session → returns the pg insert callable
- The existing upsert flow (set_metadata_record) still works on SQLite
  (i.e. on_conflict_do_update runs without error)
"""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql as pg_dialect
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlmodel import Session, SQLModel

from daemon.repositories.project.models import ProjectMetadataRecord
from daemon.repositories.project.repository import SQLModelProjectRepository


class TestGetDialectInsertHelper:
    """Tests for SQLModelProjectRepository._get_dialect_insert()."""

    def test_helper_method_exists(self):
        """The repository exposes a _get_dialect_insert method."""
        assert hasattr(SQLModelProjectRepository, "_get_dialect_insert")
        assert callable(SQLModelProjectRepository._get_dialect_insert)

    def test_sqlite_session_returns_sqlite_insert(self, tmp_path):
        """For a SQLite session, returns sqlalchemy.dialects.sqlite.insert."""
        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(engine)
        try:
            repo = SQLModelProjectRepository(engine)
            with Session(engine) as session:
                insert_fn = repo._get_dialect_insert(session)

            assert insert_fn is sqlite_dialect.insert
        finally:
            engine.dispose()

    def test_postgresql_session_returns_pg_insert(self):
        """For a PostgreSQL session, returns sqlalchemy.dialects.postgresql.insert."""
        # We can't easily instantiate a real PG engine (no driver in tests),
        # so mock the session with a PG-flavored bind.
        mock_session = MagicMock()
        mock_session.bind.dialect.name = "postgresql"

        repo = SQLModelProjectRepository(MagicMock())  # engine not used here
        insert_fn = repo._get_dialect_insert(mock_session)

        assert insert_fn is pg_dialect.insert

    def test_no_bind_defaults_to_sqlite_insert(self):
        """If session.bind is None, defaults to sqlite insert (fallback)."""
        mock_session = MagicMock()
        mock_session.bind = None

        repo = SQLModelProjectRepository(MagicMock())
        insert_fn = repo._get_dialect_insert(mock_session)

        assert insert_fn is sqlite_dialect.insert

    def test_unknown_dialect_defaults_to_sqlite_insert(self):
        """Unknown dialect names (e.g. 'mysql') fall through to sqlite insert."""
        mock_session = MagicMock()
        mock_session.bind.dialect.name = "mysql"

        repo = SQLModelProjectRepository(MagicMock())
        insert_fn = repo._get_dialect_insert(mock_session)

        assert insert_fn is sqlite_dialect.insert


class TestSqliteUpsertPreserved:
    """Verify the existing SQLite upsert path still works with the helper."""

    def test_set_metadata_record_uses_sqlite_dialect(self, tmp_path):
        """set_metadata_record executes an SQLite-dialect on_conflict_do_update."""
        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(engine)
        try:
            repo = SQLModelProjectRepository(engine)

            # Insert a project first so FK is satisfied
            with Session(engine) as session:
                from daemon.repositories.project.models import Project
                project = Project(
                    project_id="p1",
                    name="Test Project",
                    project_type="general",
                )
                session.add(project)
                session.commit()

            # First upsert — should insert
            with Session(engine) as session:
                result = repo.set_metadata_record(session, "p1", "color", "blue")
                first_value = result.meta_value  # read inside session
                session.commit()
            assert first_value == "blue"

            # Second upsert with same key — should update
            with Session(engine) as session:
                result = repo.set_metadata_record(session, "p1", "color", "red")
                second_value = result.meta_value  # read inside session
                session.commit()
            assert second_value == "red"

            # Only one row should exist (the upsert replaced, not appended)
            with Session(engine) as session:
                records = repo.list_metadata_records(session, "p1")
            assert len(records) == 1
            assert records[0].meta_value == "red"
        finally:
            engine.dispose()

    def test_helper_callable_supports_on_conflict_do_update(self, tmp_path):
        """The returned insert callable must support on_conflict_do_update()."""
        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(engine)
        try:
            repo = SQLModelProjectRepository(engine)
            with Session(engine) as session:
                insert_fn = repo._get_dialect_insert(session)

            # Build a real statement via the helper and chain on_conflict_do_update
            stmt = insert_fn(ProjectMetadataRecord).values(
                project_id="p1", meta_key="k", meta_value="v",
                created_at="2026-01-01", updated_at="2026-01-01",
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "meta_key"],
                set_={"meta_value": "v2", "updated_at": "2026-01-02"},
            )
            # Just verify the statement object is built without error
            assert stmt is not None
        finally:
            engine.dispose()
