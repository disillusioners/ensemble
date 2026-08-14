"""Structural sync between Ensemble projects and Plane projects.

The :class:`PlaneSyncService` mirrors an Ensemble ``Project`` to Plane as
a flat project (no issues, no cycles at v1). The mapping uses
``name`` ↔ ``Plane.name``, ``description`` ↔ ``Plane.description``, and
``status`` → ``Plane.state`` (via :data:`daemon.constants.PLANE_STATUS_MAP`).

Persistence
-----------
Sync state is stored on the project itself via three project metadata
records (see :mod:`daemon.constants`):

- ``plane_project_id`` — Plane's internal UUID; the primary mapping handle.
- ``plane_sync_state`` — ``"synced"`` | ``"error"`` | ``"pending"``.
- ``plane_synced_at`` — ISO8601 timestamp of the most recent attempt.

Read/write goes through ``repo.list_metadata_records`` (single call, then
filter client-side) and ``repo.set_metadata_record`` — the lower-level
record-level methods, *not* the convenience ``set_metadata`` wrapper that
also rewrites ``Project.updated_at`` (CR-6).

Error contract
--------------
``sync_project`` **never raises**. On any Plane API error it records
``plane_sync_state="error"``, logs a warning, and returns a structured
result. The caller (HTTP router or agent tool) decides whether to surface
the error to the user; the project itself is unaffected.

v1 limitation
-------------
Status and name changes are **not** automatically mirrored to Plane.
Sync happens on project creation (via auto-sync hooks in
``daemon.tools.project`` and ``daemon.routers.projects``) and on explicit
manual invocation via the ``plane_sync_project`` agent tool. Subsequent
``project_set_status`` / ``project_update`` calls do not trigger a sync
— operator must call the tool explicitly. This is a deliberate scope
limitation for v1; a future hook layer can close the loop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session

from daemon.clients.plane_http_client import (
    PlaneAPIError,
    PlaneAuthError,
    PlaneHttpClient,
    PlaneNotFoundError,
)
from daemon.constants import (
    PLANE_PROJECT_ID_METADATA_KEY,
    PLANE_STATUS_MAP,
    PLANE_SYNC_STATE_METADATA_KEY,
    PLANE_SYNCED_AT_METADATA_KEY,
)
from daemon.repositories.project.models import Project
from daemon.repositories.project.repository import SQLModelProjectRepository

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return the current UTC time as ISO8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _project_state_for_plane(status: str | None) -> str:
    """Map an Ensemble ``ProjectStatus`` value to Plane's state vocabulary."""
    if not status:
        return PLANE_STATUS_MAP.get("active", "active")
    return PLANE_STATUS_MAP.get(status, "active")


def _read_metadata_value(
    metadata_records: list[Any],
    key: str,
) -> Any:
    """Return ``meta_value`` from the first record matching ``key``.

    Operates on a pre-fetched list of ``ProjectMetadataRecord`` rows so
    callers can issue a single ``list_metadata_records`` call and filter
    client-side for the keys they care about (CR-6).
    """
    for record in metadata_records:
        if getattr(record, "meta_key", None) == key:
            return getattr(record, "meta_value", None)
    return None


# Module-level reusable executor (W3).
#
# Avoids the two hazard patterns observed in the duplicated sync hooks:
#   (W1) ``with ThreadPoolExecutor() as executor: executor.submit(...)`` —
#        the ``with`` __exit__ calls ``shutdown(wait=True)``, which blocks
#        the caller until the background work finishes (defeats the
#        fire-and-forget intent).
#   (C2) bare ``asyncio.ensure_future`` + ``loop.run_until_complete`` —
#        raises ``RuntimeError: This event loop is already running`` and
#        leaks the orphan Task.
#
# A single module-level executor lets the fire-and-forget path submit and
# return immediately. ``max_workers=2`` caps concurrent syncs to two
# projects in flight at once (Plane API rate limits + DB pool safety).
_plane_sync_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def trigger_sync_fire_and_forget(
    project_id: str,
    project_repo: "SQLModelProjectRepository",
) -> None:
    """Fire-and-forget Plane sync — non-blocking, best-effort.

    Consolidates the async-driving pattern that was duplicated across
    ``daemon.tools.project``, ``daemon.routers.projects``, and
    ``daemon.tools.plane_sync`` (W3). Designed for the auto-sync hooks
    that run on project creation, where the caller must return quickly
    and must not be coupled to Plane's response.

    Handles three cases:

    1. No event loop bound → run directly via ``asyncio.run``.
    2. Event loop running → submit to the shared module-level
       ``_plane_sync_executor`` (a fresh thread with its own event loop).
    3. Any error during dispatch → log and swallow. The caller is never
       blocked and never sees an exception from Plane sync.

    This function is fire-and-forget: it returns as soon as the work is
    *submitted* (or run directly when no loop is bound). It does NOT
    call ``.result()`` on the executor's future — the agent tool
    ``plane_sync_project`` does that because the agent expects the
    result, but the auto-sync hooks do not.

    Args:
        project_id: The ensemble project UUID to sync.
        project_repo: Project repository for the sync service.
    """
    if not PlaneSyncService.is_available():
        return

    sync_service = PlaneSyncService(project_repo)

    async def _do_sync() -> None:
        try:
            result = await sync_service.sync_project(project_id)
            logger.info(
                "Plane sync completed for project %s: %s",
                project_id,
                result.get("status"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plane sync failed for project %s: %s", project_id, exc
            )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Caller is inside a running loop — we cannot await from a
            # sync context. Submit to the shared executor (fresh thread,
            # fresh loop, runs asyncio.run internally).
            _plane_sync_executor.submit(asyncio.run, _do_sync())
        else:
            loop.run_until_complete(_do_sync())
    except RuntimeError:
        # No event loop bound at all — same fallback as the running-loop
        # path. Use the shared executor to keep pool usage consistent.
        _plane_sync_executor.submit(asyncio.run, _do_sync())
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Plane sync dispatch error for project %s: %s", project_id, exc
        )


class PlaneSyncService:
    """Orchestrates project-level Plane sync.

    The service is intentionally cheap to construct — it holds no
    connection state and is safe to instantiate per-call (the agent
    tool does so via a factory pattern).
    """

    def __init__(
        self,
        project_repo: SQLModelProjectRepository,
        http_client: PlaneHttpClient | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            project_repo: Project repository used to read/write Ensemble
                projects and their metadata.
            http_client: Optional :class:`PlaneHttpClient`. Defaults to
                :meth:`PlaneHttpClient.create` (returns ``None`` when the
                feature is disabled — handled gracefully in
                :meth:`sync_project`).
        """
        self._repo = project_repo
        # Lazy resolve — keeps imports tight and lets tests inject a
        # mock client without going through the env-var factory.
        self._http_client = http_client

    # ── Feature gating ──────────────────────────────────────────────────

    @classmethod
    def is_available(cls) -> bool:
        """Return True when the Plane integration is configured.

        Delegates to :meth:`PlaneHttpClient.is_available` so callers can
        short-circuit before constructing the service.
        """
        return PlaneHttpClient.is_available()

    def _get_client(self) -> PlaneHttpClient | None:
        """Resolve the HTTP client, defaulting to ``PlaneHttpClient.create``.

        Returns ``None`` when feature gating fails so :meth:`sync_project`
        can short-circuit without raising.
        """
        if self._http_client is not None:
            return self._http_client
        return PlaneHttpClient.create()

    # ── Internal helpers ────────────────────────────────────────────────

    def _set_metadata(self, project_id: str, key: str, value: Any) -> bool:
        """Persist a single metadata record (best-effort).

        Uses ``set_metadata_record`` directly — not ``set_metadata`` — so
        we don't churn ``Project.updated_at`` on every sync attempt
        (CR-6).

        Returns:
            ``True`` on successful write, ``False`` when the write failed
            and the exception was caught. The caller is responsible for
            deciding whether the failure is critical (e.g. the
            ``plane_project_id`` handle — losing this would cause the next
            sync to create a duplicate Plane project) or merely advisory
            (e.g. ``synced_at`` timestamp — losing this only delays
            observability).
        """
        try:
            with Session(self._repo.engine) as session:
                self._repo.set_metadata_record(session, project_id, key, value)
                session.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plane sync: failed to persist metadata %s for project %s: %s",
                key,
                project_id,
                exc,
            )
            return False

    def _mark_error(self, project_id: str) -> None:
        """Record an ``error`` sync state — best-effort, never raises."""
        self._set_metadata(project_id, PLANE_SYNC_STATE_METADATA_KEY, "error")
        self._set_metadata(project_id, PLANE_SYNCED_AT_METADATA_KEY, _now_iso())

    # ── Public API ──────────────────────────────────────────────────────

    async def sync_project(
        self,
        project_id: str,
        force: bool = False,  # noqa: ARG002 — accepted for forward-compat
    ) -> dict[str, Any]:
        """Sync a single Ensemble project to Plane.

        The algorithm:

        1. Read the project (404 if missing → return ``error``).
        2. Read all metadata records in one call (CR-6), filter for the
           three Plane keys.
        3. If ``plane_project_id`` is set → UPDATE path.
        4. Else → CREATE path. To avoid duplicates, list Plane projects
           and search by name. If a match exists, treat as UPDATE; if
           not, create a new project.
        5. On success, persist ``plane_project_id``, ``plane_sync_state="synced"``,
           and ``plane_synced_at`` (ISO8601).
        6. On failure, persist ``plane_sync_state="error"`` and the
           timestamp; log the failure; return a structured result. NEVER
           raises — the caller has no need to wrap in try/except.

        Args:
            project_id: Ensemble project UUID.
            force: Accepted for forward-compat with the cooldown layer;
                does not affect this service's behavior.

        Returns:
            Dict with ``status`` (``"synced"`` | ``"error"`` |
            ``"disabled"``), ``action`` (``"created"`` | ``"updated"``),
            and ``plane_project_id`` when known.
        """
        # Feature gate — short-circuit cleanly when env vars are missing.
        client = self._get_client()
        if client is None:
            return {
                "status": "disabled",
                "message": "Plane sync not configured (PLANE_BASE_URL not set)",
            }

        # 1. Load project.
        project = self._repo.get(project_id)
        if project is None:
            logger.warning(
                "Plane sync: project %s not found — skipping", project_id
            )
            return {
                "status": "error",
                "action": None,
                "message": f"Project {project_id} not found",
            }

        # 2. Read metadata in one call (CR-6), filter client-side.
        try:
            with Session(self._repo.engine) as session:
                metadata_records = self._repo.list_metadata_records(
                    session, project_id
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plane sync: failed to read metadata for %s: %s",
                project_id,
                exc,
            )
            metadata_records = []

        existing_plane_id = _read_metadata_value(
            metadata_records, PLANE_PROJECT_ID_METADATA_KEY
        )

        # 3-5. Drive the CREATE / UPDATE path.
        try:
            if existing_plane_id:
                plane_id, action = await self._update_existing(
                    client, project, existing_plane_id
                )
            else:
                plane_id, action = await self._create_or_adopt(
                    client, project
                )
        except PlaneAuthError as exc:
            logger.warning(
                "Plane sync: auth error syncing project %s: %s",
                project_id,
                exc,
            )
            self._mark_error(project_id)
            return {
                "status": "error",
                "action": None,
                "message": "Plane authentication failed — check PLANE_MCP_API_KEY",
            }
        except PlaneAPIError as exc:
            logger.warning(
                "Plane sync: API error syncing project %s: %s",
                project_id,
                exc,
            )
            self._mark_error(project_id)
            return {
                "status": "error",
                "action": None,
                "message": f"Plane API error: {exc}",
            }
        except Exception as exc:  # noqa: BLE001 — last-resort guard
            logger.warning(
                "Plane sync: unexpected error syncing project %s: %s",
                project_id,
                exc,
            )
            self._mark_error(project_id)
            return {
                "status": "error",
                "action": None,
                "message": f"Unexpected error: {exc}",
            }

        # Persist success metadata. The plane_project_id handle is the
        # critical key — if its write fails, we must NOT report "synced",
        # because the next sync would treat this project as fresh and
        # create a duplicate on Plane. The other two keys are advisory
        # (state for observability, timestamp for the UI) — we log and
        # continue if they fail.
        now = _now_iso()
        id_ok = self._set_metadata(
            project_id, PLANE_PROJECT_ID_METADATA_KEY, plane_id
        )
        if not id_ok:
            self._set_metadata(
                project_id, PLANE_SYNC_STATE_METADATA_KEY, "error"
            )
            self._set_metadata(
                project_id, PLANE_SYNCED_AT_METADATA_KEY, now
            )
            return {
                "status": "error",
                "action": action,
                "plane_project_id": plane_id,
                "message": (
                    "Plane project created but metadata write failed. "
                    "Manual reconciliation needed."
                ),
            }
        if not self._set_metadata(
            project_id, PLANE_SYNC_STATE_METADATA_KEY, "synced"
        ):
            logger.warning(
                "Plane sync: sync_state metadata write failed for %s",
                project_id,
            )
        if not self._set_metadata(
            project_id, PLANE_SYNCED_AT_METADATA_KEY, now
        ):
            logger.warning(
                "Plane sync: synced_at metadata write failed for %s",
                project_id,
            )

        return {
            "status": "synced",
            "action": action,
            "plane_project_id": plane_id,
            "synced_at": now,
        }

    # ── Sync paths ──────────────────────────────────────────────────────

    async def _update_existing(
        self,
        client: PlaneHttpClient,
        project: Project,
        plane_id: str,
    ) -> tuple[str, str]:
        """Update an already-known Plane project.

        Returns ``(plane_id, "updated")``. If the project has been
        deleted out-of-band on Plane (404), falls back to CREATE so the
        mapping recovers.
        """
        # v1: state computed for observability only, not pushed to Plane API.
        # Plane has no stable project-level "state" field — the mapping is
        # purely informational and only surfaces in our log line below.
        # ``network`` and other Plane fields are deliberately omitted from
        # the v1 surface.
        plane_state = _project_state_for_plane(project.status)
        try:
            await client.update_project(
                plane_id,
                name=project.name,
                description=project.description,
            )
            logger.debug(
                "Plane sync: updated project %s -> Plane %s (state=%s)",
                project.project_id,
                plane_id,
                plane_state,
            )
            return plane_id, "updated"
        except PlaneNotFoundError:
            # Project was deleted on Plane — recover by creating anew.
            logger.info(
                "Plane sync: plane project %s missing — recreating",
                plane_id,
            )
            new_plane = await client.create_project(
                name=project.name,
                description=project.description,
            )
            new_id = new_plane.get("id")
            if not new_id:
                raise PlaneAPIError(
                    f"Plane create_project returned no id: {new_plane!r}"
                )
            return str(new_id), "recreated"

    async def _create_or_adopt(
        self,
        client: PlaneHttpClient,
        project: Project,
    ) -> tuple[str, str]:
        """CREATE path with duplicate-by-name avoidance.

        Before creating, lists all Plane projects in the workspace and
        searches for one with a matching ``name``. If found, adopts its
        ID (UPDATE path) — this prevents duplicates when the same
        Ensemble project is re-synced after the metadata record was lost.

        Returns ``(plane_id, "created" | "updated")``.
        """
        try:
            plane_projects = await client.list_projects()
        except PlaneAPIError:
            # If listing fails, fall through to a direct CREATE attempt.
            # The error will surface there if it persists.
            plane_projects = []

        existing_id = _find_plane_id_by_name(plane_projects, project.name)
        if existing_id:
            logger.info(
                "Plane sync: adopting existing Plane project %s for %s",
                existing_id,
                project.name,
            )
            await client.update_project(
                existing_id,
                name=project.name,
                description=project.description,
            )
            return existing_id, "updated"

        created = await client.create_project(
            name=project.name,
            description=project.description,
        )
        new_id = created.get("id")
        if not new_id:
            raise PlaneAPIError(
                f"Plane create_project returned no id: {created!r}"
            )
        logger.debug(
            "Plane sync: created project %s -> Plane %s",
            project.project_id,
            new_id,
        )
        return str(new_id), "created"


def _find_plane_id_by_name(
    plane_projects: list[dict[str, Any]],
    name: str,
) -> str | None:
    """Return the first Plane project ID whose ``name`` matches, case-insensitive.

    Defensive against missing ``id``/``name`` keys — Plane's API is not
    strictly typed and we should not crash on a shape mismatch.
    """
    if not name:
        return None
    target = name.strip().lower()
    for proj in plane_projects:
        proj_name = (proj.get("name") or "").strip().lower()
        if proj_name == target:
            pid = proj.get("id")
            if pid is not None:
                return str(pid)
    return None