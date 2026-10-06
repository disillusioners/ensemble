"""Snapshots read-only HTTP surface — ``GET /api/snapshots*``.

Three endpoints ship in v1 (per ``be-plan.md`` §4):

* ``GET /api/snapshots`` — filterable, paginated list.
* ``GET /api/snapshots/{snapshot_id}`` — single row, ``?include=digest``
  opts into the unbounded JSONB digest (D7 — list payloads stay lean).
* ``GET /api/sapi/snapshots/metrics`` — R16 monitoring counters
  (relocated from ``GET /api/settings/snapshot-usage-metrics``; the
  legacy endpoint stays as a deprecated re-export for one release —
  see :func:`_proxy_to_snapshots_metrics` and the handler it backs in
  ``daemon/routers/settings.py``).

Writes (DELETE / archive / supersede) are explicitly out of scope and
remain tool-only (per be-plan §2 / D9).

**DI pattern.** The router depends-injects the manager via
``request.app.state.manager`` and reads two attributes:

* ``manager._snapshot_repo`` — :class:`SnapshotRepository` for reads.
* ``manager._snapshot_metrics_service`` —
  :class:`SnapshotMetricsService` for the metrics surface (may be
  ``None`` pre-init; we degrade to the empty shape, NOT a 503).

This mirrors the project precedent at
``daemon/routers/blueprints.py:336`` (``manager._blueprint_repo``).

**Sync calls.** All repo calls are synchronous by design (per
``daemon/repositories/snapshot/repository.py`` docstring); the router
bridges to async via ``asyncio.to_thread`` — the project's standard
pattern.

**Import chain.** This module is re-exported by
``daemon/routers/__init__.py`` as ``snapshots_router`` (the convention
at ``__init__.py:3-27``). The single import surface for downstream
consumers is ``from daemon.routers import snapshots_router`` — used by
``daemon/api.py`` and by the test module
``tests/unit/routers/test_snapshots.py``. Do NOT add a direct
``from .snapshots import router`` in ``api.py``; the package re-export
is the single source of truth.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from daemon.repositories.snapshot.models import SNAPSHOT_STATUSES
from .snapshot_schemas import (
    SnapshotListResponse,
    SnapshotResponse,
    SnapshotUsageMetricsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/snapshots", tags=["snapshots"])


# Sort allow-list — pattern-validated at the FastAPI layer (422 on
# anything outside this set; see ``_SORT_PATTERN`` in the ``list`` query
# below). Eight keys, matching ``be-plan.md`` §4.1 (D-3 binding).
_ALLOWED_SORT_KEYS = {
    "created_at_desc",
    "created_at_asc",
    "title_asc",
    "title_desc",
    "status_asc",
    "status_desc",
    "project_id_asc",
    "project_id_desc",
}
_SORT_PATTERN = "^(" + "|".join(sorted(_ALLOWED_SORT_KEYS)) + ")$"


# ── Helpers ───────────────────────────────────────────────────────────────


def _validate_uuid(value: str, *, field: str) -> None:
    """Raise 400 if ``value`` is not a valid UUID.

    Mirrors ``_validate_project_id`` at
    ``daemon/routers/blueprints.py:137-155``.
    """
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be a valid UUID",
        )


def _to_http_500(exc: Exception, endpoint: str) -> HTTPException:
    """Surface a repo / DB failure as a structured 500.

    Mirrors the ``_to_http_500`` idiom at ``daemon/routers/skills.py:587``.
    """
    logger.exception("[SnapshotsRouter] %s failed: %s", endpoint, exc)
    return HTTPException(
        status_code=500,
        detail={"error": str(exc), "endpoint": endpoint},
    )


def _to_list_item(s: Any) -> dict[str, Any]:
    """to_dict() minus digest (D7) and minus task_summary (A-2).

    List payloads stay lean — the digest is unbounded JSONB
    (``daemon/repositories/snapshot/models.py:224-227``), and
    ``task_summary`` is the BM25 corpus text the page doesn't render.
    The detail endpoint still returns both (full ``to_dict()``; digest
    opt-in via ``?include=digest``).
    """
    d = s.to_dict() if hasattr(s, "to_dict") else s
    d.pop("digest", None)
    d.pop("task_summary", None)  # A-2 (sequencing §1.3, binding)
    return d


# ── List endpoint ─────────────────────────────────────────────────────────


@router.get("", response_model=SnapshotListResponse)
async def list_snapshots(
    request: Request,
    project_id: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    tags: list[str] = Query(default_factory=list),
    tag_mode: str = Query(default="all", pattern="^(all|any)$"),
    status: list[str] = Query(default_factory=list),  # D5: default = all 5
    created_after: str | None = Query(default=None),
    created_before: str | None = Query(default=None),
    sort: str = Query(default="created_at_desc", pattern=_SORT_PATTERN),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SnapshotListResponse:
    """List snapshots, filterable. See ``be-plan.md`` §4.1.

    **Envelope (D-1 binding):** ``{items, total}`` — the ``items`` key
    supersedes the pre-canon design-spec text mentioning ``snapshots``
    (per ``sequencing.md`` §1 D-1).

    **List-item shape:** ``to_dict()`` minus ``digest`` (D7) and
    minus ``task_summary`` (A-2); the detail endpoint still carries
    both.

    **Validation contract:**

    * ``project_id`` — UUID-shaped; 400 otherwise.
    * ``status`` — every value must be in ``SNAPSHOT_STATUSES``; 422
      otherwise. Empty list ⇒ all 5 statuses (D5).
    * ``created_after`` / ``created_before`` — ``datetime.fromisoformat``;
      400 on parse failure.
    * ``sort`` — 8-key allow-list; 422 otherwise (FastAPI's
      ``Query(pattern=...)`` rejects before the handler runs).
    * ``limit`` / ``offset`` — ``Query(ge=1, le=200)`` /
      ``Query(ge=0)``; FastAPI rejects out-of-range with 422 (case
      21 pins this — the runtime clamp is a defense-in-depth belt
      for direct repo callers, NOT a 200-success path).
    """
    # UUID validation on the project filter (400, not 422).
    if project_id is not None:
        _validate_uuid(project_id, field="project_id")

    # Status: default = all 5; reject unknown values with 422.
    if not status:
        status = list(SNAPSHOT_STATUSES)
    else:
        unknown = [s for s in status if s not in SNAPSHOT_STATUSES]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": f"unknown status values: {unknown}",
                    "allowed": sorted(SNAPSHOT_STATUSES),
                },
            )

    # ISO-8601 window (400 on parse failure, mirroring the 404 body
    # shape at ``daemon/routers/skills.py:799-810``).
    for label, value in (
        ("created_after", created_after),
        ("created_before", created_before),
    ):
        if value is not None:
            try:
                datetime.fromisoformat(value)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"error": f"{label} must be ISO-8601 ({exc})"},
                )

    # Double-clamp idiom (``daemon/routers/skills.py:1235-1240``) —
    # defense-in-depth belt for direct repo callers. The HTTP path is
    # already gated by ``Query(ge=…, le=…)`` (422), so this never
    # trims an HTTP-served value; it only protects repo-side calls.
    effective_limit = min(int(limit), 200)
    effective_offset = max(int(offset), 0)

    manager = request.app.state.manager
    repo = manager._snapshot_repo
    try:
        items, total = await asyncio.to_thread(
            repo.list_with_filters,
            project_id=project_id,
            agent_id=agent,
            statuses=status,
            created_after=created_after,
            created_before=created_before,
            tags=tags,
            tag_mode=tag_mode,
            sort=sort,
            limit=effective_limit,
            offset=effective_offset,
        )
    except ValueError as exc:
        # ``filter_by_tags`` raises ValueError on unknown ``tag_mode``
        # belt — the router's pattern check fires first on the HTTP
        # path, but the repo's belt is non-negotiable.
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    except Exception as exc:
        raise _to_http_500(exc, "list_snapshots")

    return SnapshotListResponse(
        items=[_to_list_item(s) for s in items],
        total=int(total),
    )


# ── Metrics endpoint (relocated from /api/settings/snapshot-usage-metrics) ──


async def _proxy_to_snapshots_metrics(
    request: Request,
) -> SnapshotUsageMetricsResponse:
    """Deprecated-path proxy — delegates to :func:`get_snapshot_metrics`.

    Imported by ``daemon/routers/settings.py`` for the legacy
    ``GET /api/settings/snapshot-usage-metrics`` endpoint (LEADER
    RULING, sequencing pass 4 amendment blocker #6 — the helper lives
    HERE in the snapshots router, not in ``settings.py``, to keep the
    coupling direction one-way: ``settings.py`` → ``snapshots.py``).

    Returns the same :class:`SnapshotUsageMetricsResponse` shape the
    new canonical endpoint returns.
    """
    return await get_snapshot_metrics(request)


@router.get("/metrics", response_model=SnapshotUsageMetricsResponse)
async def get_snapshot_metrics(
    request: Request,
) -> SnapshotUsageMetricsResponse:
    """R16 monitoring counters — see ``be-plan.md`` §4.3.

    The new canonical home for the metrics surface. Replaces
    ``GET /api/settings/snapshot-usage-metrics``; the old endpoint is
    kept as a deprecated re-export (manual ``Deprecation`` /
    ``Sunset`` / ``Link`` response headers; see
    ``daemon/routers/settings.py:get_snapshot_usage_metrics_deprecated``).

    **Failure semantics:**

    * ``manager._snapshot_metrics_service is None`` (pre-init) ⇒
      return the empty shape (``capture_counts={}``,
      ``spawn_counts_per_snapshot=[]``), NOT 503.
    * ``service.surface()`` raises ⇒ log + return the empty shape
      (fail-soft; degraded metrics are observable, not blocking).
    """
    manager = request.app.state.manager
    service = getattr(manager, "_snapshot_metrics_service", None)
    if service is None:
        return SnapshotUsageMetricsResponse(
            capture_counts={},
            spawn_counts_per_snapshot=[],
        )
    try:
        data = await service.surface()
    except Exception as exc:  # fail-soft: log + return empty shape
        logger.warning("[SnapshotsRouter] metrics degraded: %s", exc)
        return SnapshotUsageMetricsResponse(
            capture_counts={},
            spawn_counts_per_snapshot=[],
        )
    return SnapshotUsageMetricsResponse(
        capture_counts=data.get("capture_counts") or {},
        spawn_counts_per_snapshot=data.get("spawn_counts_per_snapshot") or [],
    )


# ── Detail endpoint ───────────────────────────────────────────────────────


@router.get("/{snapshot_id}", response_model=SnapshotResponse)
async def get_snapshot(
    request: Request,
    snapshot_id: str,
    include: str = Query(default=""),
) -> SnapshotResponse:
    """Single snapshot by id — see ``be-plan.md`` §4.2.

    ``?include=digest`` opts in to the unbounded JSONB digest
    (default: ``digest={}``, present-but-empty so the FE can branch
    on presence without ``undefined``-guards).
    """
    if not snapshot_id or not snapshot_id.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": "snapshot_id is required"},
        )
    _validate_uuid(snapshot_id, field="snapshot_id")

    manager = request.app.state.manager
    repo = manager._snapshot_repo
    try:
        row = await asyncio.to_thread(repo.get, snapshot_id)
    except Exception as exc:
        raise _to_http_500(exc, "get_snapshot")
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Snapshot not found", "snapshot_id": snapshot_id},
        )

    d = row.to_dict() if hasattr(row, "to_dict") else row
    # Default: digest present-but-empty (D7). Only opt in when the
    # ``include`` query-param lists ``digest`` (comma-separated for
    # forward-compatibility with future include tokens).
    include_tokens = {
        t.strip() for t in (include or "").split(",") if t.strip()
    }
    if "digest" not in include_tokens:
        d["digest"] = {}

    return SnapshotResponse(**d)
