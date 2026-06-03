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
    create_postgres_checkpointer,
)
from daemon.ensemble_config import EnsembleConfig


class TestGetCheckpointer:
    """Tests for get_checkpointer function (config-aware dispatcher)."""

    @pytest.mark.asyncio
    async def test_get_checkpointer_sqlite_returns_adapter(self, tmp_path):
        """SQLite path: returns a SqliteCheckpointerAdapter.

        Mocks ``aiosqlite.connect`` because the conftest mocks the
        ``langgraph.checkpoint.sqlite.aio`` module — relying on the real
        aiosqlite would couple the test to a real SQLite file.
        """
        from daemon.checkpoint_adapter import SqliteCheckpointerAdapter

        # Point SQLite to a tmp file so the parent dir is created cleanly
        config = EnsembleConfig(
            database="sqlite",
            sqlite={"instances_db": str(tmp_path / "instances.db"),
                    "checkpoints_db": str(tmp_path / "checkpoints.db")},
        )

        # The conftest mocks AsyncSqliteSaver as MagicMock; provide a
        # fake aiosqlite connection with async ``execute`` methods so
        # ``_open_sqlite_adapter`` can run the PRAGMA statements.
        fake_conn = MagicMock()
        fake_conn.execute = AsyncMock()
        with patch("daemon.persistence.aiosqlite.connect", new=AsyncMock(return_value=fake_conn)):
            adapter = await get_checkpointer(config)

        assert isinstance(adapter, SqliteCheckpointerAdapter)
        # The adapter stores the saver (which is the mocked AsyncSqliteSaver).
        assert adapter._saver is not None

    @pytest.mark.asyncio
    async def test_get_checkpointer_postgres_dispatches_to_pg(self):
        """PostgreSQL path: dispatches to create_postgres_checkpointer."""
        config = EnsembleConfig(database="postgres")

        with patch(
            "daemon.persistence.create_postgres_checkpointer",
            new=AsyncMock(return_value=MagicMock(name="pg_adapter")),
        ) as mock_create:
            adapter = await get_checkpointer(config)
            mock_create.assert_awaited_once_with(config)
            assert adapter is not None


class TestCreatePostgresCheckpointer:
    """Tests for create_postgres_checkpointer function."""

    @pytest.mark.asyncio
    async def test_missing_deps_raises_clear_error(self):
        """When asyncpg/psycopg/langgraph-checkpoint-postgres are missing, raise ImportError."""
        config = EnsembleConfig(database="postgres")

        # Force the lazy import to fail by hiding the postgres modules in sys.modules
        # while still making the import statement raise ImportError.
        with patch.dict(
            sys.modules,
            {
                "langgraph.checkpoint.postgres.aio": None,
                "asyncpg": None,
                "psycopg": None,
            },
        ):
            with pytest.raises(ImportError) as exc_info:
                await create_postgres_checkpointer(config)

        # The error must mention the install command
        assert "ensemble[postgres]" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_creates_adapter_with_saver_and_pool(self):
        """When deps are available, builds AsyncPostgresSaver + asyncpg.Pool and wraps in adapter."""
        from daemon.checkpoint_adapter import PostgresCheckpointerAdapter

        config = EnsembleConfig(database="postgres")

        # Build fake modules for the lazy imports
        fake_saver_instance = MagicMock(name="AsyncPostgresSaver")
        fake_saver_instance.setup = AsyncMock()

        class _FakeSaverCls:
            def __init__(self, conn):
                self.conn = conn
                return fake_saver_instance  # but the cls is called, so return the mock

        # Easier: return a callable that returns the mock
        def _saver_factory(conn):
            return fake_saver_instance

        fake_aio_module = MagicMock()
        fake_aio_module.AsyncPostgresSaver = _saver_factory

        fake_pool_instance = MagicMock(name="asyncpg.Pool")

        class _FakeAsyncpg:
            @staticmethod
            async def create_pool(*args, **kwargs):
                return fake_pool_instance

        class _FakeDictRow:
            pass

        # Fake psycopg with AsyncConnection.connect that returns a mock conn
        class _FakeAsyncConnection:
            @staticmethod
            async def connect(*args, **kwargs):
                return MagicMock(name="psycopg_conn")

            async def close(self):
                pass

        class _FakePsycopg:
            AsyncConnection = _FakeAsyncConnection
            rows = MagicMock()
            rows.dict_row = _FakeDictRow

        with patch.dict(
            sys.modules,
            {
                "asyncpg": _FakeAsyncpg,
                "psycopg": _FakePsycopg,
                "psycopg.rows": _FakePsycopg.rows,
                "langgraph.checkpoint.postgres.aio": fake_aio_module,
            },
        ):
            adapter = await create_postgres_checkpointer(config)

        assert isinstance(adapter, PostgresCheckpointerAdapter)
        assert adapter.raw_saver is fake_saver_instance
        # setup() was called on the saver
        fake_saver_instance.setup.assert_awaited_once()
        # The asyncpg pool was created and stored
        assert adapter._pool is fake_pool_instance


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
