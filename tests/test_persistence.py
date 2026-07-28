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

    @pytest.mark.asyncio
    async def test_get_instance_messages_injects_synthetic_system_when_manager_provided(self):
        """System prompt is not persisted in the checkpoint but is needed by the
        frontend "View system message" toggle. When ``manager`` is passed,
        ``get_instance_messages`` should reconstruct the FULL prompt (cached
        base + post-cache appends) and prepend a synthetic ``role="system"``
        entry at index 0 of the returned list. The synthetic content must
        match the post-cache-augmented prompt the LLM actually saw, not
        just the cached base prompt.
        """
        from langchain_core.messages import HumanMessage

        # Mock checkpointer: only a human message is persisted (no system).
        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [HumanMessage(content="Hello world")]
            }
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        # Mock instance row: agent exists, agent_dir points to a real agent.
        agent_dir = Path(__file__).resolve().parent.parent / "agents" / "developer"
        if not agent_dir.exists():
            pytest.skip("developer agent directory not present in this checkout")

        instance_meta = MagicMock()
        instance_meta.agent_id = "developer"
        instance_meta.agent_dir = str(agent_dir)
        instance_meta.agent_tag = None
        instance_meta.instance_metadata = {}
        instance_meta.parent_id = None
        instance_meta.project_id = None
        instance_meta.created_at = "2026-07-28T00:00:00+00:00"

        instance_repo = MagicMock()
        instance_repo.get = MagicMock(return_value=instance_meta)

        prompt_cache = MagicMock()
        # First call (cache.get) is a miss → force ``load_and_cache_prompt`` to
        # actually run. The second call (cache.set) is fine to no-op.
        prompt_cache.get = MagicMock(return_value=None)

        # Patch load_and_cache_prompt at the import site used by
        # get_instance_messages (``daemon.manager``) so the lazy import inside
        # the helper returns our stub. The "base" prompt is what the prompt
        # cache would have returned.
        base_prompt = (
            "You are a developer agent.\n\n"
            "## Rule\nFollow the rules.\n\n## Workflow\nDo work."
        )
        # The "full" prompt is what the LLM actually saw — base + all
        # post-cache appends (context key, shared context metadata, current
        # time, allowed models, user language, auto-loaded skills, etc.).
        full_prompt = (
            base_prompt
            + "\n\n---\n\n# Context Key\nctx_key_for_inst-123"
            + "\n\n---\n\n# Shared Context Metadata\n<shared_context></shared_context>"
            + "\n\n---\n\n# Current Time\n2026-07-28T00:00:00Z"
            + "\n\n---\n\n# User Language\nen"
            + "\n\n---\n\n# Auto-Loaded Skills\n<auto_load></auto_load>"
        )

        with patch(
            "daemon.manager.load_and_cache_prompt",
            return_value=(base_prompt, len(base_prompt)),
        ) as mock_load, patch(
            # Patch at the source module — the helper imports via
            # ``from daemon.services.instance_lifecycle import _apply_post_cache_appends``,
            # so the attribute is read at call time from the source module.
            "daemon.services.instance_lifecycle._apply_post_cache_appends",
            return_value=(full_prompt, "en"),
        ) as mock_apply:
            manager = MagicMock()
            manager._instance_repository = instance_repo
            manager.prompt_cache = prompt_cache
            # The post-cache appender touches these on the manager — provide
            # MagicMocks so ``getattr`` resolves them.
            manager._project_repository = MagicMock()
            manager.shared_context_metadata_repo = MagicMock()

            messages = await get_instance_messages(
                mock_checkpointer, "inst-123", manager=manager
            )

        # load_and_cache_prompt must have been called to rebuild the base prompt.
        mock_load.assert_called_once()
        # All args are passed as kwargs.
        call_kwargs = mock_load.call_args.kwargs
        assert call_kwargs["agent_id"] == "developer"
        assert Path(call_kwargs["agent_dir"]) == agent_dir

        # The post-cache append chain must have been called exactly once,
        # mirroring the spawn/restore call sites in instance_lifecycle.py.
        mock_apply.assert_called_once()
        apply_kwargs = mock_apply.call_args.kwargs
        assert apply_kwargs["system_prompt"] == base_prompt
        assert apply_kwargs["instance_id"] == "inst-123"
        assert apply_kwargs["agent_id"] == "developer"
        assert apply_kwargs["manager"] is manager
        assert apply_kwargs["instance_repository"] is instance_repo
        assert apply_kwargs["disable_auto_load_tracking"] is True

        # Returned list now has the synthetic system message at index 0,
        # followed by the original human message.
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["type"] == "system"
        assert messages[0]["is_synthetic"] is True
        # The injected content must be the FULL prompt (post-cache), not the
        # base. This is the core improvement the test guards.
        assert messages[0]["content"] == full_prompt
        assert messages[0]["instance_id"] == "inst-123"
        assert messages[0]["message_id"] == "synthetic-system-inst-123"
        assert messages[0]["created_at"] == "2026-07-28T00:00:00+00:00"

        # The originally-persisted human message is preserved at index 1.
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_get_instance_messages_does_not_track_auto_load_skills(self):
        """Reconstruction includes auto-load skills without metadata writes."""
        from types import SimpleNamespace
        from langchain_core.messages import HumanMessage

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [HumanMessage(content="Hello world")]}
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        agent_dir = Path(__file__).resolve().parent.parent / "agents" / "developer"
        instance_meta = SimpleNamespace(
            agent_id="developer",
            agent_dir=str(agent_dir),
            agent_tag=None,
            instance_metadata={},
            parent_id=None,
            project_id="project-123",
            created_at="2026-07-28T00:00:00+00:00",
        )
        instance_repo = MagicMock()
        instance_repo.get.return_value = instance_meta

        manager = SimpleNamespace(
            _instance_repository=instance_repo,
            prompt_cache=MagicMock(),
            _project_repository=None,
            shared_context_metadata_repo=MagicMock(),
            _skill_repo=MagicMock(),
            _skill_clone_service=None,
            config=SimpleNamespace(llm=SimpleNamespace(allowed_models=[])),
        )
        manager._skill_repo.get_auto_load_skills.return_value = [
            SimpleNamespace(id="skill-1", content="Always verify the result.")
        ]

        base_prompt = "You are a developer agent."
        with patch(
            "daemon.manager.load_and_cache_prompt",
            return_value=(base_prompt, len(base_prompt)),
        ), patch("daemon.registry.get_registry", side_effect=RuntimeError("no registry")):
            messages = await get_instance_messages(
                mock_checkpointer, "inst-123", manager=manager
            )

        assert "Always verify the result." in messages[0]["content"]
        instance_repo.set_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_instance_messages_no_synthetic_without_manager(self):
        """Backward compatibility: when ``manager`` is None (or omitted),
        no synthetic system message is injected — only persisted messages
        are returned.
        """
        from langchain_core.messages import HumanMessage

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [HumanMessage(content="Hello world")]
            }
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        messages = await get_instance_messages(mock_checkpointer, "inst-123")

        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_get_instance_messages_injection_swallows_errors(self):
        """When the manager is provided but reconstruction fails (e.g. the
        ``load_and_cache_prompt`` patch raises), the function must still
        return the original list of persisted messages — never raise.
        """
        from langchain_core.messages import HumanMessage

        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [HumanMessage(content="Hello world")]
            }
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        instance_meta = MagicMock()
        instance_meta.agent_id = "developer"
        instance_meta.agent_dir = "/nonexistent/path/that/cannot/be/loaded"
        instance_meta.agent_tag = None
        instance_meta.instance_metadata = {}

        instance_repo = MagicMock()
        instance_repo.get = MagicMock(return_value=instance_meta)

        prompt_cache = MagicMock()
        prompt_cache.get = MagicMock(return_value=None)

        manager = MagicMock()
        manager._instance_repository = instance_repo
        manager.prompt_cache = prompt_cache

        # Patch load_and_cache_prompt to raise — simulating a corrupt agent
        # directory or missing registry. The function must swallow the error.
        with patch(
            "daemon.manager.load_and_cache_prompt",
            side_effect=RuntimeError("boom: prompt build failed"),
        ):
            messages = await get_instance_messages(
                mock_checkpointer, "inst-123", manager=manager
            )

        # Original list is preserved unchanged.
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello world"


class TestGetInstanceMessagesHumanMessagesContext:
    """Tests for the Phase 4 human_messages context rebuild path.

    When the agent's ``context_injection_mode`` resolves to
    ``"human_messages"``, ``get_instance_messages`` must:

    1. Call ``assemble_context_messages`` on-demand to rebuild the
       per-turn context messages (which are NOT in the checkpoint).
    2. Serialize each context message and stamp it with
       ``is_synthetic=True`` and the ``context_kind`` enum value.
    3. Insert them between the synthetic system message (when present)
       and the most recent user message.
    4. NEVER write to the database (read endpoint, ADR-2).

    When the mode is ``"system_prompt"`` (legacy), the context rebuild
    must be a strict no-op so the existing byte layout is preserved.
    """

    @pytest.mark.asyncio
    async def test_human_messages_mode_injects_synthetic_context_before_last_user(self):
        """human_messages mode rebuilds context messages and inserts them
        between the synthetic system message and the most recent user
        message. Each entry is stamped ``is_synthetic=True`` and carries
        a ``context_kind``.
        """
        from langchain_core.messages import AIMessage, HumanMessage

        # Two-turn conversation in the checkpoint — context is rebuilt
        # for the LAST user turn only (per Phase 4 plan: "before the
        # most recent user message only").
        persisted = [
            HumanMessage(content="first user turn"),
            AIMessage(content="first assistant reply"),
            HumanMessage(content="second user turn"),
        ]
        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": persisted}
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        # Manager stubs — instance_meta + agent_meta + mode all go
        # through ``_resolve_instance_message_context``, which the test
        # patches to return human_messages mode without touching the
        # registry.
        instance_meta = MagicMock()
        instance_meta.agent_id = "developer"
        instance_meta.agent_tag = None
        instance_meta.instance_metadata = {}
        instance_meta.parent_id = None
        instance_meta.project_id = "project-1"
        instance_meta.created_at = "2026-07-28T00:00:00+00:00"

        instance_repo = MagicMock()
        instance_repo.get = MagicMock(return_value=instance_meta)

        agent_meta = MagicMock()
        agent_meta.context_injection_mode = "human_messages"

        ctx = {
            "instance_meta": instance_meta,
            "agent_meta": agent_meta,
            "mode": "human_messages",
        }

        # Stub the two helpers ``get_instance_messages`` calls: the
        # metadata resolver and the context-message builder. The system-
        # prompt reconstructor is also stubbed so we don't have to
        # patch through ``load_and_cache_prompt``.
        with patch(
            "daemon.persistence._resolve_instance_message_context",
            return_value=ctx,
        ) as mock_resolve, patch(
            "daemon.persistence._build_context_dicts_for_response",
            new=AsyncMock(return_value=[
                {
                    "message_id": "synthetic-context-project-inst-123-0",
                    "type": "human",
                    "role": "user",
                    "content": "[SYSTEM CONTEXT: Related Project]\n\nproject body",
                    "thinking": None,
                    "thinking_extracted": None,
                    "tool_calls": None,
                    "images": None,
                    "created_at": "2026-07-28T00:00:00+00:00",
                    "instance_id": "inst-123",
                    "is_synthetic": True,
                    "context_kind": "project",
                },
                {
                    "message_id": "synthetic-context-skills-inst-123-1",
                    "type": "human",
                    "role": "user",
                    "content": "[SYSTEM CONTEXT: Skills]\n\nskill body",
                    "thinking": None,
                    "thinking_extracted": None,
                    "tool_calls": None,
                    "images": None,
                    "created_at": "2026-07-28T00:00:00+00:00",
                    "instance_id": "inst-123",
                    "is_synthetic": True,
                    "context_kind": "skills",
                },
            ]),
        ) as mock_build, patch(
            # ``_reconstruct_full_system_prompt`` returns ``None`` so the
            # test focuses purely on the context-rebuild path without
            # also synthesizing a system message.
            "daemon.persistence._reconstruct_full_system_prompt",
            return_value=None,
        ):
            manager = MagicMock()
            manager._instance_repository = instance_repo

            messages = await get_instance_messages(
                mock_checkpointer, "inst-123", manager=manager
            )

        # Metadata resolver + context builder were both invoked.
        mock_resolve.assert_called_once_with("inst-123", manager)
        mock_build.assert_awaited_once()

        # Result layout:
        #   [0] first persisted user  (no context before — historical turn)
        #   [1] first persisted assistant
        #   [2] synthetic context (project)
        #   [3] synthetic context (skills)
        #   [4] second persisted user (current turn — context prepended)
        assert len(messages) == 5
        assert messages[0]["content"] == "first user turn"
        assert messages[1]["content"] == "first assistant reply"
        assert messages[2]["is_synthetic"] is True
        assert messages[2]["context_kind"] == "project"
        assert messages[2]["message_id"] == "synthetic-context-project-inst-123-0"
        assert messages[3]["is_synthetic"] is True
        assert messages[3]["context_kind"] == "skills"
        assert messages[4]["role"] == "user"
        assert messages[4]["content"] == "second user turn"
        # Real persisted messages must NOT be marked synthetic.
        assert "is_synthetic" not in messages[0]
        assert "is_synthetic" not in messages[1]
        assert "is_synthetic" not in messages[4]

    @pytest.mark.asyncio
    async def test_system_prompt_mode_does_not_call_assemble_context_messages(self):
        """system_prompt mode is the legacy default. Context is baked
        into the system prompt by ``_apply_post_cache_appends``; the
        GET /messages response must NOT rebuild it via
        ``assemble_context_messages`` — that would double-token-cost
        and risk confusing the LLM by sending the same data twice.
        """
        from langchain_core.messages import HumanMessage

        persisted = [HumanMessage(content="Hello world")]
        mock_checkpointer = AsyncMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": persisted}
        })
        mock_checkpointer.alist = MagicMock(return_value=EmptyAsyncIterator())

        instance_meta = MagicMock()
        instance_meta.agent_id = "developer"
        instance_meta.agent_tag = None
        instance_meta.instance_metadata = {}
        instance_meta.parent_id = None
        instance_meta.project_id = None
        instance_meta.created_at = "2026-07-28T00:00:00+00:00"
        instance_repo = MagicMock()
        instance_repo.get = MagicMock(return_value=instance_meta)

        ctx = {
            "instance_meta": instance_meta,
            "agent_meta": None,
            "mode": "system_prompt",  # legacy default
        }

        # Track whether the context builder is invoked — must NOT be.
        with patch(
            "daemon.persistence._resolve_instance_message_context",
            return_value=ctx,
        ), patch(
            "daemon.persistence._build_context_dicts_for_response",
            new=AsyncMock(return_value=["should-not-appear"]),
        ) as mock_build, patch(
            "daemon.persistence._reconstruct_full_system_prompt",
            return_value=None,
        ):
            manager = MagicMock()
            manager._instance_repository = instance_repo

            messages = await get_instance_messages(
                mock_checkpointer, "inst-123", manager=manager
            )

        # The context builder was NEVER awaited in legacy mode.
        mock_build.assert_not_awaited()

        # Only the persisted message is returned — no synthetic entries.
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello world"
        # No ``context_kind`` leaks into legacy responses.
        assert all("context_kind" not in m for m in messages)

    @pytest.mark.asyncio
    async def test_serialize_message_emits_context_kind_only_for_context(self):
        """``serialize_message`` must add the ``context_kind`` field ONLY
        when ``additional_kwargs["context_kind"]`` is present. Messages
        without it (regular user/assistant turns) keep the legacy dict
        shape unchanged.
        """
        from daemon.utils import serialize_message
        from langchain_core.messages import HumanMessage

        # Tagged context message — must surface ``context_kind``.
        ctx_msg = HumanMessage(
            content="[SYSTEM CONTEXT: Skills]\n\nbody",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "skills",
            },
        )
        serialized = serialize_message(ctx_msg)
        assert serialized["context_kind"] == "skills"
        assert serialized["role"] == "user"

        # Regular message — must NOT add the field.
        plain_msg = HumanMessage(content="just a normal turn")
        serialized_plain = serialize_message(plain_msg)
        assert "context_kind" not in serialized_plain
        assert serialized_plain["role"] == "user"
        assert serialized_plain["content"] == "just a normal turn"

        # Edge case: ``additional_kwargs`` present but no ``context_kind``
        # — must NOT add the field.
        noisy_msg = HumanMessage(
            content="with metadata",
            additional_kwargs={"some_other_flag": True},
        )
        serialized_noisy = serialize_message(noisy_msg)
        assert "context_kind" not in serialized_noisy
