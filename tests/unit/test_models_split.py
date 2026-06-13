"""Tests for Phase 2 models split - daemon.models package structure.

This test module verifies that the daemon/models/ package split maintains
backward compatibility while providing proper modular organization.
"""

import pytest
from datetime import datetime
from pydantic import BaseModel


class TestBackwardCompatibility:
    """Test 1: Backward Compatibility — from daemon.models import X"""

    def test_import_all_models_from_daemon_models_package(self):
        """Verify ALL model classes are importable from daemon.models (the package)."""
        from daemon.models import (
            # common
            ErrorCodes,
            ErrorResponse,
            DeleteResponse,
            # instance
            InstanceStatus,
            InstanceCreate,
            InstanceInfo,
            InstanceListResponse,
            # message
            MessageCreate,
            MessageResponse,
            # agent
            AgentInfo,
            AgentListResponse,
            AgentCreate,
            # source
            SourceStatus,
            SourceType,
            SourceCreate,
            SourceUpdate,
            SourceInfo,
            SourceListResponse,
            SourceTestRequest,
            SourceTestResponse,
            SourceActionResponse,
            # schedule
            SchedulerInstanceMode,
            ScheduleInfo,
            ScheduleListResponse,
            ScheduleUpdate,
            ScheduleExecutionInfo,
            ScheduleExecutionListResponse,
            ScheduleTriggerResponse,
            # mapping
            InstanceMappingCreate,
            InstanceMappingInfo,
            InstanceMappingListResponse,
            # mcp_server
            McpServerCreate,
            McpServerUpdate,
            McpServerInfo,
            McpServerListResponse,
            McpServerDeleteResponse,
            # common (also re-exported from agent module per __init__.py)
            HealthResponse,
        )
        
        # Verify they are all classes/enums (not None)
        assert ErrorCodes is not None
        assert ErrorResponse is not None
        assert DeleteResponse is not None
        assert HealthResponse is not None
        assert InstanceStatus is not None
        assert InstanceCreate is not None
        assert InstanceInfo is not None
        assert InstanceListResponse is not None
        assert MessageCreate is not None
        assert MessageResponse is not None
        assert AgentInfo is not None
        assert AgentListResponse is not None
        assert AgentCreate is not None
        assert SourceStatus is not None
        assert SourceType is not None
        assert SourceCreate is not None
        assert SourceUpdate is not None
        assert SourceInfo is not None
        assert SourceListResponse is not None
        assert SourceTestRequest is not None
        assert SourceTestResponse is not None
        assert SourceActionResponse is not None
        assert SchedulerInstanceMode is not None
        assert ScheduleInfo is not None
        assert ScheduleListResponse is not None
        assert ScheduleUpdate is not None
        assert ScheduleExecutionInfo is not None
        assert ScheduleExecutionListResponse is not None
        assert ScheduleTriggerResponse is not None
        assert InstanceMappingCreate is not None
        assert InstanceMappingInfo is not None
        assert InstanceMappingListResponse is not None
        assert McpServerCreate is not None
        assert McpServerUpdate is not None
        assert McpServerInfo is not None
        assert McpServerListResponse is not None
        assert McpServerDeleteResponse is not None


class TestAllCompleteness:
    """Test 2: __all__ Completeness — verify __all__ contains all expected names."""

    def test_daemon_models_all_contains_expected_names(self):
        """Verify __all__ contains all expected model names (no missing, no extra)."""
        import daemon.models as models
        
        expected_names = [
            # common
            "ErrorCodes",
            "ErrorResponse",
            "DeleteResponse",
            # instance
            "InstanceStatus",
            "InstanceCreate",
            "InstanceInfo",
            "InstanceListResponse",
            "ResumeRequest",
            # message
            "MessageCreate",
            "MessageResponse",
            # agent
            "AgentInfo",
            "AgentListResponse",
            "AgentCreate",
            "HealthResponse",  # Note: defined in common.py but re-exported in agent section
            # source
            "SourceStatus",
            "SourceType",
            "SourceCreate",
            "SourceUpdate",
            "SourceInfo",
            "SourceListResponse",
            "SourceTestRequest",
            "SourceTestResponse",
            "SourceActionResponse",
            # schedule
            "SchedulerInstanceMode",
            "ScheduleExecutionInfo",
            "ScheduleExecutionListResponse",
            "ScheduleTriggerResponse",
            "ScheduleInfo",
            "ScheduleUpdate",
            "ScheduleListResponse",
            # mapping
            "InstanceMappingCreate",
            "InstanceMappingInfo",
            "InstanceMappingListResponse",
            # mcp_server
            "McpServerCreate",
            "McpServerUpdate",
            "McpServerInfo",
            "McpServerListResponse",
            "McpServerDeleteResponse",
            # mcp_server config
            "ConfigSchemaField",
            # builtin servers (Phase 1)
            "BuiltinServerConfigure",
            "BuiltinServerTemplate",
            "BuiltinTemplateListResponse",
        ]
        
        actual_all = models.__all__
        
        # Check no missing names
        for name in expected_names:
            assert name in actual_all, f"Missing in __all__: {name}"
        
        # Check no extra names
        for name in actual_all:
            assert name in expected_names, f"Extra in __all__: {name}"
        
        # Check count matches
        assert len(actual_all) == len(expected_names), (
            f"Count mismatch: expected {len(expected_names)}, got {len(actual_all)}. "
            f"Expected: {expected_names}, Got: {actual_all}"
        )

    def test_all_names_are_accessible_as_attributes(self):
        """For each name in __all__, verify it's accessible as getattr(daemon.models, name)."""
        import daemon.models as models
        
        for name in models.__all__:
            attr = getattr(models, name)
            assert attr is not None, f"getattr(daemon.models, '{name}') returned None"


class TestDirectSubmoduleImports:
    """Test 3: Direct Submodule Imports — verify each model can be imported from its submodule."""

    def test_import_from_common(self):
        """Verify models can be imported from daemon.models.common."""
        from daemon.models.common import ErrorCodes, ErrorResponse, DeleteResponse, HealthResponse
        
        assert ErrorCodes is not None
        assert ErrorResponse is not None
        assert DeleteResponse is not None
        assert HealthResponse is not None

    def test_import_from_instance(self):
        """Verify models can be imported from daemon.models.instance."""
        from daemon.models.instance import InstanceStatus, InstanceCreate, InstanceInfo, InstanceListResponse
        
        assert InstanceStatus is not None
        assert InstanceCreate is not None
        assert InstanceInfo is not None
        assert InstanceListResponse is not None

    def test_import_from_message(self):
        """Verify models can be imported from daemon.models.message."""
        from daemon.models.message import MessageCreate, MessageResponse
        
        assert MessageCreate is not None
        assert MessageResponse is not None

    def test_import_from_agent(self):
        """Verify models can be imported from daemon.models.agent."""
        from daemon.models.agent import AgentInfo, AgentListResponse, AgentCreate
        
        assert AgentInfo is not None
        assert AgentListResponse is not None
        assert AgentCreate is not None

    def test_import_from_source(self):
        """Verify models can be imported from daemon.models.source."""
        from daemon.models.source import (
            SourceStatus, SourceType, SourceCreate, SourceUpdate, SourceInfo,
            SourceListResponse, SourceTestRequest, SourceTestResponse, SourceActionResponse
        )
        
        assert SourceStatus is not None
        assert SourceType is not None
        assert SourceCreate is not None
        assert SourceUpdate is not None
        assert SourceInfo is not None
        assert SourceListResponse is not None
        assert SourceTestRequest is not None
        assert SourceTestResponse is not None
        assert SourceActionResponse is not None

    def test_import_from_schedule(self):
        """Verify models can be imported from daemon.models.schedule."""
        from daemon.models.schedule import (
            SchedulerInstanceMode, ScheduleInfo, ScheduleListResponse, ScheduleUpdate,
            ScheduleExecutionInfo, ScheduleExecutionListResponse, ScheduleTriggerResponse
        )
        
        assert SchedulerInstanceMode is not None
        assert ScheduleInfo is not None
        assert ScheduleListResponse is not None
        assert ScheduleUpdate is not None
        assert ScheduleExecutionInfo is not None
        assert ScheduleExecutionListResponse is not None
        assert ScheduleTriggerResponse is not None

    def test_import_from_mapping(self):
        """Verify models can be imported from daemon.models.mapping."""
        from daemon.models.mapping import InstanceMappingCreate, InstanceMappingInfo, InstanceMappingListResponse
        
        assert InstanceMappingCreate is not None
        assert InstanceMappingInfo is not None
        assert InstanceMappingListResponse is not None


class TestCrossModuleReferences:
    """Test 4: Cross-Module References — verify models that reference types from other submodules work."""

    def test_schedule_uses_source_status(self):
        """Verify ScheduleInfo uses SourceStatus from daemon.models.source."""
        from daemon.models.schedule import ScheduleInfo
        from daemon.models.source import SourceStatus
        from daemon.models.common import HealthResponse
        
        # Verify ScheduleInfo has a status field typed with SourceStatus
        fields = ScheduleInfo.model_fields
        assert "status" in fields
        
        # Create a ScheduleInfo instance with SourceStatus value
        schedule_data = {
            "id": "scheduler-123",
            "name": "Test Schedule",
            "config": {"type": "cron", "schedule": "0 9 * * *"},
            "status": SourceStatus.running,
            "created_at": datetime.now(),
        }
        schedule = ScheduleInfo(**schedule_data)
        assert schedule.status == SourceStatus.running
        
        # Verify we can also use string value
        schedule_data2 = {
            "id": "scheduler-456",
            "name": "Test Schedule 2",
            "config": {"type": "interval", "interval_seconds": 3600},
            "status": "stopped",
            "created_at": datetime.now(),
        }
        schedule2 = ScheduleInfo(**schedule_data2)
        assert schedule2.status == SourceStatus.stopped

    def test_source_info_uses_source_status_and_type(self):
        """Verify SourceInfo uses SourceStatus and SourceType from the same module."""
        from daemon.models.source import SourceInfo, SourceStatus, SourceType
        
        source_data = {
            "source_id": "telegram-main",
            "source_type": SourceType.telegram,
            "name": "Customer Support Bot",
            "config": {"polling_enabled": True},
            "enabled": True,
            "autostart": True,
            "status": SourceStatus.running,
            "created_at": datetime.now(),
        }
        source = SourceInfo(**source_data)
        assert source.source_type == SourceType.telegram
        assert source.status == SourceStatus.running


class TestModelInstantiation:
    """Test 5: Model Instantiation — verify models work identically after split."""

    def test_instance_create_instantiation(self):
        """Test InstanceCreate creation and validation."""
        from daemon.models.instance import InstanceCreate
        
        # Basic creation
        ic = InstanceCreate(agent_id="coder")
        assert ic.agent_id == "coder"
        assert ic.instance_id is None
        
        # With custom instance_id
        ic2 = InstanceCreate(agent_id="coder", instance_id="custom-123")
        assert ic2.agent_id == "coder"
        assert ic2.instance_id == "custom-123"
        
        # Validation - empty agent_id should fail
        with pytest.raises(ValueError):
            InstanceCreate(agent_id="")

    def test_message_create_instantiation(self):
        """Test MessageCreate creation and validation."""
        from daemon.models.message import MessageCreate
        
        # Basic creation
        msg = MessageCreate(content="Hello!")
        assert msg.content == "Hello!"
        assert msg.images is None
        
        # With images (valid base64 data URI)
        valid_image = "data:image/png;base64,aGVsbG93b3JsZA=="
        msg2 = MessageCreate(content="With image", images=[valid_image])
        assert msg2.images == [valid_image]
        
        # Empty images list becomes None
        msg3 = MessageCreate(content="Empty images", images=[])
        assert msg3.images is None
        
        # Too many images should fail
        with pytest.raises(ValueError):
            MessageCreate(content="Too many", images=["img1", "img2", "img3", "img4"])

    def test_agent_create_instantiation(self):
        """Test AgentCreate creation with defaults."""
        from daemon.models.agent import AgentCreate
        
        # Minimal creation
        agent = AgentCreate(id="my-agent", name="My Agent")
        assert agent.id == "my-agent"
        assert agent.name == "My Agent"
        assert agent.description == ""  # default
        assert agent.icon == "🤖"  # default
        assert agent.color == "accent-blue"  # default

    def test_source_create_instantiation(self):
        """Test SourceCreate creation with validation."""
        from daemon.models.source import SourceCreate, SourceType
        
        # Basic creation
        source = SourceCreate(
            source_id="telegram-main",
            source_type=SourceType.telegram,
            name="Customer Support Bot",
        )
        assert source.source_id == "telegram-main"
        assert source.source_type == SourceType.telegram
        assert source.enabled is True  # default
        
        # Invalid source_id (special characters)
        with pytest.raises(ValueError):
            SourceCreate(
                source_id="invalid source id!",  # Contains space
                source_type=SourceType.telegram,
                name="Test",
            )

    def test_schedule_update_instantiation(self):
        """Test ScheduleUpdate creation with optional fields."""
        from daemon.models.schedule import ScheduleUpdate
        
        # Empty update
        update = ScheduleUpdate()
        assert update.name is None
        assert update.config is None
        assert update.instance_mode is None
        
        # Partial update
        update2 = ScheduleUpdate(name="New Name")
        assert update2.name == "New Name"
        assert update2.config is None

    def test_instance_mapping_create_instantiation(self):
        """Test InstanceMappingCreate creation."""
        from daemon.models.mapping import InstanceMappingCreate
        
        mapping = InstanceMappingCreate(
            external_user_id="123456789",
            agent_id="coder",
        )
        assert mapping.external_user_id == "123456789"
        assert mapping.agent_id == "coder"
        assert mapping.metadata is None  # default
        
        # With metadata
        mapping2 = InstanceMappingCreate(
            external_user_id="987654321",
            agent_id="leader",
            metadata={"username": "john_doe"},
        )
        assert mapping2.metadata == {"username": "john_doe"}

    def test_error_response_instantiation(self):
        """Test ErrorResponse creation."""
        from daemon.models.common import ErrorResponse, ErrorCodes
        
        error = ErrorResponse(
            code=ErrorCodes.INVALID_REQUEST,
            message="Invalid request body",
        )
        assert error.code == ErrorCodes.INVALID_REQUEST
        assert error.message == "Invalid request body"
        assert error.details is None  # default
        
        # With details
        error2 = ErrorResponse(
            code=ErrorCodes.INSTANCE_NOT_FOUND,
            message="Instance not found",
            details={"instance_id": "abc123"},
        )
        assert error2.details == {"instance_id": "abc123"}

    def test_health_response_instantiation(self):
        """Test HealthResponse creation."""
        from daemon.models.common import HealthResponse
        
        health = HealthResponse(
            status="healthy",
            uptime_seconds=3600.5,
            version="1.2.3",
        )
        assert health.status == "healthy"
        assert health.uptime_seconds == 3600.5
        assert health.version == "1.2.3"


class TestEnumValues:
    """Test enum values remain correct after split."""

    def test_instance_status_values(self):
        """Verify InstanceStatus enum values."""
        from daemon.models.instance import InstanceStatus
        
        assert InstanceStatus.IDLE.value == "idle"
        assert InstanceStatus.RUNNING.value == "running"
        assert InstanceStatus.WAITING.value == "waiting"
        assert InstanceStatus.WAITING_CHILDREN.value == "waiting_children"
        assert InstanceStatus.ERROR.value == "error"
        assert InstanceStatus.TERMINATED.value == "terminated"
        assert InstanceStatus.COMPLETED.value == "completed"

    def test_source_status_values(self):
        """Verify SourceStatus enum values."""
        from daemon.models.source import SourceStatus
        
        assert SourceStatus.stopped.value == "stopped"
        assert SourceStatus.starting.value == "starting"
        assert SourceStatus.running.value == "running"
        assert SourceStatus.error.value == "error"

    def test_source_type_values(self):
        """Verify SourceType enum values."""
        from daemon.models.source import SourceType
        
        assert SourceType.telegram.value == "telegram"
        assert SourceType.webhook.value == "webhook"
        assert SourceType.whatsapp.value == "whatsapp"
        assert SourceType.discord.value == "discord"
        assert SourceType.scheduler.value == "scheduler"

    def test_scheduler_instance_mode_values(self):
        """Verify SchedulerInstanceMode enum values."""
        from daemon.models.schedule import SchedulerInstanceMode
        
        assert SchedulerInstanceMode.NEW_INSTANCE.value == "new_instance"
        assert SchedulerInstanceMode.REUSE_INSTANCE.value == "reuse_instance"

    def test_error_codes_values(self):
        """Verify ErrorCodes enum values."""
        from daemon.models.common import ErrorCodes
        
        assert ErrorCodes.INVALID_REQUEST.value == "INVALID_REQUEST"
        assert ErrorCodes.INSTANCE_NOT_FOUND.value == "INSTANCE_NOT_FOUND"
        assert ErrorCodes.LLM_ERROR.value == "LLM_ERROR"
        assert ErrorCodes.INTERNAL_ERROR.value == "INTERNAL_ERROR"


class TestHealthResponseSpecific:
    """Test 6: HealthResponse Specific Test — verify same class from both import paths."""

    def test_health_response_same_class_from_both_paths(self):
        """Verify HealthResponse from common and from daemon.models refer to the SAME class."""
        from daemon.models.common import HealthResponse as HealthResponseCommon
        from daemon.models import HealthResponse as HealthResponsePackage
        
        # Both should be the exact same class (identity check)
        assert HealthResponseCommon is HealthResponsePackage, (
            "HealthResponse from daemon.models.common and daemon.models should be the same class"
        )
        
        # Verify they have identical behavior
        data = {"status": "healthy", "uptime_seconds": 100.0, "version": "2.0.0"}
        
        hr1 = HealthResponseCommon(**data)
        hr2 = HealthResponsePackage(**data)
        
        assert hr1.status == hr2.status
        assert hr1.uptime_seconds == hr2.uptime_seconds
        assert hr1.version == hr2.version


class TestMcpServerModels:
    """Test MCP Server models are properly defined and accessible."""

    def test_import_mcp_server_models(self):
        """Verify MCP server models can be imported from daemon.models.mcp_server."""
        from daemon.models.mcp_server import (
            McpServerCreate,
            McpServerUpdate,
            McpServerInfo,
            McpServerListResponse,
            McpServerDeleteResponse,
        )
        
        assert McpServerCreate is not None
        assert McpServerUpdate is not None
        assert McpServerInfo is not None
        assert McpServerListResponse is not None
        assert McpServerDeleteResponse is not None

    def test_mcp_server_create_instantiation(self):
        """Test McpServerCreate creation."""
        from daemon.models.mcp_server import McpServerCreate
        
        server = McpServerCreate(
            name="test-server",
            description="A test MCP server",
        )
        assert server.name == "test-server"
        assert server.description == "A test MCP server"
        assert server.is_active is True  # default

    def test_mcp_server_info_instantiation(self):
        """Test McpServerInfo creation."""
        from daemon.models.mcp_server import McpServerInfo
        from datetime import datetime
        
        info = McpServerInfo(
            id="server-123",
            name="test-server",
            config={"command": "npx"},
            is_active=True,
            created_at=datetime.now(),
        )
        assert info.id == "server-123"
        assert info.name == "test-server"
        assert info.is_active is True


class TestPydanticModelBehavior:
    """Test 7: Pydantic Model Behavior — verify models are still proper Pydantic models."""

    def test_all_models_are_pydantic_basemodel(self):
        """Verify all models are Pydantic BaseModel subclasses."""
        from daemon.models import (
            ErrorResponse, DeleteResponse, HealthResponse,
            InstanceCreate, InstanceInfo, InstanceListResponse,
            MessageCreate, MessageResponse,
            AgentInfo, AgentListResponse, AgentCreate,
            SourceCreate, SourceUpdate, SourceInfo, SourceListResponse,
            SourceTestRequest, SourceTestResponse, SourceActionResponse,
            ScheduleInfo, ScheduleListResponse, ScheduleUpdate,
            ScheduleExecutionInfo, ScheduleExecutionListResponse, ScheduleTriggerResponse,
            InstanceMappingCreate, InstanceMappingInfo, InstanceMappingListResponse,
            McpServerCreate, McpServerUpdate, McpServerInfo, McpServerListResponse,
            McpServerDeleteResponse,
        )
        
        # All should be subclasses of BaseModel
        pydantic_models = [
            ErrorResponse, DeleteResponse, HealthResponse,
            InstanceCreate, InstanceInfo, InstanceListResponse,
            MessageCreate, MessageResponse,
            AgentInfo, AgentListResponse, AgentCreate,
            SourceCreate, SourceUpdate, SourceInfo, SourceListResponse,
            SourceTestRequest, SourceTestResponse, SourceActionResponse,
            ScheduleInfo, ScheduleListResponse, ScheduleUpdate,
            ScheduleExecutionInfo, ScheduleExecutionListResponse, ScheduleTriggerResponse,
            InstanceMappingCreate, InstanceMappingInfo, InstanceMappingListResponse,
            McpServerCreate, McpServerUpdate, McpServerInfo, McpServerListResponse,
            McpServerDeleteResponse,
        ]
        
        for model in pydantic_models:
            assert issubclass(model, BaseModel), f"{model.__name__} is not a BaseModel subclass"

    def test_model_json_schema_works(self):
        """Verify model_json_schema() works for key models."""
        from daemon.models import InstanceCreate, MessageCreate, SourceCreate
        
        # Should not raise
        schema1 = InstanceCreate.model_json_schema()
        assert "properties" in schema1
        assert "agent_id" in schema1["properties"]
        
        schema2 = MessageCreate.model_json_schema()
        assert "properties" in schema2
        
        schema3 = SourceCreate.model_json_schema()
        assert "properties" in schema3
        assert "source_id" in schema3["properties"]

    def test_model_fields_are_present(self):
        """Verify model_fields are present and correct for key models."""
        from daemon.models import InstanceCreate, MessageCreate, HealthResponse
        
        # InstanceCreate fields
        ic_fields = InstanceCreate.model_fields
        assert "agent_id" in ic_fields
        assert "instance_id" in ic_fields
        
        # MessageCreate fields
        mc_fields = MessageCreate.model_fields
        assert "content" in mc_fields
        assert "images" in mc_fields
        
        # HealthResponse fields
        hr_fields = HealthResponse.model_fields
        assert "status" in hr_fields
        assert "uptime_seconds" in hr_fields
        assert "version" in hr_fields

    def test_model_serialization(self):
        """Verify models can be serialized to dict/JSON."""
        from daemon.models import HealthResponse
        
        hr = HealthResponse(
            status="healthy",
            uptime_seconds=123.45,
            version="1.0.0",
        )
        
        # To dict
        hr_dict = hr.model_dump()
        assert hr_dict["status"] == "healthy"
        assert hr_dict["uptime_seconds"] == 123.45
        
        # To JSON
        hr_json = hr.model_dump_json()
        assert '"status"' in hr_json
        assert '"uptime_seconds"' in hr_json
