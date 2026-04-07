"""Unit tests for daemon/response_validation.py."""

import pytest
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage
from langchain_core.messages.tool import ToolCall

from daemon.response_validation import (
    LLMResponseValidationError,
    validate_llm_response,
)


# =============================================================================
# Helper Functions
# =============================================================================


def make_ai_message(**overrides) -> AIMessage:
    """Create an AIMessage with optional overrides."""
    defaults = {
        "content": "Test response content",
        "id": "test-msg-1",
    }
    defaults.update(overrides)
    return AIMessage(**defaults)


# =============================================================================
# Test LLMResponseValidationError
# =============================================================================


class TestLLMResponseValidationError:
    """Tests for the LLMResponseValidationError exception."""

    def test_exception_stores_message(self):
        """Test that exception stores the message."""
        error = LLMResponseValidationError("Test error message")
        assert str(error) == "Test error message"

    def test_exception_stores_response(self):
        """Test that exception stores the response object."""
        response = make_ai_message()
        error = LLMResponseValidationError("Test error", response=response)
        assert error.response is response

    def test_exception_response_can_be_none(self):
        """Test that exception response can be None."""
        error = LLMResponseValidationError("Test error", response=None)
        assert error.response is None


# =============================================================================
# Test Empty Content Validation
# =============================================================================


class TestEmptyContentValidation:
    """Tests for empty content validation.

    Empty content is valid — the graph's should_continue() handles routing
    to END when the model has nothing more to say. Validation does NOT reject
    empty responses.
    """

    def test_empty_string_content_passes(self):
        """Test that empty string content passes validation (not an error)."""
        response = make_ai_message(content="")
        # Should NOT raise — empty content is valid
        validate_llm_response(response)

    def test_whitespace_only_content_passes(self):
        """Test that whitespace-only content passes validation (not an error)."""
        response = make_ai_message(content="   \n\t  ")
        # Should NOT raise — empty content is valid
        validate_llm_response(response)

    def test_empty_content_with_tool_calls_passes(self):
        """Test that empty content WITH tool_calls is valid (does not raise)."""
        response = make_ai_message(
            content="",
            tool_calls=[ToolCall(id="call_1", name="test_tool", args={})],
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_empty_string_content_with_tool_calls_passes(self):
        """Test that empty string content WITH tool_calls is valid (does not raise)."""
        response = make_ai_message(
            content="",
            tool_calls=[ToolCall(id="call_1", name="test_tool", args={"param": "value"})],
        )
        # Should NOT raise
        validate_llm_response(response)


# =============================================================================
# Test Truncation Validation
# =============================================================================


class TestTruncationValidation:
    """Tests for truncation validation (raises LLMResponseValidationError)."""

    def test_finish_reason_length_raises(self):
        """Test that finish_reason='length' raises validation error."""
        response = make_ai_message(
            content="Partial response...",
            response_metadata={"finish_reason": "length"},
        )
        with pytest.raises(LLMResponseValidationError) as exc_info:
            validate_llm_response(response)
        assert "truncated" in str(exc_info.value).lower()
        assert "length" in str(exc_info.value).lower()

    def test_finish_reason_stop_passes(self):
        """Test that finish_reason='stop' passes validation."""
        response = make_ai_message(
            content="Complete response",
            response_metadata={"finish_reason": "stop"},
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_missing_response_metadata_passes(self):
        """Test that missing response_metadata passes (fail-open)."""
        response = make_ai_message(content="Valid response")
        # Remove response_metadata by setting to None
        response.response_metadata = None
        # Should NOT raise (fail-open)
        validate_llm_response(response)

    def test_empty_response_metadata_passes(self):
        """Test that empty response_metadata passes (fail-open)."""
        response = make_ai_message(
            content="Valid response",
            response_metadata={},
        )
        # Should NOT raise (fail-open)
        validate_llm_response(response)

    def test_none_finish_reason_passes(self):
        """Test that None finish_reason passes (fail-open)."""
        response = make_ai_message(
            content="Valid response",
            response_metadata={"finish_reason": None},
        )
        # Should NOT raise (fail-open)
        validate_llm_response(response)


# =============================================================================
# Test Tool Call Validation
# =============================================================================


class TestToolCallValidation:
    """Tests for tool call validation (raises LLMResponseValidationError)."""

    def test_tool_call_with_empty_name_raises(self):
        """Test that tool call with empty function name raises validation error."""
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[ToolCall(id="call_1", name="", args={})],
        )
        with pytest.raises(LLMResponseValidationError) as exc_info:
            validate_llm_response(response)
        assert "empty function name" in str(exc_info.value).lower()

    def test_tool_call_with_whitespace_name_raises(self):
        """Test that tool call with whitespace-only function name raises validation error."""
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[ToolCall(id="call_1", name="   ", args={})],
        )
        with pytest.raises(LLMResponseValidationError) as exc_info:
            validate_llm_response(response)
        assert "empty function name" in str(exc_info.value).lower()

    def test_tool_call_dict_format_with_empty_name_raises(self):
        """Test tool call in dict format with empty function name raises error."""
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[{"id": "call_1", "name": "", "args": {}}],
        )
        with pytest.raises(LLMResponseValidationError) as exc_info:
            validate_llm_response(response)
        assert "empty function name" in str(exc_info.value).lower()

    def test_tool_call_dict_format_with_whitespace_name_raises(self):
        """Test tool call in dict format with whitespace function name raises error."""
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[{"id": "call_1", "name": "  ", "args": {}}],
        )
        with pytest.raises(LLMResponseValidationError) as exc_info:
            validate_llm_response(response)
        assert "empty function name" in str(exc_info.value).lower()

    def test_valid_tool_call_passes(self):
        """Test that valid tool call passes validation."""
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[
                ToolCall(id="call_1", name="test_tool", args={"param": "value"}),
            ],
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_multiple_tool_calls_one_invalid_raises(self):
        """Test that if any tool call is invalid, validation fails."""
        response = make_ai_message(
            content="Calling tools",
            tool_calls=[
                ToolCall(id="call_1", name="valid_tool", args={"param": "value"}),
                ToolCall(id="call_2", name="", args={}),  # Invalid: empty name
            ],
        )
        with pytest.raises(LLMResponseValidationError):
            validate_llm_response(response)

    def test_multiple_valid_tool_calls_passes(self):
        """Test that multiple valid tool calls pass validation."""
        response = make_ai_message(
            content="Calling tools",
            tool_calls=[
                ToolCall(id="call_1", name="bash", args={"command": "echo hello"}),
                ToolCall(id="call_2", name="read_file", args={"path": "/tmp/test.txt"}),
            ],
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_tool_call_with_empty_args_dict_passes(self):
        """Test that tool call with empty dict args passes validation."""
        # Empty dict is valid - it just means no arguments
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[ToolCall(id="call_1", name="test_tool", args={})],
        )
        # Should NOT raise - empty dict is valid
        validate_llm_response(response)


# =============================================================================
# Test Valid Responses
# =============================================================================


class TestValidResponses:
    """Tests for valid responses that should pass validation."""

    def test_normal_response_with_content_passes(self):
        """Test that normal response with content passes."""
        response = make_ai_message(
            content="Hello! How can I help you today?",
            response_metadata={"finish_reason": "stop"},
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_response_with_content_and_tool_calls_passes(self):
        """Test that response with both content and tool calls passes."""
        response = make_ai_message(
            content="I'll help you with that.",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="some_tool",
                    args={"input": "test data"},
                ),
            ],
            response_metadata={"finish_reason": "tool_calls"},
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_tool_only_response_passes(self):
        """Test that response with only tool calls (no content) passes."""
        response = make_ai_message(
            content="",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="bash",
                    args={"command": "echo hello"},
                ),
                ToolCall(
                    id="call_2",
                    name="read_file",
                    args={"path": "/tmp/test.txt"},
                ),
            ],
            response_metadata={"finish_reason": "tool_calls"},
        )
        # Should NOT raise
        validate_llm_response(response)

    def test_response_with_complex_tool_args_passes(self):
        """Test that tool call with complex arguments passes."""
        response = make_ai_message(
            content="",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="search",
                    args={
                        "query": "python async",
                        "filters": {"language": "en", "date_range": "week"},
                        "max_results": 10,
                    },
                ),
            ],
        )
        # Should NOT raise
        validate_llm_response(response)


# =============================================================================
# Test Error Response Attribute
# =============================================================================


class TestErrorResponseAttribute:
    """Tests that LLMResponseValidationError properly stores the response."""

    def test_truncated_error_stores_response(self):
        """Test that truncation error stores the response object."""
        response = make_ai_message(
            content="Partial...",
            response_metadata={"finish_reason": "length"},
        )
        try:
            validate_llm_response(response)
            pytest.fail("Expected LLMResponseValidationError to be raised")
        except LLMResponseValidationError as e:
            assert e.response is response

    def test_malformed_tool_call_error_stores_response(self):
        """Test that malformed tool call error stores the response object."""
        response = make_ai_message(
            content="Calling tool",
            tool_calls=[ToolCall(id="call_1", name="", args={})],
        )
        try:
            validate_llm_response(response)
            pytest.fail("Expected LLMResponseValidationError to be raised")
        except LLMResponseValidationError as e:
            assert e.response is response


# =============================================================================
# Test Fail-Open Behavior
# =============================================================================


class TestFailOpenBehavior:
    """Tests for fail-open behavior when validation cannot determine validity."""

    def test_missing_tool_calls_attribute_passes(self):
        """Test that missing tool_calls attribute passes (fail-open)."""
        response = make_ai_message(content="Valid response")
        # Remove tool_calls attribute
        if hasattr(response, "tool_calls"):
            delattr(response, "tool_calls")
        # Should NOT raise (fail-open)
        validate_llm_response(response)
