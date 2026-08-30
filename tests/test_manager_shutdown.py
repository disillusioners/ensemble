"""Unit tests for ``InstanceManager.shutdown`` proc cleanup hook.

Phase 1 (2026-07-18) of the "auto-kill background processes on root
instance completion" plan: the very first thing ``shutdown()`` does
AFTER setting ``_shutting_down`` and cancelling background tasks is
to call ``BackgroundProcessManager.cleanup_all()``. This sweeps every
in-memory per-instance bucket, so the daemon-shutdown path no longer
leaks processes.

These tests construct a real ``InstanceManager`` (via the existing
``test_manager.py``-style mocks) but stub out the rest of the
shutdown sequence, so the only thing under test is whether
``cleanup_all()`` was awaited and whether its failures are isolated.

Why a separate file: ``tests/test_manager.py`` has 1.8k lines of
existing tests focused on spawn / terminate / streaming; the shutdown
path was previously untested at the unit level (integration tests in
``tests/integration/`` exercise it via a real daemon). Phase 1 adds
this small unit-level coverage so the proc-cleanup hook has a fast
local regression net independent of the full daemon stack.

Run with::

    pytest tests/test_manager_shutdown.py -v --tb=short
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_config():
    """Minimal Config-shaped mock — same shape as ``test_manager.py``."""
    from daemon.config import (
        AgentsConfig,
        Config,
        DaemonConfig,
        LimitsConfig,
        LLMConfig,
        PersistenceConfig,
    )

    return Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4",
            temperature=0.7,
        ),
        limits=LimitsConfig(
            max_children_per_instance=3,
            instance_timeout_minutes=60,
        ),
        persistence=PersistenceConfig(
            db_path=":memory:",
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
            max_instance_history=300,
        ),
        daemon=DaemonConfig(host="0.0.0.0", port=8079),
        agents=AgentsConfig(directory="./agents"),
    )


def _build_minimal_manager(mock_config, *, shutdown_steps_async=True):
    """Build a real InstanceManager with the heavy components mocked.

    Returns the manager plus references to the mocked subsystems so
    individual tests can configure per-step behavior. The shutdown
    sequence after the proc-cleanup hook is short-circuited to a
    fast no-op (we only care about the hook here).

    DB migrations are stubbed to a no-op — we don't want this test
    pack to depend on the SQLite/PostgreSQL migration dialect
    (those run in real daemon lifecycles and integration tests, not
    here). The shutdown hook itself only touches in-memory state.

    Note: ``MigrationRunner`` is imported lazily inside
    ``InstanceManager.__init__`` (``from .migrations.runner import
    MigrationRunner``), so we patch the source module — not
    ``daemon.manager.MigrationRunner``.
    """
    from daemon.manager import InstanceManager

    class _NoOpMigrationRunner:
        """MigrationRunner stub: no migrations run, no DB touched."""

        def __init__(self, engine):
            self._engine = engine

        def run_pending_migrations(self):
            return 0

    # Most heavy subsystems we don't need. Patch them out so __init__
    # doesn't try to open DBs / load LLMs / run migrations.
    with patch("daemon.manager.PromptCache") as mock_prompt_cache, patch(
        "daemon.manager.build_instance_graph"
    ), patch("daemon.manager.load_and_cache_prompt", return_value=("sp", 0)), patch(
        "daemon.manager.create_instance_tools", return_value=[]
    ), patch(
        "daemon.migrations.runner.MigrationRunner", _NoOpMigrationRunner
    ):
        manager = InstanceManager(mock_config)

    # Stub the post-proc-cleanup shutdown steps so the test does not
    # attempt to close real DBs / event buses / MCP services. Each is
    # an AsyncMock that returns immediately.
    manager.stop_sources = AsyncMock()
    manager._cancel_all_active_requests = AsyncMock()
    manager._wait_for_inflight = AsyncMock()
    manager.shutdown_worker_pool = MagicMock()  # sync (runs via to_thread)
    manager._event_bus = MagicMock()
    manager._event_bus.shutdown = AsyncMock()
    manager._maintenance_service = MagicMock()
    manager._maintenance_service.stop = AsyncMock()
    manager._db_pool_manager = MagicMock()
    manager._db_pool_manager.dispose_all = MagicMock()
    manager.close_checkpointer = AsyncMock()
    manager._drain_warmup_pool = AsyncMock()
    manager._mcp_service = MagicMock()
    manager._mcp_service.close_all_connections = AsyncMock()
    manager._shutdown_opencode_registry = AsyncMock()
    manager.cleanup = MagicMock()

    # Mark the cancellation service as not-yet-shutting-down so the
    # idempotency guard at the top of shutdown() allows the call.
    # ``is_shutting_down`` is a property on CancellationService, so we
    # replace the whole service with a stub whose property returns False.
    class _StubCancellationService:
        @property
        def is_shutting_down(self) -> bool:
            return False

    manager._cancellation_service = _StubCancellationService()  # type: ignore[assignment]

    # Empty background tasks list.
    manager._background_tasks = []

    return manager


class TestManagerShutdownProcCleanup:
    """``shutdown()`` must call ``BackgroundProcessManager.cleanup_all``.

    Best-effort: any exception from ``cleanup_all`` must be caught and
    logged at WARNING. The rest of the shutdown sequence must still
    run.
    """

    @pytest.mark.asyncio
    async def test_shutdown_invokes_cleanup_all(self, mock_config, monkeypatch):
        """The very first shutdown step is ``cleanup_all()`` (sweep all instances)."""
        from daemon.tools import proc_tools

        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        proc_mgr.cleanup_all = AsyncMock(return_value=3)

        monkeypatch.setattr(
            proc_tools,
            "get_background_process_manager",
            lambda: proc_mgr,
        )

        manager = _build_minimal_manager(mock_config)

        await manager.shutdown(grace_period=0.01)

        proc_mgr.cleanup_all.assert_awaited_once()
        # Other shutdown steps also ran (sanity check).
        manager.stop_sources.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_cleanup_all_exception_does_not_propagate(
        self, mock_config, monkeypatch, caplog
    ):
        """If ``cleanup_all`` raises, ``shutdown()`` continues and logs WARNING."""
        from daemon.tools import proc_tools

        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        proc_mgr.cleanup_all = AsyncMock(
            side_effect=RuntimeError("synthetic sweep failure")
        )

        monkeypatch.setattr(
            proc_tools,
            "get_background_process_manager",
            lambda: proc_mgr,
        )

        manager = _build_minimal_manager(mock_config)

        with caplog.at_level("WARNING"):
            # Must not raise.
            await manager.shutdown(grace_period=0.01)

        # cleanup_all was attempted.
        proc_mgr.cleanup_all.assert_awaited_once()

        # WARNING was logged for the failure.
        warning_texts = [
            r.getMessage() for r in caplog.records if r.levelname == "WARNING"
        ]
        assert any(
            "proc cleanup_all failed" in msg for msg in warning_texts
        ), f"Expected a shutdown proc-cleanup WARNING, got: {warning_texts}"

        # Subsequent shutdown steps still ran.
        manager.stop_sources.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_cleanup_all_zero_count_logs_no_info(
        self, mock_config, monkeypatch, caplog
    ):
        """When there are no processes, ``cleanup_all`` returns 0 and no info line is logged.

        Verifies the "if cleaned: logger.info(...)" guard in
        ``manager.shutdown`` — empty buckets should NOT generate noise.
        """
        from daemon.tools import proc_tools

        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        proc_mgr.cleanup_all = AsyncMock(return_value=0)

        monkeypatch.setattr(
            proc_tools,
            "get_background_process_manager",
            lambda: proc_mgr,
        )

        manager = _build_minimal_manager(mock_config)

        with caplog.at_level("INFO"):
            await manager.shutdown(grace_period=0.01)

        proc_mgr.cleanup_all.assert_awaited_once()
        info_texts = [
            r.getMessage() for r in caplog.records if r.levelname == "INFO"
        ]
        assert not any(
            "killed background processes in" in msg for msg in info_texts
        ), (
            f"Expected no 'killed background processes' INFO log when "
            f"cleaned=0; got: {info_texts}"
        )

    @pytest.mark.asyncio
    async def test_shutdown_cleanup_all_positive_count_logs_info(
        self, mock_config, monkeypatch, caplog
    ):
        """When ``cleanup_all`` returns >0, an INFO line is logged with the count."""
        from daemon.tools import proc_tools

        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        proc_mgr.cleanup_all = AsyncMock(return_value=5)

        monkeypatch.setattr(
            proc_tools,
            "get_background_process_manager",
            lambda: proc_mgr,
        )

        manager = _build_minimal_manager(mock_config)

        with caplog.at_level("INFO"):
            await manager.shutdown(grace_period=0.01)

        proc_mgr.cleanup_all.assert_awaited_once()
        info_texts = [
            r.getMessage() for r in caplog.records if r.levelname == "INFO"
        ]
        assert any(
            "killed background processes in 5 instance" in msg
            for msg in info_texts
        ), (
            f"Expected an INFO line reporting 5 instances cleaned; "
            f"got: {info_texts}"
        )


class TestManagerShutdownBashCleanup:
    """``shutdown()`` must sweep all tracked bash process groups."""

    @pytest.mark.asyncio
    async def test_shutdown_invokes_bash_cleanup_all(
        self, mock_config, monkeypatch
    ):
        import importlib

        bash_module = importlib.import_module("daemon.tools.bash")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        bash_reg.cleanup_all = AsyncMock(return_value=3)
        monkeypatch.setattr(
            bash_module,
            "get_bash_process_registry",
            lambda: bash_reg,
        )
        manager = _build_minimal_manager(mock_config)

        await manager.shutdown(grace_period=0.01)

        bash_reg.cleanup_all.assert_awaited_once()
        manager.stop_sources.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_bash_cleanup_all_exception_does_not_propagate(
        self, mock_config, monkeypatch, caplog
    ):
        import importlib

        bash_module = importlib.import_module("daemon.tools.bash")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        bash_reg.cleanup_all = AsyncMock(
            side_effect=RuntimeError("synthetic bash sweep failure")
        )
        monkeypatch.setattr(
            bash_module,
            "get_bash_process_registry",
            lambda: bash_reg,
        )
        manager = _build_minimal_manager(mock_config)

        with caplog.at_level("WARNING"):
            await manager.shutdown(grace_period=0.01)

        bash_reg.cleanup_all.assert_awaited_once()
        manager.stop_sources.assert_awaited_once()
        warning_texts = [
            record.getMessage()
            for record in caplog.records
            if record.levelname == "WARNING"
        ]
        assert any(
            "bash cleanup_all failed" in msg for msg in warning_texts
        )

    @pytest.mark.asyncio
    async def test_shutdown_bash_cleanup_all_zero_count_logs_no_info(
        self, mock_config, monkeypatch, caplog
    ):
        import importlib

        bash_module = importlib.import_module("daemon.tools.bash")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        bash_reg.cleanup_all = AsyncMock(return_value=0)
        monkeypatch.setattr(
            bash_module,
            "get_bash_process_registry",
            lambda: bash_reg,
        )
        manager = _build_minimal_manager(mock_config)

        with caplog.at_level("INFO"):
            await manager.shutdown(grace_period=0.01)

        bash_reg.cleanup_all.assert_awaited_once()
        info_texts = [
            record.getMessage()
            for record in caplog.records
            if record.levelname == "INFO"
        ]
        assert not any("killed bash processes" in msg for msg in info_texts)
