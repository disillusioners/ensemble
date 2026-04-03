"""
Tests for daemon/sources/registry.py
"""

import pytest
import asyncio
import sqlite3
import tempfile
import os
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from daemon.sources.registry import SourceRegistry
from daemon.sources.base import (
    MessageSourceAdapter,
    SourceConfig,
    SourceStatus,
    IncomingMessage,
)
from daemon.sources import persistence


# Test adapter implementation - renamed to avoid pytest collection warning
class MockMessageAdapter(MessageSourceAdapter):
    """Test adapter for unit tests."""
    
    async def start(self):
        pass
    
    async def stop(self):
        pass
    
    async def send(self, message):
        return True
    
    async def health_check(self):
        return True


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
    
    # Create instance_mappings table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS instance_mappings (
            mapping_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            agent_instance_id TEXT NOT NULL,
            agent_dir TEXT NOT NULL,
            metadata JSON,
            last_message_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, external_user_id),
            FOREIGN KEY (source_id) REFERENCES source_configs(source_id)
        )
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_instance_mappings_source 
        ON instance_mappings(source_id)
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_instance_mappings_instance 
        ON instance_mappings(agent_instance_id)
    """)
    
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_instance_mappings_cleanup 
        ON instance_mappings(last_message_at)
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


@pytest.fixture
def mock_manager():
    """Create a mock InstanceManager."""
    manager = MagicMock()
    
    # Mock config
    mock_config = MagicMock()
    mock_config.agents.directory = "/default/agents"
    manager.config = mock_config
    
    # Mock queue
    mock_queue = MagicMock()
    mock_queue.enqueue = MagicMock()
    manager.queue = mock_queue
    
    # Mock spawn_instance
    manager.spawn_instance = MagicMock(return_value="test-instance-id")
    
    return manager


# ==================== Registration Tests ====================


def test_register_adapter(conn, mock_manager):
    """Test registering a new adapter."""
    registry = SourceRegistry(conn, mock_manager)
    config = SourceConfig("test-source", "telegram", "Test Adapter", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    
    registry.register(adapter)
    
    assert registry.get("test-source") == adapter


def test_register_duplicate_raises(conn, mock_manager):
    """Test that registering a duplicate adapter raises ValueError."""
    registry = SourceRegistry(conn, mock_manager)
    config = SourceConfig("test-source", "telegram", "Test Adapter", {}, {})
    adapter1 = MockMessageAdapter(config, lambda msg: None)
    adapter2 = MockMessageAdapter(config, lambda msg: None)
    
    registry.register(adapter1)
    
    # Attempting to register the same source_id again should raise ValueError
    with pytest.raises(ValueError, match="already registered"):
        registry.register(adapter2)


def test_unregister_adapter(conn, mock_manager):
    """Test removing an adapter from the registry."""
    registry = SourceRegistry(conn, mock_manager)
    config = SourceConfig("test-source", "telegram", "Test Adapter", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    
    registry.register(adapter)
    assert registry.get("test-source") == adapter
    
    result = registry.unregister("test-source")
    
    assert result is True
    assert registry.get("test-source") is None


def test_unregister_unknown_returns_false(conn, mock_manager):
    """Test that unregistering an unknown adapter returns False."""
    registry = SourceRegistry(conn, mock_manager)
    
    result = registry.unregister("unknown-source")
    
    assert result is False


def test_get_returns_registered_adapter(conn, mock_manager):
    """Test that get returns the registered adapter."""
    registry = SourceRegistry(conn, mock_manager)
    config = SourceConfig("my-source", "webhook", "My Source", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    
    registry.register(adapter)
    
    result = registry.get("my-source")
    
    assert result == adapter


def test_get_returns_none_for_unknown(conn, mock_manager):
    """Test that get returns None for unknown source."""
    registry = SourceRegistry(conn, mock_manager)
    
    result = registry.get("unknown-source")
    
    assert result is None


# ==================== Lifecycle Tests ====================


def test_list_adapters_empty(conn, mock_manager):
    """Test that list_adapters returns empty list initially."""
    registry = SourceRegistry(conn, mock_manager)
    
    result = registry.list_adapters()
    
    assert result == []


def test_list_adapters_returns_all(conn, mock_manager):
    """Test that list_adapters returns all registered adapters."""
    registry = SourceRegistry(conn, mock_manager)
    
    # Register multiple adapters
    config1 = SourceConfig("source-1", "telegram", "Telegram 1", {}, {})
    config2 = SourceConfig("source-2", "webhook", "Webhook 1", {}, {})
    
    adapter1 = MockMessageAdapter(config1, lambda msg: None)
    adapter2 = MockMessageAdapter(config2, lambda msg: None)
    
    registry.register(adapter1)
    registry.register(adapter2)
    
    result = registry.list_adapters()
    
    assert len(result) == 2
    source_ids = [r["source_id"] for r in result]
    assert "source-1" in source_ids
    assert "source-2" in source_ids


def test_adapter_status_tracking(conn, mock_manager):
    """Test that adapter status is tracked correctly."""
    registry = SourceRegistry(conn, mock_manager)
    config = SourceConfig("test-source", "telegram", "Test", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    
    # Initial status should be STOPPED
    assert adapter.status == SourceStatus.STOPPED
    
    registry.register(adapter)
    
    # List should show stopped status
    result = registry.list_adapters()
    assert len(result) == 1
    assert result[0]["status"] == "stopped"
    assert result[0]["source_id"] == "test-source"
    assert result[0]["source_type"] == "telegram"
    assert result[0]["name"] == "Test"


# ==================== Integration Tests ====================


@pytest.mark.asyncio
async def test_handle_message_calls_queue_enqueue(conn, mock_manager):
    """Test that handle_message calls queue.enqueue with correct parameters."""
    registry = SourceRegistry(conn, mock_manager)
    
    # Create a test message
    msg = IncomingMessage(
        external_user_id="user123",
        content="Hello world",
        source_id="test-source",
        metadata={"message_id": "msg-001"}
    )
    
    # Mock persistence functions to avoid database operations
    with patch('daemon.sources.registry.persistence.is_duplicate_message', return_value=False), \
         patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper:
        
        # Setup mock mapper
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
        MockInstanceMapper.return_value = mock_mapper_instance
        
        # Call handle_message
        await registry._handle_message("test-source", msg)
        
        # Verify queue.enqueue was called
        mock_manager.queue.enqueue.assert_called_once()
        
        # Check the call parameters
        call_kwargs = mock_manager.queue.enqueue.call_args.kwargs
        assert call_kwargs["instance_id"] == "instance-123"
        assert call_kwargs["content"] == "Hello world"
        assert call_kwargs["source"] == "test-source:user123"
        assert call_kwargs["priority"] == 1
        assert call_kwargs["metadata"] == {"message_id": "msg-001"}


@pytest.mark.asyncio
async def test_handle_message_checks_duplicate(conn, mock_manager):
    """Test that handle_message checks for duplicate messages."""
    registry = SourceRegistry(conn, mock_manager)
    
    # Create a test message with a message_id
    msg = IncomingMessage(
        external_user_id="user123",
        content="Hello",
        source_id="test-source",
        metadata={"message_id": "duplicate-msg-id"}
    )
    
    # Mock is_duplicate_message to return True (duplicate)
    with patch('daemon.sources.registry.persistence.is_duplicate_message', return_value=True) as mock_dup_check:
        
        # Call handle_message
        await registry._handle_message("test-source", msg)
        
        # Verify duplicate check was called
        mock_dup_check.assert_called_once_with(conn, "test-source", "duplicate-msg-id")
        
        # queue.enqueue should NOT be called for duplicates
        mock_manager.queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_creates_instance_for_new_user(conn, mock_manager):
    """Test that new users get a new instance created."""
    registry = SourceRegistry(conn, mock_manager)
    
    # Create a test message
    msg = IncomingMessage(
        external_user_id="new-user-456",
        content="Hi there",
        source_id="test-source",
        metadata={"message_id": "msg-002"}
    )
    
    # Mock persistence functions
    with patch('daemon.sources.registry.persistence.is_duplicate_message', return_value=False), \
         patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper:
        
        # Setup mock mapper to simulate new user (no existing instance)
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="new-instance-id")
        MockInstanceMapper.return_value = mock_mapper_instance
        
        # Call handle_message
        await registry._handle_message("test-source", msg)
        
        # Verify spawn_instance was called (through mapper)
        mock_mapper_instance.get_or_create_instance.assert_called_once()
        
        # Verify queue.enqueue was called with the new instance
        mock_manager.queue.enqueue.assert_called_once()
        assert mock_manager.queue.enqueue.call_args.kwargs["instance_id"] == "new-instance-id"


@pytest.mark.asyncio
async def test_handle_message_uses_agent_dir_from_metadata(conn, mock_manager):
    """Test that agent_dir from message metadata is used when present."""
    registry = SourceRegistry(conn, mock_manager)
    
    # Create a test message with agent_dir in metadata
    msg = IncomingMessage(
        external_user_id="user123",
        content="Hello",
        source_id="test-source",
        metadata={"message_id": "msg-003", "agent_dir": "/custom/agents"}
    )
    
    with patch('daemon.sources.registry.persistence.is_duplicate_message', return_value=False), \
         patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper:
        
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
        MockInstanceMapper.return_value = mock_mapper_instance
        
        await registry._handle_message("test-source", msg)
        
        # Verify get_or_create_instance was called with custom agent_dir
        mock_mapper_instance.get_or_create_instance.assert_called_once_with(
            source_id="test-source",
            external_user_id="user123",
            agent_dir="/custom/agents"
        )


@pytest.mark.asyncio
async def test_handle_message_uses_default_agent_dir(conn, mock_manager):
    """Test that default agent_dir is used when not in metadata."""
    registry = SourceRegistry(conn, mock_manager)
    
    # Create a test message WITHOUT agent_dir in metadata
    msg = IncomingMessage(
        external_user_id="user123",
        content="Hello",
        source_id="test-source",
        metadata={"message_id": "msg-004"}  # No agent_dir
    )
    
    with patch('daemon.sources.registry.persistence.is_duplicate_message', return_value=False), \
         patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper:
        
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
        MockInstanceMapper.return_value = mock_mapper_instance
        
        await registry._handle_message("test-source", msg)
        
        # Verify get_or_create_instance was called with default agent_dir from config
        mock_mapper_instance.get_or_create_instance.assert_called_once_with(
            source_id="test-source",
            external_user_id="user123",
            agent_dir="/default/agents"  # From mock_manager.config.agents.directory
        )


# ==================== Additional Tests ====================


def test_list_adapters_includes_status_info(conn, mock_manager):
    """Test that list_adapters includes detailed status information."""
    registry = SourceRegistry(conn, mock_manager)
    config = SourceConfig("test-source", "telegram", "Test", {}, {}, enabled=True)
    adapter = MockMessageAdapter(config, lambda msg: None)
    
    # Manually set status and error
    adapter._status = SourceStatus.RUNNING
    adapter._error = "Some error"
    
    registry.register(adapter)
    
    result = registry.list_adapters()
    
    assert len(result) == 1
    assert result[0]["status"] == "running"
    assert result[0]["error"] == "Some error"
    assert result[0]["enabled"] is True


def test_unregister_cancels_supervisor_task(conn, mock_manager):
    """Test that unregistering cancels the supervisor task if running."""
    registry = SourceRegistry(conn, mock_manager)
    config = SourceConfig("test-source", "telegram", "Test", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    
    registry.register(adapter)
    
    # Create a mock task
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.done.return_value = False
    registry._supervisor_tasks["test-source"] = mock_task
    
    # Unregister
    registry.unregister("test-source")
    
    # Verify task was cancelled
    mock_task.cancel.assert_called_once()


def test_get_with_empty_registry(conn, mock_manager):
    """Test that get returns None for empty registry."""
    registry = SourceRegistry(conn, mock_manager)
    
    result = registry.get("nonexistent")
    
    assert result is None
