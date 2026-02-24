"""Tests for daemon/api.py"""

import pytest
import pytest_asyncio
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

import httpx
from fastapi import FastAPI

# Import the app and manager directly
from daemon import api as api_module


@pytest_asyncio.fixture
async def mock_manager():
    """Create a mock SessionManager."""
    manager = Mock()
    manager.spawn_session = Mock(return_value="test-session-id")
    manager.get_session = Mock()
    manager.send_message = Mock(return_value="Test response")
    manager.terminate_session = Mock(return_value=True)
    manager.list_sessions = Mock(return_value=[
        {
            "session_id": "session-1",
            "agent_dir": "/path/to/agent1",
            "status": "running",
            "parent_id": None,
            "children": [],
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00"
        }
    ])
    manager.get_session_info = Mock(return_value={
        "session_id": "test-session-id",
        "agent_dir": "/path/to/agent",
        "status": "running",
        "parent_id": None,
        "children": [],
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    })
    return manager


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
    
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_check(client):
    """Test GET /health."""
    response = await client.get("/health")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "uptime_seconds" in data
    assert data["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_create_session_success(client, mock_manager):
    """Test POST /sessions."""
    response = await client.post(
        "/sessions",
        json={
            "agent_dir": "/path/to/agent",
            "session_id": "custom-session-id"
        }
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["session_id"] == "test-session-id"
    assert data["agent_dir"] == "/path/to/agent"
    mock_manager.spawn_session.assert_called_once_with(
        agent_dir="/path/to/agent",
        session_id="custom-session-id"
    )


@pytest.mark.asyncio
async def test_create_session_max_limit(client, mock_manager):
    """Test POST /sessions with max sessions exceeded."""
    # Configure mock to raise ValueError (as the real manager does)
    mock_manager.spawn_session.side_effect = ValueError(
        "Max sessions limit reached: 5"
    )
    
    response = await client.post(
        "/sessions",
        json={
            "agent_dir": "/path/to/agent"
        }
    )
    
    assert response.status_code == 429
    data = response.json()
    assert "detail" in data
    assert data["detail"]["code"] == "MAX_SESSIONS_EXCEEDED"


@pytest.mark.asyncio
async def test_list_sessions(client, mock_manager):
    """Test GET /sessions."""
    response = await client.get("/sessions")
    
    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["session_id"] == "session-1"
    mock_manager.list_sessions.assert_called_once()


@pytest.mark.asyncio
async def test_get_session_success(client, mock_manager):
    """Test GET /sessions/{id}."""
    response = await client.get("/sessions/test-session-id")
    
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "test-session-id"
    mock_manager.get_session_info.assert_called_once_with("test-session-id")


@pytest.mark.asyncio
async def test_get_session_not_found(client, mock_manager):
    """Test GET /sessions/{id} with invalid id."""
    mock_manager.get_session_info.side_effect = KeyError("Session not found: invalid-id")
    
    response = await client.get("/sessions/invalid-id")
    
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert data["detail"]["code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_terminate_session_success(client, mock_manager):
    """Test DELETE /sessions/{id}."""
    mock_manager.get_session.return_value = Mock()  # Session exists
    
    response = await client.delete("/sessions/test-session-id")
    
    assert response.status_code == 200
    data = response.json()
    assert data["terminated"] is True
    mock_manager.terminate_session.assert_called_once_with("test-session-id")


@pytest.mark.asyncio
async def test_terminate_session_not_found(client, mock_manager):
    """Test DELETE /sessions/{id} with invalid id."""
    mock_manager.get_session.side_effect = KeyError("Session not found: invalid-id")
    
    response = await client.delete("/sessions/invalid-id")
    
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert data["detail"]["code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_send_message_success(client, mock_manager):
    """Test POST /sessions/{id}/messages."""
    response = await client.post(
        "/sessions/test-session-id/messages",
        json={
            "content": "Hello, agent!"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "message_id" in data
    assert data["role"] == "assistant"
    assert data["content"] == "Test response"
    mock_manager.send_message.assert_called_once_with(
        "test-session-id", "Hello, agent!"
    )


@pytest.mark.asyncio
async def test_send_message_session_not_found(client, mock_manager):
    """Test POST /sessions/{id}/messages with invalid id."""
    mock_manager.get_session.side_effect = KeyError("Session not found: invalid-id")
    
    response = await client.post(
        "/sessions/invalid-id/messages",
        json={
            "content": "Hello!"
        }
    )
    
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert data["detail"]["code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_messages(client, mock_manager):
    """Test GET /sessions/{id}/messages."""
    # Mock the session events storage
    with patch.object(api_module, '_session_events', {
        "test-session-id": [
            {
                "type": "message",
                "message_id": "msg-1",
                "role": "assistant",
                "content": "Hello!",
                "created_at": "2024-01-01T00:00:00"
            }
        ]
    }):
        response = await client.get("/sessions/test-session-id/messages")
    
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["message_id"] == "msg-1"


@pytest.mark.asyncio
async def test_global_exception_handler(client, mock_manager):
    """Test that exceptions return proper error response."""
    # Make send_message raise an unexpected exception
    mock_manager.get_session.return_value = Mock()
    mock_manager.send_message.side_effect = RuntimeError("Unexpected error")
    
    response = await client.post(
        "/sessions/test-session-id/messages",
        json={
            "content": "Hello!"
        }
    )
    
    assert response.status_code == 500
    data = response.json()
    assert "detail" in data
    # The send_message endpoint catches all exceptions and returns LLM_ERROR
    assert data["detail"]["code"] == "LLM_ERROR"
    assert "Unexpected error" in data["detail"]["message"]
