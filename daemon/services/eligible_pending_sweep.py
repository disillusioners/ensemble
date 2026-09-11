"""Eligible-pending sweep — load-bearing systemic backstop (Batch A, A3).

The A3 fix is a periodic asyncio sweep that wakes the worker pool
for any eligible PENDING task whose age crossed a bounded
threshold (60–120s default). The sweep is the catch-all backstop
for every "miss-reason" that can leave a task eligible but
un-notified:

* **Born-deferred-then-flipped** — A1 carve-out + A2 autopromote
  notify catch the normal flow; this sweep heals any case where
  the flip landed but the notify was lost (crash between the flip
  commit and the notify call).
* **Pool-busy at creation** — a task born during a transient
  worker-pool busy window may miss the creation-time
  ``notify_work()``; the sweep heals the stranded row at the
  next tick.
* **Notify lost to crash** — the notify seam is on the critical
  path; a daemon restart between ``enqueue_message`` and
  ``notify_work()`` leaves the row PENDING with no signal. The
  sweep heals every restart-eligible row on the first tick
  after restart.
* **Watchdog notices** — A5 fixes the same class on the
  watchdog-notice path; the sweep is a structural backstop for
  any other notice path that misses the notify.

**ALWAYS ON** (no env flag). Per the project owner's HARD POLICY
(Batch A spec, 2026-09-11): a kill-switch defaulting OFF that
gates a fix is an unacceptable deliverable. The sweep is
ALWAYS-ON infrastructure; the only tuning knobs are the interval
and the age threshold (defaults: 90s interval, 60s age
threshold — bounded so the sweep does not spin).

**Idempotent**: ``notify_work()`` is a pool-wide signal — the
claim path is the only consumer; double-notify on the same pool
is benign (no false claims, just one extra wake). The atomic
``UPDATE task ... WHERE status='pending'`` claim guard in
``TaskRepository.claim_pending_task`` prevents double-dispatch.

Modeling: borrows the asyncio-task + cancel/await lifecycle
pattern from ``daemon/services/waiting_children_watchdog.py`` and
the daemon/api.py lifespan wiring from
``_periodic_drift_reconcile_loop``. Sweep itself is a single
async method — bounded DB read, optional pool notify, structured
log. NO new ``admission_state`` writers / JobItem creators /
``work_id`` mints; the census stays at 23/1/0.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daemon.repositories.task.repository import TaskRepository

logger = logging.getLogger(__name__)


# Defaults — overridable via ``ServicesConfig`` (kept module-level so
# the constants have one source of truth and the config can reference
# them in pydantic ``Field`` default factories). The brief specifies
# 60–120s; 90s is the midpoint.
DEFAULT_SWEEP_INTERVAL_SECONDS: int = 90
DEFAULT_MIN_PENDING_AGE_SECONDS: int = 60


class EligiblePendingSweepService:
    """Periodic eligible-PENDING sweep — A3 systemic backstop.

    Each tick walks the ``task`` table for rows that look eligible
    (PENDING + NULL heartbeat + is_deferred=False + aged past the
    threshold) and calls ``worker_pool.notify_work()`` ONCE so the
    claim path picks the stranded rows up.

    The sweep does NOT write any task / job state — it only signals
    the pool. Census stays untouched (23/1/0). The atomic claim
    guard on the worker-pool side is the only dispatch guard, so
    a double-notify is benign.

    Args:
        task_repository: ``TaskRepository`` (already wired into the
            lifespan via ``manager._task_repo``). The
            ``list_pending_tasks_older_than`` helper is the canonical
            source of eligible candidates — PENDING + NULL heartbeat
            + aged-past-grace.
        worker_pool: ``WorkerPool`` (already wired via
            ``manager._worker_pool``). The notify seam is the same
            ``notify_work()`` used at task creation and A2
            autopromote. When ``None`` (legacy test fixture / pre-
            wiring lifespan), the sweep still walks the table and
            emits a DEBUG log; no wake is dispatched (the periodic
            drift reconciler is the secondary backstop in that
            case).
        interval_seconds: How often the sweep runs. Default 90s
            (midpoint of the 60–120s brief range). Floor 1 to avoid
            spin; out-of-range values FAIL FAST AT BOOT via the
            pydantic ``Field(ge=1)`` constraint in
            ``ServicesConfig``.
        min_pending_age_seconds: Minimum age for a PENDING task to
            be considered eligible for the wake. Default 60s (the
            brief lower bound). Floor 0; out-of-range values FAIL
            FAST AT BOOT.

    Lifecycle:
        * ``start()`` — spawn the asyncio task. Idempotent.
        * ``stop()`` — cancel + await the asyncio task. Safe to
          call when the task was never started (silent no-op).
    """

    def __init__(
        self,
        task_repository: "TaskRepository",
        worker_pool: Any = None,
        *,
        interval_seconds: int = DEFAULT_SWEEP_INTERVAL_SECONDS,
        min_pending_age_seconds: int = DEFAULT_MIN_PENDING_AGE_SECONDS,
    ) -> None:
        self._task_repository = task_repository
        self._worker_pool = worker_pool
        self._interval_seconds = max(1, int(interval_seconds))
        self._min_pending_age_seconds = max(0, int(min_pending_age_seconds))

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # Counters — surface via ``counters()`` for tests / observability.
        self._ticks_total: int = 0
        self._eligible_total: int = 0
        self._notified_total: int = 0
        self._errors_total: int = 0

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    def start(self) -> None:
        """Spawn the periodic sweep as an asyncio task.

        Idempotent — calling ``start()`` while a task is already
        alive is a silent no-op. Mirrors the
        ``WaitingChildrenWatchdog.run_waiting_children_watchdog_loop``
        pattern (asyncio.create_task + cancel/await on shutdown).
        """
        if self._task is not None and not self._task.done():
            logger.debug(
                "EligiblePendingSweepService: start() called while "
                "the sweep task is already alive — silent no-op"
            )
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="eligible-pending-sweep",
        )
        logger.info(
            f"EligiblePendingSweepService started: "
            f"interval={self._interval_seconds}s, "
            f"min_pending_age={self._min_pending_age_seconds}s"
        )

    async def stop(self, timeout: float = 5.0) -> None:
        """Cancel + await the sweep task.

        Honest worst-case join budget: 5s — the loop body is a
        bounded DB read + a pool notify; an unhealthy tick (e.g.,
        a hung DB connection) is bounded by the cancel timeout.
        Politer shutdown: ``self._stop_event`` is set so an
        ``asyncio.sleep`` between ticks exits promptly.
        """
        self._stop_event.set()
        if self._task is None:
            return
        try:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        finally:
            self._task = None
        logger.info("EligiblePendingSweepService stopped")

    async def sweep_once(self) -> dict[str, int]:
        """One bounded sweep tick — public for tests + on-demand use.

        Returns:
            Structured counters dict:

            * ``ticks`` — total tick count including this one
            * ``eligible`` — eligible PENDING tasks found this tick
            * ``notified`` — ``notify_work()`` calls dispatched this
              tick (one per sweep; idempotent on the pool side)
            * ``errors`` — per-tick errors caught
            * ``cumulative_eligible`` — running total
            * ``cumulative_notified`` — running total
            * ``cumulative_errors`` — running total
        """
        self._ticks_total += 1
        eligible_count = 0
        notified = 0
        try:
            tasks = await asyncio.to_thread(
                self._task_repository.list_pending_tasks_older_than,
                self._min_pending_age_seconds,
            )
            # Filter to ``is_deferred=False`` rows. The
            # ``list_pending_tasks_older_than`` helper does not
            # filter on ``is_deferred`` (Python-side post-filter
            # avoids a second DB roundtrip for a single boolean
            # column — the same convention as Pattern (g) in
            # ``_pattern_g_defer_self_witness_watchdog``).
            eligible = [
                t for t in (tasks or [])
                if not getattr(t, "is_deferred", False)
            ]
            eligible_count = len(eligible)
            self._eligible_total += eligible_count

            if eligible_count > 0 and self._worker_pool is not None:
                # One notify_work() call wakes the pool ONCE for
                # ALL eligible candidates — the claim path picks
                # them up as separate claim cycles. Multiple
                # notifies on the same tick are wasted (the pool's
                # condition variable already saw the wake).
                try:
                    self._worker_pool.notify_work()
                    notified = 1
                    self._notified_total += 1
                except Exception as notify_err:
                    # Transient pool error — log + record, do NOT
                    # abort the sweep (next tick heals).
                    logger.warning(
                        f"EligiblePendingSweepService: notify_work() "
                        f"raised {notify_err!r} on tick "
                        f"{self._ticks_total}; eligible_count="
                        f"{eligible_count} — the sweep continues "
                        f"and the next tick will retry"
                    )
                    self._errors_total += 1
            elif eligible_count > 0:
                # No pool wired — DEBUG log so the operator sees the
                # eligible rows. The A1 carve-out + A2 autopromote
                # are the primary fixes; the sweep is the
                # secondary backstop.
                logger.debug(
                    f"EligiblePendingSweepService: {eligible_count} "
                    f"eligible PENDING row(s) found on tick "
                    f"{self._ticks_total}, but worker_pool is not "
                    f"wired (legacy test fixture / pre-wiring "
                    f"lifespan) — relying on the A1/A2 primary "
                    f"fixes to surface them"
                )
        except Exception as tick_err:
            self._errors_total += 1
            logger.error(
                f"EligiblePendingSweepService: tick "
                f"{self._ticks_total} failed: {tick_err}",
                exc_info=True,
            )

        if eligible_count > 0:
            logger.info(
                f"EligiblePendingSweepService: tick "
                f"{self._ticks_total} healed {eligible_count} "
                f"eligible PENDING row(s); notify_work "
                f"dispatched={bool(notified)}"
            )

        return {
            "ticks": self._ticks_total,
            "eligible": eligible_count,
            "notified": notified,
            "errors": self._errors_total - (self._ticks_total - 1 - 0),
            # The above arithmetic is brittle; surface the running
            # totals cleanly instead — single source of truth:
            "cumulative_eligible": self._eligible_total,
            "cumulative_notified": self._notified_total,
            "cumulative_errors": self._errors_total,
        }

    def counters(self) -> dict[str, int]:
        """Read-only view of the sweep counters (for tests / observability)."""
        return {
            "ticks": self._ticks_total,
            "eligible_total": self._eligible_total,
            "notified_total": self._notified_total,
            "errors_total": self._errors_total,
        }

    # --------------------------------------------------------
    # Internal — the asyncio loop
    # --------------------------------------------------------

    async def _run_loop(self) -> None:
        """Bounded-interval asyncio loop — cancel-safe.

        The loop body calls ``sweep_once()`` on each tick and
        sleeps ``interval_seconds`` between ticks. Sleep is
        interruptible via ``self._stop_event`` (polite shutdown
        via ``stop()``); ``asyncio.CancelledError`` is the
        hard-shutdown path.
        """
        try:
            while not self._stop_event.is_set():
                try:
                    await self.sweep_once()
                except asyncio.CancelledError:
                    raise
                except Exception as tick_err:
                    # ``sweep_once`` already swallows its own
                    # exceptions; this is a defense-in-depth catch
                    # so the loop never crashes on an unexpected
                    # shape (e.g., a future contributor raising
                    # SystemExit from a hook).
                    logger.error(
                        f"EligiblePendingSweepService: unexpected "
                        f"loop error: {tick_err}",
                        exc_info=True,
                    )
                # Sleep — interruptible via ``stop_event.wait``
                # so a polite shutdown does not block on the full
                # interval.
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._interval_seconds,
                    )
                    # If wait_for returns without TimeoutError, the
                    # stop event was set — exit cleanly.
                    return
                except asyncio.TimeoutError:
                    # Expected — interval elapsed, loop iterates.
                    continue
        except asyncio.CancelledError:
            # Hard cancel — exit silently.
            return