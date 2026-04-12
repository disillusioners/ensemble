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
        """Verify extra kwargs are included in event content."""
        await service.on_user_message_stored(
            instance_id="inst-1",
            message_id="msg-1",
            content="Hello",
            source="api",
            priority=1,
        )
        
        call_args = mock_event_bus.create_message_received_event.call_args
        assert call_args is not None
        content = call_args.kwargs.get('content', {})
        assert content.get('priority') == 1


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
        
        # processing_completed event (backward compat)
        mock_event_bus.create_processing_completed_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_completed_has_full_payload(self, service, mock_event_bus):
        """Verify message_completed event contains full message payload."""
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
        assert data.get('original_message_id') == "user-msg-1"
        assert 'message' in data
        message = data['message']
        assert message['role'] == "assistant"
        assert message['content'] == "Response text"
        assert message['thinking'] == "Thinking content"
        assert message['thinking_extracted'] == "Extracted thinking"

    @pytest.mark.asyncio
    async def test_processing_completed_has_full_content(self, service, mock_event_bus):
        """Verify processing_completed event contains full content for backward compat."""
        await service.on_assistant_message_completed(
            instance_id="inst-1",
            original_message_id="user-msg-1",
            content="Response text",
            thinking="Thinking",
        )
        
        call_args = mock_event_bus.create_processing_completed_event.call_args
        assert call_args is not None
        result = call_args.kwargs.get('result', {})
        assert result.get('content') == "Response text"
        assert result.get('thinking') == "Thinking"
        assert result.get('success') is True

    @pytest.mark.asyncio
    async def test_tool_calls_included(self, service, mock_event_bus):
        """Verify tool calls are included in both events."""
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
        message = data['message']
        assert 'tool_calls' in message
        assert len(message['tool_calls']) == 1
        
        # Check processing_completed
        result_call = mock_event_bus.create_processing_completed_event.call_args
        result = result_call.kwargs.get('result', {})
        assert result.get('tool_calls') is not None
        assert len(result['tool_calls']) == 1


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
        """Verify source is correctly formatted as child:instance_id."""
        await service.on_child_completion_report(
            parent_instance_id="parent-1",
            child_instance_id="child-abc-123",
            report_content="Done",
            message_id="msg-1",
        )
        
        call_args = mock_event_bus.create_message_received_event.call_args
        content = call_args.kwargs.get('content', {})
        assert content.get('source') == "child:child-abc-123"
        assert content.get('child_instance_id') == "child-abc-123"


class TestUnifiedMessageFormats:
    """Tests for UnifiedMessage formatting methods."""

    def test_to_sse_data_omits_none_fields(self):
        """Verify to_sse_data omits None fields for clean SSE payload."""
        msg = UnifiedMessage(
            message_id="m1",
            instance_id="i1",
            role="user",
            content="Hi",
        )
        
        data = msg.to_sse_data()
        assert "thinking" not in data
        assert "tool_calls" not in data
        assert "thinking_extracted" not in data
        assert "source" not in data

    def test_to_sse_data_includes_present_fields(self):
        """Verify to_sse_data includes all present fields."""
        msg = UnifiedMessage(
            message_id="m1",
            instance_id="i1",
            role="assistant",
            content="Response",
            thinking="Thinking",
            source="api",
        )
        
        data = msg.to_sse_data()
        assert data["thinking"] == "Thinking"
        assert data["source"] == "api"
        assert "thinking_extracted" not in data

    def test_to_api_response_format(self):
        """Verify to_api_response format for GET /messages."""
        msg = UnifiedMessage(
            message_id="m1",
            instance_id="i1",
            role="assistant",
            content="Response",
        )
        
        resp = msg.to_api_response()
        assert resp["role"] == "assistant"
        assert resp["content"] == "Response"
        assert resp["message_id"] == "m1"
        assert resp["thinking"] is None

    def test_to_api_response_with_tool_calls(self):
        """Verify to_api_response includes tool_calls when present."""
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
        
        resp = msg.to_api_response()
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
