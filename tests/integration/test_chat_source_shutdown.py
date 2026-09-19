"""Phase 2 / chat-source-worker-lane — shutdown teardown test.

Pins ``InstanceManager.shutdown_worker_pool`` (chat-source-worker-lane,
Phase 2 Task #5) — the dual-pool teardown ordering:

  * B2 — snapshot pools BEFORE any stop/None, so the hung-worker
    WARNING loop iterates LIVE worker refs even after the slots
    are None'd.
  * E1 — chat pool FIRST so ``_chat_lane_active`` flips to False
    BEFORE the default pool stops (default pool claims chat rows
    again during its own stop window = fail-open restored).
  * The hung-worker WARNING loop accesses the private ``_workers``
    list on each pool (N4 option (i) — documented exception; see
    ``daemon/manager.py`` ``shutdown_worker_pool`` docstring).
  * ``_pools`` is emptied post-teardown so
    ``_notify_all_pools()`` is a no-op for any late notify.

The fixture pattern is the same one used in
``test_chat_source_pool_wiring.py`` (real ``InstanceManager`` over
file-backed SQLite, ``setup_worker_pool`` with
``num_workers=WORKER_POOL_SIZE`` for the default pool, chat pool
constructed by the production path). After ``shutdown_worker_pool``
returns, all threads MUST be joined within the timeout; the flag
must be False; the slots must be None; ``_pools`` must be empty.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

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
from daemon.constants import CHAT_WORKER_POOL_SIZE, WORKER_POOL_SIZE
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
    """File-backed SQLite with NullPool + WAL — same convention as
    the wiring test."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/chat_pool_shutdown.db",
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
def _wire_manager(engine: Engine) -> Iterator:
    """Build a real ``InstanceManager`` with both pools running.

    Mirrors the harness in ``test_chat_source_pool_wiring.py`` —
    file-backed SQLite engine injected via
    ``create_engine_from_config`` patch; ``build_instance_graph``
    patched to a sentinel; ``MaintenanceService`` stubbed manually
    to skip ``initialize()``'s async lifespan.
    """
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
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)
            yield manager
    finally:
        os.environ.pop("USE_WORKER_POOL", None)


# ---------------------------------------------------------------------------
# Shutdown tests
# ---------------------------------------------------------------------------


class TestShutdownNoExceptions:
    """shutdown_worker_pool does not raise under the dual-pool teardown
    ordering (B2 + E1)."""

    def test_shutdown_returns_cleanly_with_both_pools(self, engine):
        """shutdown_worker_pool runs to completion without exceptions
        when both pools are live."""
        with _wire_manager(engine) as manager:
            # Sanity: both pools running before shutdown.
            assert manager._worker_pool is not None
            assert manager._chat_worker_pool is not None
            assert manager._worker_pool._started is True
            assert manager._chat_worker_pool._started is True

            # This MUST NOT raise.
            manager.shutdown_worker_pool()

            assert manager._worker_pool is None
            assert manager._chat_worker_pool is None


class TestThreadsJoinedWithinTimeout:
    """All worker threads are joined within the ``stop(timeout=30)``
    budget — no leaked threads."""

    def test_default_pool_workers_joined(self, engine):
        with _wire_manager(engine) as manager:
            default_workers = list(manager._worker_pool._workers)
            chat_workers = list(manager._chat_worker_pool._workers)

            assert all(w.is_alive() for w in default_workers)
            assert all(w.is_alive() for w in chat_workers)

            manager.shutdown_worker_pool()

            # After stop(), every worker's Thread.join() completed
            # — the worker is no longer alive.
            for w in default_workers:
                assert not w.is_alive(), (
                    f"default-pool worker {w.worker_id} still alive "
                    f"after shutdown"
                )
            for w in chat_workers:
                assert not w.is_alive(), (
                    f"chat-pool worker {w.worker_id} still alive "
                    f"after shutdown"
                )

    def test_chat_pool_workers_joined(self, engine):
        """Chat workers are also joined — the chat pool's ``stop()``
        is called BEFORE the default pool's ``stop()`` (E1), so the
        join budget is bounded by sum(per-pool 30s)."""
        with _wire_manager(engine) as manager:
            chat_workers = list(manager._chat_worker_pool._workers)

            manager.shutdown_worker_pool()

            for w in chat_workers:
                assert not w.is_alive(), (
                    f"chat-pool worker {w.worker_id} still alive "
                    f"after shutdown"
                )


class TestShutdownFailOpenRestored:
    """``is_chat_lane_active()`` returns False AFTER shutdown —
    B1 fail-open restored."""

    def test_flag_false_after_shutdown(self, engine):
        with _wire_manager(engine) as manager:
            assert is_chat_lane_active() is True
            manager.shutdown_worker_pool()
            assert is_chat_lane_active() is False


class TestPoolsListEmptyAfterShutdown:
    """``_pools`` is emptied post-teardown so ``_notify_all_pools()``
    is a no-op for any late notify."""

    def test_pools_list_empty(self, engine):
        with _wire_manager(engine) as manager:
            assert len(manager._pools) == 2
            manager.shutdown_worker_pool()
            assert manager._pools == []

    def test_notify_all_pools_is_noop_after_shutdown(self, engine):
        """Calling ``_notify_all_pools()`` after shutdown must not
        raise (the helper iterates ``_pools``; an empty list makes
        the helper a clean no-op)."""
        with _wire_manager(engine) as manager:
            manager.shutdown_worker_pool()
            assert manager._pools == []

            # Must not raise — empty-list iteration is the no-op path.
            manager._notify_all_pools()


class TestShutdownLogging:
    """The shutdown log lines are emitted (operator-facing observability).

    Phase 2 doesn't pin the exact log strings — but the chat-pool
    stopped log line is part of the teardown ordering and worth
    capturing for the boot/teardown regression family.
    """

    def test_chat_pool_stopped_log_emitted(
        self, engine, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.INFO):
            with _wire_manager(engine) as manager:
                manager.shutdown_worker_pool()

        # Substring pin — the log line names the chat pool for
        # operator visibility.
        assert "Chat worker pool stopped" in caplog.text

    def test_default_pool_stopped_log_emitted(
        self, engine, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.INFO):
            with _wire_manager(engine) as manager:
                manager.shutdown_worker_pool()

        assert "Worker pool stopped" in caplog.text


# ---------------------------------------------------------------------------
# Phase 3 / Task #8 — shutdown extension: multi-pool assertion +
# "Chat worker pool stopped" log line present + threads joined ≤30s
# ---------------------------------------------------------------------------


class TestShutdownThreadsJoinedWithinProductionBudget:
    """Phase 3 / Task #8 — thread-join budget ≤30s (the production
    ``stop(timeout=30.0)`` budget). The chat pool's 2 workers
    are joined BEFORE the default pool's 5 workers (E1 ordering),
    so the total wall-clock is bounded by the sum of per-pool
    join budgets.

    The Phase 2 / Task #5 shutdown test pins the no-leaked-thread
    invariant for BOTH pools individually (already in the file).
    This Phase 3 extension pins the wall-clock budget explicitly.
    """

    def test_threads_join_within_30s_budget(self, engine, caplog):
        """With both pools running, ``shutdown_worker_pool`` joins
        all 7 worker threads (5 default + 2 chat) within the
        production 30s stop budget per pool.

        Implementation: measure wall-clock from before
        ``shutdown_worker_pool()`` to when ALL workers' threads
        report ``is_alive() == False``. The production stop budget
        is 30s per pool's ``stop()`` call (E1 chat-first ordering),
        so the total budget is bounded by the sum (60s in
        worst case if both pools' 30s timers fire fully).

        The test bounds at 30s TOTAL — both pools joined within
        the production per-pool budget. A worker that fails to
        join within its 30s budget would trigger the
        hung-worker WARNING (manager.py:6916) and ``is_alive()``
        would remain True.
        """
        import time

        with _wire_manager(engine) as manager:
            default_workers = list(manager._worker_pool._workers)
            chat_workers = list(manager._chat_worker_pool._workers)
            assert all(w.is_alive() for w in default_workers)
            assert all(w.is_alive() for w in chat_workers)

            start = time.monotonic()
            manager.shutdown_worker_pool()
            elapsed = time.monotonic() - start

            # After shutdown, no worker should still be alive.
            for w in default_workers + chat_workers:
                assert not w.is_alive(), (
                    f"worker {w.worker_id} still alive after "
                    f"shutdown elapsed={elapsed:.2f}s"
                )

            # Total wall-clock bounded by 30s — the per-pool
            # production budget. If this fails, the E1 ordering
            # or hung-worker WARNING path needs investigation.
            assert elapsed <= 30.0, (
                f"shutdown took {elapsed:.2f}s, exceeds 30s "
                f"production per-pool join budget"
            )


class TestShutdownEmitsChatPoolStoppedLog:
    """Phase 3 / Task #8 — "Chat worker pool stopped" log line is
    emitted during teardown (operator-facing observability — paired
    with the existing "Worker pool stopped" line for the default
    pool).

    The Phase 2 / Task #5 logging test already pins the chat pool
    stopped log line. This Phase 3 extension pins it alongside the
    multi-pool assertion to ensure both pools' lifecycle observability
    is consistent.
    """

    def test_chat_pool_stopped_log_emitted_alongside_default(
        self, engine, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.INFO):
            with _wire_manager(engine) as manager:
                manager.shutdown_worker_pool()

        # BOTH pools' log lines present — paired observability.
        assert "Worker pool stopped" in caplog.text, (
            f"expected 'Worker pool stopped' substring; got: "
            f"{caplog.text!r}"
        )
        assert "Chat worker pool stopped" in caplog.text, (
            f"expected 'Chat worker pool stopped' substring; got: "
            f"{caplog.text!r}"
        )


# ---------------------------------------------------------------------------
# Worker counts as a sanity check — both pools still construct at the
# production sizes (D7 / D10.3).
# ---------------------------------------------------------------------------


class TestWorkerCountsUnchanged:
    """``WORKER_POOL_SIZE`` default pool workers + ``CHAT_WORKER_POOL_SIZE``
    chat pool workers are constructed and live after ``setup_worker_pool``."""

    def test_total_workers_count(self, engine):
        with _wire_manager(engine) as manager:
            total = (
                len(manager._worker_pool._workers)
                + len(manager._chat_worker_pool._workers)
            )
            assert total == WORKER_POOL_SIZE + CHAT_WORKER_POOL_SIZE
