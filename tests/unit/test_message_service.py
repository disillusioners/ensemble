"""Unit tests for MessageService."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.message_models import ToolCallInfo, UnifiedMessage
from daemon.services.message_service import MessageService


@pytest.fixture
def mock_event_bus():
    bus = MagicMock()
    bus.create_message_received_event = AsyncMock()
    bus.create_processing_completed_event = AsyncMock()
    bus.create_event = AsyncMock()
    return bus


@pytest.fixture
def service(mock_event_bus):
    return MessageService(event_bus=mock_event_bus)


class TestUserMessageStored:
    """Tests for on_user_message_stored method."""

    @pytest.mark.asyncio
    async def test_emits_message_received(self, service, mock_event_bus):
        """Verify message_received event is emitted with correct data."""
        msg = await service.on_user_message_stored(
            instance_id="inst-1",
            message_id="msg-1",
            content="Hello",
            source="api",
        )
        
        assert isinstance(msg, UnifiedMessage)
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.instance_id == "inst-1"
        assert msg.source == "api"
        mock_event_bus.create_message_received_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_unified_message(self, service, mock_event_bus):
        """Verify method returns UnifiedMessage instance."""
        msg = await service.on_user_message_stored(
            instance_id="inst-2",
            message_id="msg-2",
            content="Test message",
            source="telegram:user:123",
        )
        
        assert isinstance(msg, UnifiedMessage)
        assert msg.message_id == "msg-2"
        assert msg.role == "user"

    @pytest.mark.asyncio
    async def test_extra_data_passed_to_event(self, service, mock_event_bus):
        """Verify extra kwargs are included in event content via message.to_dict()."""
        await service.on_user_message_stored(
            instance_id="inst-1",
            message_id="msg-1",
            content="Hello",
            source="api",
            priority=1,  # extra kwarg - not stored in message
        )
        
        call_args = mock_event_bus.create_message_received_event.call_args
        assert call_args is not None
        content = call_args.kwargs.get('content', {})
        # Now contains full message.to_dict() format
        assert content.get('role') == "user"
        assert content.get('source') == "api"
        assert content.get('content') == "Hello"
        # Note: extra kwargs like priority are NOT stored in message.to_dict()


class TestAssistantMessageCompleted:
    """Tests for on_assistant_message_completed method."""

    @pytest.mark.asyncio
    async def test_emits_both_events(self, service, mock_event_bus):
        """Verify both message_completed and processing_completed events are emitted."""
        msg = await service.on_assistant_message_completed(
            instance_id="inst-1",
            original_message_id="user-msg-1",
            content="Response text",
            thinking="Let me think...",
        )
        
        assert isinstance(msg, UnifiedMessage)
        assert msg.role == "assistant"
        assert msg.content == "Response text"
        
        # message_completed event
        mock_event_bus.create_event.assert_called_once()
        
        # processing_completed event (lightweight status)
        mock_event_bus.create_processing_completed_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_completed_has_message_dict(self, service, mock_event_bus):
        """Verify message_completed event contains message.to_dict() directly."""
        await service.on_assistant_message_completed(
            instance_id="inst-1",
            original_message_id="user-msg-1",
            content="Response text",
            thinking="Thinking content",
            thinking_extracted="Extracted thinking",
        )
        
        call_args = mock_event_bus.create_event.call_args
        assert call_args is not None
        data = call_args.kwargs.get('data', {})
        # Now contains message.to_dict() directly, not wrapped
        assert data.get('role') == "assistant"
        assert data.get('content') == "Response text"
        assert data.get('thinking') == "Thinking content"
        assert data.get('thinking_extracted') == "Extracted thinking"

    @pytest.mark.asyncio
    async def test_processing_completed_lightweight_status(self, service, mock_event_bus):
        """Verify processing_completed event contains only lightweight status (no content)."""
        await service.on_assistant_message_completed(
            instance_id="inst-1",
            original_message_id="user-msg-1",
            content="Response text",
            thinking="Thinking",
        )
        
        call_args = mock_event_bus.create_processing_completed_event.call_args
        assert call_args is not None
        result = call_args.kwargs.get('result', {})
        # Now only contains success status and assistant_message_id
        assert result.get('success') is True
        assert 'assistant_message_id' in result
        # No longer contains content, thinking, tool_calls
        assert 'content' not in result
        assert 'thinking' not in result
        assert 'tool_calls' not in result

    @pytest.mark.asyncio
    async def test_tool_calls_in_message_completed(self, service, mock_event_bus):
        """Verify tool calls are included in message_completed."""
        tool_calls = [
            ToolCallInfo(
                id="tc-1",
                name="bash",
                arguments={"command": "ls -la"},
                output="result",
            )
        ]
        
        await service.on_assistant_message_completed(
            instance_id="inst-1",
            original_message_id="user-msg-1",
            content="Using tools",
            tool_calls=tool_calls,
        )
        
        # Check message_completed
        event_call = mock_event_bus.create_event.call_args
        data = event_call.kwargs.get('data', {})
        # Now contains message.to_dict() directly
        assert 'tool_calls' in data
        assert len(data['tool_calls']) == 1
        
        # processing_completed should not contain tool_calls anymore
        result_call = mock_event_bus.create_processing_completed_event.call_args
        result = result_call.kwargs.get('result', {})
        assert 'tool_calls' not in result


class TestChildCompletionReport:
    """Tests for on_child_completion_report method."""

    @pytest.mark.asyncio
    async def test_emits_message_received(self, service, mock_event_bus):
        """Verify message_received event is emitted for child completion."""
        msg = await service.on_child_completion_report(
            parent_instance_id="parent-1",
            child_instance_id="child-1",
            report_content="Child completed successfully",
            message_id="report-msg-1",
        )
        
        assert isinstance(msg, UnifiedMessage)
        assert msg.role == "user"
        assert msg.content == "Child completed successfully"
        assert msg.source == "child:child-1"
        mock_event_bus.create_message_received_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_source_format(self, service, mock_event_bus):
        """Verify source is correctly formatted as child:instance_id in message.to_dict()."""
        await service.on_child_completion_report(
            parent_instance_id="parent-1",
            child_instance_id="child-abc-123",
            report_content="Done",
            message_id="msg-1",
        )
        
        call_args = mock_event_bus.create_message_received_event.call_args
        content = call_args.kwargs.get('content', {})
        # Now contains full message.to_dict() format
        assert content.get('source') == "child:child-abc-123"
        assert content.get('role') == "user"
        assert content.get('content') == "Done"
        # Note: child_instance_id is NOT in message.to_dict() - it's only in source


class TestUnifiedMessageFormats:
    """Tests for UnifiedMessage to_dict method."""

    def test_to_dict_omits_none_fields(self):
        """Verify to_dict omits None fields by default."""
        msg = UnifiedMessage(
            message_id="m1",
            instance_id="i1",
            role="user",
            content="Hi",
        )
        
        data = msg.to_dict()
        assert "thinking" not in data
        assert "tool_calls" not in data
        assert "thinking_extracted" not in data
        assert "source" not in data

    def test_to_dict_includes_present_fields(self):
        """Verify to_dict includes all present fields."""
        msg = UnifiedMessage(
            message_id="m1",
            instance_id="i1",
            role="assistant",
            content="Response",
            thinking="Thinking",
            source="api",
        )
        
        data = msg.to_dict()
        assert data["thinking"] == "Thinking"
        assert data["source"] == "api"
        assert "thinking_extracted" not in data

    def test_to_dict_with_include_nulls(self):
        """Verify to_dict with include_nulls includes None values."""
        msg = UnifiedMessage(
            message_id="m1",
            instance_id="i1",
            role="assistant",
            content="Response",
        )
        
        data = msg.to_dict(include_nulls=True)
        assert data.get("thinking") is None
        assert data.get("tool_calls") is None

    def test_to_dict_api_format(self):
        """Verify to_dict format for GET /messages (no include_nulls)."""
        msg = UnifiedMessage(
            message_id="m1",
            instance_id="i1",
            role="assistant",
            content="Response",
        )
        
        resp = msg.to_dict()
        assert resp["role"] == "assistant"
        assert resp["content"] == "Response"
        assert resp["message_id"] == "m1"
        assert "thinking" not in resp  # None values omitted

    def test_to_dict_with_tool_calls(self):
        """Verify to_dict includes tool_calls when present."""
        tool_calls = [
            ToolCallInfo(
                id="tc-1",
                name="bash",
                arguments={"cmd": "ls"},
                output="result",
            )
        ]
        msg = UnifiedMessage(
            message_id="m1",
            instance_id="i1",
            role="assistant",
            content="Using tool",
            tool_calls=tool_calls,
        )
        
        resp = msg.to_dict()
        assert resp["tool_calls"] is not None
        assert len(resp["tool_calls"]) == 1
        assert resp["tool_calls"][0]["name"] == "bash"


class TestToolCallInfo:
    """Tests for ToolCallInfo model."""

    def test_creation_with_required_fields(self):
        """Verify ToolCallInfo can be created with required fields."""
        tc = ToolCallInfo(
            id="tc-1",
            name="bash",
        )
        assert tc.id == "tc-1"
        assert tc.name == "bash"
        assert tc.arguments == {}
        assert tc.output is None

    def test_creation_with_all_fields(self):
        """Verify ToolCallInfo can be created with all fields."""
        tc = ToolCallInfo(
            id="tc-1",
            name="bash",
            arguments={"command": "ls -la"},
            output="file list",
        )
        assert tc.arguments == {"command": "ls -la"}
        assert tc.output == "file list"

    def test_model_dump(self):
        """Verify model_dump returns correct dict."""
        tc = ToolCallInfo(
            id="tc-1",
            name="bash",
            arguments={"cmd": "test"},
            output="passed",
        )
        data = tc.model_dump()
        assert data["id"] == "tc-1"
        assert data["name"] == "bash"
        assert data["arguments"] == {"cmd": "test"}
        assert data["output"] == "passed"
