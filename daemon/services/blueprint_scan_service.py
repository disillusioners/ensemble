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
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


# Built-in mode identifiers. The API layer mirrors these as string
# literals; keep them in sync if either side changes.
MODE_REBUILD = "rebuild"
MODE_INCREMENTAL = "incremental"


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
    """

    def __init__(
        self,
        blueprint_repo: Any,
        pending_repo: Any,
        coordinator: Any,
        config: Any,
        project_repository: Any,
    ) -> None:
        self._blueprint_repo = blueprint_repo
        self._pending_repo = pending_repo
        self._coordinator = coordinator
        self._config = config
        self._project_repository = project_repository

    # ── Public surface ────────────────────────────────────────────────

    async def execute(self) -> None:
        """Called by MaintenanceService on its interval.

        Short-circuits when ``auto_rebuild_enabled`` is False (the
        default). Never raises — any per-project exception is logged
        and swallowed so one bad project does not abort the whole
        sweep.
        """
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
                # Don't let one bad project break the whole sweep.
                logger.warning(
                    "BlueprintScanService: scan failed for project %s: %s",
                    project_id, exc,
                )

    # ── Internals ─────────────────────────────────────────────────────

    async def _get_active_projects(self) -> list[str]:
        """Return all active project IDs.

        We deliberately do NOT filter by status (active vs. archived)
        — a dormant project that becomes active again is exactly the
        case where a rebuild matters. Filtering by status here would
        be a foot-gun.
        """
        # list_projects is sync SQLAlchemy; push it to a thread so we
        # never block the event loop on disk I/O.
        projects = await asyncio.to_thread(
            self._project_repository.list_projects, limit=10_000,
        )
        return [getattr(p, "project_id", None) for p in projects if getattr(p, "project_id", None)]

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
        """Route through the coordinator.

        The coordinator decides whether to claim (and which ``job_id``
        wins), coalesce onto an in-flight same-mode build, or surface
        a cross-mode conflict. We just observe and log.
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
            logger.info(
                "BlueprintScanService: triggered %s for project %s "
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


__all__ = ["BlueprintScanService", "MODE_REBUILD", "MODE_INCREMENTAL"]
