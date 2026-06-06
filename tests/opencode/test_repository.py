"""Unit tests for ``daemon.opencode.repository.OpenCodeSessionRepository``.

Covers every public method on the repository plus ``OpenCodeSessionRecord``:

- ``create`` / ``get`` / ``list`` / ``find_by_id`` / ``delete``
- ``update_agent_state`` / ``update_state`` / ``update_last_activity`` /
  ``update_session_data``
- The ``ix_opencode_sessions_id`` index
- The error paths: ``KeyError`` for missing rows, ``IntegrityError`` on
  duplicate primary key

The tests run against an in-memory SQLite engine with **only** the
``opencode_sessions`` table — no ensemble tables leak in.  This
mirrors the production factory function
``create_opencode_session_repository()`` which also uses
``OpenCodeSessionRecord.__table__.create()``.

> **Note on duplicate-key behavior.**  The production ``create()``
> relies on SQLAlchemy's ``IntegrityError`` (a subclass of
> ``SQLAlchemyError``) for duplicate-key rejection — the Go port's
> docstring explicitly says "we rely on the SQLAlchemy IntegrityError
> for the same effect".  These tests assert the actual behaviour
> (``IntegrityError``) rather than the (incorrectly documented) value
> ``ValueError`` that the planning document sketched.
"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError


# ─────────────────────────────────────────────────────────────────────────────
# Create
# ─────────────────────────────────────────────────────────────────────────────


class TestCreate:
    """``OpenCodeSessionRepository.create()`` insert path."""

    def test_inserts_row_with_all_fields(self, repository):
        repository.create(
            project="myapp",
            session_name="feature-x",
            session_id="sess-uuid-1",
            working_dir="/path/to/work",
        )
        record = repository.get("myapp", "feature-x")
        assert record is not None
        assert record["project"] == "myapp"
        assert record["session_name"] == "feature-x"
        assert record["id"] == "sess-uuid-1"
        assert record["working_dir"] == "/path/to/work"

    def test_sets_last_activity_to_current_time(self, repository):
        repository.create("p", "s", "id", "/path")
        record = repository.get("p", "s")
        # Stored value should be a non-empty RFC3339 string.
        assert isinstance(record["last_activity"], str)
        assert record["last_activity"]

    def test_last_agent_defaults_to_empty_string(self, repository):
        repository.create("p", "s", "id", "/path")
        record = repository.get("p", "s")
        assert record["last_agent"] == ""

    def test_is_agent_locked_defaults_to_false(self, repository):
        repository.create("p", "s", "id", "/path")
        record = repository.get("p", "s")
        assert record["is_agent_locked"] is False

    def test_state_defaults_to_idle(self, repository):
        repository.create("p", "s", "id", "/path")
        record = repository.get("p", "s")
        assert record["state"] == "IDLE"

    def test_questions_default_to_empty_list(self, repository):
        repository.create("p", "s", "id", "/path")
        record = repository.get("p", "s")
        # ``to_dict`` always emits a list (never None) for the JSON column.
        assert record["questions"] == []

    def test_latest_response_defaults_to_none(self, repository):
        repository.create("p", "s", "id", "/path")
        record = repository.get("p", "s")
        assert record["latest_response"] is None

    def test_duplicate_primary_key_raises_integrity_error(self, repository):
        repository.create("myapp", "feature-x", "id-1", "/path")
        with pytest.raises(IntegrityError):
            repository.create("myapp", "feature-x", "id-2", "/path")

    def test_duplicate_pk_raises_integrity_error(self, repository):
        repository.create("myapp", "feature-x", "same-id", "/path")
        with pytest.raises(IntegrityError):
            repository.create("myapp", "feature-x", "same-id", "/path")

    def test_allows_same_session_id_under_different_pks(self, repository):
        # The ``id`` column is NOT unique-by-design — two projects can
        # own a session with the same OpenCode id.  Only the composite
        # (project, session_name) PK is unique.
        repository.create("project-a", "feature-1", "shared-id", "/a")
        repository.create("project-b", "feature-1", "shared-id", "/b")
        assert repository.find_by_id("shared-id") is not None

    def test_allows_different_ids_under_same_pk_dimensions(self, repository):
        # ``session_name`` is part of the PK so we can have many rows
        # for the same project with distinct session_names.
        repository.create("myapp", "feature-a", "id-a", "/path")
        repository.create("myapp", "feature-b", "id-b", "/path")
        assert repository.get("myapp", "feature-a")["id"] == "id-a"
        assert repository.get("myapp", "feature-b")["id"] == "id-b"


# ─────────────────────────────────────────────────────────────────────────────
# Get
# ─────────────────────────────────────────────────────────────────────────────


class TestGet:
    """``OpenCodeSessionRepository.get()`` lookup path."""

    def test_returns_dict_for_existing(self, repository):
        repository.create("p", "s", "id", "/path")
        record = repository.get("p", "s")
        assert isinstance(record, dict)

    def test_returns_none_for_missing(self, repository):
        assert repository.get("nonexistent", "missing") is None

    def test_to_dict_contains_all_columns(self, repository):
        repository.create("p", "s", "id", "/path")
        record = repository.get("p", "s")
        expected_keys = {
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
        assert set(record.keys()) == expected_keys

    def test_distinguishes_rows_by_project(self, repository):
        repository.create("p-a", "s", "id", "/path-a")
        repository.create("p-b", "s", "id", "/path-b")
        assert repository.get("p-a", "s")["working_dir"] == "/path-a"
        assert repository.get("p-b", "s")["working_dir"] == "/path-b"


# ─────────────────────────────────────────────────────────────────────────────
# List
# ─────────────────────────────────────────────────────────────────────────────


class TestList:
    """``OpenCodeSessionRepository.list()`` enumeration path."""

    def test_returns_empty_list_when_no_rows(self, repository):
        assert repository.list() == []

    def test_returns_all_rows(self, repository):
        repository.create("p1", "s1", "id1", "/a")
        repository.create("p1", "s2", "id2", "/b")
        repository.create("p2", "s1", "id3", "/c")
        assert len(repository.list()) == 3

    def test_returns_dicts(self, repository):
        repository.create("p", "s", "id", "/path")
        rows = repository.list()
        assert all(isinstance(r, dict) for r in rows)

    def test_orders_by_project_then_session_name(self, repository):
        # Insert in non-sorted order; the list should come back sorted.
        repository.create("z", "a", "id", "/path")
        repository.create("a", "z", "id", "/path")
        repository.create("a", "a", "id", "/path")
        rows = repository.list()
        assert [(r["project"], r["session_name"]) for r in rows] == [
            ("a", "a"),
            ("a", "z"),
            ("z", "a"),
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Find by id
# ─────────────────────────────────────────────────────────────────────────────


class TestFindById:
    """``OpenCodeSessionRepository.find_by_id()`` — uses ``ix_opencode_sessions_id``."""

    def test_finds_existing_session(self, repository):
        repository.create("myapp", "feature-x", "sess-uuid", "/path")
        record = repository.find_by_id("sess-uuid")
        assert record is not None
        assert record["project"] == "myapp"
        assert record["session_name"] == "feature-x"

    def test_returns_none_for_unknown_id(self, repository):
        assert repository.find_by_id("nope") is None

    def test_returns_first_match_when_id_is_shared(self, repository):
        # ``id`` is not unique — the production code returns whichever
        # row the engine yields first.  We assert that *some* matching
        # row comes back, not a specific one.
        repository.create("p-a", "s1", "shared", "/a")
        repository.create("p-b", "s2", "shared", "/b")
        record = repository.find_by_id("shared")
        assert record is not None
        assert record["id"] == "shared"

    def test_uses_index_on_id(self, sqlite_engine):
        """The index ``ix_opencode_sessions_id`` exists after table creation."""
        inspector = inspect(sqlite_engine)
        index_names = {idx["name"] for idx in inspector.get_indexes("opencode_sessions")}
        assert "ix_opencode_sessions_id" in index_names


# ─────────────────────────────────────────────────────────────────────────────
# Update agent state
# ─────────────────────────────────────────────────────────────────────────────


class TestUpdateAgentState:
    """``OpenCodeSessionRepository.update_agent_state()`` lock + label write."""

    def test_locks_agent(self, repository):
        repository.create("myapp", "feature-x", "id", "/path")
        repository.update_agent_state("myapp", "feature-x", "atlas", True)
        record = repository.get("myapp", "feature-x")
        assert record["is_agent_locked"] is True

    def test_sets_last_agent(self, repository):
        repository.create("myapp", "feature-x", "id", "/path")
        repository.update_agent_state("myapp", "feature-x", "atlas", True)
        record = repository.get("myapp", "feature-x")
        assert record["last_agent"] == "atlas"

    def test_can_unlock_agent(self, repository):
        repository.create("myapp", "feature-x", "id", "/path")
        repository.update_agent_state("myapp", "feature-x", "atlas", True)
        repository.update_agent_state("myapp", "feature-x", "", False)
        record = repository.get("myapp", "feature-x")
        assert record["is_agent_locked"] is False
        assert record["last_agent"] == ""

    def test_raises_keyerror_for_missing_row(self, repository):
        with pytest.raises(KeyError):
            repository.update_agent_state("nope", "nope", "atlas", True)


# ─────────────────────────────────────────────────────────────────────────────
# Update state
# ─────────────────────────────────────────────────────────────────────────────


class TestUpdateState:
    """``OpenCodeSessionRepository.update_state()`` single-column write."""

    def test_updates_state_to_busy(self, repository):
        repository.create("p", "s", "id", "/path")
        repository.update_state("p", "s", "BUSY")
        record = repository.get("p", "s")
        assert record["state"] == "BUSY"

    def test_updates_state_to_waiting_for_input(self, repository):
        repository.create("p", "s", "id", "/path")
        repository.update_state("p", "s", "WAITING_FOR_INPUT")
        record = repository.get("p", "s")
        assert record["state"] == "WAITING_FOR_INPUT"

    def test_does_not_touch_other_columns(self, repository):
        repository.create("p", "s", "id", "/path")
        repository.update_state("p", "s", "BUSY")
        record = repository.get("p", "s")
        # last_agent and is_agent_locked should still be at defaults.
        assert record["last_agent"] == ""
        assert record["is_agent_locked"] is False

    def test_raises_keyerror_for_missing_row(self, repository):
        with pytest.raises(KeyError):
            repository.update_state("nope", "nope", "BUSY")


# ─────────────────────────────────────────────────────────────────────────────
# Update last activity
# ─────────────────────────────────────────────────────────────────────────────


class TestUpdateLastActivity:
    """``OpenCodeSessionRepository.update_last_activity()`` timestamp write."""

    def test_stamps_supplied_timestamp(self, repository):
        repository.create("p", "s", "id", "/path")
        repository.update_last_activity("p", "s", "2026-01-01T00:00:00+00:00")
        record = repository.get("p", "s")
        assert record["last_activity"] == "2026-01-01T00:00:00+00:00"

    def test_overwrites_previous_timestamp(self, repository):
        repository.create("p", "s", "id", "/path")
        first = repository.get("p", "s")["last_activity"]
        repository.update_last_activity("p", "s", "2025-05-05T05:05:05+00:00")
        second = repository.get("p", "s")["last_activity"]
        assert second == "2025-05-05T05:05:05+00:00"
        assert first != second

    def test_raises_keyerror_for_missing_row(self, repository):
        with pytest.raises(KeyError):
            repository.update_last_activity("nope", "nope", "x")


# ─────────────────────────────────────────────────────────────────────────────
# Update session data (bulk)
# ─────────────────────────────────────────────────────────────────────────────


class TestUpdateSessionData:
    """``OpenCodeSessionRepository.update_session_data()`` all-columns write."""

    def test_persists_all_columns(self, repository):
        repository.create("p", "s", "id", "/path")
        repository.update_session_data(
            project="p",
            session_name="s",
            last_agent="atlas",
            is_agent_locked=True,
            state="BUSY",
            latest_response={"text": "hi"},
            questions=[{"id": "q1"}],
            last_activity="2026-06-01T00:00:00+00:00",
        )
        record = repository.get("p", "s")
        assert record["last_agent"] == "atlas"
        assert record["is_agent_locked"] is True
        assert record["state"] == "BUSY"
        assert record["latest_response"] == {"text": "hi"}
        assert record["questions"] == [{"id": "q1"}]
        assert record["last_activity"] == "2026-06-01T00:00:00+00:00"

    def test_persists_dict_latest_response(self, repository):
        repository.create("p", "s", "id", "/path")
        repository.update_session_data(
            project="p",
            session_name="s",
            last_agent="",
            is_agent_locked=False,
            state="IDLE",
            latest_response={"parts": [{"type": "text", "text": "x"}]},
            questions=[],
            last_activity="2026-06-01T00:00:00+00:00",
        )
        record = repository.get("p", "s")
        assert record["latest_response"] == {
            "parts": [{"type": "text", "text": "x"}]
        }

    def test_persists_list_questions(self, repository):
        repository.create("p", "s", "id", "/path")
        repo_questions = [{"id": "q1", "questions": [{"question": "Q?"}]}]
        repository.update_session_data(
            project="p",
            session_name="s",
            last_agent="",
            is_agent_locked=False,
            state="IDLE",
            latest_response=None,
            questions=repo_questions,
            last_activity="",
        )
        record = repository.get("p", "s")
        assert record["questions"] == repo_questions

    def test_none_questions_persists_as_empty_list(self, repository):
        # ``update_session_data`` guards against None with ``or []``.
        repository.create("p", "s", "id", "/path")
        repository.update_session_data(
            project="p",
            session_name="s",
            last_agent="",
            is_agent_locked=False,
            state="IDLE",
            latest_response=None,
            questions=None,
            last_activity="",
        )
        record = repository.get("p", "s")
        assert record["questions"] == []

    def test_raises_keyerror_for_missing_row(self, repository):
        with pytest.raises(KeyError):
            repository.update_session_data(
                project="nope",
                session_name="nope",
                last_agent="",
                is_agent_locked=False,
                state="IDLE",
                latest_response=None,
                questions=[],
                last_activity="",
            )


# ─────────────────────────────────────────────────────────────────────────────
# Delete
# ─────────────────────────────────────────────────────────────────────────────


class TestDelete:
    """``OpenCodeSessionRepository.delete()`` removal path."""

    def test_removes_existing_row(self, repository):
        repository.create("p", "s", "id", "/path")
        repository.delete("p", "s")
        assert repository.get("p", "s") is None

    def test_raises_keyerror_for_missing_row(self, repository):
        with pytest.raises(KeyError):
            repository.delete("nope", "nope")

    def test_allows_recreate_after_delete(self, repository):
        repository.create("p", "s", "id-1", "/path")
        repository.delete("p", "s")
        # Inserting a new row with the same PK should now succeed.
        repository.create("p", "s", "id-2", "/path")
        record = repository.get("p", "s")
        assert record["id"] == "id-2"

    def test_delete_one_does_not_affect_others(self, repository):
        repository.create("p", "a", "id-a", "/a")
        repository.create("p", "b", "id-b", "/b")
        repository.delete("p", "a")
        assert repository.get("p", "a") is None
        assert repository.get("p", "b") is not None
