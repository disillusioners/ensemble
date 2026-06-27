"""Virtual Job Management Surface — unified work list endpoint.

Phase 4 (2026-06-27) of ``feature/virtual-job-management-surface``.
Provides ``GET /api/work`` — a single read API that lists both
worker-pool tasks and job-queue items through the
:class:`WorkResolverService` view-model, with a ``kind`` filter
distinguishing message turns from completion reports so the
frontend can render queue badges only on jobs.

Architecture mirrors ``daemon/routers/queues.py``:

* module-level ``_work_resolver`` global
* ``set_work_resolver(...)`` setter called from ``daemon/api.py``
  during application startup
* ``get_work_resolver()`` FastAPI Depends factory that raises 503
  if the service has not been wired in

The endpoint is intentionally thin — it does no filtering or
shaping of its own beyond JSON-serializing the
:class:`WorkRecord` view-model and validating the ``kind`` query
parameter against the allowed vocabulary.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from daemon.services.work_resolver import WorkRecord

if TYPE_CHECKING:
    from daemon.services.work_resolver import WorkResolverService

logger = logging.getLogger(__name__)

# Create router. The path prefix is empty because the router is
# mounted under ``/api`` in ``daemon/api.py`` — the public URL is
# ``GET /api/work``.
router = APIRouter(prefix="", tags=["work"])


# ── DI: WorkResolverService ────────────────────────────────────────────────
# Module-level singleton + setter + Depends factory. Matches the
# pattern in ``daemon/routers/queues.py`` (lines 26-65) so the
# startup wiring in ``daemon/api.py`` is uniform across routers.
# 503 (not 500) is the correct status for "service not initialised"
# because the caller did everything right; the server just isn't
# ready to serve that route yet — same convention as queues.py.

_work_resolver: "WorkResolverService | None" = None


def get_work_resolver() -> "WorkResolverService":
    """Return the wired-in :class:`WorkResolverService`, or 503.

    Returns:
        The WorkResolverService instance.

    Raises:
        HTTPException: 503 if the service has not been initialized
            via :func:`set_work_resolver` during app startup.
    """
    if _work_resolver is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Work resolver service not initialized"},
        )
    return _work_resolver


def set_work_resolver(resolver: "WorkResolverService") -> None:
    """Set the :class:`WorkResolverService` instance.

    Called from ``daemon/api.py`` lifespan startup after the resolver
    is constructed (see ``api.py:260-266``). Idempotent — calling
    multiple times replaces the previously-set singleton.

    Args:
        resolver: The WorkResolverService singleton.
    """
    global _work_resolver
    _work_resolver = resolver


# ── Serialization ──────────────────────────────────────────────────────────
# `WorkRecord.to_dict()` (defined in `daemon.services.work_resolver`) is the
# canonical serializer for the virtual job surface. The router calls it
# directly — the previous local `_work_record_to_dict` and
# `_serialize_created_at` helpers were removed so MCP tools
# (`daemon.tools.job_queue`) and the HTTP route emit byte-identical
# shapes for the same WorkRecord.


# ── Routes ────────────────────────────────────────────────────────────────


# Accepted ``kind`` values for the GET /work endpoint. The
# resolver itself accepts all four (``"job"`` / ``"turn"`` /
# ``"report"`` / ``"task"``) but the router rejects unknown values
# with 400 so a typo doesn't silently return an empty list.
_KIND_VALUES: frozenset[str] = frozenset({"job", "turn", "report", "task"})


@router.get("/work", response_model=list[dict[str, Any]])
async def list_work(
    status: str | None = Query(
        default=None,
        description="Canonical status filter",
    ),
    project_id: str | None = Query(
        default=None,
        description="Project ID filter",
    ),
    instance_id: str | None = Query(
        default=None,
        description="Instance ID filter",
    ),
    kind: str | None = Query(
        default=None,
        description="Work kind: job, turn, report, or task",
    ),
    root_only: bool = Query(
        default=True,
        description=(
            "When true (default), exclude work whose backing instance "
            "has a non-null parent_id — i.e. child-instance turns. "
            "Set false to return the full root + child union."
        ),
    ),
    resolver: "WorkResolverService" = Depends(get_work_resolver),
) -> list[dict[str, Any]]:
    """List work records across both worker-pool and job-queue tables.

    The endpoint is a thin wrapper over
    :meth:`WorkResolverService.list_work`. All query parameters are
    optional and combine with AND semantics. The result is ordered
    newest-first by ``created_at`` (the resolver enforces this
    server-side).

    Args:
        status: Optional canonical status filter (``pending``,
            ``processing``, ``paused``, ``completed``, ``failed``,
            ``cancelled``, ``dead_letter``).
        project_id: Optional project ID filter.
        instance_id: Optional instance ID filter.
        kind: Optional kind filter (``job``, ``turn``, ``report``,
            or ``task`` for the backward-compatible turn+report
            union).
        root_only: When ``True`` (default), drop child-instance
            work so the management view stays scoped to the roots
            the jober bound jobs to. ``False`` returns the full
            union (debug escape hatch). See
            ``WorkResolverService.list_work`` for the rationale
            (P-A, ``docs/plans/virtual-job-tool-completeness.md``).
        resolver: Injected WorkResolverService (via Depends).

    Returns:
        A JSON array of work record dicts ordered newest first
        (created_at DESC). Empty list if nothing matches.

    Raises:
        HTTPException: 400 if ``kind`` is not one of the accepted
            values; 503 if the WorkResolverService was not
            initialized.
    """
    if kind is not None and kind not in _KIND_VALUES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"Invalid kind value: {kind!r}",
                "accepted": sorted(_KIND_VALUES),
            },
        )
    records = resolver.list_work(
        project_id=project_id,
        instance_id=instance_id,
        status=status,
        kind=kind,
        root_only=root_only,
    )
    return [r.to_dict() for r in records]


__all__ = ["router", "set_work_resolver", "get_work_resolver"]
