"""Tests for ``create_opencode_session_repository()`` table-creation semantics.

These tests verify the production factory function's three guarantees:

1. **Idempotency** — calling ``create_opencode_session_repository()``
   twice on the same engine is safe (uses ``checkfirst=True``).
2. **Isolation** — calling it must NOT create the 22+ ensemble tables
   that live in the global ``SQLModel.metadata`` registry.  Only
   ``opencode_sessions`` (and its index ``ix_opencode_sessions_id``)
   may exist on the engine afterwards.
3. **Functionality** — the returned repository can perform CRUD on
   the freshly-created table.

The test creates its own fresh ``sqlite:///:memory:`` engine rather
than reusing the ``sqlite_engine`` fixture, because the fixture is
itself pre-warmed via ``__table__.create()`` and would mask any
regression in the factory function's create-table path.
"""

import pytest
from sqlalchemy import create_engine, inspect

from daemon.opencode.repository import (
    OpenCodeSessionRecord,
    OpenCodeSessionRepository,
    create_opencode_session_repository,
)


def _fresh_engine():
    """Build a brand-new in-memory SQLite engine (no tables, no schema)."""
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────


class TestFactoryCreatesTable:
    """The factory must create the ``opencode_sessions`` table on an empty engine."""

    def test_returns_repository_instance(self):
        engine = _fresh_engine()
        repo = create_opencode_session_repository(engine)
        assert isinstance(repo, OpenCodeSessionRepository)

    def test_repository_uses_supplied_engine(self):
        engine = _fresh_engine()
        repo = create_opencode_session_repository(engine)
        assert repo.engine is engine

    def test_creates_opencode_sessions_table(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        assert "opencode_sessions" in inspector.get_table_names()

    def test_returns_usable_repository(self):
        """End-to-end smoke: factory → insert → read."""
        engine = _fresh_engine()
        repo = create_opencode_session_repository(engine)
        repo.create("p", "s", "id-1", "/path")
        record = repo.get("p", "s")
        assert record is not None
        assert record["id"] == "id-1"


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency
# ─────────────────────────────────────────────────────────────────────────────


class TestFactoryIsIdempotent:
    """Calling the factory twice on the same engine must not error."""

    def test_calling_twice_does_not_raise(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        # The second call uses checkfirst=True under the hood; it must
        # not raise even though the table already exists.
        create_opencode_session_repository(engine)

    def test_table_still_present_after_second_call(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        assert "opencode_sessions" in inspector.get_table_names()

    def test_data_persists_across_repeated_calls(self):
        """A row inserted between two factory calls must still be readable."""
        engine = _fresh_engine()
        repo1 = create_opencode_session_repository(engine)
        repo1.create("p", "s", "id-1", "/path")
        # Second factory call should not wipe the table.
        repo2 = create_opencode_session_repository(engine)
        record = repo2.get("p", "s")
        assert record is not None
        assert record["id"] == "id-1"


# ─────────────────────────────────────────────────────────────────────────────
# Isolation from the SQLModel.metadata registry
# ─────────────────────────────────────────────────────────────────────────────


class TestFactoryDoesNotLeakEnsembleTables:
    """The factory must use ``__table__.create()``, not ``metadata.create_all()``.

    Importing ``daemon.opencode.repository`` registers the opencode record
    in the global ``SQLModel.metadata`` registry, but that same registry
    also contains 22+ ensemble tables (instances, projects, jobs, …).  If
    the factory ever switched to ``SQLModel.metadata.create_all``, all
    those tables would materialise in the test engine — breaking the
    isolation promise.
    """

    def test_only_opencode_sessions_table_exists(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert tables == {"opencode_sessions"}

    def test_does_not_create_instances_table(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        assert "instances" not in inspector.get_table_names()

    def test_does_not_create_projects_table(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        assert "projects" not in inspector.get_table_names()

    def test_does_not_create_message_queue_table(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        assert "message_queue" not in inspector.get_table_names()

    def test_does_not_create_job_queue_table(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        assert "job_queue" not in inspector.get_table_names()

    def test_does_not_create_jobs_table(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        assert "jobs" not in inspector.get_table_names()


# ─────────────────────────────────────────────────────────────────────────────
# Index
# ─────────────────────────────────────────────────────────────────────────────


class TestFactoryCreatesIndex:
    """The ``ix_opencode_sessions_id`` index must exist after the factory call."""

    def test_index_exists_after_factory(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        index_names = {
            idx["name"] for idx in inspector.get_indexes("opencode_sessions")
        }
        assert "ix_opencode_sessions_id" in index_names

    def test_index_target_column_is_id(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        indexes = inspector.get_indexes("opencode_sessions")
        # Locate the opencode id index and assert it is on the ``id`` column.
        id_index = next(
            (idx for idx in indexes if idx["name"] == "ix_opencode_sessions_id"),
            None,
        )
        assert id_index is not None
        # The column descriptor may be "id" or a quoted form depending
        # on the dialect; accept either.
        assert any("id" in str(col).lower() for col in id_index["column_names"])

    def test_index_present_after_idempotent_call(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        index_names = {
            idx["name"] for idx in inspector.get_indexes("opencode_sessions")
        }
        assert "ix_opencode_sessions_id" in index_names


# ─────────────────────────────────────────────────────────────────────────────
# Column shape
# ─────────────────────────────────────────────────────────────────────────────


class TestFactoryCreatesExpectedColumns:
    """The factory-created table must have every column declared on the model."""

    def test_has_all_expected_columns(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("opencode_sessions")}
        expected = {
            "project",
            "session_name",
            "id",
            "working_dir",
            "last_agent",
            "is_agent_locked",
            "state",
            "latest_response",
            "questions",
            "last_activity",
        }
        assert expected.issubset(columns)

    def test_project_and_session_name_are_primary_key(self):
        engine = _fresh_engine()
        create_opencode_session_repository(engine)
        inspector = inspect(engine)
        pk = inspector.get_pk_constraint("opencode_sessions")
        # The composite PK uses the (project, session_name) pair.
        assert "project" in pk["constrained_columns"]
        assert "session_name" in pk["constrained_columns"]
