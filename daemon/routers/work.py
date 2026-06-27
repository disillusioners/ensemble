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
from datetime import datetime, timezone
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


def _serialize_created_at(value: datetime | None) -> str | None:
    """Return an ISO-8601 string for ``value``, or ``None``.

    Task rows give us a tz-aware or naive ``datetime``; JobItem rows
    give us a tz-aware parsed string already. JSON serialisation
    needs a string. Naive datetimes are coerced to UTC before
    formatting so the output always carries the ``+00:00`` offset
    — frontend code can rely on tz-awareness without parsing the
    string for missing offset edge cases.

    Args:
        value: A ``datetime`` (tz-aware or naive) or ``None``.

    Returns:
        An ISO-8601 string, or ``None`` if ``value`` is ``None``.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _work_record_to_dict(record: WorkRecord) -> dict[str, Any]:
    """Serialize a :class:`WorkRecord` to a JSON-safe dict.

    Field shape matches the contract the virtual job UI surface
    expects (see frontend ``work.model.ts``). None values are kept
    as JSON ``null`` — the frontend checks explicit nullability.

    The ``kind`` field is one of ``"job"`` / ``"turn"`` /
    ``"report"`` after Phase 4 — see ``WorkResolverService`` for
    how this is derived from ``Task.task_type`` vs. JobItem.

    Args:
        record: A WorkRecord produced by
            ``WorkResolverService.list_work`` or
            ``WorkResolverService.resolve_work``.

    Returns:
        A dict matching the GET /api/work response item shape.
    """
    return {
        "work_id": record.work_id,
        "kind": record.kind,
        "status": record.status,
        "instance_id": record.instance_id,
        "project_id": record.project_id,
        "agent_id": record.agent_id,
        "result_summary": record.result_summary,
        "error": record.error,
        "created_at": _serialize_created_at(record.created_at),
    }


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
    )
    return [_work_record_to_dict(r) for r in records]


__all__ = ["router", "set_work_resolver", "get_work_resolver"]