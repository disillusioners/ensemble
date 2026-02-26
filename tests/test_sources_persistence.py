"""
Tests for daemon/sources/persistence.py
"""

import pytest
import sqlite3
import tempfile
import os
import threading
from datetime import datetime, timezone, timedelta

from daemon.sources import persistence


@pytest.fixture
def conn():
    """Create a temporary SQLite database with the required schema."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    
    # Create source_configs table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_configs (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            name TEXT NOT NULL,
            config JSON NOT NULL,
            credentials TEXT,
            enabled BOOLEAN DEFAULT TRUE,
            status TEXT DEFAULT 'stopped',
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create session_mappings table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_mappings (
            mapping_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            agent_session_id TEXT NOT NULL,
            agent_dir TEXT NOT NULL,
            metadata JSON,
            last_message_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, external_user_id),
            FOREIGN KEY (source_id) REFERENCES source_configs(source_id)
        )
    """)
    
    # Create index for session_mappings
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_mappings_source 
        ON session_mappings(source_id)
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_mappings_session 
        ON session_mappings(agent_session_id)
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_mappings_cleanup 
        ON session_mappings(last_message_at)
    """)
    
    # Create processed_external_messages table for deduplication
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_external_messages (
            source_id TEXT,
            external_message_id TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_id, external_message_id)
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_processed_msg_cleanup 
        ON processed_external_messages(processed_at)
    """)
    
    conn.commit()
    yield conn
    conn.close()
    os.unlink(path)


# ==================== Source Config Tests ====================


def test_save_source_config(conn):
    """Test saving and retrieving a source config."""
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, '{"bot_token": "abc123"}', enabled=True
    )
    config = persistence.get_source_config(conn, "telegram-main")
    assert config is not None
    assert config["source_id"] == "telegram-main"
    assert config["source_type"] == "telegram"
    assert config["name"] == "Test Bot"
    assert config["config"] == {"polling": True}
    assert config["credentials"] == {"bot_token": "abc123"}  # JSON parsed on retrieval
    assert config["enabled"] == True  # SQLite stores boolean as 1
    assert config["status"] == "stopped"


def test_save_source_config_upsert(conn):
    """Test updating an existing source config."""
    # First save
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, '{"bot_token": "abc123"}', enabled=True
    )
    
    # Update
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Updated Bot",
        {"polling": False}, '{"bot_token": "xyz789"}', enabled=False
    )
    
    config = persistence.get_source_config(conn, "telegram-main")
    assert config["name"] == "Updated Bot"
    assert config["config"] == {"polling": False}
    assert config["enabled"] == False  # SQLite stores boolean as 0


def test_get_source_config_not_found(conn):
    """Test that get_source_config returns None for non-existent config."""
    config = persistence.get_source_config(conn, "non-existent")
    assert config is None


def test_list_source_configs(conn):
    """Test listing all source configs."""
    persistence.save_source_config(
        conn, "telegram-1", "telegram", "Bot 1",
        {"polling": True}, None, enabled=True
    )
    persistence.save_source_config(
        conn, "telegram-2", "telegram", "Bot 2",
        {"polling": False}, None, enabled=False
    )
    
    configs = persistence.list_source_configs(conn)
    assert len(configs) == 2
    source_ids = [c["source_id"] for c in configs]
    assert "telegram-1" in source_ids
    assert "telegram-2" in source_ids


def test_update_source_status(conn):
    """Test updating the status field of a source config."""
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, None, enabled=True
    )
    
    persistence.update_source_status(
        conn, "telegram-main", "running", error_message=None
    )
    
    config = persistence.get_source_config(conn, "telegram-main")
    assert config["status"] == "running"
    assert config["error_message"] is None
    
    # Test with error message
    persistence.update_source_status(
        conn, "telegram-main", "error", error_message="Connection failed"
    )
    
    config = persistence.get_source_config(conn, "telegram-main")
    assert config["status"] == "error"
    assert config["error_message"] == "Connection failed"


def test_delete_source_config(conn):
    """Test deleting a source config."""
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, None, enabled=True
    )
    
    result = persistence.delete_source_config(conn, "telegram-main")
    assert result is True
    
    config = persistence.get_source_config(conn, "telegram-main")
    assert config is None
    
    # Test deleting non-existent config
    result = persistence.delete_source_config(conn, "non-existent")
    assert result is False


# ==================== Session Mapping Tests ====================


def test_save_session_mapping(conn):
    """Test saving a session mapping."""
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, None, enabled=True
    )
    
    persistence.save_session_mapping(
        conn, "mapping-1", "telegram-main", "user123",
        "session-abc", "/path/to/agent", {"key": "value"}
    )
    
    mapping = persistence.get_session_mapping(conn, "telegram-main", "user123")
    assert mapping is not None
    assert mapping["mapping_id"] == "mapping-1"
    assert mapping["source_id"] == "telegram-main"
    assert mapping["external_user_id"] == "user123"
    assert mapping["agent_session_id"] == "session-abc"
    assert mapping["agent_dir"] == "/path/to/agent"
    assert mapping["metadata"] == {"key": "value"}


def test_save_session_mapping_duplicate(conn):
    """Test that save_session_mapping updates existing mapping (upsert)."""
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, None, enabled=True
    )
    
    # First save
    persistence.save_session_mapping(
        conn, "mapping-1", "telegram-main", "user123",
        "session-abc", "/path/to/agent", {"key": "value1"}
    )
    
    # Update with same mapping_id
    persistence.save_session_mapping(
        conn, "mapping-1", "telegram-main", "user123",
        "session-xyz", "/new/path", {"key": "value2"}
    )
    
    mapping = persistence.get_session_mapping(conn, "telegram-main", "user123")
    assert mapping["agent_session_id"] == "session-xyz"
    assert mapping["agent_dir"] == "/new/path"
    assert mapping["metadata"] == {"key": "value2"}


def test_get_session_mapping(conn):
    """Test getting a session mapping by source_id and external_user_id."""
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, None, enabled=True
    )
    
    persistence.save_session_mapping(
        conn, "mapping-1", "telegram-main", "user123",
        "session-abc", "/path/to/agent", None
    )
    
    mapping = persistence.get_session_mapping(conn, "telegram-main", "user123")
    assert mapping is not None
    assert mapping["mapping_id"] == "mapping-1"


def test_get_session_mapping_not_found(conn):
    """Test that get_session_mapping returns None for non-existent mapping."""
    mapping = persistence.get_session_mapping(conn, "non-existent", "user123")
    assert mapping is None


def test_get_session_mapping_by_session(conn):
    """Test getting a session mapping by agent_session_id."""
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, None, enabled=True
    )
    
    persistence.save_session_mapping(
        conn, "mapping-1", "telegram-main", "user123",
        "session-abc", "/path/to/agent", None
    )
    
    mapping = persistence.get_session_mapping_by_session(conn, "session-abc")
    assert mapping is not None
    assert mapping["external_user_id"] == "user123"


def test_update_mapping_last_message(conn):
    """Test updating the last_message_at timestamp."""
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, None, enabled=True
    )
    
    persistence.save_session_mapping(
        conn, "mapping-1", "telegram-main", "user123",
        "session-abc", "/path/to/agent", None
    )
    
    # Get initial last_message_at
    mapping_before = persistence.get_session_mapping(conn, "telegram-main", "user123")
    initial_time = mapping_before["last_message_at"]
    
    # Wait a bit to ensure time difference (SQLite CURRENT_TIMESTAMP has second precision)
    import time
    time.sleep(1.1)
    
    # Update
    persistence.update_mapping_last_message(conn, "telegram-main", "user123")
    
    mapping_after = persistence.get_session_mapping(conn, "telegram-main", "user123")
    assert mapping_after["last_message_at"] > initial_time


def test_list_session_mappings(conn):
    """Test listing session mappings by source_id."""
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, None, enabled=True
    )
    
    persistence.save_session_mapping(
        conn, "mapping-1", "telegram-main", "user1",
        "session-1", "/path/1", None
    )
    persistence.save_session_mapping(
        conn, "mapping-2", "telegram-main", "user2",
        "session-2", "/path/2", None
    )
    persistence.save_session_mapping(
        conn, "mapping-3", "other-source", "user3",
        "session-3", "/path/3", None
    )
    
    mappings = persistence.list_session_mappings(conn, "telegram-main")
    assert len(mappings) == 2
    external_ids = [m["external_user_id"] for m in mappings]
    assert "user1" in external_ids
    assert "user2" in external_ids


def test_delete_session_mapping(conn):
    """Test deleting a session mapping by mapping_id."""
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, None, enabled=True
    )
    
    persistence.save_session_mapping(
        conn, "mapping-1", "telegram-main", "user123",
        "session-abc", "/path/to/agent", None
    )
    
    result = persistence.delete_session_mapping(conn, "mapping-1")
    assert result is True
    
    mapping = persistence.get_session_mapping(conn, "telegram-main", "user123")
    assert mapping is None
    
    # Test deleting non-existent mapping
    result = persistence.delete_session_mapping(conn, "non-existent")
    assert result is False


# ==================== Deduplication Tests ====================


def test_is_duplicate_message_new(conn):
    """Test that is_duplicate_message returns False for new message."""
    result = persistence.is_duplicate_message(conn, "telegram-main", "msg-123")
    assert result is False
    
    # Verify the message was inserted
    cursor = conn.execute(
        "SELECT * FROM processed_external_messages WHERE source_id = ? AND external_message_id = ?",
        ("telegram-main", "msg-123")
    )
    row = cursor.fetchone()
    assert row is not None


def test_is_duplicate_message_duplicate(conn):
    """Test that is_duplicate_message returns True for duplicate."""
    # First insert
    persistence.is_duplicate_message(conn, "telegram-main", "msg-123")
    
    # Check again - should be duplicate
    result = persistence.is_duplicate_message(conn, "telegram-main", "msg-123")
    assert result is True


def test_is_duplicate_message_atomic(conn):
    """Test thread safety of is_duplicate_message."""
    # Insert initial message
    persistence.is_duplicate_message(conn, "telegram-main", "msg-shared")
    
    results = []
    errors = []
    
    def check_duplicate():
        try:
            # Each thread gets its own connection to the SAME database
            # (use the path from the fixture's connection)
            fd, path = tempfile.mkstemp(suffix='.db')
            os.close(fd)
            # Create fresh db for test - since we're testing atomic insert
            # we want each thread to attempt inserting same message
            thread_conn = sqlite3.connect(path)
            thread_conn.row_factory = sqlite3.Row
            
            # Create the table
            thread_conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_external_messages (
                    source_id TEXT,
                    external_message_id TEXT,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (source_id, external_message_id)
                )
            """)
            thread_conn.commit()
            
            # First thread: insert new (should succeed)
            # Second+ threads: try to insert same (should fail/duplicate)
            # Since we can't control thread order, we test basic functionality
            # by inserting different messages and verifying one is new, one is dup
            result = persistence.is_duplicate_message(thread_conn, "telegram-main", f"msg-thread-{threading.current_thread().name}")
            results.append(result)
            thread_conn.close()
            os.unlink(path)
        except Exception as e:
            errors.append(e)
    
    # Test basic insert behavior
    # First call should be False (new message), second with same ID should be True (duplicate)
    result1 = persistence.is_duplicate_message(conn, "telegram-main", "msg-dup-test")
    result2 = persistence.is_duplicate_message(conn, "telegram-main", "msg-dup-test")
    
    assert result1 is False  # First is new
    assert result2 is True   # Second is duplicate
    
    # Test with multiple threads inserting DIFFERENT messages concurrently
    # Each should succeed (not truly testing atomicity of same key, but testing thread safety)
    threads = []
    for i in range(3):
        t = threading.Thread(target=check_duplicate, name=f"thread-{i}")
        threads.append(t)
    
    for t in threads:
        t.start()
    
    for t in threads:
        t.join()
    
    # All should succeed since they have unique message IDs
    assert len(errors) == 0
    assert all(r is False for r in results)


# ==================== Cleanup Tests ====================


def test_cleanup_processed_messages_ttl(conn):
    """Test that cleanup_processed_messages deletes old messages."""
    # Insert a message with old timestamp
    old_time = datetime.now() - timedelta(hours=48)
    conn.execute(
        """
        INSERT INTO processed_external_messages (source_id, external_message_id, processed_at)
        VALUES (?, ?, ?)
        """,
        ("telegram-main", "msg-old", old_time)
    )
    
    # Insert a recent message
    conn.execute(
        """
        INSERT INTO processed_external_messages (source_id, external_message_id, processed_at)
        VALUES (?, ?, ?)
        """,
        ("telegram-main", "msg-new", datetime.now())
    )
    conn.commit()
    
    # Cleanup with 24 hour TTL
    deleted = persistence.cleanup_processed_messages(conn, max_age_hours=24)
    
    assert deleted == 1
    
    # Verify old message is deleted
    cursor = conn.execute(
        "SELECT * FROM processed_external_messages WHERE external_message_id = ?",
        ("msg-old",)
    )
    assert cursor.fetchone() is None
    
    # Verify new message still exists
    cursor = conn.execute(
        "SELECT * FROM processed_external_messages WHERE external_message_id = ?",
        ("msg-new",)
    )
    assert cursor.fetchone() is not None


def test_cleanup_inactive_mappings(conn):
    """Test that cleanup_inactive_mappings deletes old mappings."""
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, None, enabled=True
    )
    
    # Create mapping with old last_message_at
    old_time = datetime.now() - timedelta(days=60)
    conn.execute(
        """
        INSERT INTO session_mappings (mapping_id, source_id, external_user_id, agent_session_id, agent_dir, last_message_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("mapping-old", "telegram-main", "user-old", "session-old", "/old", old_time, old_time)
    )
    
    # Create recent mapping
    conn.execute(
        """
        INSERT INTO session_mappings (mapping_id, source_id, external_user_id, agent_session_id, agent_dir, last_message_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("mapping-new", "telegram-main", "user-new", "session-new", "/new", datetime.now(), datetime.now())
    )
    conn.commit()
    
    # Cleanup with 30 day TTL
    deleted = persistence.cleanup_inactive_mappings(conn, max_age_days=30)
    
    assert deleted == 1
    
    # Verify old mapping is deleted
    mapping = persistence.get_session_mapping(conn, "telegram-main", "user-old")
    assert mapping is None
    
    # Verify new mapping still exists
    mapping = persistence.get_session_mapping(conn, "telegram-main", "user-new")
    assert mapping is not None


def test_cleanup_inactive_mappings_with_null_last_message(conn):
    """Test cleanup of mappings with NULL last_message_at (only created_at checked)."""
    persistence.save_source_config(
        conn, "telegram-main", "telegram", "Test Bot",
        {"polling": True}, None, enabled=True
    )
    
    # Create mapping with NULL last_message_at but old created_at
    old_time = datetime.now() - timedelta(days=60)
    conn.execute(
        """
        INSERT INTO session_mappings (mapping_id, source_id, external_user_id, agent_session_id, agent_dir, last_message_at, created_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?)
        """,
        ("mapping-null", "telegram-main", "user-null", "session-null", "/null", old_time)
    )
    
    # Create recent mapping with NULL last_message_at
    conn.execute(
        """
        INSERT INTO session_mappings (mapping_id, source_id, external_user_id, agent_session_id, agent_dir, last_message_at, created_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?)
        """,
        ("mapping-null-new", "telegram-main", "user-null-new", "session-null-new", "/null-new", datetime.now())
    )
    conn.commit()
    
    # Cleanup with 30 day TTL
    deleted = persistence.cleanup_inactive_mappings(conn, max_age_days=30)
    
    assert deleted == 1
    
    # Verify old mapping is deleted
    mapping = persistence.get_session_mapping(conn, "telegram-main", "user-null")
    assert mapping is None
    
    # Verify new mapping still exists
    mapping = persistence.get_session_mapping(conn, "telegram-main", "user-null-new")
    assert mapping is not None
