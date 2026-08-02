"""Tests for the nudge behavior in the graph.

This module tests:
- should_continue routing function
- _is_empty_content helper
- _has_recent_tool_result helper
- NUDGE_MESSAGE constant
- nudge_node function
- build_instance_graph with nudge node
"""

import pytest
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCall

from daemon.graph import (
    should_continue,
    _is_empty_content,
    _has_recent_tool_result,
    NUDGE_MESSAGE,
    nudge_node,
    build_instance_graph,
)


class TestIsEmptyContent:
    """Unit tests for _is_empty_content helper function."""

    def test_none_returns_true(self):
        """None content should be considered empty."""
        assert _is_empty_content(None) is True

    def test_empty_string_returns_true(self):
        """Empty string should be considered empty."""
        assert _is_empty_content("") is True

    def test_whitespace_only_string_returns_true(self):
        """Whitespace-only strings should be considered empty."""
        assert _is_empty_content("   \n  ") is True
        assert _is_empty_content("\t") is True
        assert _is_empty_content("  ") is True

    def test_non_empty_string_returns_false(self):
        """Non-empty strings should not be considered empty."""
        assert _is_empty_content("Hello") is False
        assert _is_empty_content("  Hello  ") is False
        assert _is_empty_content(".") is False

    def test_non_string_returns_false(self):
        """Non-string types should not be considered empty."""
        assert _is_empty_content([]) is False
        assert _is_empty_content({}) is False
        assert _is_empty_content(123) is False


class TestHasRecentToolResult:
    """Unit tests for _has_recent_tool_result helper function."""

    def _make_state(self, messages):
        """Helper to create a mock state with messages."""
        return {"messages": messages}

    def test_tool_message_right_before_empty_ai_returns_true(self):
        """ToolMessage immediately before empty AI should return True."""
        messages = [
            HumanMessage(content="Do something"),
            AIMessage(content="", tool_calls=[ToolCall(id="call_1", name="test", args={})]),
            ToolMessage(content="result", tool_call_id="call_1"),
            AIMessage(content=""),  # empty response
        ]
        assert _has_recent_tool_result(messages) is True

    def test_ai_message_then_tool_message_before_empty_ai_returns_true(self):
        """ToolMessage preceded by AIMessage should still return True."""
        messages = [
            HumanMessage(content="Do something"),
            AIMessage(content="Let me check...", tool_calls=[ToolCall(id="call_1", name="test", args={})]),
            ToolMessage(content="result", tool_call_id="call_1"),
            AIMessage(content="Thinking..."),
            AIMessage(content=""),  # empty response
        ]
        assert _has_recent_tool_result(messages) is True

    def test_only_human_message_before_empty_ai_returns_false(self):
        """Only HumanMessage before empty AI should return False."""
        messages = [
            HumanMessage(content="Do something"),
            AIMessage(content=""),  # empty response
        ]
        assert _has_recent_tool_result(messages) is False

    def test_tool_message_separated_by_human_message_returns_false(self):
        """ToolMessage separated by HumanMessage boundary should return False."""
        messages = [
            HumanMessage(content="First task"),
            AIMessage(content="", tool_calls=[ToolCall(id="call_1", name="test", args={})]),
            ToolMessage(content="result1", tool_call_id="call_1"),
            HumanMessage(content="Second task"),  # boundary
            AIMessage(content=""),  # empty response (but from different turn)
        ]
        assert _has_recent_tool_result(messages) is False

    def test_empty_messages_except_last_returns_false(self):
        """Empty messages list (except last) should return False."""
        messages = [AIMessage(content="")]
        assert _has_recent_tool_result(messages) is False

    def test_no_tool_message_returns_false(self):
        """Messages without any ToolMessage should return False."""
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there!"),
            AIMessage(content=""),  # empty response
        ]
        assert _has_recent_tool_result(messages) is False


class TestShouldContinue:
    """Unit tests for the should_continue routing function."""

    def _make_state(self, messages):
        """Helper to create a mock state with messages."""
        return {"messages": messages}

    def test_empty_content_after_tool_result_returns_nudge(self):
        """Empty content after tool result should route to nudge."""
        messages = [
            HumanMessage(content="Do something"),
            AIMessage(content="", tool_calls=[ToolCall(id="call_1", name="test", args={})]),
            ToolMessage(content="result", tool_call_id="call_1"),
            AIMessage(content=""),  # empty response after tool
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "nudge"

    def test_empty_content_with_whitespace_after_tool_result_returns_nudge(self):
        """Empty content with whitespace after tool result should route to nudge."""
        messages = [
            HumanMessage(content="Do something"),
            AIMessage(content="", tool_calls=[ToolCall(id="call_1", name="test", args={})]),
            ToolMessage(content="result", tool_call_id="call_1"),
            AIMessage(content="   \n  "),  # whitespace only
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "nudge"

    def test_empty_content_no_tool_result_returns_end(self):
        """Empty content without tool result should return END."""
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content=""),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "__end__"

    def test_empty_content_with_tool_calls_returns_tools(self):
        """Empty content but with tool_calls should route to tools."""
        messages = [
            HumanMessage(content="Do something"),
            AIMessage(content="", tool_calls=[ToolCall(id="call_1", name="test", args={})]),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "tools"

    def test_ghost_promise_returns_agent(self):
        """Content ending with ':' (ghost promise) should route to agent."""
        messages = [
            HumanMessage(content="Write a file"),
            AIMessage(content="Now I will:"),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "agent"

    def test_normal_content_returns_end(self):
        """Normal content without tool_calls should return END."""
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Here is my response"),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "__end__"

    def test_tool_result_separated_by_human_message_returns_end(self):
        """Tool result from earlier turn (separated by HumanMessage) should return END."""
        messages = [
            HumanMessage(content="First task"),
            AIMessage(content="", tool_calls=[ToolCall(id="call_1", name="test", args={})]),
            ToolMessage(content="result1", tool_call_id="call_1"),
            HumanMessage(content="Second task"),  # boundary
            AIMessage(content=""),  # empty response (but from different turn)
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "__end__"

    def test_tool_calls_takes_priority_over_ghost_promise(self):
        """tool_calls should take priority over ghost promise detection."""
        messages = [
            HumanMessage(content="Do something"),
            AIMessage(content="Now I will:", tool_calls=[ToolCall(id="call_1", name="test", args={})]),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "tools"

    def test_thinking_only_response_routes_to_agent(self):
        """A response with reasoning_content but no content/tool_calls should
        route back to 'agent' so the LLM can produce the final answer on the
        next call. This preserves the original Claude-style extended thinking
        behavior.
        """
        messages = [
            HumanMessage(content="Think about this"),
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "an AI, let me think hard..."},
            ),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "agent"

    def test_response_with_reasoning_and_content_returns_end(self):
        """A response with BOTH reasoning_content AND content should end the
        graph. This is the streaming model case (GLM/DeepSeek) where the model
        emits thinking + final answer in a single response. Re-invoking the
        LLM in this case would overwrite the response with one that lacks
        reasoning_content, breaking the web UI's 'show thinking' feature.
        """
        messages = [
            HumanMessage(content="Tell me a joke"),
            AIMessage(
                content="Why did the chicken cross the road?",
                additional_kwargs={"reasoning_content": "an AI, the user wants a joke..."},
            ),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "__end__"

    def test_response_with_reasoning_and_tool_calls_returns_tools(self):
        """A response with reasoning_content AND tool_calls should route to
        'tools'. tool_calls has higher priority than the thinking-only check.
        """
        messages = [
            HumanMessage(content="Search for X"),
            AIMessage(
                content="",
                tool_calls=[ToolCall(id="call_1", name="search", args={"q": "X"})],
                additional_kwargs={"reasoning_content": "need to search..."},
            ),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "tools"

    def test_response_with_reasoning_and_ghost_promise_returns_agent(self):
        """Ghost promise (content ending with ':') should still route to
        'agent' even when reasoning_content is present, because the model
        explicitly promised to continue.
        """
        messages = [
            HumanMessage(content="Write a file"),
            AIMessage(
                content="Now I will:",
                additional_kwargs={"reasoning_content": "I need to write a file..."},
            ),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "agent"

    def test_empty_content_with_reasoning_no_tool_result_returns_end(self):
        """Empty content + reasoning_content without a recent tool result
        should still end (not nudge). The thinking-only re-route is the
        primary continuation mechanism now.
        """
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "the user said hello..."},
            ),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "agent"

    def test_think_only_content_routes_to_agent(self):
        """A response with content that is ONLY <think>...</think> tags
        (no visible text) and no tool_calls should route back to 'agent'
        so the LLM can produce the actual answer on the next call. This
        handles models that embed reasoning as <think> tags inside the
        content string rather than via additional_kwargs.reasoning_content.
        """
        messages = [
            HumanMessage(content="Help me with auth"),
            AIMessage(content="<think>The user wants auth help, let me think...</think>"),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "agent"

    def test_think_only_content_with_whitespace_routes_to_agent(self):
        """A response whose content is only <think> tags plus surrounding
        whitespace (no visible text) should also re-route to agent."""
        messages = [
            HumanMessage(content="Help me"),
            AIMessage(content="  \n<think>reasoning</think>\n  "),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "agent"

    def test_think_then_visible_text_returns_end(self):
        """A response like `<think>reasoning</think>Actual answer` should
        fall through to normal routing and END — not loop re-invoking the
        agent. The visible text after the think tags is the real answer.
        """
        messages = [
            HumanMessage(content="Tell me a joke"),
            AIMessage(content="<think>Thinking about jokes...</think>Why did the chicken cross the road?"),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "__end__"

    def test_think_only_with_tool_calls_returns_tools(self):
        """A response with only <think> content but WITH tool_calls should
        route to 'tools' — tool_calls has higher priority than the
        think-only check, matching the existing reasoning_content behavior.
        """
        messages = [
            HumanMessage(content="Search for X"),
            AIMessage(
                content="<think>Need to search...</think>",
                tool_calls=[ToolCall(id="call_1", name="search", args={"q": "X"})],
            ),
        ]
        state = self._make_state(messages)
        assert should_continue(state) == "tools"


class TestNudgeMessage:
    """Tests for NUDGE_MESSAGE constant."""

    def test_nudge_message_is_defined(self):
        """NUDGE_MESSAGE should be defined."""
        assert NUDGE_MESSAGE is not None

    def test_nudge_message_is_string(self):
        """NUDGE_MESSAGE should be a string."""
        assert isinstance(NUDGE_MESSAGE, str)

    def test_nudge_message_contains_continue(self):
        """NUDGE_MESSAGE should mention continuing."""
        assert "Continue" in NUDGE_MESSAGE

    def test_nudge_message_contains_finished(self):
        """NUDGE_MESSAGE should mention finishing."""
        assert "finished" in NUDGE_MESSAGE


class TestNudgeNode:
    """Tests for the nudge_node function."""

    def test_nudge_node_is_callable(self):
        """nudge_node should be defined and callable."""
        assert callable(nudge_node)

    def test_nudge_injects_human_message(self):
        """nudge_node should return a HumanMessage with NUDGE_MESSAGE."""
        result = nudge_node({"messages": []})
        assert "messages" in result
        assert len(result["messages"]) == 1
        msg = result["messages"][0]
        assert isinstance(msg, HumanMessage)
        assert msg.content == NUDGE_MESSAGE


class TestBuildInstanceGraph:
    """Tests for build_instance_graph with nudge node."""

    @pytest.fixture
    def mock_checkpointer(self):
        """Create mock checkpointer."""
        return MagicMock()

    @pytest.fixture
    def mock_tools(self):
        """Create mock tools list."""
        return [MagicMock(name="tool1"), MagicMock(name="tool2")]

    def test_graph_compiles_with_nudge_node(self, mock_checkpointer, mock_tools):
        """Graph should compile successfully with the nudge node."""
        with patch('daemon.graph.ThinkingChatOpenAI') as mock_llm_class:
            # Setup mock LLM
            mock_llm_instance = MagicMock()
            mock_llm_with_tools = MagicMock()
            mock_llm_instance.bind_tools.return_value = mock_llm_with_tools
            mock_llm_class.return_value = mock_llm_instance

            # Mock langgraph components
            with patch('daemon.graph.StateGraph') as mock_state_graph:
                mock_graph_instance = MagicMock()
                mock_compiled = MagicMock()
                mock_graph_instance.compile.return_value = mock_compiled
                mock_state_graph.return_value = mock_graph_instance

                with patch('daemon.graph.ToolNode'):
                    graph = build_instance_graph(
                        tools=mock_tools,
                        checkpointer=mock_checkpointer,
                        llm_config={"model": "gpt-4o", "api_key": "test"},
                        system_prompt="You are a test agent.",
                    )

                    # Verify graph was compiled
                    assert graph is mock_compiled

                    # Verify StateGraph was called
                    mock_state_graph.assert_called_once()

    def test_nudge_node_exists_in_graph(self, mock_checkpointer, mock_tools):
        """The compiled graph should have the nudge node accessible."""
        with patch('daemon.graph.ThinkingChatOpenAI') as mock_llm_class:
            mock_llm_instance = MagicMock()
            mock_llm_with_tools = MagicMock()
            mock_llm_instance.bind_tools.return_value = mock_llm_with_tools
            mock_llm_class.return_value = mock_llm_instance

            with patch('daemon.graph.StateGraph') as mock_state_graph:
                mock_graph_instance = MagicMock()
                mock_compiled = MagicMock()
                mock_graph_instance.compile.return_value = mock_compiled
                mock_state_graph.return_value = mock_graph_instance

                with patch('daemon.graph.ToolNode'):
                    graph = build_instance_graph(
                        tools=mock_tools,
                        checkpointer=mock_checkpointer,
                        llm_config={"model": "gpt-4o", "api_key": "test"},
                        system_prompt="You are a test agent.",
                    )

                    # The graph should have been built successfully
                    assert graph is not None

    def test_nudge_in_conditional_edges_mapping(self, mock_checkpointer, mock_tools):
        """The conditional edges from agent should include 'nudge' route."""
        with patch('daemon.graph.ThinkingChatOpenAI') as mock_llm_class:
            mock_llm_instance = MagicMock()
            mock_llm_with_tools = MagicMock()
            mock_llm_instance.bind_tools.return_value = mock_llm_with_tools
            mock_llm_class.return_value = mock_llm_instance

            with patch('daemon.graph.StateGraph') as mock_state_graph:
                mock_graph_instance = MagicMock()
                mock_compiled = MagicMock()
                mock_graph_instance.compile.return_value = mock_compiled
                mock_state_graph.return_value = mock_graph_instance

                with patch('daemon.graph.ToolNode'):
                    build_instance_graph(
                        tools=mock_tools,
                        checkpointer=mock_checkpointer,
                        llm_config={"model": "gpt-4o", "api_key": "test"},
                        system_prompt="You are a test agent.",
                    )

                    # Verify add_conditional_edges was called
                    add_conditional_edges_calls = mock_graph_instance.add_conditional_edges.call_args_list
                    assert len(add_conditional_edges_calls) > 0

                    # Check the routing dict includes "nudge"
                    # add_conditional_edges is called with:
                    # - source_node (str)
                    # - routing_function (function)
                    # - routing_mapping (dict) - the third positional arg
                    nudge_routed = False
                    for call in add_conditional_edges_calls:
                        args = call[0]
                        if len(args) >= 3:
                            routing_dict = args[2]  # third positional arg is the routing dict
                            if isinstance(routing_dict, dict) and "nudge" in routing_dict:
                                nudge_routed = True
                                break
                    assert nudge_routed, "nudge should be in the conditional edges routing"

    def test_nudge_constant_is_accessible_from_module(self):
        """NUDGE_MESSAGE should be importable from the module."""
        from daemon.graph import NUDGE_MESSAGE
        assert NUDGE_MESSAGE == "Continue with your task, or provide your final response if you are finished."

    def test_should_continue_is_accessible_from_module(self):
        """should_continue should be importable from the module."""
        from daemon.graph import should_continue
        assert callable(should_continue)

    def test_helper_functions_are_accessible_from_module(self):
        """Helper functions should be importable from the module."""
        from daemon.graph import _is_empty_content, _has_recent_tool_result
        assert callable(_is_empty_content)
        assert callable(_has_recent_tool_result)
