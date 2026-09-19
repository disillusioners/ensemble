"""Pool orchestrator — chat-source-worker-lane Phase B (extraction + consolidation).

Centralizes the worker-pool lifecycle that was previously embedded
inside ``InstanceManager`` (``daemon/manager.py`` lines ~6444-6945):

* :meth:`PoolOrchestrator.setup` — constructs the default + chat
  ``WorkerPool`` instances, populates ``_pools``, flips the
  ``_chat_lane_active`` flag in
  ``daemon/repositories/task/repository.py`` (B1 — flag goes True
  only AFTER both pools have started; the chat pool's
  ``WorkerPool(lane="chat")`` constructor wires the lane-aware
  claim seam per worker).

* :meth:`PoolOrchestrator.shutdown` — B2 + E1 + B1 teardown
  ordering: snapshots pools BEFORE stop/None; iterates the snapshot
  chat-FIRST so the lane flag flips False BEFORE the default pool's
  stop window opens (default resumes claiming chat rows during its
  own stop = fail-open restored, no stranding); clears ``_pools``
  post-teardown; emits hung-worker WARNING for any worker still
  ``is_alive()`` after the per-pool ``stop(30)`` budget.

* :meth:`PoolOrchestrator.notify_all` — fan-out wake pulse across
  every live pool (replaces the singleton-attribute reach
  ``self._worker_pool.notify_work()`` that every wake site used
  pre-Phase-2). Per-entry ``None``-guard + per-pool try/except so a
  single pool's blip does NOT abort the fan-out (A3 sweep is the
  systemic backstop for missed wakes).

* :func:`safe_notify_all_pools` — the single consolidated
  ``Mock-compat __dict__``-probe helper used at every wake site
  (service modules + manager reconcile sites). Replaces the
  ~12×-duplicated per-site pattern (probe + try/except + log + legacy
  fallback) with one helper; each wake site becomes a one-liner.

Historical context
------------------

Phase A (chat-source-worker-lane) shipped the dual-pool architecture
on the original ``feature/chat-source-worker-lane`` branch. Phase B
(chat-lane-followups) is a behavior-preserving refactor:

  * **Extraction** — pull pool lifecycle out of the 11.6k-line
    ``InstanceManager`` into this dedicated module. ``manager.py``
    keeps thin facade methods (``setup_worker_pool``,
    ``shutdown_worker_pool``, ``_notify_all_pools``) with identical
    signatures so zero callers change.

  * **Consolidation** — replace the 11 service wake sites'
    duplicated ``__dict__``-probe + try/except + legacy-fallback
    blocks with single ``safe_notify_all_pools(manager,
    site_label=...)`` calls. The Mock-compat ``__dict__``-probe
    story, the None-guard, the fail-soft try/except shape, and the
    legacy ``_worker_pool.notify_work()`` fallback all live in the
    helper's docstring (one home, not eleven).

  * **Comment hardening** — back-referencing comments on the
    intentional ``__init__`` self-assignment (Mock-hazard defeat:
    methods live on the class, the instance-``__dict__`` probe
    requires explicit binding) and the boot-claims harness barrier
    (why ``wait_default_pool_boot_claims_drained`` exists) so a
    future reader does not "simplify" them away.

The lane-flag STORAGE stays in
``daemon/repositories/task/repository.py``
(``set_chat_lane_active`` / ``is_chat_lane_active`` module state,
read PER-CLAIM at the claim seam — never a construction-time
snapshot). This module moves the SET/CLEAR CALL SITES + ``_pools``
ownership into the orchestrator; the flag storage stays in the
repository where the claim seam reads it. No second truth, no
snapshots.

The Mock-compat ``__dict__`` probe contract
-------------------------------------------

Service sites detect the helper via
``manager.__dict__.get("_notify_all_pools")``. This MUST keep
working through the refactor:

  * On a **real manager** the bound method appears in
    ``manager.__dict__`` (the explicit ``self._notify_all_pools =
    self._notify_all_pools`` self-assignment in ``__init__``
    promotes the method from the class ``__dict__`` to the
    instance ``__dict__`` — without that assignment the lookup
    finds it on the class, not on the instance, and Mock-compat
    tests fail).

  * On a **Mock manager** the lookup yields ``None`` because Mocks
    do NOT auto-populate ``__dict__`` — that is the
    production-honest check that distinguishes a real
    ``InstanceManager`` from a test Mock. The helper falls through
    to ``manager._worker_pool.notify_work()`` for those fixtures.

This is the single class of silent failure that already shipped on
this codebase (the ``__dict__``-probe incident that motivated the
2026-09-19 adjudication fix — every service site silently fell back
to default-pool-only because a class-level helper was invisible to
``manager.__dict__.get()``). See
:func:`safe_notify_all_pools` for the full mechanism narrative.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from daemon.constants import (
    CHAT_SOURCE_PREFIXES,
    CHAT_WORKER_POOL_SIZE,
    WORKER_POOL_SIZE,
)

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# PoolOrchestrator — owner of the worker-pool lifecycle
# ──────────────────────────────────────────────────────────────────────


class PoolOrchestrator:
    """Owns the worker-pool lifecycle on behalf of ``InstanceManager``.

    The orchestrator holds the canonical ``_pools`` list (the wake
    fan-out target list — D5 list-shape, future third lane is one
    list entry). The owning manager keeps a list-reference alias at
    ``manager._pools`` for back-compat (test reads at
    ``tests/integration/test_chat_source_shutdown.py:180`` and
    ``tests/integration/test_chat_source_pool_wiring.py:169-273``).

    ``_pools`` is initialized to ``[]`` in :meth:`__init__` so any
    early wake that touches ``_pools`` before ``setup`` runs cannot
    crash on a missing attribute — the E3 boot-window guard. Wake
    helper iteration over an empty list is a clean no-op.

    Args:
        manager: The owning ``InstanceManager``. The orchestrator
            reads the manager's engine, repositories, loop,
            callbacks, and config-services to build the worker pools.
            The manager is stored by reference (not deep-copied) so
            state changes on the manager (e.g., ``_worker_pool``,
            ``_chat_worker_pool``) propagate to subsequent reads.
    """

    def __init__(self, manager: "InstanceManager") -> None:
        self._manager = manager
        # E3 boot-window guard: empty list so any early wake that
        # reaches ``_pools`` is a no-op. Populated in ``setup``
        # AFTER both pools construct; cleared in ``shutdown``.
        self._pools: list[Any] = []

    @property
    def pools(self) -> list[Any]:
        """Return the canonical ``_pools`` list (read-only view)."""
        return self._pools

    # ── setup ─────────────────────────────────────────────────────

    def setup(self, num_workers: int = WORKER_POOL_SIZE) -> None:
        """Construct the default + chat ``WorkerPool`` instances.

        Mirrors the previous ``InstanceManager.setup_worker_pool``
        body (extracted verbatim — same imports, same order, same
        side effects). B1 ordering invariants are preserved:

          1. Construction of both pools.
          2. ``self._pools`` populated AFTER both pools construct
             (D5 list-shape — future third lane is one list entry).
          3. ``set_chat_lane_active(True)`` AFTER ``_pools``
             populate so the very next notify uses the new helper,
             AND AFTER both pool starts so a chat pool construction
             failure (e.g., port-in-use) leaves the flag False
             (fail-open).

        Args:
            num_workers: Number of default-pool worker threads. The
                chat pool is sized by ``CHAT_WORKER_POOL_SIZE`` (a
                hardcoded constant; the chat-source-worker-lane
                architecture does NOT take a chat-pool size kwarg
                here — bumping it requires editing the constant and
                rebuilding, same activation discipline as
                ``WORKER_POOL_SIZE`` itself).
        """
        # Check feature flag from environment — the kill-switch.
        # Both pools gate on USE_WORKER_POOL=false; setting the flag
        # alone (without building the pools) keeps the lane predicate
        # fail-open.
        env_flag = os.environ.get("USE_WORKER_POOL", "").lower()
        if env_flag in ("false", "0", "no"):
            logger.info("Worker pool disabled (USE_WORKER_POOL=false)")
            return

        # Local imports — keep module-load cheap and avoid
        # circular-import pressure (worker_pool, task_processor,
        # stale_task_recovery all import manager-related types).
        from .main_loop_bridge import MainLoopBridge
        from .worker_pool import WorkerPool
        from .task_processor import TaskProcessor
        from .stale_task_recovery import StaleTaskRecovery
        from daemon.repositories.task.repository import (
            TaskRepository,
            set_chat_lane_active,
        )
        from daemon.repositories.event.repository import EventRepository

        manager = self._manager

        # Set the main loop reference for thread-safe async calls.
        if manager._loop is not None:
            MainLoopBridge.set_loop(manager._loop)

        # Create repositories (use the manager's existing engine).
        task_repo = TaskRepository(
            engine=manager._engine,
            on_pending_task=lambda: manager._notify_all_pools(),
        )
        # Expose on the manager so cross-dispatcher handlers
        # (``MessageJobHandler._find_running_task_for_instance``)
        # can read the repo without reaching into a private local.
        manager._task_repo = task_repo
        # Wire the maintenance service's task repository here (after
        # ``self._task_repo`` is assigned) so the shared idle probe
        # can use ``TaskRepository.has_active_non_deferred_work``.
        # Calling this in ``initialize()`` would crash because
        # ``self._task_repo`` is only assigned later in
        # ``setup_worker_pool()`` per daemon/api.py startup order.
        manager._maintenance_service.set_task_repository(manager._task_repo)
        event_repo = EventRepository(engine=manager._engine)

        # Backfill last_heartbeat_at for any RUNNING tasks that lack
        # one (legacy rows or in-flight tasks surviving a restart).
        # Without this, the recovery service would flag every
        # surviving RUNNING task as stale within
        # ``stale_task_recovery_threshold_minutes`` of the new
        # deploy. Best-effort: the recovery predicate falls back to
        # ``started_at``, so a failed backfill is a recoverable
        # degraded state, not a crash.
        try:
            backfilled = task_repo.backfill_heartbeats()
            if backfilled:
                logger.info(
                    f"Backfilled last_heartbeat_at for {backfilled} "
                    f"in-flight tasks"
                )
        except Exception as exc:
            logger.warning(
                f"Startup backfill of last_heartbeat_at failed: {exc}"
            )

        # Shorthand for services config.
        svc = manager.config.services

        # Run startup crash recovery with config values.
        # NOTE: ``threshold_minutes`` is sourced from
        # ``stale_task_recovery_threshold_minutes`` (separate from
        # ``task_timeout_minutes``) so that sibling tasks blocked by
        # Fix B's per-instance guard are unblocked within ~5
        # minutes of a worker crash, not the much longer
        # ``task_timeout_minutes``.
        stale_recovery = StaleTaskRecovery(
            task_repository=task_repo,
            message_repository=manager._queue_repository,
            event_repository=event_repo,
            threshold_minutes=svc.stale_task_recovery_threshold_minutes,
            check_interval_seconds=svc.stale_task_recovery_interval,
            cancel_grace_seconds=svc.stale_task_cancel_grace_seconds,
            max_retries=svc.max_task_retries,
            retry_backoff_base=svc.task_retry_backoff_base,
            retry_backoff_max=svc.task_retry_backoff_max,
            on_task_permanently_failed=(
                manager._on_stale_task_permanent_failure
            ),
            on_task_cancelled_and_retried=(
                manager._on_stale_task_cancelled_and_retried
            ),
            instance_manager=manager,
            usage_limit_window_seconds=svc.usage_limit_window_seconds,
            usage_limit_retry_delays_seconds=(
                svc.usage_limit_retry_delays_seconds
            ),
            usage_limit_retry_jitter_fraction=(
                svc.usage_limit_retry_jitter_fraction
            ),
        )
        # FIX: C3 — Assign BEFORE calling ``recover_on_startup()``
        # so ``_stale_recovery`` is set even if recovery raises.
        manager._stale_recovery = stale_recovery
        stale_recovery.recover_on_startup()
        # FIX: C2 — Start periodic background recovery thread.
        stale_recovery.start()

        # Phase 2 (pause-report-recovery, task 2.5) — wire the
        # periodic ``ReportDeliveryRecoveryService`` AFTER the
        # ``StaleTaskRecovery`` wiring (binding order S-c: the
        # ``_ensure_postgres_columns`` + StaleTaskRecovery pair must
        # complete first so the ``report_injections`` table has its
        # Phase 1 columns + indexes, AND so the
        # ``StaleTaskRecovery`` thread is already running when the
        # sweep's busy-check consults
        # ``task_repo.has_instance_busy``).
        from .report_delivery_recovery import ReportDeliveryRecoveryService

        try:
            manager._report_recovery = ReportDeliveryRecoveryService(
                task_repo=task_repo,
                report_injection_repo=manager._report_injection_repo,
                queue_repo=manager._queue_repository,
                instance_repo=manager._instance_repository,
                manager_ref=manager,
                interval_seconds=(
                    svc.report_delivery_recovery_interval_seconds
                ),
                age_bound_minutes=(
                    svc.report_delivery_recovery_age_bound_minutes
                ),
                batch_cap=svc.report_delivery_recovery_batch_cap,
                recovery_retry_minutes=(
                    svc.report_delivery_recovery_retry_minutes
                ),
                enabled=svc.report_delivery_recovery_enabled,
                lane_deferred=svc.report_delivery_recovery_lane_deferred,
                lane_no_row_backstop=(
                    svc.report_delivery_recovery_lane_no_row_backstop
                ),
                lane_pending_age=(
                    svc.report_delivery_recovery_lane_pending_age
                ),
                lane_recovery_retry=(
                    svc.report_delivery_recovery_lane_recovery_retry
                ),
                lane_orphan=svc.report_delivery_recovery_lane_orphan,
            )
            # Fire-and-forget boot sweep (binding order S-c:
            # ``_ensure_postgres_columns`` is in ``initialize()``,
            # BEFORE this method runs).
            #
            # DEEP-REVIEW FIX (2026-08-20, C2): the boot sweep MUST
            # NOT execute lane bodies on the loop thread. The chain
            # ``api.py:241 lifespan → setup_worker_pool (here) →
            # recover_on_startup → _run_all_lanes_sync`` runs ON the
            # event-loop thread, and
            # ``_handle_recover_deferred_report``
            # (manager.py:6343-6351) calls
            # ``run_coroutine_threadsafe(...).result(timeout=30.0)``
            # per row → self-blocks the loop. Worst case ~30s × 100
            # rows ≈ 50 min blocked startup, HTTP down. This was the
            # THIRD occurrence of the loop-thread-blocking bug class
            # (bcc02b92, 5fe135e3 fixed router/reconcile paths; boot
            # was missed).
            #
            # The fix: schedule the sweep via
            # ``asyncio.to_thread`` + ``loop.create_task``, which
            # moves the lane execution OFF the loop thread. The
            # sweep body is unchanged — it still uses
            # ``run_coroutine_threadsafe(...).result(...)`` to bridge
            # to the loop, but now those bridges are coming FROM a
            # worker thread (correct: the worker thread blocks on
            # ``.result()`` while the loop continues serving HTTP).
            try:
                if manager._loop is not None and not manager._loop.is_closed():
                    # POST-DEEP-REVIEW (Y1, 2026-08-20): attach a
                    # done-callback so a sweep-body exception is
                    # logged instead of being silently dropped into a
                    # garbage-collected task (which would surface
                    # only as "Task exception was never retrieved").
                    # Boot MUST stay non-blocking — the callback
                    # fires when the worker-thread sweep finishes;
                    # the boot caller does not await the task.
                    def _log_boot_sweep_done(
                        t: asyncio.Task,
                        *,
                        _mgr: "InstanceManager" = manager,
                    ) -> None:
                        # Teardown guard: ``stop()`` may have
                        # nulled ``_report_recovery`` before the
                        # callback fires; ``logger`` itself is
                        # module-level and always safe. We only
                        # suppress the noise — the exception is
                        # always retrievable.
                        if t.cancelled():
                            return
                        exc = t.exception()
                        if exc is None:
                            return
                        if getattr(_mgr, "_report_recovery", None) is None:
                            logger.debug(
                                "ReportDeliveryRecoveryService boot "
                                "sweep task failed after manager "
                                f"teardown (suppressed): "
                                f"{type(exc).__name__}: {exc}"
                            )
                            return
                        logger.warning(
                            "ReportDeliveryRecoveryService boot sweep "
                            f"task failed (non-fatal): "
                            f"{type(exc).__name__}: {exc}"
                        )

                    # Fire-and-forget: the loop schedules the work
                    # on a thread-pool worker (``asyncio.to_thread``).
                    # The boot call returns immediately; the sweep
                    # runs concurrently on a worker thread.
                    boot_sweep_task = manager._loop.create_task(
                        asyncio.to_thread(
                            manager._report_recovery.recover_on_startup
                        )
                    )
                    boot_sweep_task.add_done_callback(_log_boot_sweep_done)
                else:
                    # Loop unavailable — fall back to running
                    # synchronously. F7 (2026-08-20): this
                    # sync-fallback path exists ONLY for tests /
                    # contexts that did NOT wire an event loop
                    # (``manager._loop is None or closed``).
                    # The production boot path always has a live
                    # loop (set in ``InstanceManager.initialize()``
                    # via the application startup sequence) and
                    # takes the ``loop.create_task(asyncio.to_thread
                    # (...))`` off-loop dispatch above — the boot
                    # caller returns immediately and the sweep
                    # runs concurrently on a worker thread.
                    #
                    # Keeping this sync branch is DELIBERATE:
                    # removing it would break every test that
                    # builds a service without wiring a loop
                    # (see
                    # ``tests/integration/test_boot_report_recovery.py``
                    # and friends). The branch is safe because
                    # ``recover_on_startup()`` is sync + idempotent
                    # and the boot caller already tolerates a
                    # blocking initial sweep (the boot sequence
                    # itself is the gate, not the sweep timing).
                    logger.info(
                        "ReportDeliveryRecoveryService boot sweep "
                        "running synchronously (no loop available)"
                    )
                    manager._report_recovery.recover_on_startup()
            except Exception as exc:
                logger.warning(
                    f"ReportDeliveryRecoveryService startup sweep "
                    f"failed (non-fatal): {type(exc).__name__}: {exc}"
                )
            # Start the periodic background thread.
            manager._report_recovery.start()
        except Exception as exc:
            # Per the MVP growth rule — recovery service cleanup
            # on shutdown means the wiring failure must be logged
            # but NEVER crash startup.
            logger.error(
                f"ReportDeliveryRecoveryService wiring failed "
                f"(non-fatal): {type(exc).__name__}: {exc}"
            )
            manager._report_recovery = None

        # Execution Gate: stale-lease recovery is performed by the
        # async lifespan in ``daemon/api.py`` BEFORE this method
        # runs, so the very first ``gate.run`` after startup is
        # guaranteed to see a clean state. We deliberately do NOT
        # call the sync wrapper here — it would be fire-and-forget
        # under the running event loop and would leave up to a
        # 5-minute window where the first ``gate.run`` could
        # contend against a stale lease from a crashed prior
        # process.

        # Create task processor with manager reference.
        manager._task_processor = TaskProcessor(
            task_repo=task_repo,
            instance_manager=manager,
            event_repo=event_repo,
            graph_timeout_minutes=svc.graph_timeout_minutes,
            source_dispatcher=manager.source_dispatcher,
        )

        # Create and start the default worker pool with timeout/retry
        # config.
        manager._worker_pool = WorkerPool(
            task_processor=manager._task_processor,
            num_workers=num_workers,
            timeout_minutes=svc.task_timeout_minutes,
            max_retries=svc.max_task_retries,
            retry_backoff_base=svc.task_retry_backoff_base,
            retry_backoff_max=svc.task_retry_backoff_max,
            heartbeat_interval_seconds=svc.task_heartbeat_interval_seconds,
            usage_limit_window_seconds=svc.usage_limit_window_seconds,
            usage_limit_retry_delays_seconds=(
                svc.usage_limit_retry_delays_seconds
            ),
            usage_limit_retry_jitter_fraction=(
                svc.usage_limit_retry_jitter_fraction
            ),
        )
        manager._worker_pool.start()

        # Chat-source worker lane (chat-source-worker-lane, Phase 2
        # Task #2): construct the dedicated chat ``WorkerPool``
        # AFTER the default pool starts (D10.3 boot ordering —
        # default workers are alive and stable before any chat
        # worker can race for shared task rows). The chat pool
        # shares the same ``TaskProcessor`` singleton
        # (``self._task_processor``) and the same engine
        # (``self._engine``) — ``TaskProcessor`` is the single seam
        # to ``TaskRepository.claim_pending_task`` and the lane
        # flows as a per-claim argument (Worker → processor →
        # repository), never processor state.
        #
        # Worker-id prefix ``"chat-worker-"`` distinguishes the
        # chat lane in ``task.worker_id`` lineage (D10.5).
        #
        # B1 — flip strictness ON immediately after start: the
        # ``set_chat_lane_active(True)`` call writes the
        # ``_chat_lane_active`` module flag in
        # ``daemon/repositories/task/repository.py``. From this
        # point, default-lane claims consult the flag and exclude
        # chat-prefix rows (strict two-way per D2). The flag is
        # read PER-CLAIM (E2 — single shared source of truth) so
        # the value is live, not a snapshot. Teardown flips it
        # back to False BEFORE the default pool stops so the
        # default pool resumes claiming chat rows during its own
        # stop window (= fail-open restored, no stranding).
        manager._chat_worker_pool = WorkerPool(
            task_processor=manager._task_processor,
            num_workers=CHAT_WORKER_POOL_SIZE,
            timeout_minutes=svc.task_timeout_minutes,
            max_retries=svc.max_task_retries,
            retry_backoff_base=svc.task_retry_backoff_base,
            retry_backoff_max=svc.task_retry_backoff_max,
            heartbeat_interval_seconds=svc.task_heartbeat_interval_seconds,
            usage_limit_window_seconds=svc.usage_limit_window_seconds,
            usage_limit_retry_delays_seconds=(
                svc.usage_limit_retry_delays_seconds
            ),
            usage_limit_retry_jitter_fraction=(
                svc.usage_limit_retry_jitter_fraction
            ),
            worker_id_prefix="chat-worker-",
            lane="chat",
        )
        manager._chat_worker_pool.start()
        # Populate the wake-fan-out list AFTER both pools construct
        # (D5 list-shape — future third lane is one list entry).
        # ``_pools`` was initialized to ``[]`` in ``__init__`` (E3).
        # We MUTATE the existing list (not rebind) so the
        # ``manager._pools`` alias set up at ``InstanceManager.__init__``
        # (``self._pools: list[WorkerPool] = self._orchestrator.pools``)
        # observes the same object — Python list aliasing.
        self._pools.clear()
        self._pools.extend(
            p for p in (manager._worker_pool, manager._chat_worker_pool)
            if p is not None
        )
        # B1 — flip strictness ON. Must come AFTER start so a chat
        # pool construction failure (e.g. port-in-use) leaves the
        # flag False (fail-open). And must come AFTER ``_pools``
        # populate so the very next notify uses the new helper.
        set_chat_lane_active(True)
        # Boot-line (Phase 2 Task #6 — substring-pinned by
        # ``tests/integration/test_chat_source_boot_line.py::test_chat_pool_started_substring_emitted``
        # + the SC#8 byte-exact literal).
        logger.info(
            f"ChatSourceWorkerPool started: workers={CHAT_WORKER_POOL_SIZE}, "
            f"prefixes={','.join(CHAT_SOURCE_PREFIXES)}"
        )

    # ── shutdown ──────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Shut down the worker pool(s) gracefully (chat-source-worker-lane).

        Teardown ordering (Phase 2 / Task #5 — D10.4 + B2 + B1 + E1):

        1. **B2 — snapshot pools BEFORE any stop/None.** Take
           ``pools_snapshot = [p for p in (self._chat_worker_pool,
           self._worker_pool) if p is not None]`` FIRST so the
           hung-worker WARNING loop iterates LIVE worker refs even
           after the slots are None'd.

        2. **E1 — chat pool FIRST so ``_chat_lane_active`` flips
           False BEFORE the default pool stops.** The snapshot is
           iterated in chat-first order so the B1 fail-open flag
           flips BEFORE the default pool's stop window opens
           (default pool claims chat rows again during its own
           stop window = fail-open restored, no stranding).

        3. **Hung-worker WARNING loop (architect A5.1, reviewer
           N4).** Iterate the snapshot AFTER both pools stop and
           for each worker still ``is_alive()`` after the
           ``stop(30)`` budget, emit a WARNING naming the
           ``worker_id`` so the operator can identify which pool
           hung (default vs chat). **N4 choice: option (i) —
           document the private-attr access.** The WARNING loop
           accesses the private ``_workers`` list on each pool.
           This is encapsulated per pool lifecycle — the manager
           owns the pools and is the only caller — and the
           observability gain outweighs the public-surface bloat
           that option (ii) (``is_any_worker_alive()`` accessor)
           would introduce. Documented here so the next reader
           knows the exception is deliberate, not accidental.

        4. **Clear ``_pools`` post-teardown** so the
           ``_notify_all_pools()`` helper is a no-op for any
           late notify (e.g., a watchdog tick that fires between
           the pool stop and the daemon thread dying). The helper
           iterates ``_pools`` with a None-guard; an empty list
           makes the helper safely a no-op.
        """
        from daemon.repositories.task.repository import set_chat_lane_active

        manager = self._manager

        # Step 1 — B2 snapshot BEFORE any stop/None. The chat pool
        # is listed first so the iteration visits the chat pool
        # BEFORE the default pool (E1).
        pools_snapshot = [
            p for p in (manager._chat_worker_pool, manager._worker_pool)
            if p is not None
        ]

        # Step 2 + 3 — chat-FIRST iteration: flip the B1 fail-open
        # flag BEFORE the default pool stops so the default pool
        # resumes claiming chat rows during its own stop window.
        for pool in pools_snapshot:
            if pool is manager._chat_worker_pool:
                # B1 — flip fail-open BEFORE ``stop()``. Order
                # matters: the default pool must see the False
                # flag from the moment it begins its own stop
                # window (no stranding — chat rows surface via the
                # default lane if the chat workers cannot drain
                # them).
                set_chat_lane_active(False)
            pool.stop(timeout=30.0)
            if pool is manager._chat_worker_pool:
                manager._chat_worker_pool = None
                logger.info("Chat worker pool stopped")
            elif pool is manager._worker_pool:
                manager._worker_pool = None
                logger.info("Worker pool stopped")

        # Step 4 — clear the wake-fan-out list. The helper is a
        # safe no-op on empty ``_pools``. We MUTATE in place (not
        # rebind) so the ``manager._pools`` alias set up at
        # ``InstanceManager.__init__`` observes the same object —
        # Python list aliasing.
        self._pools.clear()

        # Step 5 — hung-worker WARNING (N4 option (i) — accesses
        # private ``_workers``; see method docstring for the
        # deliberate-trade-off rationale). Iterate the SNAPSHOT
        # (B2), not the slots — the slots are already None.
        for pool in pools_snapshot:
            for worker in pool._workers:
                # Per-worker guard: one raise (e.g. a torn-down
                # worker object) must not erase the enumeration of
                # the remaining workers — log-and-continue, same
                # observability style as the WARNING below.
                try:
                    if worker.is_alive():
                        logger.warning(
                            f"Worker {worker.worker_id} still alive "
                            f"after stop(30) — likely blocked in "
                            f"invoke_agent_and_wait "
                            f"(mid-invoke stop not interruptible, "
                            f"see "
                            f"daemon/services/worker_pool.py:1339-1340)"
                        )
                except Exception as enum_err:
                    logger.warning(
                        f"Hung-worker enumeration failed for one "
                        f"worker (non-fatal — continuing with "
                        f"remaining workers): {enum_err}"
                    )

        if manager._stale_recovery is not None:
            manager._stale_recovery.stop()
            manager._stale_recovery = None
            logger.info("Stale task recovery stopped")

        if getattr(manager, "_report_recovery", None) is not None:
            manager._report_recovery.stop()
            manager._report_recovery = None
            logger.info("Report delivery recovery stopped")

    # ── notify_all ────────────────────────────────────────────────

    def notify_all(self) -> None:
        """Fan-out wake pulse to every live worker pool.

        Chat-source-worker-lane, D5 — replaces the
        singleton-attribute reach
        ``self._worker_pool.notify_work() if self._worker_pool
        else None`` at every wake site (manager reconcile sites +
        the 11 service wake sites routed through
        :func:`safe_notify_all_pools`). Iterates ``self._pools``
        (list, not inline 2-tuple) so a future third lane
        (priority, per-tenant) is a one-list-entry change.
        Per-entry ``None``-check satisfies the wake-site
        None-guard mandate (each widened wake site is protected
        even if the helper is reached before a second pool is
        constructed — the helper is a no-op).

        The list is populated in :meth:`setup` AFTER both pools
        construct; ``_pools`` was initialized to ``[]`` in
        :meth:`__init__` (E3 — boot-window guard).
        :meth:`shutdown` clears the list (and the slots) so
        post-teardown notifies are a no-op rather than reaching a
        stopped pool.
        """
        for pool in self._pools:
            if pool is None:
                continue
            try:
                pool.notify_work()
            except Exception as notify_err:
                # Transient pool-side blip — log + skip. The
                # wake-site None-guard mandate also covers the
                # ``notify_work``-side error path: a single
                # pool's exception must NOT abort the fan-out to
                # the remaining pool(s). Mirrors the site-local
                # try/excepts that lived in
                # ``child_reports`` / ``waiting_children_watchdog``
                # / etc. before Phase B consolidation.
                logger.warning(
                    f"_notify_all_pools: pool.notify_work() "
                    f"raised {notify_err!r} — continuing fan-out"
                )


# ──────────────────────────────────────────────────────────────────────
# safe_notify_all_pools — consolidated wake helper
# ──────────────────────────────────────────────────────────────────────


def safe_notify_all_pools(
    manager: Any,
    *,
    site_label: str = "wake_site",
    fallback_pool: Any = None,
) -> Any:
    """Fan-out wake pulse via the manager's bound helper (single seam).

    The Mock-compat ``__dict__``-probe contract (Phase A adjudication,
    2026-09-19) lives here — NOT at every call site. This is the
    single source of truth for the three guarantees the 11 service
    wake sites (and the 6 manager reconcile wake sites) depend on:

    1. **Mock-compat ``__dict__`` probe.** Service sites detect the
       helper via ``manager.__dict__.get("_notify_all_pools")``.
       This MUST keep working across refactors:

         * On a **real manager**, the bound method appears in
           ``manager.__dict__`` thanks to the explicit
           ``self._notify_all_pools = self._notify_all_pools``
           self-assignment in ``InstanceManager.__init__``. Without
           that assignment, the method lives ONLY in
           ``InstanceManager.__dict__`` (the class), and the
           instance-``__dict__`` probe yields ``None`` — a class of
           silent failure that already shipped on this codebase
           (the 2026-09-19 adjudication fix landed the
           self-assignment after the helper was added but before
           it was bound to the instance).

         * On a **Mock manager**, the lookup yields ``None`` because
           ``unittest.mock.Mock`` does NOT auto-populate
           ``__dict__``. That is the production-honest check that
           distinguishes a real ``InstanceManager`` from a test
           Mock. The helper falls through to
           ``manager._worker_pool.notify_work()`` for those
           fixtures (preserves pre-Phase-2 wake-site behavior on
           every test that builds a Mock manager without wiring
           the helper).

       **Do NOT use ``getattr(manager, '_notify_all_pools', None)``
       here.** The Mock auto-attribute would return a Mock object
       (truthy), and every Mock-fixture wake would route through
       the Mock helper instead of the test's own
       ``_worker_pool.notify_work`` Mock the test asserts on. The
       ``__dict__``-check is the only lookup that respects the
       production-honest Mock boundary.

    2. **None-guard.** When the helper is not in the manager's
       ``__dict__`` — pre-Phase-2 fixture, mock manager, or test
       that builds a manager via ``__new__`` without running
       ``__init__`` — the helper is a safe no-op for the
       ``_notify_all_pools`` branch AND falls through to the legacy
       singleton-attribute reach (``manager._worker_pool``) so old
       fixtures stay green. Tests that DO wire
       ``manager._worker_pool = MagicMock()`` keep asserting on
       ``_worker_pool.notify_work.call_count`` etc.

       For sites where the legacy fixture holds the pool on a
       DIFFERENT object than the manager (e.g.
       ``EligiblePendingSweepService._worker_pool`` — the sweep
       stores ``worker_pool=`` directly on itself), pass
       ``fallback_pool=`` so the no-manager branch can still
       dispatch the wake. Sites that hold both (manager-aware
       production + pre-Phase-2 fixture) get covered by the
       manager attribute branch.

    3. **Fail-soft try/except.** Per-pool errors are logged, NOT
       propagated, so a single transient pool-side blip does not
       abort the wake site's primary work. The periodic A3 sweep
       (``EligiblePendingSweepService``) is the systemic backstop
       for missed wakes; ``task.worker_id`` lineage carries the
       pool that missed, so an operator can correlate warnings to
       claim cycles.

    4. **Async-aware return.** The helper returns whatever the
       wake call returned — ``None`` for production sync
       ``WorkerPool.notify_work()``, or the coroutine for test
       fixtures that attach ``AsyncMock`` to ``notify_work``.
       Async callers (the watchdog's wedge-pass,
       ``long_tool_nudge``'s async sites) check the return value
       via ``inspect.iscoroutine`` and ``await`` it as needed.
       Sync callers can ignore the return value (the production
       shape is ``None``).

    Args:
        manager: The owning ``InstanceManager`` (or Mock). May be
            ``None`` — every branch None-guards; the helper is a
            no-op when ``manager is None`` AND ``fallback_pool is
            None``. Probed via
            ``manager.__dict__.get("_notify_all_pools")`` — NEVER
            via ``getattr`` (the Mock auto-attribute hazard).
        site_label: Short identifier prepended to the warning
            message when the helper raises (e.g., ``"child_reports"``,
            ``"instance_messaging"``, ``"[Watchdog]"``,
            ``"reconcile_drift_states"``). Preserves per-site
            diagnostic value — operators can grep the logs to
            find which wake site tripped on a transient blip.
        fallback_pool: Optional pool reference used when the
            manager helper is absent AND ``manager._worker_pool``
            is unset. Sites that hold a pool on a non-manager
            object (e.g., the eligible-pending sweep) pass their
            own ``self._worker_pool`` here. ``None`` means
            "no fallback available" — the helper returns ``None``
            without dispatching.

    Returns:
        The result of the dispatched wake call (typically
        ``None`` for sync production ``notify_work()``, or a
        coroutine for ``AsyncMock`` test fixtures). ``None`` if
        no pool was reachable. Per-pool errors are swallowed
        (logged, not propagated) — the helper returns the
        partial result (``None`` on error, since the exception
        interrupts the wake call's normal completion).
    """
    manager_dict = getattr(manager, "__dict__", {}) or {}
    notify_pools = manager_dict.get("_notify_all_pools")
    if notify_pools is not None:
        try:
            return notify_pools()
        except Exception as notify_err:
            logger.warning(
                f"[{site_label}] _notify_all_pools() raised "
                f"{notify_err!r} — next tick / sweep is the "
                f"systemic backstop"
            )
            return None
    if getattr(manager, "_worker_pool", None) is not None:
        # Pre-Phase-2 manager shape OR legacy test fixture — fall
        # through to the singleton-attribute reach.
        try:
            return manager._worker_pool.notify_work()
        except Exception as notify_err:
            logger.warning(
                f"[{site_label}] legacy _worker_pool.notify_work() "
                f"raised {notify_err!r} — next tick / sweep is the "
                f"systemic backstop"
            )
            return None
    if fallback_pool is not None:
        try:
            return fallback_pool.notify_work()
        except Exception as notify_err:
            logger.warning(
                f"[{site_label}] fallback_pool.notify_work() "
                f"raised {notify_err!r} — next tick / sweep is the "
                f"systemic backstop"
            )
            return None
    return None


__all__ = [
    "PoolOrchestrator",
    "safe_notify_all_pools",
]
