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

from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Engine

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
from daemon.constants import WORKER_POOL_SIZE
from tests.integration.chat_source_harness import (
    build_chat_source_engine,
    chat_lane_flag_reset_fixture,
    wire_manager_only,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test isolation
# ---------------------------------------------------------------------------


# Shared autouse lane-flag reset — the @pytest.fixture(autouse=True)
# decoration travels with the harness factory's returned object, so
# this single module-level assignment wires it for every test here.
chat_lane_flag_reset = chat_lane_flag_reset_fixture()


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite with NullPool + WAL (shared harness builder;
    ``case_sensitive_like=False`` — wiring-only, no claim seam)."""
    eng = build_chat_source_engine(
        str(tmp_path / "chat_pool_late_wire.db"), case_sensitive_like=False
    )
    yield eng
    eng.dispose()


# ---------------------------------------------------------------------------
# Late-wire tests
# ---------------------------------------------------------------------------


class TestLateWireBothPools:
    """set_work_resolver + set_watcher_repo are called on BOTH pools."""

    def test_work_resolver_set_on_default_pool(self, engine):
        """``set_work_resolver`` updates ``_work_resolver`` on the
        default pool (the pre-Phase-2 behavior, pinned here so the
        widening doesn't regress it)."""
        with wire_manager_only(engine) as manager:
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
        with wire_manager_only(engine) as manager:
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
        with wire_manager_only(engine) as manager:
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
        with wire_manager_only(engine, use_worker_pool="false") as manager:
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
