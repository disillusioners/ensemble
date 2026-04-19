"""Tests for daemon/api.py"""

import pytest
import pytest_asyncio
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime

import httpx
from fastapi import FastAPI

# Import the app and manager directly
from daemon import api as api_module


@pytest_asyncio.fixture
async def mock_manager():
    """Create a mock InstanceManager with proper cleanup."""
    import sqlite3
    import tempfile
    import os
    
    manager = Mock()
    manager.spawn_instance = Mock(return_value="test-instance-id")
    manager.get_instance = Mock()
    manager.send_message = Mock(return_value="Test response")
    manager.terminate_instance = AsyncMock(return_value=True)
    manager.list_instances = Mock(return_value=([
        {
            "instance_id": "instance-1",
            "agent_id": "coder",
            "agent_dir": "/path/to/agent1",
            "status": "running",
            "parent_id": None,
            "children": [],
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00"
        }
    ], 1))
    manager.get_instance_info = Mock(return_value={
        "instance_id": "test-instance-id",
        "agent_id": "coder",
        "agent_dir": "/path/to/agent",
        "status": "running",
        "parent_id": None,
        "children": [],
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    })
    # Mock async enqueue_message
    manager.enqueue_message = AsyncMock(return_value=Mock(
        message_id="test-message-id",
        instance_id="test-instance-id",
        status="queued"
    ))
    # Mock get_messages for message history (now async)
    manager.get_messages = AsyncMock(return_value=[])
    # Mock conn for source endpoints (temp SQLite file)
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db_path = temp_db.name
    temp_db.close()  # Close handle so sqlite3 can open it
    conn = sqlite3.connect(temp_db_path)
    # Create source tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_configs (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            name TEXT NOT NULL,
            config TEXT NOT NULL,
            credentials TEXT,
            enabled BOOLEAN DEFAULT TRUE,
            status TEXT DEFAULT 'stopped',
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS instance_mappings (
            mapping_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            agent_instance_id TEXT NOT NULL,
            agent_dir TEXT NOT NULL,
            mapping_metadata TEXT,
            last_message_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, external_user_id)
        )
    """)
    conn.commit()
    manager.conn = conn
    manager._temp_db_path = temp_db_path  # Store for cleanup
    # Mock source_registry
    manager.source_registry = None
    
    # Mock _source_repository for source management tests
    created_sources = {}
    def mock_get_source(source_id):
        return created_sources.get(source_id)
    def mock_create_source(**kwargs):
        source = MagicMock()
        source.source_id = kwargs.get('source_id', 'test')
        source.source_type = kwargs.get('source_type', 'telegram')
        source.name = kwargs.get('name', 'Test')
        source.config = kwargs.get('config', {})
        source.enabled = kwargs.get('enabled', True)
        source.status = 'stopped'
        source.error_message = None
        source.created_at = '2024-01-01T00:00:00'
        source.updated_at = '2024-01-01T00:00:00'
        created_sources[source.source_id] = source
        return source
    def mock_update_source(**kwargs):
        source = MagicMock()
        source.source_id = kwargs.get('source_id', 'test')
        source.source_type = kwargs.get('source_type', 'telegram')
        source.name = kwargs.get('name', 'Test')
        source.config = kwargs.get('config', {})
        source.enabled = kwargs.get('enabled', True)
        source.status = 'stopped'
        source.error_message = None
        source.created_at = '2024-01-01T00:00:00'
        source.updated_at = '2024-01-01T00:00:00'
        created_sources[source.source_id] = source
        return source
    
    manager._source_repository = MagicMock()
    manager._source_repository.list_source_configs = MagicMock(return_value=[])
    manager._source_repository.get_source_config = MagicMock(side_effect=mock_get_source)
    manager._source_repository.create_source_config = MagicMock(side_effect=mock_create_source)
    manager._source_repository.update_source_config = MagicMock(side_effect=mock_update_source)
    def mock_delete_source(source_id):
        created_sources.pop(source_id, None)
        return True
    manager._source_repository.delete_source_config = MagicMock(side_effect=mock_delete_source)
    manager._source_repository.list_instance_mappings = MagicMock(return_value=[])
    
    yield manager
    # Cleanup: close connection and delete temp file
    try:
        conn.close()
    except Exception:
        pass
    try:
        os.unlink(temp_db_path)
    except Exception:
        pass


@pytest.fixture
def app_with_mock_manager(mock_manager):
    """Create FastAPI app with mocked manager."""
    # Patch the global manager
    with patch.object(api_module, 'manager', mock_manager), \
         patch.object(api_module, 'start_time', 1000.0):
        yield mock_manager


@pytest_asyncio.fixture
async def client(app_with_mock_manager):
    """Create async test client."""
    # Import app inside the fixture to ensure patches are applied
    from daemon.api import app
    
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_check(client):
    """Test GET /health."""
    response = await client.get("/health")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "uptime_seconds" in data
    assert data["version"] == "0.2.2"


@pytest.mark.asyncio
async def test_create_instance_success(client, mock_manager):
    """Test POST /instances."""
    response = await client.post(
        "/instances",
        json={
            "agent_id": "coder",
            "instance_id": "550e8400-e29b-41d4-a716-446655440000"
        }
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["instance_id"] == "test-instance-id"
    assert data["agent_id"] == "coder"
    mock_manager.spawn_instance.assert_called_once_with(
        agent_id="coder",
        instance_id="550e8400-e29b-41d4-a716-446655440000"
    )


@pytest.mark.asyncio
async def test_create_instance_max_limit(client, mock_manager):
    """Test POST /instances with max instances exceeded."""
    # Configure mock to raise ValueError (as the real manager does)
    mock_manager.spawn_instance.side_effect = ValueError(
        "Max instances limit reached: 5"
    )
    
    response = await client.post(
        "/instances",
        json={
            "agent_id": "coder"
        }
    )
    
    assert response.status_code == 429
    data = response.json()
    assert "detail" in data
    assert data["detail"]["code"] == "MAX_INSTANCES_EXCEEDED"


@pytest.mark.asyncio
async def test_list_instances(client, mock_manager):
    """Test GET /instances."""
    response = await client.get("/instances")
    
    assert response.status_code == 200
    data = response.json()
    assert "instances" in data
    assert len(data["instances"]) == 1
    assert data["instances"][0]["instance_id"] == "instance-1"
    mock_manager.list_instances.assert_called_once()


@pytest.mark.asyncio
async def test_get_instance_success(client, mock_manager):
    """Test GET /instances/{id}."""
    response = await client.get("/instances/test-instance-id")
    
    assert response.status_code == 200
    data = response.json()
    assert data["instance_id"] == "test-instance-id"
    mock_manager.get_instance_info.assert_called_once_with("test-instance-id")


@pytest.mark.asyncio
async def test_get_instance_not_found(client, mock_manager):
    """Test GET /instances/{id} with invalid id."""
    mock_manager.get_instance_info.side_effect = KeyError("Instance not found: invalid-id")
    
    response = await client.get("/instances/invalid-id")
    
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert data["detail"]["code"] == "INSTANCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_terminate_instance_success(client, mock_manager):
    """Test DELETE /instances/{id}."""
    mock_manager.get_instance.return_value = Mock()  # Instance exists
    
    response = await client.delete("/instances/test-instance-id")
    
    assert response.status_code == 200
    data = response.json()
    assert data["terminated"] is True
    mock_manager.terminate_instance.assert_called_once_with("test-instance-id")


@pytest.mark.asyncio
async def test_terminate_instance_not_found(client, mock_manager):
    """Test DELETE /instances/{id} with invalid id."""
    mock_manager.get_instance.side_effect = KeyError("Instance not found: invalid-id")
    
    response = await client.delete("/instances/invalid-id")
    
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert data["detail"]["code"] == "INSTANCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_stop_instance_endpoint_exists(client, mock_manager):
    """Test that POST /instances/{instance_id}/stop endpoint works."""
    # Test 1: Instance not found
    mock_manager.get_instance.side_effect = KeyError("Instance not found")
    
    response = await client.post("/instances/nonexistent/stop")
    assert response.status_code == 404
    data = response.json()
    assert data["detail"] == "Instance not found"
    
    # Test 2: Instance exists - should return success
    mock_manager.get_instance.side_effect = None
    mock_manager.get_instance.return_value = Mock()
    mock_manager.cancel_instance_requests = Mock(return_value=3)
    
    response = await client.post("/instances/test-instance/stop")
    assert response.status_code == 200
    data = response.json()
    assert data["stopped"] == True
    assert data["cancelled_requests"] == 3
    
    # Verify cancel_instance_requests was called with USER_STOPPED
    from daemon.cancellation import CancellationReason
    mock_manager.cancel_instance_requests.assert_called_once_with(
        "test-instance", 
        CancellationReason.USER_STOPPED
    )


@pytest.mark.asyncio
async def test_send_message_success(client, mock_manager):
    """Test POST /instances/{id}/messages."""
    response = await client.post(
        "/instances/test-instance-id/messages",
        json={
            "content": "Hello, agent!"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "message_id" in data
    assert data["role"] == "assistant"
    assert data["content"] == ""  # Response comes async via SSE
    mock_manager.enqueue_message.assert_called_once_with(
        instance_id="test-instance-id",
        message="Hello, agent!",
        source="api"
    )


@pytest.mark.asyncio
async def test_send_message_instance_not_found(client, mock_manager):
    """Test POST /instances/{id}/messages with invalid id."""
    mock_manager.get_instance.side_effect = KeyError("Instance not found: invalid-id")
    
    response = await client.post(
        "/instances/invalid-id/messages",
        json={
            "content": "Hello!"
        }
    )
    
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert data["detail"]["code"] == "INSTANCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_messages(client, mock_manager):
    """Test GET /instances/{id}/messages."""
    # Message history now returns empty list (TODO: implement from LangGraph checkpoints)
    response = await client.get("/instances/test-instance-id/messages")
    
    assert response.status_code == 200
    data = response.json()
    assert data == []  # Currently returns empty until checkpoint-based history is implemented


@pytest.mark.asyncio
async def test_global_exception_handler(client, mock_manager):
    """Test that exceptions return proper error response."""
    # Make enqueue_message raise an unexpected exception
    mock_manager.get_instance.return_value = Mock()
    mock_manager.enqueue_message.side_effect = RuntimeError("Unexpected error")
    
    response = await client.post(
        "/instances/test-instance-id/messages",
        json={
            "content": "Hello!"
        }
    )
    
    assert response.status_code == 500
    data = response.json()
    assert "detail" in data
    assert data["detail"]["code"] == "INTERNAL_ERROR"


# ==================== Source Management Tests ====================


@pytest.mark.asyncio
async def test_list_sources_empty(client, mock_manager):
    """Test GET /sources with no sources configured."""
    response = await client.get("/sources")
    
    assert response.status_code == 200
    data = response.json()
    assert "sources" in data
    assert data["sources"] == []


@pytest.mark.asyncio
async def test_create_source_success(client, mock_manager):
    """Test POST /sources creates a new source."""
    response = await client.post(
        "/sources",
        json={
            "source_id": "telegram-test",
            "source_type": "telegram",
            "name": "Test Telegram Bot",
            "config": {"polling_enabled": True},
            "credentials": {"bot_token": "test_token"},
            "enabled": True
        }
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["source_id"] == "telegram-test"
    assert data["source_type"] == "telegram"
    assert data["name"] == "Test Telegram Bot"
    assert data["status"] == "stopped"


@pytest.mark.asyncio
async def test_create_source_duplicate(client, mock_manager):
    """Test POST /sources rejects duplicate source_id."""
    # Create first source
    await client.post(
        "/sources",
        json={
            "source_id": "telegram-test",
            "source_type": "telegram",
            "name": "Test Bot",
            "config": {},
            "credentials": {},
            "enabled": True
        }
    )
    
    # Try to create duplicate
    response = await client.post(
        "/sources",
        json={
            "source_id": "telegram-test",
            "source_type": "telegram",
            "name": "Another Bot",
            "config": {},
            "credentials": {},
            "enabled": True
        }
    )
    
    assert response.status_code == 409
    data = response.json()
    assert data["detail"]["code"] == "SOURCE_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_get_source_success(client, mock_manager):
    """Test GET /sources/{source_id} returns source info."""
    # Create a source first
    await client.post(
        "/sources",
        json={
            "source_id": "telegram-test",
            "source_type": "telegram",
            "name": "Test Bot",
            "config": {},
            "credentials": {},
            "enabled": True
        }
    )
    
    response = await client.get("/sources/telegram-test")
    
    assert response.status_code == 200
    data = response.json()
    assert data["source_id"] == "telegram-test"
    assert data["source_type"] == "telegram"


@pytest.mark.asyncio
async def test_get_source_not_found(client, mock_manager):
    """Test GET /sources/{source_id} with invalid id."""
    response = await client.get("/sources/nonexistent")
    
    assert response.status_code == 404
    data = response.json()
    assert data["detail"]["code"] == "SOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_update_source_success(client, mock_manager):
    """Test PUT /sources/{source_id} updates source."""
    # Create a source first
    await client.post(
        "/sources",
        json={
            "source_id": "telegram-test",
            "source_type": "telegram",
            "name": "Test Bot",
            "config": {},
            "credentials": {},
            "enabled": True
        }
    )
    
    response = await client.put(
        "/sources/telegram-test",
        json={
            "name": "Updated Bot Name",
            "enabled": False
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Bot Name"
    assert data["enabled"] is False


@pytest.mark.asyncio
async def test_delete_source_success(client, mock_manager):
    """Test DELETE /sources/{source_id} deletes source."""
    # Create a source first
    await client.post(
        "/sources",
        json={
            "source_id": "telegram-test",
            "source_type": "telegram",
            "name": "Test Bot",
            "config": {},
            "credentials": {},
            "enabled": True
        }
    )
    
    response = await client.delete("/sources/telegram-test")
    
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] is True
    
    # Verify it's gone
    get_response = await client.get("/sources/telegram-test")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_create_source_invalid_id(client, mock_manager):
    """Test POST /sources rejects invalid source_id format."""
    response = await client.post(
        "/sources",
        json={
            "source_id": "invalid:source/id!",  # Contains invalid characters
            "source_type": "telegram",
            "name": "Test Bot",
            "config": {},
            "credentials": {},
            "enabled": True
        }
    )
    
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_delete_source_cascades_to_mappings(client, mock_manager):
    """Test DELETE /sources/{source_id} also deletes associated mappings."""
    from daemon.sources.persistence import save_instance_mapping
    
    # Create a source
    await client.post(
        "/sources",
        json={
            "source_id": "telegram-test",
            "source_type": "telegram",
            "name": "Test Bot",
            "config": {},
            "credentials": {},
            "enabled": True
        }
    )
    
    # Create a mapping directly in DB (since endpoint requires instance spawning)
    save_instance_mapping(
        conn=mock_manager.conn,
        mapping_id="telegram-test:123456",
        source_id="telegram-test",
        external_user_id="123456",
        agent_instance_id="instance-abc",
        agent_dir="./agents/coder",
    )
    
    # Delete the source
    response = await client.delete("/sources/telegram-test")
    assert response.status_code == 200
    
    # Verify mappings are gone (source deleted, so 404)
    get_response = await client.get("/sources/telegram-test/mappings")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_list_mappings_empty(client, mock_manager):
    """Test GET /sources/{source_id}/mappings with no mappings."""
    # Create a source first
    await client.post(
        "/sources",
        json={
            "source_id": "telegram-test",
            "source_type": "telegram",
            "name": "Test Bot",
            "config": {},
            "credentials": {},
            "enabled": True
        }
    )
    
    response = await client.get("/sources/telegram-test/mappings")
    
    assert response.status_code == 200
    data = response.json()
    assert "mappings" in data
    assert data["mappings"] == []


@pytest.mark.asyncio
async def test_start_source_no_registry(client, mock_manager):
    """Test POST /sources/{source_id}/start when registry not available."""
    # Create a source first
    await client.post(
        "/sources",
        json={
            "source_id": "telegram-test",
            "source_type": "telegram",
            "name": "Test Bot",
            "config": {},
            "credentials": {},
            "enabled": True
        }
    )
    
    # mock_manager.source_registry is None
    response = await client.post("/sources/telegram-test/start")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stopped"
    assert "not available" in data["message"]


@pytest.mark.asyncio
async def test_stop_source_no_registry(client, mock_manager):
    """Test POST /sources/{source_id}/stop when registry not available."""
    # Create a source first
    await client.post(
        "/sources",
        json={
            "source_id": "telegram-test",
            "source_type": "telegram",
            "name": "Test Bot",
            "config": {},
            "credentials": {},
            "enabled": True
        }
    )
    
    response = await client.post("/sources/telegram-test/stop")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stopped"


@pytest.mark.asyncio
async def test_webhook_source_not_found(client, mock_manager):
    """Test POST /webhooks/{source_id} with invalid source."""
    response = await client.post(
        "/webhooks/nonexistent",
        json={"update_id": 1, "message": {}}
    )
    
    assert response.status_code == 404
    data = response.json()
    assert data["detail"]["code"] == "SOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_webhook_registry_not_available(client, mock_manager):
    """Test POST /webhooks/{source_id} when registry not available."""
    # Create a source first
    await client.post(
        "/sources",
        json={
            "source_id": "telegram-test",
            "source_type": "telegram",
            "name": "Test Bot",
            "config": {},
            "credentials": {},
            "enabled": True
        }
    )
    
    response = await client.post(
        "/webhooks/telegram-test",
        json={"update_id": 1, "message": {"text": "hello"}}
    )
    
    assert response.status_code == 503
    data = response.json()
    assert data["detail"]["code"] == "INTERNAL_ERROR"
    assert "registry not available" in data["detail"]["message"].lower()
