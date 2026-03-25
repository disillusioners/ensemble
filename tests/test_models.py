"""Tests for daemon/models.py."""

import pytest
from datetime import datetime
from daemon.models import (
    SessionCreate,
    SessionInfo,
    MessageCreate,
    MessageResponse,
    ErrorResponse,
    HealthResponse,
    SessionListResponse,
    SessionStatus,
    ErrorCodes,
)


class TestSessionCreate:
    """Tests for SessionCreate model."""

    def test_session_create_required(self, sample_session_create_data):
        """Test SessionCreate with only required fields."""
        session = SessionCreate(**sample_session_create_data)
        
        assert session.agent_id == "coder"
        assert session.session_id is None

    def test_session_create_optional(self, sample_session_create_with_session_id):
        """Test SessionCreate with optional session_id."""
        session = SessionCreate(**sample_session_create_with_session_id)
        
        assert session.agent_id == "coder"
        assert session.session_id == "custom-session-123"

    def test_session_create_serialization(self, sample_session_create_data):
        """Test SessionCreate model_dump for serialization."""
        session = SessionCreate(**sample_session_create_data)
        data = session.model_dump()
        
        assert data["agent_id"] == "coder"
        assert data["session_id"] is None

    def test_session_create_validation_missing_agent_dir(self):
        """Test SessionCreate validation requires agent_dir."""
        with pytest.raises(ValueError):
            SessionCreate()


class TestSessionInfo:
    """Tests for SessionInfo model."""

    def test_session_info_model(self, sample_session_info_data):
        """Test SessionInfo serialization."""
        session = SessionInfo(**sample_session_info_data)
        
        assert session.session_id == "test-session-123"
        assert session.agent_dir == "/path/to/agent"
        assert session.status == SessionStatus.running
        assert session.parent_id is None
        assert session.children == []
        assert session.created_at == datetime(2024, 1, 1, 0, 0, 0)
        assert session.updated_at == datetime(2024, 1, 1, 0, 1, 0)

    def test_session_info_serialization(self, sample_session_info_data):
        """Test SessionInfo model_dump for serialization."""
        session = SessionInfo(**sample_session_info_data)
        data = session.model_dump()
        
        assert data["session_id"] == "test-session-123"
        assert data["status"] == "running"

    def test_session_info_with_parent(self):
        """Test SessionInfo with parent_id."""
        data = {
            "session_id": "child-session",
            "agent_id": "coder",
            "agent_dir": "/path/to/agent",
            "status": "running",
            "parent_id": "parent-session",
            "children": [],
            "created_at": datetime(2024, 1, 1, 0, 0, 0),
        }
        
        session = SessionInfo(**data)
        assert session.parent_id == "parent-session"

    def test_session_info_with_children(self):
        """Test SessionInfo with children."""
        data = {
            "session_id": "parent-session",
            "agent_id": "coder",
            "agent_dir": "/path/to/agent",
            "status": "running",
            "parent_id": None,
            "children": ["child-1", "child-2"],
            "created_at": datetime(2024, 1, 1, 0, 0, 0),
        }
        
        session = SessionInfo(**data)
        assert session.children == ["child-1", "child-2"]


class TestMessageCreate:
    """Tests for MessageCreate model."""

    def test_message_create_validation(self, sample_message_create_data):
        """Test MessageCreate validates content."""
        message = MessageCreate(**sample_message_create_data)
        
        assert message.content == "Hello, agent!"

    def test_message_create_required_content(self):
        """Test MessageCreate requires content field."""
        with pytest.raises(ValueError):
            MessageCreate()

    def test_message_create_empty_content(self):
        """Test MessageCreate allows empty content."""
        message = MessageCreate(content="")
        assert message.content == ""

    def test_message_create_serialization(self, sample_message_create_data):
        """Test MessageCreate model_dump for serialization."""
        message = MessageCreate(**sample_message_create_data)
        data = message.model_dump()
        
        assert data["content"] == "Hello, agent!"


class TestMessageResponse:
    """Tests for MessageResponse model."""

    def test_message_response_model(self):
        """Test MessageResponse basic model."""
        data = {
            "message_id": "msg-456",
            "role": "assistant",
            "content": "Hello! How can I help you?",
            "created_at": datetime(2024, 1, 1, 0, 0, 0),
        }
        
        response = MessageResponse(**data)
        
        assert response.message_id == "msg-456"
        assert response.role == "assistant"
        assert response.content == "Hello! How can I help you?"
        assert response.tool_calls is None

    def test_message_response_with_tool_calls(self, sample_message_response_data):
        """Test MessageResponse with tool_calls."""
        response = MessageResponse(**sample_message_response_data)
        
        assert response.tool_calls is not None
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0]["id"] == "call_123"
        assert response.tool_calls[0]["function"]["name"] == "some_tool"

    def test_message_response_serialization(self, sample_message_response_data):
        """Test MessageResponse model_dump for serialization."""
        response = MessageResponse(**sample_message_response_data)
        data = response.model_dump()
        
        assert data["message_id"] == "msg-456"
        assert data["tool_calls"][0]["function"]["name"] == "some_tool"

    def test_message_response_without_content(self):
        """Test MessageResponse without content (for tool calls only)."""
        data = {
            "message_id": "msg-789",
            "role": "assistant",
            "content": None,
            "created_at": datetime(2024, 1, 1, 0, 0, 0),
        }
        
        response = MessageResponse(**data)
        assert response.content is None


class TestErrorResponse:
    """Tests for ErrorResponse model."""

    def test_error_response_model(self, sample_error_response_data):
        """Test ErrorResponse serialization."""
        error = ErrorResponse(**sample_error_response_data)
        
        assert error.code == ErrorCodes.INVALID_REQUEST
        assert error.message == "The request body is invalid"
        assert error.details == {"field": "agent_dir", "reason": "required field"}

    def test_error_response_required_fields(self):
        """Test ErrorResponse requires code and message."""
        with pytest.raises(ValueError):
            ErrorResponse(message="Some error")

    def test_error_response_serialization(self, sample_error_response_data):
        """Test ErrorResponse model_dump for serialization."""
        error = ErrorResponse(**sample_error_response_data)
        data = error.model_dump()
        
        assert data["code"] == "INVALID_REQUEST"
        assert data["message"] == "The request body is invalid"

    def test_error_response_without_details(self):
        """Test ErrorResponse without optional details."""
        error = ErrorResponse(
            code=ErrorCodes.SESSION_NOT_FOUND,
            message="Session not found"
        )
        
        assert error.details is None
        data = error.model_dump()
        assert data["details"] is None


class TestHealthResponse:
    """Tests for HealthResponse model."""

    def test_health_response_model(self, sample_health_response_data):
        """Test HealthResponse."""
        health = HealthResponse(**sample_health_response_data)
        
        assert health.status == "healthy"
        assert health.uptime_seconds == 3600.0
        assert health.version == "1.0.0"

    def test_health_response_required_fields(self):
        """Test HealthResponse requires all fields."""
        with pytest.raises(ValueError):
            HealthResponse(status="healthy")

    def test_health_response_serialization(self, sample_health_response_data):
        """Test HealthResponse model_dump for serialization."""
        health = HealthResponse(**sample_health_response_data)
        data = health.model_dump()
        
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"


class TestSessionStatus:
    """Tests for SessionStatus enum."""

    def test_session_status_enum(self):
        """Test all SessionStatus values."""
        assert SessionStatus.idle.value == "idle"
        assert SessionStatus.running.value == "running"
        assert SessionStatus.waiting.value == "waiting"
        assert SessionStatus.error.value == "error"
        assert SessionStatus.terminated.value == "terminated"

    def test_session_status_from_string(self):
        """Test SessionStatus creation from string."""
        status = SessionStatus("running")
        assert status == SessionStatus.running

    def test_session_status_values(self):
        """Test SessionStatus has correct number of values."""
        values = [s.value for s in SessionStatus]
        assert len(values) == 5
        assert "idle" in values
        assert "running" in values
        assert "waiting" in values
        assert "error" in values
        assert "terminated" in values


class TestErrorCodes:
    """Tests for ErrorCodes enum."""

    def test_error_codes_enum(self):
        """Test all ErrorCodes values."""
        assert ErrorCodes.INVALID_REQUEST.value == "INVALID_REQUEST"
        assert ErrorCodes.SESSION_NOT_FOUND.value == "SESSION_NOT_FOUND"
        assert ErrorCodes.SESSION_TERMINATED.value == "SESSION_TERMINATED"
        assert ErrorCodes.RATE_LIMITED.value == "RATE_LIMITED"
        assert ErrorCodes.MAX_SESSIONS_EXCEEDED.value == "MAX_SESSIONS_EXCEEDED"
        assert ErrorCodes.LLM_ERROR.value == "LLM_ERROR"
        assert ErrorCodes.INTERNAL_ERROR.value == "INTERNAL_ERROR"

    def test_error_codes_from_string(self):
        """Test ErrorCodes creation from string."""
        code = ErrorCodes("SESSION_NOT_FOUND")
        assert code == ErrorCodes.SESSION_NOT_FOUND

    def test_error_codes_values(self):
        """Test ErrorCodes has correct number of values."""
        values = [e.value for e in ErrorCodes]
        # Should have all expected error codes
        expected_codes = [
            "INVALID_REQUEST",
            "SESSION_NOT_FOUND",
            "SESSION_TERMINATED",
            "RATE_LIMITED",
            "MAX_SESSIONS_EXCEEDED",
            "LLM_ERROR",
            "INTERNAL_ERROR",
            "SOURCE_NOT_FOUND",
            "SOURCE_ALREADY_EXISTS",
            "SOURCE_TYPE_NOT_SUPPORTED",
            "SCHEDULER_ENABLE_NOT_ALLOWED",
            "SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED",
            "MAPPING_NOT_FOUND",
            "MAPPING_ALREADY_EXISTS",
            "SERVICE_UNAVAILABLE",
        ]
        assert len(values) == len(expected_codes)
        for code in expected_codes:
            assert code in values


class TestSessionListResponse:
    """Tests for SessionListResponse model."""

    def test_session_list_response(self):
        """Test SessionListResponse."""
        sessions = [
            SessionInfo(
                session_id="session-1",
                agent_id="coder",
                agent_dir="/path/to/agent1",
                status=SessionStatus.running,
                created_at=datetime(2024, 1, 1, 0, 0, 0),
            ),
            SessionInfo(
                session_id="session-2",
                agent_id="coder",
                agent_dir="/path/to/agent2",
                status=SessionStatus.idle,
                created_at=datetime(2024, 1, 1, 0, 0, 0),
            ),
        ]
        
        response = SessionListResponse(
            sessions=sessions,
            total=2,
            limit=100,
            offset=0,
            has_more=False,
        )
        
        assert len(response.sessions) == 2
        assert response.sessions[0].session_id == "session-1"
        assert response.sessions[1].session_id == "session-2"

    def test_session_list_response_empty(self):
        """Test SessionListResponse with empty list."""
        response = SessionListResponse(
            sessions=[],
            total=0,
            limit=100,
            offset=0,
            has_more=False,
        )
        
        assert len(response.sessions) == 0

    def test_session_list_response_serialization(self):
        """Test SessionListResponse model_dump for serialization."""
        sessions = [
            SessionInfo(
                session_id="session-1",
                agent_id="coder",
                agent_dir="/path/to/agent",
                status=SessionStatus.running,
                created_at=datetime(2024, 1, 1, 0, 0, 0),
            ),
        ]
        
        response = SessionListResponse(
            sessions=sessions,
            total=1,
            limit=100,
            offset=0,
            has_more=False,
        )
        data = response.model_dump()
        
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["session_id"] == "session-1"
        assert data["sessions"][0]["agent_id"] == "coder"


class TestModelValidation:
    """Tests for model validation and edge cases."""

    def test_model_validate(self):
        """Test model_validate for parsing validated data."""
        data = {
            "agent_id": "coder",
            "session_id": "test-session",
        }
        
        session = SessionCreate.model_validate(data)
        assert session.agent_id == "coder"
        assert session.session_id == "test-session"

    def test_model_dump_json(self):
        """Test model_dump_json for JSON serialization."""
        session = SessionCreate(agent_id="coder")
        json_str = session.model_dump_json()
        
        assert "agent_id" in json_str
        assert "coder" in json_str
