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
