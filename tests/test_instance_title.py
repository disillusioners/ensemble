"""Tests for instance title persistence functions in daemon/persistence.py"""

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
    save_instance_metadata,
    get_instance_metadata,
    update_instance_title,
    list_all_instances,
)


class TestUpdateInstanceTitle:
    """Tests for update_instance_title function."""

    def test_update_title_for_existing_instance(self, tmp_path):
        """Test updating title for existing instance."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create instance first
        save_instance_metadata(conn, "test-instance", "agents/test")
        
        # Update title
        update_instance_title(conn, "test-instance", "My Test Instance")
        
        # Verify title was updated
        meta = get_instance_metadata(conn, "test-instance")
        assert meta is not None
        assert meta["title"] == "My Test Instance"
        
        conn.close()

    def test_update_title_with_no_prior_metadata(self, tmp_path):
        """Test updating title when instance has no prior metadata."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create instance with no additional metadata
        save_instance_metadata(conn, "test-instance", "agents/test")
        
        # Update title
        update_instance_title(conn, "test-instance", "New Instance Title")
        
        # Verify title was set
        meta = get_instance_metadata(conn, "test-instance")
        assert meta is not None
        assert meta["title"] == "New Instance Title"
        
        conn.close()

    def test_update_title_overwrites_existing(self, tmp_path):
        """Test that updating title overwrites existing title."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create instance with initial title
        save_instance_metadata(conn, "test-instance", "agents/test")
        update_instance_title(conn, "test-instance", "Initial Title")
        
        # Verify initial title
        meta = get_instance_metadata(conn, "test-instance")
        assert meta is not None
        assert meta["title"] == "Initial Title"
        
        # Update to new title
        update_instance_title(conn, "test-instance", "Updated Title")
        
        # Verify title was overwritten
        meta = get_instance_metadata(conn, "test-instance")
        assert meta is not None
        assert meta["title"] == "Updated Title"
        
        conn.close()

    def test_update_title_for_nonexistent_instance(self, tmp_path, caplog):
        """Test updating title for non-existent instance logs warning but doesn't crash."""
        import logging
        
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Update title for non-existent instance - should not raise
        with caplog.at_level(logging.WARNING):
            update_instance_title(conn, "nonexistent-instance", "Some Title")
        
        # Should log warning
        assert "not found" in caplog.text.lower() or "nonexistent" in caplog.text.lower()
        
        # Should not crash - conn should still be usable
        instances, total = list_all_instances(conn)
        assert total == 0
        
        conn.close()


class TestGetInstanceMetadataWithTitle:
    """Tests for get_instance_metadata returning title correctly."""

    def test_get_instance_metadata_returns_title(self, tmp_path):
        """Test that get_instance_metadata returns title."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create instance with title
        save_instance_metadata(conn, "test-instance", "agents/test")
        update_instance_title(conn, "test-instance", "Test Title")
        
        # Get metadata
        meta = get_instance_metadata(conn, "test-instance")
        
        assert meta is not None
        assert "title" in meta
        assert meta["title"] == "Test Title"
        
        conn.close()

    def test_get_instance_metadata_title_none_when_not_set(self, tmp_path):
        """Test that title is None when not set."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create instance without title
        save_instance_metadata(conn, "test-instance", "agents/test")
        
        # Get metadata
        meta = get_instance_metadata(conn, "test-instance")
        
        assert meta is not None
        assert "title" in meta
        assert meta["title"] is None
        
        conn.close()


class TestListAllInstancesWithTitle:
    """Tests for list_all_instances including title in response."""

    def test_list_all_instances_includes_title(self, tmp_path):
        """Test that list_all_instances includes title in response."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create instances with titles
        save_instance_metadata(conn, "instance-1", "agents/test1")
        update_instance_title(conn, "instance-1", "First Instance")
        
        save_instance_metadata(conn, "instance-2", "agents/test2")
        # instance-2 has no title
        
        # List all instances
        instances, total = list_all_instances(conn)
        
        assert total == 2
        
        # Find instance-1 and verify title
        s1 = next(s for s in instances if s["instance_id"] == "instance-1")
        assert s1["title"] == "First Instance"
        
        # Find instance-2 and verify no title
        s2 = next(s for s in instances if s["instance_id"] == "instance-2")
        assert s2["title"] is None
        
        conn.close()

    def test_list_all_instances_title_in_metadata_json(self, tmp_path):
        """Test that title is also available in metadata JSON."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        
        # Create instance with title
        save_instance_metadata(conn, "test-instance", "agents/test")
        update_instance_title(conn, "test-instance", "Metadata Test")
        
        # List instances
        instances, total = list_all_instances(conn)
        
        assert total == 1
        instance = instances[0]
        
        # Title should be at top level
        assert instance["title"] == "Metadata Test"
        
        # Title should also be in metadata dict
        assert "metadata" in instance
        assert isinstance(instance["metadata"], dict)
        assert instance["metadata"].get("title") == "Metadata Test"
        
        conn.close()
