"""Graph package - re-exports all public names for backward compatibility."""
import logging

# Re-export logger for modules that need it
logger = logging.getLogger(__name__)

# Re-export langgraph components FIRST (needed by builder.py)
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

# Re-export classifier/retry for mocking compatibility
from ..llm_error_classifier import classify_llm_errors
from tenacity import Retrying

# Now import from submodules
# Re-export from thinking_llm module
from .thinking_llm import ThinkingChatOpenAI

# Re-export from state module
from .state import SessionState

# Re-export from nodes module
from .nodes import (
    should_continue,
    _is_empty_content,
    _has_recent_tool_result,
    nudge_node,
    create_agent_node,
    NUDGE_MESSAGE,
)

# Re-export from llm_builder module
from .llm_builder import build_instance_llms

# Re-export from builder module
from .builder import build_instance_graph, build_session_graph

__all__ = [
    # Logger
    "logger",
    # Classes
    "ThinkingChatOpenAI",
    "SessionState",
    # Functions and constants from nodes
    "should_continue",
    "_is_empty_content",
    "_has_recent_tool_result",
    "nudge_node",
    "create_agent_node",
    "NUDGE_MESSAGE",
    # Functions from llm_builder
    "build_instance_llms",
    # Functions from builder
    "build_instance_graph",
    "build_session_graph",
    # Re-exports for mocking compatibility
    "StateGraph",
    "ToolNode",
    "classify_llm_errors",
    "Retrying",
]
