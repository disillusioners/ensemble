"""Response validation utilities for LLM interactions.

This module provides structural validation for LLM responses to detect
common failure modes like empty content, truncated responses, and malformed
tool calls.
"""

import logging
from typing import Any

from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


class LLMResponseValidationError(Exception):
    """Raised when an LLM response fails structural validation."""

    def __init__(self, message: str, response: AIMessage | None = None):
        self.response = response
        super().__init__(message)


def validate_llm_response(response: AIMessage) -> None:
    """Validate the structural integrity of an LLM response.

    Performs structural validation checks on an AIMessage response to detect
    common failure modes. Raises LLMResponseValidationError for invalid responses.

    Validation checks (in order):
    1. Truncated response: finish_reason is "length"
    2. Missing tool call data: tool_calls with empty function.name or function.arguments

    Note: Empty content is intentionally NOT validated here. An empty response
    (no content, no tool_calls) is valid — it means the model is done speaking.
    The graph's should_continue() routing handles this correctly by ending the loop.

    Fail-open: If response structure is unexpected or validation cannot determine
    validity (e.g., missing response_metadata field), logs a warning but does NOT raise.
    We'd rather use a questionable response than crash.

    Args:
        response: A LangChain AIMessage object to validate.

    Raises:
        LLMResponseValidationError: If the response fails any structural validation check.
    """
    # Check 1: Truncated response (finish_reason == "length")
    if _is_truncated_response(response):
        raise LLMResponseValidationError(
            "Response was truncated (finish_reason=length)",
            response=response,
        )

    # Check 2: Malformed tool calls (empty function.name or function.arguments)
    if _has_malformed_tool_calls(response):
        raise LLMResponseValidationError(
            "Response has tool calls with empty function name or arguments",
            response=response,
        )


def _is_truncated_response(response: AIMessage) -> bool:
    """Check if response was truncated due to length limits.

    Checks response.response_metadata.get("finish_reason") == "length".

    Returns:
        True if response was truncated.
        False if not truncated or if metadata is missing (fail-open).
    """
    try:
        metadata = getattr(response, "response_metadata", None)
        if metadata is None:
            logger.warning(
                "Response missing response_metadata. Cannot check truncation. "
                "Passing validation."
            )
            return False

        finish_reason = metadata.get("finish_reason")
        if finish_reason is None:
            logger.warning(
                "Response metadata missing finish_reason. Cannot check truncation. "
                "Passing validation."
            )
            return False

        return finish_reason == "length"
    except Exception as e:
        logger.warning(
            f"Error checking truncation metadata: {e}. "
            "Passing validation."
        )
        return False


def _has_malformed_tool_calls(response: AIMessage) -> bool:
    """Check if response has tool calls with empty function.name or function.arguments.

    Tool calls can be in two formats:
    - ToolCall objects with .name and .args attributes
    - Dict format with "name" and "args" keys

    Returns:
        True if any tool call has empty name or arguments.
        False if all tool calls are well-formed or if no tool calls present.
    """
    tool_calls = getattr(response, "tool_calls", None)

    if not tool_calls:
        return False

    for tool_call in tool_calls:
        name = _get_tool_call_name(tool_call)
        args = _get_tool_call_args(tool_call)

        if name is None or (isinstance(name, str) and name.strip() == ""):
            logger.warning(
                f"Tool call has empty function name: {tool_call}. "
                "Failing validation."
            )
            return True

        if args is None or (isinstance(args, str) and args.strip() == ""):
            logger.warning(
                f"Tool call has empty function arguments: {tool_call}. "
                "Failing validation."
            )
            return True

    return False


def _get_tool_call_name(tool_call: Any) -> str | None:
    """Extract function name from a tool call.

    Supports both ToolCall object format (tool_call.name) and
    dict format (tool_call["name"]).

    Returns:
        Function name string or None if not found.
    """
    if isinstance(tool_call, dict):
        return tool_call.get("name")
    elif hasattr(tool_call, "name"):
        return tool_call.name
    return None


def _get_tool_call_args(tool_call: Any) -> Any | None:
    """Extract function arguments from a tool call.

    Supports both ToolCall object format (tool_call.args) and
    dict format (tool_call["args"]).

    Returns:
        Arguments dict/string or None if not found.
    """
    if isinstance(tool_call, dict):
        return tool_call.get("args")
    elif hasattr(tool_call, "args"):
        return tool_call.args
    return None
