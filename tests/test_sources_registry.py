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
    
    # Mock enqueue_message (async method called by registry)
    manager.enqueue_message = AsyncMock()
    
    # Mock spawn_instance_with_mcp
    manager.spawn_instance_with_mcp = AsyncMock(return_value="test-instance-id")
    
    # Mock _process_queue (called via asyncio.create_task)
    manager._process_queue = AsyncMock()
    
    # Mock source repository (used by InstanceMapper in _handle_message)
    mock_source_repo = MagicMock()
    mock_source_repo.check_and_mark_processed = MagicMock(return_value=False)  # Not a duplicate
    mock_source_repo.get_instance_mapping = MagicMock(return_value=None)
    mock_source_repo.create_instance_mapping = MagicMock(return_value=MagicMock(
        mapping_id="test-mapping-id",
        source_id="test-source",
        external_user_id="user123",
        agent_instance_id="test-instance",
        agent_id="developer",
        agent_dir="/default/agents",
        mapping_metadata={},
        last_message_at=None,
        created_at="2024-01-01T00:00:00",
    ))
    mock_source_repo.delete_instance_mapping = MagicMock()
    mock_source_repo.list_source_configs = MagicMock(return_value=[])
    mock_source_repo.update_source_status = MagicMock()
    mock_source_repo.get_source_config = MagicMock(return_value=None)
    mock_source_repo.update_source_config = MagicMock()
    manager._source_repo = mock_source_repo
    
    return manager


# ==================== Registration Tests ====================


def test_register_adapter(conn, mock_manager):
    """Test registering a new adapter."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    config = SourceConfig("test-source", "telegram", "Test Adapter", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    
    registry.register(adapter)
    
    assert registry.get("test-source") == adapter


def test_register_duplicate_raises(conn, mock_manager):
    """Test that registering a duplicate adapter raises ValueError."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    config = SourceConfig("test-source", "telegram", "Test Adapter", {}, {})
    adapter1 = MockMessageAdapter(config, lambda msg: None)
    adapter2 = MockMessageAdapter(config, lambda msg: None)
    
    registry.register(adapter1)
    
    # Attempting to register the same source_id again should raise ValueError
    with pytest.raises(ValueError, match="already registered"):
        registry.register(adapter2)


def test_unregister_adapter(conn, mock_manager):
    """Test removing an adapter from the registry."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    config = SourceConfig("test-source", "telegram", "Test Adapter", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    
    registry.register(adapter)
    assert registry.get("test-source") == adapter
    
    result = registry.unregister("test-source")
    
    assert result is True
    assert registry.get("test-source") is None


def test_unregister_unknown_returns_false(conn, mock_manager):
    """Test that unregistering an unknown adapter returns False."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    
    result = registry.unregister("unknown-source")
    
    assert result is False


def test_get_returns_registered_adapter(conn, mock_manager):
    """Test that get returns the registered adapter."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    config = SourceConfig("my-source", "webhook", "My Source", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    
    registry.register(adapter)
    
    result = registry.get("my-source")
    
    assert result == adapter


def test_get_returns_none_for_unknown(conn, mock_manager):
    """Test that get returns None for unknown source."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    
    result = registry.get("unknown-source")
    
    assert result is None


# ==================== Lifecycle Tests ====================


def test_list_adapters_empty(conn, mock_manager):
    """Test that list_adapters returns empty list initially."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    
    result = registry.list_adapters()
    
    assert result == []


def test_list_adapters_returns_all(conn, mock_manager):
    """Test that list_adapters returns all registered adapters."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    
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
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
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
    """Test that handle_message calls enqueue_message with correct parameters."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    
    # Create a test message
    msg = IncomingMessage(
        external_user_id="user123",
        content="Hello world",
        source_id="test-source",
        metadata={"message_id": "msg-001"}
    )
    
    # Configure mock source repo - not a duplicate
    mock_manager._source_repo.check_and_mark_processed = MagicMock(return_value=False)
    
    # Mock InstanceMapper to return our test instance
    with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
         patch('daemon.sources.mapper.get_registry') as mock_get_registry:
        mock_agent_registry = MagicMock()
        mock_agent = MagicMock()
        mock_agent.path = "/default/agents"
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(return_value=mock_agent)
        mock_get_registry.return_value = mock_agent_registry

        mock_mapper_instance = MagicMock()
        mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
        MockInstanceMapper.return_value = mock_mapper_instance
        
        # Call handle_message
        await registry._handle_message("test-source", msg)
        
        # Verify enqueue_message was called
        mock_manager.enqueue_message.assert_called_once()
        
        # Check the call parameters
        call_kwargs = mock_manager.enqueue_message.call_args.kwargs
        assert call_kwargs["instance_id"] == "instance-123"
        assert call_kwargs["message"] == "Hello world"
        assert call_kwargs["source"] == "test-source:user123"
        assert call_kwargs["priority"] == 1


@pytest.mark.asyncio
async def test_handle_message_checks_duplicate(conn, mock_manager):
    """Test that handle_message checks for duplicate messages."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    
    # Create a test message with a message_id
    msg = IncomingMessage(
        external_user_id="user123",
        content="Hello",
        source_id="test-source",
        metadata={"message_id": "duplicate-msg-id"}
    )
    
    # Configure mock source repo to return True (duplicate)
    mock_manager._source_repo.check_and_mark_processed = MagicMock(return_value=True)
    
    # Call handle_message
    await registry._handle_message("test-source", msg)
    
    # Verify duplicate check was called
    mock_manager._source_repo.check_and_mark_processed.assert_called_once_with("test-source", "duplicate-msg-id")
    
    # enqueue_message should NOT be called for duplicates
    mock_manager.enqueue_message.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_creates_instance_for_new_user(conn, mock_manager):
    """Test that new users get a new instance created."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    
    # Create a test message
    msg = IncomingMessage(
        external_user_id="new-user-456",
        content="Hi there",
        source_id="test-source",
        metadata={"message_id": "msg-002"}
    )
    
    # Configure mock source repo - not a duplicate
    mock_manager._source_repo.check_and_mark_processed = MagicMock(return_value=False)
    
    # Mock InstanceMapper
    with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
         patch('daemon.sources.mapper.get_registry') as mock_get_registry:
        mock_agent_registry = MagicMock()
        mock_agent = MagicMock()
        mock_agent.path = "/default/agents"
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(return_value=mock_agent)
        mock_get_registry.return_value = mock_agent_registry

        # Setup mock mapper to simulate new user (no existing instance)
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="new-instance-id")
        MockInstanceMapper.return_value = mock_mapper_instance
        
        # Call handle_message
        await registry._handle_message("test-source", msg)
        
        # Verify spawn_instance was called (through mapper)
        mock_mapper_instance.get_or_create_instance.assert_called_once()
        
        # Verify enqueue_message was called with the new instance
        mock_manager.enqueue_message.assert_called_once()
        assert mock_manager.enqueue_message.call_args.kwargs["instance_id"] == "new-instance-id"


@pytest.mark.asyncio
async def test_handle_message_uses_agent_dir_from_metadata(conn, mock_manager):
    """Test that agent_dir from message metadata is used when present."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    
    # Create a test message with agent_dir in metadata
    msg = IncomingMessage(
        external_user_id="user123",
        content="Hello",
        source_id="test-source",
        metadata={"message_id": "msg-003", "agent_dir": "/custom/agents"}
    )
    
    # Configure mock source repo - not a duplicate
    mock_manager._source_repo.check_and_mark_processed = MagicMock(return_value=False)
    
    # Mock InstanceMapper
    with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
         patch('daemon.sources.mapper.get_registry') as mock_get_registry:
        mock_agent_registry = MagicMock()
        mock_agent = MagicMock()
        mock_agent.path = "/custom/agents"
        mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
        mock_agent_registry.get = MagicMock(return_value=mock_agent)
        mock_get_registry.return_value = mock_agent_registry

        mock_mapper_instance = MagicMock()
        mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
        MockInstanceMapper.return_value = mock_mapper_instance
        
        await registry._handle_message("test-source", msg)
        
        # Verify get_or_create_instance was called with custom agent_dir
        mock_mapper_instance.get_or_create_instance.assert_called_once_with(
            source_id="test-source",
            external_user_id="user123",
            agent_id="/custom/agents",
            force_new=False,
            extra_mapping_metadata=None,
        )


@pytest.mark.asyncio
async def test_handle_message_uses_default_agent_dir(conn, mock_manager):
    """Test that default agent_dir is used when not in metadata."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    
    # Create a test message WITHOUT agent_dir in metadata
    msg = IncomingMessage(
        external_user_id="user123",
        content="Hello",
        source_id="test-source",
        metadata={"message_id": "msg-004"}  # No agent_dir
    )
    
    # Configure mock source repo - not a duplicate
    mock_manager._source_repo.check_and_mark_processed = MagicMock(return_value=False)
    
    # Mock InstanceMapper
    with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
         patch('daemon.sources.mapper.get_registry') as mock_get_registry:
        mock_agent_registry = MagicMock()
        mock_agent = MagicMock()
        mock_agent.path = "/default/agents/leader"
        mock_agent_registry.resolve_to_id = MagicMock(return_value="leader")
        mock_agent_registry.get = MagicMock(return_value=mock_agent)
        mock_get_registry.return_value = mock_agent_registry

        mock_mapper_instance = MagicMock()
        mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
        MockInstanceMapper.return_value = mock_mapper_instance
        
        await registry._handle_message("test-source", msg)
        
        # Verify get_or_create_instance was called with the default "leader" agent
        # under the configured base agents directory (not the bare base dir).
        mock_mapper_instance.get_or_create_instance.assert_called_once_with(
            source_id="test-source",
            external_user_id="user123",
            agent_id="/default/agents/leader",
            force_new=False,
            extra_mapping_metadata=None,
        )


# ==================== Additional Tests ====================


def test_list_adapters_includes_status_info(conn, mock_manager):
    """Test that list_adapters includes detailed status information."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
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
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
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
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    
    result = registry.get("nonexistent")
    
    assert result is None


# ==================== Autostart Delay Tests ====================


def _make_db_config(source_id, enabled=True, autostart=True, status="error"):
    """Build a mock SourceConfig row resembling the SQLModel object."""
    cfg = MagicMock()
    cfg.source_id = source_id
    cfg.source_type = "telegram"
    cfg.name = source_id
    cfg.config = {}
    cfg.credentials = None
    cfg.enabled = enabled
    cfg.autostart = autostart
    cfg.status = status
    cfg.error_message = None
    cfg.created_at = "2026-01-01T00:00:00"
    cfg.updated_at = "2026-01-01T00:00:00"
    return cfg


@pytest.mark.asyncio
async def test_start_all_schedules_delayed_autostart(mock_manager):
    """start_all schedules a delayed task (does not start immediately)."""
    repo = mock_manager._source_repo
    repo.list_source_configs = MagicMock(
        return_value=[_make_db_config("src-1", enabled=True, autostart=True)]
    )

    registry = SourceRegistry(repo, mock_manager)
    registry.AUTOSTART_DELAY_SECONDS = 60.0
    registry._create_adapter_from_config = AsyncMock(return_value=MagicMock())
    registry.start_adapter = AsyncMock(return_value=True)

    await registry.start_all()

    # An autostart task should be pending, adapter NOT started yet
    assert "src-1" in registry._autostart_tasks
    assert not registry._autostart_tasks["src-1"].done()
    registry.start_adapter.assert_not_called()

    # Cleanup
    for t in list(registry._autostart_tasks.values()):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_start_all_skips_non_autostart_source(mock_manager):
    """Sources with autostart=False are not scheduled."""
    repo = mock_manager._source_repo
    repo.list_source_configs = MagicMock(
        return_value=[_make_db_config("src-1", enabled=True, autostart=False)]
    )

    registry = SourceRegistry(repo, mock_manager)
    registry._create_adapter_from_config = AsyncMock(return_value=MagicMock())

    await registry.start_all()

    assert "src-1" not in registry._autostart_tasks
    # Adapter should not even be created for non-autostart sources
    registry._create_adapter_from_config.assert_not_called()


@pytest.mark.asyncio
async def test_start_all_skips_disabled_source(mock_manager):
    """Disabled sources are not scheduled even if autostart=True."""
    repo = mock_manager._source_repo
    repo.list_source_configs = MagicMock(
        return_value=[_make_db_config("src-1", enabled=False, autostart=True)]
    )

    registry = SourceRegistry(repo, mock_manager)
    registry._create_adapter_from_config = AsyncMock(return_value=MagicMock())

    await registry.start_all()

    assert "src-1" not in registry._autostart_tasks
    registry._create_adapter_from_config.assert_not_called()


@pytest.mark.asyncio
async def test_start_all_skips_stopped_source(mock_manager):
    """Manually-stopped sources are not scheduled even if autostart=True."""
    repo = mock_manager._source_repo
    repo.list_source_configs = MagicMock(
        return_value=[_make_db_config("src-1", enabled=True, autostart=True, status="stopped")]
    )

    registry = SourceRegistry(repo, mock_manager)
    registry._create_adapter_from_config = AsyncMock(return_value=MagicMock())

    await registry.start_all()

    assert "src-1" not in registry._autostart_tasks
    registry._create_adapter_from_config.assert_not_called()


@pytest.mark.asyncio
async def test_delayed_start_starts_adapter_after_delay(mock_manager):
    """After the delay elapses, the adapter is started."""
    repo = mock_manager._source_repo
    repo.list_source_configs = MagicMock(
        return_value=[_make_db_config("src-1", enabled=True, autostart=True)]
    )

    registry = SourceRegistry(repo, mock_manager)
    registry.AUTOSTART_DELAY_SECONDS = 0  # no wait for the test
    mock_adapter = MagicMock()
    mock_adapter.source_id = "src-1"
    mock_adapter.source_type = "telegram"
    registry._create_adapter_from_config = AsyncMock(return_value=mock_adapter)
    registry.start_adapter = AsyncMock(return_value=True)

    await registry.start_all()

    # Let the scheduled task run
    task = registry._autostart_tasks["src-1"]
    await task

    registry.start_adapter.assert_awaited_once_with("src-1")
    assert "src-1" not in registry._autostart_tasks


@pytest.mark.asyncio
async def test_stop_all_cancels_pending_autostart(mock_manager):
    """stop_all cancels pending delayed autostart tasks."""
    repo = mock_manager._source_repo
    repo.list_source_configs = MagicMock(
        return_value=[_make_db_config("src-1", enabled=True, autostart=True)]
    )

    registry = SourceRegistry(repo, mock_manager)
    registry.AUTOSTART_DELAY_SECONDS = 60.0
    registry._create_adapter_from_config = AsyncMock(return_value=MagicMock())
    registry.start_adapter = AsyncMock(return_value=True)

    await registry.start_all()
    assert "src-1" in registry._autostart_tasks

    await registry.stop_all()

    assert "src-1" not in registry._autostart_tasks
    assert registry._autostart_tasks == {}


@pytest.mark.asyncio
async def test_delayed_start_skipped_during_shutdown(mock_manager):
    """If registry is stopping when delay elapses, adapter is not started."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    registry.AUTOSTART_DELAY_SECONDS = 0
    registry._stopping = True
    registry.start_adapter = AsyncMock(return_value=True)
    registry.register(MagicMock())

    await registry._delayed_start("test-source")

    registry.start_adapter.assert_not_called()


@pytest.mark.asyncio
async def test_stop_adapter_cancels_pending_autostart(mock_manager):
    """Manually stopping a source cancels its pending autostart task."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    registry.AUTOSTART_DELAY_SECONDS = 60.0

    config = SourceConfig("src-1", "telegram", "Test", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    registry.register(adapter)
    registry._schedule_autostart("src-1")
    assert "src-1" in registry._autostart_tasks

    await registry.stop_adapter("src-1")

    assert "src-1" not in registry._autostart_tasks
    assert registry._autostart_tasks == {}


@pytest.mark.asyncio
async def test_unregister_cancels_pending_autostart(mock_manager):
    """Unregistering (delete) a source cancels its pending autostart task."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    registry.AUTOSTART_DELAY_SECONDS = 60.0

    config = SourceConfig("src-1", "telegram", "Test", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    registry.register(adapter)
    registry._schedule_autostart("src-1")
    assert "src-1" in registry._autostart_tasks

    registry.unregister("src-1")

    assert "src-1" not in registry._autostart_tasks


@pytest.mark.asyncio
async def test_stop_adapter_persists_status_by_default(mock_manager):
    """Explicit stop_adapter (user-initiated) persists status=stopped to DB."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    registry._source_repo.update_source_status = MagicMock()

    config = SourceConfig("src-1", "telegram", "Test", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    registry.register(adapter)

    await registry.stop_adapter("src-1")

    registry._source_repo.update_source_status.assert_called_with("src-1", "stopped")


@pytest.mark.asyncio
async def test_stop_adapter_skips_persist_when_disabled(mock_manager):
    """stop_all path (persist_status=False) does not write status=stopped."""
    registry = SourceRegistry(mock_manager._source_repo, mock_manager)
    registry._source_repo.update_source_status = MagicMock()

    config = SourceConfig("src-1", "telegram", "Test", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    registry.register(adapter)

    await registry.stop_adapter("src-1", persist_status=False)

    registry._source_repo.update_source_status.assert_not_called()
    # In-memory status still flipped so supervisor exits cleanly
    assert adapter._status == SourceStatus.STOPPED


@pytest.mark.asyncio
async def test_stop_all_does_not_persist_stopped_status(mock_manager):
    """stop_all (daemon shutdown) leaves DB status untouched for running sources.

    This ensures that a daemon restart does not silently mark previously-running
    sources as manually stopped, so they auto-start on the next boot.
    """
    repo = mock_manager._source_repo
    repo.update_source_status = MagicMock()

    registry = SourceRegistry(repo, mock_manager)
    registry._source_repo = repo
    config = SourceConfig("src-1", "telegram", "Test", {}, {})
    adapter = MockMessageAdapter(config, lambda msg: None)
    registry.register(adapter)
    # Simulate a running supervisor task so stop_all picks it up
    registry._supervisor_tasks["src-1"] = asyncio.create_task(asyncio.sleep(60))

    await registry.stop_all()

    # No 'stopped' status should have been written to the DB
    for call in repo.update_source_status.call_args_list:
        assert call.args[1] != "stopped", f"stop_all wrote stopped to DB: {call}"
    assert registry._stopping is True

