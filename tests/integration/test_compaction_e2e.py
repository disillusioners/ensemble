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
        ContextCompactor,
        identify_boundary_groups,
    )
    from daemon.config import CompactionConfig as CompactionConfigModel
    from daemon.graph import SessionState, build_session_graph
    from daemon.loader import estimate_messages_tokens
    from daemon.persistence import get_checkpointer
    return {
        "CompactionContext": CompactionContext,
        "ContextCompactor": ContextCompactor,
        "identify_boundary_groups": identify_boundary_groups,
        "CompactionConfigModel": CompactionConfigModel,
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
        "context_window_override": 0,
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
    - .bind_tools() for tool binding
    """
    mock_response = AIMessage(content=response_content, id="mock-ai-response")
    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke = MagicMock(return_value=mock_response)
    mock_llm_instance.bind_tools = MagicMock(return_value=mock_llm_instance)
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
    1. Build a real graph with SessionState and in-memory checkpointer
    2. Mock ThinkingChatOpenAI for predictable LLM responses
    3. Build 20+ message history and inject into graph via aupdate_state
    4. Trigger compaction via ContextCompactor.compact_state()
    5. Apply result to graph via aupdate_state
    6. Send new message via graph.ainvoke()
    7. Verify agent responds correctly after compaction
    """
    # Import daemon modules lazily
    daemon = _import_daemon_modules()
    SessionState = daemon["SessionState"]
    build_session_graph = daemon["build_session_graph"]
    ContextCompactor = daemon["ContextCompactor"]
    CompactionContext = daemon["CompactionContext"]
    estimate_messages_tokens = daemon["estimate_messages_tokens"]

    checkpointer, conn = await setup_in_memory_checkpointer()
    session_id = "test-compaction-continuation"

    try:
        # Build 24 messages (12 pairs)
        messages = build_conversation_history(num_pairs=12)
        assert len(messages) == 24

        config = {"configurable": {"thread_id": session_id}}

        mock_llm = create_mock_llm("After compaction, I can still help you.")

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            # Build graph with real checkpointer
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
            context_window_override=1000,  # Small window to trigger compaction easily
            threshold=0.50,
            recent_message_window=4,
            min_recent_window=2,
            min_messages_before_compaction=5,
        )

        # Estimate tokens to verify compaction will be triggered
        history_tokens = estimate_messages_tokens(messages)
        system_prompt_tokens = 50
        total_tokens = history_tokens + system_prompt_tokens
        context_window = compaction_config.context_window_override  # 1000

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

        # Mock the summarization LLM call within compact_state
        mock_summary_response = AIMessage(
            content="Summary of previous conversation: user asked about various topics, "
            "assistant provided helpful answers on topics 0-7.",
            id="mock-summary",
        )
        mock_summary_llm = MagicMock()
        mock_summary_llm.invoke = MagicMock(return_value=mock_summary_response)

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
        assert isinstance(result, type(result))  # CompactionResult
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

        # Apply compaction result to graph state
        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            await graph.aupdate_state(
                config,
                {"messages": result.replacement_messages},
                as_node="agent",
            )

            # Set compacted_at timestamp in state
            if result.compacted_at:
                await graph.aupdate_state(
                    config,
                    {"compacted_at": result.compacted_at},
                    as_node="agent",
                )

            # Verify compacted state
            state = await graph.aget_state(config)
            compacted_messages = state.values.get("messages", [])
            # Filter out RemoveMessage instances
            actual_messages = [
                m for m in compacted_messages
                if not isinstance(m, RemoveMessage)
            ]
            assert len(actual_messages) < 24, "State should have fewer messages after compaction"
            assert state.values.get("compacted_at") == result.compacted_at

            # Send a new message through the graph - this invokes the LLM
            invoke_result = await graph.ainvoke(
                {"messages": [HumanMessage(content="After compaction test", id="post-compact-1")]},
                config,
            )

            # Verify the graph responded
            result_messages = invoke_result.get("messages", [])
            assert len(result_messages) > 0, "Graph should return messages"

            # The last message should be from the AI (mock response)
            last_msg = result_messages[-1]
            assert hasattr(last_msg, "type")
            assert last_msg.type == "ai", f"Last message should be AI, got {last_msg.type}"
            assert last_msg.content == "After compaction, I can still help you."

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

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        session_id = "test-crash-recovery"
        config = {"configurable": {"thread_id": session_id}}

        # ---- Phase 1: Build state and compact ----
        checkpointer1 = await get_checkpointer(db_path)
        await checkpointer1.setup()

        # Build message history
        messages = build_conversation_history(num_pairs=10)
        assert len(messages) == 20

        compaction_config = make_compaction_config(
            context_window_override=800,
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
        checkpointer2 = await get_checkpointer(db_path)
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
            context_window_override=800,
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


# =============================================================================
# Test 4: Tool Call Integrity After Compaction
# =============================================================================


@pytest.mark.asyncio
async def test_tool_call_integrity_after_compaction():
    """Test that tool calls maintain integrity after compaction.

    Builds a conversation with interleaved tool calls, compacts it,
    and verifies every remaining AIMessage.tool_calls has matching
    ToolMessages - no orphans.

    Steps:
    1. Build history with interleaved tool calls (5 turns, 20 messages)
    2. Compact with a small context window
    3. Verify every remaining AIMessage with tool_calls has matching ToolMessages
    """
    # Import daemon modules lazily
    daemon = _import_daemon_modules()
    ContextCompactor = daemon["ContextCompactor"]
    CompactionContext = daemon["CompactionContext"]
    identify_boundary_groups = daemon["identify_boundary_groups"]
    estimate_messages_tokens = daemon["estimate_messages_tokens"]

    checkpointer, conn = await setup_in_memory_checkpointer()
    session_id = "test-tool-integrity"

    try:
        # Build conversation with tool calls (5 turns * 4 messages = 20 messages)
        messages = build_tool_conversation()
        assert len(messages) == 20

        # Verify initial integrity: all tool calls have matching responses
        _verify_tool_call_integrity(messages, "Pre-compaction messages")

        compaction_config = make_compaction_config(
            context_window_override=800,
            threshold=0.50,
            recent_message_window=3,  # Keep last 3 groups (tool_sequence groups)
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

        # Mock summarization LLM
        mock_summary_response = AIMessage(
            content="Summary: user made several requests involving bash tool calls. "
            "The assistant checked files and confirmed everything was in order.",
            id="mock-summary-tool",
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

        assert result is not None, "Compaction should trigger for tool conversation"

        # Extract non-RemoveMessage entries from replacement
        kept_messages = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        ]

        # The kept messages should maintain tool call integrity
        _verify_tool_call_integrity(kept_messages, "Post-compaction messages")

        # Additional verification: check that the boundary grouping was correct
        groups = identify_boundary_groups(messages)
        tool_sequence_groups = [g for g in groups if g.group_type == "tool_sequence"]
        assert len(tool_sequence_groups) == 5, (
            "Should have 5 tool_sequence groups (one per turn)"
        )

        # Each tool_sequence should have: AI + ToolMessage
        for i, group in enumerate(tool_sequence_groups):
            ai_msgs = [m for m in group.messages if isinstance(m, AIMessage)]
            tool_msgs = [m for m in group.messages if isinstance(m, ToolMessage)]
            assert len(ai_msgs) == 1, f"Group {i} should have 1 AI message"
            assert len(tool_msgs) == 1, f"Group {i} should have 1 ToolMessage"

    finally:
        await conn.close()


def _verify_tool_call_integrity(messages, label: str) -> None:
    """Verify that every AIMessage with tool_calls has matching ToolMessages.

    Args:
        messages: List of messages to check.
        label: Label for error messages (e.g., "Pre-compaction").

    Raises:
        AssertionError if any orphan tool calls are found.
    """
    # Build map of tool_call_id -> ToolMessage
    tool_responses: dict = {}
    for msg in messages:
        if isinstance(msg, ToolMessage):
            tool_responses[msg.tool_call_id] = msg

    # Check every AIMessage with tool_calls
    for i, msg in enumerate(messages):
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                assert tc_id in tool_responses, (
                    f"{label}: AIMessage at index {i} has tool_call '{tc_id}' "
                    f"with no matching ToolMessage. "
                    f"Available tool_call_ids: {list(tool_responses.keys())}"
                )
