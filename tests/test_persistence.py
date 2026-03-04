"""Tests for daemon/persistence.py"""

import pytest
import sys
from unittest.mock import patch, MagicMock

# Mock the langgraph module before importing persistence
mock_sqlite_saver = MagicMock()
mock_async_sqlite_saver = MagicMock()
sys.modules['langgraph'] = MagicMock()
sys.modules['langgraph.checkpoint'] = MagicMock()
sys.modules['langgraph.checkpoint.sqlite'] = MagicMock()
sys.modules['langgraph.checkpoint.sqlite'].SqliteSaver = mock_sqlite_saver
sys.modules['langgraph.checkpoint.sqlite.aio'] = MagicMock()
sys.modules['langgraph.checkpoint.sqlite.aio'].AsyncSqliteSaver = mock_async_sqlite_saver

from daemon.persistence import (
    init_database,
    get_checkpointer,
    save_session_metadata,
    get_session_metadata,
    update_session_status,
    list_all_sessions,
    delete_session,
    get_session_messages,
)


class TestInitDatabase:
    """Tests for init_database function."""

    def test_init_database_creates_file(self, tmp_path):
        """Test that database file is created."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        assert db_path.exists()
        conn.close()

    def test_init_database_creates_tables(self, tmp_path):
        """Test that sessions and session_hierarchy tables exist."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        # Check sessions table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        )
        assert cursor.fetchone() is not None

        # Check session_hierarchy table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='session_hierarchy'"
        )
        assert cursor.fetchone() is not None

        conn.close()


class TestSaveSessionMetadata:
    """Tests for save_session_metadata function."""

    def test_save_session_metadata(self, tmp_path):
        """Test saving session metadata."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        save_session_metadata(conn, "test-session", "agents/test")
        meta = get_session_metadata(conn, "test-session")

        assert meta is not None
        assert meta["session_id"] == "test-session"
        assert meta["agent_dir"] == "agents/test"
        assert meta["status"] == "idle"
        assert meta["parent_id"] is None

        conn.close()

    def test_save_session_metadata_with_parent(self, tmp_path):
        """Test saving session with parent_id."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        # Create parent session first
        save_session_metadata(conn, "parent-session", "agents/parent")

        # Create child session
        save_session_metadata(conn, "child-session", "agents/child", "parent-session")

        # Verify parent session
        parent_meta = get_session_metadata(conn, "parent-session")
        assert parent_meta is not None
        assert parent_meta["session_id"] == "parent-session"

        # Verify child session
        child_meta = get_session_metadata(conn, "child-session")
        assert child_meta is not None
        assert child_meta["session_id"] == "child-session"
        assert child_meta["parent_id"] == "parent-session"

        conn.close()


class TestGetSessionMetadata:
    """Tests for get_session_metadata function."""

    def test_get_session_metadata(self, tmp_path):
        """Test retrieving session metadata."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        save_session_metadata(conn, "test-session", "agents/test")
        meta = get_session_metadata(conn, "test-session")

        assert meta is not None
        assert meta["session_id"] == "test-session"
        assert meta["agent_dir"] == "agents/test"
        assert meta["status"] == "idle"

        conn.close()

    def test_get_session_metadata_not_found(self, tmp_path):
        """Test retrieving non-existent session returns None."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        meta = get_session_metadata(conn, "non-existent-session")

        assert meta is None

        conn.close()

    def test_get_session_metadata_includes_children(self, tmp_path):
        """Test that metadata includes children list."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        # Create parent session
        save_session_metadata(conn, "parent-session", "agents/parent")

        # Create child sessions
        save_session_metadata(conn, "child-1", "agents/child", "parent-session")
        save_session_metadata(conn, "child-2", "agents/child", "parent-session")

        # Get parent metadata
        meta = get_session_metadata(conn, "parent-session")

        assert meta is not None
        assert "children" in meta
        assert "child-1" in meta["children"]
        assert "child-2" in meta["children"]

        conn.close()


class TestUpdateSessionStatus:
    """Tests for update_session_status function."""

    def test_update_session_status(self, tmp_path):
        """Test updating session status."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        save_session_metadata(conn, "test-session", "agents/test")

        # Update status
        update_session_status(conn, "test-session", "running")

        # Verify status was updated
        meta = get_session_metadata(conn, "test-session")
        assert meta is not None
        assert meta["status"] == "running"

        conn.close()


class TestListAllSessions:
    """Tests for list_all_sessions function."""

    def test_list_all_sessions_empty(self, tmp_path):
        """Test listing when no sessions."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        sessions = list_all_sessions(conn)

        assert sessions == []

        conn.close()

    def test_list_all_sessions(self, tmp_path):
        """Test listing multiple sessions."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        # Create multiple sessions
        save_session_metadata(conn, "session-1", "agents/test1")
        save_session_metadata(conn, "session-2", "agents/test2")

        sessions = list_all_sessions(conn)

        assert len(sessions) == 2
        session_ids = [s["session_id"] for s in sessions]
        assert "session-1" in session_ids
        assert "session-2" in session_ids

        conn.close()


class TestDeleteSession:
    """Tests for delete_session function."""

    def test_delete_session(self, tmp_path):
        """Test deleting a session."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        save_session_metadata(conn, "test-session", "agents/test")

        # Verify session exists
        meta = get_session_metadata(conn, "test-session")
        assert meta is not None

        # Delete session
        delete_session(conn, "test-session")

        # Verify session is deleted
        meta = get_session_metadata(conn, "test-session")
        assert meta is None

        conn.close()

    def test_delete_session_removes_hierarchy(self, tmp_path):
        """Test that deleting also removes from hierarchy."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        # Create parent and child
        save_session_metadata(conn, "parent-session", "agents/parent")
        save_session_metadata(conn, "child-session", "agents/child", "parent-session")

        # Delete parent session
        delete_session(conn, "parent-session")

        # Verify parent is deleted
        parent_meta = get_session_metadata(conn, "parent-session")
        assert parent_meta is None

        # Verify child is still there (parent deleted but child remains)
        child_meta = get_session_metadata(conn, "child-session")
        assert child_meta is not None

        # Verify hierarchy entry is removed
        cursor = conn.execute(
            "SELECT * FROM session_hierarchy WHERE parent_id = ?",
            ("parent-session",)
        )
        assert cursor.fetchone() is None

        conn.close()

    def test_session_hierarchy_tracking(self, tmp_path):
        """Test that parent-child relationships are tracked."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        # Create sessions with hierarchy
        save_session_metadata(conn, "root-session", "agents/root")
        save_session_metadata(conn, "child-1", "agents/child", "root-session")
        save_session_metadata(conn, "child-2", "agents/child", "root-session")

        # Verify hierarchy table
        cursor = conn.execute("SELECT * FROM session_hierarchy")
        rows = cursor.fetchall()
        assert len(rows) == 2

        hierarchy = [(row["parent_id"], row["child_id"]) for row in rows]
        assert ("root-session", "child-1") in hierarchy
        assert ("root-session", "child-2") in hierarchy

        conn.close()


class TestGetCheckpointer:
    """Tests for get_checkpointer function."""

    def test_get_checkpointer(self, tmp_path):
        """Test that get_checkpointer returns a SqliteSaver."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        checkpointer = get_checkpointer(conn)

        # The mock is set up at module import time
        assert checkpointer is not None

        conn.close()
