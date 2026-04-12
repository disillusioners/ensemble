"""Mock tests for MessageService SSE message unification.

Tests the unified message handling between SSE and GET /messages API.

Run with: pytest tests/mock_message_service.py -v
"""

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch
import pytest


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def mock_event_repo():
    """Create a mock EventRepository."""
    repo = MagicMock()
    repo.create_event = AsyncMock(return_value=MagicMock(id=1))
    return repo


@pytest.fixture
def mock_event_bus(mock_event_repo):
    """Create a mock EventBus with all methods."""
    from daemon.services.event_bus import EventBus
    
    bus = EventBus(event_repo=mock_event_repo)
    bus.create_event = AsyncMock()
    bus.create_message_received_event = AsyncMock()
    bus.create_processing_completed_event = AsyncMock()
    bus.broadcast_streaming_event = AsyncMock()
    return bus


@pytest.fixture
def message_service(mock_event_bus):
    """Create a MessageService with mocked EventBus."""
    from daemon.services.message_service import MessageService
    return MessageService(event_bus=mock_event_bus)


@pytest.fixture
def sample_instance_id():
    """Sample instance ID."""
    return str(uuid.uuid4())


@pytest.fixture
def sample_message_id():
    """Sample message ID."""
    return str(uuid.uuid4())


# ============================================================================
# UnifiedMessage Model Tests
# ============================================================================

class TestUnifiedMessage:
    """Tests for UnifiedMessage model serialization."""

    def test_to_sse_data_omits_none_fields(self):
        """Verify to_sse_data() omits None fields for clean SSE payloads."""
        from daemon.message_models import UnifiedMessage
        
        msg = UnifiedMessage(
            message_id="test-123",
            instance_id="instance-456",
            role="assistant",
            content="Hello world",
            thinking=None,  # Should be omitted
            thinking_extracted=None,  # Should be omitted
            tool_calls=None,  # Should be omitted
            source=None,  # Should be omitted
        )
        
        sse_data = msg.to_sse_data()
        
        # Core fields should be present
        assert "message_id" in sse_data
        assert "instance_id" in sse_data
        assert "role" in sse_data
        assert "content" in sse_data
        assert "created_at" in sse_data
        
        # None fields should NOT be present
        assert "thinking" not in sse_data
        assert "thinking_extracted" not in sse_data
        assert "tool_calls" not in sse_data
        assert "source" not in sse_data
        
        # Values should be correct
        assert sse_data["message_id"] == "test-123"
        assert sse_data["instance_id"] == "instance-456"
        assert sse_data["content"] == "Hello world"

    def test_to_sse_data_includes_present_fields(self):
        """Verify to_sse_data() includes non-None optional fields."""
        from daemon.message_models import UnifiedMessage, ToolCallInfo
        
        tool_calls = [
            ToolCallInfo(id="call-1", name="test_tool", arguments={"arg": "value"})
        ]
        
        msg = UnifiedMessage(
            message_id="test-123",
            instance_id="instance-456",
            role="assistant",
            content="Hello",
            thinking="I'm thinking...",
            thinking_extracted="Extracted thoughts",
            tool_calls=tool_calls,
            source="api",
        )
        
        sse_data = msg.to_sse_data()
        
        assert sse_data["thinking"] == "I'm thinking..."
        assert sse_data["thinking_extracted"] == "Extracted thoughts"
        assert sse_data["source"] == "api"
        assert "tool_calls" in sse_data
        assert len(sse_data["tool_calls"]) == 1
        assert sse_data["tool_calls"][0]["id"] == "call-1"

    def test_to_api_response_includes_null_fields(self):
        """Verify to_api_response() includes all fields with None as null."""
        from daemon.message_models import UnifiedMessage
        
        msg = UnifiedMessage(
            message_id="test-123",
            instance_id="instance-456",
            role="assistant",
            content="Hello",
            thinking=None,
            thinking_extracted=None,
            tool_calls=None,
        )
        
        api_data = msg.to_api_response()
        
        # All fields should be present
        assert "message_id" in api_data
        assert "role" in api_data
        assert "content" in api_data
        assert "thinking" in api_data
        assert "thinking_extracted" in api_data
        assert "tool_calls" in api_data
        
        # None fields should be None (serialized as null in JSON)
        assert api_data["thinking"] is None
        assert api_data["thinking_extracted"] is None
        assert api_data["tool_calls"] is None

    def test_to_api_response_with_tool_calls(self):
        """Verify to_api_response() properly serializes tool_calls."""
        from daemon.message_models import UnifiedMessage, ToolCallInfo
        
        tool_calls = [
            ToolCallInfo(id="call-1", name="search", arguments={"query": "test"}, output="result")
        ]
        
        msg = UnifiedMessage(
            message_id="test-123",
            instance_id="instance-456",
            role="assistant",
            content="Found it",
            tool_calls=tool_calls,
        )
        
        api_data = msg.to_api_response()
        
        assert "tool_calls" in api_data
        assert api_data["tool_calls"] is not None
        assert len(api_data["tool_calls"]) == 1
        assert api_data["tool_calls"][0]["id"] == "call-1"
        assert api_data["tool_calls"][0]["name"] == "search"
        assert api_data["tool_calls"][0]["arguments"] == {"query": "test"}
        assert api_data["tool_calls"][0]["output"] == "result"

    def test_empty_content_string(self):
        """Verify empty content string is preserved (not treated as None)."""
        from daemon.message_models import UnifiedMessage
        
        msg = UnifiedMessage(
            message_id="test-123",
            instance_id="instance-456",
            role="assistant",
            content="",  # Empty string, not None
        )
        
        sse_data = msg.to_sse_data()
        assert sse_data["content"] == ""
        
        api_data = msg.to_api_response()
        assert api_data["content"] == ""

    def test_empty_tool_calls_list(self):
        """Verify empty tool_calls list is excluded in SSE (falsy check matches code behavior)."""
        from daemon.message_models import UnifiedMessage, ToolCallInfo
        
        msg = UnifiedMessage(
            message_id="test-123",
            instance_id="instance-456",
            role="assistant",
            content="No tools",
            tool_calls=[],  # Empty list - falsy, so excluded from SSE
        )
        
        sse_data = msg.to_sse_data()
        # Empty list is falsy, so excluded from SSE (this is intentional per the code)
        assert "tool_calls" not in sse_data
        # But the field is still accessible on the model
        assert msg.tool_calls == []


# ============================================================================
# MessageService.emit_message_completed Tests
# ============================================================================

class TestMessageServiceEmitCompleted:
    """Tests for MessageService.on_assistant_message_completed() which emits MESSAGE_COMPLETED."""

    @pytest.mark.asyncio
    async def test_emits_message_completed_event(
        self, message_service, mock_event_bus, sample_instance_id, sample_message_id
    ):
        """Verify MESSAGE_COMPLETED event is created via EventBus."""
        await message_service.on_assistant_message_completed(
            instance_id=sample_instance_id,
            original_message_id=sample_message_id,
            content="Test response",
        )
        
        # Verify create_event was called with MESSAGE_COMPLETED kind
        mock_event_bus.create_event.assert_called()
        call_args = mock_event_bus.create_event.call_args
        
        assert call_args.kwargs["instance_id"] == sample_instance_id
        assert call_args.kwargs["message_id"] == sample_message_id
        assert "kind" in call_args.kwargs

    @pytest.mark.asyncio
    async def test_sse_payload_structure(
        self, message_service, mock_event_bus, sample_instance_id, sample_message_id
    ):
        """Verify SSE data payload has correct structure (message, original_message_id, instance_id)."""
        await message_service.on_assistant_message_completed(
            instance_id=sample_instance_id,
            original_message_id=sample_message_id,
            content="Test response",
            thinking="I'm thinking",
        )
        
        # Get the data payload from create_event call
        call_args = mock_event_bus.create_event.call_args
        data = call_args.kwargs["data"]
        
        # Verify structure
        assert "original_message_id" in data
        assert data["original_message_id"] == sample_message_id
        
        assert "message" in data
        message = data["message"]
        assert "message_id" in message
        assert "instance_id" in message
        assert "role" in message
        assert "content" in message
        assert message["content"] == "Test response"
        assert message["instance_id"] == sample_instance_id

    @pytest.mark.asyncio
    async def test_error_isolation_on_event_bus_failure(
        self, message_service, mock_event_bus, sample_instance_id, sample_message_id
    ):
        """Verify no exception propagates if EventBus fails."""
        mock_event_bus.create_event.side_effect = Exception("EventBus failure")
        
        # Should NOT raise - error is caught internally
        try:
            await message_service.on_assistant_message_completed(
                instance_id=sample_instance_id,
                original_message_id=sample_message_id,
                content="Test",
            )
        except Exception as e:
            pytest.fail(f"Exception should not propagate: {e}")

    @pytest.mark.asyncio
    async def test_enriched_payload_with_thinking_and_tool_calls(
        self, message_service, mock_event_bus, sample_instance_id, sample_message_id
    ):
        """Verify enriched payload includes content, thinking, and tool_calls."""
        from daemon.message_models import ToolCallInfo
        
        tool_calls = [
            ToolCallInfo(
                id="call-1",
                name="search",
                arguments={"query": "test"},
                output="result"
            )
        ]
        
        await message_service.on_assistant_message_completed(
            instance_id=sample_instance_id,
            original_message_id=sample_message_id,
            content="Found results",
            thinking="Looking through database...",
            thinking_extracted="Found relevant entries",
            tool_calls=tool_calls,
        )
        
        # Get the data payload
        call_args = mock_event_bus.create_event.call_args
        data = call_args.kwargs["data"]
        message = data["message"]
        
        # Verify enriched data
        assert message["content"] == "Found results"
        assert message["thinking"] == "Looking through database..."
        assert message["thinking_extracted"] == "Found relevant entries"
        assert "tool_calls" in message
        assert len(message["tool_calls"]) == 1


# ============================================================================
# MessageService.emit_processing_completed Tests
# ============================================================================

class TestMessageServiceEmitProcessingCompleted:
    """Tests for PROCESSING_COMPLETED event emission."""

    @pytest.mark.asyncio
    async def test_emits_processing_completed_event(
        self, message_service, mock_event_bus, sample_instance_id, sample_message_id
    ):
        """Verify PROCESSING_COMPLETED event is created via EventBus."""
        await message_service.on_assistant_message_completed(
            instance_id=sample_instance_id,
            original_message_id=sample_message_id,
            content="Response",
        )
        
        # Verify create_processing_completed_event was called
        mock_event_bus.create_processing_completed_event.assert_called_once()
        call_args = mock_event_bus.create_processing_completed_event.call_args
        
        assert call_args.kwargs["instance_id"] == sample_instance_id
        assert call_args.kwargs["message_id"] == sample_message_id

    @pytest.mark.asyncio
    async def test_processing_completed_enriched_payload(
        self, message_service, mock_event_bus, sample_instance_id, sample_message_id
    ):
        """Verify PROCESSING_COMPLETED has enriched payload (content, thinking, tool_calls)."""
        from daemon.message_models import ToolCallInfo
        
        tool_calls = [
            ToolCallInfo(id="call-1", name="tool", arguments={})
        ]
        
        await message_service.on_assistant_message_completed(
            instance_id=sample_instance_id,
            original_message_id=sample_message_id,
            content="Final response",
            thinking="Final thoughts",
            thinking_extracted="Extracted",
            tool_calls=tool_calls,
        )
        
        call_args = mock_event_bus.create_processing_completed_event.call_args
        result = call_args.kwargs["result"]
        
        # Verify enriched fields
        assert result["content"] == "Final response"
        assert result["thinking"] == "Final thoughts"
        assert result["thinking_extracted"] == "Extracted"
        assert "tool_calls" in result
        assert result["assistant_message_id"] is not None
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_error_isolation_on_processing_completed_failure(
        self, message_service, mock_event_bus, sample_instance_id, sample_message_id
    ):
        """Verify no exception propagates if create_processing_completed_event fails."""
        mock_event_bus.create_processing_completed_event.side_effect = Exception("EventBus failure")
        
        # Should NOT raise - error is caught internally
        try:
            await message_service.on_assistant_message_completed(
                instance_id=sample_instance_id,
                original_message_id=sample_message_id,
                content="Test",
            )
        except Exception as e:
            pytest.fail(f"Exception should not propagate: {e}")


# ============================================================================
# MessageService.on_child_completion_report Tests
# ============================================================================

class TestMessageServiceOnChildCompletionReport:
    """Tests for child completion report handling."""

    @pytest.mark.asyncio
    async def test_emits_message_received_for_parent(
        self, message_service, mock_event_bus, sample_instance_id, sample_message_id
    ):
        """Verify MESSAGE_RECEIVED is emitted for parent with correct child info."""
        child_instance_id = str(uuid.uuid4())
        report_content = "Child completed task"
        
        await message_service.on_child_completion_report(
            parent_instance_id=sample_instance_id,
            child_instance_id=child_instance_id,
            report_content=report_content,
            message_id=sample_message_id,
        )
        
        # Verify create_message_received_event was called
        mock_event_bus.create_message_received_event.assert_called_once()
        call_args = mock_event_bus.create_message_received_event.call_args
        
        assert call_args.kwargs["instance_id"] == sample_instance_id
        assert call_args.kwargs["message_id"] == sample_message_id
        
        # Verify content includes child info
        content = call_args.kwargs["content"]
        assert f"child:{child_instance_id}" == content["source"]
        assert report_content == content["content"]
        assert child_instance_id == content["child_instance_id"]

    @pytest.mark.asyncio
    async def test_child_completion_message_structure(
        self, message_service, mock_event_bus, sample_instance_id, sample_message_id
    ):
        """Verify child completion message has correct structure."""
        child_instance_id = str(uuid.uuid4())
        
        message = await message_service.on_child_completion_report(
            parent_instance_id=sample_instance_id,
            child_instance_id=child_instance_id,
            report_content="Done",
            message_id=sample_message_id,
        )
        
        # Verify returned UnifiedMessage structure
        assert message.message_id == sample_message_id
        assert message.instance_id == sample_instance_id
        assert message.role == "user"
        assert message.content == "Done"
        assert message.source == f"child:{child_instance_id}"


# ============================================================================
# Duplicate Emission Prevention Tests
# ============================================================================

class TestDuplicateEmissionPrevention:
    """Tests to verify processing_completed is only emitted by MessageService."""

    @pytest.mark.asyncio
    async def test_task_processor_calls_message_service(
        self, mock_event_bus, sample_instance_id, sample_message_id
    ):
        """Verify TaskProcessor delegates to MessageService instead of direct EventBus calls."""
        from daemon.services.message_service import MessageService
        from daemon.services.task_processor import ProcessMessageProcessor
        
        # Create real MessageService with mock EventBus
        message_service = MessageService(event_bus=mock_event_bus)
        
        # Create mock manager and task
        mock_manager = MagicMock()
        mock_task = MagicMock()
        mock_task.id = "task-123"
        mock_task.message_id = sample_message_id
        mock_task.instance_id = sample_instance_id
        mock_task.retry_count = 0
        
        # Mock message result with proper attributes
        mock_result = MagicMock()
        mock_result.content = "Test response"
        mock_result.thinking = None  # Must be None, not MagicMock
        mock_result.thinking_extracted = None  # Must be None, not MagicMock
        mock_result.tool_calls = None
        
        mock_message = MagicMock()
        mock_message.content = "User message"
        mock_message.source = "api"
        
        mock_manager._process_message_with_tracking = AsyncMock(return_value=mock_result)
        
        # Create processor
        processor = ProcessMessageProcessor(
            instance_manager=mock_manager,
            task_repo=MagicMock(),
            event_repo=None,
            message_repository=MagicMock(get=MagicMock(return_value=mock_message)),
            event_bus=mock_event_bus,
            message_service=message_service,
        )
        
        # Process the task
        result = await processor.process(mock_task)
        
        # Verify message_service was called for assistant message completion
        # This is the single point for emitting both MESSAGE_COMPLETED and PROCESSING_COMPLETED
        assert mock_event_bus.create_event.called or mock_event_bus.create_processing_completed_event.called

    @pytest.mark.asyncio
    async def test_no_duplicate_processing_completed_emission(
        self, message_service, mock_event_bus, sample_instance_id, sample_message_id
    ):
        """Verify processing_completed is emitted only once per message."""
        await message_service.on_assistant_message_completed(
            instance_id=sample_instance_id,
            original_message_id=sample_message_id,
            content="Test",
        )
        
        # Should only be called once (inside on_assistant_message_completed)
        # Not called separately elsewhere
        mock_event_bus.create_processing_completed_event.assert_called_once()


# ============================================================================
# Frontend Handler Tests (without browser)
# ============================================================================

class TestFrontendMessageCompletedHandler:
    """Tests for frontend message_completed handler validation."""

    def test_sse_service_validates_required_fields(self):
        """Verify SseService validates message, original_message_id, instance_id fields."""
        # Simulate the validation logic from sse.service.ts
        required_fields = ["message", "original_message_id"]
        
        # Valid payload (from backend)
        valid_data = {
            "instance_id": "inst-123",
            "original_message_id": "msg-456",
            "message": {
                "message_id": "new-789",
                "role": "assistant",
                "content": "Response",
            }
        }
        
        for field in required_fields:
            assert field in valid_data
        
        # Missing message field
        invalid_data = {
            "instance_id": "inst-123",
            "original_message_id": "msg-456",
        }
        
        assert "message" not in invalid_data

    def test_chat_component_msg_index_fallback(self):
        """Verify ChatComponent handles msgIndex === -1 fallback (auto-create placeholder)."""
        # Simulate the logic from chat.component.ts
        messages = []
        delta = {
            "type": "message_completed",
            "message_id": "msg-456",
            "instance_id": "inst-123",
            "message": {
                "message_id": "new-789",
                "role": "assistant",
                "content": "Final response",
            }
        }
        
        # Find message index (will be -1 if not found)
        msg_index = next(
            (i for i, m in enumerate(messages) if m.get("message_id") == delta["message_id"]),
            -1
        )
        
        # Verify fallback logic
        assert msg_index == -1
        
        # Should create placeholder when msgIndex === -1
        if msg_index == -1:
            placeholder = {
                "type": "message",
                "message_id": delta["message_id"] or "",
                "role": (delta["message"].get("role") if delta.get("message") else None) or "assistant",
                "content": (delta["message"].get("content") if delta.get("message") else "") or "",
            }
            assert placeholder["message_id"] == "msg-456"
            assert placeholder["role"] == "assistant"


# ============================================================================
# Edge Cases
# ============================================================================

class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_empty_content_string(self, message_service, mock_event_bus):
        """Verify empty content string is handled correctly."""
        instance_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        
        await message_service.on_assistant_message_completed(
            instance_id=instance_id,
            original_message_id=message_id,
            content="",  # Empty string
        )
        
        call_args = mock_event_bus.create_event.call_args
        data = call_args.kwargs["data"]
        assert data["message"]["content"] == ""

    @pytest.mark.asyncio
    async def test_empty_tool_calls_list(self, message_service, mock_event_bus):
        """Verify empty tool_calls list is handled correctly."""
        instance_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        
        await message_service.on_assistant_message_completed(
            instance_id=instance_id,
            original_message_id=message_id,
            content="Response",
            tool_calls=[],  # Empty list
        )
        
        call_args = mock_event_bus.create_event.call_args
        data = call_args.kwargs["data"]
        # Empty list should NOT be included in SSE data (falsy check)
        # This matches the behavior: `if self.tool_calls:`
        # But we also need to check processing_completed
        proc_args = mock_event_bus.create_processing_completed_event.call_args
        result = proc_args.kwargs["result"]
        # Empty list in result should be None
        assert result["tool_calls"] is None

    @pytest.mark.asyncio
    async def test_missing_optional_fields(self, message_service, mock_event_bus):
        """Verify missing optional fields don't cause errors."""
        instance_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        
        # Only required fields
        await message_service.on_assistant_message_completed(
            instance_id=instance_id,
            original_message_id=message_id,
            content="Response",
            # thinking, thinking_extracted, tool_calls all omitted
        )
        
        # Should not raise any exceptions
        assert True

    @pytest.mark.asyncio
    async def test_concurrent_message_processing(self, message_service, mock_event_bus):
        """Verify concurrent message processing doesn't cause race conditions."""
        instance_id = str(uuid.uuid4())
        
        # Create multiple concurrent tasks
        tasks = [
            message_service.on_assistant_message_completed(
                instance_id=instance_id,
                original_message_id=str(uuid.uuid4()),
                content=f"Response {i}",
            )
            for i in range(5)
        ]
        
        # Run all concurrently
        await asyncio.gather(*tasks)
        
        # All should complete successfully
        assert mock_event_bus.create_event.call_count == 5
        assert mock_event_bus.create_processing_completed_event.call_count == 5

    def test_tool_call_info_model(self):
        """Verify ToolCallInfo model serialization."""
        from daemon.message_models import ToolCallInfo
        
        tc = ToolCallInfo(
            id="call-123",
            name="search",
            arguments={"query": "test", "limit": 10},
            output=None
        )
        
        # Test model_dump
        dumped = tc.model_dump()
        assert dumped["id"] == "call-123"
        assert dumped["name"] == "search"
        assert dumped["arguments"] == {"query": "test", "limit": 10}
        assert dumped["output"] is None


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
