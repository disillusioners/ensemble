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

import os
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


# =============================================================================
# bash-tool orphan reaper Phase 2 — durable spawn-registry sweep (task 2.4)
# =============================================================================


class TestManagerShutdownDurableBashSweep:
    """The Phase-3 block in ``manager.shutdown`` (after the bash
    cleanup_all block): ``sweep_open_rows_for_shutdown`` — awaited,
    failure-isolated, logged; sibling/dead-writer/self/executor legs."""

    @staticmethod
    def _build_manager(mock_config):
        return _build_minimal_manager(mock_config)

    @pytest.mark.asyncio
    async def test_durable_sweep_awaited_and_logged(
        self, mock_config, monkeypatch, caplog
    ):
        import importlib

        bash_module = importlib.import_module("daemon.tools.bash")
        sweep_mod = importlib.import_module("daemon.services.bash_spawn_reaper")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        bash_reg.cleanup_all = AsyncMock(return_value=0)
        bash_reg.get_durable_writer = MagicMock(return_value=None)
        monkeypatch.setattr(
            bash_module, "get_bash_process_registry", lambda: bash_reg
        )
        async_sweep = AsyncMock(return_value=3)
        monkeypatch.setattr(sweep_mod, "sweep_open_rows_for_shutdown", async_sweep)

        manager = self._build_manager(mock_config)
        with caplog.at_level("INFO"):
            await manager.shutdown(grace_period=0.01)

        async_sweep.assert_awaited_once()
        info_texts = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert any("reaped 3 durable bash spawn row" in m for m in info_texts), (
            f"expected durable-sweep INFO log with count 3, got: {info_texts}"
        )

    @pytest.mark.asyncio
    async def test_durable_sweep_failure_isolated(
        self, mock_config, monkeypatch, caplog
    ):
        import importlib

        bash_module = importlib.import_module("daemon.tools.bash")
        sweep_mod = importlib.import_module("daemon.services.bash_spawn_reaper")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        bash_reg.cleanup_all = AsyncMock(return_value=0)
        bash_reg.get_durable_writer = MagicMock(return_value=None)
        monkeypatch.setattr(
            bash_module, "get_bash_process_registry", lambda: bash_reg
        )
        async_sweep = AsyncMock(side_effect=RuntimeError("synthetic sweep fail"))
        monkeypatch.setattr(sweep_mod, "sweep_open_rows_for_shutdown", async_sweep)

        manager = self._build_manager(mock_config)
        with caplog.at_level("WARNING"):
            await manager.shutdown(grace_period=0.01)

        async_sweep.assert_awaited_once()
        warning_texts = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("bash spawn durable sweep failed" in m for m in warning_texts), (
            f"expected durable-sweep WARNING, got: {warning_texts}"
        )
        manager.stop_sources.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_durable_sweep_zero_count_logs_no_info(
        self, mock_config, monkeypatch, caplog
    ):
        import importlib

        bash_module = importlib.import_module("daemon.tools.bash")
        sweep_mod = importlib.import_module("daemon.services.bash_spawn_reaper")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        bash_reg.cleanup_all = AsyncMock(return_value=0)
        bash_reg.get_durable_writer = MagicMock(return_value=None)
        monkeypatch.setattr(
            bash_module, "get_bash_process_registry", lambda: bash_reg
        )
        async_sweep = AsyncMock(return_value=0)
        monkeypatch.setattr(sweep_mod, "sweep_open_rows_for_shutdown", async_sweep)

        manager = self._build_manager(mock_config)
        with caplog.at_level("INFO"):
            await manager.shutdown(grace_period=0.01)

        async_sweep.assert_awaited_once()
        info_texts = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert not any("reaped 0 durable" in m for m in info_texts)

    @pytest.mark.asyncio
    async def test_durable_sweep_sibling_alive_skip_and_dead_writer_swept(
        self, mock_config, monkeypatch
    ):
        """Service-level legs: live-foreign rows skipped (open), dead-writer
        rows swept (gc-closed), self-owned current-boot rows swept (gc
        close proves the self-exemption fired)."""
        import importlib
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel, create_engine

        bash_module = importlib.import_module("daemon.tools.bash")
        sweep_mod = importlib.import_module("daemon.services.bash_spawn_reaper")

        # REAL repo over in-memory engine (StaticPool).
        eng = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        from daemon.repositories.bash_spawn import (  # noqa: F401
            BashSpawnRecord,
            BashSpawnRepository,
        )

        SQLModel.metadata.create_all(eng)
        repo = BashSpawnRepository(engine=eng)
        bash_reg = AsyncMock(name="BashProcessRegistry")
        bash_reg.cleanup_all = AsyncMock(return_value=0)
        bash_reg.get_durable_writer = MagicMock(return_value=repo)
        monkeypatch.setattr(
            bash_module, "get_bash_process_registry", lambda: bash_reg
        )

        from datetime import datetime, timezone
        from daemon.services.bash_spawn_reaper import CURRENT_BOOT_ID

        # sibling (live foreign): daemon_pid = a live sleep we spawn now
        import subprocess

        holder = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            row_sibling = repo.insert_open_row(
                pid=holder.pid,
                pgid=holder.pid,
                start_time=None,
                tracking_id=("s" * 32),
                instance_id="sib-inst",
                command_sanitized="sleep 30",
                boot_id=CURRENT_BOOT_ID,
                daemon_pid=holder.pid,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            # dead-writer row: daemon_pid dead (huge pid)
            row_dead = repo.insert_open_row(
                pid=999201,
                pgid=999201,
                start_time=None,
                tracking_id=("d" * 32),
                instance_id="dead-inst",
                command_sanitized="sleep 30",
                boot_id="foreign-boot",
                daemon_pid=999200,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            # self-owned current-boot row
            row_self = repo.insert_open_row(
                pid=999202,
                pgid=999202,
                start_time=None,
                tracking_id=("e" * 32),
                instance_id="self-inst",
                command_sanitized="sleep 30",
                boot_id=CURRENT_BOOT_ID,
                daemon_pid=os.getpid(),
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            manager = self._build_manager(mock_config)
            await manager.shutdown(grace_period=0.01)

            # sibling row stays OPEN (live foreign daemon owns it)
            r_sib = self._get_row(repo, row_sibling.id)
            assert r_sib.reaped_at is None, "live-sibling row must stay OPEN"
            # dead-writer + self rows swept (closed)
            r_dead = self._get_row(repo, row_dead.id)
            r_self = self._get_row(repo, row_self.id)
            assert r_dead.reaped_at is not None, "dead-writer row must be swept"
            assert r_self.reaped_at is not None, "self-owned row must be swept (self-exemption)"
        finally:
            try:
                if _pid_alive(holder.pid):
                    os.kill(holder.pid, 9)
            except Exception:
                pass
            eng.dispose()

    @pytest.mark.asyncio
    async def test_executor_shaped_child_not_swept_by_shutdown_durable_sweep(
        self, mock_config, monkeypatch
    ):
        """M6 partial: an executor-shaped child (own session, unstamped,
        NO registry row) survives the shutdown durable sweep; a control
        row (dead group) is swept. Proves the sweep only reaches rows."""
        import subprocess
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel, create_engine

        bash_module = importlib if False else __import__("importlib")
        import importlib as _il

        bash_module = _il.import_module("daemon.tools.bash")
        sweep_mod = _il.import_module("daemon.services.bash_spawn_reaper")

        eng = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        from daemon.repositories.bash_spawn import (  # noqa: F401
            BashSpawnRecord,
            BashSpawnRepository,
        )

        SQLModel.metadata.create_all(eng)
        repo = BashSpawnRepository(engine=eng)
        bash_reg = AsyncMock(name="BashProcessRegistry")
        bash_reg.cleanup_all = AsyncMock(return_value=0)
        bash_reg.get_durable_writer = MagicMock(return_value=repo)
        monkeypatch.setattr(
            bash_module, "get_bash_process_registry", lambda: bash_reg
        )

        # Executor-shaped child: own session, unstamped, NO row.
        executor = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            from datetime import datetime, timezone

            from daemon.services.bash_spawn_reaper import CURRENT_BOOT_ID

            # Control row: dead group ⇒ swept.
            repo.insert_open_row(
                pid=999301,
                pgid=999301,
                start_time=None,
                tracking_id=("x" * 32),
                instance_id="ctrl-inst",
                command_sanitized="sleep 30",
                boot_id="foreign-boot",
                daemon_pid=999300,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            manager = self._build_manager(mock_config)
            await manager.shutdown(grace_period=0.01)

            assert self._pid_alive(executor.pid), (
                "executor-shaped child (own session, unstamped, no row) "
                "was swept by the shutdown durable sweep"
            )
        finally:
            try:
                if _pid_alive(executor.pid):
                    os.kill(executor.pid, 9)
            except Exception:
                pass
            eng.dispose()

    @staticmethod
    def _get_row(repo, row_id):
        from sqlmodel import Session

        from daemon.repositories.bash_spawn import BashSpawnRecord

        with Session(repo.engine) as session:
            return session.get(BashSpawnRecord, row_id)

    @staticmethod
    def _pid_alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, OSError):
            return False
