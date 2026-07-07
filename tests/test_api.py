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
    manager.spawn_instance_with_mcp = AsyncMock(return_value="test-instance-id")
    # Phase 3: routers check manager.is_write_paused; Mock auto-attr is truthy → 503.
    manager.is_write_paused = False
    manager.get_instance = AsyncMock()
    manager.send_message = Mock(return_value="Test response")
    manager.terminate_instance = AsyncMock(return_value=True)
    manager.pause_instance_cascade = AsyncMock(return_value={
        "paused_ids": ["test-instance"],
        "skipped_ids": []
    })
    manager.list_instances = Mock(return_value=([
        {
            "instance_id": "instance-1",
            "agent_id": "developer",
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
        "agent_id": "developer",
        "agent_dir": "/path/to/agent",
        "status": "running",
        "parent_id": None,
        "children": [],
        "project_id": None,
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    })
    manager.get_queue_stats = AsyncMock(return_value={
        "pending_count": 0,
        "processing_count": 0,
        "oldest_message_age_seconds": None
    })
    # Mock async enqueue_message (job queue path - used by router)
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
    # All calls use asyncio.to_thread so sync mocks work
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
    # Import app and set manager on app.state (Phase 3: routers use request.app.state.manager)
    from daemon.api import app
    from unittest.mock import Mock
    app.state.manager = mock_manager
    app.state.start_time = 1000.0
    # Mock credential_manager for source endpoints
    app.state.credential_manager = Mock()
    return mock_manager


@pytest_asyncio.fixture
async def client(app_with_mock_manager):
    """Create async test client."""
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
    assert data["version"] == "0.7.10"


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
    # Response echoes the mock manager's get_instance_info() payload, which
    # is hardcoded with agent_id="developer" in the test fixture (mock data,
    # unaffected by the validator alias).
    assert data["agent_id"] == "developer"
    # InstanceCreate validator normalizes the request agent_id "coder" ->
    # "developer" via the AGENT_ID_ALIASES alias before dispatch.
    mock_manager.spawn_instance_with_mcp.assert_called_once_with(
        agent_id="developer",
        instance_id="550e8400-e29b-41d4-a716-446655440000",
        project_id=None,
    )


@pytest.mark.asyncio
async def test_create_instance_with_project_id(client, mock_manager):
    """Test POST /instances with project_id is passed through correctly."""
    response = await client.post(
        "/instances",
        json={
            "agent_id": "coder",
            "project_id": "test-project-123"
        }
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["instance_id"] == "test-instance-id"
    # Verify call: router generates instance_id when none provided
    call_kwargs = mock_manager.spawn_instance_with_mcp.call_args.kwargs
    assert call_kwargs["agent_id"] == "developer"
    assert call_kwargs["project_id"] == "test-project-123"
    assert call_kwargs["instance_id"] is not None  # Router generates UUID


@pytest.mark.asyncio
async def test_get_instance_returns_project_id(client, mock_manager):
    """Test GET /instances/{id} returns project_id in response."""
    # Configure mock to return project_id
    mock_manager.get_instance_info.return_value = {
        "instance_id": "test-instance-id",
        "agent_id": "developer",
        "agent_dir": "/path/to/agent",
        "status": "running",
        "parent_id": None,
        "children": [],
        "project_id": "test-project-123",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    }
    
    response = await client.get("/instances/test-instance-id")
    
    assert response.status_code == 200
    data = response.json()
    assert data["instance_id"] == "test-instance-id"
    assert data["project_id"] == "test-project-123"


@pytest.mark.asyncio
async def test_project_id_roundtrip(client, mock_manager):
    """Test full roundtrip: POST with project_id -> GET returns same project_id."""
    # Configure mock to echo back the project_id from spawn call
    created_instance_id = None
    
    def mock_spawn(agent_id, instance_id, project_id):
        nonlocal created_instance_id
        created_instance_id = instance_id or "generated-instance-id"
        return created_instance_id
    
    def mock_get_info(instance_id):
        return {
            "instance_id": created_instance_id,
            "agent_id": "developer",
            "agent_dir": "/path/to/agent",
            "status": "running",
            "parent_id": None,
            "children": [],
            "project_id": "test-project-123",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00"
        }
    
    mock_manager.spawn_instance_with_mcp.side_effect = mock_spawn
    mock_manager.spawn_instance.side_effect = mock_spawn
    mock_manager.get_instance_info.side_effect = mock_get_info
    
    # Create instance with project_id
    create_response = await client.post(
        "/instances",
        json={
            "agent_id": "developer",
            "project_id": "test-project-123"
        }
    )
    
    assert create_response.status_code == 201
    create_data = create_response.json()
    assert create_data["project_id"] == "test-project-123"
    
    # Get instance by ID
    get_response = await client.get(f"/instances/{created_instance_id}")
    
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["project_id"] == "test-project-123"


@pytest.mark.asyncio
async def test_create_instance_max_limit(client, mock_manager):
    """Test POST /instances with max instances exceeded."""
    # Configure mock to raise ValueError (as the real manager does)
    mock_manager.spawn_instance_with_mcp.side_effect = ValueError(
        "Max instances limit reached: 5"
    )
    mock_manager.spawn_instance.side_effect = ValueError(
        "Max instances limit reached: 5"
    )
    
    response = await client.post(
        "/instances",
        json={
            "agent_id": "developer"
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
async def test_list_instances_no_project_id_filter(client, mock_manager):
    """Test GET /instances without project_id filter returns all instances."""
    response = await client.get("/instances")
    
    assert response.status_code == 200
    data = response.json()
    assert "instances" in data
    assert len(data["instances"]) == 1
    # Verify manager was called with project_id=None
    mock_manager.list_instances.assert_called_once_with(
        limit=10, offset=0, project_id=None, exclude_kb=True, include_descendants=True
    )


@pytest.mark.asyncio
async def test_list_instances_filter_by_project_id(client, mock_manager):
    """Test GET /instances?project_id=test-project-123 filters correctly."""
    # Configure mock to return instances for a specific project
    mock_manager.list_instances.return_value = ([
        {
            "instance_id": "instance-project-1",
            "agent_id": "developer",
            "agent_dir": "/path/to/agent1",
            "status": "running",
            "parent_id": None,
            "children": [],
            "project_id": "test-project-123",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00"
        }
    ], 1)
    
    response = await client.get("/instances?project_id=test-project-123")
    
    assert response.status_code == 200
    data = response.json()
    assert "instances" in data
    assert len(data["instances"]) == 1
    assert data["instances"][0]["instance_id"] == "instance-project-1"
    assert data["instances"][0]["project_id"] == "test-project-123"
    # Verify manager was called with the correct project_id
    mock_manager.list_instances.assert_called_once_with(
        limit=10, offset=0, project_id="test-project-123", exclude_kb=True, include_descendants=True
    )


@pytest.mark.asyncio
async def test_list_instances_filter_by_nonexistent_project_id(client, mock_manager):
    """Test GET /instances?project_id=nonexistent returns empty list."""
    mock_manager.list_instances.return_value = ([], 0)
    
    response = await client.get("/instances?project_id=nonexistent")
    
    assert response.status_code == 200
    data = response.json()
    assert "instances" in data
    assert len(data["instances"]) == 0
    assert data["total"] == 0
    # Verify manager was called with the nonexistent project_id
    mock_manager.list_instances.assert_called_once_with(
        limit=10, offset=0, project_id="nonexistent", exclude_kb=True, include_descendants=True
    )


@pytest.mark.asyncio
async def test_list_instances_project_id_with_status_filter(client, mock_manager):
    """Test GET /instances?project_id=xxx&status=running passes both filters."""
    mock_manager.list_instances.return_value = ([
        {
            "instance_id": "instance-running-1",
            "agent_id": "developer",
            "agent_dir": "/path/to/agent1",
            "status": "running",
            "parent_id": None,
            "children": [],
            "project_id": "test-project",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00"
        }
    ], 1)
    
    # Note: The status filter is part of the query string but project_id is what we test here
    response = await client.get("/instances?project_id=test-project&status=running")
    
    assert response.status_code == 200
    data = response.json()
    assert "instances" in data
    assert len(data["instances"]) == 1
    # Verify project_id was passed to manager
    mock_manager.list_instances.assert_called_once_with(
        limit=10, offset=0, project_id="test-project", exclude_kb=True, include_descendants=True
    )


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
    mock_manager.get_instance.return_value = AsyncMock()  # Instance exists
    
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
async def test_pause_instance_endpoint_exists(client, mock_manager):
    """Test that POST /instances/{instance_id}/pause endpoint works."""
    # Test 1: Instance not found
    mock_manager.get_instance.side_effect = KeyError("Instance not found")

    response = await client.post("/instances/nonexistent/pause")
    assert response.status_code == 404
    data = response.json()
    assert data["detail"]["code"] == "INSTANCE_NOT_FOUND"

    # Test 2: Instance exists - should return cascade pause result
    mock_manager.get_instance.side_effect = None
    mock_manager.get_instance.return_value = AsyncMock()

    # Mock cascade pause with children
    mock_manager.pause_instance_cascade = AsyncMock(return_value={
        "paused_ids": ["test-instance", "child-1", "child-2"],
        "skipped_ids": []
    })

    response = await client.post("/instances/test-instance/pause")
    assert response.status_code == 200
    data = response.json()
    assert data["paused"] == True
    assert "paused_ids" in data
    assert "skipped_ids" in data
    assert data["paused_ids"] == ["test-instance", "child-1", "child-2"]
    assert data["skipped_ids"] == []

    # Verify pause_instance_cascade was called
    mock_manager.pause_instance_cascade.assert_called_once_with("test-instance")


@pytest.mark.asyncio
async def test_stop_instance_deprecated_alias(client, mock_manager):
    """Test that deprecated POST /instances/{instance_id}/stop delegates to pause logic."""
    # Instance exists - should return same result as /pause endpoint
    mock_manager.get_instance.return_value = AsyncMock()

    # Mock cascade pause with children
    mock_manager.pause_instance_cascade = AsyncMock(return_value={
        "paused_ids": ["test-instance", "child-1"],
        "skipped_ids": []
    })

    response = await client.post("/instances/test-instance/stop")
    
    assert response.status_code == 200
    data = response.json()
    assert data["paused"] == True
    assert "paused_ids" in data
    assert "skipped_ids" in data
    assert data["paused_ids"] == ["test-instance", "child-1"]
    assert data["skipped_ids"] == []

    # Verify pause_instance_cascade was called (proves delegation to pause logic)
    mock_manager.pause_instance_cascade.assert_called_once_with("test-instance")


# ==================== Resume Instance Tests ====================


@pytest.mark.asyncio
async def test_resume_instance_not_found(client, mock_manager):
    """Test POST /instances/{instance_id}/resume with non-existent instance."""
    mock_manager.get_instance.side_effect = KeyError("Instance not found: invalid-id")
    
    response = await client.post("/instances/invalid-id/resume")
    
    assert response.status_code == 404
    data = response.json()
    assert data["detail"]["code"] == "INSTANCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_resume_instance_with_default_message(client, mock_manager):
    """Test resuming with no body sends default 'resume' message."""
    mock_manager.get_instance.return_value = AsyncMock()
    mock_manager.resume_instance_cascade = AsyncMock(return_value={
        "resumed_ids": ["test-instance-id"],
        "skipped_ids": []
    })
    mock_manager.resume_processing_job = AsyncMock(return_value={
        "job_id": "job-123",
        "message_id": "msg-resume-123"
    })

    # No body - should use default "resume" message
    response = await client.post("/instances/test-instance-id/resume")
    
    assert response.status_code == 200
    data = response.json()
    assert data["resumed"] is True
    assert data["resumed_ids"] == ["test-instance-id"]
    assert data["skipped_ids"] == []
    assert data["resume_results"]["test-instance-id"]["message_id"] == "msg-resume-123"
    
    # Verify resume_processing_job was called with default message and silent=False for target
    mock_manager.resume_processing_job.assert_called_once_with(
        "test-instance-id",
        message="resume",
        silent=False,
    )


@pytest.mark.asyncio
async def test_resume_instance_with_custom_message(client, mock_manager):
    """Test resuming with custom message body."""
    mock_manager.get_instance.return_value = AsyncMock()
    mock_manager.resume_instance_cascade = AsyncMock(return_value={
        "resumed_ids": ["test-instance-id"],
        "skipped_ids": []
    })
    mock_manager.resume_processing_job = AsyncMock(return_value={
        "job_id": "job-456",
        "message_id": "msg-custom-456"
    })

    response = await client.post(
        "/instances/test-instance-id/resume",
        json={"message": "hello world"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["resumed"] is True
    assert data["resume_results"]["test-instance-id"]["message_id"] == "msg-custom-456"
    
    # Verify custom message was passed through with silent=False for target
    mock_manager.resume_processing_job.assert_called_once_with(
        "test-instance-id",
        message="hello world",
        silent=False,
    )


@pytest.mark.asyncio
async def test_resume_instance_with_whitespace_message_uses_default(client, mock_manager):
    """Test resuming with whitespace-only message falls back to default 'resume'."""
    mock_manager.get_instance.return_value = AsyncMock()
    mock_manager.resume_instance_cascade = AsyncMock(return_value={
        "resumed_ids": ["test-instance-id"],
        "skipped_ids": []
    })
    mock_manager.resume_processing_job = AsyncMock(return_value={
        "job_id": "job-789",
        "message_id": "msg-default-789"
    })

    # Whitespace-only message should be stripped to empty, then default to "resume"
    response = await client.post(
        "/instances/test-instance-id/resume",
        json={"message": "   "}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["resumed"] is True
    assert data["resume_results"]["test-instance-id"]["message_id"] == "msg-default-789"
    
    # Verify default message was used after stripping with silent=False for target
    mock_manager.resume_processing_job.assert_called_once_with(
        "test-instance-id",
        message="resume",
        silent=False,
    )


@pytest.mark.asyncio
async def test_resume_instance_already_running_no_message_enqueued(client, mock_manager):
    """Test resuming an already-running instance skips message enqueue."""
    mock_manager.get_instance.return_value = AsyncMock()
    # Instance is in skipped_ids (already running, not paused)
    mock_manager.resume_instance_cascade = AsyncMock(return_value={
        "resumed_ids": [],
        "skipped_ids": ["test-instance-id"]
    })

    response = await client.post("/instances/test-instance-id/resume")
    
    assert response.status_code == 200
    data = response.json()
    assert data["resumed"] is True
    assert data["resumed_ids"] == []
    assert data["skipped_ids"] == ["test-instance-id"]
    assert data["resume_results"] == {}  # No jobs resumed since none were paused
    
    # Verify resume_processing_job was NOT called
    mock_manager.resume_processing_job.assert_not_called()


@pytest.mark.asyncio
async def test_resume_instance_response_includes_message_id(client, mock_manager):
    """Test that resume response includes message_id when message is enqueued."""
    mock_manager.get_instance.return_value = AsyncMock()
    mock_manager.resume_instance_cascade = AsyncMock(return_value={
        "resumed_ids": ["test-instance-id", "child-1"],
        "skipped_ids": []
    })
    
    expected_msg_id = "msg-from-resume-001"
    mock_manager.resume_processing_job = AsyncMock(return_value={
        "job_id": "job-001",
        "message_id": expected_msg_id
    })

    response = await client.post(
        "/instances/test-instance-id/resume",
        json={"message": "continue work"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["resume_results"]["test-instance-id"]["message_id"] == expected_msg_id


@pytest.mark.asyncio
async def test_resume_instance_cascade_target_vs_children(client, mock_manager):
    """Test that target instance gets silent=False but children get silent=True."""
    mock_manager.get_instance.return_value = AsyncMock()
    mock_manager.resume_instance_cascade = AsyncMock(return_value={
        "resumed_ids": ["test-instance-id", "child-1", "child-2"],
        "skipped_ids": []
    })
    mock_manager.resume_processing_job = AsyncMock(return_value={
        "job_id": "job-resume",
        "message_id": "msg-resume"
    })

    response = await client.post(
        "/instances/test-instance-id/resume",
        json={"message": "continue"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["resumed"] is True
    assert len(data["resumed_ids"]) == 3

    # Verify resume_processing_job was called 3 times (once per instance)
    assert mock_manager.resume_processing_job.call_count == 3

    # Check that the calls were made with correct parameters
    calls = mock_manager.resume_processing_job.call_args_list
    call_instances = {call[0][0]: call for call in calls}

    # Target instance: gets custom message, silent=False
    target_call = call_instances["test-instance-id"]
    assert target_call[1]["message"] == "continue"
    assert target_call[1]["silent"] is False

    # Children: get default message, silent=True
    for child_id in ["child-1", "child-2"]:
        child_call = call_instances[child_id]
        assert child_call[1]["message"] == "resume"
        assert child_call[1]["silent"] is True


@pytest.mark.asyncio
async def test_resume_instance_backward_compatibility_no_body(client, mock_manager):
    """Test resume endpoint works without any request body (backward compatibility)."""
    mock_manager.get_instance.return_value = AsyncMock()
    mock_manager.resume_instance_cascade = AsyncMock(return_value={
        "resumed_ids": ["test-instance-id"],
        "skipped_ids": []
    })
    mock_manager.resume_processing_job = AsyncMock(return_value={
        "job_id": "job-backward",
        "message_id": "msg-backward-compat"
    })

    # Send request without any body (using content='' to ensure no JSON)
    response = await client.post(
        "/instances/test-instance-id/resume",
        content=b"",
        headers={"content-type": "application/json"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["resumed"] is True
    assert data["resume_results"]["test-instance-id"]["message_id"] == "msg-backward-compat"
    
    # Verify default "resume" message was sent with silent=False for target
    mock_manager.resume_processing_job.assert_called_once_with(
        "test-instance-id",
        message="resume",
        silent=False,
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
        source="api",
        images=None,
    )


@pytest.mark.asyncio
async def test_send_message_instance_not_found(client, mock_manager):
    """Test POST /instances/{id}/messages with invalid id."""
    mock_manager.get_instance_info.side_effect = KeyError("Instance not found: invalid-id")
    
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
    mock_manager.get_instance.return_value = AsyncMock()
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
        agent_dir="./agents/developer",
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
