"""Tests for session title persistence functions in daemon/persistence.py"""

import pytest
import sys
import json
from unittest.mock import patch, MagicMock
from pathlib import Path

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
    save_session_metadata,
    get_session_metadata,
    update_session_title,
    list_all_sessions,
)


class TestUpdateSessionTitle:
    """Tests for update_session_title function."""

    def test_update_title_for_existing_session(self, tmp_path):
        """Test updating title for existing session."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create session first
        save_session_metadata(conn, "test-session", "agents/test")
        
        # Update title
        update_session_title(conn, "test-session", "My Test Session")
        
        # Verify title was updated
        meta = get_session_metadata(conn, "test-session")
        assert meta is not None
        assert meta["title"] == "My Test Session"
        
        conn.close()

    def test_update_title_with_no_prior_metadata(self, tmp_path):
        """Test updating title when session has no prior metadata."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create session with no additional metadata
        save_session_metadata(conn, "test-session", "agents/test")
        
        # Update title
        update_session_title(conn, "test-session", "New Session Title")
        
        # Verify title was set
        meta = get_session_metadata(conn, "test-session")
        assert meta is not None
        assert meta["title"] == "New Session Title"
        
        conn.close()

    def test_update_title_overwrites_existing(self, tmp_path):
        """Test that updating title overwrites existing title."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create session with initial title
        save_session_metadata(conn, "test-session", "agents/test")
        update_session_title(conn, "test-session", "Initial Title")
        
        # Verify initial title
        meta = get_session_metadata(conn, "test-session")
        assert meta is not None
        assert meta["title"] == "Initial Title"
        
        # Update to new title
        update_session_title(conn, "test-session", "Updated Title")
        
        # Verify title was overwritten
        meta = get_session_metadata(conn, "test-session")
        assert meta is not None
        assert meta["title"] == "Updated Title"
        
        conn.close()

    def test_update_title_for_nonexistent_session(self, tmp_path, caplog):
        """Test updating title for non-existent session logs warning but doesn't crash."""
        import logging
        
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Update title for non-existent session - should not raise
        with caplog.at_level(logging.WARNING):
            update_session_title(conn, "nonexistent-session", "Some Title")
        
        # Should log warning
        assert "not found" in caplog.text.lower() or "nonexistent" in caplog.text.lower()
        
        # Should not crash - conn should still be usable
        sessions, total = list_all_sessions(conn)
        assert total == 0
        
        conn.close()


class TestGetSessionMetadataWithTitle:
    """Tests for get_session_metadata returning title correctly."""

    def test_get_session_metadata_returns_title(self, tmp_path):
        """Test that get_session_metadata returns title."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create session with title
        save_session_metadata(conn, "test-session", "agents/test")
        update_session_title(conn, "test-session", "Test Title")
        
        # Get metadata
        meta = get_session_metadata(conn, "test-session")
        
        assert meta is not None
        assert "title" in meta
        assert meta["title"] == "Test Title"
        
        conn.close()

    def test_get_session_metadata_title_none_when_not_set(self, tmp_path):
        """Test that title is None when not set."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create session without title
        save_session_metadata(conn, "test-session", "agents/test")
        
        # Get metadata
        meta = get_session_metadata(conn, "test-session")
        
        assert meta is not None
        assert "title" in meta
        assert meta["title"] is None
        
        conn.close()


class TestListAllSessionsWithTitle:
    """Tests for list_all_sessions including title in response."""

    def test_list_all_sessions_includes_title(self, tmp_path):
        """Test that list_all_sessions includes title in response."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create sessions with titles
        save_session_metadata(conn, "session-1", "agents/test1")
        update_session_title(conn, "session-1", "First Session")
        
        save_session_metadata(conn, "session-2", "agents/test2")
        # session-2 has no title
        
        # List all sessions
        sessions, total = list_all_sessions(conn)
        
        assert total == 2
        
        # Find session-1 and verify title
        s1 = next(s for s in sessions if s["session_id"] == "session-1")
        assert s1["title"] == "First Session"
        
        # Find session-2 and verify no title
        s2 = next(s for s in sessions if s["session_id"] == "session-2")
        assert s2["title"] is None
        
        conn.close()

    def test_list_all_sessions_title_in_metadata_json(self, tmp_path):
        """Test that title is also available in metadata JSON."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create session with title
        save_session_metadata(conn, "test-session", "agents/test")
        update_session_title(conn, "test-session", "Metadata Test")
        
        # List sessions
        sessions, total = list_all_sessions(conn)
        
        assert total == 1
        session = sessions[0]
        
        # Title should be at top level
        assert session["title"] == "Metadata Test"
        
        # Title should also be in metadata dict
        assert "metadata" in session
        assert isinstance(session["metadata"], dict)
        assert session["metadata"].get("title") == "Metadata Test"
        
        conn.close()
