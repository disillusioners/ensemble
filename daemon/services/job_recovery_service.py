"""Startup + periodic drift recovery service for orphaned jobs/tasks.

The dual work-tracking tables (``job_queue_items`` and ``task``) drift
out of sync when:
- A job is admitted but its driving Task is never claimed (P1 pattern).
- A job's Task is force-completed but the JobItem stays ``active`` (F10).
- An instance is paused/terminated but its JobItem stays ``active`` (C2).

``JobRecoveryService.recover_on_startup`` handles the post-crash
cleanup at daemon startup. ``reconcile_drift_states`` is the periodic
counterpart (60s default, bypasses ``_is_idle``) that catches drift
*during* active work — exactly the case ``MaintenanceService._loop``
skips because it gates on ``_is_idle``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from daemon.constants import ALIVE_INSTANCE_STATUSES
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, Decision
from daemon.repositories.task.models import TaskStatus
from daemon.services.dependency_bus import get_dependency_bus
from daemon.services.job_state_machine import InvalidTransitionError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.repositories.job_queue.models import JobItem
    from daemon.repositories.job_queue.repository import JobRepository
    from daemon.repositories.task.repository import TaskRepository
    from daemon.services.job_queue_service import JobQueueService
    from daemon.services.stale_task_recovery import StaleTaskRecovery

logger = logging.getLogger(__name__)

# Terminal instance statuses - instance is no longer active
_TERMINAL_INSTANCE_STATUSES: set[str] = {
    InstanceStatus.COMPLETED.value,
    InstanceStatus.ERROR.value,
    InstanceStatus.TERMINATED.value,
    InstanceStatus.FAILED.value,
}

# Pattern (f2) completed_at age floor — the wall-clock grace between a
# Task's ``completed_at`` stamp and the moment the drift reconciler is
# allowed to finalize the JobItem as DONE. Exists for two reasons:
#
# 1. Mechanism A residue (council REJECT 2026-08-29, Critical #3): the
#    observer's terminal decision stamps ``failed_at`` (:3301-3302 in
#    job_feedback_observer.py) — the marker atomic_retry requires. A
#    bare ``done`` transition that fires in the same wall-clock window
#    forecloses retry. 60s gives the observer enough slack to land its
#    ``failed_at`` stamp (and any sibling atomic_retry chain) BEFORE
#    the reconciler finalizes the JobItem.
#
# 2. Mechanism B (same critical): a waiting_children parent's driving
#    Task COMPLETES at first-turn end (task_processor.py:856-864)
#    while the JobItem is held open SOLELY by observer gates. The 60s
#    floor lets the observer's gate set fully unwind before we
#    finalize — without it, the reconciler routinely DONE-finalizes
#    healthy parents mid-wait and notify_watchers claims/deletes all
#    watchers prematurely (premature completed UP-side, lost
#    DOWN-side).
#
# Module-level named constant (NOT a config knob) — per the brief's
# "no new knobs unless the 60s floor genuinely needs one; prefer a
# module-level named constant" constraint. Operator can override at
# the call site if a deployment demands a tighter floor; the
# reconciler does not expose a tunable.
_F2_COMPLETED_AGE_FLOOR_SECONDS: int = 60

# Pattern (f) instance.created_at guard — closes the W1 mid-mint
# window. The age signal for an active JobItem is ``JobItem.created_at``
# (model has no ``updated_at`` column) but a just-spawned instance can
# still have its Task minted in flight when the reconciler walks the
# candidate set. The same grace threshold used for the JobItem's
# ``created_at`` check applies to ``Instance.created_at`` — the
# reconciler consults BOTH ages with the same threshold so a
# just-spawned instance never matches even if its paired JobItem is
# already past the JobItem-side grace.
# No separate constant — the existing ``threshold`` variable in
# ``_pattern_f_orphan_active_job_recovery`` (computed from
# ``min_orphan_age_seconds``) is reused for the instance-side check.


# Alive instance statuses - hoisted to ``daemon.constants`` (see
# ``daemon/constants.py::ALIVE_INSTANCE_STATUSES`` for the canonical home
# + member documentation). This module imports it as the single source
# of truth; do NOT re-declare it locally — duplicate definitions are a
# silent-divergence hazard on a set that gates drift-cancels.


class _SessionAdapter:
    """Minimal adapter that lets the named-transition ``run(session)``
    interface accept a SQLAlchemy ``Connection`` directly.

    The named transitions (``DeadLetterTurn``, ``AbortTurn``, etc.)
    call ``session.execute(text(...))`` to write their updates.
    The drift sweep Pattern (e) holds a long-lived
    ``self.engine.begin()`` connection so the entire sweep is one
    transaction (atomic SELECT + per-row UPDATE + companion
    DELETE + named transition ``run()``); passing a raw
    ``Connection`` avoids opening a fresh nested SAVEPOINT per
    row.

    The adapter delegates ``execute`` to the underlying connection
    and exposes no other session surface — the named transitions
    used here only touch the ``task`` table via the ``run``
   `` method.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, statement, params=None):
        if params is None:
            return self._conn.execute(statement)
        return self._conn.execute(statement, params)


class JobRecoveryService:
    """Service for recovering orphaned jobs (startup + periodic drift).

    Two recovery surfaces:

    - ``recover_on_startup`` — runs once at daemon startup to clean
      up PROCESSING jobs whose instance was terminal at the moment
      of crash. Iterates every active JobItem and reconciles against
      its instance's liveness.

    - ``reconcile_drift_states`` — periodic (300s default) drift
      reconciler. Catches four classes of dual-table drift that
      ``recover_on_startup`` cannot see because they arise at
      runtime (post-startup):

        (a) ``active`` JobItem + ``pending`` Task with NULL heartbeat
            → P1-pattern deadlock (task never claimed).
        (b) ``done`` JobItem + ``running`` Task → F10 zombie task.
        (c) ``active`` JobItem + instance not alive for extended
            period → stuck processing.
        (d) ``pending`` Task on terminal JobItem → orphan PENDING
            cleanup (safety net for PENDING tasks whose JobItem
            closed but the Task row was never cancelled).

      Schedules independently of ``MaintenanceService._loop`` —
      which is gated on ``_is_idle`` and skips during active work
      — so it runs *precisely* during active work, which is when
      drift appears.
    """

    def __init__(
        self,
        job_repository: "JobRepository",
        lock_repository: "LockRepository",
        instance_repository: "SQLModelInstanceRepository",
        job_queue_service: "JobQueueService | None" = None,
        task_repository: "TaskRepository | None" = None,
        stale_task_recovery: "StaleTaskRecovery | None" = None,
    ) -> None:
        """Initialize the recovery service.

        Args:
            job_repository: Repository for job operations.
            lock_repository: Repository for lock operations.
            instance_repository: Repository for instance operations.
            job_queue_service: Optional JobQueueService for watcher notifications.
            task_repository: Optional TaskRepository for drift queries
                (``list_running_tasks``, ``list_pending_tasks_older_than``).
                When ``None``, drift patterns that require Task-side
                inspection are skipped with a DEBUG log.
            stale_task_recovery: Optional ``StaleTaskRecovery`` for the
                F10 force-complete path (``force_complete_task``) and
                the F5 force-fail path (``fail_task``). When ``None``,
                drift corrections that need Task-side writes are
                skipped with a DEBUG log. Both refs are independent —
                tests can construct the recovery service with only
                ``task_repository`` (F5-only), only ``stale_task_recovery``
                (F10-only), or both (full drift detection).
        """
        self._job_repository = job_repository
        self._lock_repository = lock_repository
        self._instance_repository = instance_repository
        self._job_queue_service = job_queue_service
        # Phase 3 (F5/F10 drift reconciler) — late-wired by the
        # lifespan in ``daemon/api.py`` after both ``TaskRepository``
        # and ``StaleTaskRecovery`` are constructed. ``None`` means
        # "skip drift correction" — production always wires both.
        self._task_repository = task_repository
        self._stale_task_recovery = stale_task_recovery

    def _is_instance_alive(self, instance_status: str | None) -> bool:
        """Check if an instance status indicates the instance is still alive.
        
        Args:
            instance_status: The instance status string.
            
        Returns:
            True if the instance is considered alive.
        """
        if instance_status is None:
            return False
        return instance_status in ALIVE_INSTANCE_STATUSES

    def _is_instance_terminal(self, instance_status: str | None) -> bool:
        """Check if an instance status indicates a terminal state.
        
        Args:
            instance_status: The instance status string.
            
        Returns:
            True if the instance is in a terminal state.
        """
        if instance_status is None:
            return False
        return instance_status in _TERMINAL_INSTANCE_STATUSES

    async def recover_on_startup(self) -> dict:
        """Recover orphaned PROCESSING jobs on startup.

        Called once at daemon startup to handle jobs that were PROCESSING
        when the daemon crashed or was killed.

        For each PROCESSING job:
        - Check if its instance is still alive
        - If instance not found or terminal → mark job FAILED, release lock
        - If instance PAUSED → reconcile job PROCESSING → PAUSED (C2 fix)
        - If instance alive (RUNNING, IDLE, etc.) → leave as PROCESSING (observer handles)

        Returns:
            Dict with recovery stats: {"recovered": int, "alive": int, "total": int}
        """
        logger.info("Starting job recovery — checking PROCESSING jobs...")

        processing_jobs = await asyncio.to_thread(self._job_repository.find_processing_jobs)

        stats = {"recovered": 0, "alive": 0, "total": len(processing_jobs)}

        for job in processing_jobs:
            # Phase 5 (Option B): for MESSAGE jobs, check whether the
            # Task was created (i.e., ``enqueue_message`` ran after
            # ``start_job``). If the daemon crashed between
            # ``start_job`` and ``enqueue_message``, the JobItem is
            # ``active`` with no Task row — the observer will wait
            # forever for an instance-completion event that never
            # arrives. Detect and re-arm to ``queued`` so the
            # ``JobProcessor`` re-dispatches it.
            if job.job_type == "message" and job.instance_id and self._task_repository is not None:
                try:
                    task_exists = await asyncio.to_thread(
                        self._task_repository.get_by_work_id, job.job_id
                    )
                except Exception as task_check_err:
                    logger.warning(
                        f"JobRecovery: failed to check Task existence "
                        f"for message job {job.job_id[:8]}...: "
                        f"{task_check_err}. Falling through to "
                        "standard alive-leave branch."
                    )
                    task_exists = None  # Treat as unknown — leave alone.

                if task_exists is None:
                    # No Task row — crash between start_job and
                    # enqueue_message. Reset to 'queued' for re-dispatch
                    # and release the slot lock so re-dispatch can
                    # re-acquire it.
                    logger.warning(
                        f"JobRecovery: message job {job.job_id[:8]}... "
                        f"is active but has no Task — resetting to "
                        f"queued for re-dispatch (instance "
                        f"{job.instance_id[:8]}...)"
                    )
                    try:
                        # B2 fix: ``rearm_with_lock`` (F9 race-safe
                        # variant) handles the ``done -> active``
                        # post-finalize re-arm path for late-child
                        # callbacks. The crash-recovery path needs the
                        # OPPOSITE direction (``active -> queued``), and
                        # ``rearm_with_lock`` explicitly returns
                        # ``(None, False)`` for any state that is not
                        # ``done``. The dedicated
                        # ``reset_active_to_queued`` repository method
                        # performs DELETE-lock + UPDATE-state in a
                        # single transaction (same transactional
                        # pattern as ``start_job_atomic_with_lock`` so
                        # the PG ``trg_job_locks_active_guard`` trigger
                        # is satisfied at COMMIT). Returns True iff
                        # the state flip succeeded. Only increments
                        # ``stats["recovered"]`` on success so the
                        # counter reflects reality.
                        lock_ok = await asyncio.to_thread(
                            self._job_repository.reset_active_to_queued,
                            job.job_id,
                            job.instance_id,
                        )
                        if lock_ok:
                            logger.info(
                                f"JobRecovery: reset orphaned message "
                                f"job {job.job_id[:8]}... from active "
                                f"to queued for re-dispatch"
                            )
                            stats["recovered"] += 1
                        else:
                            logger.warning(
                                f"JobRecovery: failed to reset message "
                                f"job {job.job_id[:8]}... (state "
                                f"changed concurrently? — leaving as-is)"
                            )
                    except Exception as rearm_err:
                        logger.error(
                            f"JobRecovery: failed to reset message "
                            f"job {job.job_id[:8]}... to queued: "
                            f"{rearm_err}. Leaving as-is."
                        )
                    continue  # Skip the rest of the recovery branches

            if not job.instance_id:
                # Job has no instance — orphaned, mark as failed
                logger.warning(f"Job {job.job_id[:8]}... has no instance_id, marking FAILED")
                await self._fail_orphaned_job(job, "Recovered: no instance assigned", stats)
                continue

            # Check instance liveness
            instance = await asyncio.to_thread(self._instance_repository.get, job.instance_id)

            if instance is None:
                # Instance not found — orphaned
                logger.warning(
                    f"Job {job.job_id[:8]}... instance {job.instance_id[:8]}... not found, marking FAILED"
                )
                await self._fail_orphaned_job(job, "Recovered: instance no longer exists", stats)
            elif instance.status in (
                InstanceStatus.COMPLETED.value,
                InstanceStatus.TERMINATED.value,
                InstanceStatus.ERROR.value,
                InstanceStatus.FAILED.value,
            ):
                # Instance is terminal — job is orphaned
                logger.warning(
                    f"Job {job.job_id[:8]}... instance {job.instance_id[:8]}... "
                    f"is terminal ({instance.status}), marking FAILED"
                )
                await self._fail_orphaned_job(job, f"Recovered: instance is {instance.status}", stats)
            elif instance.status == InstanceStatus.PAUSED.value:
                # C2 fix (Phase 6): instance is PAUSED but job is still PROCESSING.
                # This state arises from (a) the pre-Phase-2 hack where pause did
                # not touch jobs, or (b) a crash during the pause transition window
                # (after instance → PAUSED but before job → PAUSED committed).
                # Reconcile by transitioning the job to PAUSED so its status
                # matches the instance. The (PROCESSING, PAUSED) "pause" entry
                # is in the TRANSITIONS dict (Phase 1).
                logger.info(
                    f"Job {job.job_id[:8]}... instance {job.instance_id[:8]}... "
                    f"is PAUSED — reconciling job PROCESSING → PAUSED"
                )
                try:
                    # Phase 7b: under admission_state, PROCESSING→PAUSED
                    # maps to (active→active) — a same-state no-op. The
                    # state machine's ``can_transition`` treats
                    # ``from == to`` as implicitly valid (see
                    # ``job_state_machine.py``). Pause is an Instance-side
                    # concern; the job stays ``active`` in admission_state.
                    await asyncio.to_thread(
                        self._job_repository.atomic_transition,
                        job.job_id,
                        from_status="processing",
                        to_status="paused",
                    )
                    stats["recovered"] += 1
                except InvalidTransitionError:
                    # Job was already transitioned by another actor — expected
                    # during concurrent recovery (e.g., another node). The job
                    # is no longer PROCESSING so we leave it alone.
                    logger.debug(
                        f"Job {job.job_id[:8]}... already transitioned during "
                        f"PAUSED recovery, skipping"
                    )
            else:
                # Instance is truly alive (RUNNING, IDLE, QUEUED, WAITING_CHILDREN)
                # — leave as PROCESSING, the observer will resume pickup.
                logger.info(
                    f"Job {job.job_id[:8]}... instance {job.instance_id[:8]}... "
                    f"is alive ({instance.status}), leaving as PROCESSING"
                )
                stats["alive"] += 1

        logger.info(
            f"Job recovery complete: {stats['recovered']} recovered, "
            f"{stats['alive']} alive, {stats['total']} total"
        )
        return stats

    async def _fail_orphaned_job(
        self, job: "JobItem", error_message: str, stats: dict[str, int]
    ) -> bool:
        """Mark an orphaned job as failed and release its lock.

        Phase 4 (Job as Queue Proxy): routes through the single
        terminal-write boundary ``JobQueueService._finalize_terminal``
        with ``Decision.NO_RETRY``. The boundary handles the
        ``active → done`` write (admission_state='done',
        status='failed') AND the scoped per-job lock release in its
        finally block — guaranteeing the lock is released on every
        code path (success, ``InvalidTransitionError``, unexpected
        exceptions) WITHOUT touching sibling locks.

        C1 fix (Phase 2 follow-up): the previous implementation
        additionally called ``release_by_instance(job.instance_id)``
        in an outer ``finally`` block, unconditionally wiping ALL
        locks for the instance — the F4/F7 sibling-lock-deletion
        bug, reintroduced in the recovery path post-92cb026a. That
        outer block is removed. The legacy fallback branch (no
        ``_job_queue_service`` wired) now does a scoped
        ``release_by_job(project_id, queue_id, job_id)`` itself so
        the structural invariant — "lock for this job is released
        on success" — still holds in the test-only path.

        Pre-fix, this method issued ``atomic_transition(processing
        → failed)`` directly and released the lock in a ``finally``
        block. The structural guarantee is preserved (the lock is
        always released on success), but the work now goes through
        the boundary so a future recovery code path cannot silently
        bypass retry/DLQ handling or the per-job lock-scoping rule.

        Args:
            job: The job to fail.
            error_message: Reason for failure.
            stats: Stats dict to increment on success.

        Returns:
            True if job was successfully transitioned, False if transition was
            skipped (e.g., already transitioned by another actor) or failed.
        """
        # Phase 4: route through the single terminal-write boundary.
        # The recovery path never retries (the job's instance is
        # gone/terminal, so retrying would loop on the same dead
        # instance) — NO_RETRY is correct.
        #
        # Phase 7c: ``target_status='failed'`` is passed so the
        # boundary writes ``terminal_reason='failed'`` (via the
        # ``_derive_terminal_reason`` mapping on ``target_status``).
        # Without the override the boundary would derive the status
        # from a now-missing/terminal Instance, which can land on
        # ``'cancelled'`` (TERMINATED Instance) — wrong, the
        # recovery path is failing an orphan, not cancelling it.
        try:
            if self._job_queue_service is not None:
                # Preferred path: use the boundary on JobQueueService.
                # The boundary releases the lock scoped to this job
                # (release_by_job) in its own finally block — we must
                # NOT release here or we'd double-release / wipe sibling
                # locks. C1 fix removes the prior outer finally block.
                canonical_job_id, _ = await self._job_queue_service._finalize_terminal(
                    instance_id=job.instance_id or "",
                    decision=Decision.NO_RETRY,
                    job_id=job.job_id,
                    error_message=error_message,
                    target_status="failed",
                )
                if canonical_job_id is not None:
                    stats["recovered"] += 1
                    try:
                        await self._job_queue_service.notify_watchers(
                            job.job_id, "failed", error_message
                        )
                    except Exception as e:
                        logger.warning(
                            f"_fail_orphaned_job: notify_watchers failed "
                            f"for {job.job_id[:8]}...: {e}"
                        )
                    return True
                # Boundary returned None — the job was not in
                # admission_state='active' (already transitioned by
                # another actor). Fall through to the InvalidTransition
                # handling below.
                logger.debug(
                    f"_fail_orphaned_job: _finalize_terminal no-op for "
                    f"job {job.job_id[:8]}... (already transitioned)"
                )
                return False
            else:
                # Legacy fallback (rare — only in tests that construct
                # JobRecoveryService without a JobQueueService). The
                # legacy path does NOT route through ``_finalize_terminal``
                # so it must release the lock itself — C1 fix uses the
                # SCOPED ``release_by_job(project_id, queue_id, job_id)``
                # to honor the F4/F7 invariant. ``release_by_instance``
                # here would wipe sibling locks (different jobs on the
                # same instance) — that is the exact bug this fix
                # removes from the main path.
                now = datetime.now(timezone.utc).isoformat()
                await asyncio.to_thread(
                    self._job_repository.atomic_transition,
                    job.job_id,
                    from_status="processing",
                    to_status="failed",
                    completed_at=now,
                    error_message=error_message,
                )
                # C1 fix: scoped per-job lock release (F4/F7). Only
                # attempt release when we have all three key parts;
                # otherwise the lock row cannot be matched and the
                # call is a safe no-op.
                if job.project_id and job.queue_id and job.job_id:
                    try:
                        await asyncio.to_thread(
                            self._lock_repository.release_by_job,
                            job.project_id,
                            job.queue_id,
                            job.job_id,
                        )
                    except Exception as lock_err:
                        # Lock release failure must not mask the
                        # successful transition — log and continue.
                        logger.error(
                            f"_fail_orphaned_job (legacy): failed to "
                            f"release lock for job {job.job_id[:8]}...: "
                            f"{lock_err}"
                        )
                stats["recovered"] += 1
                return True
        except InvalidTransitionError:
            # Job was already transitioned by another actor — this is expected.
            # No lock release here: the actor that transitioned the job
            # already released its lock (per F4/F7 contract). Re-releasing
            # would be either a no-op or, with the buggy
            # ``release_by_instance``, a sibling-lock wipe.
            logger.info(
                f"Job {job.job_id[:8]}... already transitioned during recovery, skipping"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to recover job {job.job_id[:8]}...: {e}")
            return False
        # C1 fix: REMOVED the outer ``finally`` block that called
        # ``release_by_instance(job.instance_id)``. That unconditional
        # call was reintroducing the F4/F7 sibling-lock-deletion bug
        # in the recovery path post-92cb026a:
        # - Main path (``_job_queue_service`` wired) already releases
        #   the lock scoped to this job inside ``_finalize_terminal``.
        # - Legacy path (``_job_queue_service is None``) now releases
        #   the lock scoped to this job above, before returning.
        # There is no path through this method that still needs an
        # instance-wide lock release — and any such path would be the
        # exact bug we are fixing.

    # ─────────────────────────────────────────────────────────────
    # Phase 3 (defer-seam bugfix, F5/F10): periodic drift reconciler
    # ─────────────────────────────────────────────────────────────

    async def reconcile_drift_states(
        self,
        min_pending_age_seconds: int = 300,
        min_orphan_age_seconds: int = 900,
    ) -> dict[str, Any]:
        """Detect and repair drift between ``job_queue_items`` and ``task``.

        Runs on a 60s loop (default, configurable via
        ``DaemonConfig.services.drift_reconcile_interval_seconds``) and
        bypasses the ``MaintenanceService._is_idle`` gate — drift
        appears *during* active work, which is precisely the case the
        idle-gated maintenance loop skips. Wired by the lifespan in
        ``daemon/api.py``.

        Three drift patterns detected:

        **(a)** ``active`` JobItem + ``pending`` Task with NULL
        heartbeat → P1-pattern deadlock (task never claimed).
        - If the instance is dead → cancel the task and finalize
          the job as FAILED (force-fail + terminal-write boundary).
          The F5 fix adds an explicit
          ``cancel_pending_tasks_for_instance`` call after the
          JobItem finalization so the orphan PENDING does not leak
          (the pre-fix comment claiming "next reconciler tick will
          clean it" was incorrect — F10 only inspects RUNNING
          tasks, PENDING tasks are invisible to it).
        - If the instance is alive → log only (the task may be
          claimable soon — premature cancellation would race with
          a worker about to claim it).

        **(b)** ``done`` JobItem + ``running`` Task → F10 zombie
        task. Force-complete the Task via
        ``StaleTaskRecovery.force_complete_task`` (JobItem already
        terminal). Log at WARNING. Do NOT retry — F10 is a
        terminal cleanup, not a recovery.

        **(c)** ``active`` JobItem + instance not ``running``/
        ``idle`` for extended period → stuck processing.
        Log-only mode (conservative). No automatic correction —
        the operator can investigate via the drift log.

        **(d)** ``pending`` Task on terminal JobItem (any instance
        state) → orphan PENDING cleanup. Safety net for PENDING
        tasks whose JobItem is ``done``/``dead_letter`` but were
        not cancelled by Pattern (a) (e.g. pre-F5 deployments or
        retry paths that skip the F5 path). Cancels via
        ``cancel_pending_tasks_for_instance`` (atomic
        ``status='pending'`` UPDATE — RUNNING siblings are not
        touched). Log at WARNING.

        **(f)** Orphan ACTIVE JobItem recovery (Pattern (f) —
        joined to a-d per the leader-locked design, RCA
        802095d8). Two sub-shapes:

        * **(f1) active JobItem + NO Task rows + alive/stale
          instance** → finalize the JobItem to
          ``admission_state='dead'`` (DEAD, distinct from
          Pattern (a)'s ``failed`` outcome — the JobItem is
          structurally orphaned, not retried). The Task
          absence is the restart-orphan signature: a daemon
          restart cleared the ``task`` table but left the
          JobItem row behind, so the JobItem is now
          ``active`` with nothing to drive it forward. The
          instance is left untouched (alive, but the
          JobItem's work is unrecoverable). Lock release
          follows the F4/F7 contract — scoped per-job via
          ``release_by_job`` (NOT the buggy
          ``release_by_instance``). The grace period
          (default 15min, configurable via
          ``drift_reconcile_min_orphan_age_seconds``) is
          applied to ``JobItem.created_at`` (JobItem has no
          ``updated_at`` column; ``created_at`` is the
          canonical "age" signal since the JobItem was
          enqueued at that time and ``active`` rows don't
          receive regular updates).

        * **(f2) active JobItem + COMPLETED Task** →
          finalize the JobItem to ``admission_state='done'``
          (DONE, ``terminal_reason='completed'``), and
          fire/cancel the dependency watchers via
          ``JobQueueService.notify_watchers`` (the
          canonical "fire-then-cancel" pattern used on
          terminate paths — UP-side fire for waiting
          parents, DOWN-side cancel for the JobItem's
          own watches).

        Healthy shapes (active JobItem + pending/running
        Task) are EXPLICITLY EXCLUDED — encoded as guard
        clauses, not incidental side effects. A pending or
        running Task means the JobItem's work is still in
        flight; Pattern (a) owns that surface.

        Args:
            min_pending_age_seconds: Minimum age (seconds) for a
                PENDING task to be considered drift-eligible. Tasks
                younger than this are left alone to avoid racing
                with a freshly-enqueued worker (default 300s = 5
                minutes — long enough to absorb a normal claim
                cycle, short enough to surface a genuine stuck
                task within one reconciliation tick).
            min_orphan_age_seconds: Minimum age (seconds) of an
                orphan ACTIVE JobItem (active JobItem + no Task
                rows + alive instance) before Pattern (f1)
                finalizes it as DEAD. Default 900s = 15 minutes.
                Configurable via
                ``DaemonConfig.services.drift_reconcile_min_orphan_age_seconds``.
                JobItems younger than this grace are left alone
                to avoid racing with a healthy active job whose
                Task row is still being enqueued (the
                "healthy-shape exclusion" contract — encoded as
                an explicit guard, not an incidental side
                effect).

        Returns:
            Dict summary of the form::

                {
                    "reconciled": int,         # number of corrections applied
                    "details": [                # per-correction records
                        {
                            "pattern": "P1_dead_instance"
                                  | "P1_alive_instance_log"
                                  | "F10_zombie_task"
                                  | "stuck_instance_log"
                                  | "orphan_pending_terminal_job"
                                  | "orphan_active_no_task_dead"
                                  | "orphan_active_completed_task_done"
                                  | "orphan_active_skipped_healthy_shape"
                                  | "orphan_active_skipped_grace"
                                  | "orphan_active_skipped_no_deps",
                            "job_id": str | None,
                            "task_id": int | None,
                            "instance_id": str | None,
                            "reason": str,
                        },
                        ...
                    ],
                }
        """
        details: list[dict[str, Any]] = []
        reconciled = 0

        # Bail early if the required dependencies aren't wired. This
        # happens in legacy test paths that construct
        # ``JobRecoveryService`` with only the original three args.
        # The drift reconciler is opt-in via dependency wiring — we
        # don't crash if a test only cares about ``recover_on_startup``.
        if self._task_repository is None:
            logger.debug(
                "reconcile_drift_states: task_repository not wired; "
                "drift reconciliation skipped."
            )
            return {"reconciled": 0, "details": details}

        # ── Pattern (b): F10 — done JobItem + running Task ────────
        # Iterate the (small) RUNNING task set first — F10 is the
        # most observable drift (workers see the zombie task but
        # can't transition it because the JobItem is already DONE).
        running_tasks = await asyncio.to_thread(
            self._task_repository.list_running_tasks
        )
        for task in running_tasks:
            try:
                # F10 detection: match the running Task to its OWN
                # JobItem via ``work_id == job_id``. The JobItem's
                # driving Task is stamped with ``work_id = job_id`` at
                # dispatch (see ``job_processor``), so a Task whose
                # ``work_id`` resolves to a ``done`` JobItem is the
                # genuine zombie (the job finished but its Task is
                # stuck running). A ``job_continue`` / message /
                # report Task has a standalone ``work_id`` that
                # matches NO JobItem → it is never an F10 zombie
                # (stale_task_recovery handles crashed-worker tasks
                # via heartbeat staleness, separately).
                task_work_id = getattr(task, "work_id", None)

                # Additive reconciler pass (Site 4: periodic sweep).
                # For each work_id the sweep encounters, normalize the
                # eight-table mirror before any of the heavier F10/P1
                # patterns run. CATCH per increment1-plan §5.1 — the
                # sweep is best-effort by design; one work_id's
                # failure MUST NOT abort the whole sweep. The
                # reconciler runs its own ``engine.begin()``
                # transaction; the matching task_id-aware
                # ``except Exception`` handler below continues to
                # cover unrelated drift detection failures.
                if task_work_id and hasattr(
                    self._task_repository, "reconcile_turn_mirror"
                ):
                    try:
                        await asyncio.to_thread(
                            self._task_repository.reconcile_turn_mirror,
                            task_work_id,
                        )
                    except InvalidTransitionError as e:
                        logger.warning(
                            "Reconciler invariant violation in drift "
                            "sweep for work_id=%s: %s",
                            task_work_id,
                            e,
                        )

                job: Any = None
                if task_work_id:
                    job = await asyncio.to_thread(
                        self._job_repository.get, task_work_id
                    )
                if job is None:
                    # No JobItem keyed by this Task's work_id — this
                    # is a continuation / message Task, not a job's
                    # driving Task. Not F10.
                    continue
                if job.admission_state != AdmissionState.DONE.value:
                    # This Task's own JobItem is not terminal yet —
                    # legitimately running.
                    continue

                # Legitimately-waiting parent guard: even a genuine
                # job Task that has spawned child agents (and is
                # waiting on the dependency bus) must not be force-
                # completed — its JobItem being ``done`` here is
                # unusual but force-completing would short-circuit the
                # terminal path. Defense-in-depth; the work_id match
                # above already eliminates the common false positive.
                from daemon.services.dependency_bus import get_dependency_bus
                _bus = get_dependency_bus()
                if _bus is not None and hasattr(
                    _bus, "count_pending_for_target_sync"
                ):
                    try:
                        pending = _bus.count_pending_for_target_sync(
                            task.instance_id
                        )
                    except Exception:
                        pending = 0
                    if pending > 0:
                        logger.info(
                            f"reconcile_drift_states: F10 skip for task "
                            f"{task.id} on instance "
                            f"{task.instance_id[:8]}... — {pending} pending "
                            f"child agent(s) (legitimately waiting)"
                        )
                        continue

                # Pattern (b) confirmed. Force-complete the task.
                # Caller contract (per ``force_complete_task`` docs):
                # the JobItem must already be terminal — verified
                # by the ``admission_state == 'done'`` check above.
                if self._stale_task_recovery is None:
                    logger.debug(
                        f"reconcile_drift_states: F10 zombie task "
                        f"{task.id} on instance "
                        f"{task.instance_id[:8]}... detected, but "
                        f"stale_task_recovery is not wired — skipping "
                        f"force-complete."
                    )
                    details.append({
                        "pattern": "F10_zombie_task",
                        "job_id": job.job_id,
                        "task_id": task.id,
                        "instance_id": task.instance_id,
                        "reason": (
                            "done JobItem + running Task — "
                            "force_complete skipped (no "
                            "stale_task_recovery wired)"
                        ),
                    })
                    continue

                reason = (
                    f"F10 drift: JobItem {job.job_id[:8]}... is "
                    f"done but task {task.id} is still running"
                )
                updated = await asyncio.to_thread(
                    self._stale_task_recovery.force_complete_task,
                    task.id,
                    reason,
                )
                if updated is not None:
                    reconciled += 1
                    details.append({
                        "pattern": "F10_zombie_task",
                        "job_id": job.job_id,
                        "task_id": task.id,
                        "instance_id": task.instance_id,
                        "reason": reason,
                    })
            except Exception as e:
                logger.error(
                    f"reconcile_drift_states: F10 check failed for "
                    f"task {task.id}: {e}",
                    exc_info=True,
                )

        # ── Pattern (a): P1 — active JobItem + old PENDING Task ──
        # Detect P1-pattern deadlocks: JobItem ``active`` but
        # driving Task has been PENDING without a heartbeat for
        # longer than the threshold. The Task never transitioned to
        # RUNNING because the cross-system guard in
        # ``claim_pending_task`` blocked it (e.g. NULL ``message_id``
        # stamp pre-Phase-1, or a worker pool restart between
        # enqueue and claim). Without reconciliation this drift
        # wedges forever — the JobItem never sees a terminal write.
        try:
            stuck_pending = await asyncio.to_thread(
                self._task_repository.list_pending_tasks_older_than,
                min_pending_age_seconds,
            )
        except Exception as e:
            logger.error(
                f"reconcile_drift_states: failed to list pending "
                f"tasks older than {min_pending_age_seconds}s: {e}",
                exc_info=True,
            )
            stuck_pending = []

        for task in stuck_pending:
            try:
                job = await asyncio.to_thread(
                    self._job_repository.get_by_instance, task.instance_id
                )
                if job is None or job.admission_state != AdmissionState.ACTIVE.value:
                    # Not a P1 candidate — JobItem is either missing
                    # (virtual-job case, not drift) or already
                    # terminal (handled by F10 path above).
                    continue

                # Check instance liveness — the fork point for
                # correction vs log-only.
                instance = await asyncio.to_thread(
                    self._instance_repository.get, task.instance_id
                )

                if instance is None or self._is_instance_terminal(
                    instance.status
                ):
                    # Dead instance → cancel task + finalize job.
                    if self._stale_task_recovery is None or self._job_queue_service is None:
                        logger.debug(
                            f"reconcile_drift_states: P1 stuck pending "
                            f"task {task.id} on dead instance "
                            f"{task.instance_id[:8]}... detected, but "
                            f"recovery deps not wired — skipping."
                        )
                        details.append({
                            "pattern": "P1_dead_instance",
                            "job_id": job.job_id,
                            "task_id": task.id,
                            "instance_id": task.instance_id,
                            "reason": (
                                "dead instance + pending task — "
                                "correction skipped (deps not wired)"
                            ),
                        })
                        continue

                    # Cancel the orphan PENDING Task on the dead
                    # instance BEFORE returning. Without this step the
                    # PENDING task survives forever — F10 only handles
                    # RUNNING tasks on terminal JobItems (``done``),
                    # and ``complete_task``'s ``status='running'`` guard
                    # is a no-op for PENDING rows. The
                    # ``cancel_pending_tasks_for_instance`` method
                    # (F12, Phase 3) is the dedicated cleanup for
                    # stale PENDING tasks: it issues an atomic SQL
                    # UPDATE flipping ``status='pending'`` → 'cancelled'
                    # for the target ``instance_id``, with the
                    # ``status='pending'`` guard ensuring no sibling
                    # RUNNING tasks are touched. The next
                    # ``claim_pending_task`` will then ignore the row.
                    #
                    # (Pre-fix comment claimed "the next reconciler
                    # tick will observe the orphan and clean it" — that
                    # was incorrect; the orphan PENDING was invisible
                    # to F10 until ``recover_on_startup`` ran on a
                    # daemon restart.)
                    # F12/F4 ordering fix (Phase 3, 2026-07-01):
                    # finalize FIRST, then cancel. The previous
                    # order (cancel → finalize) was an unrecoverable
                    # wedge when finalization failed: the catch
                    # block would log and continue, leaving
                    # JobItem=active + task=cancelled + instance=
                    # terminal — invisible to all 4 periodic
                    # reconciler patterns (a/b/c/d), only recoverable
                    # by ``recover_on_startup`` on next daemon
                    # restart. Post-fix ordering is safe:
                    #   * Finalize fails → cancel never runs →
                    #     state unchanged → Pattern (a) retries
                    #     next cycle (canonical P1 signature).
                    #   * Finalize succeeds, cancel fails →
                    #     JobItem=done + task=pending → Pattern (d)
                    #     catches orphan PENDING on terminal JobItem.
                    #   * Both succeed → clean.
                    try:
                        canonical_job_id, _ = await self._job_queue_service._finalize_terminal(
                            instance_id=task.instance_id,
                            decision=Decision.NO_RETRY,
                            job_id=job.job_id,
                            error_message=(
                                f"Drift reconciler: instance "
                                f"{instance.status if instance else 'missing'} "
                                f"while task {task.id} PENDING > "
                                f"{min_pending_age_seconds}s without heartbeat"
                            ),
                            target_status="failed",
                        )
                        if canonical_job_id is not None:
                            reconciled += 1
                            details.append({
                                "pattern": "P1_dead_instance",
                                "job_id": job.job_id,
                                "task_id": task.id,
                                "instance_id": task.instance_id,
                                "reason": (
                                    f"instance is "
                                    f"{instance.status if instance else 'missing'}; "
                                    f"task was PENDING > "
                                    f"{min_pending_age_seconds}s — "
                                    f"job finalized as failed"
                                ),
                            })
                            try:
                                await self._job_queue_service.notify_watchers(
                                    job.job_id,
                                    "failed",
                                    f"Drift reconciler: stuck on dead "
                                    f"instance {instance.status if instance else 'missing'}",
                                )
                            except Exception as notify_err:
                                logger.warning(
                                    f"reconcile_drift_states: "
                                    f"notify_watchers failed for "
                                    f"{job.job_id[:8]}...: {notify_err}"
                                )
                            # Cancel the orphan PENDING task AFTER
                            # successful finalization. Pattern (d)
                            # handles the case where this cancel
                            # fails — orphan PENDING on terminal
                            # JobItem is exactly Pattern (d)'s
                            # signature.
                            try:
                                await asyncio.to_thread(
                                    self._task_repository.cancel_pending_tasks_for_instance,
                                    task.instance_id,
                                )
                            except Exception as cancel_err:
                                # Cancel failure is non-fatal — the
                                # JobItem is already terminal, and
                                # Pattern (d) will clean up the
                                # orphan PENDING on the next cycle.
                                logger.error(
                                    f"reconcile_drift_states: failed "
                                    f"to cancel orphan PENDING task "
                                    f"{task.id} on dead instance "
                                    f"{task.instance_id[:8]}...: "
                                    f"{cancel_err}",
                                    exc_info=True,
                                )
                    except InvalidTransitionError:
                        logger.info(
                            f"reconcile_drift_states: P1 finalization "
                            f"for {job.job_id[:8]}... already done by "
                            f"another actor — skipping"
                        )
                    except Exception as final_err:
                        logger.error(
                            f"reconcile_drift_states: failed to "
                            f"finalize P1 stuck job {job.job_id[:8]}...: "
                            f"{final_err}",
                            exc_info=True,
                        )
                else:
                    # Instance alive — log only. The P1 wedge
                    # shouldn't happen with the post-Phase-1 NULL-
                    # safe cross-system guard, but a worker pool
                    # restart between enqueue and claim can
                    # legitimately produce a stuck PENDING task
                    # while the instance is alive. The natural
                    # claim path will pick it up; we just log so
                    # the operator has visibility.
                    logger.warning(
                        f"reconcile_drift_states: P1 candidate "
                        f"(task {task.id} PENDING > "
                        f"{min_pending_age_seconds}s, JobItem "
                        f"{job.job_id[:8]}... active, instance "
                        f"{instance.status if instance else 'unknown'}) "
                        f"— alive instance, log only"
                    )
                    details.append({
                        "pattern": "P1_alive_instance_log",
                        "job_id": job.job_id,
                        "task_id": task.id,
                        "instance_id": task.instance_id,
                        "reason": (
                            f"alive instance ({instance.status}); "
                            f"task PENDING > {min_pending_age_seconds}s "
                            f"without heartbeat — awaiting natural claim"
                        ),
                    })
            except Exception as e:
                logger.error(
                    f"reconcile_drift_states: P1 check failed for "
                    f"task {task.id}: {e}",
                    exc_info=True,
                )

        # ── Pattern (c): stuck instance — log only ───────────────
        # ``active`` JobItem + instance in a non-alive status
        # (PAUSED/WAITING_CHILDREN are alive by definition —
        # excluded via ``ALIVE_INSTANCE_STATUSES``). Detect
        # non-PAUSED, non-terminal stuck states and log for
        # operator visibility. The recovery service's
        # ``recover_on_startup`` handles the terminal-instance
        # case at startup; this handles the
        # mid-runtime-stuck case (e.g. an instance crashed mid-
        # update without transitioning to TERMINATED).
        try:
            active_jobs = await asyncio.to_thread(
                self._job_repository.find_processing_jobs
            )
            for job in active_jobs:
                if not job.instance_id:
                    continue
                instance = await asyncio.to_thread(
                    self._instance_repository.get, job.instance_id
                )
                if instance is None:
                    # Orphan — covered by recover_on_startup;
                    # log for visibility.
                    details.append({
                        "pattern": "stuck_instance_log",
                        "job_id": job.job_id,
                        "task_id": None,
                        "instance_id": job.instance_id,
                        "reason": (
                            "active JobItem but instance row "
                            "missing — covered by startup recovery"
                        ),
                    })
                    continue
                if (
                    self._is_instance_alive(instance.status)
                    or self._is_instance_terminal(instance.status)
                ):
                    continue
                # Non-alive, non-terminal status — log only.
                logger.warning(
                    f"reconcile_drift_states: stuck instance — "
                    f"job {job.job_id[:8]}... active, instance "
                    f"{job.instance_id[:8]}... in unexpected status "
                    f"{instance.status}"
                )
                details.append({
                    "pattern": "stuck_instance_log",
                    "job_id": job.job_id,
                    "task_id": None,
                    "instance_id": job.instance_id,
                    "reason": (
                        f"active JobItem but instance is in "
                        f"unexpected status '{instance.status}'"
                    ),
                })
        except Exception as e:
            logger.error(
                f"reconcile_drift_states: stuck-instance check "
                f"failed: {e}",
                exc_info=True,
            )

        # ── Pattern (d): orphan PENDING Task on terminal JobItem ──
        # Safety net for the general case: a PENDING Task whose
        # associated JobItem is already terminal (``done`` /
        # ``dead_letter``) on ANY instance state (alive or dead).
        # The JobItem side is closed, but the Task row can still be
        # PENDING (NULL heartbeat) — for example, a retry path that
        # flipped the JobItem to ``done``/``dead`` but the
        # ``cancel_pending_tasks_for_instance`` call (F12) was
        # skipped, or a recovery path that finalized the JobItem
        # before the F5 fix landed (this very patch adds the cancel
        # to Pattern (a); pre-fix reconcilers left the orphan
        # behind). F10 only inspects RUNNING tasks — PENDING tasks
        # are invisible to it — so this is the only periodic path
        # that catches the PENDING orphan specifically.
        #
        # Query: PENDING tasks older than the threshold whose
        # ``get(task.work_id)`` JobItem — the canonical linkage-key
        # lookup that resolves to the Task's OWN JobItem or
        # ``None`` (JAFP / virtual-job) — is in
        # ``admission_state='done'`` (which covers ``completed``,
        # ``failed``, ``cancelled``, ``dead_letter`` per the
        # admission_state mapping in ``job_state_machine.py``).
        # Action: cancel the PENDING task via
        # ``cancel_pending_tasks_for_instance`` (same atomic
        # WHERE status='pending' guard F12 uses — does NOT touch
        # sibling RUNNING tasks). Log at WARNING.
        #
        # This pattern is a pure safety net — Pattern (a) already
        # cancels the PENDING Task in the dead-instance branch.
        # Without Pattern (d), pre-fix deployments (or future drift
        # paths that skip Pattern (a)) would leak PENDING tasks
        # until ``recover_on_startup`` on the next daemon restart.
        try:
            orphan_pending = await asyncio.to_thread(
                self._task_repository.list_pending_tasks_older_than,
                min_pending_age_seconds,
            )
        except Exception as e:
            logger.error(
                f"reconcile_drift_states: Pattern (d) failed to list "
                f"pending tasks older than {min_pending_age_seconds}s: "
                f"{e}",
                exc_info=True,
            )
            orphan_pending = []

        for task in orphan_pending:
            try:
                # ── Wedge-fix: linkage by work_id (NOT instance) ──
                # Pre-fix this used ``get_by_instance(task.instance_id)``
                # which returns the MOST RECENT non-deleted JobItem for
                # the instance. That can be an OLD terminal dispatch
                # JobItem unrelated to THIS task — and was used as
                # false evidence to cancel a live PROCESS_REPORT carrier
                # (JAFP has NO JobItem at all by design), wedging the
                # waiting_children parent. The JobItem-side
                # ``job_id`` field IS the canonical cross-system
                # linkage key (Task.work_id == JobItem.job_id per the
                # linkage contract); ``get(task.work_id)`` returns the
                # task's OWN JobItem or ``None`` (JAFP / virtual-job).
                # A PROCESS_REPORT task whose work_id has no JobItem
                # → ``job is None`` → early-continue, task untouched.
                job = await asyncio.to_thread(
                    self._job_repository.get, task.work_id
                )
                # Only catch the case where the Task's OWN JobItem is
                # terminal. ``active`` JobItems are P1 candidates
                # (handled by Pattern (a) above); ``None`` JobItems are
                # virtual-job cases — including all PROCESS_REPORT
                # carriers per JAFP — and MUST NOT be considered drift.
                if job is None or job.admission_state != AdmissionState.DONE.value:
                    continue

                # ── Wedge-fix: alive-instance guard (defense in depth) ──
                # Even when the linkage lookup is unambiguous (the
                # Task's OWN JobItem is terminal), a live
                # ``WAITING_CHILDREN`` parent must NEVER be drift-
                # cancelled — it is parked on its child carrier and
                # the cancel would orphan the parent's wake surface.
                # This mirrors the alive-instance guard Pattern (a)
                # applies at :791-794 (it cancels only when the
                # instance is dead; live instances fall into the
                # log-only branch). Same guard, applied here, so a
                # stale PENDING task on a live parent is left for the
                # natural claim path or the watchdog backstop.
                instance = await asyncio.to_thread(
                    self._instance_repository.get, task.instance_id
                )
                if instance is not None and self._is_instance_alive(
                    instance.status
                ):
                    logger.debug(
                        f"reconcile_drift_states: Pattern (d) skip — "
                        f"instance {task.instance_id[:8]}... is alive "
                        f"(status={instance.status}); task {task.id} "
                        f"left for natural claim path or watchdog backstop."
                    )
                    continue

                # Cancel the orphan PENDING task. The atomic UPDATE
                # uses the ``status='pending'`` guard — sibling
                # RUNNING tasks on the same instance are NOT
                # touched.
                try:
                    cancelled = await asyncio.to_thread(
                        self._task_repository.cancel_pending_tasks_for_instance,
                        task.instance_id,
                    )
                except Exception as cancel_err:
                    logger.error(
                        f"reconcile_drift_states: Pattern (d) cancel "
                        f"failed for instance "
                        f"{task.instance_id[:8]}... task {task.id}: "
                        f"{cancel_err}",
                        exc_info=True,
                    )
                    details.append({
                        "pattern": "orphan_pending_terminal_job",
                        "job_id": job.job_id,
                        "task_id": task.id,
                        "instance_id": task.instance_id,
                        "reason": (
                            f"PENDING task on terminal JobItem "
                            f"(admission_state={job.admission_state}) "
                            f"— cancel failed: {cancel_err}"
                        ),
                    })
                    continue

                if cancelled > 0:
                    logger.warning(
                        f"reconcile_drift_states: orphan PENDING "
                        f"task {task.id} on terminal JobItem "
                        f"{job.job_id[:8]}... (admission_state="
                        f"{job.admission_state}) — cancelled "
                        f"{cancelled} task row(s) on instance "
                        f"{task.instance_id[:8]}..."
                    )
                    reconciled += 1
                    details.append({
                        "pattern": "orphan_pending_terminal_job",
                        "job_id": job.job_id,
                        "task_id": task.id,
                        "instance_id": task.instance_id,
                        "reason": (
                            f"PENDING task older than "
                            f"{min_pending_age_seconds}s on terminal "
                            f"JobItem (admission_state="
                            f"{job.admission_state}) — cancelled"
                        ),
                    })
            except Exception as e:
                logger.error(
                    f"reconcile_drift_states: Pattern (d) check "
                    f"failed for task {task.id}: {e}",
                    exc_info=True,
                )

        msg = (
            f"reconcile_drift_states: complete — "
            f"reconciled={reconciled}, "
            f"details={len(details)}"
        )
        if reconciled == 0 and not details:
            logger.debug(msg)
        else:
            logger.info(msg)

        # ── Pattern (e): PENDING process_report Task on TERMINATED
        # / missing parent (plan §T8 (d), AF2 C1/C3) ───────────────
        # Phase 1 / T8 (d) of the pause-resume-terminate-tree-fix
        # (``.agents/shared/planning/pause-resume-terminate-tree-fix/``
        # Rev 2.1). The B4-tail livelock root cause: a PENDING
        # ``process_report`` Task whose target ``instances`` row is
        # TERMINATED (or missing) is permanently unclaimable — the
        # pause gate at ``task/repository.py`` ~:1315-1336 filters
        # ``status IN (paused, terminated)`` uniformly for ALL task
        # types. The Task sits PENDING forever; the ``[GUARD] … blocked
        # by guard`` diagnostic in ``claim_pending_task`` loops every
        # poll.
        #
        # Pattern (d) above would catch the JobItem-side terminal case
        # but ``process_report`` Tasks have NO linked JobItem (the
        # plan §AF2 "2c REJECTED" rationale, ``daemon/repositories/job_queue/repository.py:2736-2741``
        # cleanup bucket 4 requires ``EXISTS(job_queue_items …)`` —
        # process_report never matches). The dead-letter is scoped
        # strictly to ``process_report`` Tasks (R3 — do not mask other
        # starvation via a broad sweep).
        #
        # Action per row:
        #   1. Atomic UPDATE ``status='pending' → 'failed'`` with the
        #      parent-status EXISTS folded into WHERE (closes the
        #      revive race — TOCTOU window between read and write).
        #   2. ``DeadLetterTurn`` named transition (canonical
        #      ``terminal_reason='failed'`` per leader D3) via
        #      ``reconcile_turn_mirror(work_id)`` — the SOLE
        #      completion authority for the 8-mirror set.
        #   3. DELETE the companion ``report_injections`` row whose
        #      ``report_message_id == task.message_id`` (no injection
        #      terminal state exists; INJECTED / TASK_DELIVERED would
        #      falsely signal delivery — the AF2 C3 trap).
        #
        # Sweep age threshold ≥300s default (TOCTOU healing) — a fresh
        # PENDING row may have just been enqueued by the natural
        # completion path and we must not race it.
        #
        # The ``[GUARD]`` diagnostic in ``claim_pending_task`` is left
        # INTACT for everything else — this sweep is for the
        # dead-parent class only.
        try:
            extra = await self._pattern_e_dead_letter_dead_parent_process_reports(
                min_pending_age_seconds=min_pending_age_seconds,
            )
            if extra:
                reconciled += extra.get("reconciled", 0)
                details.extend(extra.get("details", []))
        except Exception as e:
            logger.error(
                f"reconcile_drift_states: Pattern (e) check failed: {e}",
                exc_info=True,
            )

        # ── Pattern (f): orphan ACTIVE JobItem recovery ──────────
        # Leader-locked design (incident 802095d8, RCA-confirmed).
        # The "restart-orphan" class: a daemon restart cleared the
        # ``task`` table ("Cleared 737 backlog task(s)") but the
        # JobItem rows SURVIVED the restart-wipe. Result: an
        # ``admission_state='active'`` JobItem with no Task rows and
        # an alive/stale instance. Without this pattern, the JobItem
        # sits ``active`` forever — the instance is still alive, the
        # Task observer has nothing to observe, and the defer idle
        # gate holds forever.
        #
        # Two sub-shapes (both leader-locked):
        #
        #   (f1) active JobItem + NO Task rows + alive/stale
        #        instance (older than the grace) → finalize the
        #        JobItem to ``admission_state='dead'`` (DEAD,
        #        distinct from Pattern (a)'s ``failed`` outcome —
        #        the JobItem is structurally orphaned, not
        #        retried) + release the queue lock scoped per
        #        job (F4/F7 contract; never
        #        ``release_by_instance``).
        #
        #   (f2) active JobItem + COMPLETED Task → finalize the
        #        JobItem to ``admission_state='done'`` (DONE,
        #        ``terminal_reason='completed'``) + fire/cancel
        #        dependency watchers via
        #        ``JobQueueService.notify_watchers`` (the
        #        canonical "fire-then-cancel" pattern used on
        #        terminate paths).
        #
        # Healthy shapes (active JobItem + pending Task, active
        # JobItem + running Task) are EXPLICITLY EXCLUDED via guard
        # clauses. The brief is explicit: a pending Task means the
        # work is awaiting claim (Pattern (a) owns that surface);
        # a running Task means the work is in flight. Encoding
        # these as guards (NOT incidental side effects) ensures
        # the pattern cannot race a healthy work cycle.
        #
        # Grace period: ``min_orphan_age_seconds`` (default 900s =
        # 15 minutes, configurable via
        # ``DaemonConfig.services.drift_reconcile_min_orphan_age_seconds``).
        # The age signal is ``JobItem.created_at`` — JobItem has
        # no ``updated_at`` column; ``created_at`` is the
        # canonical "age" signal for an active JobItem since the
        # row was enqueued at that time and ``active`` rows don't
        # receive regular updates. The boundary semantics are
        # "strict less-than": an JobItem with
        # ``created_at == now - min_orphan_age_seconds`` is NOT
        # matched (mirrors the existing
        # ``list_pending_tasks_older_than`` contract at
        # ``task/repository.py:720``: ``created_at < threshold``).
        try:
            extra = await self._pattern_f_orphan_active_job_recovery(
                min_orphan_age_seconds=min_orphan_age_seconds,
            )
            if extra:
                reconciled += extra.get("reconciled", 0)
                details.extend(extra.get("details", []))
        except Exception as e:
            logger.error(
                f"reconcile_drift_states: Pattern (f) check failed: {e}",
                exc_info=True,
            )

        # Final tally — recount from `details` so the summary line
        # reflects patterns (a/b/d/e/f) accurately. Pattern (c) is
        # log-only — ``stuck_instance_log`` entries land in `details`
        # but are intentionally excluded from the `reconciled` count
        # (the recovery path cannot correct stuck-alive instances).
        # The four Pattern (f) ``skipped_*`` records are also excluded
        # from the ``reconciled`` count (they're observability —
        # log+detail only — the brief says EXPLICIT exclusion
        # healthy shapes must not become reconciled counts).
        reconciled = sum(
            1 for d in details if d.get("pattern") in (
                "P1_dead_instance",
                "F10_zombie_task",
                "orphan_pending_terminal_job",
                "dead_parent_pending_process_report",
                "orphan_active_no_task_dead",
                "orphan_active_completed_task_done",
            )
        )

        msg = (
            f"reconcile_drift_states: complete — "
            f"reconciled={reconciled}, "
            f"details={len(details)}"
        )
        if reconciled == 0 and not details:
            logger.debug(msg)
        else:
            logger.info(msg)
        return {"reconciled": reconciled, "details": details}

    async def _pattern_e_dead_letter_dead_parent_process_reports(
        self,
        *,
        min_pending_age_seconds: int,
    ) -> dict | None:
        """Pattern (e) async wrapper — delegates to a sync sweep body
        via ``asyncio.to_thread`` so the long-lived ``engine.begin()``
        transaction does not block the event loop on the first real
        stranded row.

        The full sweep predicate, the companion ``report_injections``
        DELETE, and the ``DeadLetterTurn`` mirror reconcile all live
        in :meth:`_pattern_e_dead_letter_sweep_sync` (the F1
        production-path seam). The async wrapper is a thin bridge
        that resolves the engine from ``self._task_repository`` and
        surfaces the result shape (``None`` when nothing was
        dead-lettered, ``{reconciled, details}`` otherwise) to the
        caller (``reconcile_drift_states``).
        """
        engine = self._task_repository.engine if self._task_repository else None
        if engine is None:
            logger.debug(
                "_pattern_e_dead_letter_dead_parent_process_reports: "
                "task_repository not wired; pattern (e) skipped"
            )
            return None

        try:
            return await asyncio.to_thread(
                self._pattern_e_dead_letter_sweep_sync,
                engine,
                self._task_repository,
                min_pending_age_seconds,
            )
        except Exception as e:
            logger.error(
                f"reconcile_drift_states: Pattern (e) check failed: {e}",
                exc_info=True,
            )
            return None

    def _pattern_e_dead_letter_sweep_sync(
        self,
        engine,
        task_repository,
        min_pending_age_seconds: int,
    ) -> dict | None:
        """Pattern (e) sync body — F1 production-path seam.

        Walks the dead-parent ``process_report`` candidate set and
        dead-letters each row in a single ``engine.begin()``
        transaction. Three correctness invariants preserved:

          * F1 (a): the entire SELECT + per-row UPDATE + companion
            DELETE + named transition lives inside ONE
            ``engine.begin()`` — the mirror reconcile therefore
            JOINS this transaction (the named transition's
            ``_reconcile()`` is called with ``connection=conn`` so
            ``reconcile_turn_mirror`` writes against the same
            engine). Pre-fix, ``_reconcile()`` opened its own
            ``engine.begin()`` inside the sweep's open txn, which
            self-deadlocked on PG (no ``lock_timeout``) and silently
            lost the reconcile on file SQLite (``OperationalError``
            after busy-timeout, swallowed by the per-row except).
          * F1 (b): the long sync SQL runs on a worker thread via
            ``asyncio.to_thread`` in the async wrapper — the event
            loop cannot block on ``engine.begin()``.
          * F1 (c): the sweep predicate and the EXISTS-in-WHERE
            revive-race closure are unchanged from the verified
            shape — only the reconcile-threading was wrong.

        The companion ``report_injections`` DELETE is scoped to a
        single row per dead-lettered Task (the
        ``report_message_id == task.message_id`` lookup is exact).
        The named transition's ``reconcile_turn_mirror`` is the
        SOLE completion authority for the 8-mirror set; this
        sweep never touches ``job_queue_items`` directly (plan §C6
        invariant).

        Returns ``None`` when nothing was dead-lettered; otherwise a
        ``{reconciled, details}`` dict for the caller to merge into
        its ``reconcile_drift_states`` summary.
        """
        from daemon.repositories.task.models import TaskStatus, TaskType
        from daemon.repositories.instance.models import InstanceStatus
        from daemon.services.turn_transitions import DeadLetterTurn

        threshold = datetime.now(timezone.utc) - timedelta(
            seconds=min_pending_age_seconds
        )

        reconciled = 0
        details: list[dict] = []

        with engine.begin() as conn:
            # 1. Candidate SELECT — scope-strict (R3) on
            # ``process_report`` type, status='pending', age threshold,
            # and the parent-status EXISTS folded in (closes the
            # revive race — TOCTOU window between this read and the
            # write below).
            candidates = conn.execute(
                text("""
                    SELECT t.id, t.work_id, t.message_id, t.instance_id
                    FROM task t
                    WHERE t.status = :status_pending
                      AND t.task_type = :process_report_type
                      AND t.created_at <= :threshold
                      AND (
                          NOT EXISTS (
                              SELECT 1 FROM instances i
                              WHERE i.instance_id = t.instance_id
                          )
                          OR EXISTS (
                              SELECT 1 FROM instances i
                              WHERE i.instance_id = t.instance_id
                                AND i.status = :status_terminated
                          )
                      )
                """),
                {
                    "status_pending": TaskStatus.PENDING.value,
                    "process_report_type": TaskType.PROCESS_REPORT.value,
                    "threshold": threshold,
                    "status_terminated": InstanceStatus.TERMINATED.value,
                },
            ).fetchall()

            for row in candidates:
                task_id, work_id, message_id, instance_id = row
                # 2. Atomic UPDATE with EXISTS-in-WHERE — closes the
                # revive race. A concurrent revive that transitioned
                # the parent out of TERMINATED would flip this
                # UPDATE to rowcount 0 (no match), and we skip the
                # dead-letter; Pattern (a) / (d) handle the row on
                # the next cycle.
                update_result = conn.execute(
                    text("""
                        UPDATE task
                        SET status = :status_failed,
                            completed_at = :now,
                            error = :reason
                        WHERE id = :task_id
                          AND status = :status_pending
                          AND (
                              NOT EXISTS (
                                  SELECT 1 FROM instances i
                                  WHERE i.instance_id = :instance_id
                              )
                              OR EXISTS (
                                  SELECT 1 FROM instances i
                                  WHERE i.instance_id = :instance_id
                                    AND i.status = :status_terminated
                              )
                          )
                    """),
                    {
                        "status_failed": TaskStatus.FAILED.value,
                        "now": datetime.now(timezone.utc),
                        "reason": "drift_sweep_dead_parent",
                        "task_id": task_id,
                        "status_pending": TaskStatus.PENDING.value,
                        "instance_id": instance_id,
                        "status_terminated": InstanceStatus.TERMINATED.value,
                    },
                )
                if update_result.rowcount == 0:
                    # Revive race lost — another actor mutated the
                    # row since the SELECT. Skip; the next cycle
                    # picks it up.
                    continue

                # 3. Companion ReportInjection DELETE (T8 (b) — load-
                # bearing). No injection terminal state exists; the
                # ``uq_report_injections_oblig_triple`` partial
                # index is preserved (only INJECTED / TASK_DELIVERED
                # partial-unique; PENDING + DELETED are free).
                if message_id:
                    conn.execute(
                        text("""
                            DELETE FROM report_injections
                            WHERE report_message_id = :message_id
                        """),
                        {"message_id": message_id},
                    )

                # 4. Named transition + mirror reconcile — the SOLE
                # completion authority for the 8-mirror set. The
                # transition's UPDATE will rowcount 0 (no longer
                # PENDING after step 2), but the mirror reconcile
                # still fires — that's the canonical path. F1 fix:
                # ``DeadLetterTurn.run(_SessionAdapter(conn))`` passes
                # the underlying ``conn`` to ``_reconcile`` so the
                # mirror reconcile joins THIS transaction (see
                # ``turn_transitions.py:_StatusTransition._reconcile``
                # ``connection=`` parameter). Pre-fix, the reconcile
                # opened a nested ``engine.begin()`` and self-
                # deadlocked on PG / silently failed on SQLite.
                if task_repository is not None:
                    try:
                        t = DeadLetterTurn(
                            work_id=work_id,
                            task_repo=task_repository,
                            reason="drift_sweep_dead_parent",
                        )
                        t.run(_SessionAdapter(conn))
                        logger.warning(
                            f"reconcile_drift_states: Pattern (e) "
                            f"dead-lettered stranded process_report "
                            f"task {task_id} (work_id="
                            f"{work_id[:8]}..., instance_id="
                            f"{instance_id[:8] if instance_id else 'missing'}..., "
                            f"message_id="
                            f"{message_id[:8] if message_id else '?'}...); "
                            f"companion report_injections row "
                            f"DELETEd (no injection terminal state "
                            f"exists — AF2 C3); canonical "
                            f"terminal_reason='failed' (leader D3)"
                        )
                        reconciled += 1
                        details.append({
                            "pattern": "dead_parent_pending_process_report",
                            "job_id": None,
                            "task_id": task_id,
                            "instance_id": instance_id,
                            "reason": (
                                f"PENDING process_report Task older "
                                f"than {min_pending_age_seconds}s "
                                f"targeting a TERMINATED/missing "
                                f"instance — dead-lettered with "
                                f"canonical 'failed'"
                            ),
                        })
                    except Exception as recon_err:
                        # Mirror reconcile failure is non-fatal — the
                        # Task row is already FAILED via the SQL
                        # UPDATE above. Pattern (a) / (d) safety nets
                        # catch any orphan on the next cycle.
                        #
                        # W6 (governor-council NEEDS-FIXES): surface the
                        # failure in the sweep's ``details`` payload so
                        # drift is observable (operators see the
                        # mirror-reconcile failure count alongside the
                        # successful count — no longer silently lost).
                        # Count semantics for successful rows are
                        # unchanged (``reconciled`` still counts only
                        # full successes). The ``mirror_reconcile_failed``
                        # entry is appended below so callers can split
                        # the two counts.
                        logger.error(
                            f"reconcile_drift_states: Pattern (e) "
                            f"mirror reconcile failed for task "
                            f"{task_id}: {recon_err}",
                            exc_info=True,
                        )
                        details.append({
                            "pattern": "mirror_reconcile_failed",
                            "job_id": None,
                            "task_id": task_id,
                            "instance_id": instance_id,
                            "reason": (
                                f"Pattern (e) dead-letter succeeded (Task "
                                f"row FAILED via SQL UPDATE) but the "
                                f"named transition's mirror reconcile "
                                f"raised: {type(recon_err).__name__}: "
                                f"{recon_err}. Mirrors may stay stale "
                                f"until the next sweep cycle."
                            ),
                        })

        # W6 (governor-council NEEDS-FIXES): return the payload whenever
        # ANY row was processed — even if every row failed mirror
        # reconcile (the SQL UPDATE still dead-lettered the rows; only
        # the named transition's mirror reconcile raised). Pre-fix the
        # `if reconciled == 0: return None` short-circuit swallowed the
        # failure detail so a sweep where every row failed to
        # reconcile returned None (operator-invisible drift). Now we
        # return the payload when there is any details entry — that
        # way failures are always observable.
        if reconciled == 0 and not details:
            return None
        return {"reconciled": reconciled, "details": details}

    # ─────────────────────────────────────────────────────────────
    # Pattern (f) — orphan ACTIVE JobItem recovery (leader-locked)
    # ─────────────────────────────────────────────────────────────

    async def _pattern_f_orphan_active_job_recovery(
        self,
        *,
        min_orphan_age_seconds: int,
    ) -> dict | None:
        """Pattern (f) — detect and repair orphan ACTIVE JobItems.

        Leader-locked design (incident 802095d8, RCA-confirmed).
        Two sub-shapes are detected and corrected:

        * **(f1) active JobItem + NO Task rows + alive/stale
          instance** (older than the grace) → finalize to
          ``admission_state='dead'`` (DEAD, distinct from
          Pattern (a)'s ``failed`` outcome — the JobItem is
          structurally orphaned, not retried). Lock release
          follows the F4/F7 contract — scoped per-job via
          ``release_by_job`` (NOT the buggy
          ``release_by_instance``).

        * **(f2) active JobItem + COMPLETED Task** → finalize
          to ``admission_state='done'``
          (``terminal_reason='completed'``) and fire/cancel
          dependency watchers via
          ``JobQueueService.notify_watchers`` (the
          canonical "fire-then-cancel" pattern used on
          terminate paths — UP-side fire for waiting
          parents, DOWN-side cancel for the JobItem's
          own watches).

        Healthy shapes (active JobItem + pending Task,
        active JobItem + running Task) are EXPLICITLY
        EXCLUDED via guard clauses — encoded as
        ``continue`` branches with explicit pattern
        names (``orphan_active_skipped_healthy_shape``)
        so observability can confirm a misconfigured
        deploy hasn't accidentally collapsed the guard.

        Grace period: ``min_orphan_age_seconds`` (default
        900s = 15 minutes, configurable via
        ``DaemonConfig.services.drift_reconcile_min_orphan_age_seconds``).
        The age signal is ``JobItem.created_at`` — JobItem
        has no ``updated_at`` column; ``created_at`` is the
        canonical "age" signal for an active JobItem since
        the row was enqueued at that time and ``active``
        rows don't receive regular updates. Boundary
        semantics are strict less-than
        (``created_at < threshold``), matching the existing
        ``list_pending_tasks_older_than`` contract at
        ``task/repository.py:720``.

        Per-candidate isolation: one JobItem's failure MUST
        NOT abort the sweep. Each per-row block is wrapped
        in its own ``try/except Exception`` so a transient
        DB blip on one row only loses that row.
        """
        if self._job_repository is None:
            logger.debug(
                "_pattern_f_orphan_active_job_recovery: "
                "job_repository not wired; pattern (f) skipped"
            )
            return None

        reconciled = 0
        details: list[dict[str, Any]] = []

        # Resolve threshold once (strict less-than
        # semantics, mirroring
        # ``list_pending_tasks_older_than``).
        threshold = datetime.now(timezone.utc) - timedelta(
            seconds=min_orphan_age_seconds
        )

        # Walk the active JobItem candidate set. The set is
        # the same source ``recover_on_startup`` and Pattern
        # (c) use (``find_processing_jobs`` — queries
        # ``admission_state='active' AND deleted_at IS NULL``),
        # so the candidate scope is consistent across the
        # recovery service's drift surfaces.
        try:
            active_jobs = await asyncio.to_thread(
                self._job_repository.find_processing_jobs
            )
        except Exception as e:
            logger.error(
                f"reconcile_drift_states: Pattern (f) failed to "
                f"list active jobs: {e}",
                exc_info=True,
            )
            return None

        for job in active_jobs:
            try:
                # Per-row work is wrapped so a single
                # failure cannot abort the sweep.
                job_id = getattr(job, "job_id", None)
                instance_id = getattr(job, "instance_id", None)
                if not job_id or not instance_id:
                    # Without instance_id the alive/terminal
                    # check is undefined. The startup-
                    # recovery path handles no-instance
                    # orphans; here we just skip (the
                    # next cycle retries).
                    logger.debug(
                        f"reconcile_drift_states: Pattern (f) "
                        f"skip — job "
                        f"{job_id[:8] if job_id else '?'}... "
                        f"has no instance_id "
                        f"(startup-recovery scope)"
                    )
                    continue

                # ── Task-side inspection ───────────────
                # f1 requires "no Task rows" for this
                # JobItem. f2 requires a COMPLETED Task.
                # Both use
                # ``TaskRepository.get_by_work_id`` (the
                # canonical cross-system linkage key —
                # Task ``work_id == JobItem.job_id`` per
                # the dispatch contract, see
                # ``job_processor``). ``None`` here
                # means "no Task row"; any other status
                # is the current Task state.
                task = None
                if self._task_repository is not None:
                    try:
                        task = await asyncio.to_thread(
                            self._task_repository.get_by_work_id,
                            job_id,
                        )
                    except Exception as task_lookup_err:
                        logger.warning(
                            f"reconcile_drift_states: Pattern (f) "
                            f"Task lookup failed for job "
                            f"{job_id[:8]}...: "
                            f"{task_lookup_err}. "
                            f"Falling through to skip."
                        )
                        task = None

                # ── Healthy-shape exclusion (STRICT
                # guard, council REJECT 2026-08-29
                # Critical #1) ──────────────────────────
                # The pre-fix guard was lenient: any Task
                # row present meant "not f1", but only
                # PENDING/RUNNING were explicitly named
                # skip shapes. PAUSED Jobs kept
                # admission_state='active'
                # (repositories/job_queue/repository.py:
                # 995-998) and were silently dead-lettered
                # by f1 — a pause older than the grace let
                # f1 DEAD-finalize LIVE RESUMABLE work and
                # resume then found DEAD. FAILED/CANCELLED
                # Tasks landed on bare DEAD, foreclosing
                # atomic_retry
                # (repositories/job_queue/repository.py:
                # 1381-1382 vs :2230) and losing the
                # canonical ``terminal_reason`` (the
                # observer's ``failed_at`` marker is the
                # trigger atomic_retry requires).
                #
                # The fix: the f1 predicate is now STRICT
                # ``task is None`` — any Task row present
                # means NOT an f1 candidate. Each non-null
                # Task status routes to its proper surface:
                #
                # * PENDING/RUNNING → existing
                #   ``orphan_active_skipped_healthy_shape``
                #   (Pattern (a) / (b) own this surface).
                # * PAUSED         → new
                #   ``orphan_active_skipped_paused`` —
                #   pause cancels the in-flight task;
                #   resume re-mints. The reconciler MUST
                #   NOT DEAD-finalize a still-resumable
                #   row. Resume will re-claim it.
                # * FAILED/CANCELLED → route through the
                #   ``_fail_orphaned_job``-style boundary
                #   (lock release + ``failed_at`` +
                #   ``terminal_reason`` +
                #   ``notify_watchers`` — the same seam
                #   ``_fail_orphaned_job`` uses). NOT
                #   bare DEAD: a FAILED Task carries
                #   canonical ``terminal_reason='failed'``;
                #   a CANCELLED Task carries
                #   ``terminal_reason='cancelled'``. Bare
                #   DEAD would foreclose atomic_retry.
                #   This routing also resolves the
                #   council's Warning 2 automatically.
                # * COMPLETED → f2 path (with the
                #   Critical #3 gate below).
                if task is not None:
                    task_status = getattr(task, "status", None)
                    task_id = getattr(task, "id", None)
                    if task_status in (
                        TaskStatus.PENDING.value,
                        TaskStatus.RUNNING.value,
                    ):
                        details.append({
                            "pattern": "orphan_active_skipped_healthy_shape",
                            "job_id": job_id,
                            "task_id": task_id,
                            "instance_id": instance_id,
                            "reason": (
                                f"active JobItem with "
                                f"{task_status!r} Task — "
                                f"healthy shape excluded "
                                f"from Pattern (f)"
                            ),
                        })
                        continue
                    if task_status == TaskStatus.PAUSED.value:
                        # Pause cancels the in-flight task;
                        # resume re-mints a fresh Task on
                        # the same JobItem. The reconciler
                        # MUST NOT DEAD-finalize a
                        # still-resumable row — resume
                        # would find DEAD and the work
                        # would silently die.
                        details.append({
                            "pattern": "orphan_active_skipped_paused",
                            "job_id": job_id,
                            "task_id": task_id,
                            "instance_id": instance_id,
                            "reason": (
                                f"active JobItem with "
                                f"PAUSED Task {task_id} — "
                                f"pause owns the resume "
                                f"path; reconciler must NOT "
                                f"DEAD-finalize a "
                                f"still-resumable row "
                                f"(resume would find DEAD)"
                            ),
                        })
                        continue
                    if task_status in (
                        TaskStatus.FAILED.value,
                        TaskStatus.CANCELLED.value,
                    ):
                        # ── Council REJECT 2026-08-29 W1
                        # fix — retry-child lineage
                        # conjunct ─────────────────────
                        # ``TaskRepository.get_by_work_id(job_id)``
                        # only sees the parent Task (the
                        # retry child minted by
                        # ``schedule_retry`` /
                        # ``force_cancel_and_schedule_retry``
                        # carries a FRESH ``work_id`` —
                        # ``task_repository.py:3261 / :3702``
                        # — so a parent-work_id-only
                        # non-terminal query misses it
                        # entirely). The retry child
                        # INHERITS the parent's
                        # ``instance_id``
                        # (``task_repository.py:3299 /
                        # :3719`` — the
                        # ``RetryTurn`` constructor passes
                        # ``parent_row.instance_id``),
                        # so the lineage is keyed by
                        # ``instance_id``, not work_id.
                        # A live retry child (PENDING /
                        # RUNNING) on the same instance
                        # means the JobItem still has
                        # live work in flight — finalizing
                        # the parent orphans the retry
                        # child (the JobItem mirror flips
                        # terminal while the child Task
                        # is still driving the graph).
                        # Skip this sweep; the next 60s
                        # cycle retries once the lineage
                        # quiesces. Reuses the
                        # ``orphan_active_skipped_*``
                        # detail family for
                        # observability symmetry with
                        # ``skipped_paused`` /
                        # ``skipped_no_deps``.
                        retry_live = (
                            await self._pattern_f_instance_has_inflight_task(
                                instance_id
                            )
                        )
                        if retry_live:
                            details.append({
                                "pattern": "orphan_active_skipped_retry_child_live",
                                "job_id": job_id,
                                "task_id": task_id,
                                "instance_id": instance_id,
                                "reason": (
                                    f"active JobItem with "
                                    f"{task_status!r} parent "
                                    f"Task {task_id} + a live "
                                    f"retry child Task "
                                    f"(PENDING/RUNNING) on "
                                    f"the same instance "
                                    f"{instance_id[:8] if instance_id else '?'}... "
                                    f"— lineage is still "
                                    f"alive; bare "
                                    f"finalization would "
                                    f"orphan the retry "
                                    f"child. Skipping "
                                    f"this sweep; next "
                                    f"60s cycle retries."
                                ),
                            })
                            logger.warning(
                                f"reconcile_drift_states: Pattern (f) "
                                f"skip — active JobItem "
                                f"{job_id[:8]}... has "
                                f"{task_status!r} parent Task "
                                f"{task_id} but a live retry "
                                f"child is still PENDING/RUNNING "
                                f"on instance "
                                f"{instance_id[:8] if instance_id else '?'}...; "
                                f"lineage is not quiescent, "
                                f"finalizing now would orphan "
                                f"the retry. Next 60s cycle "
                                f"retries once the child "
                                f"terminalises."
                            )
                            continue
                        # Route through the
                        # ``_fail_orphaned_job``-style
                        # boundary: lock release +
                        # failed_at + terminal_reason +
                        # notify_watchers. NOT bare DEAD —
                        # atomic_retry depends on the
                        # observer's ``failed_at`` stamp
                        # being preserved (the marker
                        # ``repository.py:2230`` requires).
                        # The Task's terminal_reason is
                        # authoritative — pass it through.
                        # This auto-resolves Warning 2.
                        boundary_ok, boundary_reason = (
                            await self._pattern_f_finalize_failed_terminal(
                                job=self._job_repository,
                                job_queue_service=self._job_queue_service,
                                lock=self._lock_repository,
                                job_item=job,
                                task=task,
                            )
                        )
                        if boundary_ok:
                            reconciled += 1
                            details.append({
                                "pattern": "orphan_active_failed_terminal",
                                "job_id": job_id,
                                "task_id": task_id,
                                "instance_id": instance_id,
                                "reason": boundary_reason,
                            })
                            logger.warning(
                                f"reconcile_drift_states: Pattern (f) "
                                f"terminal-routed orphan ACTIVE JobItem "
                                f"{job_id[:8]}... (task {task_id} "
                                f"status={task_status!r}, instance "
                                f"{instance_id[:8]}...) — finalized to "
                                f"DONE with terminal_reason="
                                f"{task_status!r} via _fail_orphaned_job-"
                                f"style boundary; lock released; "
                                f"watchers notified. NOT bare DEAD "
                                f"(atomic_retry preserved)."
                            )
                        else:
                            # Boundary no-op (already
                            # terminal) or hard failure —
                            # surface in details so the
                            # operator sees the row was
                            # observed but not corrected.
                            details.append({
                                "pattern": "orphan_active_skipped_no_deps",
                                "job_id": job_id,
                                "task_id": task_id,
                                "instance_id": instance_id,
                                "reason": boundary_reason,
                            })
                            logger.warning(
                                f"reconcile_drift_states: Pattern (f) "
                                f"could not terminal-route orphan ACTIVE "
                                f"JobItem {job_id[:8]}... (task "
                                f"{task_id} status={task_status!r}, "
                                f"instance {instance_id[:8]}...): "
                                f"{boundary_reason}"
                            )
                        continue

                # ── Sub-shape (f2) — active + COMPLETED
                # Task ───────────────────────────────
                # Task finished but the JobItem side was
                # never finalized. The Task is the
                # authoritative "this work is done"
                # signal — the JobItem side is the
                # lagging observer. Finalize as DONE with
                # ``terminal_reason='completed'`` and
                # fire the dependency watchers.
                #
                # Critical #3 gate (council REJECT
                # 2026-08-29): the pre-fix code
                # finalized on COMPLETED alone — no
                # bus check, no instance-terminal
                # check, no age floor. Mechanism B
                # (decisive): a waiting_children
                # parent's driving Task COMPLETES at
                # first-turn end (task_processor.py:
                # 856-864) while the JobItem is held
                # open SOLELY by observer gates
                # (bus :3133-3165, F14 :3196-3222) —
                # a 300s sweep vs minute-to-hour child
                # waits means f2 ROUTINELY DONE-
                # finalizes healthy parents mid-wait
                # and notify_watchers claims/deletes
                # ALL watchers (premature completed
                # UP-side, lost DOWN-side).
                # Mechanism A: the observer's
                # terminal decision stamps failed_at
                # (:3301-3302) — the marker
                # atomic_retry requires; bare done
                # forecloses retry.
                #
                # FIX (locked): gate f2 on ALL of:
                #   1. bus_pending == 0 (dependency
                #      bus reports no pending watchers
                #      for this task),
                #   2. no PENDING instance tasks
                #      (instance has no claimable
                #      Task rows), AND
                #   3. task.completed_at older than
                #      ~60s (age floor — REQUIRED,
                #      closes Mechanism A residual).
                # FAIL-SAFE: when the bus is
                # unavailable/unqueryable → SKIP
                # (leave JobItem active; next 60s
                # cycle retries). Never guess. Add
                # a distinct detail name
                # (``orphan_active_skipped_bus_unavailable``)
                # and log it.
                if (
                    task is not None
                    and task.status == TaskStatus.COMPLETED.value
                ):
                    task_id = getattr(task, "id", None)
                    # ── Gate 1: bus_pending == 0
                    # (FAIL-SAFE: bus unavailable
                    # → skip the entire f2 finalize)
                    bus_pending_count, bus_unavailable = (
                        await self._pattern_f_check_bus_pending(task_id)
                    )
                    if bus_unavailable:
                        details.append({
                            "pattern": "orphan_active_skipped_bus_unavailable",
                            "job_id": job_id,
                            "task_id": task_id,
                            "instance_id": instance_id,
                            "reason": (
                                f"dependency bus unavailable "
                                f"or unqueryable for task "
                                f"{task_id} — FAIL-SAFE: "
                                f"leaving JobItem active; "
                                f"next 60s cycle retries "
                                f"(never guess)"
                            ),
                        })
                        logger.warning(
                            f"reconcile_drift_states: Pattern (f2) "
                            f"skip — dependency bus unavailable "
                            f"for task {task_id} (job "
                            f"{job_id[:8]}..., instance "
                            f"{instance_id[:8]}...); FAIL-SAFE — "
                            f"JobItem left active, next 60s "
                            f"cycle retries"
                        )
                        continue
                    if bus_pending_count > 0:
                        details.append({
                            "pattern": "orphan_active_skipped_bus_pending",
                            "job_id": job_id,
                            "task_id": task_id,
                            "instance_id": instance_id,
                            "reason": (
                                f"dependency bus reports "
                                f"{bus_pending_count} pending "
                                f"watchers for task {task_id} — "
                                f"finalize deferred until bus "
                                f"drains (Mechanism B: a "
                                f"waiting_children parent's "
                                f"observer gate is still "
                                f"holding the JobItem open)"
                            ),
                        })
                        continue
                    # ── Gate 2: no PENDING instance
                    # tasks (the instance has no
                    # claimable Task rows — observer
                    # gate cleared)
                    has_pending_instance_tasks = (
                        await self._pattern_f_instance_has_pending_tasks(
                            instance_id
                        )
                    )
                    if has_pending_instance_tasks:
                        details.append({
                            "pattern": "orphan_active_skipped_pending_instance_tasks",
                            "job_id": job_id,
                            "task_id": task_id,
                            "instance_id": instance_id,
                            "reason": (
                                f"instance {instance_id[:8]}... "
                                f"has PENDING Task rows — "
                                f"finalize deferred until "
                                f"instance is idle"
                            ),
                        })
                        continue
                    # ── Gate 3: completed_at age
                    # floor (closes Mechanism A
                    # residual — the observer's
                    # ``failed_at`` stamp must land
                    # BEFORE we foreclose retry)
                    task_completed_at = getattr(
                        task, "completed_at", None
                    )
                    age_floor_ok, age_floor_reason = (
                        self._pattern_f_check_completed_at_age_floor(
                            task_completed_at
                        )
                    )
                    if not age_floor_ok:
                        details.append({
                            "pattern": "orphan_active_skipped_age_floor",
                            "job_id": job_id,
                            "task_id": task_id,
                            "instance_id": instance_id,
                            "reason": age_floor_reason,
                        })
                        continue
                    f2_ok, f2_reason = await self._pattern_f_finalize_done(
                        job=self._job_repository,
                        lock=self._lock_repository,
                        job_queue_service=self._job_queue_service,
                        job_item=job,
                        task=task,
                    )
                    if f2_ok:
                        reconciled += 1
                        details.append({
                            "pattern": "orphan_active_completed_task_done",
                            "job_id": job_id,
                            "task_id": task_id,
                            "instance_id": instance_id,
                            "reason": f2_reason,
                        })
                        logger.warning(
                            f"reconcile_drift_states: Pattern (f2) "
                            f"finalized orphan ACTIVE JobItem "
                            f"{job_id[:8]}... (task {task_id}, "
                            f"instance {instance_id[:8]}...) to "
                            f"DONE — Task was COMPLETED but the "
                            f"JobItem never transitioned. "
                            f"Watchers fired/cancelled."
                        )
                    else:
                        # Finalize failed (race,
                        # concurrent writer, or
                        # finalization exception).
                        # Record as a skipped_no_deps
                        # detail so the operator can see
                        # the row was observed but not
                        # corrected.
                        details.append({
                            "pattern": "orphan_active_skipped_no_deps",
                            "job_id": job_id,
                            "task_id": task_id,
                            "instance_id": instance_id,
                            "reason": f2_reason,
                        })
                        logger.warning(
                            f"reconcile_drift_states: Pattern (f2) "
                            f"could not finalize orphan ACTIVE "
                            f"JobItem {job_id[:8]}... (task "
                            f"{task_id}, instance "
                            f"{instance_id[:8]}...): {f2_reason}"
                        )
                    continue

                # ── Sub-shape (f1) — active + NO Task
                # rows ─────────────────────────────────
                # No Task row + alive/stale instance +
                # older than the grace → finalize to
                # DEAD and release the per-job lock. The
                # instance MUST be alive (or terminal-
                # stale — the brief says "alive/stale
                # instance"). A terminal instance is the
                # P1-pattern surface (Pattern (a)) and
                # the startup-recovery path; Pattern
                # (f1) is specifically the "alive but
                # structurally orphaned" class. An
                # alive instance is checked via
                # ``_is_instance_alive``.
                instance = None
                if self._instance_repository is not None:
                    try:
                        instance = await asyncio.to_thread(
                            self._instance_repository.get,
                            instance_id,
                        )
                    except Exception as inst_lookup_err:
                        logger.warning(
                            f"reconcile_drift_states: "
                            f"Pattern (f) Instance lookup "
                            f"failed for "
                            f"{instance_id[:8]}...: "
                            f"{inst_lookup_err}. Falling "
                            f"through to skip."
                        )
                        instance = None

                # If the instance is missing OR
                # terminal, this is NOT Pattern (f1) —
                # it's either a startup-recovery case
                # (orphan instance) or a Pattern (a)
                # case (P1 dead instance with no Task).
                # The startup path handles
                # ``instance_id=None``; Pattern (a)
                # handles dead instances. Skip — both
                # surfaces already correct this.
                if instance is None or self._is_instance_terminal(
                    getattr(instance, "status", None)
                ):
                    details.append({
                        "pattern": "orphan_active_skipped_no_deps",
                        "job_id": job_id,
                        "task_id": None,
                        "instance_id": instance_id,
                        "reason": (
                            f"no Task rows but instance is "
                            f"{'missing' if instance is None else 'terminal'} "
                            f"({instance.status if instance else 'n/a'}) — "
                            f"owned by recover_on_startup / "
                            f"Pattern (a), not Pattern (f1)"
                        ),
                    })
                    continue

                # If the instance is alive — this is
                # the genuine f1 candidate. Apply the
                # grace period: only JobItems with
                # ``created_at < threshold`` are
                # eligible. ``JobItem.created_at`` is
                # the canonical age signal (the model
                # has no ``updated_at``; the active
                # JobItem was enqueued at
                # ``created_at`` and ``active`` rows
                # don't receive regular updates).
                job_created = self._parse_job_created_at(
                    getattr(job, "created_at", None)
                )
                if (
                    job_created is None
                    or job_created >= threshold
                ):
                    # Either the timestamp is
                    # unparseable (defensive — should
                    # never happen for rows produced by
                    # JobRepository.create) or the row
                    # is inside the grace. Skip with a
                    # grace detail so the operator can
                    # see the row was observed.
                    details.append({
                        "pattern": "orphan_active_skipped_grace",
                        "job_id": job_id,
                        "task_id": None,
                        "instance_id": instance_id,
                        "reason": (
                            f"orphan ACTIVE JobItem (no "
                            f"Task rows, alive instance) "
                            f"is within the grace period "
                            f"(created_at="
                            f"{job.created_at!r}, "
                            f"threshold="
                            f"{threshold.isoformat()}, "
                            f"grace={min_orphan_age_seconds}s)"
                            f" — left alone, next cycle "
                            f"retries"
                        ),
                    })
                    continue

                # ── W1 mid-mint window (council REJECT
                # 2026-08-29, W1) ───────────────────
                # Queue-aged defer jobs can sit past
                # ``created_at``-grace at dispatch; the
                # spawn→Task-mint window is unguarded.
                # Without this conjunct, a just-spawned
                # instance whose Task mint is in flight
                # could match f1 BEFORE the Task row
                # exists (Task-mint is async; the
                # ``task is None`` check would pass and
                # the row would be DEAD-finalized on the
                # next 60s cycle — losing the live
                # work). Add conjunct:
                # ``instance.created_at < threshold``
                # (same grace threshold as the JobItem
                # side) so a just-spawned instance never
                # matches.
                instance_created = self._parse_job_created_at(
                    getattr(instance, "created_at", None)
                )
                if (
                    instance_created is None
                    or instance_created >= threshold
                ):
                    # Instance is fresh (inside the
                    # grace) — Task mint is likely
                    # in-flight. Skip with the grace
                    # detail so the operator sees the
                    # row was observed.
                    details.append({
                        "pattern": "orphan_active_skipped_grace",
                        "job_id": job_id,
                        "task_id": None,
                        "instance_id": instance_id,
                        "reason": (
                            f"orphan ACTIVE JobItem (no "
                            f"Task rows, alive instance) "
                            f"instance is within the grace "
                            f"period (instance.created_at="
                            f"{getattr(instance, 'created_at', None)!r}, "
                            f"threshold="
                            f"{threshold.isoformat()}, "
                            f"grace={min_orphan_age_seconds}s) "
                            f"— W1 mid-mint guard: Task "
                            f"mint is likely in flight; "
                            f"next cycle retries"
                        ),
                    })
                    continue

                # f1 confirmed — apply the DEAD
                # finalization + per-job lock release.
                f1_ok, f1_reason = await self._pattern_f_finalize_dead(
                    job=self._job_repository,
                    lock=self._lock_repository,
                    job_item=job,
                )
                if f1_ok:
                    reconciled += 1
                    details.append({
                        "pattern": "orphan_active_no_task_dead",
                        "job_id": job_id,
                        "task_id": None,
                        "instance_id": instance_id,
                        "reason": f1_reason,
                    })
                    logger.warning(
                        f"reconcile_drift_states: Pattern (f1) "
                        f"finalized orphan ACTIVE JobItem "
                        f"{job_id[:8]}... (no Task rows, "
                        f"instance {instance_id[:8]}... alive "
                        f"in {instance.status}) to DEAD — "
                        f"restart-orphan semantics. Per-job "
                        f"lock released."
                    )
                else:
                    # Finalize failed (race, concurrent
                    # writer, missing key parts, etc.).
                    # Record as a skipped_no_deps detail.
                    details.append({
                        "pattern": "orphan_active_skipped_no_deps",
                        "job_id": job_id,
                        "task_id": None,
                        "instance_id": instance_id,
                        "reason": f1_reason,
                    })
                    logger.warning(
                        f"reconcile_drift_states: Pattern (f1) "
                        f"could not finalize orphan ACTIVE "
                        f"JobItem {job_id[:8]}... (instance "
                        f"{instance_id[:8]}...): {f1_reason}"
                    )
            except Exception as per_row_err:
                logger.error(
                    f"reconcile_drift_states: Pattern (f) "
                    f"per-row check failed for job "
                    f"{getattr(job, 'job_id', '?')}: "
                    f"{per_row_err}",
                    exc_info=True,
                )

        # W6-style payload rule: return whenever any
        # detail was observed (a sweep where every
        # candidate was excluded by a guard still
        # produces details the operator should see).
        # Matches Pattern (e)'s final return shape.
        if reconciled == 0 and not details:
            return None
        return {"reconciled": reconciled, "details": details}

    @staticmethod
    def _parse_job_created_at(value):
        """Defensive parse of ``JobItem.created_at`` (string) into
        a timezone-aware ``datetime`` for grace-period
        comparison.

        JobItem stores ``created_at`` as an ISO-8601
        string (per the model default factory at
        ``daemon/repositories/job_queue/models.py:349``).
        A malformed or missing value is a defensive
        concern — the ``recover_on_startup`` and Pattern
        (a) paths assume well-formed timestamps; we
        follow suit and treat unparseable as "no
        opinion" (``None``) so the caller can skip with
        the grace guard (safer than silently treating
        as old).

        Returns ``None`` for unparseable or missing
        values; otherwise a ``datetime`` with
        ``tzinfo=timezone.utc``.
        """
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            # JobItem stores naive ISO strings in some
            # code paths; assume UTC for the grace
            # comparison.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    async def _pattern_f_finalize_dead(
        self,
        *,
        job,
        lock,
        job_item,
    ) -> tuple[bool, str]:
        """Apply the f1 DEAD finalization + per-job lock
        release.

        Two-step transaction-shaped write, both halves
        scoped to the (project_id, queue_id, job_id)
        triple so the F4/F7 invariant (no sibling-lock
        deletion) holds:

        1. Lock release via
           ``lock_repository.release_by_job`` (NOT
           ``release_by_instance`` — the buggy
           sibling-wipe surface). Skipped if any of the
           three key parts is missing (the legacy
           fallback is the same; documented in
           ``_fail_orphaned_job``).
        2. State flip via
           ``job_repository.atomic_transition`` (the
           same seam ``recover_on_startup`` uses for
           the ``processing -> failed`` finalization).
           The target state is
           ``admission_state='dead'`` (the 4-value
           ``AdmissionState`` vocabulary's
           dead-letter value — distinct from Pattern
           (a)'s ``failed`` outcome). On PostgreSQL
           the
           ``trg_job_queue_items_active_lock_guard``
           trigger requires a matching ``job_locks``
           row, which is exactly what the prior lock
           release already removed; SQLite is
           permissive.

        Both halves run on a worker thread via
        ``asyncio.to_thread`` so the event loop is not
        blocked. A partial failure (lock released,
        transition failed, or vice versa) is logged
        and reported without aborting the sweep. The
        lock release runs FIRST so a transition
        failure leaves a leaked slot rather than a
        wedged slot (the inverse wedge is the more
        common recovery path on the next cycle —
        recover_on_startup will sweep a leaked lock
        cleanly).

        Args:
            job: The ``JobRepository`` (for
                ``atomic_transition``).
            lock: The ``LockRepository`` (for scoped
                release).
            job_item: The JobItem SQLModel row.

        Returns:
            ``(ok, reason)`` tuple. ``ok=True`` means
            the transition succeeded (or was already
            terminal, no-op). ``ok=False`` means the
            transition raised or the JobItem state
            changed concurrently; ``reason`` is a
            human-readable string suitable for the
            detail record.
        """
        job_id = getattr(job_item, "job_id", None)
        project_id = getattr(job_item, "project_id", None)
        queue_id = getattr(job_item, "queue_id", None)
        instance_id = getattr(job_item, "instance_id", None)

        # 1. Scoped lock release (F4/F7). Only attempt
        # when all three key parts are present;
        # otherwise the release is a safe no-op
        # (matches the legacy fallback in
        # ``_fail_orphaned_job``).
        if project_id and queue_id and job_id and lock is not None:
            try:
                released = await asyncio.to_thread(
                    lock.release_by_job,
                    project_id,
                    queue_id,
                    job_id,
                )
                if not released:
                    # No matching lock row — fine;
                    # the JobItem was active but the
                    # slot was already released (e.g.
                    # a sibling recovery path ran).
                    # The transition below still runs.
                    logger.debug(
                        f"_pattern_f_finalize_dead: no lock row "
                        f"matched for job {job_id[:8]}... "
                        f"(project={project_id}, "
                        f"queue={queue_id}) — transition "
                        f"proceeds anyway"
                    )
            except Exception as lock_err:
                # Lock release failure must not mask
                # the transition. Log + continue.
                logger.error(
                    f"_pattern_f_finalize_dead: scoped lock "
                    f"release failed for job "
                    f"{job_id[:8]}...: {lock_err}"
                )

        # 2. Active → DEAD transition via the
        # existing ``atomic_transition`` seam.
        # ``from_status='active'`` is the canonical
        # key; ``to_status='dead'`` is the 4-value
        # ``AdmissionState`` vocabulary's DEAD value.
        try:
            now = datetime.now(timezone.utc).isoformat()
            await asyncio.to_thread(
                job.atomic_transition,
                job_id,
                from_status="active",
                to_status="dead",
                completed_at=now,
                error_message=(
                    "Pattern (f1) restart-orphan: active "
                    "JobItem with no Task rows (alive "
                    "instance) — daemon restart cleared the "
                    "task table but the JobItem row "
                    "survived; finalizing to DEAD per "
                    "RCA 802095d8"
                ),
            )
        except InvalidTransitionError:
            # Job was already terminal (concurrent
            # finalize). Expected during recovery —
            # no error, the row is in the right state
            # already. Return True so the caller
            # counts the row as handled.
            return (
                True,
                f"Pattern (f1): job {job_id[:8]}... was "
                f"already terminal (concurrent finalize); "
                f"no-op",
            )
        except Exception as trans_err:
            return (
                False,
                f"Pattern (f1): transition failed for job "
                f"{job_id[:8]}...: "
                f"{type(trans_err).__name__}: {trans_err}",
            )

        return (
            True,
            f"Pattern (f1): active JobItem with no Task "
            f"rows (alive instance "
            f"{instance_id[:8] if instance_id else '?'}...)"
            f" finalized to DEAD after grace "
            f"({job_id[:8]}...)",
        )

    async def _pattern_f_finalize_done(
        self,
        *,
        job,
        lock,
        job_queue_service,
        job_item,
        task,
    ) -> tuple[bool, str]:
        """Apply the f2 DONE finalization + scoped
        per-job lock release + dependency-watcher
        fire/cancel.

        Council REJECT 2026-08-29 Critical #2: the
        pre-fix method transitioned active→done with
        NO ``release_by_job`` (f1 has it at :2196) —
        every f2 firing on a c=1 queue (defer /
        background queues!) wedged that queue until
        restart. Fix: ``release_by_job(project_id,
        queue_id, job_id)`` between the transition
        and ``notify_watchers``, mirroring f1's
        ordering (lock-release-first; the deferred
        PG trigger
        ``trg_job_queue_items_active_lock_guard``
        evaluates final state, which requires no
        lock for terminal states).

        Three-step transaction-shaped write, all
        scoped to the (project_id, queue_id, job_id)
        triple so the F4/F7 invariant (no
        sibling-lock deletion) holds:

        1. State flip via
           ``job_repository.atomic_transition`` (the
           same seam as f1).
           ``to_status='completed'`` — the
           backward-compat ``status`` string. The
           corresponding
           ``terminal_reason='completed'`` is implied
           via the post-Phase-7 seam
           (``atomic_transition`` doesn't accept
           ``terminal_reason`` directly; the
           ``finalize_active_to_done`` repo method is
           the post-Phase-7 home for
           ``terminal_reason`` writes — for Pattern
           (f2) we keep the legacy
           ``atomic_transition`` shape to match
           ``recover_on_startup`` /
           ``_fail_orphaned_job``).

        2. Scoped per-job lock release via
           ``lock.release_by_job`` (NOT
           ``release_by_instance`` — the buggy
           sibling-wipe surface). Skipped if any of
           the three key parts is missing (the legacy
           fallback is the same; documented in
           ``_fail_orphaned_job``). On PostgreSQL the
           ``trg_job_queue_items_active_lock_guard``
           trigger requires a matching ``job_locks``
           row, which is exactly what this release
           removes; SQLite is permissive. Lock
           release runs AFTER the transition but
           BEFORE ``notify_watchers`` — same shape
           as the lock-release-first ordering f1
           uses.

        3. Watcher notify (fire/cancel) via
           ``JobQueueService.notify_watchers`` — the
           canonical "fire-then-cancel" pattern used
           on terminate paths. UP-side rows (waiting
           parents) FIRE with
           ``Outcome(status='completed')``; DOWN-side
           rows (the JobItem's own watches on its
           children) CANCEL. Best-effort: a notify
           failure is logged but does NOT roll back
           the transition (the JobItem is terminal;
           the watcher's ``_recover_fired_unsent``
           will pick it up if the notify was lost).

        Returns:
            ``(ok, reason)`` tuple mirroring
            :meth:`_pattern_f_finalize_dead`.
        """
        job_id = getattr(job_item, "job_id", None)
        project_id = getattr(job_item, "project_id", None)
        queue_id = getattr(job_item, "queue_id", None)
        task_id = getattr(task, "id", None)
        instance_id = getattr(job_item, "instance_id", None)

        # 1. Active → DONE transition.
        try:
            now = datetime.now(timezone.utc).isoformat()
            await asyncio.to_thread(
                job.atomic_transition,
                job_id,
                from_status="active",
                to_status="completed",
                completed_at=now,
                result_summary=(
                    f"Pattern (f2) completed-task orphan: "
                    f"Task {task_id} reached COMPLETED but "
                    f"the JobItem never transitioned; "
                    f"finalizing to DONE per the "
                    f"leader-locked design"
                ),
            )
        except InvalidTransitionError:
            # Job was already terminal — expected
            # during recovery; no error.
            return (
                True,
                f"Pattern (f2): job {job_id[:8]}... was "
                f"already terminal (concurrent finalize); "
                f"no-op",
            )
        except Exception as trans_err:
            return (
                False,
                f"Pattern (f2): transition failed for job "
                f"{job_id[:8]}...: "
                f"{type(trans_err).__name__}: {trans_err}",
            )

        # 2. Scoped per-job lock release (Critical #2
        # fix — pre-fix this step was missing,
        # wedging c=1 queues). Mirrors f1's lock
        # release-first ordering. Only attempt when
        # all three key parts are present; otherwise
        # the release is a safe no-op (matches the
        # legacy fallback in ``_fail_orphaned_job``).
        if project_id and queue_id and job_id and lock is not None:
            try:
                released = await asyncio.to_thread(
                    lock.release_by_job,
                    project_id,
                    queue_id,
                    job_id,
                )
                if not released:
                    # No matching lock row — fine;
                    # the JobItem was active but the
                    # slot was already released (e.g.
                    # a sibling recovery path ran).
                    # The notify below still runs.
                    logger.debug(
                        f"_pattern_f_finalize_done: no lock row "
                        f"matched for job {job_id[:8]}... "
                        f"(project={project_id}, "
                        f"queue={queue_id}) — notify proceeds "
                        f"anyway"
                    )
            except Exception as lock_err:
                # Lock release failure must not mask
                # the transition. Log + continue.
                logger.error(
                    f"_pattern_f_finalize_done: scoped lock "
                    f"release failed for job "
                    f"{job_id[:8]}...: {lock_err}"
                )

        # 3. Watcher notify (fire/cancel). Best-effort
        # — a notify failure must not mask the
        # successful transition. ``notify_watchers`` is
        # async; we're in the async wrapper here, so a
        # direct ``await`` is correct.
        notify_status = "notify_skipped_no_service"
        if job_queue_service is not None:
            try:
                await job_queue_service.notify_watchers(
                    job_id, "completed"
                )
                notify_status = "notify_completed"
            except Exception as notify_err:
                # Best-effort — log + continue.
                logger.warning(
                    f"_pattern_f_finalize_done: "
                    f"notify_watchers failed for "
                    f"{job_id[:8]}...: {notify_err}"
                )
                notify_status = (
                    f"notify_failed:{type(notify_err).__name__}"
                )

        return (
            True,
            f"Pattern (f2): active JobItem with COMPLETED "
            f"Task {task_id} (instance "
            f"{instance_id[:8] if instance_id else '?'}...) "
            f"finalized to DONE; lock_released; {notify_status}",
        )

    async def _pattern_f_finalize_failed_terminal(
        self,
        *,
        job,
        lock,
        job_queue_service,
        job_item,
        task,
    ) -> tuple[bool, str]:
        """Council REJECT 2026-08-29 Critical #1 helper:
        terminal-route an ACTIVE JobItem whose Task
        reached FAILED or CANCELLED.

        The pre-fix code let these rows fall through to
        the bare f1 DEAD path (for ``task is None`` the
        f1 path was OK; for FAILED/CANCELLED the task
        was non-null but the pre-fix guard only excluded
        PENDING/RUNNING, so the row skipped the healthy
        guard and the COMPLETED gate, falling into f1
        bare-DEAD). That broke atomic_retry: bare DEAD
        forecloses the retry the observer's ``failed_at``
        marker is supposed to gate.

        The new route is the
        ``_fail_orphaned_job``-style boundary (lock
        release + ``failed_at`` + ``terminal_reason`` +
        ``notify_watchers``). The Task's terminal status
        is authoritative for ``terminal_reason``:

        * Task FAILED    → ``terminal_reason='failed'``
        * Task CANCELLED → ``terminal_reason='cancelled'``

        Preferred path (when ``job_queue_service`` is
        wired): route through
        ``JobQueueService._finalize_terminal`` with the
        matching ``target_status``. That boundary writes
        the transition AND releases the lock AND
        notifies watchers in one place, and it preserves
        ``terminal_reason`` via the post-Phase-7 seam.

        Legacy fallback (rare — only test doubles that
        build ``JobRecoveryService`` without
        ``job_queue_service``): do the
        ``atomic_transition`` + ``release_by_job`` +
        ``notify_watchers`` triplet manually, mirroring
        the f1 lock-release-first ordering so a partial
        failure leaves a leaked slot rather than a
        wedged slot.

        Auto-resolves Warning 2: the Task's terminal
        marker (``failed_at``) is preserved across the
        JobItem transition (the boundary writes the
        ``completed_at`` column with the matching
        ``error_message`` and ``terminal_reason``);
        atomic_retry sees the marker it expects and the
        retry chain proceeds.

        Args:
            job: The ``JobRepository`` (for
                ``atomic_transition``).
            lock: The ``LockRepository`` (for scoped
                release). May be ``None`` in test
                doubles.
            job_queue_service: The ``JobQueueService``
                (preferred path through
                ``_finalize_terminal``). May be
                ``None``.
            job_item: The JobItem SQLModel row.
            task: The Task SQLModel row
                (status FAILED or CANCELLED).

        Returns:
            ``(ok, reason)`` tuple mirroring
            :meth:`_pattern_f_finalize_dead`.
        """
        job_id = getattr(job_item, "job_id", None)
        task_id = getattr(task, "id", None)
        task_status = getattr(task, "status", None)
        instance_id = getattr(job_item, "instance_id", None)

        # Map task status to target_status for the
        # boundary. FAILED → "failed",
        # CANCELLED → "cancelled".
        if task_status == TaskStatus.FAILED.value:
            target_status = "failed"
        elif task_status == TaskStatus.CANCELLED.value:
            target_status = "cancelled"
        else:
            return (
                False,
                f"Pattern (f) terminal-routing: task "
                f"{task_id} status={task_status!r} is "
                f"neither FAILED nor CANCELLED — caller "
                f"bug (should not reach this method)",
            )

        error_message = (
            f"Pattern (f) terminal-routing: Task "
            f"{task_id} reached {task_status!r} but the "
            f"JobItem never transitioned; finalizing "
            f"to {target_status!r} via _fail_orphaned_job-"
            f"style boundary (lock release + failed_at + "
            f"terminal_reason + notify_watchers). NOT "
            f"bare DEAD — atomic_retry preserved."
        )

        # Preferred path: route through the boundary.
        if job_queue_service is not None:
            try:
                canonical_job_id, _ = (
                    await job_queue_service._finalize_terminal(
                        instance_id=instance_id or "",
                        decision=Decision.NO_RETRY,
                        job_id=job_id,
                        error_message=error_message,
                        target_status=target_status,
                    )
                )
                if canonical_job_id is not None:
                    try:
                        await job_queue_service.notify_watchers(
                            job_id, target_status, error_message
                        )
                    except Exception as notify_err:
                        logger.warning(
                            f"_pattern_f_finalize_failed_terminal: "
                            f"notify_watchers failed for "
                            f"{job_id[:8]}...: {notify_err}"
                        )
                    return (
                        True,
                        f"Pattern (f): active JobItem with "
                        f"{task_status!r} Task {task_id} "
                        f"(instance "
                        f"{instance_id[:8] if instance_id else '?'}...) "
                        f"finalized to {target_status!r} via "
                        f"boundary; lock released; "
                        f"watchers notified. NOT bare DEAD "
                        f"(atomic_retry preserved).",
                    )
                # Boundary returned None — job was
                # already terminal (concurrent
                # finalize). No-op success.
                logger.debug(
                    f"_pattern_f_finalize_failed_terminal: "
                    f"_finalize_terminal no-op for job "
                    f"{job_id[:8]}... (already transitioned)"
                )
                return (
                    True,
                    f"Pattern (f): job {job_id[:8]}... was "
                    f"already terminal (concurrent finalize); "
                    f"no-op",
                )
            except Exception as boundary_err:
                return (
                    False,
                    f"Pattern (f): terminal-route boundary "
                    f"failed for job {job_id[:8]}...: "
                    f"{type(boundary_err).__name__}: "
                    f"{boundary_err}",
                )

        # Legacy fallback: manual transition + scoped
        # lock release + notify (no boundary wired).
        project_id = getattr(job_item, "project_id", None)
        queue_id = getattr(job_item, "queue_id", None)

        # 1. Scoped lock release FIRST (mirrors f1
        # lock-release-first ordering). On PG the
        # ``trg_job_queue_items_active_lock_guard``
        # trigger requires no lock for terminal states;
        # SQLite is permissive.
        if project_id and queue_id and job_id and lock is not None:
            try:
                await asyncio.to_thread(
                    lock.release_by_job,
                    project_id,
                    queue_id,
                    job_id,
                )
            except Exception as lock_err:
                logger.error(
                    f"_pattern_f_finalize_failed_terminal "
                    f"(legacy fallback): scoped lock "
                    f"release failed for job "
                    f"{job_id[:8]}...: {lock_err}"
                )

        # 2. Active → done transition with the
        # matching target_status.
        try:
            now = datetime.now(timezone.utc).isoformat()
            await asyncio.to_thread(
                job.atomic_transition,
                job_id,
                from_status="active",
                to_status=target_status,
                completed_at=now,
                error_message=error_message,
            )
        except InvalidTransitionError:
            return (
                True,
                f"Pattern (f): job {job_id[:8]}... was "
                f"already terminal (concurrent finalize); "
                f"no-op",
            )
        except Exception as trans_err:
            return (
                False,
                f"Pattern (f): transition failed for job "
                f"{job_id[:8]}...: "
                f"{type(trans_err).__name__}: {trans_err}",
            )

        return (
            True,
            f"Pattern (f): active JobItem with "
            f"{task_status!r} Task {task_id} (instance "
            f"{instance_id[:8] if instance_id else '?'}...) "
            f"finalized to {target_status!r} via "
            f"legacy fallback; lock released; "
            f"watchers_notify_skipped_no_service",
        )

    async def _pattern_f_check_bus_pending(
        self,
        task_id: int | None,
    ) -> tuple[int, bool]:
        """Council REJECT 2026-08-29 Critical #3
        Gate 1 helper: dependency-bus pending-watchers
        count for ``task_id``, with a FAIL-SAFE return
        when the bus is unavailable.

        Returns:
            ``(count, unavailable)`` tuple:
            * ``count`` — non-negative integer count of
              PENDING watchers for ``task_id`` (the bus
              is keyed by ``source_task_id``).
            * ``unavailable`` — ``True`` when the bus
              is not wired (``get_dependency_bus()``
              returned ``None``) or the count query
              raised. The caller MUST treat this as
              FAIL-SAFE (skip finalize, leave JobItem
              active; next 60s cycle retries). Never
              guess.
        """
        if task_id is None:
            # No task id means the candidate is not
            # an f2 candidate anyway; the caller
            # shouldn't have called this. Treat as
            # "unavailable" so the FAIL-SAFE path
            # catches it.
            return (0, True)
        try:
            bus = get_dependency_bus()
        except Exception as bus_lookup_err:
            logger.warning(
                f"_pattern_f_check_bus_pending: get_dependency_bus "
                f"raised: {bus_lookup_err}. FAIL-SAFE: "
                f"leaving JobItem active."
            )
            return (0, True)
        if bus is None:
            # Bus singleton not wired (test doubles,
            # partial init, or a future bus-isolation
            # incident). FAIL-SAFE: never guess.
            return (0, True)
        try:
            pending = await bus.pending_watchers(task_id)
            return (len(pending), False)
        except Exception as bus_query_err:
            logger.warning(
                f"_pattern_f_check_bus_pending: "
                f"bus.pending_watchers raised for "
                f"task {task_id}: {bus_query_err}. "
                f"FAIL-SAFE: leaving JobItem active."
            )
            return (0, True)

    async def _pattern_f_instance_has_pending_tasks(
        self,
        instance_id: str | None,
    ) -> bool:
        """Council REJECT 2026-08-29 Critical #3
        Gate 2 helper: does the instance have any
        PENDING Task rows?

        A TRUE return means the instance is still
        processing claimable work (the observer gate
        has NOT cleared). The f2 finalize must defer
        until the instance is idle.

        The check is ``TaskRepository.get_by_instance``
        filtered to ``status='pending'``. We don't
        call the more expensive
        ``count_pending_and_running_by_instance_ids``
        because Critical #3 is specifically about the
        PENDING bucket — a RUNNING task on the
        instance is a sibling concern owned by other
        surfaces (Pattern (b) / observer).

        Args:
            instance_id: The instance id to inspect.

        Returns:
            ``True`` if at least one PENDING Task
            exists for the instance, ``False`` if the
            instance has no PENDING Tasks (or the
            repository is not wired). A repository
            miss is treated as ``False`` (no pending
            tasks) — the candidate gate has other
            fail-safes (bus + age floor) to catch
            pathological cases.
        """
        if not instance_id:
            return False
        if self._task_repository is None:
            return False
        try:
            tasks = await asyncio.to_thread(
                self._task_repository.get_by_instance,
                instance_id,
            )
        except Exception as lookup_err:
            logger.warning(
                f"_pattern_f_instance_has_pending_tasks: "
                f"get_by_instance raised for "
                f"{instance_id[:8]}...: {lookup_err}. "
                f"Treating as no pending tasks (other "
                f"fail-safes cover pathological cases)."
            )
            return False
        for t in tasks or []:
            if getattr(t, "status", None) == TaskStatus.PENDING.value:
                return True
        return False

    async def _pattern_f_instance_has_inflight_task(
        self,
        instance_id: str | None,
    ) -> bool:
        """Council REJECT 2026-08-29 W1 helper: does the
        instance have any PENDING or RUNNING ``task`` row?

        Sister query to :meth:`_pattern_f_instance_has_pending_tasks`
        — widened to RUNNING so a live retry child (the
        ``schedule_retry`` / ``force_cancel_and_schedule_retry`` mint
        at ``task_repository.py:3261 / :3702`` inserts a fresh
        ``work_id`` but inherits the parent's ``instance_id`` via
        ``RetryTurn`` at ``turn_transitions.py:622``) is detectable
        from the parent's FAILED/CANCELLED branch even after the
        worker_pool claims it.

        The lineage query is intentionally NOT keyed by
        ``task.work_id``: the parent Task's ``work_id`` equals the
        JobItem's ``job_id`` (the canonical cross-system linkage
        per the dispatch contract at ``job_processor``) and the
        retry child has a DIFFERENT ``work_id`` — so a
        work_id-only check would miss the retry entirely. The
        instance_id-keyed query is the minimal, correct join
        shape.

        Used by the FAILED/CANCELLED terminal-routing branch
        (:meth:`reconcile_drift_states` Pattern (f), the W1 fix)
        to skip finalization when a live retry child Task exists
        on the same instance — finalizing the parent would
        orphan the retry child (the JobItem mirror flips
        terminal while the child Task is still driving the
        graph).

        Args:
            instance_id: The instance id to inspect.

        Returns:
            ``True`` if at least one PENDING or RUNNING Task
            exists for the instance, ``False`` if the instance
            has no in-flight Tasks (or the repository is not
            wired). A repository miss is treated as ``False``
            (no in-flight tasks) — the FAILED/CANCELLED branch
            already has a finalization path, so a conservative
            "no retry detected" reading is the right FAIL-SAFE
            (the next 60s cycle will re-check).
        """
        if not instance_id:
            return False
        if self._task_repository is None:
            return False
        try:
            return await asyncio.to_thread(
                self._task_repository.has_inflight_task,
                instance_id,
            )
        except Exception as lookup_err:
            logger.warning(
                f"_pattern_f_instance_has_inflight_task: "
                f"has_inflight_task raised for "
                f"{instance_id[:8]}...: {lookup_err}. "
                f"Treating as no in-flight tasks "
                f"(next 60s cycle re-checks; the "
                f"FAILED/CANCELLED branch will fall "
                f"through to finalize when the lookup "
                f"recovers)."
            )
            return False

    def _pattern_f_check_completed_at_age_floor(
        self,
        task_completed_at,
    ) -> tuple[bool, str]:
        """Council REJECT 2026-08-29 Critical #3
        Gate 3 helper: is ``task.completed_at`` older
        than the
        ``_F2_COMPLETED_AGE_FLOOR_SECONDS`` (60s)?

        Closes Mechanism A residual (the observer's
        ``failed_at`` stamp must land BEFORE we
        foreclose retry). A bare ``done`` transition
        that fires in the same wall-clock window
        forecloses atomic_retry; 60s gives the
        observer enough slack to land its
        ``failed_at`` stamp (and any sibling
        atomic_retry chain) BEFORE the reconciler
        finalizes the JobItem.

        Args:
            task_completed_at: The Task's
                ``completed_at`` value (datetime,
                ISO string, or ``None``).

        Returns:
            ``(ok, reason)`` tuple:
            * ``ok=True`` — the Task is past the
              floor (or floor is 0 = disabled).
            * ``ok=False`` — the Task was completed
              too recently; ``reason`` is a
              human-readable string suitable for the
              detail record.
        """
        if _F2_COMPLETED_AGE_FLOOR_SECONDS <= 0:
            # Floor disabled (defensive — should
            # never happen, the constant is 60).
            return (True, "")
        parsed = self._parse_job_created_at(task_completed_at)
        if parsed is None:
            # No ``completed_at`` is suspicious —
            # a COMPLETED Task without a
            # ``completed_at`` stamp is a defensive
            # skip (the seam is the same as
            # ``_parse_job_created_at`` for
            # ``created_at``: never guess, never
            # match). Surface a reason that names the
            # floor so the operator can see why the
            # candidate was deferred.
            return (
                False,
                f"Task has no ``completed_at`` "
                f"stamp — deferring finalize until "
                f"the age floor ({_F2_COMPLETED_AGE_FLOOR_SECONDS}s) "
                f"can be evaluated; never guess",
            )
        age_seconds = (
            datetime.now(timezone.utc) - parsed
        ).total_seconds()
        if age_seconds < _F2_COMPLETED_AGE_FLOOR_SECONDS:
            return (
                False,
                f"Task completed only {age_seconds:.1f}s ago "
                f"(age floor="
                f"{_F2_COMPLETED_AGE_FLOOR_SECONDS}s) — "
                f"deferring finalize until observer's "
                f"``failed_at`` marker can land "
                f"(Mechanism A residual); next cycle retries",
            )
        return (True, "")
