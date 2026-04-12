"""Tests for daemon/persistence.py"""

import pytest
import asyncio
import sys
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path


class EmptyAsyncIterator:
    """Async iterator that yields nothing, for mocking alist."""
    
    def __init__(self, items=None):
        self.items = items or []
        self.index = 0
    
    def __aiter__(self):
        return self
    
    async def __anext__(self):
        if self.index < len(self.items):
            item = self.items[self.index]
            self.index += 1
            return item
        raise StopAsyncIteration


# langgraph mocking is handled by conftest.py

from daemon.persistence import (
    get_checkpointer,
    get_instance_messages,
)


class TestGetCheckpointer:
    """Tests for get_checkpointer function."""

    def test_get_checkpointer_returns_checkpointer(self, tmp_path):
        """Test that get_checkpointer returns a checkpointer."""
        db_path = tmp_path / "test.db"

        checkpointer = get_checkpointer(db_path)

        # The mock is set up at module import time
        assert checkpointer is not None


class TestGetInstanceMessages:
    """Tests for get_instance_messages function."""

    @pytest.mark.asyncio
    async def test_get_instance_messages_empty_state(self):
        """Test that get_instance_messages returns empty list for None state."""
        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value=None)

        messages = await get_instance_messages(mock_checkpointer, "test-instance")

        assert messages == []

    @pytest.mark.asyncio
    async def test_get_instance_messages_no_messages(self):
        """Test that get_instance_messages returns empty list when no messages."""
        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {}
        })

        messages = await get_instance_messages(mock_checkpointer, "test-instance")

        assert messages == []

    @pytest.mark.asyncio
    async def test_get_instance_messages_with_human_message(self):
        """Test parsing human message."""
        from langchain_core.messages import HumanMessage

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [HumanMessage(content="Hello world")]
            }
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        messages = await get_instance_messages(mock_checkpointer, "test-instance")

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello world"
        assert messages[0]["type"] == "human"

    @pytest.mark.asyncio
    async def test_get_instance_messages_with_ai_message(self):
        """Test parsing AI message."""
        from langchain_core.messages import AIMessage

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [AIMessage(content="Hello, how can I help?")]
            }
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        messages = await get_instance_messages(mock_checkpointer, "test-instance")

        assert len(messages) == 1
        assert messages[0]["role"] == "assistant"
        assert messages[0]["content"] == "Hello, how can I help?"
        assert messages[0]["type"] == "ai"

    @pytest.mark.asyncio
    async def test_get_instance_messages_with_system_message(self):
        """Test parsing system message."""
        from langchain_core.messages import SystemMessage

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [SystemMessage(content="You are helpful.")]
            }
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        messages = await get_instance_messages(mock_checkpointer, "test-instance")

        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful."
        assert messages[0]["type"] == "system"

    @pytest.mark.asyncio
    async def test_get_instance_messages_extracts_thinking(self):
        """Test extracting thinking from AI message."""
        from langchain_core.messages import AIMessage

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    AIMessage(
                        content="42",
                        additional_kwargs={"thinking": "Let me calculate..."}
                    )
                ]
            }
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        messages = await get_instance_messages(mock_checkpointer, "test-instance")

        assert len(messages) == 1
        assert messages[0]["thinking"] == "Let me calculate..."

    @pytest.mark.asyncio
    async def test_get_instance_messages_extracts_think_tags(self):
        """Test extracting think tags from content."""
        from langchain_core.messages import AIMessage

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    AIMessage(content="<think>\nReasoning here\n</think>\nThe answer is 42.")
                ]
            }
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        messages = await get_instance_messages(mock_checkpointer, "test-instance")

        assert len(messages) == 1
        assert messages[0]["content"] == "The answer is 42."
        assert messages[0]["thinking_extracted"] == "Reasoning here"

    @pytest.mark.asyncio
    async def test_get_instance_messages_with_tool_calls(self):
        """Test parsing tool calls from AI message."""
        from langchain_core.messages import AIMessage, ToolMessage

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    AIMessage(
                        content="Let me search for that.",
                        tool_calls=[
                            {"id": "call_1", "name": "search", "args": {"query": "test"}}
                        ]
                    ),
                    ToolMessage(content="Search result", tool_call_id="call_1")
                ]
            }
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        messages = await get_instance_messages(mock_checkpointer, "test-instance")

        # Should have the AI message
        ai_msg = messages[0]
        assert ai_msg["role"] == "assistant"
        assert ai_msg["content"] == "Let me search for that."
        assert ai_msg["tool_calls"] is not None
        assert len(ai_msg["tool_calls"]) == 1
        assert ai_msg["tool_calls"][0]["name"] == "search"
        assert ai_msg["tool_calls"][0]["output"] == "Search result"

    @pytest.mark.asyncio
    async def test_get_instance_messages_skips_tool_messages(self):
        """Test that tool messages are not included in main list."""
        from langchain_core.messages import ToolMessage

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    ToolMessage(content="Tool output", tool_call_id="call_1")
                ]
            }
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        messages = await get_instance_messages(mock_checkpointer, "test-instance")

        # Tool messages should be skipped (only included in tool_calls of AI messages)
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_get_instance_messages_multiple_messages(self):
        """Test parsing multiple messages in order."""
        from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    SystemMessage(content="You are a helpful assistant."),
                    HumanMessage(content="Hello!"),
                    AIMessage(content="Hi there!"),
                ]
            }
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        messages = await get_instance_messages(mock_checkpointer, "test-instance")

        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_get_instance_messages_generates_message_ids(self):
        """Test that message IDs are generated."""
        from langchain_core.messages import HumanMessage

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [HumanMessage(content="Test")]
            }
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        messages = await get_instance_messages(mock_checkpointer, "test-instance")

        assert len(messages) == 1
        assert "message_id" in messages[0]
        assert messages[0]["message_id"] is not None
