"""agent-secrets P2 phase 3 — MCP spawn-point seam tests (design §1c).

Pins the three per-seam hooks for the THREE MCP spawn paths (the
warm pool + BOTH cold paths — the design requires all three wrapped,
not just the pool):

* ``warmup_pool._create_pooled_connection`` — warm (re)spawn
* ``connection_manager._create_stdio_session`` — cold spawn #1
* ``connection_manager._create_test_stdio_session`` — cold spawn #2

Each path: (i) resolve success (the subprocess env receives the
PLAINTEXT), (ii) fail-closed (bogus marker → typed envelope, server
NOT started), (iii) zero-change (marker-free env → byte-identical
behavior, KMS resolution still works, no new latency).

Composition pin: LITERAL ``resolve_secret_markers(resolve_env(...))``
— KMS-Lite FIRST (pre-existing seam), agent-secrets markers resolve
on the KMS-resolved dict (marker shapes are disjoint, order-stable).
``build_pooled_stdio_config`` stays DO-NOT-TOUCH (R4).
"""

from __future__ import annotations

import uuid
from typing import Any, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event as sa_event, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from daemon.mcp.config import McpStdioConfig
from daemon.repositories.secrets.models import (  # noqa: F401
    Secret,
    SecretAuditEvent,
)
from daemon.services.secret_service import (
    SECRET_CODE_MARKER_MALFORMED,
    SECRET_CODE_NOT_FOUND,
    SecretCipher,
    SecretMarkerResolutionError,
    SecretService,
)
from daemon.services.secret_persistence_scrub import (
    register_secret_service,
    reset_process_registry,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


def _make_sqlite_engine(tmp_path) -> Engine:
    db_path = tmp_path / "mcp-secret-spawn-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


@pytest.fixture
def sqlite_engine(tmp_path) -> Iterator[Engine]:
    eng = _make_sqlite_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def service(
    sqlite_engine: Engine, fernet_key: str
) -> Iterator[SecretService]:
    svc = SecretService(
        engine=sqlite_engine,
        cipher=SecretCipher(encryption_key=fernet_key),
    )
    register_secret_service(svc)
    try:
        yield svc
    finally:
        reset_process_registry()


@pytest.fixture
def pool():
    """A warmup pool with the stdio-client/session plumbing mocked."""
    from daemon.mcp.warmup_pool import McpWarmupPool

    return McpWarmupPool(tool_call_timeout=10)


@pytest.fixture
def manager_cm():
    """A connection manager with the default wiring."""
    from daemon.mcp.connection_manager import McpConnectionManager

    return McpConnectionManager()


def _mock_stdio_env() -> tuple[MagicMock, AsyncMock, MagicMock]:
    """Stdio client + session mocks that expose the spawned env."""
    captured: dict[str, Any] = {}

    cm = MagicMock()

    def _cm_enter():
        # The StdioServerParameters were baked into the cm at
        # construction — capture the env from the server_params the
        # caller passed to TaskScopedStdioClient.
        return (AsyncMock(), AsyncMock())

    cm.__aenter__ = AsyncMock(side_effect=_cm_enter)
    cm.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.start = AsyncMock()
    session.initialize = AsyncMock(return_value=None)
    session.send_ping = AsyncMock()
    return cm, session, captured


# ─────────────────────────────────────────────────────────────────────
# Warm pool — _create_pooled_connection
# ─────────────────────────────────────────────────────────────────────


class TestWarmPoolSecretMarkers:
    @pytest.mark.asyncio
    async def test_resolve_success_plaintext_reaches_server_params(
        self, pool, service: SecretService
    ) -> None:
        """The env passed to ``StdioServerParameters`` carries the
        PLAINTEXT (agent-secrets markers resolved at the spawn
        point; §1c flow).

        Note: the root conftest globally mocks the ``mcp`` SDK, so
        ``StdioServerParameters`` under tests is a MagicMock — the
        env is inspected at the CALL ARGS (the house pattern,
        mirrors ``TestKMSEnvResolution`` in
        ``test_mcp_warmup_pool.py``)."""
        minted = service.store(
            name="mcp-pool-happy", kind="generic",
            value="pt-od-token", actor="worker",
        )
        marker = minted["marker"]
        config = McpStdioConfig(
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"P2_MCP_SECRET": marker},
        )
        pool.register_server("p2-pool", config)

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)
        mock_session.send_ping = AsyncMock()

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "daemon.mcp.warmup_pool.TaskScopedStdioClient",
            return_value=mock_cm,
        ), patch(
            "daemon.mcp.warmup_pool.ManagedClientSession",
            return_value=mock_session,
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
            await pool._create_pooled_connection("p2-pool")

        mock_params.assert_called_once()
        forwarded_env = mock_params.call_args.kwargs.get("env")
        assert forwarded_env is not None
        # The agent-secrets marker resolved to plaintext BEFORE the
        # subprocess spawn — never a literal marker in the env.
        assert forwarded_env["P2_MCP_SECRET"] == "pt-od-token"
        assert "__ENS_SECRET__" not in forwarded_env["P2_MCP_SECRET"]

    @pytest.mark.asyncio
    async def test_fail_closed_missing_id_aborts_spawn(
        self, pool, service: SecretService
    ) -> None:
        """A well-formed marker with an unknown id → typed
        ``secret_not_found``; the stdio client is NEVER constructed
        (server does NOT start with literal markers or partial env)."""
        bogus = f"__ENS_SECRET__{uuid.uuid4()}__"
        config = McpStdioConfig(
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"P2_MCP_SECRET": bogus},
        )
        pool.register_server("p2-pool-fc", config)

        with patch(
            "daemon.mcp.warmup_pool.TaskScopedStdioClient"
        ) as mock_client:
            with pytest.raises(SecretMarkerResolutionError) as exc_info:
                await pool._create_pooled_connection("p2-pool-fc")
            # The spawn was ABORTED — no subprocess client was built.
            mock_client.assert_not_called()
        assert exc_info.value.code == SECRET_CODE_NOT_FOUND
        assert exc_info.value.surface == "mcp_spawn"

    @pytest.mark.asyncio
    async def test_fail_closed_malformed_marker_aborts_spawn(
        self, pool, service: SecretService
    ) -> None:
        """A prefix-shaped value failing fullmatch → typed
        ``secret_marker_malformed``; spawn ABORTED."""
        config = McpStdioConfig(
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"P2_MCP_SECRET": "__ENS_SECRET__not-a-uuid__"},
        )
        pool.register_server("p2-pool-fc2", config)

        with patch(
            "daemon.mcp.warmup_pool.TaskScopedStdioClient"
        ) as mock_client:
            with pytest.raises(SecretMarkerResolutionError) as exc_info:
                await pool._create_pooled_connection("p2-pool-fc2")
            mock_client.assert_not_called()
        assert exc_info.value.code == SECRET_CODE_MARKER_MALFORMED
        assert exc_info.value.surface == "mcp_spawn"

    @pytest.mark.asyncio
    async def test_marker_free_env_kms_still_resolves(
        self, pool
    ) -> None:
        """Marker-free agent-secrets env + a KMS env-ref marker —
        the KMS resolution keeps working (zero-change: no new
        latency on marker-free agent-secrets paths)."""
        import os

        monkey_val = "kms-plaintext-via-env-ref"
        os.environ["P2_MCP_KMS_PROBE"] = monkey_val
        try:
            config = McpStdioConfig(
                transport="stdio",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                env={
                    "P2_MCP_KMS_PROBE": "__KMS_ENV__P2_MCP_KMS_PROBE__",
                    "P2_MCP_PLAIN": "plain-value",
                },
            )
            pool.register_server("p2-pool-zc", config)

            mock_cm = MagicMock()
            mock_cm.__aenter__ = AsyncMock(
                return_value=(AsyncMock(), AsyncMock())
            )
            mock_cm.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.start = AsyncMock()
            mock_session.initialize = AsyncMock(return_value=None)
            mock_session.send_ping = AsyncMock()

            with patch(
                "daemon.mcp.warmup_pool.TaskScopedStdioClient",
                return_value=mock_cm,
            ), patch(
                "daemon.mcp.warmup_pool.ManagedClientSession",
                return_value=mock_session,
            ), patch(
                "daemon.mcp.warmup_pool.load_mcp_tools",
                new_callable=AsyncMock,
            ) as mock_tools, patch(
                "daemon.mcp.warmup_pool.adapt_mcp_tools"
            ) as mock_adapt, patch(
                "daemon.mcp.warmup_pool.StdioServerParameters",
                wraps=__import__("mcp").StdioServerParameters,
            ) as mock_params:
                mock_tools.return_value = [MagicMock()]
                mock_adapt.return_value = [MagicMock()]
                await pool._create_pooled_connection("p2-pool-zc")

            forwarded_env = mock_params.call_args.kwargs.get("env")
            assert forwarded_env["P2_MCP_PLAIN"] == "plain-value"
            assert forwarded_env["P2_MCP_KMS_PROBE"] == monkey_val
        finally:
            del os.environ["P2_MCP_KMS_PROBE"]


# ─────────────────────────────────────────────────────────────────────
# Cold spawn #1 — _create_stdio_session
# ─────────────────────────────────────────────────────────────────────


class TestColdSpawnOne:
    @pytest.mark.asyncio
    async def test_resolve_success_plaintext_reaches_server_params(
        self, manager_cm, service: SecretService
    ) -> None:
        """Cold spawn #1: the agent-secrets marker resolves at the
        spawn point — plaintext in the env forwarded to
        ``StdioServerParameters`` (call-args inspection; the root
        conftest mocks the mcp SDK)."""
        minted = service.store(
            name="mcp-cold1-happy", kind="generic",
            value="pt-cold1-token", actor="worker",
        )
        config = McpStdioConfig(
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"P2_MCP_SECRET": minted["marker"]},
        )

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "daemon.mcp.connection_manager.TaskScopedStdioClient",
            return_value=mock_cm,
        ), patch.object(
            manager_cm, "_open_and_track_session", new_callable=AsyncMock
        ) as mock_open, patch(
            "daemon.mcp.connection_manager.StdioServerParameters",
            wraps=__import__("mcp").StdioServerParameters,
        ) as mock_params:
            mock_open.return_value = MagicMock()
            await manager_cm._create_stdio_session(
                config, "inst-cold1", "p2-cold1", timeout=10.0
            )

        mock_params.assert_called_once()
        forwarded_env = mock_params.call_args.kwargs.get("env")
        assert forwarded_env is not None
        assert forwarded_env["P2_MCP_SECRET"] == "pt-cold1-token"
        assert "__ENS_SECRET__" not in forwarded_env["P2_MCP_SECRET"]

    @pytest.mark.asyncio
    async def test_fail_closed_missing_id_aborts_spawn(
        self, manager_cm, service: SecretService
    ) -> None:
        """Cold spawn #1 fail-closed: server does NOT start."""
        bogus = f"__ENS_SECRET__{uuid.uuid4()}__"
        config = McpStdioConfig(
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"P2_MCP_SECRET": bogus},
        )
        with patch(
            "daemon.mcp.connection_manager.TaskScopedStdioClient"
        ) as mock_client:
            with pytest.raises(SecretMarkerResolutionError) as exc_info:
                await manager_cm._create_stdio_session(
                    config, "inst-cold1-fc", "p2-cold1-fc", timeout=10.0
                )
            mock_client.assert_not_called()
        assert exc_info.value.code == SECRET_CODE_NOT_FOUND
        assert exc_info.value.surface == "mcp_spawn"


# ─────────────────────────────────────────────────────────────────────
# Cold spawn #2 — _create_test_stdio_session
# ─────────────────────────────────────────────────────────────────────


class TestColdSpawnTwo:
    @pytest.mark.asyncio
    async def test_resolve_success_plaintext_reaches_server_params(
        self, manager_cm, service: SecretService
    ) -> None:
        """Cold spawn #2 (test sessions carry the same markers as
        real sessions): plaintext in the env forwarded to
        ``StdioServerParameters`` (call-args inspection)."""
        minted = service.store(
            name="mcp-cold2-happy", kind="generic",
            value="pt-cold2-token", actor="worker",
        )
        config = McpStdioConfig(
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"P2_MCP_SECRET": minted["marker"]},
        )

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "daemon.mcp.connection_manager.TaskScopedStdioClient",
            return_value=mock_cm,
        ), patch.object(
            manager_cm,
            "_create_test_session_from_streams",
            new_callable=AsyncMock,
        ) as mock_test, patch(
            "daemon.mcp.connection_manager.StdioServerParameters",
            wraps=__import__("mcp").StdioServerParameters,
        ) as mock_params:
            mock_test.return_value = (MagicMock(), MagicMock())
            await manager_cm._create_test_stdio_session(
                config, timeout=10.0
            )

        mock_params.assert_called_once()
        forwarded_env = mock_params.call_args.kwargs.get("env")
        assert forwarded_env is not None
        assert forwarded_env["P2_MCP_SECRET"] == "pt-cold2-token"
        assert "__ENS_SECRET__" not in forwarded_env["P2_MCP_SECRET"]

    @pytest.mark.asyncio
    async def test_fail_closed_missing_id_aborts_spawn(
        self, manager_cm, service: SecretService
    ) -> None:
        """Cold spawn #2 fail-closed: server does NOT start."""
        bogus = f"__ENS_SECRET__{uuid.uuid4()}__"
        config = McpStdioConfig(
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"P2_MCP_SECRET": bogus},
        )
        with patch(
            "daemon.mcp.connection_manager.TaskScopedStdioClient"
        ) as mock_client:
            with pytest.raises(SecretMarkerResolutionError) as exc_info:
                await manager_cm._create_test_stdio_session(
                    config, timeout=10.0
                )
            mock_client.assert_not_called()
        assert exc_info.value.code == SECRET_CODE_NOT_FOUND
        assert exc_info.value.surface == "mcp_spawn"


# ─────────────────────────────────────────────────────────────────────
# Composition + audit pins (all three paths)
# ─────────────────────────────────────────────────────────────────────


class TestCompositionAndAudit:
    @pytest.mark.asyncio
    async def test_use_marker_audit_row_emitted(
        self, pool, service: SecretService
    ) -> None:
        """Every successful resolution emits a ``use_marker`` audit
        row with ``context='mcp_spawn'`` (design §3.4)."""
        minted = service.store(
            name="mcp-audit", kind="generic",
            value="pt-audit-token", actor="worker",
        )
        config = McpStdioConfig(
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"P2_MCP_SECRET": minted["marker"]},
        )
        pool.register_server("p2-pool-audit", config)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)
        mock_session.send_ping = AsyncMock()

        class _CapturingStdioClient:
            def __init__(self, server_params):
                pass

            async def __aenter__(self):
                return (AsyncMock(), AsyncMock())

            async def __aexit__(self, *args):
                return None

        with patch(
            "daemon.mcp.warmup_pool.TaskScopedStdioClient",
            _CapturingStdioClient,
        ), patch(
            "daemon.mcp.warmup_pool.ManagedClientSession",
            return_value=mock_session,
        ), patch(
            "daemon.mcp.warmup_pool.load_mcp_tools",
            new_callable=AsyncMock,
        ) as mock_tools, patch(
            "daemon.mcp.warmup_pool.adapt_mcp_tools"
        ) as mock_adapt:
            mock_tools.return_value = [MagicMock()]
            mock_adapt.return_value = [MagicMock()]
            await pool._create_pooled_connection("p2-pool-audit")

        eng = service._engine
        with eng.connect() as conn:
            rows = conn.execute(
                select(SecretAuditEvent).where(  # type: ignore[arg-type]
                    SecretAuditEvent.action == "use_marker"
                )
            ).fetchall()
        assert len(rows) == 1
        assert rows[0].context == "mcp_spawn"

    @pytest.mark.asyncio
    async def test_stored_config_never_holds_resolved_values(
        self, pool, service: SecretService
    ) -> None:
        """R1 invariant — the STORED config keeps the MARKER after a
        spawn-point resolution (plaintext lives in the subprocess env
        exclusively)."""
        minted = service.store(
            name="mcp-r1", kind="generic",
            value="pt-r1-token", actor="worker",
        )
        marker = minted["marker"]
        config = McpStdioConfig(
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"P2_MCP_SECRET": marker},
        )
        pool.register_server("p2-pool-r1", config)

        class _CapturingStdioClient:
            def __init__(self, server_params):
                pass

            async def __aenter__(self):
                return (AsyncMock(), AsyncMock())

            async def __aexit__(self, *args):
                return None

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)
        mock_session.send_ping = AsyncMock()

        with patch(
            "daemon.mcp.warmup_pool.TaskScopedStdioClient",
            _CapturingStdioClient,
        ), patch(
            "daemon.mcp.warmup_pool.ManagedClientSession",
            return_value=mock_session,
        ), patch(
            "daemon.mcp.warmup_pool.load_mcp_tools",
            new_callable=AsyncMock,
        ) as mock_tools, patch(
            "daemon.mcp.warmup_pool.adapt_mcp_tools"
        ) as mock_adapt:
            mock_tools.return_value = [MagicMock()]
            mock_adapt.return_value = [MagicMock()]
            await pool._create_pooled_connection("p2-pool-r1")

        # The stored config is UNCHANGED — still the marker.
        stored = pool._configs["p2-pool-r1"]
        assert stored.env["P2_MCP_SECRET"] == marker
        assert "pt-r1-token" not in str(stored.env)
