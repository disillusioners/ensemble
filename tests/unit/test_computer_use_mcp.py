"""Unit tests for the Computer Use (cu-mcp) built-in MCP server.

Covers (mirror 1:1 with the cu-mcp task contract, 2026-10-16):

  1. ``ComputerUseServerDefinition.is_available()`` — primary
     ``COMPUTER_USE_MCP_ENABLED`` kill-switch + generic MCP disable +
     npx resolution gate.
  2. Tool name prefix override — ``cu_{tool}`` instead of
     ``mcp_computer_use_{tool}`` (the prefix is the only way
     ``PRIVILEGED_TOOL_CATEGORIES`` + ``_strip_privileged_category_tools``
     catches these tools).
  3. Privilege-stamp — ``create_lazy_mcp_tools`` (and
     ``adapt_mcp_tools``) land ``_tool_category="cu"`` on prefix-
     overridden MCP tools so the privilege strip works. Regression
     guard: ``plane_`` stays un-stamped (plane is intentionally NOT
     a privileged category today).
  4. Bootstrap-skip-unavailable — when kill-switch or npx resolution
     refuses, no DB record is created.
  5. Drift-refresh — schema_version=1 + a stored row at the same
     version → no drift detected. Bumping the version (via a fake
     stored row) + bootstrap → config is regenerated.
  6. npx-unavailable — when npx cannot spawn
     (``_create_session`` raises ``FileNotFoundError``), the error
     is surfaced through ``connect_instance`` (NEVER spawns the real
     binary — patched).
  7. Empty-allow agent leak regression — an agent with no tools config
     does NOT see any ``cu_*`` tools. ``ari`` / ``leader`` with
     ``tools.allow: ["cu"]`` DO see them. An ``"mcp"``-only allow-list
     does NOT see them (because the prefix override means they're
     NOT under the ``mcp`` category). Plane (``plane_``) tool behavior
     is unchanged.
  8. D7 kill-switch bypass fix in ``/mcp-servers/test-connection`` —
     the guard refuses a probe that targets the cu-mcp package while
     the kill-switch is OFF.

The test file mirrors ``test_plane_mcp.py`` for kill-switch and
prefix-override semantics, ``test_builtin_mcp_servers.py`` for the
bootstrap-skip-unavailable flow, ``test_context7_builtin.py`` for the
drift-refresh + npx-unavailable patterns.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Unmock daemon.mcp.tool_adapter so we import the REAL module. The root
# conftest.py replaces it with a stub that lacks ``create_lazy_mcp_tools``
# / ``_build_lazy_coroutine`` / ``is_mcp_tool`` (it stubs them as
# MagicMock). Same ritual as ``test_plane_mcp.py``.
# ---------------------------------------------------------------------------
_mock_tool_adapter = sys.modules.pop("daemon.mcp.tool_adapter", None)
from daemon.mcp.tool_adapter import (
    _privilege_stamp_category_for_prefix,
    adapt_mcp_tools,
    create_lazy_mcp_tools,
    is_mcp_tool,
)  # noqa: E402
if _mock_tool_adapter is not None:
    sys.modules["daemon.mcp.tool_adapter"] = _mock_tool_adapter

from daemon.mcp.builtin_servers.computer_use import (
    CU_DEFAULT_PROFILE,
    CU_NPM_PACKAGE,
    CU_NPX_ARGS,
    CU_SCHEMA_VERSION,
    CU_TOOL_CALL_TIMEOUT_SECONDS,
    CU_TOOL_NAME_PREFIX,
    ComputerUseServerDefinition,
    _reset_one_shot_log_flags_for_tests,
)  # noqa: E402

from daemon.mcp.builtin_servers import (  # noqa: E402
    BuiltinServerRegistry,
    get_registry,
    is_builtin_disabled,
)
from daemon.routers.mcp_servers import (  # noqa: E402
    _block_test_connection_when_builtin_unavailable,
)


# ---------------------------------------------------------------------------
# Single-source test-side constant for the canonical (GOOD) package pin.
#
# The cu-mcp requirement (adjustment 1, 2026-10-16) targets a package
# SWAP (zavora → domdomegg, or any future swap) being a single-file edit:
# ``daemon/mcp/builtin_servers/computer_use.py`` is the production side;
# this constant is the test side. ``CU_NPX_ARGS`` is imported from the
# production module above; we re-export it under a test-local name to
# make the "swap = ONE FILE EDIT + this constant" intent explicit.
#
# CANONICAL-PIN test fixtures MUST reference ``_CU_MCP_TEST_NPX_ARGS``
# (or ``CU_NPM_PACKAGE`` for single-string checks). OFF-CANONICAL pins
# used in drift / D7 negative-test fixtures are intentionally
# hardcoded — they exist to assert the gate rejects mismatches, so
# binding them to the canonical constant would defeat the test.
# ---------------------------------------------------------------------------
_CU_MCP_TEST_NPX_ARGS: tuple[str, ...] = CU_NPX_ARGS
_CU_MCP_TEST_NPM_PACKAGE: str = CU_NPM_PACKAGE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema_dict(
    name: str = "screenshot",
    description: str = "Capture the screen",
    input_schema: dict | None = None,
) -> dict:
    """Schema shape ``create_lazy_mcp_tools`` / ``adapt_mcp_tools`` expect."""
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


@pytest.fixture(autouse=True)
def _reset_log_flags():
    """Reset module-level one-shot log flags between tests.

    ``ComputerUseServerDefinition.is_available()`` flips
    ``_kill_switch_log_emitted`` / ``_unresolvable_npx_log_emitted``
    once per process to keep boot logs to one line; without this
    fixture, later tests would silently skip the log assertion because
    the flag was already set by an earlier case.
    """
    _reset_one_shot_log_flags_for_tests()
    yield
    _reset_one_shot_log_flags_for_tests()


# ---------------------------------------------------------------------------
# Class 1 — Kill-switch semantics (mirror TestPlaneMcpKillSwitch 1:1)
# ---------------------------------------------------------------------------


class TestComputerUseMcpKillSwitch:
    """``COMPUTER_USE_MCP_ENABLED`` — primary kill-switch.

    Default ON (unset/empty). Falsy values ``0`` / ``false`` / ``no`` /
    ``off`` (case-insensitive, whitespace-stripped) disable. The
    switch is INDEPENDENT of npx resolution AND of the generic
    ``MCP_DISABLE_BUILT_IN_COMPUTER_USE`` (consulted in
    ``is_builtin_disabled``); we test both gates below.
    """

    def test_switch_off_disables_even_with_npx_resolvable(self, monkeypatch):
        """Kill-switch beats npx resolution — the gate is upstream."""
        monkeypatch.setenv("COMPUTER_USE_MCP_ENABLED", "false")
        monkeypatch.delenv("MCP_DISABLE_BUILT_IN_COMPUTER_USE", raising=False)
        # We don't reset npx — even with a resolvable npx, the
        # kill-switch alone is enough to refuse registration.
        assert ComputerUseServerDefinition.is_available() is False

    @pytest.mark.parametrize("value", ["0", "False", "no", "off"])
    def test_switch_falsy_values_disable(self, monkeypatch, value):
        monkeypatch.setenv("COMPUTER_USE_MCP_ENABLED", value)
        monkeypatch.delenv("MCP_DISABLE_BUILT_IN_COMPUTER_USE", raising=False)
        assert ComputerUseServerDefinition.is_available() is False

    def test_switch_explicit_true_stays_available(self, monkeypatch):
        """``COMPUTER_USE_MCP_ENABLED=true`` + resolvable npx → ON.

        Force npx to a known-good path via ``CU_NPX_PATH`` so the
        resolution gate also passes on every host.
        """
        monkeypatch.setenv("COMPUTER_USE_MCP_ENABLED", "true")
        monkeypatch.setenv("CU_NPX_PATH", "/usr/bin/env")
        monkeypatch.delenv("MCP_DISABLE_BUILT_IN_COMPUTER_USE", raising=False)
        assert ComputerUseServerDefinition.is_available() is True

    def test_switch_on_still_refuses_unresolvable_npx(self, monkeypatch):
        """Switch ON + no npx → still unavailable (both gates stack).

        With ``CU_NPX_PATH`` pointing at a non-existent file AND a PATH
        stripped of npx (real test environments without a global npx),
        the 4-gate AND returns False. ``PATH=/dev/null`` keeps
        ``shutil.which`` honest.
        """
        monkeypatch.delenv("COMPUTER_USE_MCP_ENABLED", raising=False)
        monkeypatch.setenv("CU_NPX_PATH", "/nonexistent/npx")
        monkeypatch.setenv("PATH", "/dev/null")
        monkeypatch.delenv("MCP_DISABLE_BUILT_IN_COMPUTER_USE", raising=False)
        assert ComputerUseServerDefinition.is_available() is False

    def test_switch_off_logs_once(self, monkeypatch, caplog):
        """OFF → ONE precise log line per process.

        Mirrors plane.py: the bootstrap layer (which is the only
        caller at boot) consults ``is_available()`` once per builtin.
        The lazy one-shot flag keeps it to ONE process-wide log.
        """
        monkeypatch.setenv("COMPUTER_USE_MCP_ENABLED", "false")
        monkeypatch.delenv("MCP_DISABLE_BUILT_IN_COMPUTER_USE", raising=False)
        _reset_one_shot_log_flags_for_tests()

        with caplog.at_level(
            logging.INFO, logger="daemon.mcp.builtin_servers.computer_use"
        ):
            assert ComputerUseServerDefinition.is_available() is False
            # Second call — flag should suppress the log again.
            assert ComputerUseServerDefinition.is_available() is False

        switch_lines = [
            r
            for r in caplog.records
            if "COMPUTER_USE_MCP_ENABLED" in r.getMessage()
        ]
        assert len(switch_lines) == 1, (
            f"expected exactly ONE log line, got {len(switch_lines)}: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_npx_unresolvable_logs_once(self, monkeypatch, caplog):
        """Unresolvable npx → ONE precise log line per process.

        Distinct from the kill-switch-off log: this log carries the
        remediation hint (``set CU_NPX_PATH to an absolute npx path
        or install Node.js >= 20``) so operators can act without
        grepping stack traces.
        """
        monkeypatch.delenv("COMPUTER_USE_MCP_ENABLED", raising=False)
        monkeypatch.setenv("CU_NPX_PATH", "/nonexistent/npx")
        monkeypatch.setenv("PATH", "/dev/null")
        monkeypatch.delenv("MCP_DISABLE_BUILT_IN_COMPUTER_USE", raising=False)
        _reset_one_shot_log_flags_for_tests()

        with caplog.at_level(
            logging.INFO, logger="daemon.mcp.builtin_servers.computer_use"
        ):
            assert ComputerUseServerDefinition.is_available() is False
            assert ComputerUseServerDefinition.is_available() is False

        hint_lines = [
            r for r in caplog.records if "CU_NPX_PATH" in r.getMessage()
        ]
        assert len(hint_lines) == 1, (
            f"expected exactly ONE npx-missing log, got {len(hint_lines)}"
        )


# ---------------------------------------------------------------------------
# Class 2 — Generic MCP framework disable ("MCP_DISABLE_BUILT_IN_*")
# ---------------------------------------------------------------------------


class TestGenericMcpDisable:
    """``MCP_DISABLE_BUILT_IN_COMPUTER_USE`` — framework-level disable.

    The framework's ``is_builtin_disabled(name)`` helper is consulted
    inside ``is_available()``. Setting it to "true" (case-insensitive)
    is enough to refuse registration regardless of the
    ``COMPUTER_USE_MCP_ENABLED`` switch and npx state.
    """

    def test_generic_disable_overrides_kill_switch(self, monkeypatch):
        monkeypatch.delenv("COMPUTER_USE_MCP_ENABLED", raising=False)
        monkeypatch.setenv("MCP_DISABLE_BUILT_IN_COMPUTER_USE", "true")
        monkeypatch.setenv("CU_NPX_PATH", "/usr/bin/env")
        assert ComputerUseServerDefinition.is_available() is False

    def test_generic_disable_helper_capitalizes(self, monkeypatch):
        """The helper replaces ``-`` with ``_`` and uppercases; verify
        ``computer-use`` → ``COMPUTER_USE``."""
        monkeypatch.setenv("MCP_DISABLE_BUILT_IN_COMPUTER_USE", "true")
        assert is_builtin_disabled("computer-use") is True
        monkeypatch.delenv("MCP_DISABLE_BUILT_IN_COMPUTER_USE", raising=False)
        assert is_builtin_disabled("computer-use") is False


# ---------------------------------------------------------------------------
# Class 3 — Prefix override → cu_{tool} (mirror TestPlanePrefixOverride)
# ---------------------------------------------------------------------------


class TestComputerUsePrefixOverride:
    """Tool name prefix override: ``cu_`` instead of ``mcp_computer_use_``."""

    def test_tools_get_cu_prefix(self):
        """``tool_name_prefix='cu'`` → ``cu_click``, NOT ``mcp_computer_use_...``."""
        schemas = [_schema_dict(name="click", description="click at coord")]
        tools = create_lazy_mcp_tools(
            server_name="computer-use",
            schemas=schemas,
            session_provider=_FakeSessionProvider(),
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
            tool_name_prefix="cu",
        )
        assert len(tools) == 1
        assert tools[0].name == "cu_click"
        assert not tools[0].name.startswith("mcp_computer_use_")
        assert not tools[0].name.startswith("mcp_plane_")

    def test_default_prefix_unchanged(self):
        """Regression guard: no prefix param → standard ``mcp_{slug}_{tool}``."""
        schemas = [_schema_dict(name="get_docs")]
        tools = create_lazy_mcp_tools(
            server_name="cu-mcp",
            schemas=schemas,
            session_provider=_FakeSessionProvider(),
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
        )
        assert len(tools) == 1
        assert tools[0].name == "mcp_cu_mcp_get_docs"

    def test_is_mcp_tool_false_for_cu_tools(self):
        """``is_mcp_tool('cu_screenshot')`` → False; cu tools bypass MCP detection.

        Same carve-out as plane tools: the prefix override renames the
        AGENT surface (the LLM-visible name) but DOES NOT change the
        MCP dispatch contract. ``is_mcp_tool`` is the registry's
        guard for standard ``mcp_{slug}_`` names; cu (and plane)
        should be filtered OUT so they don't appear in the agent's
        general MCP allow-list.
        """
        assert is_mcp_tool("cu_screenshot") is False
        assert is_mcp_tool("cu_doctor") is False
        assert is_mcp_tool("cu_left_click") is False

    def test_is_mcp_tool_true_for_standard_mcp(self):
        """Regression guard: ``is_mcp_tool('mcp_ctx7_x')`` → True."""
        assert is_mcp_tool("mcp_ctx7_get_docs") is True
        assert is_mcp_tool("mcp_webfetch_fetch") is True


# ---------------------------------------------------------------------------
# Class 4 — Privilege-stamp: cu tools get ``_tool_category="cu"``,
#           plane stays un-stamped (zero-regression).
# ---------------------------------------------------------------------------


class TestPrivilegeStamp:
    """``_tool_category`` stamping makes the privilege strip work.

    Pre-cu-mcp hole: prefix-overridden MCP tools (``plane_``,
    ``cu_``, ...) bypassed ``_strip_privileged_category_tools``
    because the strip checks
    ``getattr(t, "_tool_category", None) in PRIVILEGED_TOOL_CATEGORIES``
    and no StructuredTool EVER carried ``_tool_category``. The stamp
    landed by this code path makes cu_ tools subject to the same
    strip as any first-party privileged category.
    """

    def test_helper_none_for_no_prefix(self):
        """No override → no stamp."""
        assert _privilege_stamp_category_for_prefix(None) is None

    def test_helper_cu_for_cu_prefix(self):
        """``"cu"`` is in PRIVILEGED_TOOL_CATEGORIES → stamp."""
        assert _privilege_stamp_category_for_prefix("cu") == "cu"

    def test_helper_none_for_plane_prefix(self):
        """``"plane"`` is intentionally NOT privileged → no stamp.

        This is the zero-regression test: plane behavior is unchanged
        before and after this commit.
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

    def test_lazy_factory_stamps_cu_tools(self):
        """``create_lazy_mcp_tools(tool_name_prefix='cu')`` stamps every tool.

        Two-tool scenario: verifies the loop stamps each generated
        tool, not just the last one.
        """
        schemas = [
            _schema_dict(name="screenshot", description="shoot"),
            _schema_dict(name="doctor", description="diag"),
        ]
        tools = create_lazy_mcp_tools(
            server_name="computer-use",
            schemas=schemas,
            session_provider=_FakeSessionProvider(),
            shared_session_cache={},
            shared_session_lock=asyncio.Lock(),
            tool_name_prefix="cu",
        )
        assert len(tools) == 2
        for t in tools:
            assert getattr(t, "_tool_category", None) == "cu", (
                f"{t.name}: expected _tool_category='cu', "
                f"got {getattr(t, '_tool_category', None)!r}"
            )

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

    def test_sync_adapt_stamps_cu_tools(self):
        """The synchronous ``adapt_mcp_tools`` (warmup-pool path)
        stamps cu tools identically — guards against drift between
        the two factory entry points."""
        from langchain_core.tools import StructuredTool

        async def _coro(**kw):
            return "ok"

        src_tool = StructuredTool(
            name="screenshot",
            description="shoot",
            args_schema={},
            coroutine=_coro,
            response_format="content_and_artifact",
        )
        out = adapt_mcp_tools(
            server_name="computer-use",
            tools=[src_tool],
            tool_name_prefix="cu",
        )
        assert out[0].name == "cu_screenshot"
        assert getattr(out[0], "_tool_category", None) == "cu"


# ---------------------------------------------------------------------------
# Class 5 — Bootstrap + warmup-skip-when-unavailable
#           (mirror test_builtin_mcp_servers.py:2089+)
# ---------------------------------------------------------------------------


class TestBootstrapSkipsUnavailable:
    """Bootstrap layer must skip DB record creation when unavailable.

    Mirrors the existing ``test_builtin_mcp_servers.py``
    TestBootstrapSkipsUnavailable pattern — we use the real
    builtin (cu-mcp) and exercise the standard bootstrap path with
    a surgical mock of ``InstanceManager`` (the full
    ``instance_manager_with_repo`` fixture requires a service-tool
    mock that isn't relevant to the cu-mcp gate contract).

    The assertion that matters in production is: when
    ``ComputerUseServerDefinition.is_available()`` is False, the
    bootstrap's
    ``self._mcp_server_repository.get_mcp_server_by_name(name)`` is
    NOT consulted for row creation. We exercise that path by
    inspecting the bootstrap orchestration directly.
    """

    @pytest.fixture
    def fake_manager_for_bootstrap(self, monkeypatch):
        """Minimal InstanceManager double for cu-mcp bootstrap tests.

        Avoids the heavy ``instance_manager_with_repo`` fixture
        (which mocks a service-tool field unrelated to cu-mcp). We
        only need the three attributes the bootstrap method touches
        (``_mcp_server_repository``, ``_deactivate_orphaned_builtin_servers``)
        plus the registry.
        """
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        fake = SimpleNamespace()
        fake._mcp_server_repository = MagicMock()
        fake._mcp_server_repository.get_mcp_server_by_name.return_value = None
        fake._mcp_server_repository.create_mcp_server = MagicMock()
        fake._deactivate_orphaned_builtin_servers = MagicMock()
        # Bootstrap fetches the registry via ``get_mcp_registry()`` —
        # we monkeypatch the source function to return the one with
        # only cu-mcp registered.
        return fake

    def test_bootstrap_skips_cu_mcp_when_kill_switch_off(
        self, monkeypatch, caplog
    ):
        """Kill-switch OFF → no DB record, single INFO log line.

        Mirrors TestBootstrapSkipsUnavailable.test_bootstrap_skips_unavailable_builtin
        — exercises the SAME bootstrap code path via binding; the
        difference is that our ``is_available()`` returns False
        because of a kill-switch, not a missing Python package.
        """
        import logging
        from unittest.mock import MagicMock

        monkeypatch.setenv("COMPUTER_USE_MCP_ENABLED", "false")
        monkeypatch.delenv("MCP_DISABLE_BUILT_IN_COMPUTER_USE", raising=False)
        _reset_one_shot_log_flags_for_tests()

        from daemon.mcp.builtin_servers import _registry
        from daemon.manager import InstanceManager

        # Save and restore the registry contents so other tests
        # see the full default registry.
        saved = dict(_registry._definitions)
        _registry._definitions.clear()
        _registry.register(ComputerUseServerDefinition())
        try:
            fake = MagicMock()  # no spec — bootstrap only touches 3 attrs
            fake._mcp_server_repository.get_mcp_server_by_name.return_value = None
            fake._mcp_server_repository.create_mcp_server = MagicMock()
            fake._deactivate_orphaned_builtin_servers = MagicMock()

            # Patch get_mcp_registry to return the narrowed registry.
            with patch(
                "daemon.manager.get_mcp_registry",
                return_value=_registry,
            ):
                with caplog.at_level(
                    logging.INFO, logger="daemon.manager"
                ):
                    InstanceManager._bootstrap_builtin_servers(fake)

            # The kill-switch path NEVER calls create_mcp_server.
            fake._mcp_server_repository.create_mcp_server.assert_not_called()
            # And there's at least one INFO log line mentioning the skip.
            skip_msgs = [
                r.getMessage()
                for r in caplog.records
                if r.levelno == logging.INFO
                and "computer-use" in r.getMessage().lower()
            ]
            assert len(skip_msgs) >= 1, (
                "expected an INFO log line mentioning computer-use being "
                f"skipped; got: {[r.getMessage() for r in caplog.records]}"
            )
        finally:
            _registry._definitions.clear()
            _registry._definitions.update(saved)


# ---------------------------------------------------------------------------
# Class 6 — Drift-refresh (mirror test_context7_builtin.py:388-430)
# ---------------------------------------------------------------------------


class TestCuMcpDriftRefresh:
    """``schema_version=1`` + a stored row at any different version
    → bootstrap refreshes config from defaults.

    Uses a minimal in-memory SQLite repo (matching the existing
    ``bootstrap_engine`` fixture's setup but with our own scope) so
    we exercise the actual drift seam without the heavy
    ``instance_manager_with_repo`` fixture (which requires the
    service-tool mock irrelevant to cu-mcp's drift contract).
    """

    @pytest.fixture
    def cu_drift_repo(self):
        """In-memory SQLite repo for drift tests; auto-cleanup on teardown."""
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel, create_engine

        from daemon.repositories.mcp_server import SQLModelMcpServerRepository

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        yield SQLModelMcpServerRepository(engine)
        engine.dispose()

    @pytest.fixture
    def fake_manager_for_drift(self, cu_drift_repo):
        """Minimal manager with a real repo + invocation recorder."""
        from unittest.mock import MagicMock

        fake = MagicMock()  # no spec — bootstrap only touches attrs we set
        fake._mcp_server_repository = cu_drift_repo
        fake._deactivate_orphaned_builtin_servers = MagicMock()
        return fake

    def test_schema_drift_refreshes_config(
        self, fake_manager_for_drift, monkeypatch
    ):
        monkeypatch.delenv("COMPUTER_USE_MCP_ENABLED", raising=False)
        monkeypatch.delenv("MCP_DISABLE_BUILT_IN_COMPUTER_USE", raising=False)
        monkeypatch.setenv("CU_NPX_PATH", "/usr/bin/env")
        _reset_one_shot_log_flags_for_tests()

        from daemon.manager import InstanceManager

        repo = fake_manager_for_drift._mcp_server_repository

        # Stage a stored row with a STALE package pin (simulates a
        # pre-cu-mcp-config schema_version="0" row that the cu-mcp
        # transition's bump 0 → 1 must heal).
        old_config = {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@zavora-ai/computer-use-mcp@0.0.0"],
        }
        repo.create_mcp_server(
            name="computer-use",
            description="Computer Use MCP server (stale row)",
            config=old_config,
            is_builtin=True,
            config_schema=[],
            config_schema_version="0",
        )

        stored = repo.get_mcp_server_by_name("computer-use")
        assert stored is not None
        assert stored.config.get("args") == [
            "-y",
            "@zavora-ai/computer-use-mcp@0.0.0",
        ]
        assert stored.config_schema_version == "0"

        from daemon.mcp.builtin_servers import _registry

        # Narrow registry to only cu-mcp for the bootstrap run.
        saved = dict(_registry._definitions)
        _registry._definitions.clear()
        _registry.register(ComputerUseServerDefinition())
        try:
            with patch(
                "daemon.manager.get_mcp_registry",
                return_value=_registry,
            ):
                InstanceManager._bootstrap_builtin_servers(fake_manager_for_drift)
        finally:
            _registry._definitions.clear()
            _registry._definitions.update(saved)

        refreshed = repo.get_mcp_server_by_name("computer-use")
        # Pin must be the canonical 7.6.1, NOT the stale 0.0.0.
        assert refreshed.config.get("args") == ["-y", CU_NPM_PACKAGE], (
            f"expected {CU_NPM_PACKAGE}, got {refreshed.config.get('args')}"
        )
        # Schema version must be bumped to the canonical "1".
        assert refreshed.config_schema_version == CU_SCHEMA_VERSION, (
            f"expected schema version {CU_SCHEMA_VERSION}, "
            f"got {refreshed.config_schema_version}"
        )


# ---------------------------------------------------------------------------
# Class 7 — npx unavailable (mirror test_context7_builtin.py:438-549)
#           — patches _create_session side_effect=FileNotFoundError, NEVER
#           spawns the real binary.
# ---------------------------------------------------------------------------


class TestCuMcpNpxUnavailable:
    """``_create_session`` raising ``FileNotFoundError`` is logged.

    Mirrors ``TestContext7NpxUnavailable``: we patch
    ``McpConnectionManager._create_session`` so the test NEVER spawns
    ``@zavora-ai/computer-use-mcp`` for real — the side_effect raises
    the error path would otherwise surface to the daemon on a host
    with no Node.js installed.
    """

    @pytest.mark.asyncio
    async def test_file_not_found_logged_when_npx_missing(self, caplog, monkeypatch):
        """``FileNotFoundError`` from ``_create_session`` is logged.

        Set up a config dict in the expected shape, hand the daemon a
        fake McpServer row, and patch ``_create_session`` to raise
        ``FileNotFoundError`` as if npx were unresolvable. The
        ``connect_instance`` path catches the error and logs it for
        diagnostics — the test asserts the log line carries the
        right hint.
        """
        import logging

        monkeypatch.setenv("CU_NPX_PATH", "/usr/bin/env")  # pretend npx exists
        _reset_one_shot_log_flags_for_tests()

        from daemon.mcp.connection_manager import McpConnectionManager

        cu_def = ComputerUseServerDefinition()
        config = cu_def.get_base_config()

        mock_server = MagicMock()
        mock_server.config = config
        mock_server.name = "computer-use"

        manager = McpConnectionManager()
        manager._active_sessions = {}

        with patch.object(manager, "_create_session") as mock_create:
            mock_create.side_effect = FileNotFoundError(
                "[Errno 2] No such file or directory: 'npx'"
            )
            with caplog.at_level(logging.ERROR):
                # connect_instance catches the error and logs it; the
                # loop must NOT raise out (bootstrap path's
                # fault-tolerance invariant).
                await manager.connect_instance("test-instance", [mock_server])

        # The error message must mention the bin name and/or "not found".
        assert any(
            "npx" in record.message.lower()
            or "not found" in record.message.lower()
            for record in caplog.records
            if record.levelname == "ERROR"
        ), (
            f"expected an ERROR log mentioning npx/not-found, "
            f"got: {[r.message for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_os_error_logged(self, caplog, monkeypatch):
        """``OSError`` from ``_create_session`` (subprocess fails) is logged."""
        import logging

        monkeypatch.setenv("CU_NPX_PATH", "/usr/bin/env")
        _reset_one_shot_log_flags_for_tests()

        from daemon.mcp.connection_manager import McpConnectionManager

        cu_def = ComputerUseServerDefinition()
        config = cu_def.get_base_config()

        mock_server = MagicMock()
        mock_server.config = config
        mock_server.name = "computer-use"

        manager = McpConnectionManager()
        manager._active_sessions = {}

        with patch.object(manager, "_create_session") as mock_create:
            mock_create.side_effect = OSError("npx: command not found")
            with caplog.at_level(logging.ERROR):
                await manager.connect_instance("test-instance", [mock_server])

        assert any(
            "npx" in record.message.lower()
            or "command not found" in record.message.lower()
            for record in caplog.records
            if record.levelname == "ERROR"
        )


# ---------------------------------------------------------------------------
# Class 8 — Empty-allow agent LEAK regression (the cu-mcp pre-existing hole).
# ---------------------------------------------------------------------------


class TestEmptyAllowAgentLeak:
    """Empty-allow agent (e.g. ``watcher``) MUST NOT inherit ``cu_*`` tools.

    PRE-CU-MCP HOLE: ``create_lazy_mcp_tools`` did not stamp
    ``_tool_category`` on its output, so ``_strip_privileged_category_tools``
    silently bypassed every prefix-overridden MCP tool. Today, ``cu``
    is in ``PRIVILEGED_TOOL_CATEGORIES`` AND every cu tool carries
    ``_tool_category="cu"`` (TestPrivilegeStamp) — the strip MUST
    catch it.

    We exercise the integration seam via ``resolve_tool_filter``
    (the only call site that matters in production). The MCP tools
    fixture simulates what ``_load_mcp_tools`` /
    ``create_lazy_mcp_tools`` would emit.
    """

    def _make_cu_tool(self, name: str = "cu_screenshot"):
        async def _coro(**kw):
            return "x"

        from langchain_core.tools import StructuredTool

        t = StructuredTool(
            name=name,
            description="[MCP:computer-use] stub",
            args_schema={},
            coroutine=_coro,
            response_format="content_and_artifact",
        )
        # The factory stamp:
        t._tool_category = "cu"
        return t

    def _make_plane_tool(self, name: str = "plane_list_issues"):
        async def _coro(**kw):
            return "x"

        from langchain_core.tools import StructuredTool

        return StructuredTool(
            name=name,
            description="[MCP:plane] stub",
            args_schema={},
            coroutine=_coro,
            response_format="content_and_artifact",
        )
        # NOTE: plane stays un-stamped — zero-regression contract.

    def test_empty_allow_agent_does_not_inherit_cu_tools(self):
        """Empty ``tools.allow`` → strip catches cu tools.

        ``resolve_tool_filter`` for an agent with empty
        ``tools.allow`` returns the privileged-stripped universe;
        the strip MUST remove ``cu_*`` because ``watcher`` does not
        carry ``tools.allow: ["cu"]``.
        """
        # The actual exercise lives in daemon.tools.instance, but the
        # _strip_privileged_category_tools helper is what does the
        # work. Verify directly here.
        from daemon.tools.instance import (
            _strip_privileged_category_tools,
        )
        from daemon.tools._tool_registry import PRIVILEGED_TOOL_CATEGORIES

        cu_tool = self._make_cu_tool()
        plane_tool = self._make_plane_tool()

        # Sanity: the strip sees cu_tool._tool_category == "cu" and
        # BOTH PRIVILEGED + the strip helper.
        assert getattr(cu_tool, "_tool_category", None) == "cu"
        assert "cu" in PRIVILEGED_TOOL_CATEGORIES

        survivors = _strip_privileged_category_tools([cu_tool, plane_tool])
        names = {t.name for t in survivors}
        assert "cu_screenshot" not in names, (
            "cu_* MUST be stripped from empty-allow agent universe "
            "(the watcher's tool set today)"
        )
        assert "plane_list_issues" in names, (
            "plane_* stays — plane is NOT privileged (zero-regression)"
        )

    def test_explicit_cu_allow_grants_cu_tools(self):
        """An agent with ``tools.allow: ["cu"]`` keeps cu_* tools.

        This is the gateway for ari + leader: their meta.json carries
        the explicit entry. The strip still catches OTHER
        privileged categories (``system_upgrade``, ``ens-db``,
        ``view-views``) — but ``cu`` is whitelisted.

        BEHAVIORAL PIN (reviewer should-fix 2026-10-16):
        ``resolve_tool_filter(allow=["cu"], all_tool_names={cu_*,
        plane_*, mcp_*})`` MUST grant the cu_* tools. If the
        dynamic category population regresses — e.g. the
        ``DYNAMIC_TOOL_PREFIXES`` expansion loop stops enumerating
        ``cu_`` into the ``"cu"`` key, or the ``"cu"`` row is
        removed from CATEGORY_MODULES so allow-list resolution
        no longer maps the entry to a category key — this test
        fails. Today nothing would: the loop is in place and
        the row was added by the same review pass. The test
        pins the integration, not just the strip helper.
        """
        from daemon.tools.instance import resolve_tool_filter

        all_tool_names: set[str] = {
            # cu tools (the cu-mcp built-in's prefix-overridden set)
            "cu_click", "cu_screenshot", "cu_doctor", "cu_type", "cu_key",
            # plane tools (prefix-overridden — also from DYNAMIC_TOOL_PREFIXES)
            "plane_list_issues", "plane_create_issue",
            # standard MCP tools (the incumbent mcp_<server>_<tool> lane)
            "mcp_context7_get_docs", "mcp_webfetch_fetch",
        }

        allowed = resolve_tool_filter(
            allow=["cu"],
            deny=None,
            tool_categories={"mcp": ["mcp_context7_get_docs", "mcp_webfetch_fetch"]},
            all_tool_names=all_tool_names,
        )

        assert allowed is not None, (
            "resolve_tool_filter with allow=['cu'] must return a concrete "
            "set, not None (None would mean 'all tools allowed' which "
            "skips the cu-gating we are pinning here)"
        )
        # The cu_* tools MUST be granted.
        assert {"cu_click", "cu_screenshot", "cu_doctor", "cu_type", "cu_key"} <= allowed, (
            f"cu_* tools MUST be in the allowed set when allow=['cu']; "
            f"got {sorted(allowed)}"
        )
        # The mcp_* and plane_* tools MUST NOT be granted (the cu
        # allow-list entry is category-scoped, not 'all MCP').
        assert "mcp_context7_get_docs" not in allowed, (
            "mcp_* tools must NOT be granted via allow=['cu'] — "
            "the cu lane is prefix-scoped, not generic"
        )
        assert "plane_list_issues" not in allowed, (
            "plane_* tools must NOT be granted via allow=['cu'] — "
            "the cu lane is prefix-scoped, not generic"
        )

    def test_mcp_only_allow_does_not_grant_cu_tools(self):
        """``tools.allow: ["mcp"]`` does NOT cover cu_* (prefix-override).

        The carve-out: cu tools are exposed as ``cu_*`` (not
        ``mcp_computer_use_*``), so the standard ``mcp`` category
        exclude rule does not match them. A per-agent ``["mcp"]``
        allow-list therefore sees ZERO cu tools — the explicit
        ``["cu"]`` entry is the ONLY access path. (The cu allow
        entry on ari/leader is what makes their access work.)

        BEHAVIORAL PIN (reviewer should-fix 2026-10-16):
        ``resolve_tool_filter(allow=["mcp"], all_tool_names={cu_*,
        plane_*, mcp_*})`` MUST grant the mcp_* tools AND must
        NOT grant cu_* / plane_*. The mcp lane is the incumbent
        ``mcp_<server>_<tool>`` namespace; a future refactor that
        swept ``DYNAMIC_TOOL_PREFIXES`` into the mcp category
        would silently grant computer control via a generic
        category entry — this test catches that regression.
        """
        from daemon.tools.instance import resolve_tool_filter

        all_tool_names: set[str] = {
            # cu tools — MUST NOT appear in the result
            "cu_click", "cu_screenshot", "cu_doctor", "cu_type", "cu_key",
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
        # The mcp_* tools MUST be granted (the mcp category is what
        # the agent asked for).
        assert {"mcp_context7_get_docs", "mcp_webfetch_fetch"} <= allowed, (
            f"mcp_* tools MUST be in the allowed set when allow=['mcp']; "
            f"got {sorted(allowed)}"
        )
        # The cu_* tools MUST NOT be granted — the mcp category lane
        # is for the standard mcp_<server>_<tool> namespace, not the
        # cu_/plane_ prefix-overridden sets.
        for cu_name in ("cu_click", "cu_screenshot", "cu_doctor", "cu_type", "cu_key"):
            assert cu_name not in allowed, (
                f"{cu_name!r} MUST NOT be granted via allow=['mcp']; "
                "the mcp lane must never sweep DYNAMIC_TOOL_PREFIXES. "
                "If this assertion fires, a refactor silently granted "
                "computer control via a generic category — revert immediately."
            )
        # The plane_* tools MUST NOT be granted for the same reason.
        for plane_name in ("plane_list_issues", "plane_create_issue"):
            assert plane_name not in allowed, (
                f"{plane_name!r} MUST NOT be granted via allow=['mcp']; "
                "the mcp lane is for the mcp_<server>_<tool> shape only"
            )


# ---------------------------------------------------------------------------
# Class 9 — D7 test-connection kill-switch bypass fix
# ---------------------------------------------------------------------------


class TestD7TestConnectionKillSwitchBypass:
    """The ``/mcp-servers/test-connection`` endpoint MUST honor the
    cu-mcp kill-switch when the submitted config targets cu-mcp.

    Pre-D7 hole: the test-connection probe bypassed builtin
    availability entirely. An operator who turned
    ``COMPUTER_USE_MCP_ENABLED=false`` would still see the binary
    surface via this UI probe. The fix is the
    ``_block_test_connection_when_builtin_unavailable`` helper,
    which detects cu-mcp signatures and refuses the probe with a
    clear message when the gate is closed.
    """

    def _good_config(self) -> dict:
        """Config that matches the cu-mcp signature.

        References the test-side single-source constant
        ``_CU_MCP_TEST_NPX_ARGS`` (which itself re-exports the
        canonical ``CU_NPX_ARGS`` from
        ``daemon/mcp/builtin_servers/computer_use.py``) so this
        happy-path fixture needs no hardcoded pin string. Off-
        canonical pins (drift / negative-D7 fixtures below) ARE
        explicitly hardcoded — they assert the gate rejects
        mismatches, so binding them to the canonical constant would
        defeat the test.
        """
        return {
            "transport": "stdio",
            "command": "/Users/.../npx",
            "args": list(_CU_MCP_TEST_NPX_ARGS),
        }

    def test_match_and_kill_off_refuses(self, monkeypatch):
        monkeypatch.setenv("COMPUTER_USE_MCP_ENABLED", "false")
        _reset_one_shot_log_flags_for_tests()
        msg = _block_test_connection_when_builtin_unavailable(self._good_config())
        assert msg is not None
        assert "kill-switch" in msg
        assert "COMPUTER_USE_MCP_ENABLED" in msg

    @pytest.mark.parametrize("value", ["0", "False", "no", "off"])
    def test_match_and_falsy_switch_refuses(self, monkeypatch, value):
        monkeypatch.setenv("COMPUTER_USE_MCP_ENABLED", value)
        _reset_one_shot_log_flags_for_tests()
        msg = _block_test_connection_when_builtin_unavailable(self._good_config())
        assert msg is not None and "kill-switch" in msg

    def test_match_and_kill_on_proceeds(self, monkeypatch):
        monkeypatch.delenv("COMPUTER_USE_MCP_ENABLED", raising=False)
        monkeypatch.setenv("CU_NPX_PATH", "/usr/bin/env")  # so npx resolves
        _reset_one_shot_log_flags_for_tests()
        msg = _block_test_connection_when_builtin_unavailable(self._good_config())
        assert msg is None

    def test_match_and_generic_disable_refuses(self, monkeypatch):
        """``MCP_DISABLE_BUILT_IN_COMPUTER_USE=true`` overrides ON switch."""
        monkeypatch.delenv("COMPUTER_USE_MCP_ENABLED", raising=False)
        monkeypatch.setenv("CU_NPX_PATH", "/usr/bin/env")
        monkeypatch.setenv("MCP_DISABLE_BUILT_IN_COMPUTER_USE", "true")
        _reset_one_shot_log_flags_for_tests()
        msg = _block_test_connection_when_builtin_unavailable(self._good_config())
        assert msg is not None and "kill-switch" in msg

    def test_match_and_unresolvable_npx_refuses(self, monkeypatch):
        """npx unresolvable → refusal via the resolution gate."""
        monkeypatch.delenv("COMPUTER_USE_MCP_ENABLED", raising=False)
        monkeypatch.setenv("CU_NPX_PATH", "/nonexistent/npx")
        monkeypatch.setenv("PATH", "/dev/null")
        _reset_one_shot_log_flags_for_tests()
        msg = _block_test_connection_when_builtin_unavailable(self._good_config())
        assert msg is not None and "kill-switch" in msg

    def test_non_stdio_transport_no_op(self, monkeypatch):
        """Non-stdio configs are never blocked (no current
        non-stdio cu-mcp variant)."""
        monkeypatch.setenv("COMPUTER_USE_MCP_ENABLED", "false")
        _reset_one_shot_log_flags_for_tests()
        msg = _block_test_connection_when_builtin_unavailable(
            {"transport": "streamable-http", "url": "http://x"}
        )
        assert msg is None

    def test_other_npx_server_no_op(self, monkeypatch):
        """A different npx package is NOT cu-mcp — no block."""
        monkeypatch.setenv("COMPUTER_USE_MCP_ENABLED", "false")
        _reset_one_shot_log_flags_for_tests()
        msg = _block_test_connection_when_builtin_unavailable(
            {
                "transport": "stdio",
                "command": "/usr/local/bin/npx",
                "args": ["-y", "@some-other-pkg@1.0.0"],
            }
        )
        assert msg is None

    def test_wrong_version_pin_no_op(self, monkeypatch):
        """Bumping past the pin (e.g. 7.6.0 or 8.0.0) → no block.

        Future-proof: if a future cu-mcp bump changes the pin, the
        builtin module's own pin changes too — the guard then
        recognizes the new pin. An unpinned npx invocation (no
        version) is treated as a non-match (so an operator with a
        lockfile drift would NOT be blocked — they'd see a
        connection attempt and a separate drift log from the
        builtin module).
        """
        monkeypatch.setenv("COMPUTER_USE_MCP_ENABLED", "false")
        _reset_one_shot_log_flags_for_tests()
        for pin in ("@zavora-ai/computer-use-mcp@7.6.0",
                    "@zavora-ai/computer-use-mcp@99.0.0",
                    "@zavora-ai/computer-use-mcp",
                    "@zavora-ai-different@7.6.1"):
            msg = _block_test_connection_when_builtin_unavailable(
                {
                    "transport": "stdio",
                    "command": "/usr/local/bin/npx",
                    "args": ["-y", pin],
                }
            )
            assert msg is None, f"pin {pin!r} should NOT match the guard"

    def test_command_basename_normalization(self, monkeypatch):
        """Absolute ``CU_NPX_PATH``-style command matches the same
        path as ``/usr/local/bin/npx`` would — we use the FINAL
        PATH COMPONENT so a future host with a different absolute
        path doesn't break the guard."""
        monkeypatch.setenv("COMPUTER_USE_MCP_ENABLED", "false")
        _reset_one_shot_log_flags_for_tests()
        for cmd in (
            "/Users/me/.nvm/versions/node/v22.21.1/bin/npx",
            "/opt/homebrew/bin/npx",
            "/usr/local/bin/npx",
        ):
            cfg = {
                "transport": "stdio",
                "command": cmd,
                "args": list(_CU_MCP_TEST_NPX_ARGS),
            }
            msg = _block_test_connection_when_builtin_unavailable(cfg)
            assert msg is not None and "kill-switch" in msg, (
                f"absolute command {cmd!r} should still match the guard"
            )

    def test_invalid_config_returns_none(self, monkeypatch):
        """Defensive: non-dict, missing fields, etc. all → no block.

        The guard exists to ENFORCE cu-mcp's kill-switch; it must
        NOT refuse valid non-cu-mcp probes for any malformed-input
        reason. Any config that fails the match check returns
        ``None`` so the legacy open path runs.
        """
        monkeypatch.setenv("COMPUTER_USE_MCP_ENABLED", "false")
        _reset_one_shot_log_flags_for_tests()
        for cfg in (None, {}, {"transport": "stdio"}, {"transport": "stdio", "command": 1}):
            msg = _block_test_connection_when_builtin_unavailable(cfg)
            assert msg is None


# ---------------------------------------------------------------------------
# Class 10 — Registry sanity: real cu-mcp entry is present
# ---------------------------------------------------------------------------


class TestRegistryContainsCuMcp:
    """The real ``daemon/mcp/builtin_servers/__init__.py`` wires
    ``ComputerUseServerDefinition`` into the module-level registry on
    import. This pins that import-time wiring against silent removal.
    """

    def test_registry_contains_computer_use_entry(self):
        registry = get_registry()
        entry = registry.get_by_name("computer-use")
        assert entry is not None, (
            "daemon.mcp.builtin_servers must register "
            "ComputerUseServerDefinition on import"
        )
        assert entry.display_name == "Computer Use"
        assert entry.tool_name_prefix == CU_TOOL_NAME_PREFIX
        assert entry.read_only_tools is False
        assert entry.schema_version == CU_SCHEMA_VERSION
        assert entry.tool_call_timeout == CU_TOOL_CALL_TIMEOUT_SECONDS

    def test_get_base_config_pins_package(self):
        """``get_base_config()`` carries the npm pin AND the env-overridable profile."""
        cu_def = ComputerUseServerDefinition()
        cfg = cu_def.get_base_config()
        assert cfg["transport"] == "stdio"
        # ``command`` is resolved dynamically — accept any non-empty
        # absolute path OR ``npx`` (when PATH-lookup succeeds).
        assert cfg["command"].endswith("npx")
        assert cfg["args"] == list(CU_NPX_ARGS)
        # The npm pin is the LAST string in args[1]; cannot drift.
        assert CU_NPM_PACKAGE.endswith("@7.6.1")  # contract pin
        assert cfg["env"]["COMPUTER_USE_PROFILE"] == CU_DEFAULT_PROFILE
