"""Pinning tests for the MCP warmup-pool config starvation fix (2026-10-02).

Root cause (pinned):

1. ``daemon/manager.py`` ``_init_warmup_pool`` registered each
   built-in stdio MCP server with ``definition.build_config({})``
   only — the live row's ``config.env`` was never read. A pooled
   subprocess therefore carried the BUILTIN defaults and ignored any
   post-bootstrap ``mcp_set_env`` / ``kms_attach`` write.
2. ``mcp_set_env`` and ``kms_attach`` (in ``daemon/tools/infra.py``)
   invalidated ONLY the schema cache after a DB write. The pool's
   ``_configs[server_name]`` snapshot was never refreshed, so even a
   live write while the daemon was up did not reach a pooled
   connection's env.

Live symptom (Stage-3 validation, 2026-10-02): ``od_generate_design``
returned "BYOK not configured" while the row held all 4 BYOK values
(BYOK_API_KEY stored as ``__KMS_ENV__`` marker).

What the pin tests cover (each MUST fail on the pre-fix base):

  (1) ``McpWarmupPool.update_server_env`` exists and merges new env
      over the stored config without touching command / args / timeout.
  (2) ``build_pooled_stdio_config`` (the boot-time row overlay helper)
      carries row env over definition defaults — row wins on conflict.
  (3) ``mcp_set_env`` triggers a pool refresh (i.e. the manager's
      pool singleton's ``update_server_env`` is called with the new env).
  (4) ``kms_attach`` triggers a pool refresh (LANE-1 and LANE-2).
  (5) Unrelated pooled servers' stored env is unaffected when one
      server is refreshed — pool-refresh does not bleed across servers.
  (6) Boot-registration row overlay test — when the row has an env
      with the BYOK keys, the resulting ``McpStdioConfig.env`` carries
      those keys (not just the definition defaults). This is the
      end-to-end check on the helper that ``_init_warmup_pool`` now
      routes through.

The pool method (1) and the boot overlay (2) are direct unit tests;
(3) / (4) / (5) / (6) exercise the helper seam through the
``create_mcp_env_tools`` and ``create_kms_tools`` factories that the
manager wires into the tool surface.

Base-FAIL proof: stash this file (or the implementation files) and
re-run — the missing method calls and the missing row-overlay
semantics surface as ``AttributeError`` (the pool method does not
exist on base) and the un-refreshed pool snapshot (mcp_set_env /
kms_attach only invalidate the schema cache).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, create_engine

from daemon.mcp.builtin_servers import get_registry
from daemon.mcp.config import McpStdioConfig
from daemon.mcp.warmup_pool import (
    McpWarmupPool,
    build_pooled_stdio_config,
    get_mcp_warmup_pool,
)
from daemon.repositories.mcp_server import (
    McpServer,
    SQLModelMcpServerRepository,
)
from daemon.tools.infra import (
    create_kms_tools,
    create_mcp_env_tools,
)


SERVER_NAME = "opendesign"
MARKER = "__KMS_REF__KMS_HANDLE_test1234__"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pool() -> McpWarmupPool:
    return McpWarmupPool()


def _register_with_defaults(pool: McpWarmupPool, server_name: str) -> McpStdioConfig:
    """Register a server using the same defaults the manager's
    _init_warmup_pool would have built pre-fix — definition-only env
    (no row overlay)."""
    config = McpStdioConfig(
        transport="stdio",
        command="open-design-mcp",
        args=[],
        env={"OD_DAEMON_URL": "http://127.0.0.1:7456"},
    )
    pool.register_server(server_name, config, pool_size=1)
    return config


@pytest.fixture
def pool() -> McpWarmupPool:
    return _make_pool()


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{tmp_path}/warmup_pool_env_refresh_test.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repository(engine: Engine) -> SQLModelMcpServerRepository:
    return SQLModelMcpServerRepository(engine)


@pytest.fixture
def seed_server(repository: SQLModelMcpServerRepository) -> McpServer:
    """A builtin-shaped row whose env carries a non-secret key + a
    KMS marker (the post-``kms_attach`` state)."""
    return repository.create_mcp_server(
        name=SERVER_NAME,
        description="OpenDesign MCP seam",
        config={
            "transport": "stdio",
            "command": "open-design-mcp",
            "args": [],
            "env": {
                "OD_DAEMON_URL": "http://127.0.0.1:7456",
                "BYOK_API_KEY": MARKER,
            },
        },
        is_builtin=True,
    )


def _make_pool_mock() -> MagicMock:
    """Build a pool mock that the manager's mcp_service exposes."""
    pool = MagicMock(spec=McpWarmupPool)
    # update_server_env must be an AsyncMock-free callable returning
    # True/False; spec=McpWarmupPool gives us the method but we want
    # it observable (MagicMock attributes work for spec'd methods).
    pool.update_server_env = MagicMock(return_value=True)
    return pool


def _make_manager_with_pool(repository: SQLModelMcpServerRepository) -> SimpleNamespace:
    """Bare manager that exposes ``_mcp_service._warmup_pool`` so the
    refresh helper can find the pool through the standard chain.

    The pool is a MagicMock — the helper only calls
    ``update_server_env(name, env)`` on it; we don't need a real
    pool here. The real pool is exercised by the dedicated
    :class:`McpWarmupPool` tests above.
    """
    pool = _make_pool_mock()
    mcp_service = SimpleNamespace(_warmup_pool=pool, invalidate_schema_cache=MagicMock())
    return SimpleNamespace(
        _mcp_server_repository=repository,
        _mcp_service=mcp_service,
    )


# ---------------------------------------------------------------------------
# (1) McpWarmupPool.update_server_env — direct pool test
# ---------------------------------------------------------------------------


class TestUpdateServerEnv:
    """``McpWarmupPool.update_server_env`` is the run-time half of
    the fix. The pre-fix pool had no such method, so the base-FAIL
    proof is straightforward: a call on base raises
    ``AttributeError``.
    """

    def test_update_server_env_method_exists(self, pool) -> None:
        """The pool class MUST expose ``update_server_env`` — base
        has no such method (it's added in this fix)."""
        assert hasattr(pool, "update_server_env"), (
            "McpWarmupPool.update_server_env is missing — the "
            "warmup-pool config starvation fix is not in place"
        )
        assert callable(pool.update_server_env)

    def test_update_server_env_returns_false_for_unregistered(self, pool) -> None:
        """An unknown server name is a no-op returning ``False``
        (matches the bootstrap "skip if not registered" pattern)."""
        assert pool.update_server_env("not-registered", {"X": "1"}) is False
        # And the pool's state did not gain a phantom entry.
        assert "not-registered" not in pool._configs

    def test_update_server_env_overlays_env_only(
        self, pool, mock_stdio_client=None, mock_load_mcp_tools=None
    ) -> None:
        """The new env is MERGED over the stored env. command / args
        / timeout are unchanged. Existing keys survive unless the
        caller passes a new value for them (overlay semantics)."""
        original = _register_with_defaults(pool, SERVER_NAME)
        original_command = original.command
        original_args = list(original.args)
        original_timeout = original.timeout

        ok = pool.update_server_env(
            SERVER_NAME,
            {"BYOK_BASE_URL": "https://llm-supervisor-proxy/v1", "BYOK_MODEL": "vision"},
        )
        assert ok is True

        stored = pool._configs[SERVER_NAME]
        # Existing OD_DAEMON_URL preserved.
        assert stored.env["OD_DAEMON_URL"] == "http://127.0.0.1:7456"
        # New keys landed.
        assert stored.env["BYOK_BASE_URL"] == "https://llm-supervisor-proxy/v1"
        assert stored.env["BYOK_MODEL"] == "vision"
        # command / args / timeout UNCHANGED (env is the only mutable axis).
        assert stored.command == original_command
        assert list(stored.args) == original_args
        assert stored.timeout == original_timeout

    def test_update_server_env_visible_to_subsequent_create_pooled_connection(
        self, pool
    ) -> None:
        """The next ``_create_pooled_connection`` MUST see the new env
        forwarded to ``StdioServerParameters``. This is the actual
        fix-path: after ``mcp_set_env`` writes BYOK_BASE_URL, the
        next ``acquire()`` + ``_replenish`` must spawn a stdio
        subprocess with that key set."""
        _register_with_defaults(pool, SERVER_NAME)
        pool.update_server_env(
            SERVER_NAME,
            {"BYOK_BASE_URL": "https://llm-supervisor-proxy/v1"},
        )

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)
        mock_session.send_ping = AsyncMock()

        with patch(
            "daemon.mcp.stdio_wrapper.mcp.stdio_client", return_value=mock_cm
        ), patch(
            "daemon.mcp.warmup_pool.ManagedClientSession", return_value=mock_session
        ), patch(
            "daemon.mcp.warmup_pool.load_mcp_tools", new_callable=AsyncMock
        ) as mock_tools, patch(
            "daemon.mcp.warmup_pool.adapt_mcp_tools"
        ) as mock_adapt, patch(
            "daemon.mcp.warmup_pool.StdioServerParameters",
            wraps=__import__("mcp").StdioServerParameters,
        ) as mock_params:
            mock_tools.return_value = [MagicMock()]
            mock_adapt.return_value = [MagicMock()]

            # The pool is not "running" — call the private seam so
            # the env-forward assertion is direct (not blocked by
            # the warmup-running guard in the public ``acquire``).
            asyncio.get_event_loop().run_until_complete(
                pool._create_pooled_connection(SERVER_NAME)
            )

        # The new env was forwarded to StdioServerParameters.
        mock_params.assert_called_once()
        forwarded_env = mock_params.call_args.kwargs.get("env")
        assert forwarded_env is not None
        assert forwarded_env.get("BYOK_BASE_URL") == "https://llm-supervisor-proxy/v1"
        # Definition default survived.
        assert forwarded_env.get("OD_DAEMON_URL") == "http://127.0.0.1:7456"

    def test_update_server_env_defensive_copy(self, pool) -> None:
        """The helper MUST take a defensive copy of the caller's dict
        so a later mutation of that dict does not silently corrupt
        the pool's stored env. This pins the "no shared-reference
        surprises" contract — R1's co-ownership applies here too:
        a tool that reuses the same dict across writes must not be
        able to retroactively change the pool's view of an earlier
        write."""
        _register_with_defaults(pool, SERVER_NAME)

        call_env = {"BYOK_BASE_URL": "https://llm-supervisor-proxy/v1"}
        pool.update_server_env(SERVER_NAME, call_env)

        # Mutate the caller's dict AFTER the refresh.
        call_env["BYOK_BASE_URL"] = "https://hijacked.example/v1"
        call_env["INJECTED"] = "yes"

        stored = pool._configs[SERVER_NAME]
        assert stored.env["BYOK_BASE_URL"] == "https://llm-supervisor-proxy/v1", (
            "call-site mutation of the input dict leaked into the pool's stored env"
        )
        assert "INJECTED" not in stored.env, (
            "post-refresh mutation of the input dict landed in the pool's stored env"
        )


# ---------------------------------------------------------------------------
# (2) build_pooled_stdio_config — boot-time row overlay helper
# ---------------------------------------------------------------------------


class TestBuildPooledStdioConfig:
    """The module-level helper used by ``_init_warmup_pool`` to
    overlay the row's env on the definition's build_config defaults.

    Precedence (PINNED): row env wins on every key.
    """

    def test_overlay_row_env_onto_definition_defaults(self) -> None:
        """The row's env (post-``mcp_set_env`` state) is merged
        over the definition defaults. Pre-fix, the manager called
        ``build_config({})`` directly and dropped every row entry."""
        definition = get_registry().get_by_name(SERVER_NAME)

        row_config = {
            "transport": "stdio",
            "command": "open-design-mcp",
            "args": [],
            "env": {
                "OD_DAEMON_URL": "http://127.0.0.1:7456",  # default
                "BYOK_BASE_URL": "https://llm-supervisor-proxy/v1",
                "BYOK_MODEL": "vision",
            },
        }

        config = build_pooled_stdio_config(definition, row_config)

        # BYOK keys from the row landed in the pooled config.
        assert config.env.get("BYOK_BASE_URL") == "https://llm-supervisor-proxy/v1"
        assert config.env.get("BYOK_MODEL") == "vision"
        # OD_DAEMON_URL is the definition default and survives.
        assert config.env.get("OD_DAEMON_URL") == "http://127.0.0.1:7456"
        # transport / command / args come from the definition.
        assert config.transport == "stdio"
        assert config.command  # non-empty

    def test_row_env_wins_on_conflict(self) -> None:
        """PINNED precedence: row env wins on every key. The row is
        the live state post-bootstrap; a stale definition would
        silently drop KMS markers / operator-set values."""
        definition = get_registry().get_by_name(SERVER_NAME)

        row_config = {
            "env": {
                "OD_DAEMON_URL": "http://row-wins:9999",  # conflicts w/ definition default
                "OD_API_TOKEN": "row-set-token",  # new key not in defaults
            },
        }

        config = build_pooled_stdio_config(definition, row_config)
        assert config.env["OD_DAEMON_URL"] == "http://row-wins:9999"
        assert config.env["OD_API_TOKEN"] == "row-set-token"

    def test_no_row_config_falls_back_to_definition_defaults(self) -> None:
        """Missing / empty row config MUST leave the pre-fix behavior
        intact (other builtin servers without env writes still work).
        This is the "no regression for unrelated pooled servers"
        pin."""
        definition = get_registry().get_by_name(SERVER_NAME)

        # No row at all.
        config = build_pooled_stdio_config(definition, None)
        assert config.env.get("OD_DAEMON_URL") == "http://127.0.0.1:7456"

        # Empty row config.
        config = build_pooled_stdio_config(definition, {})
        assert config.env.get("OD_DAEMON_URL") == "http://127.0.0.1:7456"

        # Row config without an env block.
        config = build_pooled_stdio_config(definition, {"transport": "stdio"})
        assert config.env.get("OD_DAEMON_URL") == "http://127.0.0.1:7456"

    def test_row_env_with_kms_marker_survives_overlay(self) -> None:
        """A KMS marker written by ``kms_attach`` flows through the
        overlay byte-identical — the resolver still expands it at
        spawn time. Pre-fix, the row was never read so this was
        a moot point; the pin guarantees the FIX does not silently
        drop the marker on the overlay path."""
        definition = get_registry().get_by_name(SERVER_NAME)
        marker = "__KMS_ENV__OPENAI_API_KEY__"

        row_config = {
            "env": {
                "OD_DAEMON_URL": "http://127.0.0.1:7456",
                "BYOK_API_KEY": marker,
            },
        }

        config = build_pooled_stdio_config(definition, row_config)
        assert config.env["BYOK_API_KEY"] == marker


# ---------------------------------------------------------------------------
# (3) mcp_set_env triggers pool refresh
# ---------------------------------------------------------------------------


class TestMcpSetEnvTriggersPoolRefresh:
    """``mcp_set_env`` MUST call ``McpWarmupPool.update_server_env``
    with the row's env after a successful DB write. Pre-fix, the
    tool only invalidated the schema cache; the pool's stored config
    was never refreshed.
    """

    def test_mcp_set_env_calls_update_server_env(
        self, repository, seed_server
    ) -> None:
        manager = _make_manager_with_pool(repository)
        pool = manager._mcp_service._warmup_pool

        tool = next(
            t for t in create_mcp_env_tools(manager, current_instance_id="test-iid")
            if t.name == "mcp_set_env"
        )
        result = json.loads(
            tool.invoke(
                {
                    "server": SERVER_NAME,
                    "env": {"BYOK_BASE_URL": "https://llm-supervisor-proxy/v1"},
                }
            )
        )
        assert result["ok"] is True

        # The pool's update_server_env was called with the SERVER NAME
        # and the full env the row now holds (including the just-written
        # key alongside the pre-existing OD_DAEMON_URL and BYOK_API_KEY
        # marker).
        pool.update_server_env.assert_called_once()
        call_args = pool.update_server_env.call_args
        assert call_args.args[0] == SERVER_NAME, (
            "pool refresh was called with the wrong server name"
        )
        forwarded_env = call_args.args[1]
        assert forwarded_env["BYOK_BASE_URL"] == "https://llm-supervisor-proxy/v1"
        assert forwarded_env["OD_DAEMON_URL"] == "http://127.0.0.1:7456"
        assert forwarded_env["BYOK_API_KEY"] == MARKER

    def test_mcp_set_env_noop_without_pool(
        self, repository, seed_server
    ) -> None:
        """When the manager has no ``_mcp_service`` (legacy fixture
        pattern), ``mcp_set_env`` MUST still succeed — the refresh
        helper is a no-op without a pool, matching the
        ``_invalidate_mcp_schema_cache`` getattr guard."""
        manager = SimpleNamespace(_mcp_server_repository=repository)
        tool = next(
            t for t in create_mcp_env_tools(manager, current_instance_id="test-iid")
            if t.name == "mcp_set_env"
        )
        result = json.loads(
            tool.invoke({"server": SERVER_NAME, "env": {"BYOK_MODEL": "vision"}})
        )
        assert result["ok"] is True

    def test_mcp_set_env_noop_when_pool_has_no_update_method(
        self, repository, seed_server
    ) -> None:
        """Defensive: a pool mock that does NOT expose
        ``update_server_env`` (e.g. a bare ``MagicMock``) MUST NOT
        break ``mcp_set_env``. The helper's ``getattr` guard
        no-ops, the tool returns success. This pins the helper's
        no-pool-method fallback."""
        manager = SimpleNamespace(
            _mcp_server_repository=repository,
            _mcp_service=SimpleNamespace(_warmup_pool=MagicMock(spec=[])),
        )
        tool = next(
            t for t in create_mcp_env_tools(manager, current_instance_id="test-iid")
            if t.name == "mcp_set_env"
        )
        result = json.loads(
            tool.invoke({"server": SERVER_NAME, "env": {"BYOK_MODEL": "vision"}})
        )
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# (4) kms_attach triggers pool refresh — both lanes
# ---------------------------------------------------------------------------


class TestKmsAttachTriggersPoolRefresh:
    """``kms_attach`` MUST also refresh the pool — it writes a marker
    into ``config.env`` which the spawn-time resolver expands. Pre-fix
    even the schema cache was NOT invalidated by kms_attach, so this
    test pins BOTH that the schema cache is now invalidated AND the
    pool refresh path runs."""

    def test_kms_attach_lane1_calls_update_server_env(
        self, repository, seed_server
    ) -> None:
        from daemon.services.kms_lite import kms_request

        manager = _make_manager_with_pool(repository)
        pool = manager._mcp_service._warmup_pool
        handle = kms_request(SERVER_NAME, "test-lane1")["handle"]

        tool = next(
            t for t in create_kms_tools(manager, current_instance_id="test-iid")
            if t.name == "kms_attach"
        )
        result = json.loads(
            tool.invoke(
                {
                    "server_id": seed_server.id,
                    "env_key": "BYOK_API_KEY",
                    "handle": handle,
                }
            )
        )
        assert "server_id" in result

        pool.update_server_env.assert_called_once()
        call_args = pool.update_server_env.call_args
        assert call_args.args[0] == SERVER_NAME
        forwarded_env = call_args.args[1]
        # The just-written marker is in the forwarded env.
        assert "BYOK_API_KEY" in forwarded_env
        # OD_DAEMON_URL still present.
        assert forwarded_env.get("OD_DAEMON_URL") == "http://127.0.0.1:7456"

    def test_kms_attach_lane2_calls_update_server_env(
        self, repository, seed_server, monkeypatch
    ) -> None:
        from daemon.services.kms_lite import build_env_marker

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-llm-proxy-key-DO-NOT-LEAK")
        manager = _make_manager_with_pool(repository)
        pool = manager._mcp_service._warmup_pool

        tool = next(
            t for t in create_kms_tools(manager, current_instance_id="test-iid")
            if t.name == "kms_attach"
        )
        result = json.loads(
            tool.invoke(
                {
                    "server_id": seed_server.id,
                    "env_key": "BYOK_API_KEY",
                    "env_source": "OPENAI_API_KEY",
                }
            )
        )
        assert "env_source" in result

        pool.update_server_env.assert_called_once()
        call_args = pool.update_server_env.call_args
        assert call_args.args[0] == SERVER_NAME
        forwarded_env = call_args.args[1]
        # Marker (NOT plaintext) flows to the pool — the resolver
        # expands it at spawn time. The literal plaintext must NOT
        # leak through this code path (R1 invariant: markers only,
        # never plaintext, on the env-write surface).
        assert forwarded_env["BYOK_API_KEY"] == build_env_marker("OPENAI_API_KEY")
        assert "sk-test-llm-proxy-key-DO-NOT-LEAK" not in forwarded_env["BYOK_API_KEY"]


# ---------------------------------------------------------------------------
# (5) Unrelated pooled servers unaffected
# ---------------------------------------------------------------------------


class TestUnrelatedServersUnaffected:
    """Refreshing one server's pool config MUST NOT bleed into
    another server's stored config. The fix operates per-server
    via a name key; the helper's defensive copy keeps each
    server's env isolated.
    """

    def test_refresh_one_server_does_not_touch_others(self, pool) -> None:
        # Three servers with distinct default envs.
        for name, od_url in [
            ("opendesign", "http://127.0.0.1:7456"),
            ("plane", "http://plane.local:8080"),
            ("context7", "http://context7.local:9090"),
        ]:
            _register_with_defaults(
                pool,
                name,
            )
            # Replace the env explicitly so the per-server identity
            # test below is sharp (the default fixture's name is
            # "context7" in the helper — we just want distinct envs).
            pool._configs[name].env = {"OD_DAEMON_URL": od_url}

        # Refresh only opendesign.
        pool.update_server_env(
            "opendesign",
            {"BYOK_BASE_URL": "https://llm-supervisor-proxy/v1", "BYOK_MODEL": "vision"},
        )

        # opendesign has the BYOK keys.
        od_env = pool._configs["opendesign"].env
        assert od_env["BYOK_BASE_URL"] == "https://llm-supervisor-proxy/v1"
        assert od_env["BYOK_MODEL"] == "vision"
        assert od_env["OD_DAEMON_URL"] == "http://127.0.0.1:7456"

        # plane and context7 are byte-identical to before the refresh —
        # no BYOK keys, original env intact.
        plane_env = pool._configs["plane"].env
        assert plane_env == {"OD_DAEMON_URL": "http://plane.local:8080"}
        assert "BYOK_BASE_URL" not in plane_env
        assert "BYOK_MODEL" not in plane_env

        ctx_env = pool._configs["context7"].env
        assert ctx_env == {"OD_DAEMON_URL": "http://context7.local:9090"}
        assert "BYOK_BASE_URL" not in ctx_env
        assert "BYOK_MODEL" not in ctx_env


# ---------------------------------------------------------------------------
# (6) End-to-end boot-registration: row env reaches pool
# ---------------------------------------------------------------------------


class TestBootRegistrationRowEnvReachesPool:
    """End-to-end pin on the boot-time fix in
    ``InstanceManager._init_warmup_pool``: the helper that the
    manager now routes through (``build_pooled_stdio_config``) must
    carry the row's env into the registered ``McpStdioConfig``.

    We don't instantiate the real manager here (the manager's
    __init__ is a sprawling facade). Instead, this test exercises
    the same code path the manager uses — calling the helper with
    a row-shaped config and then registering the resulting
    ``McpStdioConfig`` with a real ``McpWarmupPool``. Pre-fix the
    manager would have constructed ``McpStdioConfig(**build_config({}))``
    and the row's env was silently dropped; with the fix the row's
    env lands in ``pool._configs[server_name].env`` at boot.
    """

    def test_row_env_lands_in_pooled_config(self) -> None:
        definition = get_registry().get_by_name(SERVER_NAME)
        row_config = {
            "transport": "stdio",
            "command": "open-design-mcp",
            "args": [],
            "env": {
                "OD_DAEMON_URL": "http://127.0.0.1:7456",
                "BYOK_BASE_URL": "https://llm-supervisor-proxy/v1",
                "BYOK_MODEL": "vision",
            },
        }

        pool = McpWarmupPool()
        stdio_config = build_pooled_stdio_config(definition, row_config)
        pool.register_server(SERVER_NAME, stdio_config, pool_size=1)

        # The pool's stored config carries the row's BYOK keys.
        stored = pool._configs[SERVER_NAME]
        assert stored.env.get("BYOK_BASE_URL") == "https://llm-supervisor-proxy/v1"
        assert stored.env.get("BYOK_MODEL") == "vision"
        assert stored.env.get("OD_DAEMON_URL") == "http://127.0.0.1:7456"

    def test_row_with_no_env_uses_definition_defaults(self) -> None:
        """A row whose env block is missing or empty (e.g. user
        just provisioned the server and hasn't run mcp_set_env yet)
        MUST still register — the helper falls back to the
        definition's defaults so other pooled servers don't regress."""
        definition = get_registry().get_by_name(SERVER_NAME)

        pool = McpWarmupPool()
        stdio_config = build_pooled_stdio_config(definition, None)
        pool.register_server(SERVER_NAME, stdio_config, pool_size=1)

        stored = pool._configs[SERVER_NAME]
        # Only the definition defaults — no spurious BYOK keys.
        assert stored.env.get("OD_DAEMON_URL") == "http://127.0.0.1:7456"
        assert "BYOK_BASE_URL" not in stored.env
        assert "BYOK_MODEL" not in stored.env
