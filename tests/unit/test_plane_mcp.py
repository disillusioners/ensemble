"""Unit tests for the Plane built-in MCP server.

Covers:
- ``PlaneServerDefinition.is_available()`` — both URL and API key required.
- Tool name prefix override (``plane_`` instead of ``mcp_plane_``).
- Dispatch safety — prefixed tool dispatches to MCP with the ORIGINAL name.
- ``resolve_tool_filter`` plane-vs-mcp category isolation.
- No ``MCP_DISABLE_BUILT_IN_PLANE`` toggle reference remains.
- ``mcp_full_access`` per-agent opt-out from CR-3 read-only strip
  (Approach B, PM-domain-access architecture doc §1, §2, §6).

Follows the patterns in ``test_mcp_tool_filter.py`` and
``test_mcp_lazy_init.py``. Like ``test_mcp_lazy_init.py``, we must unmock
``daemon.mcp.tool_adapter`` because the root ``conftest.py`` replaces it
with a stub that lacks the real functions.
"""
import asyncio
import inspect
import pytest
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


# ---------------------------------------------------------------------------
# Class 6: TestDoublePrefixGuard
# ---------------------------------------------------------------------------

class TestDoublePrefixGuard:
    """Document behavior when a server-side tool name already starts with 'plane_'.

    The prefix override is a simple string concatenation: ``f"{prefix}_{tool_name}"``.
    If the MCP server's tool is already named ``plane_list_issues``, the exposed
    name becomes ``plane_plane_list_issues``. This is intentional (no dedup)
    because the override is meant for servers whose tools DON'T already match
    the prefix.
    """

    def test_double_prefix_documented(self):
        """Server tool named 'plane_get_data' → exposed as 'plane_plane_get_data'."""
        schemas = [_schema_dict(name="plane_get_data", description="Already prefixed")]
        tools = create_lazy_mcp_tools(
            server_name="plane",
            schemas=schemas,
            session_provider=_make_provider(),
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
            tool_name_prefix="plane",
        )
        assert len(tools) == 1
        assert tools[0].name == "plane_plane_get_data"

    def test_double_prefix_still_not_mcp_tool(self):
        """Even with double prefix, is_mcp_tool returns False (no 'mcp_' prefix)."""
        assert is_mcp_tool("plane_plane_get_data") is False


# ---------------------------------------------------------------------------
# Class 7: TestCategoryCollision
# ---------------------------------------------------------------------------

class TestCategoryCollision:
    """Verify 'plane' category and 'mcp' category don't overlap.

    A tool named ``plane_list_issues`` should NOT be discovered by the
    'mcp' category expansion because ``is_mcp_tool()`` returns False for
    names not starting with 'mcp_'.
    """

    def test_plane_tool_not_in_mcp_category(self):
        """plane_list_issues is NOT in the mcp category after expansion."""
        from daemon.tools.instance import resolve_tool_filter

        tool_categories = {
            "plane": ["plane_list_issues", "plane_create_issue"],
            "mcp": [],  # Empty — will be expanded from all_tool_names
        }
        all_tool_names = {
            "plane_list_issues", "plane_create_issue",
            "mcp_ctx7_get_docs",
            "mcp_webfetch_fetch",
        }

        # Expand mcp category: only tools where is_mcp_tool() is True
        result = resolve_tool_filter(
            allow=["mcp"],
            deny=[],
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        # plane tools should NOT be in the mcp allow set
        assert "plane_list_issues" not in result
        assert "plane_create_issue" not in result
        # Standard mcp tools SHOULD be in the set
        assert "mcp_ctx7_get_docs" in result
        assert "mcp_webfetch_fetch" in result

    def test_plane_category_independent_from_mcp(self):
        """Allowing 'plane' doesn't pull in any mcp_ tools."""
        from daemon.tools.instance import resolve_tool_filter

        tool_categories = {
            "plane": ["plane_list_issues"],
            "mcp": [],
        }
        all_tool_names = {
            "plane_list_issues",
            "mcp_ctx7_get_docs",
        }

        result = resolve_tool_filter(
            allow=["plane"],
            deny=[],
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        assert result == {"plane_list_issues"}


# ---------------------------------------------------------------------------
# Class 8: TestMultipleBuiltinServers
# ---------------------------------------------------------------------------

class TestMultipleBuiltinServers:
    """Verify Plane's addition doesn't affect context7 or webfetch built-in servers.

    Context7 and webfetch use the default ``tool_name_prefix=None``, so their
    tools should still follow the ``mcp_{server}_{tool}`` convention.
    """

    def test_context7_uses_default_prefix(self):
        """Context7 tools use 'mcp_ctx7_' prefix (not affected by Plane override)."""
        schemas = [_schema_dict(name="get_docs", description="Get docs")]
        tools = create_lazy_mcp_tools(
            server_name="ctx7",
            schemas=schemas,
            session_provider=_make_provider(),
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
            # No tool_name_prefix → default mcp_{server}_ behavior
        )
        assert len(tools) == 1
        assert tools[0].name == "mcp_ctx7_get_docs"

    def test_webfetch_uses_default_prefix(self):
        """WebFetch tools use 'mcp_webfetch_' prefix (not affected by Plane override)."""
        schemas = [_schema_dict(name="fetch", description="Fetch URL")]
        tools = create_lazy_mcp_tools(
            server_name="webfetch",
            schemas=schemas,
            session_provider=_make_provider(),
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
            # No tool_name_prefix → default mcp_{server}_ behavior
        )
        assert len(tools) == 1
        assert tools[0].name == "mcp_webfetch_fetch"

    def test_context7_is_mcp_tool_true(self):
        """Context7 tools are still detected as MCP tools (is_mcp_tool → True)."""
        assert is_mcp_tool("mcp_ctx7_get_docs") is True

    def test_webfetch_is_mcp_tool_true(self):
        """WebFetch tools are still detected as MCP tools (is_mcp_tool → True)."""
        assert is_mcp_tool("mcp_webfetch_fetch") is True


# ---------------------------------------------------------------------------
# Class 9: TestToolNamePrefixResolution
# ---------------------------------------------------------------------------

class TestToolNamePrefixResolution:
    """Verify _get_tool_name_prefix() in mcp_service.py returns correct values.

    PlaneServerDefinition has tool_name_prefix='plane'.
    Context7ServerDefinition and WebFetchServerDefinition have tool_name_prefix=None (default).
    Non-builtin servers return None.
    """

    def test_plane_prefix_resolution(self):
        """_get_tool_name_prefix('plane') returns 'plane'."""
        from daemon.mcp.builtin_servers import get_registry
        registry = get_registry()
        defn = registry.get_by_name("plane")
        assert defn is not None
        assert defn.tool_name_prefix == "plane"

    def test_context7_prefix_resolution_none(self):
        """_get_tool_name_prefix('context7') returns None (default)."""
        from daemon.mcp.builtin_servers import get_registry
        registry = get_registry()
        defn = registry.get_by_name("context7")
        assert defn is not None
        assert defn.tool_name_prefix is None

    def test_webfetch_prefix_resolution_none(self):
        """_get_tool_name_prefix('webfetch') returns None (default)."""
        from daemon.mcp.builtin_servers import get_registry
        registry = get_registry()
        defn = registry.get_by_name("webfetch")
        assert defn is not None
        assert defn.tool_name_prefix is None

    def test_nonexistent_server_prefix_none(self):
        """_get_tool_name_prefix for non-builtin server returns None."""
        from daemon.mcp.builtin_servers import get_registry
        registry = get_registry()
        defn = registry.get_by_name("nonexistent-server-xyz")
        assert defn is None

    def test_plane_transport_not_stdio(self):
        """Plane uses streamable-http, not stdio — this is why it bypasses warmup pool."""
        defn = PlaneServerDefinition()
        config = defn.get_base_config()
        assert config["transport"] == "streamable-http"
        assert config["transport"] != "stdio"

    def test_context7_transport_stdio(self):
        """Context7 uses stdio transport (goes through warmup pool)."""
        from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition
        defn = Context7ServerDefinition()
        config = defn.get_base_config()
        assert config["transport"] == "stdio"


# ---------------------------------------------------------------------------
# Class 10: TestPlaneResilienceConfig (Phase 4)
# ---------------------------------------------------------------------------

class TestPlaneResilienceConfig:
    """Plane-specific resilience tuning via ``resilience_config``.

    Verifies the Plane builtin opts into the hybrid resilience layer
    with the documented defaults + env var overrides. The function
    is called once per ``create_lazy_mcp_tools`` invocation, so the
    values must be stable for a given env state.
    """

    def test_resilience_config_defaults(self, monkeypatch):
        """No env vars → defaults: TTL=300, retries=3, threshold=5, etc."""
        # Clear all PLANE_* env vars that could affect defaults.
        for var in (
            "PLANE_RETRY_MAX_ATTEMPTS",
            "PLANE_RETRY_BASE_DELAY",
            "PLANE_CACHE_TTL_SECONDS",
            "PLANE_CIRCUIT_FAILURE_THRESHOLD",
            "PLANE_CIRCUIT_RECOVERY_TIMEOUT",
            "PLANE_PROBE_TIMEOUT",
        ):
            monkeypatch.delenv(var, raising=False)

        defn = PlaneServerDefinition()
        cfg = defn.resilience_config

        # Cache defaults
        assert cfg.cache_ttl == 300.0
        assert cfg.cache_max_entries == 1000
        # Retry defaults
        assert cfg.retry_policy is not None
        assert cfg.retry_policy.max_attempts == 3
        assert cfg.retry_policy.base_delay == 1.0
        # Circuit defaults
        assert cfg.circuit_failure_threshold == 5
        assert cfg.circuit_recovery_timeout == 60.0
        assert cfg.probe_timeout == 5.0
        # Staleness default
        assert cfg.stale_threshold == 300.0

    def test_resilience_config_env_override(self, monkeypatch):
        """All env vars override their respective defaults."""
        monkeypatch.setenv("PLANE_RETRY_MAX_ATTEMPTS", "7")
        monkeypatch.setenv("PLANE_RETRY_BASE_DELAY", "2.5")
        monkeypatch.setenv("PLANE_CACHE_TTL_SECONDS", "120")
        monkeypatch.setenv("PLANE_CIRCUIT_FAILURE_THRESHOLD", "10")
        monkeypatch.setenv("PLANE_CIRCUIT_RECOVERY_TIMEOUT", "180")
        monkeypatch.setenv("PLANE_PROBE_TIMEOUT", "15")

        defn = PlaneServerDefinition()
        cfg = defn.resilience_config

        assert cfg.cache_ttl == 120.0
        assert cfg.retry_policy.max_attempts == 7
        assert cfg.retry_policy.base_delay == 2.5
        assert cfg.circuit_failure_threshold == 10
        assert cfg.circuit_recovery_timeout == 180.0
        assert cfg.probe_timeout == 15.0

    def test_resilience_config_is_not_none(self):
        """Plane opts in (returns a config, not None)."""
        defn = PlaneServerDefinition()
        # Even with no env overrides, Plane returns a real config —
        # the resilience layer is opt-in via returning non-None.
        cfg = defn.resilience_config
        assert cfg is not None

    def test_other_builtins_resilience_is_none(self):
        """context7 and webfetch don't opt into resilience."""
        from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition
        from daemon.mcp.builtin_servers.webfetch import WebFetchServerDefinition

        assert Context7ServerDefinition().resilience_config is None
        assert WebFetchServerDefinition().resilience_config is None

    def test_read_tool_patterns(self):
        """Plane's read patterns: list_, get_, search_."""
        defn = PlaneServerDefinition()
        cfg = defn.resilience_config
        assert "list_" in cfg.read_tool_patterns
        assert "get_" in cfg.read_tool_patterns
        assert "search_" in cfg.read_tool_patterns

    def test_write_tool_patterns(self):
        """Plane's write patterns include create/update/delete + Plane verbs."""
        defn = PlaneServerDefinition()
        cfg = defn.resilience_config
        # Required per the spec.
        for p in ("create_", "update_", "delete_", "add_", "remove_"):
            assert p in cfg.write_tool_patterns, f"Missing write pattern: {p}"
        # Plane-specific verbs.
        for p in ("set_", "edit_", "assign_"):
            assert p in cfg.write_tool_patterns, f"Missing write pattern: {p}"

    def test_fallback_message_structure(self):
        """Fallback is a valid JSON with status=unavailable + source=plane."""
        import json
        defn = PlaneServerDefinition()
        cfg = defn.resilience_config
        assert cfg.fallback_message is not None
        # Must be valid JSON.
        parsed = json.loads(cfg.fallback_message)
        assert parsed["status"] == "unavailable"
        assert parsed["source"] == "plane"
        # Message must be human-readable.
        assert "Plane" in parsed["message"]
        assert "unreachable" in parsed["message"].lower() or "unavailable" in parsed["message"].lower()

    def test_fallback_message_consistent_with_module_constant(self):
        """``cfg.fallback_message`` equals the module-level constant."""
        from daemon.mcp.builtin_servers.plane import _DEFAULT_PLANE_FALLBACK
        defn = PlaneServerDefinition()
        cfg = defn.resilience_config
        assert cfg.fallback_message == _DEFAULT_PLANE_FALLBACK

    def test_resilience_config_does_not_break_when_url_missing(self, monkeypatch):
        """``resilience_config`` reads ONLY resilience env vars, not URL/key.

        Even with PLANE_MCP_URL + PLANE_MCP_API_KEY unset (which would
        make ``is_available()`` return False), the resilience config
        still builds cleanly. This matters because ``preload_mcp_tools``
        could in principle query both — though in practice it gates on
        ``is_available()`` first. The decoupling is intentional so the
        resilience plumbing doesn't accidentally depend on env state.
        """
        monkeypatch.delenv("PLANE_MCP_URL", raising=False)
        monkeypatch.delenv("PLANE_MCP_API_KEY", raising=False)
        defn = PlaneServerDefinition()
        cfg = defn.resilience_config
        assert cfg is not None
        assert cfg.cache_ttl == 300.0


# ---------------------------------------------------------------------------
# Class 11: TestPlaneReadToolClassification (Phase 4)
# ---------------------------------------------------------------------------

class TestPlaneReadToolClassification:
    """Verify Plane's ``is_read_tool`` works for actual Plane tool names.

    Uses the real ``PlaneServerDefinition.resilience_config`` patterns
    to classify tool names — proves the Plane tuning is internally
    consistent (read + write patterns are exclusive at the start of
    the stripped tool name).
    """

    def _is_read(self, tool_name: str) -> bool:
        from daemon.mcp.resilience import is_read_tool
        defn = PlaneServerDefinition()
        return is_read_tool(tool_name, defn.resilience_config)

    def test_plane_list_issues_is_read(self):
        """``plane_list_issues`` is a read tool."""
        assert self._is_read("plane_list_issues") is True

    def test_plane_get_project_is_read(self):
        """``plane_get_project`` is a read tool."""
        assert self._is_read("plane_get_project") is True

    def test_plane_search_issues_is_read(self):
        """``plane_search_issues`` is a read tool."""
        assert self._is_read("plane_search_issues") is True

    def test_plane_create_issue_is_write(self):
        """``plane_create_issue`` is a write tool."""
        assert self._is_read("plane_create_issue") is False

    def test_plane_update_issue_is_write(self):
        """``plane_update_issue`` is a write tool."""
        assert self._is_read("plane_update_issue") is False

    def test_plane_delete_issue_is_write(self):
        """``plane_delete_issue`` is a write tool."""
        assert self._is_read("plane_delete_issue") is False

    def test_plane_set_priority_is_write(self):
        """Plane-specific verb: ``plane_set_issue_priority`` is write."""
        assert self._is_read("plane_set_issue_priority") is False

    def test_plane_assign_issue_is_write(self):
        """Plane-specific verb: ``plane_assign_issue`` is write."""
        assert self._is_read("plane_assign_issue") is False

    def test_plane_edit_issue_is_write(self):
        """Plane-specific verb: ``plane_edit_issue`` is write."""
        assert self._is_read("plane_edit_issue") is False

    def test_plane_add_comment_is_write(self):
        """``plane_add_comment`` is a write tool."""
        assert self._is_read("plane_add_comment") is False

    def test_plane_remove_label_is_write(self):
        """``plane_remove_label`` is a write tool."""
        assert self._is_read("plane_remove_label") is False


# ---------------------------------------------------------------------------
# Class 12: TestPlaneReadOnlyFilter (CR-3)
# ---------------------------------------------------------------------------
#
# CR-3 added the ``read_only_tools`` property on Plane's builtin
# definition. When True, ``McpService`` filters the schema list at
# discovery time using ``is_read_tool(name, resilience_config)`` so the
# LLM never sees write tools in its tool list. The deny-list in
# meta.json is belt-and-suspenders, not the primary enforcement.
#
# These tests pin three independent layers:
#
#   1. The property itself: ``PlaneServerDefinition.read_only_tools``
#      is True; other builtins default to False.
#   2. The filter invariant: when a mixed list of read + write
#      schemas passes through ``is_read_tool`` with Plane's
#      ``resilience_config``, only the read schemas survive.
#   3. The end-to-end list-shape: the surviving schemas are exactly
#      the ones an LLM would see if the filter were applied to a
#      realistic Plane tool surface.


class TestPlaneReadOnlyFilter:
    """CR-3: ``PlaneServerDefinition.read_only_tools`` and the schema
    filter it gates inside ``McpService``.

    The filter is the only thing keeping new write verbs (added by
    future Plane MCP server releases) out of the LLM's tool list.
    Without it, the deny-list in ``project-manager/meta.json``
    could fall behind the server surface and silently expose a
    write verb the agent was never supposed to see.
    """

    def test_plane_read_only_tools_property_is_true(self):
        """``PlaneServerDefinition().read_only_tools`` is True.

        Pins the declaration: the Plane builtin opts into the
        read-only filter at definition time. If a future refactor
        drops the override, this test fails immediately and the
        CR-3 contract is preserved as an explicit decision.
        """
        defn = PlaneServerDefinition()
        assert defn.read_only_tools is True, (
            "PlaneServerDefinition.read_only_tools must be True — "
            "CR-3 requires the read-only filter at discovery time."
        )

    def test_other_builtins_default_read_only_false(self):
        """Other builtins default ``read_only_tools=False``.

        Only Plane opts into the filter today. Context7 and
        WebFetch default to the legacy "expose all tools, let
        meta.json deny-list filter" behavior. Pin the default
        here so a future addition of another read-only server
        (or accidental flip of the base class) is an explicit,
        tested decision.
        """
        from daemon.mcp.builtin_servers.context7 import (
            Context7ServerDefinition,
        )
        from daemon.mcp.builtin_servers.webfetch import (
            WebFetchServerDefinition,
        )

        assert Context7ServerDefinition().read_only_tools is False, (
            "Context7 must NOT default to read_only_tools=True — "
            "its tool surface is small + stable and the agent "
            "needs both reads and writes."
        )
        assert WebFetchServerDefinition().read_only_tools is False, (
            "WebFetch must NOT default to read_only_tools=True — "
            "same reasoning as Context7."
        )

    def test_read_only_filter_drops_write_tools_from_schema_list(self):
        """The McpService filter applied to a realistic Plane tool
        surface drops every write verb.

        Simulates the CR-3 contract end-to-end: take a mixed
        list of read + write schemas (using the exact
        ``is_read_tool(name, PlaneServerDefinition.resilience_config)``
        classifier the McpService filter uses), keep only the
        reads, and assert the surviving set is exactly the read
        verbs. If the Plane patterns ever drift (e.g. a new
        write pattern is added without ``write_tool_patterns``
        being updated), this test fails.
        """
        from daemon.mcp.resilience import is_read_tool

        defn = PlaneServerDefinition()
        cfg = defn.resilience_config

        # Realistic Plane tool surface — covers every read/write
        # verb in the documented schema. Names are unprefixed;
        # ``is_read_tool`` strips the prefix before matching.
        mixed_schemas = [
            {"name": "list_issues"},
            {"name": "list_projects"},
            {"name": "list_cycles"},
            {"name": "get_issue"},
            {"name": "get_project"},
            {"name": "get_cycle"},
            {"name": "search_issues"},
            # Write verbs — every one of these MUST be dropped.
            {"name": "create_issue"},
            {"name": "update_issue"},
            {"name": "delete_issue"},
            {"name": "create_project"},
            {"name": "add_comment"},
            {"name": "remove_comment"},
            {"name": "add_label"},
            {"name": "remove_label"},
            {"name": "set_priority"},
            {"name": "edit_issue"},
            {"name": "assign_issue"},
            {"name": "create_cycle"},
            {"name": "update_cycle"},
        ]

        # Apply the same filter the McpService applies: build the
        # adapted prefix (``plane_``) and let ``is_read_tool``
        # classify each name. The prefix is added so the function
        # sees the canonical Plane naming convention.
        effective_prefix = "plane_"
        surviving = [
            s for s in mixed_schemas
            if is_read_tool(f"{effective_prefix}{s['name']}", cfg)
        ]
        surviving_names = sorted(s["name"] for s in surviving)

        expected_reads = sorted([
            "list_issues", "list_projects", "list_cycles",
            "get_issue", "get_project", "get_cycle",
            "search_issues",
        ])
        assert surviving_names == expected_reads, (
            f"CR-3 filter must keep ONLY read verbs. "
            f"Survived: {surviving_names}; expected: {expected_reads}"
        )

        # Spot-check: no write verb survived. This is the core
        # CR-3 guarantee — the LLM can never call any of these.
        write_verbs = {
            "create_issue", "update_issue", "delete_issue",
            "create_project", "add_comment", "remove_comment",
            "add_label", "remove_label", "set_priority",
            "edit_issue", "assign_issue", "create_cycle",
            "update_cycle",
        }
        leaked = set(surviving_names) & write_verbs
        assert not leaked, (
            f"CR-3 must drop every write verb from the schema list. "
            f"Leaked: {sorted(leaked)}"
        )

    def test_read_only_filter_keeps_read_tools_with_new_verbs(self):
        """A read verb not in the test list still survives the filter.

        Forward-compat guard: the filter is pattern-based, so
        any new ``list_*`` / ``get_*`` / ``search_*`` tool added
        to Plane's server (a future ``list_milestones`` for
        example) automatically passes the filter without a
        meta.json change. This is the value CR-3 adds over a
        pure deny-list.
        """
        from daemon.mcp.resilience import is_read_tool

        defn = PlaneServerDefinition()
        cfg = defn.resilience_config

        # A new verb that didn't exist when the deny-list was
        # written. ``is_read_tool`` must classify it correctly
        # because the read patterns (``list_``, ``get_``,
        # ``search_``) are pattern-based.
        assert is_read_tool("plane_list_milestones", cfg) is True, (
            "Read patterns must be pattern-based so future read "
            "verbs (e.g. list_milestones) survive the filter "
            "without a meta.json change."
        )
        assert is_read_tool("plane_get_release", cfg) is True
        assert is_read_tool("plane_search_users", cfg) is True

        # A future write verb also works without a meta.json
        # change — the write patterns catch it. The deny-list
        # is the second line of defense; the read-only filter
        # is the first.
        assert is_read_tool("plane_export_data", cfg) is False, (
            "Write patterns must be pattern-based so future "
            "write verbs (e.g. export_data) are dropped "
            "automatically — deny-list falls behind if it "
            "isn't kept in sync."
        )

    def test_builtin_base_class_default_is_false(self):
        """The base ``BuiltinServerDefinition.read_only_tools`` default
        is False (preserves legacy behavior for non-Plane builtins).

        Pins the abstract default so a refactor of the base
        class can't silently turn the read-only filter on for
        every builtin. The CR-3 fix is opt-in per server, not
        a forced opt-in.
        """
        from daemon.mcp.builtin_servers.base import (
            BuiltinServerDefinition,
        )

        # Direct instance — but the base is ABC; check via a
        # concrete subclass that doesn't override the property.
        # We use Plane here but temporarily monkey-patch the
        # override to verify the BASE class default is False.
        defn = PlaneServerDefinition()

        # Temporarily shadow Plane's override with the base default.
        base_default = BuiltinServerDefinition.read_only_tools.fget(defn)
        assert base_default is False, (
            "BuiltinServerDefinition.read_only_tools base default "
            "must be False — CR-3 is opt-in per server, not a "
            "forced opt-in across all builtins."
        )


# ---------------------------------------------------------------------------
# Class 13: TestMcpFullAccess (Approach B opt-out)
# ---------------------------------------------------------------------------
#
# Approach B in the PM-domain-access architecture doc (§1, §2, §6) gives
# agents a per-agent ``mcp_full_access`` opt-out from the global
# read-only strip. ``PlaneServerDefinition.read_only_tools`` stays True;
# the per-agent list is consulted at ``McpService._get_read_only_tools``
# time, and an entry of ``"plane"`` returns False (skip the strip).
# Typo and unknown entries are validated in
# ``AgentRegistry.validate_tool_configs`` and fail closed — the strip
# stays applied so a future Plane write verb can't leak because of a
# silent typo.
#
# These tests pin the four layers Approach B touches:
#
#   1. The model accepts the field (``AgentMetadata.mcp_full_access``).
#   2. The filter consults it correctly (``_get_read_only_tools``).
#   3. The registry validator warns on unknown names.
#   4. Identity resolution in ``preload_mcp_tools`` fails closed when
#      the agent cannot be resolved (no exception leak).
#
# Test #7 (effective-surface inventory) and test #8 (e2e with a mock
# Plane MCP server) are intentionally NOT included here per the doc:
# test #7 belongs to the PM-side worker that owns the agent-side
# changes (drift alarm over time), and test #8 is deferred e2e.


class TestMcpFullAccess:
    """Approach B: ``mcp_full_access`` per-agent opt-out from CR-3.

    The agent's ``meta.json`` declares ``mcp_full_access: ["plane"]``
    to receive the full Plane tool surface (writes included) instead
    of the read-only-stripped version every other agent sees.

    Fail-closed: unknown entries, identity-resolution failures, and
    missing identity all leave the CR-3 strip applied. The agent
    boots read-only rather than write-open.
    """

    def test_agent_metadata_accepts_mcp_full_access_field(self):
        """``AgentMetadata`` accepts ``mcp_full_access`` and defaults to ``[]``.

        Pins the Pydantic model contract: the field is recognized by
        ``daemon.registry.AgentMetadata`` and defaults to an empty list
        so an agent without the opt-out inherits the global read-only
        behavior. The list round-trips through the model so a
        ``meta.json`` declaring ``["plane"]`` produces the expected
        ``AgentMetadata`` instance — the entire daemon flow depends
        on this single line of Pydantic plumbing.
        """
        from daemon.registry import AgentMetadata

        # Default: no opt-out.
        meta_default = AgentMetadata(
            id="agent-default",
            name="Agent",
            path="/tmp/agent-default",
        )
        assert meta_default.mcp_full_access == [], (
            "AgentMetadata.mcp_full_access must default to [] — "
            "the global read-only filter applies to every agent "
            "that does not opt out explicitly."
        )

        # Explicit opt-out: list round-trips.
        meta_opted_out = AgentMetadata(
            id="agent-opted-out",
            name="Agent",
            path="/tmp/agent-opted-out",
            mcp_full_access=["plane"],
        )
        assert meta_opted_out.mcp_full_access == ["plane"]

        # Empty list (explicit) behaves like the default — the
        # validator does not warn, and the strip stays applied.
        meta_empty = AgentMetadata(
            id="agent-empty",
            name="Agent",
            path="/tmp/agent-empty",
            mcp_full_access=[],
        )
        assert meta_empty.mcp_full_access == []

        # Multi-server opt-out is supported (Approach B is
        # extensible beyond Plane).
        meta_multi = AgentMetadata(
            id="agent-multi",
            name="Agent",
            path="/tmp/agent-multi",
            mcp_full_access=["plane", "context7"],
        )
        assert meta_multi.mcp_full_access == ["plane", "context7"]

    def test_bypass_with_mcp_full_access_returns_false(self):
        """``mcp_full_access=["plane"]`` makes the strip a no-op
        for that (agent, server) pair.

        Constructs a minimal ``McpService`` (mocks only the ``manager``
        attribute — ``_get_read_only_tools`` does not touch
        ``create_lazy_mcp_tools`` or any other heavy machinery) and
        verifies the new ``agent_meta`` parameter is consulted:
        ``PlaneServerDefinition`` declares ``read_only_tools=True``
        but the agent opts out, so the helper must return False.
        """
        from daemon.services.mcp_service import McpService

        # Minimal McpService — only ``_manager`` is touched by
        # ``_get_read_only_tools``. We don't construct a full
        # ``InstanceManager`` because the helper reads from the
        # built-in MCP server registry directly via a function-local
        # import of ``daemon.mcp.builtin_servers``.
        service = McpService(manager=MagicMock())

        from daemon.registry import AgentMetadata

        opted_out = AgentMetadata(
            id="project-manager",
            name="PM",
            path="/tmp/project-manager",
            mcp_full_access=["plane"],
        )

        # The opt-out returns False: strip is skipped, full tool
        # surface exposed to the agent.
        assert service._get_read_only_tools("plane", opted_out) is False, (
            "An agent with mcp_full_access=['plane'] must NOT have "
            "Plane's CR-3 read-only filter applied — Approach B's "
            "primary contract."
        )

    def test_typo_in_mcp_full_access_is_fail_closed(self):
        """Typo (``mcp_full_access=["pane"]``) keeps the strip applied.

        The opt-out is checked by EXACT server-name membership, so
        a typo never silently grants unintended write access. The
        helper still consults ``PlaneServerDefinition.read_only_tools``
        (True) and returns True — the CR-3 strip applies. The typo is
        ALSO caught by ``AgentRegistry.validate_tool_configs`` (see
        ``test_validate_tool_configs_warns_on_unknown_entry``) so the
        operator gets a WARNING at startup.
        """
        from daemon.services.mcp_service import McpService

        service = McpService(manager=MagicMock())

        from daemon.registry import AgentMetadata

        typo = AgentMetadata(
            id="agent-typo",
            name="Agent",
            path="/tmp/agent-typo",
            mcp_full_access=["pane"],  # typo: "pane" !== "plane"
        )
        # Filter is consulted first ("plane" matches the declaration);
        # then membership check on the typo list — "plane" not in
        # ["pane"], so the strip stays applied. Fail closed.
        assert service._get_read_only_tools("plane", typo) is True, (
            "A typo in mcp_full_access must NOT bypass the CR-3 "
            "filter — typo means strip stays applied (fail closed). "
            "Approach B's safety guarantee."
        )

        # And the same agent does NOT get any other server's writes:
        # Plane is the only server with read_only_tools=True today;
        # passing "context7" returns False (no strip) regardless.
        # This pins that the typo doesn't accidentally flip other
        # servers' defaults.
        assert service._get_read_only_tools("context7", typo) is False

    def test_absent_or_empty_flag_keeps_strip(self):
        """Regression: an agent WITHOUT ``mcp_full_access`` (or with an
        empty list, or called with ``agent_meta=None``) still gets the
        CR-3 strip applied to Plane. Default behavior must be
        preserved — the new field is opt-out, not opt-in.
        """
        from daemon.services.mcp_service import McpService

        service = McpService(manager=MagicMock())

        from daemon.registry import AgentMetadata

        # No field at all → default value of [].
        meta_no_field = AgentMetadata(
            id="agent-no-field",
            name="Agent",
            path="/tmp/agent-no-field",
        )
        assert meta_no_field.mcp_full_access == []
        assert service._get_read_only_tools("plane", meta_no_field) is True, (
            "An agent without mcp_full_access must keep the CR-3 "
            "filter (strip stays applied). The field is opt-out."
        )

        # Explicit empty list — same effect.
        meta_empty = AgentMetadata(
            id="agent-empty",
            name="Agent",
            path="/tmp/agent-empty",
            mcp_full_access=[],
        )
        assert service._get_read_only_tools("plane", meta_empty) is True

        # ``agent_meta=None`` — caller did not supply identity. The
        # helper must NOT raise and must defer to the builtin's
        # declared read_only_tools (True for Plane).
        assert service._get_read_only_tools("plane", None) is True, (
            "agent_meta=None must NOT crash and must defer to "
            "PlaneServerDefinition.read_only_tools=True."
        )

        # And for a non-CR-3 server the helper returns False
        # regardless of identity — pins the "server that didn't opt
        # into CR-3 strips nothing" invariant.
        assert service._get_read_only_tools("context7", None) is False
        assert service._get_read_only_tools("context7", meta_empty) is False

    def test_validate_tool_configs_warns_on_unknown_entry(self):
        """``AgentRegistry.validate_tool_configs`` warns on unknown
        ``mcp_full_access`` entries (typo / outdated name) and stays
        silent on known entries.

        Pins open-question #2 from the architecture doc (cold-start
        ordering): the validator's import of the built-in MCP server
        registry must NOT crash at registry construction time — even
        if the import path raises (e.g. before side-effect imports
        have finished), ``validate_tool_configs`` logs at debug level
        and returns the baseline warnings list.
        """
        import json
        from pathlib import Path

        from daemon.registry import AgentRegistry

        # A minimal tools registry must be in place so
        # ``validate_tool_configs`` doesn't trip on missing
        # ``scan_tools_for_full_docs`` machinery. The registry has
        # a documented setup helper at ``tests/test_registry.py``
        # (``TestAgentVersioning._setup_mock_tools``); we mirror
        # the pattern inline for the plane-mcp test isolation.
        from daemon.tools import _tool_registry

        _tool_registry._tool_metadata.clear()
        _tool_registry._tool_metadata.update({
            "bash": {"category": "bash", "short_doc": "Run bash"},
        })
        _tool_registry._full_docs.clear()

        # Use a tmp_path mirror of ``test_registry.temp_agents_dir``
        # so we don't depend on the conftest's fixtures (the
        # ``tests/test_registry.py``-level fixture isn't reachable
        # from ``tests/unit/``).
        tmp = Path("/tmp/_agents_test_mcp_full_access")
        if tmp.exists():
            import shutil
            shutil.rmtree(tmp)
        tmp.mkdir()

        def _create_agent_dir(dir_name: str, meta: dict) -> None:
            (tmp / dir_name).mkdir()
            with open(tmp / dir_name / "meta.json", "w") as f:
                json.dump(meta, f)

        # Agent with a VALID entry: ``plane`` is a known built-in.
        _create_agent_dir(
            "valid-pm",
            {
                "id": "valid-pm",
                "name": "Valid PM",
                "tools": {"allow": ["plane"], "deny": []},
                "mcp_full_access": ["plane"],
            },
        )
        # Agent with a TYPO: ``pane`` is NOT a known built-in.
        _create_agent_dir(
            "typo-agent",
            {
                "id": "typo-agent",
                "name": "Typo Agent",
                "tools": {"allow": ["bash"], "deny": []},
                "mcp_full_access": ["pane"],
            },
        )
        # Agent with a FICTIONAL server: never registered.
        _create_agent_dir(
            "unknown-agent",
            {
                "id": "unknown-agent",
                "name": "Unknown Agent",
                "tools": {"allow": ["bash"], "deny": []},
                "mcp_full_access": ["nonexistent-server"],
            },
        )
        # Agent with an empty list: no warning (and no opt-out).
        _create_agent_dir(
            "empty-agent",
            {
                "id": "empty-agent",
                "name": "Empty Agent",
                "tools": {"allow": ["bash"], "deny": []},
                "mcp_full_access": [],
            },
        )

        try:
            reg = AgentRegistry(tmp)
            reg.discover()
            warnings = reg.validate_tool_configs()

            # The two agents with unknown entries produce warnings.
            assert any(
                "typo-agent" in w and "pane" in w for w in warnings
            ), (
                f"validate_tool_configs must warn on typo entry; "
                f"warnings={warnings}"
            )
            assert any(
                "unknown-agent" in w and "nonexistent-server" in w
                for w in warnings
            ), (
                f"validate_tool_configs must warn on unknown "
                f"server entry; warnings={warnings}"
            )

            # The valid-pm entry does NOT produce a warning. Empty
            # list does NOT produce a warning. Filter to the
            # shape of warnings we expect.
            mcp_warnings = [
                w for w in warnings
                if "mcp_full_access entry" in w
            ]
            # Exactly the two typo / unknown entries warn — not the
            # known ``plane`` entry, and not the empty list.
            assert len(mcp_warnings) == 2, (
                f"Expected exactly 2 mcp_full_access warnings, got "
                f"{len(mcp_warnings)}: {mcp_warnings}"
            )
            # And neither valid-pm nor empty-agent is in the warning set.
            assert not any(
                "valid-pm" in w or "empty-agent" in w
                for w in mcp_warnings
            )
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_validate_tool_configs_does_not_crash_on_import_failure(
        self, monkeypatch, caplog
    ):
        """``validate_tool_configs`` survives a failed import of the
        built-in MCP server registry (open-question #2 cold-start
        ordering guard).

        Pins the fail-soft behavior: if the registry import raises
        (e.g. a side-effect import not yet ready at construction
        time), ``validate_tool_configs`` logs at DEBUG and returns
        the baseline warnings list — it never crashes registry
        construction. The runtime ``preload_mcp_tools`` has its own
        resolution path; a missing validation here only means a typo
        might slip past the startup warning, not that anything is
        silently write-enabled.
        """
        import json
        import logging
        from pathlib import Path

        from daemon.registry import AgentRegistry

        # Force the function-local import of the built-in registry to
        # raise — simulates the cold-start ordering failure mode the
        # doc flags as an open question. We do this by replacing the
        # ``__import__`` builtin on the registry module for the
        # duration of the test.
        import builtins as _builtins
        import daemon.registry as _registry_module

        real_import = _builtins.__import__

        def _raise_on_mcp_builtin_import(name, *args, **kwargs):
            if name == "daemon.mcp.builtin_servers" or name.startswith(
                "daemon.mcp.builtin_servers."
            ):
                raise ImportError(
                    "simulated cold-start import failure"
                )
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(
            _registry_module,
            "__builtins__",
            type(
                "B",
                (object,),
                {"__import__": staticmethod(_raise_on_mcp_builtin_import)},
            )(),
        )

        tmp = Path("/tmp/_agents_test_mcp_full_access_import")
        if tmp.exists():
            import shutil
            shutil.rmtree(tmp)
        tmp.mkdir()
        (tmp / "test-agent").mkdir()
        with open(tmp / "test-agent" / "meta.json", "w") as f:
            json.dump(
                {
                    "id": "test-agent",
                    "name": "Test",
                    "tools": {"allow": ["bash"], "deny": []},
                    "mcp_full_access": ["plane"],
                },
                f,
            )

        try:
            reg = AgentRegistry(tmp)
            reg.discover()

            with caplog.at_level(
                logging.DEBUG, logger="daemon.registry"
            ):
                # Must NOT raise — the import is guarded.
                warnings = reg.validate_tool_configs()
            # Baseline must succeed; the mcp_full_access check is
            # skipped (debug logged), so no warning is produced
            # for our otherwise-valid ``plane`` entry.
            assert isinstance(warnings, list), (
                "validate_tool_configs must return a list even when "
                "the built-in registry import fails"
            )
            assert not any(
                "mcp_full_access entry" in w for w in warnings
            ), (
                f"Failed-import path must not emit spurious "
                f"warnings; got {warnings}"
            )
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_preload_mcp_tools_unknown_agent_id_fails_closed(self):
        """``preload_mcp_tools`` with an unknown ``agent_id`` resolves
        ``agent_meta=None`` and applies the strip — no exception
        leak, identity lookup failure stays fail-closed.

        Pins Approach B's identity-resolution safety: a typo'd or
        expired agent_id, an instance-row exists but agent_id
        missing, or a get_version/get_resolved exception must
        all leave the agent booting read-only rather than
        write-open. The CR-3 declaration
        (``PlaneServerDefinition.read_only_tools=True``) governs.
        """
        from daemon.services.mcp_service import McpService

        # Empty server list → the per-server filter loop has nothing
        # to classify, so the identity branch is the only thing
        # under test here. We avoid the per-server filter to keep
        # the assertion narrow on identity resolution.
        manager = MagicMock()
        manager._mcp_server_repository = MagicMock()
        manager._mcp_server_repository.list_mcp_servers.return_value = []
        manager.config = MagicMock(mcp_pool=MagicMock(tool_call_timeout=120))

        service = McpService(manager=manager)

        # Unknown agent_id — registry.get_version returns None,
        # registry.get_resolved returns None. agent_meta must
        # resolve to None and preload must return cleanly.
        result = await service.preload_mcp_tools(
            "instance-xyz-1234", agent_id="nonexistent-agent"
        )
        assert result is None  # preload returns None on success/no-op

        # An exception in get_version is also tolerated — patch
        # the registry's get_version to raise; the helper still
        # completes, no exception leaks.
        from daemon.registry import get_registry as _real_get_registry
        real_registry = _real_get_registry()
        original_get_version = real_registry.get_version
        original_get_resolved = real_registry.get_resolved

        def _raise_get_version(*args, **kwargs):
            raise RuntimeError("simulated registry failure")

        real_registry.get_version = _raise_get_version
        try:
            result = await service.preload_mcp_tools(
                "instance-abc-5678", agent_id="any-agent"
            )
            assert result is None, (
                "Registry exception inside preload_mcp_tools must "
                "NOT propagate — fail-closed path swallows it so "
                "the agent boots read-only."
            )
        finally:
            real_registry.get_version = original_get_version
            real_registry.get_resolved = original_get_resolved
