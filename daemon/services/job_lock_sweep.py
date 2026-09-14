"""JobLock periodic sweep — F3 of the joblock-leak fix.

The F3 fix wires the existing ``JobLockManager.cleanup_terminal_job_locks``
(the periodic variant of ``recover_stale_job_locks``) into a steady-state
asyncio sweep. The method itself has existed since the C12 ship but was
never wired into any periodic service — a previous process that died
mid-execution would have its orphans cleared at the next boot, but the
LIVE daemon (the production 7807e521-class root cause) had no steady-state
backstop. The sweep closes the gap by reclaiming locks orphaned by:

  * F1 Fix-B inline writer races (inline writer wins the SQL guard and
    transitions ``active → done`` BEFORE the bus publishes the terminal
    event — observer ``_finalize_job_db_sync`` never fires, lock leaked
    unless the inline writer's atomic same-commit release (F1) caught
    it).
  * F2 cancellation mid-release (R6 / R7 sites: the previous
    ``except Exception`` missed ``asyncio.CancelledError``; the sweep
    reclaims what those hardened sites log as warnings).
  * F5 ``force_finalize_orphan`` reap (the orphan-reaper writer
    transitions ``active → done`` for an orphan but does NOT release the
    lock atomically — same gap F1 closes inline; the sweep reclaims the
    remaining stragglers).

**F4 pause-contract alignment.** The sweep is also the
"smallest-risk carve-out" for the F4 pause contract — "a paused
instance holds ZERO locks for FINALIZED jobs". The underlying SQL
``DELETE FROM job_locks WHERE job_id NOT IN (active job ids)`` deletes
locks for jobs whose ``admission_state`` is terminal (``done`` /
``dead``) regardless of the parent instance's status. A paused
instance holding a lock for a FINALIZED job is therefore reclaimed on
the next sweep tick — exactly the desired F4 contract. The
2026-08-24 paused-race guard lives on the
``reconcile_turn_mirror`` path
(``daemon/repositories/task/repository.py:1096-1113``, gated on
``instance.status IN (waiting_children, paused, running)``) which
runs ONLY during resume races — a different code path that this
sweep does NOT touch. The two paths are complementary: this sweep
reclaims for FINALIZED jobs in any instance state; the
``reconcile_turn_mirror`` guard protects ACTIVE jobs during the
resume race. No carve-out needed in this sweep.

**ALWAYS ON** (no env flag). Per the project owner's HARD POLICY
(Batch A spec, 2026-09-11): a kill-switch defaulting OFF that gates
a fix is an unacceptable deliverable. The sweep is ALWAYS-ON
infrastructure; the only tuning knob is the interval (default 90s,
shared with A3 cadence). Out-of-range values FAIL FAST AT BOOT via the
pydantic ``Field(ge=1)`` constraint in ``ServicesConfig``.

**Idempotent + safe**: ``clear_terminal_job_locks`` is a single atomic
``DELETE FROM job_locks WHERE job_id NOT IN (active job ids)``. A live
job whose lock is reclaimed would be one whose JobItem already moved to
a terminal state — a bug, not normal traffic — so a stray race is
benign (the worker would re-claim on the next tick). The 2026-08-24
paused-race guard lives on ``reconcile_turn_mirror`` (a different path
that fires only on resume races), NOT on this sweep — see
``daemon/repositories/task/repository.py:1096-1113``. This sweep
deletes ONLY for jobs whose ``admission_state`` is terminal, so a
paused instance holding a lock for a still-ACTIVE job is NEVER
touched.

Modeling: borrows the asyncio-task + cancel/await lifecycle pattern
from ``daemon/services/eligible_pending_sweep.py`` (A3) and the
daemon/api.py lifespan wiring from the same service. The sweep itself
is a single async method — bounded DB read, structured log, NO new
admission-state writers / JobItem creators / ``work_id`` mints; the
census stays at 23/1/0.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daemon.services.job_lock_manager import JobLockManager

logger = logging.getLogger(__name__)


# Default cadence — shared with A3 (90s). The F3 sweep is a steady-
# state reclaim backstop, not a high-frequency monitor; the boot-time
# ``recover_stale_job_locks`` covers the restart window and the F1/F5
# inline writers cover the hot path. 90s = bounded reclaim latency
# (~90s worst case) without spinning the DB.
DEFAULT_JOB_LOCK_SWEEP_INTERVAL_SECONDS: int = 90


class JobLockSweepService:
    """Periodic job-lock reclaim sweep — F3 systemic backstop.

    Each tick calls ``JobLockManager.cleanup_terminal_job_locks``
    (delegating to ``LockRepository.clear_terminal_job_locks``),
    which DELETEs every ``job_locks`` row whose ``job_id`` is no
    longer in an active (``{queued, active}``) state. The DELETE
    is a single SQL statement with the ``job_id NOT IN (...)``
    guard so it is race-safe against concurrent terminal writers
    (F1 inline / F5 reap) that release the same lock atomically —
    a double-DELETE is benign (second call sees ``rowcount == 0``).

    The sweep does NOT touch the ``admission_state`` of any
    JobItem, nor does it mint ``work_id``-shaped handles — the
    census stays untouched (23/1/0). The atomic DELETE is the
    only state mutation.

    Args:
        job_lock_manager: ``JobLockManager`` (already wired into
            the lifespan via ``manager._job_lock_manager``). The
            ``cleanup_terminal_job_locks`` method has existed
            since C12 and was previously DEAD CODE (no periodic
            caller) — F3 wires it into this sweep.
        interval_seconds: How often the sweep runs. Default 90s
            (shared with A3 cadence). Floor 1 to avoid spin;
            out-of-range values FAIL FAST AT BOOT via the
            pydantic ``Field(ge=1)`` constraint in
            ``ServicesConfig``.

    Lifecycle:
        * ``start()`` — spawn the asyncio task. Idempotent.
        * ``stop()`` — cancel + await the asyncio task. Safe to
          call when the task was never started (silent no-op).
    """

    def __init__(
        self,
        job_lock_manager: "JobLockManager",
        *,
        interval_seconds: int = DEFAULT_JOB_LOCK_SWEEP_INTERVAL_SECONDS,
    ) -> None:
        self._job_lock_manager = job_lock_manager
        self._interval_seconds = max(1, int(interval_seconds))
        self._task: asyncio.Task[None] | None = None
        self._stopping: bool = False

    @property
    def interval_seconds(self) -> int:
        """Current sweep interval (seconds). Read-only."""
        return self._interval_seconds

    def start(self) -> None:
        """Spawn the periodic sweep as an asyncio task.

        Idempotent — a second call while a task is already running
        is a no-op (matches the ``EligiblePendingSweepService``
        pattern). ``asyncio.sleep`` between ticks exits promptly
        on ``stop()``.
        """
        if self._task is not None and not self._task.done():
            logger.debug(
                f"JobLockSweepService: start() called while "
                f"already running — no-op"
            )
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._run(), name="JobLockSweepService"
        )
        logger.info(
            f"JobLockSweepService started: interval="
            f"{self._interval_seconds}s (default "
            f"{DEFAULT_JOB_LOCK_SWEEP_INTERVAL_SECONDS}s)"
        )

    async def stop(self) -> None:
        """Cancel the sweep task and await its cancellation.

        Safe to call when the task was never started — silent
        no-op (matches ``EligiblePendingSweepService.stop``).
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
            # CancelledError is the normal shutdown path; Exception
            # is a defensive catch in case the task raised something
            # unexpected during shutdown. Either way the sweep is
            # stopped — no re-raise.
            pass
        logger.info("JobLockSweepService stopped")

    async def sweep_once(self) -> int:
        """Run a single reclaim tick. Returns the rowcount deleted.

        Public entry point so the daemon/api.py lifespan startup can
        run a synchronous first sweep before the periodic loop takes
        over, and so tests can exercise a single tick without
        spawning the asyncio task.
        """
        try:
            cleared = await self._job_lock_manager.cleanup_terminal_job_locks()
        except Exception as sweep_err:  # noqa: BLE001
            # Defensive: a transient DB error must not crash the
            # sweep loop. Log and return 0 — the next tick will
            # retry. Mirrors the ``OrphanWatcherSweepService``
            # fail-closed-on-error pattern.
            logger.warning(
                f"JobLockSweepService: sweep failed: "
                f"{type(sweep_err).__name__}: {sweep_err} — "
                "next tick will retry",
                exc_info=True,
            )
            return 0
        if cleared:
            logger.info(
                f"JobLockSweepService: reclaimed {cleared} "
                "stale job lock(s) for terminal jobs"
            )
        return cleared

    async def _run(self) -> None:
        """Periodic tick loop — exits on ``stop()`` cancellation.

        Sleeps ``interval_seconds`` between ticks; ``asyncio.sleep``
        raises ``CancelledError`` promptly when ``stop()`` cancels
        the task. Mirrors the ``EligiblePendingSweepService``
        pattern (``daemon/services/eligible_pending_sweep.py:178``).
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
            # the daemon. Log loudly and exit. The next start()
            # (if any) will re-spawn the loop.
            logger.error(
                f"JobLockSweepService: unexpected loop error: "
                f"{type(loop_err).__name__}: {loop_err} — exiting",
                exc_info=True,
            )
            return
