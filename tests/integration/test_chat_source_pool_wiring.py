"""Phase 2 / chat-source-worker-lane — wiring test for the dual-pool boot.

Pins the production wiring of ``InstanceManager.setup_worker_pool`` /
``shutdown_worker_pool`` (chat-source-worker-lane, Phase 2 Tasks
#1-#6). Verifies:

  * ``setup_worker_pool(num_workers=WORKER_POOL_SIZE)`` constructs
    BOTH pools (default + chat); the chat pool is sized at
    ``CHAT_WORKER_POOL_SIZE=2`` and the workers are named
    ``chat-worker-{i}`` (D10.5).
  * The ``ChatSourceWorkerPool started: workers=2,
    prefixes=telegram:,slack:,discord:`` boot line is logged (Task
    #6 — substring pin via ``caplog``; filesystem log parsing is
    FORBIDDEN in tests per the Phase 2 Exit Criterion #1
    precedent in
    ``tests/unit/job_queue/test_joblock_sweep_lifecycle.py``).
  * ``USE_WORKER_POOL=false`` short-circuits BOTH pool
    constructions — both slots stay None and
    ``is_chat_lane_active()`` stays False (the kill-switch is
    shared across pools, not just the default).
  * ``shutdown_worker_pool()`` clears BOTH slots (Task #5 — B2
    ordering).

The B1 conditional-fail-open test (``test_b1_conditional_fail_open
_chat_pool_absent_then_present``, approver S4 — home = this file)
covers the three flag states (P1 alone / P1+P2 / P2-shutdown).
Phase 3's pinning test points at this file rather than duplicating
the case (single home per approver S4).

Harness: file-backed SQLite (``tmp_path`` + ``NullPool`` +
WAL + busy_timeout=10000) — mirrors the production concurrency
shape. The chat-source-worker-lane F7/F9 discipline pins
``PRAGMA case_sensitive_like = ON`` in the connect listener, but
this harness does NOT need LIKE semantics (no claim seam
exercise); the harness is wiring-only. The chat-lane claim
predicate is already pinned by
``tests/unit/test_repository_claim_lane.py``.

No real LLM is invoked — ``build_instance_graph`` is patched to
return a sentinel; the wiring path runs through the rest of the
manager unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
from daemon.constants import (
    CHAT_SOURCE_PREFIXES,
    CHAT_WORKER_POOL_SIZE,
    WORKER_POOL_SIZE,
)
from daemon.repositories.task.repository import (
    is_chat_lane_active,
    set_chat_lane_active,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test isolation — module-global flag must not leak between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_lane_flag():
    """Hard-reset the B1 module flag around EVERY test — ``_chat_lane_active``
    is shared module state; leakage into other suites would misroute
    default-lane claims (mirrors the discipline in
    ``tests/unit/test_repository_claim_lane.py``)."""
    set_chat_lane_active(False)
    yield
    set_chat_lane_active(False)


# ---------------------------------------------------------------------------
# Engine fixture — file-backed SQLite, NullPool, WAL (chat-source F7)
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path) -> Engine:
    """Real SQLite FILE database with per-connection PRAGMAs.

    ``NullPool`` + per-connection ``connect`` listener PRAGMA is the
    convention for chat-source-worker-lane integration tests (F7 —
    file-backed SQLite, not in-memory). WAL keeps multi-checkout
    concurrency honest.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/chat_pool_wiring.db",
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


# ---------------------------------------------------------------------------
# Harness builder — real InstanceManager, real worker pools, no LLM
# ---------------------------------------------------------------------------


@contextmanager
def _wire_manager(engine: Engine, *, use_worker_pool: str | None = None) -> Iterator:
    """Build a real ``InstanceManager`` and run ``setup_worker_pool``.

    The LLM is irrelevant (wiring-only); ``build_instance_graph`` is
    patched to a sentinel so the manager can be constructed without
    the full graph stack. The chat pool construction does NOT call
    into the graph (only the default pool's TaskProcessor and the
    claim seam would, both of which run with a stub TaskProcessor
    because no claim happens during ``setup_worker_pool``).

    Skip ``manager.initialize()`` (heavy async lifespan) and stub
    the only seam ``setup_worker_pool`` touches on the manager's
    pre-init state — ``self._maintenance_service`` is set by
    ``initialize()`` but ``setup_worker_pool`` calls
    ``self._maintenance_service.set_task_repository(...)`` at
    manager.py:6473. Same recipe as
    ``tests/integration/test_wc_wake_pure_hang.py:406-409``.

    Args:
        engine: The shared engine injected into every ``create_engine_from_config``
            call inside the manager.
        use_worker_pool: ``"false"`` to set the kill-switch; ``None``
            (default) clears it so both pools construct.
    """
    # Set/clear the USE_WORKER_POOL env flag WITHOUT polluting the
    # process environment for other tests — the fixture's autouse
    # teardown restores the default and other tests in this module
    # use the same pattern.
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
            # Stub the maintenance service — ``initialize()`` is the
            # production site but is heavy (async lifespan). The
            # only seam ``setup_worker_pool`` touches is
            # ``set_task_repository(...)``; a bare ``MaintenanceService``
            # instance + a request-registry dict suffices. Mirror
            # the wake_harness recipe at
            # ``tests/integration/test_wc_wake_pure_hang.py:406-409``.
            manager._maintenance_service = MaintenanceService()
            manager._maintenance_service.set_request_registry({})
            yield manager
            # Safety net: ensure pools are torn down even if the
            # caller forgets — prevents leaked worker threads across
            # tests.
            try:
                manager.shutdown_worker_pool()
            except Exception:
                pass
    finally:
        os.environ.pop("USE_WORKER_POOL", None)


# ---------------------------------------------------------------------------
# Wiring tests
# ---------------------------------------------------------------------------


class TestBothPoolsConstructed:
    """setup_worker_pool constructs BOTH pools (default + chat)."""

    def test_default_pool_present_with_production_size(
        self, engine, caplog: pytest.LogCaptureFixture
    ):
        """``_worker_pool`` is non-None with ``WORKER_POOL_SIZE=5`` workers
        (Phase 2 Exit Criterion #1, approver N9 — fixed from
        ``num_workers=1``; production default size, NOT 1, NOT
        ``CHAT_WORKER_POOL_SIZE``)."""
        with caplog.at_level(logging.INFO):
            with _wire_manager(engine) as manager:
                manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

                assert manager._worker_pool is not None
                assert len(manager._worker_pool._workers) == WORKER_POOL_SIZE
                # Workers are named with the default prefix
                # (``worker-{i}``), NOT the chat prefix.
                worker_ids = sorted(
                    w.worker_id for w in manager._worker_pool._workers
                )
                assert worker_ids == [
                    f"worker-{i}" for i in range(WORKER_POOL_SIZE)
                ]

    def test_chat_pool_present_with_chat_size(
        self, engine, caplog: pytest.LogCaptureFixture
    ):
        """``_chat_worker_pool`` is non-None with
        ``CHAT_WORKER_POOL_SIZE=2`` workers named ``chat-worker-{i}``
        (D10.5 + Phase 2 Exit Criterion #1)."""
        with caplog.at_level(logging.INFO):
            with _wire_manager(engine) as manager:
                manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

                assert manager._chat_worker_pool is not None
                assert (
                    len(manager._chat_worker_pool._workers)
                    == CHAT_WORKER_POOL_SIZE
                )
                chat_worker_ids = sorted(
                    w.worker_id
                    for w in manager._chat_worker_pool._workers
                )
                assert chat_worker_ids == [
                    f"chat-worker-{i}"
                    for i in range(CHAT_WORKER_POOL_SIZE)
                ]

    def test_pools_list_populated_with_both(self, engine):
        """``_pools`` is populated AFTER both pools construct — D5 list-shape."""
        with _wire_manager(engine) as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

            assert len(manager._pools) == 2
            assert manager._worker_pool in manager._pools
            assert manager._chat_worker_pool in manager._pools

    def test_b1_flag_flipped_true_after_construction(self, engine):
        """``is_chat_lane_active()`` returns True after
        ``setup_worker_pool`` (B1 — flag flipped ON immediately after
        chat-pool start)."""
        with _wire_manager(engine) as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

            assert is_chat_lane_active() is True

    def test_boot_line_substring_emitted(
        self, engine, caplog: pytest.LogCaptureFixture
    ):
        """The Task #6 boot line
        ``ChatSourceWorkerPool started: workers=2,
        prefixes=telegram:,slack:,discord:`` is emitted via
        ``logger.info`` (caplog substring — NOT filesystem log)."""
        with caplog.at_level(logging.INFO):
            with _wire_manager(engine) as manager:
                manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

        # Substring match against the SC#8 literal (Phase 2 Exit
        # Criterion #1, plan-overview.md SC#8 row). The f-string
        # construction guarantees the workers/prefixes fields are
        # rendered from the live CHAT_* constants.
        assert "ChatSourceWorkerPool started:" in caplog.text
        assert "workers=2" in caplog.text
        assert "prefixes=" + ",".join(CHAT_SOURCE_PREFIXES) in caplog.text


class TestUseWorkerPoolFalseDisablesBoth:
    """``USE_WORKER_POOL=false`` disables BOTH pools (shared kill-switch)."""

    def test_both_pool_slots_remain_none(self, engine):
        """``USE_WORKER_POOL=false`` short-circuits inside
        ``setup_worker_pool`` BEFORE any pool construct — both
        ``_worker_pool`` and ``_chat_worker_pool`` stay None."""
        with _wire_manager(engine, use_worker_pool="false") as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

            assert manager._worker_pool is None
            assert manager._chat_worker_pool is None
            assert manager._pools == []

    def test_b1_flag_stays_false(self, engine):
        """``is_chat_lane_active()`` stays False when the kill-switch
        fires — the flag is never set in this path."""
        with _wire_manager(engine, use_worker_pool="false") as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

            assert is_chat_lane_active() is False


class TestShutdownClearsBothSlots:
    """``shutdown_worker_pool`` clears both slots and resets B1 flag."""

    def test_both_slots_none_after_shutdown(self, engine):
        with _wire_manager(engine) as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

            # Sanity: both pools live before shutdown.
            assert manager._worker_pool is not None
            assert manager._chat_worker_pool is not None

            manager.shutdown_worker_pool()

            # Both slots cleared (Task #5 — B2 ordering: snapshot
            # taken BEFORE stop, slots None'd DURING iteration).
            assert manager._worker_pool is None
            assert manager._chat_worker_pool is None
            # _pools emptied post-teardown — the helper is a no-op
            # for any late notify.
            assert manager._pools == []

    def test_b1_flag_flipped_false_after_shutdown(self, engine):
        """``is_chat_lane_active()`` returns False AFTER
        ``shutdown_worker_pool`` — fail-open restored (B1)."""
        with _wire_manager(engine) as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)
            assert is_chat_lane_active() is True

            manager.shutdown_worker_pool()

            assert is_chat_lane_active() is False


class TestShutdownIsIdempotent:
    """Calling ``shutdown_worker_pool`` twice does not raise — the
    early-return path (already-None slots) is exercised."""

    def test_double_shutdown_is_safe(self, engine):
        with _wire_manager(engine) as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)
            manager.shutdown_worker_pool()

            # Second call — all slots already None, list empty.
            # Must not raise.
            manager.shutdown_worker_pool()

            assert manager._worker_pool is None
            assert manager._chat_worker_pool is None
            assert manager._pools == []


# ---------------------------------------------------------------------------
# B1 conditional fail-open (approver S4 — home = this file)
# ---------------------------------------------------------------------------


class TestB1ConditionalFailOpen:
    """Three flag states (B1 — Phase 2 Exit Criterion #10):

    * State 1 — P1 alone (P2 not merged): chat pool absent; default
      lane claims chat rows (fail-open = today's behavior,
      ``_chat_lane_active == False``).
    * State 2 — P1 + P2 (normal): chat pool live; strict two-way;
      default lane excludes chat rows.
    * State 3 — P2 shutdown: chat pool stopped/cleared; default
      lane claims chat rows again (fail-open restored).

    State 1 is exercised by NOT calling ``setup_worker_pool`` (chat
    pool never constructed). State 2 is exercised by the wiring
    test. State 3 is exercised by the shutdown test. The single
    in-test case below pins the STATE TRANSITION
    (P1 alone → P1+P2) — the flag must flip correctly between
    states.
    """

    def test_b1_conditional_fail_open_chat_pool_absent_then_present(
        self, engine
    ):
        """Two-phase transition pin (approver S4):

        Phase (a) — chat pool ABSENT (no setup_worker_pool call):
        ``is_chat_lane_active()`` is False (the module-default),
        so default-lane claims include chat rows.

        Phase (b) — chat pool PRESENT (after setup_worker_pool):
        ``is_chat_lane_active()`` is True, so default-lane claims
        EXCLUDE chat rows (strict two-way per D2).

        Both phases observed in the same test — proves the flag
        is the SINGLE shared source of truth (E2) and that the
        flip is LIVE (not a snapshot).
        """
        # Phase (a) — chat pool absent.
        # NB: a bare InstanceManager __init__ does not call
        # setup_worker_pool; _chat_lane_active is the module
        # default (False). The autouse fixture
        # ``_reset_lane_flag`` already pinned it False.
        assert is_chat_lane_active() is False

        # Phase (b) — chat pool constructed → flag flips True.
        with _wire_manager(engine) as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

            assert is_chat_lane_active() is True

            # The flag must be LIVE — flipping it externally to
            # False simulates a teardown without running
            # shutdown_worker_pool (e.g., a code path that bypasses
            # the manager helper). The next default-lane claim
            # would see False.
            set_chat_lane_active(False)
            assert is_chat_lane_active() is False

            set_chat_lane_active(True)
            assert is_chat_lane_active() is True

        # Phase (c) — chat pool torn down → flag False again
        # (this happened inside ``_wire_manager``'s ``finally`` —
        # the manager.shutdown_worker_pool() call ran).
        assert is_chat_lane_active() is False


class TestNotifyAllPoolsFanOut:
    """Real-manager probe for the D5 fan-out seam.

    Adjudication fix (2026-09-19): the service-module wake sites
    detect the helper via ``manager.__dict__.get("_notify_all_pools")``
    (the production-honest check that differentiates a real
    ``InstanceManager`` from a ``MagicMock`` manager). The class-level
    ``_notify_all_pools`` method is bound to the instance in
    ``__init__`` via ``self._notify_all_pools = self._notify_all_pools``
    so the lookup succeeds on real managers and returns ``None`` on
    Mock managers.

    WITHOUT this fix the helper is only in ``InstanceManager.__dict__``
    (the class dict) — ``manager.__dict__.get(...)`` returns ``None``
    on a REAL instance and every service-site wake silently falls
    through to the legacy ``_worker_pool.notify_work()`` path. The
    chat pool never wakes from the 11 service sites — including
    CRITICAL site #12 (``instance_messaging.py``) which every
    registry-minted chat row traverses. This is exactly the
    ships-silently defect class the D5 census is designed to
    prevent; pinning it here so future regressions surface loudly
    in CI.

    The two tests below prove:
      (b) ``'_notify_all_pools' in manager.__dict__`` AND the value
          is a callable bound method on a REAL manager (and ``None``
          on a Mock manager).
      (c) The CRITICAL ``instance_messaging.py`` service site, when
          invoked on a real manager, reaches the chat pool through
          the helper (verified by recording the per-pool
          ``notifications_sent`` counter across the helper call —
          both pools increment; Mock managers fall through to legacy
          and only the default pool increments).
    """

    def test_real_manager_helper_in_instance_dict_and_callable(self, engine):
        """(b) — probe 1: ``'_notify_all_pools' in manager.__dict__`` and
        the value is a callable bound method on a REAL manager."""
        with _wire_manager(engine) as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

            assert "_notify_all_pools" in manager.__dict__, (
                "Service-site guard `manager.__dict__.get('_notify_all_pools')` "
                "returns None on a real manager — the chat pool fan-out via the "
                "helper is BROKEN (silent fallback to legacy _worker_pool.notify_work "
                "wakes only the default pool, not the chat pool). This is the "
                "BLOCKER-class defect the D5 census is designed to prevent. The "
                "fix is `self._notify_all_pools = self._notify_all_pools` in "
                "InstanceManager.__init__ so the bound method is an instance "
                "attribute (and therefore in manager.__dict__)."
            )
            helper = manager.__dict__["_notify_all_pools"]
            assert callable(helper), (
                f"_notify_all_pools in __dict__ is not callable: {helper!r}"
            )

    def test_real_manager_helper_wakes_both_pools(self, engine):
        """(c) — probe 2: invoking the helper wakes BOTH pools
        (``notifications_sent`` counter increments on the default
        AND the chat pool). Mock managers fall through to legacy
        and only the default pool increments — pinned by the same
        mechanism that distinguishes real from Mock in production
        fan-out."""
        with _wire_manager(engine) as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

            default_before = manager._worker_pool._stats[
                "notifications_sent"
            ]
            chat_before = manager._chat_worker_pool._stats[
                "notifications_sent"
            ]

            manager._notify_all_pools()

            assert (
                manager._worker_pool._stats["notifications_sent"]
                == default_before + 1
            ), (
                "Default pool's notifications_sent did not increment after "
                "the helper call — the fan-out helper is not invoking "
                "notify_work on the default pool."
            )
            assert (
                manager._chat_worker_pool._stats["notifications_sent"]
                == chat_before + 1
            ), (
                "Chat pool's notifications_sent did not increment after "
                "the helper call — the fan-out helper is not invoking "
                "notify_work on the chat pool. CRITICAL: every registry-"
                "minted chat row traverses instance_messaging.py wake "
                "site #12; if the helper does not wake the chat pool "
                "from that seam, chat messages ride the 3s poll fallback."
            )

    def test_real_manager_service_site_guard_finds_helper(self, engine):
        """Service-site lookup pattern (verbatim from
        ``daemon/services/instance_messaging.py:2100-2101``) returns
        the helper on a real manager. This is the EXACT pattern
        CRITICAL site #12 uses to find the helper — without the
        instance-bound fix the lookup returns ``None``."""
        with _wire_manager(engine) as manager:
            manager.setup_worker_pool(num_workers=WORKER_POOL_SIZE)

            # Verbatim service-site lookup.
            manager_dict = getattr(manager, "__dict__", {})
            notify_pools = manager_dict.get("_notify_all_pools")

            assert notify_pools is not None, (
                "Service-site __dict__ guard returns None on a real manager "
                "— the 11 service-module wake sites (incl. CRITICAL "
                "instance_messaging.py site #12) silently fall through to "
                "legacy _worker_pool.notify_work(). The fix is the "
                "instance-bound `self._notify_all_pools = self._notify_all_pools` "
                "in InstanceManager.__init__ so the bound method is in "
                "manager.__dict__."
            )
            assert callable(notify_pools)

    def test_mock_manager_helper_lookup_returns_none(self, engine):
        """Mirror probe: on a MagicMock manager the lookup returns
        ``None`` — preserves backward-compat with the existing test
        fixtures that build ``MagicMock(spec=...)`` managers (they
        fall through to the legacy ``_worker_pool.notify_work()``
        path; their ``pool.notify_work`` Mock assertion stays
        green)."""
        from unittest.mock import MagicMock

        mm = MagicMock()
        manager_dict = getattr(mm, "__dict__", {})
        notify_pools = manager_dict.get("_notify_all_pools")

        assert notify_pools is None, (
            "Mock manager's __dict__ lookup returned a non-None value — "
            "the guard lost its backward-compat with MagicMock fixtures. "
            "Production-honest check should return None for Mock managers."
        )
