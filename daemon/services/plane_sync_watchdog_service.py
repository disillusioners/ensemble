"""Plane sync retry watchdog — Phase 3 of plane-integration-revival.

The watchdog is the systemic backstop for projects that fall into the
``error`` or ``drift`` state (the "never retries" class fix that the
Phase-2 implementation lacked). The pre-Phase-3 code path only ran
syncs on project creation; the watchdog closes the gap by periodically
re-driving error / drift rows until they reach ``linked`` or hit the
quarantine ceiling.

**Boot sweep.** On daemon start, the watchdog immediately re-drives every
project in ``error`` or ``drift`` so the ~21 stuck projects observed in
prod recover without operator intervention. The boot sweep is bounded
to a single tick before the periodic loop takes over (no special
boot-only code path — the periodic entry point is reusable).

**No-key behavior.** When ``PLANE_API_KEY`` is absent / empty the
watchdog no-ops on EVERY tick (after a single boot-time log line). It
also deliberately does NOT mark projects ``error`` merely because the
integration is off — the absence of the integration is a configuration
question, not a sync failure.

**Re-entrancy.** The watchdog never starts a sync for a row that is
already in ``syncing`` state; the manual endpoint's re-entrancy guard
takes priority (an operator-driven POST /api/plane/sync/{id} must not
race the watchdog sweep).

Modeling: borrows the ``start()`` / ``stop()`` / ``sweep_once()`` /
``_run()`` shape from :class:`daemon.services.job_lock_sweep.JobLockSweepService`
(F3 of the joblock-leak fix). Key differences:

  * The watchdog tick is heavier (N Plane HTTP calls per tick), so the
    default cadence is 300s (vs 90s for the lock sweep).
  * Per-project exponential backoff is applied INSIDE the tick (the
    lock sweep is a single atomic SQL — no per-row gating needed).
  * The watchdog has a single boot sweep before settling into the
    periodic loop (the lock sweep defers the first reclaim to the
    first ``asyncio.sleep``).

ALWAYS-ON infrastructure. Per the project owner's HARD POLICY (Batch A,
2026-09-11) codified in ``job_lock_sweep.py``: a kill-switch
defaulting OFF that gates a fix is an unacceptable deliverable. The
watchdog is ALWAYS-ON; the only tuning knobs are the interval (300s),
the backoff base/max (60s/1800s), and the max-attempts ceiling (5).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from daemon.constants import (
    PLANE_SYNC_STATE_ERROR,
    PLANE_SYNC_STATES_RETRYABLE,
    PLANE_SYNC_WATCHDOG_BACKOFF_BASE_SECONDS,
    PLANE_SYNC_WATCHDOG_BACKOFF_MAX_SECONDS,
    PLANE_SYNC_WATCHDOG_INTERVAL_SECONDS,
    PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS,
)
from daemon.services.plane_sync_service import (
    PlaneSyncService,
    iter_projects_in_states,
)
from daemon.services.timestamps import now_utc

if TYPE_CHECKING:
    from daemon.repositories.project.repository import SQLModelProjectRepository

logger = logging.getLogger(__name__)


class PlaneSyncWatchdogService:
    """Periodic retry sweep for stuck Plane sync rows.

    Each tick (``sweep_once``) walks the project repository for rows
    whose ``plane_sync_state`` is in ``PLANE_SYNC_STATES_RETRYABLE``
    (currently ``error`` and ``drift``), filters by
    :meth:`PlaneSyncService.is_retry_eligible` (per-project exponential
    backoff + max-attempts ceiling), and invokes
    :meth:`PlaneSyncService.sync_project` for the survivors.

    The result of each tick is a structured summary dict
    (``{"considered": N, "re_drove": M, "skipped_backoff": K,
    "skipped_quarantine": Q, "errors": E}``) so the boot-sweep call
    site can emit the ONE summary log line.

    Args:
        project_repo: ``SQLModelProjectRepository`` — used to walk
            project rows. The watchdog never creates / deletes
            projects; it only invokes ``PlaneSyncService.sync_project``
            (which owns all metadata writes).
        sync_service: ``PlaneSyncService`` instance. When omitted the
            watchdog constructs one internally (cheap; the service
            holds no connection state).
        interval_seconds: Steady-state cadence (default
            ``PLANE_SYNC_WATCHDOG_INTERVAL_SECONDS`` == 300s). Floor 1s;
            out-of-range config values FAIL FAST AT BOOT via the
            pydantic ``Field(ge=1)`` constraint in ``ServicesConfig``.
        backoff_base_seconds: Per-project exponential backoff base.
        backoff_max_seconds: Backoff ceiling.
        max_attempts: Quarantine threshold.

    Lifecycle:
        * ``start()`` — spawn the periodic sweep as an asyncio task.
          Idempotent.
        * ``stop()`` — cancel + await the asyncio task. Safe to call
          when the task was never started (silent no-op).
        * ``sweep_once()`` — single tick, returns a structured dict.
          Public entry point so tests + the lifespan's boot anchor can
          drive a single deterministic pass.

    No-key behavior:
        The watchdog no-ops (logs ONE line at boot, then silence) when
        ``PlaneSyncService.is_available()`` is False. The
        ``is_available`` check is the same gate the sync service uses;
        it gates on ``PLANE_BASE_URL`` + ``PLANE_API_KEY``. The
        watchdog deliberately does NOT mark projects ``error`` —
        absence of the integration is a configuration question, not a
        sync failure.
    """

    def __init__(
        self,
        project_repo: "SQLModelProjectRepository",
        sync_service: PlaneSyncService | None = None,
        *,
        interval_seconds: int = PLANE_SYNC_WATCHDOG_INTERVAL_SECONDS,
        backoff_base_seconds: int = PLANE_SYNC_WATCHDOG_BACKOFF_BASE_SECONDS,
        backoff_max_seconds: int = PLANE_SYNC_WATCHDOG_BACKOFF_MAX_SECONDS,
        max_attempts: int = PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS,
    ) -> None:
        self._repo = project_repo
        # Lazy-build the service so a disabled integration can still
        # construct the watchdog for the boot-time log line.
        self._sync_service = sync_service
        self._interval_seconds = max(1, int(interval_seconds))
        self._backoff_base_seconds = int(backoff_base_seconds)
        self._backoff_max_seconds = int(backoff_max_seconds)
        self._max_attempts = int(max_attempts)
        self._task: asyncio.Task[None] | None = None
        self._stopping: bool = False
        self._boot_log_emitted: bool = False

    @property
    def interval_seconds(self) -> int:
        return self._interval_seconds

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    def _get_service(self) -> PlaneSyncService:
        """Resolve the sync service — lazy-build on first use."""
        if self._sync_service is None:
            self._sync_service = PlaneSyncService(self._repo)
        return self._sync_service

    def start(self) -> None:
        """Spawn the periodic sweep as an asyncio task. Idempotent."""
        if self._task is not None and not self._task.done():
            logger.debug(
                "PlaneSyncWatchdogService: start() called while already "
                "running — no-op"
            )
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="PlaneSyncWatchdog")
        logger.info(
            "PlaneSyncWatchdogService started: interval=%ds "
            "backoff_base=%ds backoff_max=%ds max_attempts=%d",
            self._interval_seconds,
            self._backoff_base_seconds,
            self._backoff_max_seconds,
            self._max_attempts,
        )

    async def stop(self) -> None:
        """Cancel the sweep task and await cancellation. Silent no-op if never started."""
        if self._task is None:
            return
        self._stopping = True
        task = self._task
        self._task = None
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        logger.info("PlaneSyncWatchdogService stopped")

    async def sweep_once(self) -> dict[str, int]:
        """Run a single re-drive tick. Returns the summary dict.

        The summary dict is shaped::

            {
              "considered": int,        # rows in retryable states
              "re_drove": int,           # sync_project calls actually made
              "skipped_backoff": int,    # rows whose backoff window has not elapsed
              "skipped_quarantine": int, # rows at/above max_attempts
              "errors": int,             # re-drive attempts that raised
              "skipped_disabled": int,   # rows skipped because the integration is off
            }

        The watchdog NEVER marks projects ``error`` — that is the sync
        service's job. A "skipped_disabled" count simply notes that the
        row is in retryable state but we cannot act on it until the
        operator configures ``PLANE_API_KEY``.
        """
        if not PlaneSyncService.is_available():
            # Emit ONE boot-time log line, then silence. The "stuck
            # projects recover on activation" message lives here so an
            # operator tailing the log sees the gap immediately.
            if not self._boot_log_emitted:
                logger.info(
                    "PlaneSyncWatchdogService: PLANE_API_KEY not set — "
                    "watchdog disabled (no re-drives; projects in "
                    "error/drift will recover on next activation)"
                )
                self._boot_log_emitted = True
            return {
                "considered": 0,
                "re_drove": 0,
                "skipped_backoff": 0,
                "skipped_quarantine": 0,
                "errors": 0,
                "skipped_disabled": 0,
            }

        try:
            candidates = iter_projects_in_states(
                self._repo,
                PLANE_SYNC_STATES_RETRYABLE,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PlaneSyncWatchdogService: failed to enumerate retryable "
                "projects: %s",
                exc,
            )
            return {
                "considered": 0,
                "re_drove": 0,
                "skipped_backoff": 0,
                "skipped_quarantine": 0,
                "errors": 0,
                "skipped_disabled": 0,
            }

        summary = {
            "considered": len(candidates),
            "re_drove": 0,
            "skipped_backoff": 0,
            "skipped_quarantine": 0,
            "errors": 0,
            "skipped_disabled": 0,
        }
        if not candidates:
            return summary

        service = self._get_service()
        for project in candidates:
            pid = project["project_id"]
            # Per-project backoff gate.
            if not service.is_retry_eligible(pid, now=now_utc()):
                summary["skipped_backoff"] += 1
                continue
            try:
                result = await service.sync_project(pid)
            except Exception as exc:  # noqa: BLE001 — sync_project is contractually never-raises
                logger.warning(
                    "PlaneSyncWatchdogService: sync_project raised for %s "
                    "(should not happen — sync_project is never-raise): %s",
                    pid,
                    exc,
                )
                summary["errors"] += 1
                continue
            # Mirror ``sync_project`` semantics: the slot-claim guard
            # returns ``status=syncing`` when another caller holds the
            # row. We count those as "skipped_disabled" (semantically
            # "skipped because another caller owns the row").
            status = result.get("status")
            if status == "syncing":
                summary["skipped_disabled"] += 1
                continue
            if status == "error":
                # The row was either still in error after the attempt
                # (the attempt was a no-op because the prior attempt
                # already exhausted max_attempts) OR the row errored.
                # Both count as errors; the attempt counter was bumped
                # by the sync service.
                summary["errors"] += 1
                continue
            if status in ("linked", "drift"):
                summary["re_drove"] += 1
                continue
            # Unknown status — treat as no-op (defensive).
            logger.debug(
                "PlaneSyncWatchdogService: sync_project returned unknown "
                "status %r for %s — counting as skipped",
                status,
                pid,
            )
        return summary

    async def _run(self) -> None:
        """Periodic tick loop. Exits on ``stop()`` cancellation.

        The first tick runs IMMEDIATELY (no leading sleep) so the
        boot-sweep path actually executes at startup. Subsequent ticks
        run on the steady-state cadence.
        """
        try:
            # Boot sweep — runs FIRST before any sleep so the ~21
            # stuck projects in prod recover without operator
            # intervention. The same path handles periodic ticks; the
            # only difference is the call site (lifespan vs periodic).
            while not self._stopping:
                summary = await self.sweep_once()
                if summary["considered"] or summary["errors"]:
                    logger.info(
                        "PlaneSyncWatchdogService tick: %s",
                        summary,
                    )
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            return
        except Exception as loop_err:  # noqa: BLE001
            logger.error(
                "PlaneSyncWatchdogService: unexpected loop error: "
                "%s: %s — exiting",
                type(loop_err).__name__,
                loop_err,
                exc_info=True,
            )
            return
