"""G5 — Daemon-side daily Blueprint scan, registered with MaintenanceService.

Runs when the system is idle AND ``auto_rebuild_enabled`` is True.
Implements smart trigger logic:

* **Empty corpus** (no active blueprints for a project) → enqueue a
  ``rebuild`` job via :class:`BlueprintTriggerCoordinator`.
* **Has blueprints + pending updates** → enqueue an ``incremental``
  job via the coordinator.
* **Has blueprints + no pending** → skip (nothing to do).

The coordinator is the single chokepoint for enqueuing — see
:mod:`daemon.services.blueprint_trigger_coordinator` for the claim
contract and ``/rebuild`` / ``/update`` semantics.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import logging
import uuid
from typing import Any

from daemon.constants import BLUEPRINT_ACTIVE_METADATA_KEY, SYSTEM_DEFAULT_PROJECT_NAME

logger = logging.getLogger(__name__)


# Built-in mode identifiers. The API layer mirrors these as string
# literals; keep them in sync if either side changes.
MODE_REBUILD = "rebuild"
MODE_INCREMENTAL = "incremental"

# Configurable peak-hours gate. The defaults match the original behaviour
# (12:00 - 20:00) but in GMT+7 rather than UTC, since most operator
# timelines are anchored to a local working day. The values are stored on
# the system default project's metadata KV so operators can tweak them via
# the Settings UI without restarting the daemon.
_SYSTEM_DEFAULT_PID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
_PEAK_HOURS_START_KEY = "blueprint_peak_hours_start"
_PEAK_HOURS_END_KEY = "blueprint_peak_hours_end"
_PEAK_HOURS_TZ_KEY = "blueprint_peak_hours_tz_offset"

# Defaults (used when metadata is absent or read fails).
_DEFAULT_PEAK_START = 12
_DEFAULT_PEAK_END = 20
_DEFAULT_TZ_OFFSET = 7  # GMT+7


class BlueprintScanService:
    """Daemon-side blueprint daily scan, gated by the auto-rebuild flag.

    Registered with :class:`MaintenanceService` and invoked on its
    interval (default: every 24 hours, configurable via
    the MaintenanceService interval (currently 24 hours).

    Args:
        blueprint_repo: :class:`BlueprintRepository` for
            ``list_by_project`` (read-only; safe to share across
            threads).
        pending_repo: :class:`BlueprintPendingRepository` for
            ``get_pending_count`` (read-only).
        coordinator: :class:`BlueprintTriggerCoordinator` — the single
            enqueue chokepoint.
        config: :class:`BlueprintConfig`. The ``auto_rebuild_enabled``
            flag is read on every ``execute()`` call so an operator
            can flip it without restarting the daemon.
        project_repository: :class:`SQLModelProjectRepository` for
            ``list_projects`` (used to discover active projects).
        job_queue_service: Optional :class:`JobQueueService`. When
            ``None`` at construction (the common path — the service is
            not yet built), wire it lazily via
            :meth:`set_job_queue_service`. The scan service needs it to
            actually enqueue blueprinter jobs after a coordinator claim.
    """

    def __init__(
        self,
        blueprint_repo: Any,
        pending_repo: Any,
        coordinator: Any,
        config: Any,
        project_repository: Any,
        job_queue_service: Any = None,
    ) -> None:
        self._blueprint_repo = blueprint_repo
        self._pending_repo = pending_repo
        self._coordinator = coordinator
        self._config = config
        self._project_repository = project_repository
        self._job_queue_service = job_queue_service

    def set_job_queue_service(self, service: Any) -> None:
        """Wire the JobQueueService (called lazily after construction).

        The manager constructs the scan service during ``__init__``
        before the JobQueueService exists; it calls this method from
        its own ``set_job_queue_service`` once the queue service is
        available.
        """
        self._job_queue_service = service

    # ── Public surface ────────────────────────────────────────────────

    async def execute(self) -> None:
        """Called by MaintenanceService on its interval.

        Short-circuits when ``auto_rebuild_enabled`` is False (the
        default). Never raises — any per-project exception is logged
        and swallowed so one bad project does not abort the whole
        scan.
        """
        # Skip during peak hours. Config is read FRESH from the system
        # default project metadata on every execute() so an operator can
        # adjust the window via the Settings UI without restarting the
        # daemon. Defaults: 12:00 - 20:00 GMT+7.
        peak_start = _DEFAULT_PEAK_START
        peak_end = _DEFAULT_PEAK_END
        tz_offset = _DEFAULT_TZ_OFFSET
        try:
            peak_start = int(await asyncio.to_thread(
                self._project_repository.get_metadata,
                _SYSTEM_DEFAULT_PID, _PEAK_HOURS_START_KEY,
            ) or _DEFAULT_PEAK_START)
            peak_end = int(await asyncio.to_thread(
                self._project_repository.get_metadata,
                _SYSTEM_DEFAULT_PID, _PEAK_HOURS_END_KEY,
            ) or _DEFAULT_PEAK_END)
            tz_offset = int(await asyncio.to_thread(
                self._project_repository.get_metadata,
                _SYSTEM_DEFAULT_PID, _PEAK_HOURS_TZ_KEY,
            ) or _DEFAULT_TZ_OFFSET)
        except Exception:
            # Metadata read failed (repo down, project row missing, etc.) —
            # fall back to defaults rather than aborting the whole scan.
            pass

        tz = timezone(timedelta(hours=tz_offset))
        now = datetime.now(tz)
        if peak_start <= now.hour < peak_end:
            logger.info(
                "BlueprintScanService: skipping scan during peak hours "
                "(hour=%d, peak=%d-%d, tz=GMT%+d)",
                now.hour, peak_start, peak_end, tz_offset,
            )
            return

        if not getattr(self._config, "auto_rebuild_enabled", False):
            logger.debug("Blueprint daily scan skipped (auto_rebuild_enabled=False)")
            return

        try:
            project_ids = await self._get_active_projects()
        except Exception as exc:
            logger.warning("BlueprintScanService: failed to list projects: %s", exc)
            return

        for project_id in project_ids:
            try:
                await self._scan_project(project_id)
            except Exception as exc:
                # Don't let one bad project break the whole scan.
                logger.warning(
                    "BlueprintScanService: scan failed for project %s: %s",
                    project_id, exc,
                )

        # Persist last_run so the scan doesn't fire immediately on
        # restart. The MaintenanceService keeps ``last_run`` in memory
        # only — without this, a daemon restart would re-arm the
        # 24h-interval clock and run the scan as soon as the worker
        # loop next ticks. The timestamp lives in project metadata KV
        # on the system default project (a bookkeeping home — the scan
        # itself never operates on it).
        #
        # Wrapped in ``asyncio.to_thread`` because
        # ``SQLModelProjectRepository.set_metadata`` is sync SQLAlchemy
        # (ADR-12) — calling it directly would block the event loop on
        # the project DB write. Failure is non-fatal: if the metadata
        # write fails we log + swallow; the next scan will retry the
        # persist and the in-memory ``last_run`` still updates
        # immediately after execute() returns.
        _SCAN_LAST_RUN_KEY = "blueprint_scan_last_run"
        try:
            await asyncio.to_thread(
                self._project_repository.set_metadata,
                _SYSTEM_DEFAULT_PID,
                _SCAN_LAST_RUN_KEY,
                datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            logger.warning(
                "BlueprintScanService: failed to persist last_run timestamp",
                exc_info=True,
            )

    # ── Internals ─────────────────────────────────────────────────────

    async def _get_active_projects(self) -> list[str]:
        """Return all active project IDs.

        We deliberately do NOT filter by status (active vs. archived)
        — a dormant project that becomes active again is exactly the
        case where a rebuild matters. Filtering by status here would
        be a foot-gun.

        The system default project (``__system_default__``) is a
        virtual bookkeeping project — no blueprints should ever be
        built for it, so we exclude it from the scan entirely.

        Per-project opt-in gate: each project must have the
        ``BLUEPRINT_ACTIVE_METADATA_KEY`` metadata key set to a truthy
        value (``True``/``"true"``/etc.). Absent = inactive (skip).
        ``get_metadata`` is sync SQLAlchemy; wrap in
        ``asyncio.to_thread`` per ADR-12.
        """
        # list_projects is sync SQLAlchemy; push it to a thread so we
        # never block the event loop on disk I/O.
        projects = await asyncio.to_thread(
            self._project_repository.list_projects, limit=10_000,
        )
        result: list[str] = []
        for p in projects:
            pid = getattr(p, "project_id", None)
            if not pid:
                continue
            if getattr(p, "name", None) == SYSTEM_DEFAULT_PROJECT_NAME:
                continue
            # Per-project opt-in gate (default: false = skip). Failure
            # to read the metadata must NOT hard-abort the whole scan
            # — treat the project as inactive (the safer default).
            try:
                active = await asyncio.to_thread(
                    self._project_repository.get_metadata,
                    pid,
                    BLUEPRINT_ACTIVE_METADATA_KEY,
                )
            except Exception:
                active = None
            if not bool(active):
                continue
            result.append(pid)
        return result

    async def _scan_project(self, project_id: str) -> None:
        """Smart trigger logic for one project.

        * Empty corpus → ``rebuild``
        * Pending rows > 0 → ``incremental``
        * Otherwise → skip
        """
        # Both repo calls hit SQLite/PostgreSQL — keep them off the loop.
        blueprints = await asyncio.to_thread(
            self._blueprint_repo.list_by_project, project_id,
        )
        pending_count = await asyncio.to_thread(
            self._pending_repo.get_pending_count, project_id,
        )

        if not blueprints:
            await self._trigger(project_id, MODE_REBUILD)
            return

        # A core blueprint alone is only a scaffold; area blueprints must be rebuilt.
        has_non_core = any(b.kind != "core" for b in blueprints)
        if not has_non_core:
            await self._trigger(project_id, MODE_REBUILD)
            return

        if pending_count > 0:
            await self._trigger(project_id, MODE_INCREMENTAL)
        else:
            logger.debug(
                "BlueprintScanService: project %s has %d blueprint(s) and "
                "no pending — skipping",
                project_id, len(blueprints),
            )

    async def _trigger(self, project_id: str, mode: str) -> None:
        """Route through the coordinator AND enqueue the blueprinter job.

        The coordinator decides whether to claim (and which ``job_id``
        wins), coalesce onto an in-flight same-mode build, or surface
        a cross-mode conflict. On a fresh claim we must enqueue the
        blueprinter job on the background queue — a claim without an
        enqueue leaks the lease and produces no work.

        On enqueue failure we release the lease so the next scan pass
        (or a manual ``/rebuild``) can retry.
        """
        job_id = str(uuid.uuid4())
        try:
            result = await self._coordinator.try_claim(project_id, mode, job_id)
        except Exception as exc:
            logger.warning(
                "BlueprintScanService: coordinator.try_claim raised for "
                "project %s mode %s: %s",
                project_id, mode, exc,
            )
            return

        if result.claimed:
            # Enqueue the blueprinter job — the claim is useless without it.
            message = self._build_message(mode)
            try:
                from .blueprint_job_helper import enqueue_blueprinter_job

                await enqueue_blueprinter_job(
                    job_queue_service=self._job_queue_service,
                    project_id=project_id,
                    trigger_type=mode,
                    message=message,
                    run_token=result.run_token,
                    job_id=job_id,
                    source="auto-scan",
                )
            except Exception:
                # Enqueue failed — release the lease so it can be retried
                # on the next scan or a manual trigger. Swallow release
                # errors; there's nothing useful to do with them here.
                try:
                    await self._coordinator.release(project_id, result.run_token)
                except Exception:
                    pass
                logger.error(
                    "BlueprintScanService: enqueue failed for project %s "
                    "mode %s, lease released",
                    project_id, mode,
                    exc_info=True,
                )
            else:
                logger.info(
                    "BlueprintScanService: enqueued %s for project %s "
                    "(job_id=%s, token=%s)",
                    mode, project_id, job_id, result.run_token,
                )
        elif result.coalesced:
            logger.debug(
                "BlueprintScanService: %s already in progress for project %s "
                "(in-flight job=%s)",
                mode, project_id, result.job_id,
            )
        else:
            logger.debug(
                "BlueprintScanService: %s blocked — project %s has a "
                "different-mode build (mode=%s) in flight",
                mode, project_id, result.conflict_mode,
            )

    def _build_message(self, mode: str) -> str:
        """Build the blueprinter dispatch message for the given mode.

        Mirrors the message bodies the REST ``/rebuild`` and ``/update``
        endpoints send so the blueprinter agent behaves the same whether
        triggered by the daily scan or a manual admin click.
        """
        if mode == MODE_REBUILD:
            return (
                "Rebuild all project blueprints.\n\n"
                "Perform a full rebuild: create the core blueprint and all "
                "area blueprints from scratch. Generate trigger queries for "
                "each. Respect the rate limit."
            )
        return (
            "Incremental blueprint update.\n\n"
            "Process accumulated pending-experience changes. Review existing "
            "blueprints for drift. Respect the rate limit."
        )


__all__ = ["BlueprintScanService", "MODE_REBUILD", "MODE_INCREMENTAL"]
