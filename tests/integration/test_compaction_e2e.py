"""Integration tests for context compaction with real LangGraph and SQLite checkpointer.

These tests validate end-to-end compaction behavior:
- Real LangGraph StateGraph with SessionState
- Real SQLite checkpointer (AsyncSqliteSaver) for checkpointing
- Mocked LLM calls (ThinkingChatOpenAI) to avoid real API requests

IMPORTANT: These tests must restore real langgraph modules because the root
conftest.py mocks them for unit tests. This is done in the restore_modules fixture.

Run with:
    pytest tests/integration/test_compaction_e2e.py -v
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import aiosqlite
import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from pydantic import Field

pytestmark = pytest.mark.integration

# =============================================================================
# Test Tools
# =============================================================================


class EchoTool(BaseTool):
    """Simple echo tool for testing tool calls after compaction."""

    name: str = "echo"
    description: str = "Echoes the input text back"

    text: str = Field(default="", description="Text to echo back")

    def _run(self, text: str) -> str:
        return f"echo: {text}"

    async def _arun(self, text: str) -> str:
        return f"echo: {text}"


# =============================================================================
# Fixture: Restore real langgraph modules (undo root conftest mocking)
# =============================================================================


@pytest.fixture(autouse=True)
def restore_langgraph_modules():
    """Restore real langgraph modules for integration tests.

    The root conftest.py mocks langgraph modules globally for unit tests.
    This fixture undoes that so integration tests can use real langgraph.
    """
    # Store current mocked state
    original_modules = {}
    mock_keys = [
        "langgraph",
        "langgraph.graph",
        "langgraph.graph.state",
        "langgraph.prebuilt",
        "langgraph.constants",
        "langgraph.checkpoint",
        "langgraph.checkpoint.sqlite",
        "langgraph.checkpoint.sqlite.aio",
    ]

    for key in mock_keys:
        if key in sys.modules:
            original_modules[key] = sys.modules[key]

    # Clear the mocked modules so real ones are loaded
    for key in mock_keys:
        if key in sys.modules:
            del sys.modules[key]

    # Clear daemon modules from cache so they re-import with real langgraph
    modules_to_clear = [
        "daemon.compaction",
        "daemon.graph",
        "daemon.manager",
        "daemon.persistence",
    ]
    for mod_name in modules_to_clear:
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    yield

    # Restore original mocked state (for other tests)
    for key in mock_keys:
        if key in original_modules:
            sys.modules[key] = original_modules[key]


# =============================================================================
# Lazy imports (must be after fixture restores modules)
# =============================================================================


def _import_daemon_modules():
    """Import daemon modules after langgraph modules are restored."""
    from daemon.compaction import (
        CompactionContext,
        CompactionResult,
        ContextCompactor,
        identify_boundary_groups,
    )
    from daemon.config import CompactionConfig as CompactionConfigModel
    from daemon.ensemble_config import EnsembleConfig
    from daemon.graph import SessionState, build_session_graph
    from daemon.loader import estimate_messages_tokens
    from daemon.persistence import get_checkpointer
    return {
        "CompactionContext": CompactionContext,
        "CompactionResult": CompactionResult,
        "ContextCompactor": ContextCompactor,
        "identify_boundary_groups": identify_boundary_groups,
        "CompactionConfigModel": CompactionConfigModel,
        "EnsembleConfig": EnsembleConfig,
        "SessionState": SessionState,
        "build_session_graph": build_session_graph,
        "estimate_messages_tokens": estimate_messages_tokens,
        "get_checkpointer": get_checkpointer,
    }


def _get_real_async_sqlite_saver():
    """Get the real AsyncSqliteSaver class after modules are restored."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    return AsyncSqliteSaver


# =============================================================================
# Helpers
# =============================================================================


def make_compaction_config(**overrides):
    """Create a CompactionConfig with optional overrides."""
    # Import lazy to get real module
    CompactionConfigModel = _import_daemon_modules()["CompactionConfigModel"]
    defaults = {
        "enabled": True,
        "threshold": 0.80,
        "recent_message_window": 10,
        "min_recent_window": 3,
        "context_window_overrides": {},
        "context_window_default": 0,
        "target_ratio": 0.40,
        "summarization_model": "",
        "min_messages_before_compaction": 10,
        "summarization_chunk_threshold": 0.60,
    }
    defaults.update(overrides)
    return CompactionConfigModel(**defaults)


def build_conversation_history(
    num_pairs: int = 12,
    content_prefix: str = "Conversation turn",
):
    """Build a realistic alternating Human/AI conversation with IDs.

    Returns a list with 2*num_pairs messages.
    """
    messages = []
    for i in range(num_pairs):
        messages.append(
            HumanMessage(
                content=f"{content_prefix}: user asks about topic {i}. " * 5,
                id=f"human-{i}",
            )
        )
        messages.append(
            AIMessage(
                content=f"{content_prefix}: assistant answers topic {i}. " * 5,
                id=f"ai-{i}",
            )
        )
    return messages


def build_tool_conversation():
    """Build a conversation with interleaved tool calls and responses.

    Creates a realistic pattern:
        Human -> AI(tool_call) -> ToolMessage -> AI(response) -> Human -> ...
    """
    messages = []
    idx = 0

    for turn in range(5):
        # Human message
        messages.append(
            HumanMessage(
                content=f"User request {turn}: please check the files and read config. " * 3,
                id=f"human-{idx}",
            )
        )
        idx += 1

        # AI message with tool call
        tool_call_id = f"tc-{turn}"
        messages.append(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": tool_call_id,
                        "name": "bash",
                        "args": {"command": f"echo 'checking turn {turn}'"},
                    }
                ],
                id=f"ai-toolcall-{idx}",
            )
        )
        idx += 1

        # Tool response
        messages.append(
            ToolMessage(
                content=f"Tool output for turn {turn}: checking turn {turn}\nEXIT CODE: 0",
                tool_call_id=tool_call_id,
                name="bash",
                id=f"tool-{idx}",
            )
        )
        idx += 1

        # AI response after tool
        messages.append(
            AIMessage(
                content=f"I checked the files for request {turn}. Everything looks good. " * 3,
                id=f"ai-response-{idx}",
            )
        )
        idx += 1

    return messages


def create_mock_llm(response_content: str = "I can help with that."):
    """Create a mock ThinkingChatOpenAI that returns predictable responses.

    The mock supports:
    - .invoke() for direct calls (used by agent node)
    - .bind_tools() for tool binding - returns a callable with .invoke
    """
    mock_response = AIMessage(content=response_content, id="mock-ai-response")

    # Create the base mock
    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke = MagicMock(return_value=mock_response)

    # bind_tools should return a callable that has .invoke that returns the response
    mock_bound = MagicMock()
    mock_bound.invoke = MagicMock(return_value=mock_response)
    mock_llm_instance.bind_tools = MagicMock(return_value=mock_bound)
    return mock_llm_instance


def create_mock_llm_with_tool_calls(response_content: str = "Done."):
    """Create a mock LLM that returns AIMessage with tool_calls.

    The mock returns an AI message with a tool_call on first invoke,
    then a plain response on second invoke.
    """
    call_count = 0

    def invoke_side_effect(messages):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            # First call: return message with tool_calls
            # Use the modern langchain-core tool_call format
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "name": "echo",
                        "args": {"text": "hello"},
                    }
                ],
                id="mock-ai-toolcall",
            )
        else:
            # Subsequent calls: return plain response
            return AIMessage(content=response_content, id="mock-ai-response")

    # Create the base mock
    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke = MagicMock(side_effect=invoke_side_effect)

    # bind_tools should return a callable that has .invoke that returns the tool_calls response
    mock_bound = MagicMock()
    mock_bound.invoke = MagicMock(side_effect=invoke_side_effect)
    mock_llm_instance.bind_tools = MagicMock(return_value=mock_bound)
    return mock_llm_instance


async def setup_in_memory_checkpointer():
    """Create an in-memory SQLite checkpointer for testing.

    Returns:
        Tuple of (checkpointer, connection) - connection must be closed after use.
    """
    AsyncSqliteSaver = _get_real_async_sqlite_saver()
    conn = await aiosqlite.connect(":memory:")
    checkpointer = AsyncSqliteSaver(conn)
    await checkpointer.setup()
    return checkpointer, conn


# =============================================================================
# Test 1: Compaction and Graph Continuation
# =============================================================================


@pytest.mark.asyncio
async def test_compaction_and_graph_continuation():
    """Test that compaction works end-to-end with a real graph.

    Steps:
    1. Build a real graph with SessionState, echo tool, and in-memory checkpointer
    2. Mock ThinkingChatOpenAI for predictable LLM responses (with tool_calls)
    3. Build 20+ message history and inject into graph via aupdate_state
    4. Trigger compaction via ContextCompactor.compact_state()
    5. Apply result to graph via aupdate_state
    6. Send new message via graph.ainvoke()
    7. Verify tool call works: human -> AI with tool_calls -> tool execution -> AI response
    8. Verify ToolMessage appears in results after compaction
    """
    # Import daemon modules lazily
    daemon = _import_daemon_modules()
    SessionState = daemon["SessionState"]
    build_session_graph = daemon["build_session_graph"]
    ContextCompactor = daemon["ContextCompactor"]
    CompactionContext = daemon["CompactionContext"]
    CompactionResult = daemon["CompactionResult"]
    estimate_messages_tokens = daemon["estimate_messages_tokens"]

    checkpointer, conn = await setup_in_memory_checkpointer()
    session_id = "test-compaction-continuation"

    # Create echo tool for testing tool calls
    echo_tool = EchoTool()

    # Create mock LLM that returns tool_calls on first call, then plain response
    mock_llm_with_tools = create_mock_llm_with_tool_calls(
        response_content="After compaction, I can still help you."
    )

    # Create plain mock for compaction (no tools needed)
    mock_plain_response = AIMessage(
        content="Summary of previous conversation: user asked about various topics, "
        "assistant provided helpful answers on topics 0-7.",
        id="mock-summary",
    )
    mock_summary_llm = MagicMock()
    mock_summary_llm.invoke = MagicMock(return_value=mock_plain_response)

    try:
        # Build 24 messages (12 pairs)
        messages = build_conversation_history(num_pairs=12)
        assert len(messages) == 24

        config = {"configurable": {"thread_id": session_id}}

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_summary_llm):
            # Build graph with real checkpointer AND echo tool
            graph = build_session_graph(
                tools=[echo_tool],  # Include echo tool for tool call testing
                checkpointer=checkpointer,
                llm_config={
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "test-key",
                    "model": "gpt-4o",
                    "temperature": 0.7,
                    "request_timeout": 60,
                },
                system_prompt="You are a helpful assistant.",
            )

            # Inject message history into graph state
            await graph.aupdate_state(
                config,
                {"messages": messages},
                as_node="agent",
            )

            # Verify state was stored
            state = await graph.aget_state(config)
            assert state is not None
            stored_messages = state.values.get("messages", [])
            assert len(stored_messages) == 24

        # Now compact the state using ContextCompactor (outside patch, with separate mock)
        compaction_config = make_compaction_config(
            context_window_overrides={"gpt-4o": 1000},  # Small window to trigger compaction easily
            threshold=0.50,
            recent_message_window=4,
            min_recent_window=2,
            min_messages_before_compaction=5,
        )

        # Estimate tokens to verify compaction will be triggered
        history_tokens = estimate_messages_tokens(messages)
        system_prompt_tokens = 50
        total_tokens = history_tokens + system_prompt_tokens
        context_window = compaction_config.context_window_overrides["gpt-4o"]  # 1000

        # Our messages should exceed the threshold for compaction
        # Each message is ~20+ tokens, 24 messages = ~480+ tokens, plus overhead
        # With 1000 context window and 0.50 threshold = 500 threshold
        assert total_tokens > context_window * compaction_config.threshold, (
            f"Test messages ({total_tokens} tokens) should exceed threshold "
            f"({context_window * compaction_config.threshold}). "
            f"Adjust message count or content length."
        )

        compactor = ContextCompactor(
            config=compaction_config,
            llm_config={
                "base_url": "http://localhost:1234/v1",
                "api_key": "test-key",
                "model": "gpt-4o",
                "temperature": 0.7,
                "request_timeout": 60,
            },
        )

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_summary_llm):
            context = CompactionContext(
                messages=messages,
                system_prompt_tokens=system_prompt_tokens,
                model_name="gpt-4o",
                config=compaction_config,
                llm_config={
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "test-key",
                    "model": "gpt-4o",
                    "temperature": 0.7,
                    "request_timeout": 60,
                },
                last_compacted_at=None,
            )

            result = await compactor.compact_state(context)

        # Verify compaction occurred
        assert result is not None, "Compaction should have been triggered"
        assert isinstance(result, CompactionResult)
        assert result.tokens_before > 0
        assert result.compacted_at is not None
        assert result.messages_before == 24
        assert result.messages_after < 24, "Should have fewer messages after compaction"

        # Verify replacement messages contain RemoveMessage entries
        remove_msgs = [m for m in result.replacement_messages if isinstance(m, RemoveMessage)]
        assert len(remove_msgs) > 0, "Should have RemoveMessage entries for deleted messages"

        # Verify replacement messages contain a summary
        summary_msgs = [
            m for m in result.replacement_messages
            if isinstance(m, SystemMessage) and "Summary" in (m.content or "")
        ]
        assert len(summary_msgs) > 0, "Should contain a summary SystemMessage"

        # Rebuild the graph with the tool-calling mock (to ensure bound tools have correct invoke)
        # The graph needs to be rebuilt because bind_tools needs to return a mock with proper invoke
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm_with_tools):
            graph_with_tools = build_session_graph(
                tools=[echo_tool],
                checkpointer=checkpointer,
                llm_config={
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "test-key",
                    "model": "gpt-4o",
                    "temperature": 0.7,
                    "request_timeout": 60,
                },
                system_prompt="You are a helpful assistant.",
            )

            # Apply compaction result to the NEW graph
            await graph_with_tools.aupdate_state(
                config,
                {"messages": result.replacement_messages},
                as_node="agent",
            )

            # Set compacted_at timestamp in state
            if result.compacted_at:
                await graph_with_tools.aupdate_state(
                    config,
                    {"compacted_at": result.compacted_at},
                    as_node="agent",
                )

            # Verify compacted state
            state = await graph_with_tools.aget_state(config)
            compacted_messages = state.values.get("messages", [])
            # Filter out RemoveMessage instances
            actual_messages = [
                m for m in compacted_messages
                if not isinstance(m, RemoveMessage)
            ]
            assert len(actual_messages) < 24, "State should have fewer messages after compaction"
            assert state.values.get("compacted_at") == result.compacted_at

            # Send a new message through the graph - this invokes the LLM
            # The mock LLM returns tool_calls on first call, then a plain response
            invoke_result = await graph_with_tools.ainvoke(
                {"messages": [HumanMessage(content="After compaction test", id="post-compact-1")]},
                config,
            )

            # Verify the graph responded
            result_messages = invoke_result.get("messages", [])
            assert len(result_messages) > 0, "Graph should return messages"

            # Verify tool call pipeline worked:
            # 1. Find the AI message with tool_calls
            ai_with_toolcall = None
            tool_message = None
            final_ai_response = None

            for msg in result_messages:
                if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                    ai_with_toolcall = msg
                elif isinstance(msg, ToolMessage):
                    tool_message = msg
                elif isinstance(msg, AIMessage) and not (hasattr(msg, "tool_calls") and msg.tool_calls):
                    final_ai_response = msg

            # Verify the full pipeline: human -> AI with tool_calls -> tool -> AI response
            assert ai_with_toolcall is not None, (
                "Should have an AI message with tool_calls after compaction. "
                f"Messages: {[type(m).__name__ for m in result_messages]}"
            )
            assert ai_with_toolcall.tool_calls is not None
            assert len(ai_with_toolcall.tool_calls) > 0
            # Check tool name - could be accessed as 'name' or 'function.name'
            tc = ai_with_toolcall.tool_calls[0]
            tool_name = tc.get("name") or tc.get("function", {}).get("name")
            assert tool_name == "echo", (
                f"Tool call should be 'echo', got: {tc}"
            )

            assert tool_message is not None, (
                "Should have a ToolMessage after the AI tool_call. "
                f"Messages: {[type(m).__name__ for m in result_messages]}"
            )
            assert tool_message.content == "echo: hello", (
                f"ToolMessage should contain 'echo: hello', got: {tool_message.content}"
            )

            assert final_ai_response is not None, (
                "Should have a final AI response after tool execution. "
                f"Messages: {[type(m).__name__ for m in result_messages]}"
            )
            assert final_ai_response.content == "After compaction, I can still help you."

    finally:
        await conn.close()


# =============================================================================
# Test 2: Crash Recovery After Compaction
# =============================================================================


@pytest.mark.asyncio
async def test_crash_recovery_after_compaction():
    """Test that compacted state survives connection close and re-open.

    Steps:
    1. Create temp file SQLite DB
    2. Build history, compact, close connection
    3. Open new connection with same DB file
    4. Verify compacted state is restored
    5. Send message works with recovered state
    """
    # Import daemon modules lazily
    daemon = _import_daemon_modules()
    build_session_graph = daemon["build_session_graph"]
    ContextCompactor = daemon["ContextCompactor"]
    CompactionContext = daemon["CompactionContext"]
    estimate_messages_tokens = daemon["estimate_messages_tokens"]
    get_checkpointer = daemon["get_checkpointer"]
    EnsembleConfig = daemon["EnsembleConfig"]

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        session_id = "test-crash-recovery"
        config = {"configurable": {"thread_id": session_id}}

        # Phase 1: Build state and compact.
        # Phase 2 of the SQLite→PostgreSQL migration moved get_checkpointer to a
        # config-aware dispatcher that returns a CheckpointerAdapter. Tests that
        # wire the saver directly into build_session_graph use ``raw_saver``.
        ensemble_config = EnsembleConfig(
            database="sqlite",
            sqlite={
                "checkpoints_db": db_path,
                # instances_db isn't used by this test, but EnsembleConfig
                # requires both fields to be set; point to a sibling temp path.
                "instances_db": db_path,
            },
        )
        adapter1 = await get_checkpointer(ensemble_config)
        checkpointer1 = adapter1.raw_saver
        await checkpointer1.setup()

        # Build message history
        messages = build_conversation_history(num_pairs=10)
        assert len(messages) == 20

        compaction_config = make_compaction_config(
            context_window_overrides={"gpt-4o": 800},
            threshold=0.50,
            recent_message_window=3,
            min_recent_window=2,
            min_messages_before_compaction=5,
        )

        compactor = ContextCompactor(
            config=compaction_config,
            llm_config={
                "base_url": "http://localhost:1234/v1",
                "api_key": "test-key",
                "model": "gpt-4o",
                "temperature": 0.7,
                "request_timeout": 60,
            },
        )

        # Compact
        mock_summary_response = AIMessage(
            content="Summary: user and assistant discussed topics 0-6.",
            id="mock-summary-crash",
        )
        mock_summary_llm = MagicMock()
        mock_summary_llm.invoke = MagicMock(return_value=mock_summary_response)

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_summary_llm):
            context = CompactionContext(
                messages=messages,
                system_prompt_tokens=30,
                model_name="gpt-4o",
                config=compaction_config,
                llm_config={
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "test-key",
                    "model": "gpt-4o",
                    "temperature": 0.7,
                    "request_timeout": 60,
                },
                last_compacted_at=None,
            )

            result = await compactor.compact_state(context)

        assert result is not None, "Compaction should trigger"

        mock_llm = create_mock_llm("Recovered and responding.")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            graph1 = build_session_graph(
                tools=[],
                checkpointer=checkpointer1,
                llm_config={
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "test-key",
                    "model": "gpt-4o",
                    "temperature": 0.7,
                    "request_timeout": 60,
                },
                system_prompt="You are a helpful assistant.",
            )

            # Inject messages
            await graph1.aupdate_state(
                config,
                {"messages": messages},
                as_node="agent",
            )

            # Apply compaction result
            await graph1.aupdate_state(
                config,
                {"messages": result.replacement_messages},
                as_node="agent",
            )

            if result.compacted_at:
                await graph1.aupdate_state(
                    config,
                    {"compacted_at": result.compacted_at},
                    as_node="agent",
                )

            # Verify state before close
            state1 = await graph1.aget_state(config)
            assert state1.values.get("compacted_at") == result.compacted_at

        # ---- Simulate crash: close connection ----
        await checkpointer1.conn.close()

        # ---- Phase 2: Recover from same DB file ----
        # Re-open via the same EnsembleConfig; the adapter returns a fresh
        # AsyncSqliteSaver bound to the same on-disk DB file.
        adapter2 = await get_checkpointer(ensemble_config)
        checkpointer2 = adapter2.raw_saver
        await checkpointer2.setup()

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            graph2 = build_session_graph(
                tools=[],
                checkpointer=checkpointer2,
                llm_config={
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "test-key",
                    "model": "gpt-4o",
                    "temperature": 0.7,
                    "request_timeout": 60,
                },
                system_prompt="You are a helpful assistant.",
            )

            # Verify compacted state is restored
            state2 = await graph2.aget_state(config)
            assert state2 is not None, "State should be recovered from DB"

            recovered_messages = state2.values.get("messages", [])
            # Filter out RemoveMessage instances
            actual_messages = [
                m for m in recovered_messages
                if not isinstance(m, RemoveMessage)
            ]
            assert len(actual_messages) < 20, (
                "Recovered state should have compacted (fewer) messages"
            )

            # Verify compacted_at timestamp persisted
            recovered_compacted_at = state2.values.get("compacted_at")
            assert recovered_compacted_at == result.compacted_at, (
                "compacted_at timestamp should survive crash recovery"
            )

            # Send a new message with recovered graph
            invoke_result = await graph2.ainvoke(
                {"messages": [HumanMessage(content="Post-recovery message", id="recovery-1")]},
                config,
            )

            result_messages = invoke_result.get("messages", [])
            assert len(result_messages) > 0

            last_msg = result_messages[-1]
            assert last_msg.type == "ai"
            assert last_msg.content == "Recovered and responding."

        await checkpointer2.conn.close()

    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


# =============================================================================
# Test 3: Dedup via Session State
# =============================================================================


@pytest.mark.asyncio
async def test_dedup_via_session_state():
    """Test that compaction dedup works via compacted_at timestamp in state.

    Steps:
    1. Compact session and apply result to graph state including compacted_at
    2. Verify state contains compacted_at
    3. Attempt re-compaction - should return None due to dedup
    """
    # Import daemon modules lazily
    daemon = _import_daemon_modules()
    build_session_graph = daemon["build_session_graph"]
    ContextCompactor = daemon["ContextCompactor"]
    CompactionContext = daemon["CompactionContext"]
    estimate_messages_tokens = daemon["estimate_messages_tokens"]

    checkpointer, conn = await setup_in_memory_checkpointer()
    session_id = "test-dedup-compaction"
    config = {"configurable": {"thread_id": session_id}}

    try:
        messages = build_conversation_history(num_pairs=10)
        assert len(messages) == 20

        compaction_config = make_compaction_config(
            context_window_overrides={"gpt-4o": 800},
            threshold=0.50,
            recent_message_window=4,
            min_recent_window=2,
            min_messages_before_compaction=5,
        )

        mock_llm = create_mock_llm("Dedup test response.")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            graph = build_session_graph(
                tools=[],
                checkpointer=checkpointer,
                llm_config={
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "test-key",
                    "model": "gpt-4o",
                    "temperature": 0.7,
                    "request_timeout": 60,
                },
                system_prompt="You are a helpful assistant.",
            )

            # Inject messages into graph
            await graph.aupdate_state(
                config,
                {"messages": messages},
                as_node="agent",
            )

        compactor = ContextCompactor(
            config=compaction_config,
            llm_config={
                "base_url": "http://localhost:1234/v1",
                "api_key": "test-key",
                "model": "gpt-4o",
                "temperature": 0.7,
                "request_timeout": 60,
            },
        )

        # First compaction
        mock_summary_response = AIMessage(
            content="Summary: conversation about various topics.",
            id="mock-summary-dedup",
        )
        mock_summary_llm = MagicMock()
        mock_summary_llm.invoke = MagicMock(return_value=mock_summary_response)

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_summary_llm):
            context = CompactionContext(
                messages=messages,
                system_prompt_tokens=30,
                model_name="gpt-4o",
                config=compaction_config,
                llm_config={
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "test-key",
                    "model": "gpt-4o",
                    "temperature": 0.7,
                    "request_timeout": 60,
                },
                last_compacted_at=None,
            )

            result1 = await compactor.compact_state(context)

        assert result1 is not None, "First compaction should succeed"
        assert result1.compacted_at is not None

        # Apply compaction result to graph state
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            await graph.aupdate_state(
                config,
                {"messages": result1.replacement_messages},
                as_node="agent",
            )
            await graph.aupdate_state(
                config,
                {"compacted_at": result1.compacted_at},
                as_node="agent",
            )

        # Verify compacted_at is in state
        state = await graph.aget_state(config)
        assert state.values.get("compacted_at") == result1.compacted_at

        # Attempt re-compaction with the compacted_at timestamp
        # This simulates what _maybe_compact_context does: reads compacted_at from state
        compacted_messages = [
            m for m in state.values.get("messages", [])
            if not isinstance(m, RemoveMessage)
        ]

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_summary_llm):
            context2 = CompactionContext(
                messages=compacted_messages,
                system_prompt_tokens=30,
                model_name="gpt-4o",
                config=compaction_config,
                llm_config={
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "test-key",
                    "model": "gpt-4o",
                    "temperature": 0.7,
                    "request_timeout": 60,
                },
                last_compacted_at=result1.compacted_at,  # <-- recently compacted
            )

            result2 = await compactor.compact_state(context2)

        # Should return None due to dedup (recently compacted within 60 seconds)
        assert result2 is None, (
            "Re-compaction should return None due to dedup - "
            f"last_compacted_at={result1.compacted_at} is within 60 seconds"
        )

    finally:
        await conn.close()


