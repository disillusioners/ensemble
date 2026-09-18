"""Phase 2 / chat-source-worker-lane — late-wire test for both pools.

Pins the api.py late-wire path (Phase 2 Task #4):

  * After both pools construct, the api.py lifespan calls
    ``pool.set_work_resolver(work_resolver)`` AND
    ``pool.set_watcher_repo(watcher_repo)`` on EACH pool.
  * Each pool's private ``_work_resolver`` / ``_watcher_repo``
    attribute is updated; the chat pool is NOT skipped.
  * ``USE_WORKER_POOL=false`` is tolerated (the late-wire iterates
    a list and skips None pools — the ``for pool in (...)`` loop in
    ``daemon/api.py`` wraps each call in ``if pool is not None``).

Harness: file-backed SQLite (``tmp_path`` + ``NullPool`` + WAL +
busy_timeout=10000) — same convention as the other Phase 2
integration tests. No LLM is invoked (wiring-only).
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
from daemon.constants import WORKER_POOL_SIZE
from daemon.repositories.task.repository import (
    is_chat_lane_active,
    set_chat_lane_active,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_lane_flag():
    """Hard-reset the B1 module flag around EVERY test — shared
    module state must not leak into other suites."""
    set_chat_lane_active(False)
    yield
    set_chat_lane_active(False)


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite with NullPool + WAL."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/chat_pool_late_wire.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@contextmanager
def _wire_manager(engine: Engine, *, use_worker_pool: str | None = None) -> Iterator:
    """Build a real ``InstanceManager`` with both pools running — the
    wiring-only seam used by the late-wire test."""
    if use_worker_pool is not None:
        os.environ["USE_WORKER_POOL"] = use_worker_pool
    else:
        os.environ.pop("USE_WORKER_POOL", None)

    try:
        from daemon.config import (
            AgentsConfig,
            Config,
            DaemonConfig,
            LLMConfig,
            LimitsConfig,
            PersistenceConfig,
        )
        from daemon.manager import InstanceManager
        from daemon.services.maintenance import MaintenanceService

        config = Config(
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
            daemon=DaemonConfig(host="127.0.0.1", port=8079),
            agents=AgentsConfig(directory="./agents"),
        )

        with (
            patch(
                "daemon.migrations.runner.MigrationRunner.run_pending_migrations",
                return_value=[],
            ),
            patch(
                "daemon.manager.create_engine_from_config",
                return_value=engine,
            ),
            patch(
                "daemon.manager.build_instance_graph",
                return_value=None,
            ),
        ):
            manager = InstanceManager(config)
            manager._maintenance_service = MaintenanceService()
            manager._maintenance_service.set_request_registry({})
            yield manager
            try:
                manager.shutdown_worker_pool()
            except Exception:
                pass
    finally:
        os.environ.pop("USE_WORKER_POOL", None)


# ---------------------------------------------------------------------------
# Late-wire tests
# ---------------------------------------------------------------------------


class TestLateWireBothPools:
    """set_work_resolver + set_watcher_repo are called on BOTH pools."""

    def test_work_resolver_set_on_default_pool(self, engine):
        """``set_work_resolver`` updates ``_work_resolver`` on the
        default pool (the pre-Phase-2 behavior, pinned here so the
        widening doesn't regress it)."""
        with _wire_manager(engine) as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

            # Sentinel resolver — the late-wire set_work_resolver
            # should overwrite ``_work_resolver`` with this exact
            # reference (not just any non-None value).
            sentinel_resolver = MagicMock(name="work_resolver")
            sentinel_watcher = MagicMock(name="watcher_repo")

            # Mirror the api.py late-wire shape: iterate both pools
            # with None-guard.
            for pool in (
                manager._worker_pool,
                manager._chat_worker_pool,
            ):
                if pool is not None:
                    pool.set_work_resolver(sentinel_resolver)
                    pool.set_watcher_repo(sentinel_watcher)

            assert manager._worker_pool._work_resolver is sentinel_resolver
            assert manager._worker_pool._watcher_repo is sentinel_watcher

    def test_work_resolver_set_on_chat_pool(self, engine):
        """``set_work_resolver`` updates ``_work_resolver`` on the
        chat pool — the Phase 2 widening (Task #4)."""
        with _wire_manager(engine) as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

            sentinel_resolver = MagicMock(name="work_resolver")
            sentinel_watcher = MagicMock(name="watcher_repo")

            for pool in (
                manager._worker_pool,
                manager._chat_worker_pool,
            ):
                if pool is not None:
                    pool.set_work_resolver(sentinel_resolver)
                    pool.set_watcher_repo(sentinel_watcher)

            assert manager._chat_worker_pool._work_resolver is sentinel_resolver
            assert manager._chat_worker_pool._watcher_repo is sentinel_watcher

    def test_chat_pool_workers_inherit_resolver(self, engine):
        """``set_work_resolver`` propagates the resolver to every
        Worker in the chat pool (the pool's setter fans out to
        live workers — the same fan-out mechanism the default pool
        has used since Phase 2 Batch 2)."""
        with _wire_manager(engine) as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

            sentinel_resolver = MagicMock(name="work_resolver")
            sentinel_watcher = MagicMock(name="watcher_repo")

            for pool in (
                manager._worker_pool,
                manager._chat_worker_pool,
            ):
                if pool is not None:
                    pool.set_work_resolver(sentinel_resolver)
                    pool.set_watcher_repo(sentinel_watcher)

            for worker in manager._chat_worker_pool._workers:
                assert worker._work_resolver is sentinel_resolver
                assert worker._watcher_repo is sentinel_watcher


class TestLateWireUseWorkerPoolFalseTolerated:
    """``USE_WORKER_POOL=false`` leaves both pools None — the late-wire
    loop's None-guard means the iteration is a clean no-op."""

    def test_no_pool_setter_calls_when_both_pools_none(self, engine):
        with _wire_manager(engine, use_worker_pool="false") as manager:
            # Use the kill-switch — both pools stay None.
            assert manager._worker_pool is None
            assert manager._chat_worker_pool is None

            # Mirror api.py late-wire — must not raise on None pools.
            sentinel_resolver = MagicMock(name="work_resolver")
            sentinel_watcher = MagicMock(name="watcher_repo")

            # The exact api.py shape.
            for pool in (
                manager._worker_pool,
                manager._chat_worker_pool,
            ):
                if pool is not None:
                    pool.set_work_resolver(sentinel_resolver)
                    pool.set_watcher_repo(sentinel_watcher)
            # Loop exits cleanly with no setter calls — no AttributeError
            # from a None pool.
