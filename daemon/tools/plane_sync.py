"""Plane sync tool category — leader-facing agent tool.

Exposes a single ``plane_sync_project`` tool that drives
:class:`daemon.services.plane_sync_service.PlaneSyncService` to mirror an
Ensemble project to Plane. The tool is registered in the ``"project"``
category so any agent with ``tools.allow: ["project"]`` (notably the
leader agent) can invoke it.

CR-4: Per-project cooldown
--------------------------
A module-level ``_last_sync`` dict tracks the monotonic timestamp of the
most recent sync per ``project_id``. Calls within ``PLANE_SYNC_COOLDOWN_S``
(30s by default) for the same project are short-circuited with a
``"rate_limited"`` result unless ``force=True``. This protects Plane
from a tight LLM tool-call loop accidentally re-syncing every turn.

Loop contract
-------------
The tool itself is synchronous (LangChain ``@tool`` is sync-by-default
for our factory pattern). The underlying service is async; we drive it
with the same ``asyncio.get_event_loop()`` /
``ThreadPoolExecutor + asyncio.run`` pattern used by
``daemon/tools/project.py`` for queue provisioning.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ..constants import PLANE_SYNC_COOLDOWN_S
from ..services.plane_sync_service import PlaneSyncService
from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from ..repositories.project.repository import SQLModelProjectRepository

logger = logging.getLogger(__name__)


# ── Cooldown tracking (CR-4) ────────────────────────────────────────────────
# Keyed by project_id. Value is ``time.monotonic()`` of the most recent
# sync invocation (regardless of success). ``force=True`` bypasses.
#
# W4: guarded by ``_last_sync_lock`` so concurrent calls from different
# agent threads don't race on the read-modify-write of the timestamp.
# The lock is intentionally narrow: it covers only the dict's critical
# sections (cooldown check + cooldown write), not the I/O work.
_last_sync: dict[str, float] = {}
_last_sync_lock = threading.Lock()


CATEGORY_NAME = "Project Management"
CATEGORY_DOC = """\
Sync Ensemble projects to Plane (plane.so) for cross-tool visibility.

**Tool**: `plane_sync_project` — mirror an Ensemble project to Plane as a
flat project. Auto-runs on project creation; manually callable when you
need to re-sync after a Plane outage or to pick up changes that the v1
auto-sync layer doesn't cover (name, description).

Requires `PLANE_BASE_URL`, `PLANE_MCP_API_KEY`, `PLANE_MCP_WORKSPACE_SLUG`
env vars. When unset, the tool returns ``{"status": "disabled"}``.
"""


# Full documentation for the tool (referenced by tool_help).
_FULL_DOC_PLANE_SYNC_PROJECT = """\
Sync an Ensemble project to Plane.

Mirrors the project's name and description to a Plane project. Core
project info (name, description) is synced to Plane. Status mapping
is computed for logging but not pushed to Plane in v1.

The tool is auto-invoked on project creation. Call it manually to:
- Recover after a Plane outage (records ``error`` state until the next sync).
- Re-sync after editing name or description on either side. (Status is
  logged only at v1 — see Notes.)
- Adopt an existing Plane project (matched by name) after a metadata loss.

Args:
    project_id: The Ensemble project UUID to sync.
    force: Bypass the 30s per-project cooldown (default: False).

Returns:
    Dictionary with:
      - ``status``: ``"synced"`` | ``"error"`` | ``"disabled"`` |
        ``"rate_limited"``
      - ``action``: ``"created"`` | ``"updated"`` | ``"recreated"`` |
        ``None`` (on error/disabled/rate_limited)
      - ``plane_project_id``: Plane's UUID for the project (when synced)
      - ``synced_at``: ISO8601 timestamp of the sync
      - ``message``: Human-readable detail (errors, disabled message,
        rate-limit notice)

Notes:
    - v1 limitation: name changes on the Ensemble side are
      NOT auto-synced. Call this tool explicitly to push them.
      (Status changes are not pushed at all in v1 — they are only
      computed for observability in the sync log.)
    - Sync is best-effort and never raises. Errors are recorded in the
      project's ``plane_sync_state`` metadata.
"""


def _check_cooldown(project_id: str, force: bool) -> dict | None:
    """Return a rate-limit result dict if the cooldown is active, else None.

    Separated from the main tool function so the cooldown contract is
    testable in isolation. Reads the module-level ``_last_sync`` under
    ``_last_sync_lock`` (W4) so concurrent threads observe a consistent
    timestamp.
    """
    if force:
        return None
    with _last_sync_lock:
        now = time.monotonic()
        last = _last_sync.get(project_id)
        if last is None:
            return None
        elapsed = now - last
        if elapsed < PLANE_SYNC_COOLDOWN_S:
            return {
                "status": "rate_limited",
                "message": (
                    "Sync already triggered recently. "
                    "Use force=True to override."
                ),
                "last_sync_seconds_ago": round(elapsed, 2),
                "cooldown_seconds": PLANE_SYNC_COOLDOWN_S,
            }
        return None


def _drive_async(coro) -> dict:
    """Run an awaitable from a sync context, returning its result.

    Mirrors the queue-provisioning pattern (asyncio.get_event_loop /
    ThreadPoolExecutor + asyncio.run). Two cases:

    1. No event loop bound → ``loop.run_until_complete`` directly.
    2. Event loop is running → cannot ``run_until_complete`` inside
       a running loop (would raise ``RuntimeError: This event loop is
       already running``) and cannot ``ensure_future`` + ``wait`` (the
       earlier C2 bug — this branch was always dead code because
       ``run_until_complete`` is illegal from a running loop). Instead
       we go straight to the thread-pool fallback.

    On the thread-pool path, the shared ``_plane_sync_executor`` from
    :mod:`daemon.services.plane_sync_service` is used so we don't
    create a new executor per call (W3). The tool is synchronous —
    it calls ``future.result()`` to block until the coroutine returns
    so the agent sees the sync result.

    Returns the awaited result, or a synthesized error dict on exception.
    Never raises.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # C2: the old code did ``ensure_future(coro)`` + ``run_until_complete(future)``
            # here, which ALWAYS raised ``RuntimeError`` and leaked the
            # orphan Task. Going straight to the thread fallback is the
            # correct behavior — a running loop cannot host our coroutine
            # directly from this sync context.
            raise RuntimeError(
                "Event loop already running — use thread fallback"
            )
        return loop.run_until_complete(coro)
    except RuntimeError:
        # No event loop bound, or the running-loop case above — submit
        # to the shared executor (W3) so we don't allocate a new
        # ThreadPoolExecutor per call.
        from ..services.plane_sync_service import _plane_sync_executor

        future = _plane_sync_executor.submit(asyncio.run, coro)
        return future.result()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Plane sync tool: unhandled error: %s", exc)
        return {
            "status": "error",
            "action": None,
            "message": f"Unhandled error: {exc}",
        }


def create_plane_sync_tools(
    store: "SQLModelProjectRepository",
) -> list:
    """Create the ``plane_sync_project`` tool factory.

    Registered in the ``"project"`` category so the leader agent (which
    has ``tools.allow: ["project"]``) can invoke it.

    Args:
        store: SQLModelProjectRepository used by the sync service.

    Returns:
        List containing the ``plane_sync_project`` tool.
    """
    @register_tool_category("project")
    @tool
    def plane_sync_project(project_id: str, force: bool = False) -> dict:
        """Sync an Ensemble project to Plane. Use tool_help("plane_sync_project") for details."""
        # CR-4: cooldown gate.
        rate_limited = _check_cooldown(project_id, force)
        if rate_limited is not None:
            return rate_limited

        # Mark the attempt BEFORE we do the work, so even a failing sync
        # resets the cooldown (the cost was paid). Write under the lock
        # (W4) so concurrent calls from different threads see a
        # consistent timestamp.
        with _last_sync_lock:
            _last_sync[project_id] = time.monotonic()

        if not PlaneSyncService.is_available():
            return {
                "status": "disabled",
                "message": "Plane sync not configured (PLANE_BASE_URL not set)",
            }

        service = PlaneSyncService(store)
        try:
            return _drive_async(service.sync_project(project_id, force=force))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Plane sync tool: failed to drive service for %s: %s",
                project_id,
                exc,
            )
            return {
                "status": "error",
                "action": None,
                "message": f"Failed to invoke sync service: {exc}",
            }

    plane_sync_project._full_doc_ = _FULL_DOC_PLANE_SYNC_PROJECT

    return [plane_sync_project]