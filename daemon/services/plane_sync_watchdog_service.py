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
race the watchdog sweep). ONE exception (Phase 4): a ``syncing`` row
stale for > N x interval is treated as crash wreckage — the boot sweep
steals it back to ``error`` (rowcount-guarded CAS) and re-drives it.

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

ALWAYS-ON infrastructure, with one sanctioned exception. Per the
project owner's HARD POLICY (Batch A, 2026-09-11) codified in
``job_lock_sweep.py``: a kill-switch defaulting OFF that gates a fix is
an unacceptable deliverable — the watchdog itself has no default-OFF
gate. The ``PLANE_SYNC_ENABLED`` integration kill-switch (Phase 4,
explicitly user-requested, sanctioned 2026-09-20) is the single
exception: it disables the WHOLE Plane integration (watchdog included),
defaults ON, and its OFF state is announced by a one-line boot log.
Other levers are the interval (300s), the backoff base/max (60s/1800s),
and the max-attempts ceiling (5).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from daemon.constants import (
    PLANE_SYNC_STATE_ERROR,
    PLANE_SYNC_STATE_SYNCING,
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

# Phase-4 advisory a(2): a ``syncing`` row whose attempt is older than
# N x sweep interval is treated as a crash wedge (the claimer died
# between claim and release). N=2 means: a healthy in-flight sync (HTTP
# round-trips are seconds) is never falsely stolen, while a row wedged
# by a dead process is recovered within ~2 ticks of the crash.
STALE_SYNCING_INTERVAL_MULTIPLIER: int = 2


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
              "recovered_stale_syncing": int,  # crash-wedged syncing rows stolen + re-driven
            }

        The watchdog NEVER marks projects ``error`` on its own
        initiative — with ONE exception (Phase-4 advisory a(2)): rows
        wedged in ``syncing`` for more than
        ``STALE_SYNCING_INTERVAL_MULTIPLIER x interval`` are crash
        wreckage (the claimer died between claim and release); the
        sweep steals the slot back to ``error`` and immediately
        re-drives the row. A healthy in-flight sync is never stolen —
        its attempt timestamp is seconds old and it releases its own
        slot in the same flow.

        A "skipped_disabled" count simply notes that the
        row is in retryable state but we cannot act on it until the
        operator enables the integration (kill-switch or key config).
        """
        if not PlaneSyncService.is_available():
            # Emit ONE boot-time log line, then silence. The reason is
            # differentiated so an operator tailing the log knows which
            # lever to pull: the kill-switch vs the env config.
            if not self._boot_log_emitted:
                logger.info(
                    "PlaneSyncWatchdogService: Plane sync %s — "
                    "watchdog no-op (no re-drives; projects in "
                    "error/drift will recover on next activation)",
                    PlaneSyncService.unavailable_reason(),
                )
                self._boot_log_emitted = True
            return {
                "considered": 0,
                "re_drove": 0,
                "skipped_backoff": 0,
                "skipped_quarantine": 0,
                "errors": 0,
                "skipped_disabled": 0,
                "recovered_stale_syncing": 0,
            }

        # ONE walk over the projects in any watchdog-relevant state
        # (retryable + stuck-in-syncing), partitioned client-side via
        # the normalized state each row carries — no second metadata
        # read pass.
        sweep_states = PLANE_SYNC_STATES_RETRYABLE | frozenset(
            {PLANE_SYNC_STATE_SYNCING}
        )
        try:
            rows = iter_projects_in_states(self._repo, sweep_states)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PlaneSyncWatchdogService: failed to enumerate "
                "retryable projects: %s",
                exc,
            )
            return {
                "considered": 0,
                "re_drove": 0,
                "skipped_backoff": 0,
                "skipped_quarantine": 0,
                "errors": 0,
                "skipped_disabled": 0,
                "recovered_stale_syncing": 0,
            }

        service = self._get_service()
        stale_after = (
            STALE_SYNCING_INTERVAL_MULTIPLIER * self._interval_seconds
        )

        summary = {
            "considered": 0,
            "re_drove": 0,
            "skipped_backoff": 0,
            "skipped_quarantine": 0,
            "errors": 0,
            "skipped_disabled": 0,
            "recovered_stale_syncing": 0,
        }

        # ── Crash-wedge recovery pass (stale ``syncing`` rows) ────────
        # Partitioned from the retryable list so a row stolen here is
        # re-driven exactly once (the retryable pass below never sees it).
        for row in rows:
            if row["state"] != PLANE_SYNC_STATE_SYNCING:
                continue
            pid = row["project_id"]
            if not service.is_stale_syncing(
                pid, stale_after_seconds=stale_after, now=now_utc()
            ):
                continue  # healthy in-flight sync — leave it alone
            if not service.fail_stale_syncing(pid):
                continue  # lost the steal race — the live sync released
            summary["recovered_stale_syncing"] += 1
            logger.warning(
                "PlaneSyncWatchdogService: recovered stale syncing row %s "
                "(no release for >%ds — crash wedge) — re-driving",
                pid,
                stale_after,
            )
            await self._redrive(service, pid, summary)

        # ── Retryable pass (error / drift rows) ───────────────────────
        candidates = [
            row for row in rows if row["state"] in PLANE_SYNC_STATES_RETRYABLE
        ]
        summary["considered"] = len(candidates)
        if not candidates:
            return summary

        for project in candidates:
            await self._redrive(service, project["project_id"], summary)
        return summary

    async def _redrive(
        self,
        service: PlaneSyncService,
        pid: str,
        summary: dict[str, int],
    ) -> None:
        """Re-drive one project, updating ``summary`` in place.

        Shared by the crash-wedge recovery pass and the retryable pass.
        """
        # Per-project backoff gate.
        if not service.is_retry_eligible(pid, now=now_utc()):
            summary["skipped_backoff"] += 1
            return
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
            return
        # Mirror ``sync_project`` semantics: the slot-claim guard
        # returns ``status=syncing`` when another caller holds the
        # row. We count those as "skipped_disabled" (semantically
        # "skipped because another caller owns the row").
        status = result.get("status")
        if status == "syncing":
            summary["skipped_disabled"] += 1
            return
        if status == "error":
            # The row was either still in error after the attempt
            # (the attempt was a no-op because the prior attempt
            # already exhausted max_attempts) OR the row errored.
            # Both count as errors; the attempt counter was bumped
            # by the sync service.
            summary["errors"] += 1
            return
        if status in ("linked", "drift"):
            summary["re_drove"] += 1
            return
        # Unknown status — treat as no-op (defensive).
        logger.debug(
            "PlaneSyncWatchdogService: sync_project returned unknown "
            "status %r for %s — counting as skipped",
            status,
            pid,
        )

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
                if (
                    summary["considered"]
                    or summary["errors"]
                    or summary["recovered_stale_syncing"]
                ):
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
