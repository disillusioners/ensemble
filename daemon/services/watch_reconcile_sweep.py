"""Watch-reconcile periodic sweep — U1-SLICE FIX (2026-09-27).

U1-SLICE FIX (incident c7f59aaf, mission 538e2f59, held-
mission_terminal starvation). The natural notify path
(:func:`daemon.services.work_notifier.notify_work_watchers`)
HELD a ``mission_terminal`` watcher row when the underlying work
was already terminal but the MISSION was still live (the C1
partition routes the row into ``held_for_mission`` /
``matching_readonly`` to survive until the mission flips
terminal). The mission-terminal event would normally arrive via
the parent instance lifecycle event — which it does — but
nothing in the natural path says "mission became terminal →
re-evaluate held rows for that mission's parent instance".
Only the boot-time ``JobQueueService.reconcile_terminal_watches``
sweep did that re-evaluation, leaving an in-session delivery gap
that was unbounded for a daemon that never restarted.

This service is the STRUCTURAL BACKSTOP. The event hooks in
``JobFeedbackObserver`` (the
``_fire_watcher_notify_for_terminal`` end-of-method seam and the
``_finalize_job`` post-commit-outbox seam) are the IN-SESSION
FAST-PATH; this periodic sweep catches the 310ms parent-
lifecycle-completed vs last-child-settle race the live evidence
documented, plus the long-tail case where an instance flips
terminal WITHOUT triggering either of the two hook arms (the
``_process_event`` early-return for TERMINATED / FAILED before
they reach ``_finalize_job`` post-commit outbox).

The service is intentionally a FOCUSED small wrapper around the
``JobQueueService.reconcile_held_watches_for_instance`` helper.
It has one job (call ``reconcile_held_watches_for_instance(instance_id=None)``
on a bounded interval), zero new writers / no new schema, and
the same shutdown contract as ``JobLockSweepService``.

**ALWAYS ON** (no env flag). Per the project owner's HARD POLICY
(Batch A spec, 2026-09-11, codified in
``daemon/services/job_lock_sweep.py``): a kill-switch defaulting
OFF that gates a fix is an unacceptable deliverable. The sweep
is ALWAYS-ON infrastructure; the only tuning knob is the
interval (``watch_reconcile_sweep_interval_seconds`` on
``ServicesConfig``, default 300s — the upper bound the U1
commission mandated: delivery latency =
``min(in-session event, 300s sweep)``).

**Idempotent + safe**: ``reconcile_held_watches_for_instance``
delegates to :meth:`JobQueueService.notify_watchers`, which uses
the existing CAS-claim (``watcher_repo.claim_watchers_for_job_for_instances``)
to enforce exactly-once delivery. A live concurrent caller of
the same path on the same work_id sees ``rowcount == 0`` for
the rows this sweep already claimed; the held rows the
mission-live guard still holds (mission not yet terminal) SURVIVE
this tick and re-evaluate on the next. The zombie-GC leg
(``remove_all_watches_for_job``) is also idempotent — the only
state mutation is a DELETE; a second concurrent call sees
``rowcount == 0``.

Modeling: borrows the asyncio-task + cancel/await lifecycle
pattern from ``daemon/services/job_lock_sweep.py`` (F3). The
loop exits via ``task.cancel()`` + the loop's ``CancelledError``
handler (``_stopping`` is a defensive flag for the rare path
where the loop has not yet entered ``asyncio.sleep``, NOT the
primary exit). The sweep itself is a single async method that
calls the helper — bounded DB read, structured log, NO new
admission-state writers / JobItem creators / ``work_id`` mints;
the census stays untouched.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daemon.services.job_queue_service import JobQueueService

logger = logging.getLogger(__name__)


# Default cadence — the U1 commission's upper-bound guarantee:
# delivery latency = ``min(in-session event, 300s sweep)``. The
# hooks in ``JobFeedbackObserver`` carry the in-session fast path;
# this sweep is the structural backstop for the 310ms race + the
# long-tail case (instance flips terminal without triggering
# either hook arm). 300s = bounded delivery latency without
# spinning the DB. Tuning knob lives on ``ServicesConfig`` so the
# config layer owns the resolution order.
DEFAULT_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS: int = 300


class WatchReconcileSweepService:
    """Periodic held-watcher reconcile + zombie-GC sweep — U1-SLICE FIX.

    Each tick calls
    :meth:`JobQueueService.reconcile_held_watches_for_instance`
    with ``instance_id=None`` (the GLOBAL scan variant). The
    helper consults the canonical
    :func:`daemon.services.mission_live_guard.evaluate_mission_live`
    guard for every held ``mission_terminal`` watcher row in the
    DB, CAS-claims + delivers via the existing
    :meth:`JobQueueService.notify_watchers` path when the mission
    is now terminal, and retires rows whose ``job_id`` no longer
    resolves AND whose mission liveness is terminal or
    unresolvable (the zombie-GC leg — closes the row 538e2f59
    live-evidence class, a held row that survived ≥5 boots with
    its job deleted because the pre-fix reconcile path
    short-circuited on ``resolve_work is None``).

    The sweep does NOT touch the ``admission_state`` of any
    JobItem, NOR does it mint ``work_id``-shaped handles, NOR
    does it publish notification events directly — it routes
    through the existing ``notify_work_watchers`` partition so
    the ``[JOB_EVENT]`` format + ``source`` prefix stay
    byte-for-byte identical to the natural notify path
    (orchestrator parser contract).

    Args:
        job_queue_service: ``JobQueueService`` (already wired
            into the daemon via ``manager._job_queue_service``
            at lifespan boot). The service MUST have
            ``_watcher_repo`` + ``_instance_manager`` wired or
            the helper returns the zero-counts dict on every
            tick (silent no-op — the natural notify path is the
            primary carrier; this sweep only closes the gap).
        interval_seconds: How often the sweep runs. Default
            300s (U1 commission upper-bound). Floor 1s to
            avoid spin; out-of-range values FAIL FAST AT BOOT
            via the pydantic ``Field(ge=1)`` constraint in
            ``ServicesConfig``.

    Lifecycle:
        * ``start()`` — spawn the asyncio task. Idempotent.
        * ``stop()`` — cancel + await the asyncio task. Safe
          to call when the task was never started (silent
          no-op).
    """

    def __init__(
        self,
        job_queue_service: "JobQueueService",
        *,
        interval_seconds: int = DEFAULT_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self._job_queue_service = job_queue_service
        self._interval_seconds = max(1, int(interval_seconds))
        self._task: asyncio.Task[None] | None = None
        self._stopping: bool = False
        # Counters for the sweep — useful for tests + observability.
        # Mirrors the ``OrphanWatcherSweepService.counters()`` shape.
        self._ticks_total: int = 0
        self._fired_total: int = 0
        self._retired_total: int = 0
        self._errors_total: int = 0

    @property
    def interval_seconds(self) -> int:
        """Current sweep interval (seconds). Read-only."""
        return self._interval_seconds

    def start(self) -> None:
        """Spawn the periodic sweep as an asyncio task.

        Idempotent — a second call while a task is already
        running is a no-op (matches ``JobLockSweepService`` and
        ``EligiblePendingSweepService`` patterns). The
        ``asyncio.sleep`` between ticks exits promptly on
        ``stop()``.
        """
        if self._task is not None and not self._task.done():
            logger.debug(
                f"WatchReconcileSweepService: start() called "
                f"while already running — no-op"
            )
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._run(), name="WatchReconcileSweepService"
        )
        logger.info(
            f"WatchReconcileSweepService started: interval="
            f"{self._interval_seconds}s (default "
            f"{DEFAULT_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS}s)"
        )

    async def stop(self) -> None:
        """Cancel the sweep task and await its cancellation.

        Safe to call when the task was never started — silent
        no-op (matches ``JobLockSweepService.stop``).
        """
        if self._task is None:
            return
        self._stopping = True
        task = self._task
        self._task = None
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            # CancelledError is the normal shutdown path;
            # Exception is a defensive catch in case the task
            # raised something unexpected during shutdown.
            # Either way the sweep is stopped — no re-raise.
            pass
        logger.info("WatchReconcileSweepService stopped")

    async def sweep_once(self) -> dict[str, int]:
        """Run a single held-watcher reconcile + zombie-GC tick.

        Public entry point so tests can exercise a single tick
        deterministically without spawning the asyncio task.

        Returns:
            Structured counters dict mirroring the
            ``reconcile_held_watches_for_instance`` return shape
            (plus cumulative fields):

            * ``fired`` — watchers CAS-claimed + delivered this tick
            * ``retired`` — zombie rows removed this tick
            * ``scanned`` — unique work_ids visited this tick
            * ``cumulative_fired`` — running total
            * ``cumulative_retired`` — running total
            * ``cumulative_errors`` — running total
        """
        self._ticks_total += 1
        fired = 0
        retired = 0
        scanned = 0
        try:
            result = (
                await self._job_queue_service
                .reconcile_held_watches_for_instance(
                    instance_id=None,
                )
            )
            fired = int(result.get("fired", 0))
            retired = int(result.get("retired", 0))
            scanned = int(result.get("scanned", 0))
            self._fired_total += fired
            self._retired_total += retired
        except Exception as sweep_err:  # noqa: BLE001
            # Defensive: a transient DB error or a helper
            # glitch must not crash the sweep loop. Log and
            # return the zero-counts shape — the next tick will
            # retry. Mirrors the ``JobLockSweepService`` and
            # ``OrphanWatcherSweepService`` fail-closed-on-error
            # pattern.
            self._errors_total += 1
            logger.warning(
                f"WatchReconcileSweepService: tick "
                f"{self._ticks_total} failed: "
                f"{type(sweep_err).__name__}: {sweep_err} — "
                "next tick will retry",
                exc_info=True,
            )

        if fired or retired:
            logger.info(
                f"WatchReconcileSweepService: tick "
                f"{self._ticks_total} scanned={scanned} "
                f"fired={fired} retired={retired} "
                f"(cumulative_fired={self._fired_total}, "
                f"cumulative_retired={self._retired_total})"
            )

        return {
            "fired": fired,
            "retired": retired,
            "scanned": scanned,
            "cumulative_fired": self._fired_total,
            "cumulative_retired": self._retired_total,
            "cumulative_errors": self._errors_total,
        }

    def counters(self) -> dict[str, int]:
        """Read-only view of the sweep counters (tests + observability)."""
        return {
            "ticks": self._ticks_total,
            "fired_total": self._fired_total,
            "retired_total": self._retired_total,
            "errors_total": self._errors_total,
        }

    async def _run(self) -> None:
        """Periodic tick loop — exits on ``stop()`` cancellation.

        Sleeps ``interval_seconds`` between ticks;
        ``asyncio.sleep`` raises ``CancelledError`` promptly when
        ``stop()`` invites the task via ``task.cancel()``. The
        loop does NOT use ``EligiblePendingSweepService``'s
        ``asyncio.Event`` ``stop_event``-driven early-exit
        pattern — the ``_stopping`` flag is only a defensive
        guard for the path between ``sweep_once`` return and
        the next ``asyncio.sleep`` call, not the primary exit
        mechanism (mirrors ``JobLockSweepService._run``).
        """
        try:
            while not self._stopping:
                await self.sweep_once()
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            # Normal shutdown path via stop() — exit cleanly.
            return
        except Exception as loop_err:  # noqa: BLE001
            # Defensive: an unexpected loop error must not crash
            # the sweep. Logged; the loop exits so the daemon
            # lifespan can shut down cleanly. (The next sweep
            # service restart — if any — would re-enter via
            # ``start()``; in practice the loop is only stopped
            # via ``stop()``, so this is a belt-and-braces guard.)
            logger.error(
                f"WatchReconcileSweepService: unexpected loop "
                f"error: {loop_err}",
                exc_info=True,
            )
