"""Tests for daemon/models package."""

import pytest
from datetime import datetime
from daemon.models import (
    InstanceCreate,
    InstanceInfo,
    MessageCreate,
    MessageResponse,
    ErrorResponse,
    HealthResponse,
    InstanceListResponse,
    InstanceStatus,
    ErrorCodes,
)


class TestInstanceCreate:
    """Tests for InstanceCreate model."""

    def test_instance_create_required(self, sample_instance_create_data):
        """Test InstanceCreate with only required fields."""
        instance = InstanceCreate(**sample_instance_create_data)
        
        assert instance.agent_id == "coder"
        assert instance.instance_id is None

    def test_instance_create_optional(self, sample_instance_create_with_instance_id):
        """Test InstanceCreate with optional instance_id."""
        instance = InstanceCreate(**sample_instance_create_with_instance_id)
        
        assert instance.agent_id == "coder"
        assert instance.instance_id == "custom-instance-123"

    def test_instance_create_serialization(self, sample_instance_create_data):
        """Test InstanceCreate model_dump for serialization."""
        instance = InstanceCreate(**sample_instance_create_data)
        data = instance.model_dump()
        
        assert data["agent_id"] == "coder"
        assert data["instance_id"] is None

    def test_instance_create_validation_missing_agent_dir(self):
        """Test InstanceCreate validation requires agent_dir."""
        with pytest.raises(ValueError):
            InstanceCreate()


class TestInstanceInfo:
    """Tests for InstanceInfo model."""

    def test_instance_info_model(self, sample_instance_info_data):
        """Test InstanceInfo serialization."""
        instance = InstanceInfo(**sample_instance_info_data)
        
        assert instance.instance_id == "test-instance-123"
        assert instance.agent_dir == "/path/to/agent"
        assert instance.status == InstanceStatus.RUNNING
        assert instance.parent_id is None
        assert instance.children == []
        assert instance.created_at == datetime(2024, 1, 1, 0, 0, 0)
        assert instance.updated_at == datetime(2024, 1, 1, 0, 1, 0)

    def test_instance_info_serialization(self, sample_instance_info_data):
        """Test InstanceInfo model_dump for serialization."""
        instance = InstanceInfo(**sample_instance_info_data)
        data = instance.model_dump()
        
        assert data["instance_id"] == "test-instance-123"
        assert data["status"] == "running"

    def test_instance_info_with_parent(self):
        """Test InstanceInfo with parent_id."""
        data = {
            "instance_id": "child-instance",
            "agent_id": "coder",
            "agent_dir": "/path/to/agent",
            "status": "running",
            "parent_id": "parent-instance",
            "children": [],
            "created_at": datetime(2024, 1, 1, 0, 0, 0),
        }
        
        instance = InstanceInfo(**data)
        assert instance.parent_id == "parent-instance"

    def test_instance_info_with_children(self):
        """Test InstanceInfo with children."""
        data = {
            "instance_id": "parent-instance",
            "agent_id": "coder",
            "agent_dir": "/path/to/agent",
            "status": "running",
            "parent_id": None,
            "children": ["child-1", "child-2"],
            "created_at": datetime(2024, 1, 1, 0, 0, 0),
        }
        
        instance = InstanceInfo(**data)
        assert instance.children == ["child-1", "child-2"]


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
            code=ErrorCodes.INSTANCE_NOT_FOUND,
            message="Instance not found"
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


class TestInstanceStatus:
    """Tests for InstanceStatus enum."""

    def test_instance_status_enum(self):
        """Test all InstanceStatus values."""
        assert InstanceStatus.IDLE.value == "idle"
        assert InstanceStatus.RUNNING.value == "running"
        assert InstanceStatus.WAITING.value == "waiting"
        assert InstanceStatus.ERROR.value == "error"
        assert InstanceStatus.TERMINATED.value == "terminated"

    def test_instance_status_from_string(self):
        """Test InstanceStatus creation from string."""
        status = InstanceStatus("running")
        assert status == InstanceStatus.RUNNING

    def test_instance_status_values(self):
        """Test InstanceStatus has correct number of values."""
        values = [s.value for s in InstanceStatus]
        assert len(values) == 8
        assert "idle" in values
        assert "running" in values
        assert "waiting" in values
        assert "waiting_children" in values
        assert "error" in values
        assert "terminated" in values
        assert "completed" in values
        assert "paused" in values


class TestErrorCodes:
    """Tests for ErrorCodes enum."""

    def test_error_codes_enum(self):
        """Test all ErrorCodes values."""
        assert ErrorCodes.INVALID_REQUEST.value == "INVALID_REQUEST"
        assert ErrorCodes.INSTANCE_NOT_FOUND.value == "INSTANCE_NOT_FOUND"
        assert ErrorCodes.INSTANCE_TERMINATED.value == "INSTANCE_TERMINATED"
        assert ErrorCodes.RATE_LIMITED.value == "RATE_LIMITED"
        assert ErrorCodes.MAX_INSTANCES_EXCEEDED.value == "MAX_INSTANCES_EXCEEDED"
        assert ErrorCodes.LLM_ERROR.value == "LLM_ERROR"
        assert ErrorCodes.INTERNAL_ERROR.value == "INTERNAL_ERROR"

    def test_error_codes_from_string(self):
        """Test ErrorCodes creation from string."""
        code = ErrorCodes("INSTANCE_NOT_FOUND")
        assert code == ErrorCodes.INSTANCE_NOT_FOUND

    def test_error_codes_values(self):
        """Test ErrorCodes has correct number of values."""
        values = [e.value for e in ErrorCodes]
        # Should have all expected error codes
        expected_codes = [
            "INVALID_REQUEST",
            "INSTANCE_NOT_FOUND",
            "INSTANCE_TERMINATED",
            "RATE_LIMITED",
            "MAX_INSTANCES_EXCEEDED",
            "LLM_ERROR",
            "INTERNAL_ERROR",
            "SOURCE_NOT_FOUND",
            "SOURCE_ALREADY_EXISTS",
            "MCP_SERVER_NOT_FOUND",
            "MCP_SERVER_ALREADY_EXISTS",
            "SOURCE_TYPE_NOT_SUPPORTED",
            "SCHEDULER_ENABLE_NOT_ALLOWED",
            "SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED",
            "MAPPING_NOT_FOUND",
            "MAPPING_ALREADY_EXISTS",
            "SERVICE_UNAVAILABLE",
            "BUILTIN_SERVER_PROTECTED",
        ]
        assert len(values) == len(expected_codes)
        for code in expected_codes:
            assert code in values


class TestInstanceListResponse:
    """Tests for InstanceListResponse model."""

    def test_instance_list_response(self):
        """Test InstanceListResponse."""
        instances = [
            InstanceInfo(
                instance_id="instance-1",
                agent_id="coder",
                agent_dir="/path/to/agent1",
                status=InstanceStatus.RUNNING,
                created_at=datetime(2024, 1, 1, 0, 0, 0),
            ),
            InstanceInfo(
                instance_id="instance-2",
                agent_id="coder",
                agent_dir="/path/to/agent2",
                status=InstanceStatus.IDLE,
                created_at=datetime(2024, 1, 1, 0, 0, 0),
            ),
        ]
        
        response = InstanceListResponse(
            instances=instances,
            total=2,
            limit=100,
            offset=0,
            has_more=False,
        )
        
        assert len(response.instances) == 2
        assert response.instances[0].instance_id == "instance-1"
        assert response.instances[1].instance_id == "instance-2"

    def test_instance_list_response_empty(self):
        """Test InstanceListResponse with empty list."""
        response = InstanceListResponse(
            instances=[],
            total=0,
            limit=100,
            offset=0,
            has_more=False,
        )
        
        assert len(response.instances) == 0

    def test_instance_list_response_serialization(self):
        """Test InstanceListResponse model_dump for serialization."""
        instances = [
            InstanceInfo(
                instance_id="instance-1",
                agent_id="coder",
                agent_dir="/path/to/agent",
                status=InstanceStatus.RUNNING,
                created_at=datetime(2024, 1, 1, 0, 0, 0),
            ),
        ]
        
        response = InstanceListResponse(
            instances=instances,
            total=1,
            limit=100,
            offset=0,
            has_more=False,
        )
        data = response.model_dump()
        
        assert len(data["instances"]) == 1
        assert data["instances"][0]["instance_id"] == "instance-1"
        assert data["instances"][0]["agent_id"] == "coder"


class TestModelValidation:
    """Tests for model validation and edge cases."""

    def test_model_validate(self):
        """Test model_validate for parsing validated data."""
        data = {
            "agent_id": "coder",
            "instance_id": "test-instance",
        }
        
        instance = InstanceCreate.model_validate(data)
        assert instance.agent_id == "coder"
        assert instance.instance_id == "test-instance"

    def test_model_dump_json(self):
        """Test model_dump_json for JSON serialization."""
        instance = InstanceCreate(agent_id="coder")
        json_str = instance.model_dump_json()
        
        assert "agent_id" in json_str
        assert "coder" in json_str


class TestInstanceCreateProjectId:
    """Tests for InstanceCreate project_id field."""

    def test_instance_create_with_project_id(self):
        """Test InstanceCreate accepts project_id."""
        instance = InstanceCreate(agent_id="coder", project_id="proj-123")
        assert instance.project_id == "proj-123"

    def test_instance_create_project_id_defaults_to_none(self):
        """Test InstanceCreate project_id defaults to None."""
        instance = InstanceCreate(agent_id="coder")
        assert instance.project_id is None

    def test_instance_create_serialization_includes_project_id(self):
        """Test InstanceCreate model_dump includes project_id."""
        instance = InstanceCreate(agent_id="coder", project_id="proj-456")
        data = instance.model_dump()
        assert data["project_id"] == "proj-456"

    def test_instance_create_serialization_project_id_none(self):
        """Test InstanceCreate model_dump includes project_id as None."""
        instance = InstanceCreate(agent_id="coder")
        data = instance.model_dump()
        assert "project_id" in data
        assert data["project_id"] is None


class TestInstanceInfoProjectId:
    """Tests for InstanceInfo project_id field."""

    def test_instance_info_with_project_id(self):
        """Test InstanceInfo accepts project_id."""
        instance = InstanceInfo(
            instance_id="test-instance",
            agent_id="coder",
            agent_dir="/path/to/agent",
            status=InstanceStatus.RUNNING,
            project_id="proj-123",
            created_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        assert instance.project_id == "proj-123"

    def test_instance_info_project_id_defaults_to_none(self):
        """Test InstanceInfo project_id defaults to None."""
        instance = InstanceInfo(
            instance_id="test-instance",
            agent_id="coder",
            agent_dir="/path/to/agent",
            status=InstanceStatus.RUNNING,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        assert instance.project_id is None

    def test_instance_info_serialization_includes_project_id(self):
        """Test InstanceInfo model_dump includes project_id."""
        instance = InstanceInfo(
            instance_id="test-instance",
            agent_id="coder",
            agent_dir="/path/to/agent",
            status=InstanceStatus.RUNNING,
            project_id="proj-789",
            created_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        data = instance.model_dump()
        assert data["project_id"] == "proj-789"

    def test_instance_info_serialization_project_id_none(self):
        """Test InstanceInfo model_dump includes project_id as None."""
        instance = InstanceInfo(
            instance_id="test-instance",
            agent_id="coder",
            agent_dir="/path/to/agent",
            status=InstanceStatus.RUNNING,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
        )
        data = instance.model_dump()
        assert "project_id" in data
        assert data["project_id"] is None


class TestInstanceModelProjectId:
    """Tests for SQLModel Instance project_id field."""

    def test_instance_with_project_id(self):
        """Test Instance accepts project_id in constructor."""
        from daemon.repositories.instance.models import Instance
        instance = Instance(
            instance_id="test-instance",
            project_id="proj-123",
            agent_id="coder",
            agent_dir="/path/to/agent",
        )
        assert instance.project_id == "proj-123"

    def test_instance_project_id_defaults_to_none(self):
        """Test Instance project_id defaults to None."""
        from daemon.repositories.instance.models import Instance
        instance = Instance(
            instance_id="test-instance",
            agent_id="coder",
            agent_dir="/path/to/agent",
        )
        assert instance.project_id is None

    def test_instance_to_dict_includes_project_id(self):
        """Test Instance to_dict includes project_id."""
        from daemon.repositories.instance.models import Instance
        instance = Instance(
            instance_id="test-instance",
            project_id="proj-456",
            agent_id="coder",
            agent_dir="/path/to/agent",
        )
        data = instance.to_dict()
        assert "project_id" in data
        assert data["project_id"] == "proj-456"

    def test_instance_to_dict_project_id_none(self):
        """Test Instance to_dict includes project_id as None."""
        from daemon.repositories.instance.models import Instance
        instance = Instance(
            instance_id="test-instance",
            agent_id="coder",
            agent_dir="/path/to/agent",
        )
        data = instance.to_dict()
        assert "project_id" in data
        assert data["project_id"] is None
