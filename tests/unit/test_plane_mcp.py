"""Unit tests for the Plane built-in MCP server.

Covers:
- ``PlaneServerDefinition.is_available()`` — both URL and API key required.
- Tool name prefix override (``plane_`` instead of ``mcp_plane_``).
- Dispatch safety — prefixed tool dispatches to MCP with the ORIGINAL name.
- ``resolve_tool_filter`` plane-vs-mcp category isolation.
- No ``MCP_DISABLE_BUILT_IN_PLANE`` toggle reference remains.

Follows the patterns in ``test_mcp_tool_filter.py`` and
``test_mcp_lazy_init.py``. Like ``test_mcp_lazy_init.py``, we must unmock
``daemon.mcp.tool_adapter`` because the root ``conftest.py`` replaces it
with a stub that lacks the real functions.
"""
import asyncio
import inspect
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Unmock daemon.mcp.tool_adapter so we import the REAL module. The conftest
# mock lacks ``create_lazy_mcp_tools`` / ``_build_lazy_coroutine`` /
# ``is_mcp_tool`` (it stubs them as MagicMock).
# ---------------------------------------------------------------------------
_mock_tool_adapter = sys.modules.pop("daemon.mcp.tool_adapter", None)
from daemon.mcp.tool_adapter import create_lazy_mcp_tools, is_mcp_tool  # noqa: E402
if _mock_tool_adapter is not None:
    sys.modules["daemon.mcp.tool_adapter"] = _mock_tool_adapter

from daemon.mcp.builtin_servers.plane import PlaneServerDefinition  # noqa: E402
from daemon.tools.instance import resolve_tool_filter  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _schema_dict(name: str = "list_issues", description: str = "List issues",
                 input_schema: dict | None = None) -> dict:
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


# ---------------------------------------------------------------------------
# Class 1: TestPlaneIsAvailable
# ---------------------------------------------------------------------------

class TestPlaneIsAvailable:
    """Test ``PlaneServerDefinition.is_available()`` — both URL and API key required."""

    def test_not_available_no_env_vars(self, monkeypatch):
        """Both PLANE_MCP_URL and PLANE_MCP_API_KEY unset → False."""
        monkeypatch.delenv("PLANE_MCP_URL", raising=False)
        monkeypatch.delenv("PLANE_MCP_API_KEY", raising=False)
        assert PlaneServerDefinition.is_available() is False

    def test_available_both_set(self, monkeypatch):
        """Both URL and API key set → True."""
        monkeypatch.setenv("PLANE_MCP_URL", "https://mcp.example/plane/mcp")
        monkeypatch.setenv("PLANE_MCP_API_KEY", "secret-key-123")
        assert PlaneServerDefinition.is_available() is True

    def test_not_available_only_url(self, monkeypatch):
        """URL set, API_KEY unset → False."""
        monkeypatch.setenv("PLANE_MCP_URL", "https://mcp.example/plane/mcp")
        monkeypatch.delenv("PLANE_MCP_API_KEY", raising=False)
        assert PlaneServerDefinition.is_available() is False

    def test_not_available_only_api_key(self, monkeypatch):
        """API_KEY set, URL unset → False."""
        monkeypatch.delenv("PLANE_MCP_URL", raising=False)
        monkeypatch.setenv("PLANE_MCP_API_KEY", "secret-key-123")
        assert PlaneServerDefinition.is_available() is False

    def test_available_ignores_whitespace(self, monkeypatch):
        """Whitespace-only values (stripped to empty) → False."""
        monkeypatch.setenv("PLANE_MCP_URL", "   ")
        monkeypatch.setenv("PLANE_MCP_API_KEY", "   ")
        assert PlaneServerDefinition.is_available() is False


# ---------------------------------------------------------------------------
# Class 2: TestPlanePrefixOverride
# ---------------------------------------------------------------------------

class TestPlanePrefixOverride:
    """Test the ``tool_name_prefix`` override produces ``plane_*`` names."""

    def test_tools_get_plane_prefix(self):
        """``tool_name_prefix='plane'`` → ``plane_list_issues``, NOT ``mcp_plane_...``."""
        schemas = [_schema_dict(name="list_issues", description="List issues")]
        tools = create_lazy_mcp_tools(
            server_name="plane",
            schemas=schemas,
            session_provider=_make_provider(),
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
            tool_name_prefix="plane",
        )
        assert len(tools) == 1
        assert tools[0].name == "plane_list_issues"
        assert not tools[0].name.startswith("mcp_plane_")

    def test_default_prefix_unchanged(self):
        """Regression guard: no prefix param → standard ``mcp_{server}_{tool}``."""
        schemas = [_schema_dict(name="get_docs", description="Get docs")]
        tools = create_lazy_mcp_tools(
            server_name="ctx7",
            schemas=schemas,
            session_provider=_make_provider(),
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
        )
        assert len(tools) == 1
        assert tools[0].name == "mcp_ctx7_get_docs"

    def test_is_mcp_tool_false_for_plane_tools(self):
        """``is_mcp_tool('plane_list_issues')`` → False; plane tools bypass MCP detection."""
        assert is_mcp_tool("plane_list_issues") is False
        assert is_mcp_tool("plane_create_issue") is False

    def test_is_mcp_tool_true_for_standard_mcp(self):
        """Regression guard: ``is_mcp_tool('mcp_ctx7_x')`` → True."""
        assert is_mcp_tool("mcp_ctx7_get_docs") is True


# ---------------------------------------------------------------------------
# Class 3: TestDispatchSafety (CRITICAL)
# ---------------------------------------------------------------------------

class TestDispatchSafety:
    """Verify that a prefixed tool dispatches to MCP with the ORIGINAL name.

    The lazy coroutine built by ``_build_lazy_coroutine`` closes over
    ``original_tool_name`` and calls ``session.call_tool(original_tool_name,
    kwargs)``. The exposed ``StructuredTool.name`` (e.g.
    ``plane_list_issues``) is only the tool surface — it must NOT reach the
    MCP server.
    """

    def test_dispatch_uses_original_tool_name(self):
        """Prefixed tool dispatches to MCP with ORIGINAL name, not prefixed."""
        mock_session = _make_session()

        session_provider = _make_provider(mock_session)

        schemas = [_schema_dict(name="list_issues", description="List issues")]
        tools = create_lazy_mcp_tools(
            server_name="plane",
            schemas=schemas,
            session_provider=session_provider,
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
            tool_call_timeout=120,
            tool_name_prefix="plane",
        )
        tool = tools[0]
        assert tool.name == "plane_list_issues"

        # Call the tool
        asyncio.run(tool.coroutine(project_id="test"))

        # CRITICAL: the MCP session was called with the ORIGINAL name
        mock_session.call_tool.assert_called_once()
        call_args = mock_session.call_tool.call_args
        # session.call_tool(original_tool_name, kwargs) — first positional is the name
        dispatched_name = call_args.args[0] if call_args.args else call_args.kwargs.get("name")
        assert dispatched_name == "list_issues", (
            f"Dispatch used '{dispatched_name}' instead of original 'list_issues'!"
        )

    def test_dispatch_kwargs_forwarded(self):
        """Tool call kwargs (minus stripped 'runtime') are forwarded to the MCP session."""
        mock_session = _make_session()
        session_provider = _make_provider(mock_session)

        schemas = [_schema_dict(name="list_issues", description="List issues")]
        tools = create_lazy_mcp_tools(
            server_name="plane",
            schemas=schemas,
            session_provider=session_provider,
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
            tool_name_prefix="plane",
        )
        tool = tools[0]

        asyncio.run(tool.coroutine(project_id="test", limit=10, runtime="should-be-stripped"))

        mock_session.call_tool.assert_called_once()
        call_args = mock_session.call_tool.call_args
        # First positional = tool name; second positional = kwargs dict
        forwarded_kwargs = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs
        assert "runtime" not in forwarded_kwargs, (
            "runtime kwarg should be stripped before MCP dispatch"
        )
        assert forwarded_kwargs.get("project_id") == "test"
        assert forwarded_kwargs.get("limit") == 10


# ---------------------------------------------------------------------------
# Class 4: TestResolveToolFilterPlaneVsMcp
# ---------------------------------------------------------------------------

class TestResolveToolFilterPlaneVsMcp:
    """Test that ``allow=['plane'], deny=['mcp']`` correctly separates categories.

    ``plane_*`` tools don't match the ``mcp_`` prefix so they're never added
    to the mcp category during expansion — that's the safety mechanism that
    keeps them alive when ``deny=['mcp']`` is applied.
    """

    def test_plane_allowed_mcp_denied(self):
        """``allow=['plane'], deny=['mcp']`` → plane tools survive, mcp excluded."""
        tool_categories = {
            "plane": ["plane_list_issues", "plane_create_issue"],
            "mcp": [],  # MCP category empty — will be expanded from all_tool_names
        }
        all_tool_names = {
            "plane_list_issues", "plane_create_issue",
            "mcp_ctx7_get_docs",
        }

        result = resolve_tool_filter(
            allow=["plane"],
            deny=["mcp"],
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        assert result == {"plane_list_issues", "plane_create_issue"}

    def test_plane_tools_not_caught_by_mcp_deny(self):
        """``allow=None, deny=['mcp']`` → plane_* survive, mcp_* excluded."""
        tool_categories = {
            "plane": ["plane_list_issues", "plane_create_issue"],
            "mcp": [],  # MCP category empty — will be expanded from all_tool_names
        }
        all_tool_names = {
            "plane_list_issues", "plane_create_issue",
            "mcp_ctx7_get_docs",
        }

        result = resolve_tool_filter(
            allow=None,
            deny=["mcp"],
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        # plane_* tools survive, mcp_* excluded
        assert "plane_list_issues" in result
        assert "plane_create_issue" in result
        assert "mcp_ctx7_get_docs" not in result


# ---------------------------------------------------------------------------
# Class 5: TestNoDisableToggle
# ---------------------------------------------------------------------------

class TestNoDisableToggle:
    """Verify the ``MCP_DISABLE_BUILT_IN_PLANE`` toggle was fully removed."""

    def test_no_disable_toggle_reference(self):
        """``MCP_DISABLE_BUILT_IN_PLANE`` must not appear anywhere in the module."""
        src = inspect.getsource(PlaneServerDefinition)
        assert "MCP_DISABLE_BUILT_IN_PLANE" not in src

        from daemon.mcp.builtin_servers import plane as plane_mod
        mod_src = inspect.getsource(plane_mod)
        assert "MCP_DISABLE_BUILT_IN_PLANE" not in mod_src
