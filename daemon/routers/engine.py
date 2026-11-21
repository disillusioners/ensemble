"""Engine observability router (Engine Arc Phase 6 — T6.4/T6.6).

A NEW read-only surface under ``/api/engine`` — three GETs, NO mutating
verb (D6.8; the method set is frozen at ``{GET}`` and the pack asserts
it: adding a POST REDs).

Routes
------
* ``GET /api/engine/jobs`` — the paged, filtered job inventory
  (``category`` / ``service_tag`` / ``enabled`` filters; ``limit``
  default 100, hard cap 500; opaque ``next_cursor``).
* ``GET /api/engine/jobs/{job_id}`` — one job's detail: the inventory
  row + the resolved kill-switch state (D6.7) + a BOUNDED ``engine_runs``
  tail (≤ 20, D6.10) + the ``estate`` convergence block.
* ``GET /api/engine/events`` — the P5 kind catalog with per-kind firing
  and consumer facts, composed through the registry's ``snapshot()``.

Auth posture (D6.3, mirroring the metrics query door EXACTLY)
-------------------------------------------------------------
* SCOPE — all three routes carry the any-of set ``maintenance:read`` OR
  ``metrics:read`` via ``dependencies=[scope_required(...)]`` (the same
  pair the metrics query door uses; no new taxonomy).
* AUTH-ON — no class tag ⇒ protected by default; the middleware 401s an
  unauthenticated caller and the scope check 403s
  (``SCOPE_INSUFFICIENT``).
* AUTH-OFF — the inline :func:`_require_auth_on` gate returns
  ``503 AUTH_NOT_CONFIGURED`` BEFORE any read (byte-identical detail to
  the metrics door's branch — the pack asserts the comparability).

DI seam (why this file imports nothing from the engine runtime)
---------------------------------------------------------------
The route handlers read an INJECTED observability adapter stored on
``app.state.engine_observability`` (assembled by the boot wiring — a
later wiring step; until then a test mounts the router with its own
adapter). The adapter exposes: ``repository`` (the P2 row contract),
``engine_service`` / ``maintenance_service`` (for the convergence
mapping), ``switch_resolver`` (P3's pure resolver), ``event_snapshot``
(the P5 registry snapshot callable) and an optional ``runs_reader``
(a bounded ``engine_runs`` tail reader). NONE of those is imported
here; the router imports the projection module only.

Fences: F-15 (mounted INSIDE ``api_router`` before the SPA catch-all —
the wiring line is P4-coordinated), F-2 (``job_metrics()`` untouched),
F-1 (zero metrics registered). No frontend file; no mutating verb.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import orjson
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from daemon.auth.decorators import scope_required
from daemon.services.engine import projection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/engine", tags=["engine"])

#: The any-of scope pair (D6.3 — the metrics query door's set, reused).
_SCOPE_READ = "maintenance:read"
_SCOPE_READ_METRICS = "metrics:read"

_ERR_AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"
_ERR_OBSERVABILITY_UNAVAILABLE = "engine_observability_unavailable"
_ERR_INVALID_CURSOR = "invalid_cursor"
_ERR_JOB_NOT_FOUND = "engine_job_not_found"
_ERR_INVALID_FILTER = "invalid_filter"


def _err(status_code: int, error: str, message: str) -> HTTPException:
    """A plain-dict detail (byte-stable across OpenAPI versions)."""
    return HTTPException(
        status_code=status_code, detail={"error": error, "message": message}
    )


def _require_auth_on(request: Request) -> None:
    """The D3 fail-closed posture — auth_config absent ⇒ 503.

    Byte-identical to ``daemon/routers/metrics.py``'s branch (the pack
    asserts the comparability): the same error code and message.
    """
    cfg = getattr(getattr(request.app, "state", None), "auth_config", None)
    if cfg is None:
        raise _err(
            503,
            _ERR_AUTH_NOT_CONFIGURED,
            "Auth not configured (fail-closed)",
        )


def _adapter(request: Request) -> Any:
    """The injected observability adapter (its ABSENCE is a typed 503)."""
    adapter = getattr(
        getattr(request.app, "state", None), "engine_observability", None
    )
    if adapter is None:
        raise _err(
            503,
            _ERR_OBSERVABILITY_UNAVAILABLE,
            "The engine observability adapter is not available",
        )
    return adapter


def _default_runs_reader(repository: Any, job_id: str, limit: int) -> list[Any]:
    """A BOUNDED ``engine_runs`` tail (newest first) — D6.10.

    Reads through the repository's OWN engine handle (the P2 row
    contract) and never scans more than ``limit`` rows. Injected /
    overridable on the adapter so tests need no DB.
    """
    from sqlmodel import Session, select

    from daemon.repositories.engine.models import EngineRun

    engine = getattr(repository, "engine", None)
    if engine is None:
        return []
    with Session(engine) as session:
        stmt = (
            select(EngineRun)
            .where(EngineRun.job_id == job_id)
            .order_by(EngineRun.id.desc())
            .limit(int(limit))
        )
        return list(session.exec(stmt).all())


def _build(
    adapter: Any, build_fn: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """Run the page build ON THE OFF-LOOP WORKER (D6.9).

    The caller schedules this via ``db_offload.offload``; keeping the
    build in a plain function makes the thread-identity assertion
    trivial (the pack records the builder's thread vs the loop's).
    """
    return build_fn()


def _render(payload: dict[str, Any]) -> bytes:
    """Encode the wire body (off-loop, alongside the build)."""
    return orjson.dumps(payload)


async def _off_loop(fn: Callable[..., bytes], *args: Any, **kwargs: Any) -> bytes:
    from daemon.services.db_offload import offload as _offload

    return await _offload(fn, *args, **kwargs)


def _snapshot_of(adapter: Any) -> dict[str, Any]:
    engine_service = getattr(adapter, "engine_service", None)
    if engine_service is None:
        return {}
    metrics = getattr(engine_service, "metrics", None)
    if metrics is None or not hasattr(metrics, "snapshot"):
        return {}
    try:
        return metrics.snapshot() or {}
    except Exception:  # noqa: BLE001 — a diagnostics read never 500s
        logger.warning("engine observability: snapshot() failed", exc_info=True)
        return {}


def _merged_of(adapter: Any) -> tuple[dict[str, Any], set[str]]:
    """The convergence mapping + the maintenance-tier job-id set (T6.2).

    ``project_merged`` is reached through the PROJECTION module (the
    single sanctioned engine-package import for this router: dispatch
    import discipline) — never a second direct import.
    """
    maintenance = getattr(adapter, "maintenance_service", None)
    engine_service = getattr(adapter, "engine_service", None)
    merged: dict[str, Any] = {}
    maint_ids: set[str] = set()
    if maintenance is not None:
        try:
            merged = projection.project_merged(maintenance, engine_service) or {}
            # The F-2 fields' SOURCE is "maintenance" exactly for the
            # jobs the maintenance tier publishes.
            job_metrics = getattr(maintenance, "job_metrics", None)
            if callable(job_metrics):
                maint_ids = set((job_metrics() or {}).keys())
        except Exception:  # noqa: BLE001
            logger.warning(
                "engine observability: convergence mapping failed",
                exc_info=True,
            )
    return merged, maint_ids


# ── the three GETs ──────────────────────────────────────────────────────


@router.get(
    "/jobs",
    dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)],
    responses={
        200: {"description": "The paged, filtered engine job inventory."},
        400: {"description": "An invalid cursor or filter parameter."},
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {"description": "The caller lacks maintenance:read OR metrics:read."},
        503: {"description": "Auth OFF (fail-closed) or the adapter is unwired."},
    },
)
async def list_engine_jobs(request: Request) -> Response:
    """``GET /api/engine/jobs`` — the paged, filtered inventory (D6.2/D6.9)."""
    _require_auth_on(request)
    adapter = _adapter(request)

    qp = request.query_params
    category = qp.get("category") or None
    service_tag = qp.get("service_tag") or None
    enabled_raw = qp.get("enabled")
    enabled: bool | None = None
    if enabled_raw is not None and str(enabled_raw).strip():
        lowered = str(enabled_raw).strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            enabled = True
        elif lowered in ("0", "false", "no", "off"):
            enabled = False
        else:
            raise _err(
                400,
                _ERR_INVALID_FILTER,
                "enabled must be a boolean (true/false)",
            )
    limit = projection.clamp_limit(qp.get("limit"))
    cursor = qp.get("cursor") or None

    repository = getattr(adapter, "repository", None)
    if repository is None:
        raise _err(
            503,
            _ERR_OBSERVABILITY_UNAVAILABLE,
            "The engine repository is not wired",
        )

    def _build_page() -> bytes:
        rows = repository.list_jobs(category=category, service_tag=service_tag)
        snapshot = _snapshot_of(adapter)
        merged, maint_ids = _merged_of(adapter)
        payload = projection.list_view(
            rows,
            snapshot=snapshot,
            merged=merged,
            maintenance_job_ids=maint_ids,
            category=category,
            service_tag=service_tag,
            enabled=enabled,
            limit=limit,
            cursor=cursor,
        )
        return _render(payload)

    try:
        body = await _off_loop(_build_page)
    except projection.InvalidCursor as exc:
        raise _err(400, _ERR_INVALID_CURSOR, str(exc)) from exc
    return Response(content=body, media_type="application/json")


@router.get(
    "/jobs/{job_id}",
    dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)],
    responses={
        200: {"description": "One job's detail + resolved switches + runs tail."},
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {"description": "The caller lacks maintenance:read OR metrics:read."},
        404: {"description": "The job id is not registered."},
        503: {"description": "Auth OFF (fail-closed) or the adapter is unwired."},
    },
)
async def get_engine_job(request: Request, job_id: str) -> Response:
    """``GET /api/engine/jobs/{job_id}`` — detail (D6.7 + D6.10)."""
    _require_auth_on(request)
    adapter = _adapter(request)
    repository = getattr(adapter, "repository", None)
    if repository is None:
        raise _err(
            503,
            _ERR_OBSERVABILITY_UNAVAILABLE,
            "The engine repository is not wired",
        )

    def _build_detail() -> bytes:
        row = repository.get_job(job_id)
        if row is None:
            raise _err(404, _ERR_JOB_NOT_FOUND, "No such engine job")
        snapshot = _snapshot_of(adapter)
        merged, maint_ids = _merged_of(adapter)
        runs_reader = getattr(adapter, "runs_reader", None)
        runs = (
            runs_reader(repository, job_id, projection.RUNS_TAIL_LIMIT)
            if callable(runs_reader)
            else _default_runs_reader(
                repository, job_id, projection.RUNS_TAIL_LIMIT
            )
        )
        payload = projection.detail_view(
            row,
            snapshot=snapshot,
            merged=merged,
            maintenance_job_ids=maint_ids,
            runs=runs,
            resolver=getattr(adapter, "switch_resolver", None),
        )
        return _render(payload)

    body = await _off_loop(_build_detail)
    return Response(content=body, media_type="application/json")


@router.get(
    "/events",
    dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)],
    responses={
        200: {"description": "The event-kind catalog with firing/consumer facts."},
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {"description": "The caller lacks maintenance:read OR metrics:read."},
        503: {"description": "Auth OFF (fail-closed) or the adapter is unwired."},
    },
)
async def list_engine_events(request: Request) -> Response:
    """``GET /api/engine/events`` — the P5 catalog + per-kind facts (T6.1)."""
    _require_auth_on(request)
    adapter = _adapter(request)
    snapshot_fn = getattr(adapter, "event_snapshot", None)

    def _build_events() -> bytes:
        payload = projection.event_view(
            bus=getattr(request.app.state, "event_bus", None),
            snapshot_fn=snapshot_fn,
        )
        return _render(payload)

    body = await _off_loop(_build_events)
    return Response(content=body, media_type="application/json")


__all__ = ["router"]
