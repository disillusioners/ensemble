"""Generic built-in MCP availability-gate + privilege-stamp coverage.

PROVENANCE (2026-10-24) — this file is the port of the GENERIC half of
the deleted ``tests/unit/test_computer_use_mcp.py`` (the cu builtin MCP
server was removed in ``feature/remove-cu-builtin``; the replacement
path is the ``mcps_client`` wrapper + gateway + ``mcp_rpc`` tools).

What SURVIVED the removal (ported here, verbatim in behaviour):

  1. The S1 built-in availability gate in
     ``McpService.preload_mcp_tools`` — a registered builtin's
     ``is_available()`` is consulted BEFORE any
     ``get_schemas_for_server`` call, so a post-boot kill-switch flip
     with the server's row still ACTIVE cannot cold-spawn the child.
     The gate is GENERIC (it resolves any
     ``BuiltinServerDefinition`` from the global registry); the cu
     built-in was merely its first consumer.
  2. The privilege-stamp helper
     (``_privilege_stamp_category_for_prefix``) and the lazy-tool
     factory's non-stamping contract for the standard ``mcp_{slug}_``
     prefix and for NON-privileged prefix overrides (``plane_``).
  3. ``is_mcp_tool`` still recognises the standard ``mcp_{slug}_``
     shape.
  4. The empty-allow agent leak contract: a tool carrying a
     ``_tool_category`` that is in ``PRIVILEGED_TOOL_CATEGORIES`` is
     stripped from the default-allow universe, while a tool of a
     non-privileged category survives.

What DIED with the cu builtin (NOT ported — the subject no longer
exists): the ``COMPUTER_USE_MCP_ENABLED`` / ``MCP_DISABLE_BUILT_IN_
COMPUTER_USE`` kill-switch matrix, the ``CU_NPX_ARGS`` package pin,
the ``cu_`` prefix override, the cu privilege stamp, the cu bootstrap /
drift-refresh / npx-unavailable flows, and the cu-specific D7
``/mcp-servers/test-connection`` guard (removed from
``daemon/routers/mcp_servers.py`` in the same PR).

POST-REMOVAL TRUTH the pins below reflect: ``DYNAMIC_TOOL_PREFIXES``
is ``{"plane_"}`` and ``"plane"`` is NOT in
``PRIVILEGED_TOOL_CATEGORIES`` — so NO dynamic prefix stamps a
category any more. The "no stamp" assertions are therefore the
correct current-truth pins, not leftovers.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Unmock daemon.mcp.tool_adapter so we import the REAL module. The root
# conftest.py replaces it with a stub that lacks ``create_lazy_mcp_tools``
# / ``_build_lazy_coroutine`` / ``is_mcp_tool`` (it stubs them as
# MagicMock). Same ritual as the deleted cu suite.
# ---------------------------------------------------------------------------
_mock_tool_adapter = sys.modules.pop("daemon.mcp.tool_adapter", None)
from daemon.mcp.tool_adapter import (  # noqa: E402
    _privilege_stamp_category_for_prefix,
    create_lazy_mcp_tools,
    is_mcp_tool,
)
if _mock_tool_adapter is not None:
    sys.modules["daemon.mcp.tool_adapter"] = _mock_tool_adapter

from daemon.mcp.models import McpToolSchema  # noqa: E402


# ---------------------------------------------------------------------------
# THE CONSTANT-PIN TAUTOLOGY RULE
#
# Relocated here (2026-10-24) from the deleted
# ``tests/unit/test_computer_use_mcp.py`` header — that file was the
# rule's only home in the tree, and the rule must not be lost with it.
#
# WHEN A TEST PINS A PRODUCTION CONSTANT (a package pin, a version, a
# tool-name prefix, a category name), THE PIN FIXTURE MUST REFERENCE
# THE PRODUCTION CONSTANT — imported from the production module, and
# optionally re-exported under a test-local name so the "swap = ONE
# FILE EDIT + this constant" intent is explicit. A hand-copied literal
# degenerates the pin into a tautology: the test asserts that a copy
# of the constant equals itself and can never fail when the production
# value changes.
#
# THE ONE EXCEPTION: OFF-CANONICAL pins used in drift / negative-test
# fixtures are INTENTIONALLY hardcoded — they exist to assert that the
# gate REJECTS a mismatch, so binding them to the canonical constant
# would defeat the test.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema_dict(
    name: str = "get_docs",
    description: str = "Fetch the docs",
    input_schema: dict | None = None,
) -> dict:
    """Schema shape ``create_lazy_mcp_tools`` expects."""
    return {
        "name": name,
        "description": description,
        "input_schema": input_schema or {"type": "object", "properties": {}},
    }


class _FakeSessionProvider:
    """Minimal ``McpSessionProvider`` — resolves to a sentinel MagicMock.

    Used by lazy-tool tests to construct ``StructuredTool`` instances
    without actually opening an MCP session. The sentinel is enough to
    let the tools be created; only NAME + DESCRIPTION + CATEGORY
    stamping matter for these tests.
    """

    async def get_session(self, server_name: str):
        return MagicMock()


# ---------------------------------------------------------------------------
# Class 1 — Privilege-stamp: the standard prefix and non-privileged
#           prefix overrides get NO stamp (zero-regression contract).
# ---------------------------------------------------------------------------


class TestPrivilegeStamp:
    """``_tool_category`` stamping makes the privilege strip work.

    The stamp is the conjunction of TWO registry facts: the bare prefix
    must be a member of ``DYNAMIC_TOOL_PREFIXES`` AND the resulting
    category must be a member of ``PRIVILEGED_TOOL_CATEGORIES``.
    Post-cu-removal ``DYNAMIC_TOOL_PREFIXES == {"plane_"}`` and
    ``"plane"`` is NOT privileged — so the only correct current truth is
    that NOTHING dynamic stamps a category any more. These pins hold
    that truth.
    """

    def test_helper_none_for_no_prefix(self):
        """No override → no stamp."""
        assert _privilege_stamp_category_for_prefix(None) is None

    def test_helper_none_for_plane_prefix(self):
        """``"plane"`` is intentionally NOT privileged → no stamp.

        This is the zero-regression test: plane behavior is unchanged.
        """
        assert _privilege_stamp_category_for_prefix("plane") is None

    def test_helper_none_for_untracked_prefix(self):
        """Random prefix that's not in DYNAMIC_TOOL_PREFIXES → no stamp.

        Future-prefix protection: a new prefix override (e.g.
        ``some-future-srv_``) is NOT auto-privileged — it must be
        both registered in DYNAMIC_TOOL_PREFIXES AND in
        PRIVILEGED_TOOL_CATEGORIES to acquire the stamp.
        """
        assert _privilege_stamp_category_for_prefix("not_registered") is None
        assert _privilege_stamp_category_for_prefix("future") is None

    def test_lazy_factory_does_not_stamp_plane_tools(self):
        """``create_lazy_mcp_tools(tool_name_prefix='plane')`` → no stamp.

        The zero-regression case: this is the contract that lets
        every existing plane test keep passing unchanged.
        """
        schemas = [_schema_dict(name="list_issues")]
        tools = create_lazy_mcp_tools(
            server_name="plane",
            schemas=schemas,
            session_provider=_FakeSessionProvider(),
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
            tool_name_prefix="plane",
        )
        assert len(tools) == 1
        assert getattr(tools[0], "_tool_category", None) is None

    def test_lazy_factory_does_not_stamp_standard_prefix(self):
        """``mcp_{slug}_`` (no override) → no stamp — context7 etc. unchanged."""
        schemas = [_schema_dict(name="get_docs")]
        tools = create_lazy_mcp_tools(
            server_name="context7",
            schemas=schemas,
            session_provider=_FakeSessionProvider(),
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
        )
        assert len(tools) == 1
        assert getattr(tools[0], "_tool_category", None) is None


# ---------------------------------------------------------------------------
# Class 2 — Standard MCP tool-name detection
# ---------------------------------------------------------------------------


class TestStandardMcpToolDetection:
    """``is_mcp_tool`` recognises the standard ``mcp_{slug}_`` shape.

    Prefix-overridden surfaces (e.g. ``plane_``) are deliberately NOT
    ``mcp_<server>_<tool>`` names and are filtered OUT of the general
    MCP allow-list — the registry's guard is keyed on the standard
    shape only.
    """

    def test_is_mcp_tool_true_for_standard_mcp(self):
        """Regression guard: ``is_mcp_tool('mcp_ctx7_x')`` → True."""
        assert is_mcp_tool("mcp_ctx7_get_docs") is True
        assert is_mcp_tool("mcp_webfetch_fetch") is True


# ---------------------------------------------------------------------------
# Class 3 — Empty-allow agent LEAK regression (generic, category-driven)
# ---------------------------------------------------------------------------


class TestEmptyAllowAgentLeak:
    """Empty-allow agent (e.g. ``watcher``) MUST NOT inherit a
    PRIVILEGED-category tool.

    The strip consults ``getattr(t, "_tool_category", None) in
    PRIVILEGED_TOOL_CATEGORIES``. A tool that carries no category (or a
    non-privileged one, e.g. the plane lane) survives; a tool stamped
    with a privileged category does not.

    Vehicle: ``mcp_rpc`` (the remote-hands gateway category — a REAL
    privileged category that survives the cu removal). The cu builtin's
    ``cu`` category is gone; the contract it exercised is category-
    driven and therefore unchanged.
    """

    @staticmethod
    def _make_privileged_tool(name: str = "mcp_rpc_command"):
        async def _coro(**kw):
            return "x"

        from langchain_core.tools import StructuredTool

        t = StructuredTool(
            name=name,
            description="[MCP:mcps-gateway] stub",
            args_schema={},
            coroutine=_coro,
            response_format="content_and_artifact",
        )
        # The factory stamp a privileged MCP category would carry.
        t._tool_category = "mcp_rpc"
        return t

    @staticmethod
    def _make_plane_tool(name: str = "plane_list_issues"):
        async def _coro(**kw):
            return "x"

        from langchain_core.tools import StructuredTool

        t = StructuredTool(
            name=name,
            description="[MCP:plane] stub",
            args_schema={},
            coroutine=_coro,
            response_format="content_and_artifact",
        )
        # NOTE: plane stays un-stamped — zero-regression contract.
        return t

    def test_empty_allow_agent_does_not_inherit_privileged_category_tools(self):
        """Empty ``tools.allow`` → strip catches the privileged category.

        ``resolve_tool_filter`` for an agent with empty ``tools.allow``
        returns the privileged-stripped universe; the strip MUST remove
        the ``mcp_rpc`` tool because ``watcher`` does not carry
        ``tools.allow: ["mcp_rpc"]``.
        """
        from daemon.tools.instance import (
            _strip_privileged_category_tools,
        )
        from daemon.tools._tool_registry import PRIVILEGED_TOOL_CATEGORIES

        privileged_tool = self._make_privileged_tool()
        plane_tool = self._make_plane_tool()

        # Sanity: the strip sees the category stamp AND BOTH
        # PRIVILEGED + the strip helper.
        assert getattr(privileged_tool, "_tool_category", None) == "mcp_rpc"
        assert "mcp_rpc" in PRIVILEGED_TOOL_CATEGORIES

        survivors = _strip_privileged_category_tools(
            [privileged_tool, plane_tool]
        )
        names = {t.name for t in survivors}
        assert "mcp_rpc_command" not in names, (
            "a PRIVILEGED-category tool MUST be stripped from the "
            "empty-allow agent universe (the watcher's tool set today)"
        )
        assert "plane_list_issues" in names, (
            "plane_* stays — plane is NOT privileged (zero-regression)"
        )

    def test_explicit_dynamic_category_allow_grants_prefix_tools(self):
        """An agent with ``tools.allow: ["plane"]`` keeps ``plane_*`` tools.

        The dynamic-category expansion loop
        (``daemon/tools/instance.py``) enumerates every
        ``DYNAMIC_TOOL_PREFIXES`` entry into its bare category key, so
        the allow entry resolves to the live prefix-overridden surface.
        This is the same mechanism the removed ``cu`` category used.
        """
        from daemon.tools.instance import resolve_tool_filter

        all_tool_names: set[str] = {
            # plane tools (prefix-overridden — from DYNAMIC_TOOL_PREFIXES)
            "plane_list_issues", "plane_create_issue",
            # standard MCP tools (the incumbent mcp_<server>_<tool> lane)
            "mcp_context7_get_docs", "mcp_webfetch_fetch",
        }

        allowed = resolve_tool_filter(
            allow=["plane"],
            deny=None,
            tool_categories={"mcp": ["mcp_context7_get_docs", "mcp_webfetch_fetch"]},
            all_tool_names=all_tool_names,
        )

        assert allowed is not None, (
            "resolve_tool_filter with allow=['plane'] must return a "
            "concrete set, not None (None would mean 'all tools allowed' "
            "which skips the lane we are pinning here)"
        )
        assert {"plane_list_issues", "plane_create_issue"} <= allowed, (
            f"plane_* tools MUST be in the allowed set when "
            f"allow=['plane']; got {sorted(allowed)}"
        )
        # The mcp_* tools MUST NOT be granted (the dynamic allow entry
        # is category-scoped, not 'all MCP').
        assert "mcp_context7_get_docs" not in allowed, (
            "mcp_* tools must NOT be granted via allow=['plane'] — "
            "the dynamic lane is prefix-scoped, not generic"
        )

    def test_mcp_only_allow_does_not_grant_dynamic_prefix_tools(self):
        """``tools.allow: ["mcp"]`` does NOT cover prefix-overridden sets.

        The carve-out: prefix-overridden tools are exposed as
        ``plane_*`` (not ``mcp_plane_*``), so the standard ``mcp``
        category exclude rule does not match them. A per-agent ``["mcp"]``
        allow-list therefore sees ZERO plane_* tools.

        BEHAVIORAL PIN: ``resolve_tool_filter(allow=["mcp"], ...)`` MUST
        grant the mcp_* tools AND must NOT grant plane_* tools. The mcp
        lane is the incumbent ``mcp_<server>_<tool>`` namespace; a
        future refactor that swept ``DYNAMIC_TOOL_PREFIXES`` into the
        mcp category would silently grant the whole prefix-overridden
        surface via a generic category entry — this test catches that
        regression.
        """
        from daemon.tools.instance import resolve_tool_filter

        all_tool_names: set[str] = {
            # plane tools — MUST NOT appear in the result
            "plane_list_issues", "plane_create_issue",
            # standard MCP tools — MUST appear in the result
            "mcp_context7_get_docs", "mcp_webfetch_fetch",
        }

        allowed = resolve_tool_filter(
            allow=["mcp"],
            deny=None,
            tool_categories={"mcp": ["mcp_context7_get_docs", "mcp_webfetch_fetch"]},
            all_tool_names=all_tool_names,
        )

        assert allowed is not None, (
            "resolve_tool_filter with allow=['mcp'] must return a concrete "
            "set, not None (None would skip the mcp-only lane we're pinning)"
        )
        assert {"mcp_context7_get_docs", "mcp_webfetch_fetch"} <= allowed, (
            f"mcp_* tools MUST be in the allowed set when allow=['mcp']; "
            f"got {sorted(allowed)}"
        )
        for plane_name in ("plane_list_issues", "plane_create_issue"):
            assert plane_name not in allowed, (
                f"{plane_name!r} MUST NOT be granted via allow=['mcp']; "
                "the mcp lane must never sweep DYNAMIC_TOOL_PREFIXES. "
                "If this assertion fires, a refactor silently granted "
                "the whole prefix-overridden surface via a generic "
                "category — revert immediately."
            )


# ---------------------------------------------------------------------------
# Class 4 — S1 regression pin: the built-in availability gate in
#           ``preload_mcp_tools`` (GENERIC — the gate resolves ANY
#           registered ``BuiltinServerDefinition``).
# ---------------------------------------------------------------------------


class TestPreloadBuiltinAvailabilityGate:
    """Pin the S1 load-bearing fix: ``preload_mcp_tools`` consults
    ``BuiltinServerDefinition.is_available()`` per server BEFORE any
    ``get_schemas_for_server`` call, so a post-boot kill-switch flip
    with the server's row still ACTIVE from the prior ON boot cannot
    cold-spawn the child.

    The S1 contract (per the task plan):

      1. **Kill-switch OFF + active row** → preload must NOT call
         ``get_schemas_for_server`` for that server, must NOT add
         the server to the per-instance session cache, and must NOT
         surface that server's tools.
      2. **Kill-switch ON** (control) → preload behavior unchanged.
         The gate is silently bypassed when ``is_available()``
         returns True.
      3. **Always-on builtins (plane / context7 / webfetch)** → gate
         is silently bypassed (their ``is_available()`` returns
         True). Legacy behavior preserved.
      4. **Non-builtin (user-added) servers** → gate is silently
         bypassed (no definition in the registry). Legacy behavior
         preserved.
      5. **One-shot log + per-server error isolation** → the gate
         log is process-once. A bad ``is_available()``
         (transient exception) MUST NOT abort the loop — the loop
         continues with the next server.

    The cu builtin was the first consumer of this gate (its kill-switch
    tests died with the builtin); the SEAM behaviour below is generic
    and survives it.
    """

    @pytest.fixture(autouse=True)
    def _reset_preload_gate_log_flag(self):
        """Reset ``mcp_service._preload_gate_skip_log_emitted``
        before AND after each test.

        The flag is module-level (one per process) and persists
        across test cases inside this class. Without resetting,
        the second-and-later cases would silently skip the log
        assertion.
        """
        from daemon.services import mcp_service

        prev = mcp_service._preload_gate_skip_log_emitted
        mcp_service._preload_gate_skip_log_emitted = False
        try:
            yield
        finally:
            mcp_service._preload_gate_skip_log_emitted = prev

    @staticmethod
    def _make_mock_manager():
        """Minimal ``InstanceManager`` double for preload tests.

        Avoids the heavy ``instance_manager_with_repo`` fixture
        (which mocks fields unrelated to the gate). The preload loop
        only touches:
          * ``_mcp_server_repository.list_mcp_servers(is_active=True)``
          * ``config.mcp_pool.tool_call_timeout``
        Plus the registry lookup (function-local import inside
        ``_is_builtin_server_available``). The MagicMock satisfies
        both without spinning up a real DB.
        """
        mgr = MagicMock()
        mgr._mcp_server_repository = MagicMock()
        mgr.config = MagicMock(mcp_pool=MagicMock(tool_call_timeout=120))
        return mgr

    @pytest.mark.asyncio
    async def test_gate_bypassed_for_non_builtin_server(self):
        """The gate MUST NOT touch non-builtin (user-added) servers.

        A user with a fully custom MCP server row named ``weird``
        that happens to land in ``list_mcp_servers(is_active=True)``
        has no definition in the builtin registry → the gate is
        silently bypassed → the legacy preload path runs as
        before. Pin against the gate accidentally imposing a
        kill-switch opinion on user-added servers.
        """
        from daemon.services.mcp_service import McpService

        user_server = MagicMock()
        user_server.name = "weird-user-mcp"
        user_server.config = {
            "transport": "http", "url": "https://example.com/mcp"
        }
        user_server.is_active = True

        manager = self._make_mock_manager()
        manager._mcp_server_repository.list_mcp_servers.return_value = [
            user_server
        ]

        schemas = [
            McpToolSchema(
                name="weird_tool",
                description="x",
                input_schema={"type": "object", "properties": {}},
                server_name="weird-user-mcp",
            ),
        ]

        service = McpService(manager=manager)
        service.get_schemas_for_server = AsyncMock(return_value=schemas)

        with patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[MagicMock(name="weird_tool")],
        ) as mock_create:
            await service.preload_mcp_tools("inst-1")

        # Non-builtin → gate is a no-op → schemas fetched.
        service.get_schemas_for_server.assert_awaited_once_with(
            user_server
        )
        assert mock_create.called

        # Session cache has the user server.
        session_state = service._session_caches["inst-1"]
        assert "weird-user-mcp" in session_state

    @pytest.mark.asyncio
    async def test_gate_bypassed_for_healthy_builtin(self, monkeypatch, caplog):
        """The gate MUST silently bypass always-on builtins.

        ``PlaneServerDefinition.is_available()`` returns True when
        the plane kill-switch is on + the URL + API key are all
        set. We pin that a healthy-availability definition does NOT
        trigger the gate-skip log (the existing
        ``test_plane_mcp.py`` covers the plane kill-switch
        contract; this test pins the SHARED SEAM behavior).
        """
        from daemon.services.mcp_service import McpService

        # Plane reads its own env inside its own ``is_available()`` —
        # feed it the URL + API key so the gate sees a
        # healthy-availability builtin (mirrors a real prod workspace).
        monkeypatch.setenv("PLANE_MCP_ENABLED", "true")
        monkeypatch.setenv("PLANE_MCP_URL", "https://api.plane.so")
        monkeypatch.setenv("PLANE_MCP_API_KEY", "test-fixture-api-key")

        plane_server = MagicMock()
        plane_server.name = "plane"
        plane_server.config = {"transport": "stdio", "command": "uvx"}
        plane_server.is_active = True

        manager = self._make_mock_manager()
        manager._mcp_server_repository.list_mcp_servers.return_value = [
            plane_server
        ]

        plane_schemas = [
            McpToolSchema(
                name="list_projects",
                description="x",
                input_schema={
                    "type": "object", "properties": {}
                },
                server_name="plane",
            ),
        ]

        service = McpService(manager=manager)
        service.get_schemas_for_server = AsyncMock(
            return_value=plane_schemas
        )

        plane_tool_mock = MagicMock()
        plane_tool_mock.name = "plane_list_projects"

        with caplog.at_level(
            logging.INFO, logger="daemon.services.mcp_service"
        ):
            with patch(
                "daemon.services.mcp_service.create_lazy_mcp_tools",
                return_value=[plane_tool_mock],
            ):
                await service.preload_mcp_tools("inst-1")

        # Gate bypassed.
        service.get_schemas_for_server.assert_awaited_once_with(
            plane_server
        )

        # The gate's one-shot skip log MUST NOT appear for a
        # healthy-availability builtin.
        skip_msgs = [
            r.getMessage() for r in caplog.records
            if r.levelno == logging.INFO
            and "skip" in r.getMessage().lower()
            and "plane" in r.getMessage().lower()
        ]
        assert skip_msgs == [], (
            "the gate-skip log is reserved for unavailable "
            "builtins; a healthy plane (or any always-on "
            "builtin) MUST NOT trigger it: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_gate_exception_isolates_to_one_server(self, caplog):
        """A bad ``is_available()`` MUST NOT abort the loop.

        Per-server error isolation: the gate is wrapped in
        ``try/except`` and any exception inside the helper logs
        a WARNING and skips the server, mirroring the
        schema-lookup failure path right below it in the loop.
        The next server on the iteration MUST still be processed.
        """
        from daemon.services.mcp_service import McpService

        # The failing server is a REGISTERED builtin (``plane``) whose
        # gate helper raises; the surviving server is a non-builtin
        # user-added server (``weird-user-mcp`` has no definition in
        # the registry → the helper returns True for it
        # unconditionally → load succeeds). Using a user-added server
        # keeps the test focused on the helper's exception path
        # without coupling to plane-env mock state.
        plane_server = MagicMock()
        plane_server.name = "plane"
        plane_server.config = {"transport": "stdio", "command": "uvx"}
        plane_server.is_active = True

        user_server = MagicMock()
        user_server.name = "weird-user-mcp"
        user_server.config = {
            "transport": "http", "url": "https://example.com/mcp"
        }
        user_server.is_active = True

        manager = self._make_mock_manager()
        manager._mcp_server_repository.list_mcp_servers.return_value = [
            plane_server, user_server,
        ]

        user_schemas = [
            McpToolSchema(
                name="weird_tool",
                description="x",
                input_schema={
                    "type": "object", "properties": {}
                },
                server_name="weird-user-mcp",
            ),
        ]

        service = McpService(manager=manager)

        # We deliberately do NOT poison ``get_schemas_for_server`` —
        # the test pins that a helper exception logs a WARNING and
        # skips just the affected server, leaving the next iteration
        # alive.
        async def _schema_lookup(srv):
            return user_schemas if srv.name == "weird-user-mcp" else []

        service.get_schemas_for_server = AsyncMock(side_effect=_schema_lookup)

        # Patch the helper to raise for the registered builtin
        # specifically. The real helper is a method on McpService — we
        # patch it on the instance so the patch is contained to this
        # test.
        def _fake_gate(self, server_name):
            if server_name == "plane":
                raise RuntimeError("simulated transient helper failure")
            return True  # user-added servers always pass

        with patch.object(
            McpService, "_is_builtin_server_available", _fake_gate
        ), caplog.at_level(
            logging.WARNING, logger="daemon.services.mcp_service"
        ), patch(
            "daemon.services.mcp_service.create_lazy_mcp_tools",
            return_value=[MagicMock(name="weird_tool")],
        ):
            await service.preload_mcp_tools("inst-1")

        # Per-server isolation: the user-added server STILL got
        # through despite the builtin helper raising.
        session_state = service._session_caches["inst-1"]
        assert "weird-user-mcp" in session_state, (
            "the failing builtin entry MUST NOT prevent the "
            "user-added entry from being processed — per-server "
            "error isolation is load-bearing"
        )
        assert "plane" not in session_state

        # The helper's exception must surface as a WARNING at
        # the gate site (mirrors the schema-lookup WARNING right
        # below it in the loop).
        warning_msgs = [
            r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING
            and "plane" in r.getMessage()
        ]
        assert warning_msgs, (
            "expected a WARNING log line naming 'plane' "
            "for the helper exception path; got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
