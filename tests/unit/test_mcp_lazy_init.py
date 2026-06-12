"""Tests for MCP lazy initialization feature.

Covers the real ``create_lazy_mcp_tools`` and ``_build_lazy_coroutine``
code paths in ``daemon.mcp.tool_adapter``. The root ``tests/conftest.py``
mocks ``daemon.mcp.tool_adapter`` to a stub module, so we unmock it at
import time (same pattern as ``test_mcp_tool_timeout.py``).

Test surface:
- ``TestCreateLazyMcpTools`` — schema → tool factory.
- ``TestLazyCoroutine`` — session resolution, kwargs handling, error
  mapping.
- ``TestConcurrencyGuard`` (W7) — N concurrent first calls produce 1
  session via double-check locking.
- ``TestSharedSessionCache`` (C1) — same/different-server cache
  isolation.
- ``TestTimeoutWrapping`` — ``tool_call_timeout=0`` disables wrapping.
- ``TestMcpSessionProviderProtocol`` — ``@runtime_checkable`` Protocol
  duck-typing.
"""
import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.tools import ToolException

# ---------------------------------------------------------------------------
# Unmock daemon.mcp.tool_adapter so we import the REAL module. The conftest
# mock lacks ``create_lazy_mcp_tools`` / ``_build_lazy_coroutine`` /
# ``McpSessionProvider`` (it stubs them as MagicMock).
# ---------------------------------------------------------------------------
_mock_tool_adapter = sys.modules.pop("daemon.mcp.tool_adapter", None)
from daemon.mcp.tool_adapter import (  # noqa: E402
    create_lazy_mcp_tools,
    _build_lazy_coroutine,
    McpSessionProvider,
)
if _mock_tool_adapter is not None:
    sys.modules["daemon.mcp.tool_adapter"] = _mock_tool_adapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema_dict(name: str = "echo", description: str = "Echo", input_schema: dict | None = None) -> dict:
    """Build a schema dict in the shape ``create_lazy_mcp_tools`` expects."""
    return {
        "name": name,
        "description": description,
        "input_schema": input_schema or {"type": "object", "properties": {}},
    }


def _make_session(content: list | None = None, is_error: bool = False) -> MagicMock:
    """Build a mock MCP session whose ``call_tool`` returns a result."""
    session = MagicMock()
    result = SimpleNamespace(
        content=content if content is not None else [SimpleNamespace(text="ok")],
        isError=is_error,
    )
    session.call_tool = AsyncMock(return_value=result)
    return session


def _make_provider(session: MagicMock | None = None) -> MagicMock:
    """Build a mock ``McpSessionProvider`` (duck-typed)."""
    provider = MagicMock()
    provider.get_session = AsyncMock(return_value=session or _make_session())
    return provider


def _build_coroutine(
    server_name: str = "test",
    original_tool_name: str = "echo",
    session: MagicMock | None = None,
    provider: MagicMock | None = None,
    cache: dict | None = None,
    lock: asyncio.Lock | None = None,
    timeout_seconds: float | None = 5.0,
):
    """Build a lazy coroutine with the given session/provider/cache/lock."""
    if session is None:
        session = _make_session()
    if provider is None:
        provider = _make_provider(session)
    if cache is None:
        cache = {}
    if lock is None:
        lock = asyncio.Lock()
    return _build_lazy_coroutine(
        server_name=server_name,
        original_tool_name=original_tool_name,
        session_provider=provider,
        shared_session_cache=cache,
        shared_session_lock=lock,
        timeout_seconds=timeout_seconds,
    ), provider, session, cache, lock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateLazyMcpTools:
    """Test the real ``create_lazy_mcp_tools`` factory."""

    def test_creates_structured_tools_with_correct_names(self):
        """Schemas become tools prefixed with ``mcp_{server}_{tool}``."""
        schemas = [
            _schema_dict(name="echo", description="Echo tool"),
            _schema_dict(name="ping", description="Ping tool"),
        ]
        tools = create_lazy_mcp_tools(
            server_name="my_server",
            schemas=schemas,
            session_provider=_make_provider(),
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
        )
        assert [t.name for t in tools] == ["mcp_my_server_echo", "mcp_my_server_ping"]

    def test_tools_have_mcp_description_suffix(self):
        """Each tool's description ends with ``[MCP:server_name]``."""
        schemas = [_schema_dict(name="echo", description="Echoes input")]
        tools = create_lazy_mcp_tools(
            server_name="alpha",
            schemas=schemas,
            session_provider=_make_provider(),
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
        )
        assert tools[0].description.endswith("[MCP:alpha]")
        assert "Echoes input" in tools[0].description

    def test_empty_schemas_returns_empty_list(self):
        """Empty input → empty list (no tool wrappers, no provider calls)."""
        provider = _make_provider()
        tools = create_lazy_mcp_tools(
            server_name="x",
            schemas=[],
            session_provider=provider,
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
        )
        assert tools == []
        # The provider must NOT be touched for an empty schema list.
        provider.get_session.assert_not_called()

    async def test_tool_coroutine_is_callable(self):
        """Each tool has a working coroutine that reaches ``call_tool``."""
        schemas = [
            _schema_dict(name="echo"),
            _schema_dict(name="ping"),
        ]
        session = _make_session()
        provider = _make_provider(session)
        tools = create_lazy_mcp_tools(
            server_name="svc",
            schemas=schemas,
            session_provider=provider,
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
        )
        for tool in tools:
            # coroutine is the bound async function — must be callable.
            assert callable(tool.coroutine)
            await tool.coroutine()  # exercises the full path

        assert session.call_tool.call_count == 2
        assert provider.get_session.call_count == 1


class TestLazyCoroutine:
    """Test the real ``_build_lazy_coroutine`` return value."""

    async def test_coroutine_resolves_session_on_first_call(self):
        """``session_provider.get_session`` is invoked when the coroutine runs."""
        coro, provider, _session, cache, _lock = _build_coroutine()
        assert "test" not in cache  # not yet populated

        await coro(arg="value")

        provider.get_session.assert_awaited_once_with("test")
        assert "test" in cache  # session was cached

    async def test_coroutine_calls_session_call_tool(self):
        """``session.call_tool(tool_name, kwargs)`` is invoked with user kwargs."""
        coro, _provider, session, _cache, _lock = _build_coroutine(
            original_tool_name="my_tool"
        )

        await coro(foo="bar", count=3)

        session.call_tool.assert_awaited_once()
        args, kwargs = session.call_tool.call_args
        # Either positional or keyword — accept both shapes.
        if args:
            assert args[0] == "my_tool"
            assert args[1] == {"foo": "bar", "count": 3}
        else:
            assert kwargs["name"] == "my_tool" or kwargs.get("original_tool_name") == "my_tool"

    async def test_coroutine_strips_runtime_injected_arg(self):
        """LangGraph's ``runtime`` InjectedToolArg is removed before ``call_tool``."""
        coro, _provider, session, _cache, _lock = _build_coroutine()
        runtime_obj = MagicMock(name="runtime")

        await coro(arg="v", runtime=runtime_obj)

        # Extract the kwargs passed to call_tool — must not contain "runtime".
        session.call_tool.assert_awaited_once()
        call = session.call_tool.call_args
        if call.args:
            passed_kwargs = call.args[1]
        else:
            passed_kwargs = call.kwargs
        assert "runtime" not in passed_kwargs
        assert passed_kwargs.get("arg") == "v"

    async def test_coroutine_returns_converted_result(self):
        """``_convert_call_tool_result`` translates the result shape."""
        content = [SimpleNamespace(text="hello world")]
        session = _make_session(content=content, is_error=False)
        coro, _provider, _session, _cache, _lock = _build_coroutine(session=session)

        result = await coro()

        # Fallback converter returns (list_of_text_dicts, None) for text content.
        assert result == ([{"type": "text", "text": "hello world"}], None)

    async def test_coroutine_handles_timeout(self):
        """``asyncio.TimeoutError`` from the session becomes ``ToolException``."""
        session = MagicMock()
        session.call_tool = AsyncMock(side_effect=asyncio.TimeoutError)
        coro, _provider, _session, _cache, _lock = _build_coroutine(
            session=session, timeout_seconds=5.0
        )

        with pytest.raises(ToolException, match="timed out"):
            await coro()

    async def test_coroutine_handles_connection_error(self):
        """A generic ``Exception`` becomes a ``ToolException`` with context."""
        session = MagicMock()
        session.call_tool = AsyncMock(side_effect=ConnectionError("server down"))
        coro, _provider, _session, _cache, _lock = _build_coroutine(
            session=session
        )

        with pytest.raises(ToolException, match="MCP tool call failed"):
            await coro()

    async def test_coroutine_reuses_cached_session(self):
        """Second call hits the cache; ``get_session`` is called exactly once."""
        coro, provider, session, _cache, _lock = _build_coroutine()

        await coro(arg="first")
        await coro(arg="second")

        assert provider.get_session.await_count == 1
        assert session.call_tool.await_count == 2


class TestConcurrencyGuard:
    """W7 — N concurrent first calls share exactly one session."""

    async def test_concurrent_first_calls_share_one_session(self):
        """3 lazy tools called concurrently produce 1 session, 3 call_tool calls."""
        session = _make_session()
        provider = _make_provider(session)
        cache: dict = {}
        lock = asyncio.Lock()

        schemas = [_schema_dict(name=f"tool{i}") for i in range(3)]
        tools = create_lazy_mcp_tools(
            server_name="shared",
            schemas=schemas,
            session_provider=provider,
            shared_session_cache=cache,
            shared_session_lock=lock,
        )

        # Fire all 3 tools at the same time — the lock must serialize them
        # so only the first call hits the provider.
        await asyncio.gather(*(t.coroutine() for t in tools))

        # W7 invariant: N tools → 1 session.
        assert provider.get_session.await_count == 1
        # Each tool still made its own call_tool invocation.
        assert session.call_tool.await_count == 3
        # The cache now holds the resolved session.
        assert cache.get("shared") is session

    async def test_double_check_locking_prevents_duplicate_connections(self):
        """A slow ``get_session`` is invoked exactly once even with concurrent calls."""
        session = _make_session()
        call_count = 0
        original_get_session = provider_get_session = AsyncMock()

        async def slow_get_session(server_name: str):
            nonlocal call_count
            call_count += 1
            # Sleep long enough that the second concurrent caller is forced
            # to queue on the lock (rather than completing before us).
            await asyncio.sleep(0.1)
            return session

        provider = MagicMock()
        provider.get_session = AsyncMock(side_effect=slow_get_session)

        cache: dict = {}
        lock = asyncio.Lock()

        coro1 = _build_lazy_coroutine(
            server_name="x",
            original_tool_name="a",
            session_provider=provider,
            shared_session_cache=cache,
            shared_session_lock=lock,
            timeout_seconds=5.0,
        )
        coro2 = _build_lazy_coroutine(
            server_name="x",
            original_tool_name="b",
            session_provider=provider,
            shared_session_cache=cache,
            shared_session_lock=lock,
            timeout_seconds=5.0,
        )

        await asyncio.gather(coro1(), coro2())

        # The slow path was taken only once; the second arrival found the
        # session already cached on the re-check.
        assert call_count == 1
        assert provider.get_session.await_count == 1


class TestSharedSessionCache:
    """C1 invariant — same/different-server cache isolation."""

    async def test_shared_cache_across_tools_for_same_server(self):
        """3 tools for the same server share one cache; only the first connects."""
        session = _make_session()
        provider = _make_provider(session)
        cache: dict = {}
        lock = asyncio.Lock()

        schemas = [_schema_dict(name=f"tool{i}") for i in range(3)]
        tools = create_lazy_mcp_tools(
            server_name="srv",
            schemas=schemas,
            session_provider=provider,
            shared_session_cache=cache,
            shared_session_lock=lock,
        )

        # Call them in order: the first connects, the rest hit the cache.
        await tools[0].coroutine()
        await tools[1].coroutine()
        await tools[2].coroutine()

        assert provider.get_session.await_count == 1
        assert session.call_tool.await_count == 3

    async def test_separate_caches_for_different_servers(self):
        """Different servers keep independent caches and call the provider separately."""
        session_a = _make_session()
        session_b = _make_session()

        async def get_for(server_name: str):
            return session_a if server_name == "alpha" else session_b

        provider = MagicMock()
        provider.get_session = AsyncMock(side_effect=get_for)

        cache_a: dict = {}
        lock_a = asyncio.Lock()
        cache_b: dict = {}
        lock_b = asyncio.Lock()

        coro_a = _build_lazy_coroutine(
            server_name="alpha",
            original_tool_name="tool_a",
            session_provider=provider,
            shared_session_cache=cache_a,
            shared_session_lock=lock_a,
            timeout_seconds=5.0,
        )
        coro_b = _build_lazy_coroutine(
            server_name="beta",
            original_tool_name="tool_b",
            session_provider=provider,
            shared_session_cache=cache_b,
            shared_session_lock=lock_b,
            timeout_seconds=5.0,
        )

        await coro_a()
        await coro_b()

        # Each server populated its own cache.
        assert cache_a.get("alpha") is session_a
        assert cache_b.get("beta") is session_b
        # Cross-contamination: alpha must NOT be in cache_b and vice versa.
        assert "alpha" not in cache_b
        assert "beta" not in cache_a
        # Provider was called once per server.
        assert provider.get_session.await_count == 2


class TestTimeoutWrapping:
    """The ``tool_call_timeout`` knob on ``create_lazy_mcp_tools``."""

    async def test_timeout_applied_when_nonzero(self):
        """Positive ``tool_call_timeout`` wraps the session call in ``asyncio.timeout``."""
        session = _make_session()
        provider = _make_provider(session)
        cache: dict = {}
        lock = asyncio.Lock()

        # Build a real context manager mock so ``async with`` works.
        with patch.object(asyncio, "timeout") as mock_timeout:
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=cm)
            cm.__aexit__ = AsyncMock(return_value=None)
            mock_timeout.return_value = cm

            tools = create_lazy_mcp_tools(
                server_name="t",
                schemas=[_schema_dict(name="echo")],
                session_provider=provider,
                shared_session_cache=cache,
                shared_session_lock=lock,
                tool_call_timeout=5,
            )
            await tools[0].coroutine()

            # The non-zero timeout must be passed to ``asyncio.timeout``.
            mock_timeout.assert_called_once_with(5)

    async def test_no_timeout_when_zero(self):
        """``tool_call_timeout=0`` disables the ``asyncio.timeout`` wrap entirely."""
        session = _make_session()
        provider = _make_provider(session)
        cache: dict = {}
        lock = asyncio.Lock()

        with patch.object(asyncio, "timeout") as mock_timeout:
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=cm)
            cm.__aexit__ = AsyncMock(return_value=None)
            mock_timeout.return_value = cm

            tools = create_lazy_mcp_tools(
                server_name="t",
                schemas=[_schema_dict(name="echo")],
                session_provider=provider,
                shared_session_cache=cache,
                shared_session_lock=lock,
                tool_call_timeout=0,  # disabled
            )
            await tools[0].coroutine()

            # ``asyncio.timeout`` must NOT have been entered.
            mock_timeout.assert_not_called()
            # But ``call_tool`` still ran.
            session.call_tool.assert_awaited_once()


class TestMcpSessionProviderProtocol:
    """``McpSessionProvider`` is a ``@runtime_checkable`` Protocol — duck typing."""

    async def test_protocol_satisfied_by_any_async_get_session(self):
        """Any class with an async ``get_session`` method satisfies the protocol."""

        class CustomProvider:
            """Hand-rolled provider — no inheritance, no base class."""

            def __init__(self, session):
                self._session = session

            async def get_session(self, server_name: str):
                return self._session

        session = _make_session()
        custom = CustomProvider(session)
        # @runtime_checkable makes isinstance() work for the duck-typed shape.
        assert isinstance(custom, McpSessionProvider)

        # And it can actually drive a lazy coroutine end-to-end.
        cache: dict = {}
        lock = asyncio.Lock()
        coro, _, _, _, _ = _build_coroutine(
            server_name="custom",
            original_tool_name="echo",
            provider=custom,
            cache=cache,
            lock=lock,
        )
        result = await coro()
        # The fallback converter on the session's result shape returns the
        # content+artifact tuple — we only care that no exception leaked.
        assert result is not None
        assert cache.get("custom") is session
