"""
Phase 3: Multi-turn Graph Resume Validation Tests.

Tests LangGraph checkpoint-based resume after various failure scenarios:
1. LLM failure (APIConnectionError)
2. Transient API error (TransientAPIError)  
3. Tool failure

Uses real LangGraph with AsyncSqliteSaver (in-memory) and mock LLM.
LLM invoke() is SYNC (production uses sync invoke in async agent_node).
"""

import sys
import pytest
import asyncio
import tempfile
from typing import Optional, Any
from datetime import datetime
from unittest.mock import MagicMock

pytestmark = pytest.mark.integration

# =============================================================================
# UNMOCKING: Restore real langgraph modules for integration tests
# Pattern from test_message_queue_e2e.py
# =============================================================================
_original_modules = {}
_langgraph_keys = [
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
]
for key in _langgraph_keys:
    if key in sys.modules:
        _original_modules[key] = sys.modules[key]
        del sys.modules[key]


def pytest_sessionfinish(session, exitstatus):
    """Restore mock modules after all tests run."""
    for key in _langgraph_keys:
        if key in _original_modules:
            sys.modules[key] = _original_modules[key]
        elif key in sys.modules:
            del sys.modules[key]


# =============================================================================
# MOCK LLM CLASS (sync invoke)
# =============================================================================

class MockLLMWithTools:
    """Mock LLM that returns configurable responses and tracks invocations.
    
    LLM invoke() is SYNC - matches production agent_node pattern.
    """
    
    def __init__(self, responses: list):
        """
        Args:
            responses: List of responses. Each can be:
                - AIMessage: Return this message
                - Exception: Raise this exception
        """
        self.responses = responses
        self.call_count = 0
        self.calls = []  # Track call details
    
    def invoke(self, messages: list, stop=None, run_manager=None, **kwargs) -> Any:
        """Sync invoke - matches what production agent_node calls."""
        self.call_count += 1
        call_info = {
            'call_number': self.call_count,
            'message_count': len(messages),
        }
        self.calls.append(call_info)
        
        response = self.responses[self.call_count - 1]
        if isinstance(response, Exception):
            raise response
        return response
    
    def bind_tools(self, tools):
        """Return self for chaining."""
        return self


# =============================================================================
# TOOL HELPERS (shared state for failure injection)
# =============================================================================

from langchain_core.tools import tool

# Shared state for tool_b failure injection across graph instances
_tool_b_state = {'call_count': 0, 'should_fail': True}


def reset_tool_b_state():
    """Reset tool_b state before each test."""
    _tool_b_state['call_count'] = 0
    _tool_b_state['should_fail'] = True  # Reset to default (failing)


@pytest.fixture(autouse=True)
def auto_reset_tool_b():
    """Auto-reset tool_b state before and after each test."""
    reset_tool_b_state()
    yield
    reset_tool_b_state()


def set_tool_b_should_fail(fail: bool):
    """Configure whether tool_b should fail."""
    _tool_b_state['should_fail'] = fail


@tool
def tool_a() -> str:
    """Tool A does operation A."""
    return {"result": "tool_a_success", "timestamp": datetime.now().isoformat()}


@tool
def tool_b() -> str:
    """Tool B does operation B."""
    _tool_b_state['call_count'] += 1
    if _tool_b_state['should_fail'] and _tool_b_state['call_count'] == 1:
        raise RuntimeError("tool_b failed on first call")
    return {"result": "tool_b_success", "timestamp": datetime.now().isoformat()}


def get_tool_definitions():
    """Return tool definitions for testing."""
    return [tool_a, tool_b]


def create_tools():
    """Create tools list for testing."""
    return [tool_a, tool_b]


# =============================================================================
# GRAPH BUILDER
# =============================================================================

def build_test_graph(checkpointer, mock_llm, tools, system_prompt: str = "You are a helpful assistant."):
    """Build a test graph with agent and tools nodes."""
    from langgraph.graph import StateGraph, MessagesState, START, END
    from langgraph.prebuilt import ToolNode
    
    def should_continue(state: MessagesState) -> str:
        messages = state["messages"]
        last_message = messages[-1]
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            return "tools"
        return END
    
    async def agent_node(state):
        messages = state['messages']
        # Production uses SYNC invoke in async function
        response = mock_llm.invoke(messages)
        return {'messages': [response]}
    
    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    
    compiled = graph.compile(checkpointer=checkpointer)
    return compiled


# =============================================================================
# HELPERS
# =============================================================================

async def setup_in_memory_checkpointer():
    """Create in-memory checkpointer."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    import aiosqlite
    
    # Use file-based SQLite with :memory: for sharing across connections
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    checkpointer = AsyncSqliteSaver(conn)
    await checkpointer.setup()
    return checkpointer, conn


# =============================================================================
# TEST 1: Resume after LLM failure (APIConnectionError)
# =============================================================================

@pytest.mark.asyncio
async def test_resume_after_llm_failure_preserves_state():
    """Test that checkpoint resume preserves state after LLM API failure.
    
    Flow:
    1. Mock LLM returns tool_a call, then tool_b call, then raises APIConnectionError
    2. Run graph until failure
    3. Build new graph with new mock LLM (same checkpointer, same thread_id)
    4. New LLM returns final answer
    5. Resume with astream(None, config)
    6. Verify: all messages present, tools called once each, resume LLM called once
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    
    set_tool_b_should_fail(False)  # Tools should succeed, only LLM fails
    
    checkpointer, conn = await setup_in_memory_checkpointer()
    thread_id = "test-llm-failure-thread"
    
    try:
        # === Phase 1: Run with failing LLM ===
        
        # Mock LLM: tool_a -> tool_b -> APIConnectionError
        tool_call_1 = AIMessage(
            content="",
            tool_calls=[{"name": "tool_a", "args": {}, "id": "call_1", "type": "tool_call"}]
        )
        tool_call_2 = AIMessage(
            content="",
            tool_calls=[{"name": "tool_b", "args": {}, "id": "call_2", "type": "tool_call"}]
        )
        api_error = Exception("APIConnectionError: Connection failed")
        
        mock_llm_1 = MockLLMWithTools([tool_call_1, tool_call_2, api_error])
        tools_1 = create_tools()
        
        graph_1 = build_test_graph(checkpointer, mock_llm_1, tools_1)
        config_1 = {"configurable": {"thread_id": thread_id}}
        
        # Run graph - should fail on third LLM call
        with pytest.raises(Exception, match="APIConnectionError"):
            async for event in graph_1.astream(
                {"messages": [HumanMessage(content="Do tool_a then tool_b")]},
                config_1
            ):
                pass
        
        # Verify first two LLM calls happened
        assert mock_llm_1.call_count == 3, f"Expected 3 calls, got {mock_llm_1.call_count}"
        
        # === Phase 2: Resume with new graph and LLM ===
        
        # New mock LLM returns final answer
        final_answer = AIMessage(content="All done!")
        mock_llm_2 = MockLLMWithTools([final_answer])
        tools_2 = create_tools()
        
        # NEW graph with same checkpointer and thread_id
        graph_2 = build_test_graph(checkpointer, mock_llm_2, tools_2)
        config_2 = {"configurable": {"thread_id": thread_id}}
        
        # Resume with None input
        results = []
        async for event in graph_2.astream(None, config_2):
            results.append(event)
        
        # Verify resume LLM called once
        assert mock_llm_2.call_count == 1, f"Expected 1 resume call, got {mock_llm_2.call_count}"
        
        # Get final state
        final_state = await graph_2.aget_state(config_2)
        messages = final_state.values.get('messages', [])
        
        # Verify all messages: user -> tool_a_call -> tool_a_result -> tool_b_call -> tool_b_result -> final_answer
        assert len(messages) >= 6, f"Expected at least 6 messages, got {len(messages)}"
        
        # Verify tool results
        tool_results = [m for m in messages if isinstance(m, ToolMessage)]
        assert len(tool_results) == 2, f"Expected 2 tool results, got {len(tool_results)}"
        
        # Verify final answer
        assert messages[-1].content == "All done!"
        
        # Verify message order: HumanMessage -> AIMessage(tool_a) -> ToolMessage -> AIMessage(tool_b) -> ToolMessage -> AIMessage(final)
        assert isinstance(messages[0], HumanMessage), "First message should be HumanMessage"
        assert isinstance(messages[1], AIMessage) and messages[1].tool_calls, "Second should be AIMessage with tool_a call"
        assert isinstance(messages[2], ToolMessage), "Third should be ToolMessage for tool_a"
        assert isinstance(messages[3], AIMessage) and messages[3].tool_calls, "Fourth should be AIMessage with tool_b call"
        assert isinstance(messages[4], ToolMessage), "Fifth should be ToolMessage for tool_b"
        assert isinstance(messages[5], AIMessage) and not messages[5].tool_calls, "Sixth should be final AIMessage"
        
        print(f"✅ Test 1 passed: {len(messages)} messages, resume LLM called {mock_llm_2.call_count} time(s)")
        
    finally:
        await conn.close()


# =============================================================================
# TEST 2: Resume after TransientAPIError
# =============================================================================

@pytest.mark.asyncio
async def test_resume_after_transient_api_error():
    """Test that checkpoint resume preserves state after TransientAPIError.
    
    Flow:
    1. Mock LLM returns tool_call, then raises TransientAPIError
    2. Run graph until failure
    3. Resume with new LLM returning final answer
    4. Verify tool result was preserved (not re-executed)
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from daemon.llm_error_classifier import TransientAPIError
    import openai
    from httpx import Response, Request
    
    checkpointer, conn = await setup_in_memory_checkpointer()
    thread_id = "test-transient-error-thread"
    
    try:
        # === Phase 1: Run with transient error ===
        
        tool_call = AIMessage(
            content="",
            tool_calls=[{"name": "tool_a", "args": {}, "id": "call_1", "type": "tool_call"}]
        )
        # Create a mock APIStatusError with retryable status code
        mock_request = Request("POST", "http://test.com")
        mock_response = Response(429, request=mock_request)
        mock_api_error = openai.APIStatusError(
            message="Rate limit hit",
            response=mock_response,
            body=None
        )
        transient_error = TransientAPIError(mock_api_error)
        
        mock_llm_1 = MockLLMWithTools([tool_call, transient_error])
        tools_1 = create_tools()
        
        graph_1 = build_test_graph(checkpointer, mock_llm_1, tools_1)
        config_1 = {"configurable": {"thread_id": thread_id}}
        
        # Run graph - should fail on second LLM call
        with pytest.raises(TransientAPIError):
            async for event in graph_1.astream(
                {"messages": [HumanMessage(content="Call tool_a")]},
                config_1
            ):
                pass
        
        assert mock_llm_1.call_count == 2, f"Expected 2 calls, got {mock_llm_1.call_count}"
        
        # Verify tool_a result is in state
        state_after_failure = await graph_1.aget_state(config_1)
        messages_after = state_after_failure.values.get('messages', [])
        tool_results = [m for m in messages_after if isinstance(m, ToolMessage)]
        assert len(tool_results) == 1
        assert "tool_a_success" in tool_results[0].content
        
        # === Phase 2: Resume with new LLM ===
        
        final_answer = AIMessage(content="Recovery successful!")
        mock_llm_2 = MockLLMWithTools([final_answer])
        tools_2 = create_tools()
        
        graph_2 = build_test_graph(checkpointer, mock_llm_2, tools_2)
        config_2 = {"configurable": {"thread_id": thread_id}}
        
        results = []
        async for event in graph_2.astream(None, config_2):
            results.append(event)
        
        assert mock_llm_2.call_count == 1, f"Expected 1 call, got {mock_llm_2.call_count}"
        
        # Verify final state
        final_state = await graph_2.aget_state(config_2)
        messages = final_state.values.get('messages', [])
        
        # Should have: user, tool_call, tool_result, final_answer
        assert len(messages) >= 4, f"Expected at least 4 messages, got {len(messages)}"
        
        # Verify tool result was preserved (not re-executed)
        tool_results = [m for m in messages if isinstance(m, ToolMessage)]
        assert len(tool_results) == 1, f"Expected 1 tool result, got {len(tool_results)}"
        
        # Verify final answer
        assert messages[-1].content == "Recovery successful!"
        
        # Verify message order: HumanMessage -> AIMessage(tool) -> ToolMessage -> AIMessage(final)
        assert isinstance(messages[0], HumanMessage), "First message should be HumanMessage"
        assert isinstance(messages[1], AIMessage) and messages[1].tool_calls, "Second should be AIMessage with tool call"
        assert isinstance(messages[2], ToolMessage), "Third should be ToolMessage"
        assert isinstance(messages[3], AIMessage) and not messages[3].tool_calls, "Fourth should be final AIMessage"
        
        print(f"✅ Test 2 passed: {len(messages)} messages, tool result preserved")
        
    finally:
        await conn.close()


# =============================================================================
# TEST 3: Resume after Tool Failure
# =============================================================================

@pytest.mark.asyncio
async def test_resume_after_tool_failure():
    """Test that checkpoint resume re-executes failed tool and preserves other results.
    
    Flow:
    1. Mock LLM returns tool_a call, then tool_b call
    2. tool_a succeeds, tool_b fails on first call
    3. Run graph until tool_b fails
    4. Resume with tool_b now succeeding
    5. Verify tool_a result preserved, tool_b re-executed
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    
    set_tool_b_should_fail(True)  # tool_b fails on first call
    
    checkpointer, conn = await setup_in_memory_checkpointer()
    thread_id = "test-tool-failure-thread"
    
    try:
        # === Phase 1: Run with tool_b failing ===
        
        tool_call_1 = AIMessage(
            content="",
            tool_calls=[{"name": "tool_a", "args": {}, "id": "call_1", "type": "tool_call"}]
        )
        tool_call_2 = AIMessage(
            content="",
            tool_calls=[{"name": "tool_b", "args": {}, "id": "call_2", "type": "tool_call"}]
        )
        
        mock_llm_1 = MockLLMWithTools([tool_call_1, tool_call_2])
        tools_1 = create_tools()
        
        graph_1 = build_test_graph(checkpointer, mock_llm_1, tools_1)
        config_1 = {"configurable": {"thread_id": thread_id}}
        
        # Run graph - should fail on tool_b execution
        with pytest.raises(RuntimeError, match="tool_b failed"):
            async for event in graph_1.astream(
                {"messages": [HumanMessage(content="Run tool_a then tool_b")]},
                config_1
            ):
                pass
        
        # Verify LLM called twice (once for each tool request)
        assert mock_llm_1.call_count == 2, f"Expected 2 calls, got {mock_llm_1.call_count}"
        
        # Verify tool_a succeeded and tool_b failed
        state_after_failure = await graph_1.aget_state(config_1)
        messages_after = state_after_failure.values.get('messages', [])
        
        # Should have: user, tool_a_call, tool_a_result, tool_b_call (no result yet)
        assert len(messages_after) == 4, f"Expected 4 messages, got {len(messages_after)}"
        
        # Verify tool_a result present
        tool_results = [m for m in messages_after if isinstance(m, ToolMessage)]
        assert len(tool_results) == 1
        assert "tool_a_success" in tool_results[0].content
        
        # === Phase 2: Resume with tool_b now succeeding ===
        
        # New mock LLM returns final answer
        final_answer = AIMessage(content="Both tools completed!")
        mock_llm_2 = MockLLMWithTools([final_answer])
        
        # Configure tool_b to succeed now
        set_tool_b_should_fail(False)
        tools_2 = create_tools()
        
        graph_2 = build_test_graph(checkpointer, mock_llm_2, tools_2)
        config_2 = {"configurable": {"thread_id": thread_id}}
        
        results = []
        async for event in graph_2.astream(None, config_2):
            results.append(event)
        
        # Resume LLM should be called once
        assert mock_llm_2.call_count == 1, f"Expected 1 resume call, got {mock_llm_2.call_count}"
        
        # Get final state
        final_state = await graph_2.aget_state(config_2)
        messages = final_state.values.get('messages', [])
        
        # Should have all messages including tool_b result and final answer
        # user, tool_a_call, tool_a_result, tool_b_call, tool_b_result, final_answer
        assert len(messages) >= 6, f"Expected at least 6 messages, got {len(messages)}"
        
        # Verify tool results
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        assert len(tool_messages) == 2, f"Expected 2 tool results, got {len(tool_messages)}"
        
        # tool_a result preserved
        assert "tool_a_success" in tool_messages[0].content
        
        # tool_b result now present
        assert "tool_b_success" in tool_messages[1].content
        
        # Verify final answer
        assert messages[-1].content == "Both tools completed!"
        
        # Verify message order: HumanMessage -> AIMessage(tool_a) -> ToolMessage -> AIMessage(tool_b) -> ToolMessage -> AIMessage(final)
        assert isinstance(messages[0], HumanMessage), "First message should be HumanMessage"
        assert isinstance(messages[1], AIMessage) and messages[1].tool_calls, "Second should be AIMessage with tool_a call"
        assert isinstance(messages[2], ToolMessage), "Third should be ToolMessage for tool_a"
        assert isinstance(messages[3], AIMessage) and messages[3].tool_calls, "Fourth should be AIMessage with tool_b call"
        assert isinstance(messages[4], ToolMessage), "Fifth should be ToolMessage for tool_b"
        assert isinstance(messages[5], AIMessage) and not messages[5].tool_calls, "Sixth should be final AIMessage"
        
        print(f"✅ Test 3 passed: {len(messages)} messages, tool_b re-executed successfully")
        
    finally:
        await conn.close()


# =============================================================================
# RUN DIRECTLY
# =============================================================================

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
