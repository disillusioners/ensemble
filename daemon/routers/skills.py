"""REST API router for skill management — Phase 6 of the Skill Evolution System.

Phase 6 of the Skill Evolution System. Mounted under
``/api/skills`` (see :mod:`daemon.routers.__init__`) and exposes the
four Phase 2 services plus the trigger repository over HTTP:

* :class:`~daemon.services.skill_store_service.SkillStoreService` —
  skill CRUD + lineage bundle (``view_skill``).
* :class:`~daemon.services.skill_search_service.SkillSearchService` —
  BM25 + embedding re-rank + LLM selection.
* :class:`~daemon.services.skill_metrics_service.SkillMetricsService` —
  feedback recording + per-skill stats.
* :class:`~daemon.services.skill_evolution_service.SkillEvolutionService` —
  bundled skill/stats/A-B-test view + A/B test resolution.
* :class:`~daemon.services.skill_job_dispatcher.SkillJobDispatcher` —
  dispatches ``FIX`` evolution jobs to the skill-keeper agent.
* :class:`~daemon.repositories.skill.SkillTriggerRepository` —
  declarative condition → action rules.

DI conventions (matches :mod:`daemon.routers.jobs_crud` and
:mod:`daemon.routers.work`):

* The four Phase 2 services are wired via
  :func:`daemon.utils.create_service_dependency` so the accessors
  raise HTTP 503 ("service not initialized") when called before
  startup completes.
* The trigger repository and the job dispatcher use the
  module-level singleton + setter + ``_require_*`` access pattern
  — same shape as :mod:`daemon.routers.work` — because the
  ``create_service_dependency`` factory does not cover classes
  that take their own constructor arguments.

Response shape conventions (Phase 6 polish):

* Skill endpoints (``POST``, ``GET``, ``PUT``, ``DELETE``,
  ``/share``, ``/deactivate``, ``/fix``) return **skill objects
  directly** — no ``{"skill": …}`` envelope. Matches the
  ``/api/work`` list pattern (``GET /api/work`` returns a bare
  list, every record a flat dict) and the ``JobResponse``
  single-resource pattern. The legacy envelope caused the Skills
  page to compute ``NaN%`` for the success-rate chip because the
  front-end ``SkillService.get`` returned ``undefined`` for
  ``response.skill`` when the server omitted the wrapper.
* ``GET /api/skills/{id}`` is the canonical "skill detail"
  endpoint — it returns the full skill record **plus** the
  ``lineage`` and ``metrics`` sub-payloads inline so the detail
  page can render in one round-trip instead of three.
* ``GET /api/skills/{id}/metrics`` returns the
  :class:`~daemon.services.skill_metrics_service.SkillMetricsService.get_skill_stats`
  shape directly (no ``{"stats": …}`` envelope). The endpoint
  returns a zero-stat dict (HTTP 200) when the skill is missing
  so the front-end can render an empty analytics card without
  having to special-case a 404 — the metric endpoint is
  intentionally non-fatal for the detail page's call.
* ``GET /api/skills/{id}/lineage`` returns the
  :class:`~daemon.models.skill.SkillLineage` shape directly
  (``{parents, children, generation, origin, skill_id}``) so
  the detail page can render the lineage panel without an
  intermediate unwrap step. The previous shape
  ``{"skill_id", "lineage": {"skill", "lineage": {…}}}`` was a
  double-nest that no caller actually used.
* ``POST /api/skills/{id}/ab-test/resolve`` returns the
  resolution dict from
  :meth:`~daemon.services.skill_evolution_service.SkillEvolutionService.check_ab_test_resolution`
  directly (already flat — no change).
* ``POST /api/skills/{id}/feedback`` returns
  ``{"recorded": bool}`` — the only envelope in the file and
  intentionally preserved because it is the public signal the
  caller uses to decide whether the feedback stuck.

Error-handling conventions:

* :class:`ValueError` raised by the evolution / dispatcher services
  maps to HTTP 400 with ``{"error": str(e)}``.
* Any other exception is logged via :func:`logger.exception` and
  re-raised as HTTP 500 with
  ``{"error": "Internal error", "message": str(e)}``.
* Missing resources (``get`` returns ``None``) map to HTTP 404.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from daemon.services.skill_job_dispatcher import SkillJobDispatcher
from daemon.services.skill_store_service import SkillStoreService
from daemon.services.skill_search_service import SkillSearchService
from daemon.services.skill_metrics_service import SkillMetricsService
from daemon.services.skill_evolution_service import SkillEvolutionService
from daemon.repositories.skill import SkillTriggerRepository
from daemon.utils import (
    create_service_dependency,
    raise_service_unavailable,
)

from .skill_schemas import (
    SkillCreateRequest,
    SkillFeedbackRequest,
    SkillFixRequest,
    SkillSearchRequest,
    SkillUpdateRequest,
    TriggerCreateRequest,
    TriggerUpdateRequest,
)

if TYPE_CHECKING:
    from daemon.services.skill_store_service import SkillStoreService
    from daemon.services.skill_search_service import SkillSearchService
    from daemon.services.skill_metrics_service import SkillMetricsService
    from daemon.services.skill_evolution_service import SkillEvolutionService

logger = logging.getLogger(__name__)

# Router mounted under ``/api`` by ``daemon/api.py`` (another agent
# wires the include_router / set_*_calls). Public URL prefix is
# ``/api/skills`` for the resource endpoints and
# ``/api/skills/triggers`` (relative) for the trigger sub-resource.
router = APIRouter(prefix="/skills", tags=["skills"])


# ============================================================
# Service DI accessors
# ============================================================
# The four Phase 2 services are routed through
# ``create_service_dependency`` so missing initialization surfaces
# as HTTP 503 (the standard "service not initialized" response —
# see ``daemon/routers/jobs_crud.py:34-35`` and ``daemon/utils.py``).


get_store = create_service_dependency(SkillStoreService)
get_search = create_service_dependency(SkillSearchService)
get_metrics = create_service_dependency(SkillMetricsService)
get_evolution = create_service_dependency(SkillEvolutionService)


# ============================================================
# Trigger repository + job dispatcher (manual wiring)
# ============================================================
# These two collaborators take constructor arguments and don't
# fit the ``create_service_dependency`` shape. The pattern mirrors
# ``daemon/routers/work.py``: module-level ``_foo`` global + a
# ``set_foo(...)`` setter for the startup wiring + a private
# ``_require_foo()`` accessor that raises 503 if the global is
# unset. The trigger repo and dispatcher both need this — the
# router uses the trigger repo directly (no service layer in
# between), and the dispatcher is wired explicitly because its
# constructor needs the JobQueueService + queue repo.


_skill_trigger_repo: SkillTriggerRepository | None = None
_skill_job_dispatcher: SkillJobDispatcher | None = None


def set_skill_trigger_repo(repo: SkillTriggerRepository) -> None:
    """Inject the :class:`SkillTriggerRepository` singleton.

    Called from ``daemon/api.py`` lifespan startup after the engine
    factory has constructed the repo. Idempotent — calling more
    than once replaces the previously-set instance.

    Args:
        repo: The SkillTriggerRepository bound to the project
            engine.
    """
    global _skill_trigger_repo
    _skill_trigger_repo = repo


def set_skill_job_dispatcher(dispatcher: SkillJobDispatcher) -> None:
    """Inject the :class:`SkillJobDispatcher` singleton.

    Called from ``daemon/api.py`` lifespan startup after the
    dispatcher is constructed. Idempotent.

    Args:
        dispatcher: The SkillJobDispatcher singleton wired into
            the daemon's job queue + skill-keeper agent.
    """
    global _skill_job_dispatcher
    _skill_job_dispatcher = dispatcher


def _get_skill_trigger_repo() -> SkillTriggerRepository:
    """Return the wired-in SkillTriggerRepository, else 503.

    Returns:
        The SkillTriggerRepository instance.

    Raises:
        HTTPException: 503 if :func:`set_skill_trigger_repo` was
            not called during app startup.
    """
    if _skill_trigger_repo is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Service not initialized",
                "message": "SkillTriggerRepository not initialized",
            },
        )
    return _skill_trigger_repo


def _require_skill_dispatcher() -> SkillJobDispatcher:
    """Return the wired-in SkillJobDispatcher, else 503.

    Returns:
        The SkillJobDispatcher instance.

    Raises:
        HTTPException: 503 if :func:`set_skill_job_dispatcher`
            was not called during app startup.
    """
    if _skill_job_dispatcher is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Service not initialized",
                "message": "SkillJobDispatcher not initialized",
            },
        )
    return _skill_job_dispatcher


# ============================================================
# Serialization helpers
# ============================================================


def _skill_to_dict(skill: Any) -> dict[str, Any] | None:
    """Project a skill SQLModel / dict to a JSON-safe dict.

    Handles the three shapes the store service can hand back:

    1. ``Skill.to_dict()`` already called (rare — the store keeps
       the SQLModel around): returns the dict as-is.
    2. A SQLModel instance: falls back to column iteration via
       ``__table__.columns`` so test mocks without ``to_dict``
       don't crash the response shape.
    3. ``None`` for ``get_skill`` misses — propagated as ``None``
       so the calling route can return 404.

    Args:
        skill: Skill row, dict, or ``None``.

    Returns:
        JSON-safe dict, or ``None`` if ``skill`` was ``None``.
    """
    if skill is None:
        return None
    if hasattr(skill, "to_dict"):
        return skill.to_dict()
    if isinstance(skill, dict):
        return skill
    # SQLModel fallback — iterate declared columns.
    return {c.name: getattr(skill, c.name) for c in skill.__table__.columns}


def _flatten_lineage_view(bundle: dict | None, skill_id: str) -> dict[str, Any]:
    """Project ``view_skill`` into the flat :class:`SkillLineage` shape.

    The ``view_skill`` bundle returns
    ``{"skill": <metadata>, "lineage": {"parents": [...], "children": [...]}}``
    where each parent / child row carries the full lineage edge
    metadata (``parent_skill_id``, ``child_skill_id``,
    ``generation_delta``, etc.). The lineage endpoint reuses the
    bundle but strips the meta-only skill body, then projects the
    two edge lists at the top level so the front-end can render
    the lineage panel without an unwrap step.

    The result matches :class:`daemon.models.skill.SkillLineage`:
    ``{skill_id, parents, children, generation, origin}``.

    Args:
        bundle: The ``view_skill`` payload (``{"skill": …,
            "lineage": …}``), or ``None`` if the skill is missing.
        skill_id: The skill id — used as a top-level key so the
            caller doesn't have to fish it out of the URL.

    Returns:
        Flat ``SkillLineage`` shape with ``content`` stripped
        from each parent / child row.
    """
    skill: Any = bundle.get("skill") if isinstance(bundle, dict) else None
    lineage = bundle.get("lineage") if isinstance(bundle, dict) else None
    parents_raw: list[Any] = (lineage or {}).get("parents") or []
    children_raw: list[Any] = (lineage or {}).get("children") or []

    def _strip(row: Any) -> Any:
        if isinstance(row, dict):
            return {k: v for k, v in row.items() if k != "content"}
        return row

    generation = 0
    origin = "imported"
    if isinstance(skill, dict):
        generation = int(skill.get("generation") or 0)
        origin = skill.get("lineage_origin") or "imported"

    return {
        "skill_id": skill_id,
        "generation": generation,
        "origin": origin,
        "parents": [_strip(p) for p in parents_raw],
        "children": [_strip(c) for c in children_raw],
    }


def _trigger_to_dict(trigger: Any) -> dict[str, Any] | None:
    """Project a trigger SQLModel / dict to a JSON-safe dict.

    Trigger rows expose ``to_dict()`` for happy-path serialization
    but the repository does not require it — fall back to
    ``vars()`` for duck-typed instances (test mocks) and to
    column iteration for raw SQLModels.

    Args:
        trigger: A SkillTrigger instance, dict, or duck-typed
            mock.

    Returns:
        JSON-safe dict mirroring the trigger's attributes.
    """
    if trigger is None:
        return None
    if hasattr(trigger, "to_dict"):
        return trigger.to_dict()
    if isinstance(trigger, dict):
        return trigger
    if hasattr(trigger, "__table__"):
        return {c.name: getattr(trigger, c.name) for c in trigger.__table__.columns}
    return vars(trigger)


def _to_http_500(e: Exception, op: str) -> HTTPException:
    """Wrap an unexpected exception as a logged HTTP 500.

    Args:
        e: The original exception.
        op: Short operation description for the log (e.g.
            ``"create_skill"``).

    Returns:
        An HTTPException with status 500.
    """
    logger.exception("[SkillsRouter] %s failed", op)
    return HTTPException(
        status_code=500,
        detail={"error": "Internal error", "message": str(e)},
    )


# ============================================================
# Skill endpoints
# ============================================================

# IMPORTANT: ``/triggers`` literal routes MUST be registered before
# the ``/{skill_id}/...`` wildcard routes below. Starlette's path
# matcher walks the route list in order and matches the first
# pattern that fits — if ``GET /skills/{skill_id}`` is reached
# first, a request to ``GET /skills/triggers`` would resolve to
# ``skill_id="triggers"`` and return the wrong payload. Registering
# the trigger sub-resource first avoids the ambiguity without
# requiring a path-validator regex on ``{skill_id}``.


@router.get("")
async def list_skills(
    project_id: str | None = Query(
        default=None,
        description="Project scope; None = global library only.",
    ),
    category: str | None = Query(
        default=None,
        description="Optional category bucket filter.",
    ),
    active_only: bool = Query(
        default=True,
        description="Filter to status='active' rows when true.",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    store: SkillStoreService = Depends(get_store),
) -> list[dict[str, Any]]:
    """List skills with project-scope filtering.

    Returns the skills as a flat array (no ``{"items", "total"}``
    envelope) so the response shape matches the ``GET /api/work``
    list pattern. Each item carries the full set of list-view
    columns — counters, lineage origin, A/B-test group, last-used
    timestamp — so the Skills page card can render success-rate
    chips, A/B-test badges, and the deactivate / share actions
    without a per-row detail fetch. The bulky ``content`` body is
    intentionally stripped at the store layer.

    Args:
        project_id: Project scope. ``None`` returns only globals.
        category: Category filter (Phase 3 repo layer may ignore
            this on the initial pass — kept on the route for
            forward compatibility).
        active_only: When ``True`` (default), filter to active
            rows.
        limit: Maximum rows per bucket (the store merges two
            buckets so the combined count can be up to
            ``2 * limit``).
        offset: Skip-N offset (applied per bucket).
        store: Injected SkillStoreService via Depends.

    Returns:
        Flat JSON array of skill dicts. Empty list when nothing
        matches the filters.

    Raises:
        HTTPException: 500 if the underlying service raises.
    """
    try:
        items, _total = await store.list_skills(
            project_id=project_id,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        # Phase 3 filter hook: the v1 store does not accept a
        # category kwarg. Apply it client-side here so the wire
        # contract already supports it.
        if category:
            items = [s for s in items if s.get("category") == category]
        return items
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except Exception as e:
        raise _to_http_500(e, "list_skills")


@router.post("", status_code=201)
async def create_skill(
    body: SkillCreateRequest,
    store: SkillStoreService = Depends(get_store),
) -> dict[str, Any]:
    """Create a new skill and refresh its embedding cache.

    Args:
        body: The create payload (name, description, content,
            optional project_id, category).
        store: Injected SkillStoreService via Depends.

    Returns:
        201 with the freshly-created skill row (no
        ``{"skill": …}`` envelope). The full row is returned
        serialized via :func:`_skill_to_dict`.

    Raises:
        HTTPException: 400 if a value-error comes out of the
            repo (e.g. UNIQUE constraint violation); 500 for any
            other failure.
    """
    try:
        skill = await store.create_skill(**body.model_dump(exclude_none=True))
        return _skill_to_dict(skill) or {}
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except Exception as e:
        raise _to_http_500(e, "create_skill")


# ============================================================
# Trigger endpoints (registered before ``/{skill_id}`` to avoid
# route shadowing — see the routing-order warning above)
# ============================================================


@router.get("/triggers")
async def list_triggers(
    project_id: str | None = Query(
        default=None,
        description="Project scope; None lists globals.",
    ),
    enabled_only: bool = Query(
        default=True,
        description="When True, restrict to is_enabled=True rows.",
    ),
) -> dict[str, Any]:
    """List trigger rules.

    Args:
        project_id: Project scope.
        enabled_only: Enabled-only filter (default True).
        repo: Resolved via :func:`_get_skill_trigger_repo` —
            FastAPI will use the default for non-Depends params,
            so we resolve manually below.

    Returns:
        ``{"items": [...]}`` with each trigger serialized via
        :func:`_trigger_to_dict`.

    Raises:
        HTTPException: 503 if the repo was never initialized;
        500 on unexpected failure.
    """
    repo = _get_skill_trigger_repo()
    try:
        triggers = repo.list(
            project_id=project_id,
            enabled_only=enabled_only,
        )
        return {
            "items": [
                _trigger_to_dict(t) for t in triggers if t is not None
            ]
        }
    except Exception as e:
        raise _to_http_500(e, "list_triggers")


@router.post("/triggers")
async def create_trigger(
    body: TriggerCreateRequest,
) -> JSONResponse:
    """Create a new trigger rule.

    Args:
        body: The create payload.

    Returns:
        201 with the created trigger.

    Raises:
        HTTPException: 503 if the repo was never initialized;
        500 on unexpected failure.
    """
    repo = _get_skill_trigger_repo()
    try:
        trigger = repo.create(**body.model_dump(exclude_none=True))
        return JSONResponse(
            status_code=201,
            content={"trigger": _trigger_to_dict(trigger)},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except Exception as e:
        raise _to_http_500(e, "create_trigger")


@router.put("/triggers/{trigger_id}")
async def update_trigger(
    trigger_id: str,
    body: TriggerUpdateRequest,
) -> dict[str, Any]:
    """Apply a partial update to a trigger rule.

    Args:
        trigger_id: The trigger's UUID4 primary key.
        body: Partial update payload. ``None`` fields are
            dropped.

    Returns:
        ``{"trigger": {...}}`` with the updated trigger.

    Raises:
        HTTPException: 404 if no row matches; 400 on
            ``ValueError``; 500 on unexpected failure.
    """
    repo = _get_skill_trigger_repo()
    try:
        trigger = repo.update(
            trigger_id,
            **body.model_dump(exclude_none=True),
        )
        if trigger is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Trigger not found", "trigger_id": trigger_id},
            )
        return {"trigger": _trigger_to_dict(trigger)}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except Exception as e:
        raise _to_http_500(e, "update_trigger")


@router.delete("/triggers/{trigger_id}")
async def delete_trigger(
    trigger_id: str,
) -> dict[str, Any]:
    """Hard-delete a trigger rule.

    Args:
        trigger_id: The trigger's UUID4 primary key.

    Returns:
        ``{"deleted": true}`` on success, ``{"deleted": false}``
        when no row matched.

    Raises:
        HTTPException: 503 if the repo was never initialized;
        500 on unexpected failure.
    """
    repo = _get_skill_trigger_repo()
    try:
        deleted = repo.delete(trigger_id)
        return {"deleted": bool(deleted)}
    except Exception as e:
        raise _to_http_500(e, "delete_trigger")


@router.get("/{skill_id}")
async def get_skill(
    skill_id: str,
    store: SkillStoreService = Depends(get_store),
    metrics: SkillMetricsService = Depends(get_metrics),
) -> dict[str, Any]:
    """Fetch a single skill by ID, enriched with lineage and metrics.

    The detail endpoint is the canonical "skill detail" payload:
    the full skill row (including the ``content`` body) plus the
    pre-computed ``lineage`` parents / children and the
    ``metrics`` counters. The front-end detail page consumes all
    three in a single render, so bundling them server-side keeps
    the page from making three sequential round-trips.

    The lineage bundle is fetched via :meth:`SkillStoreService.view_skill`
    and stripped of the (large) ``content`` field on the linked
    rows to keep the detail payload compact. Metrics come from
    :meth:`SkillMetricsService.get_skill_stats` — a missing skill
    surfaces as a 404 from the route, so the metrics call is
    only reached when the skill exists.

    Args:
        skill_id: The skill's UUID4 primary key. Must be non-blank.
        store: Injected SkillStoreService via Depends.
        metrics: Injected SkillMetricsService via Depends.

    Returns:
        Flat skill record (no ``{"skill": …}`` envelope) with two
        extra keys: ``lineage`` (parents + children array) and
        ``metrics`` (counters + derived rates).

    Raises:
        HTTPException: 400 if ``skill_id`` is blank; 404 if no
            row matches; 500 on unexpected failure.
    """
    if not skill_id or not skill_id.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": "skill_id is required"},
        )
    try:
        skill = await store.get_skill(skill_id)
        if skill is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Skill not found", "skill_id": skill_id},
            )
        detail = _skill_to_dict(skill) or {}
        # Enrich with lineage (parents + children) and metrics.
        # Each lookup is best-effort — a missing lineage / metrics
        # row should NOT 500 the detail endpoint, only 404 on a
        # truly missing skill (which we already raised above).
        bundle = await store.view_skill(skill_id)
        if bundle and isinstance(bundle, dict):
            inner_lineage = bundle.get("lineage") or {}
            # Strip ``content`` from parent / child rows so the
            # lineage payload doesn't carry tens of KB per sibling.
            parents_raw = inner_lineage.get("parents") or []
            children_raw = inner_lineage.get("children") or []
            parents = [
                {k: v for k, v in p.items() if k != "content"}
                if isinstance(p, dict) else p
                for p in parents_raw
            ]
            children = [
                {k: v for k, v in c.items() if k != "content"}
                if isinstance(c, dict) else c
                for c in children_raw
            ]
            detail["lineage"] = {"parents": parents, "children": children}
        else:
            detail["lineage"] = {"parents": [], "children": []}
        try:
            detail["metrics"] = await metrics.get_skill_stats(skill_id)
        except Exception as metrics_exc:  # noqa: BLE001
            logger.warning(
                "[SkillsRouter] get_skill(%s) metrics fetch failed: %s",
                skill_id,
                metrics_exc,
            )
            detail["metrics"] = {
                "total": 0,
                "selected": 0,
                "applied": 0,
                "completions": 0,
                "fallbacks": 0,
                "avg_iterations": 0.0,
                "avg_duration": 0.0,
                "completion_rate": 0.0,
                "applied_rate": 0.0,
                "fallback_rate": 0.0,
                "consecutive_failures": 0,
            }
        return detail
    except HTTPException:
        raise
    except Exception as e:
        raise _to_http_500(e, "get_skill")


@router.get("/{skill_id}/view")
async def view_skill(
    skill_id: str,
    store: SkillStoreService = Depends(get_store),
) -> dict[str, Any]:
    """Return the full skill document plus its lineage graph.

    Args:
        skill_id: The skill's UUID4 primary key.
        store: Injected SkillStoreService via Depends.

    Returns:
        ``{"skill": {...}, "lineage": {"parents": [...],
        "children": [...]}}``. The ``lineage.parents`` /
        ``lineage.children`` arrays contain the
        ``SkillLineage.to_dict()`` rows.

    Raises:
        HTTPException: 404 if no row matches; 500 on unexpected
        failure.
    """
    try:
        bundle = await store.view_skill(skill_id)
        if bundle is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Skill not found", "skill_id": skill_id},
            )
        return bundle
    except HTTPException:
        raise
    except Exception as e:
        raise _to_http_500(e, "view_skill")


@router.put("/{skill_id}")
async def update_skill(
    skill_id: str,
    body: SkillUpdateRequest,
    store: SkillStoreService = Depends(get_store),
) -> dict[str, Any]:
    """Apply a partial update to a skill row.

    Forwarded via ``store.update_skill(skill_id, **fields)`` so the
    embedding cache refresh hook fires when the ``content`` field
    changes. ``is_active`` is mapped to ``status`` so the public
    API stays spoke-language while the SQLModel keeps its existing
    column name.

    Args:
        skill_id: The skill to update.
        body: Partial update payload. ``None`` fields are dropped.
        store: Injected SkillStoreService via Depends.

    Returns:
        The refreshed skill row (no ``{"skill": …}`` envelope).

    Raises:
        HTTPException: 404 if no row matches; 400 on
            ``ValueError``; 500 on unexpected failure.
    """
    fields = body.model_dump(exclude_none=True)
    # Translate the public ``is_active`` flag to the SQLModel's
    # ``status`` column. Keeping them aligned keeps the API
    # intuitive without changing the on-disk layout.
    if "is_active" in fields:
        fields["status"] = "active" if fields.pop("is_active") else "inactive"
    try:
        skill = await store.update_skill(skill_id, **fields)
        if skill is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Skill not found", "skill_id": skill_id},
            )
        return _skill_to_dict(skill) or {}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except Exception as e:
        raise _to_http_500(e, "update_skill")


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str,
    store: SkillStoreService = Depends(get_store),
) -> dict[str, Any]:
    """Soft-delete (deactivate) a skill row.

    Routes through ``deactivate_skill`` rather than the hard-delete
    ``delete_skill`` so usage history remains queryable. For a
    true purge, an operator can run the SQL directly.

    Args:
        skill_id: The skill to deactivate.
        store: Injected SkillStoreService via Depends.

    Returns:
        ``{"deactivated": true}`` on success.

    Raises:
        HTTPException: 404 if no row matches; 500 on unexpected
        failure.
    """
    try:
        skill = await store.deactivate_skill(skill_id)
        if skill is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Skill not found", "skill_id": skill_id},
            )
        return {"deactivated": True}
    except HTTPException:
        raise
    except Exception as e:
        raise _to_http_500(e, "delete_skill")


@router.post("/{skill_id}/deactivate")
async def deactivate_skill(
    skill_id: str,
    store: SkillStoreService = Depends(get_store),
) -> dict[str, Any]:
    """Soft-delete via the POST verb (alias of DELETE).

    Identical side effect to :func:`delete_skill` but returns
    the refreshed skill row directly (no ``{"skill": …}`` envelope)
    so the caller can confirm the new ``status`` without a second
    round-trip — the same shape ``GET /{skill_id}`` uses.

    Kept as a separate verb-routed endpoint so the frontend
    can wire ``POST`` buttons on usage / error screens without
    having to negotiate a ``DELETE`` preflight.

    Args:
        skill_id: The skill to deactivate.
        store: Injected SkillStoreService via Depends.

    Returns:
        The refreshed skill row (``status`` flipped to ``inactive``).

    Raises:
        HTTPException: 404 if no row matches; 500 on unexpected
            failure.
    """
    try:
        deactivated = await store.deactivate_skill(skill_id)
        if deactivated is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Skill not found", "skill_id": skill_id},
            )
        # Re-fetch so the response carries the post-update row
        # (deactivate_skill may not round-trip every field).
        # Mirrors the GET /{skill_id} envelope exactly.
        skill = await store.get_skill(skill_id)
        if skill is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Skill not found", "skill_id": skill_id},
            )
        return _skill_to_dict(skill) or {}
    except HTTPException:
        raise
    except Exception as e:
        raise _to_http_500(e, "deactivate_skill")


@router.post("/search")
async def search_skills(
    body: SkillSearchRequest,
    search: SkillSearchService = Depends(get_search),
) -> dict[str, Any]:
    """Run the three-stage skill search pipeline.

    Args:
        body: The search payload (query, project_id, max_results).
        search: Injected SkillSearchService via Depends.

    Returns:
        ``{"injected": [...], "low_match": [...]}`` as produced by
        :meth:`SkillSearchService.search`.

    Raises:
        HTTPException: 500 on unexpected failure.
    """
    try:
        result = await search.search(
            body.query,
            body.project_id,
            body.max_results,
        )
        return result
    except Exception as e:
        raise _to_http_500(e, "search_skills")


@router.get("/{skill_id}/lineage")
async def get_lineage(
    skill_id: str,
    store: SkillStoreService = Depends(get_store),
) -> dict[str, Any]:
    """Return the lineage graph for a skill (no body content).

    Returns the flat :class:`daemon.models.skill.SkillLineage`
    shape directly (``{skill_id, parents, children, generation,
    origin}``) — no doubly-nested ``.lineage.lineage`` envelope.
    ``content`` is stripped from each parent / child row to keep
    the payload compact; callers needing the central node's body
    should fetch ``GET /api/skills/{id}``.

    Args:
        skill_id: The skill to summarise.
        store: Injected SkillStoreService via Depends.

    Returns:
        Flat ``SkillLineage`` dict.

    Raises:
        HTTPException: 404 if no row matches; 500 on unexpected
            failure.
    """
    try:
        bundle = await store.view_skill(skill_id)
        if bundle is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Skill not found", "skill_id": skill_id},
            )
        return _flatten_lineage_view(bundle, skill_id)
    except HTTPException:
        raise
    except Exception as e:
        raise _to_http_500(e, "get_lineage")


@router.get("/{skill_id}/metrics")
async def get_skill_metrics_endpoint(
    skill_id: str,
    metrics: SkillMetricsService = Depends(get_metrics),
) -> dict[str, Any]:
    """Return the per-skill counter bundle + derived rates.

    Returns the
    :meth:`~daemon.services.skill_metrics_service.SkillMetricsService.get_skill_stats`
    shape **directly** (no ``{"stats": …}`` envelope) so the
    front-end :class:`SkillMetrics` interface maps 1:1. A missing
    skill surfaces as a 404 so callers can distinguish a missing
    skill from a zero-stat row (``total_selections == 0`` still
    surfaces a valid 200 with all-zero counters).

    Args:
        skill_id: The skill to summarise.
        metrics: Injected SkillMetricsService via Depends.

    Returns:
        Flat ``SkillMetrics`` dict (counters + derived rates).

    Raises:
        HTTPException: 500 on unexpected failure. (Missing skills
            surface as a zero-stat dict rather than 404 here —
            the metric endpoint is intentionally non-fatal for the
            detail page's call.)
    """
    try:
        stats = await metrics.get_skill_stats(skill_id)
        return stats
    except Exception as e:
        raise _to_http_500(e, "get_metrics")


@router.get("/{skill_id}/usage")
async def get_usage(
    skill_id: str,
    metrics: SkillMetricsService = Depends(get_metrics),
) -> dict[str, Any]:
    """Return aggregate usage stats for a skill.

    v1 read — exposes the denormalized counter columns and the
    derived rates via
    :meth:`SkillMetricsService.get_skill_stats`. A full usage
    record feed (per-event) is a Phase 7 deliverable.

    Args:
        skill_id: The skill to summarise.
        metrics: Injected SkillMetricsService via Depends.

    Returns:
        ``{"skill_id": skill_id, "stats": {...}}`` with keys
        matching :meth:`SkillMetricsService.get_skill_stats`.

    Raises:
        HTTPException: 500 on unexpected failure.
    """
    try:
        stats = await metrics.get_skill_stats(skill_id)
        return {"skill_id": skill_id, "stats": stats}
    except Exception as e:
        raise _to_http_500(e, "get_usage")


@router.post("/{skill_id}/feedback")
async def post_feedback(
    skill_id: str,
    body: SkillFeedbackRequest,
    instance_id: str | None = Query(
        default=None,
        description=(
            "Originating instance ID. Optional — when omitted the "
            "service cannot attach feedback to a specific usage "
            "record and returns ``recorded=False``."
        ),
    ),
    agent_id: str | None = Query(
        default=None,
        description="Originating agent ID. Optional.",
    ),
    project_id: str | None = Query(
        default=None,
        description="Project scope (for audit hooks; not used at the row layer in Phase 6).",
    ),
    metrics: SkillMetricsService = Depends(get_metrics),
) -> dict[str, Any]:
    """Stamp feedback onto the most recent usage record.

    ``instance_id`` / ``agent_id`` are OPTIONAL query parameters.
    Without ``instance_id`` the metrics service has no usage
    record to attach feedback to and the call resolves with
    ``{"recorded": False}`` — not an error. This keeps the
    endpoint safe for callers (e.g. CLI / scripted fixes) that
    don't have an originating instance handy.

    Args:
        skill_id: The skill being given feedback on.
        body: ``{"applied": bool | None, "note": str}``.
        instance_id: Originating instance ID (optional).
        agent_id: Originating agent ID (optional).
        project_id: Project scope (optional; forward-compatible).
        metrics: Injected SkillMetricsService via Depends.

    Returns:
        ``{"recorded": True}`` when a usage row was stamped,
        ``{"recorded": False}`` when no usage record was found
        (including the case where ``instance_id`` was omitted).

    Raises:
        HTTPException: 400 on ``ValueError`` from the service;
        500 on unexpected failure.
    """
    try:
        recorded = await metrics.record_feedback(
            skill_id=skill_id,
            instance_id=instance_id or "",
            agent_id=agent_id or "",
            project_id=project_id,
            applied=body.applied,
            note=body.note,
        )
        return {"recorded": bool(recorded)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except Exception as e:
        raise _to_http_500(e, "post_feedback")


@router.post("/{skill_id}/fix")
async def post_fix(
    skill_id: str,
    body: SkillFixRequest,
    project_id: str | None = Query(
        default=None,
        description="Optional project scope; None routes through the system default.",
    ),
    instance_id: str | None = Query(
        default=None,
        description="Optional originating instance ID for audit/lineage.",
    ),
    dispatcher: SkillJobDispatcher = Depends(_require_skill_dispatcher),
) -> JSONResponse:
    """Dispatch a user-reported skill fix request.

    Args:
        skill_id: The skill to fix.
        body: Issue description + optional suggested fix.
        project_id: Project scope (defaults to ``None``).
        instance_id: Originating instance ID (forwarded to the
            dispatcher for audit hooks).
        dispatcher: Injected SkillJobDispatcher via Depends.

    Returns:
        202 Accepted with ``{"job_id": str}``.

    Raises:
        HTTPException: 400 on ``ValueError``; 500 on unexpected
        failure.
    """
    try:
        job_id = await dispatcher.dispatch_fix(
            project_id=project_id,
            skill_id=skill_id,
            issue_description=body.issue_description,
            suggested_fix=body.suggested_fix or "",
            current_instance_id=instance_id or "",
        )
        return JSONResponse(
            status_code=202,
            content={"job_id": job_id},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except Exception as e:
        raise _to_http_500(e, "post_fix")


@router.get("/{skill_id}/ab-test")
async def get_ab_test(
    skill_id: str,
    evolution: SkillEvolutionService = Depends(get_evolution),
) -> dict[str, Any]:
    """Return the A/B test status for a skill.

    Looks the skill up via
    :meth:`SkillEvolutionService.get_skill_metrics` (so a 404 on
    the metrics call also surfaces as a 404 here) and projects
    the ``ab_test`` sub-dict. ``ab_test`` is ``None`` when the
    skill is not enrolled in a test.

    Args:
        skill_id: The skill to query.
        evolution: Injected SkillEvolutionService via Depends.

    Returns:
        ``{"skill_id": skill_id, "ab_test": {...} | null}`` —
        ``null`` when the skill is not in a test.

    Raises:
        HTTPException: 404 if the metrics call says the skill
        was not found; 400 on ``ValueError``; 500 on unexpected
        failure.
    """
    try:
        metrics = await evolution.get_skill_metrics(skill_id)
        if not metrics.get("found"):
            raise HTTPException(
                status_code=404,
                detail={"error": "Skill not found", "skill_id": skill_id},
            )
        return {
            "skill_id": skill_id,
            "ab_test": metrics.get("ab_test"),
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except Exception as e:
        raise _to_http_500(e, "get_ab_test")


@router.post("/{skill_id}/ab-test/resolve")
async def resolve_ab_test(
    skill_id: str,
    winner_id: str | None = Query(
        default=None,
        description=(
            "Optional forced-winner skill ID. When set, the "
            "evolution service force-selects this variant as "
            "the winner instead of falling back to the "
            "completion-rate-driven decision tree. The ID "
            "must match one of the test's two variants — "
            "anything else raises 400."
        ),
    ),
    evolution: SkillEvolutionService = Depends(get_evolution),
) -> dict[str, Any]:
    """Check whether the skill's A/B test should resolve.

    Steps:

    1. Look the skill up via
       :meth:`SkillEvolutionService.get_skill_metrics` so a missing
       row surfaces as 404.
    2. Read ``ab_test.ab_test_group`` off the metrics payload.
       ``None`` (skill not in a test) returns a 404 with a clear
       message.
    3. Call :meth:`SkillEvolutionService.check_ab_test_resolution`
       with the group ID and the optional ``winner_id`` and return
       the dict verbatim.

    Args:
        skill_id: The skill to resolve.
        winner_id: Optional forced-winner ID. When provided, the
            service force-selects this variant (the other is
            deactivated). When ``None``, the service runs its
            normal completion-rate-driven decision tree.
        evolution: Injected SkillEvolutionService via Depends.

    Returns:
        ``{"skill_id": skill_id, "ab_test_group": str, ...}``
        with the resolution dict fields merged in.

    Raises:
        HTTPException: 404 if the skill is missing or not in a
        test; 400 on ``ValueError`` (e.g. ``winner_id`` not in
        the test group); 500 on unexpected failure.
    """
    try:
        metrics = await evolution.get_skill_metrics(skill_id)
        if not metrics.get("found"):
            raise HTTPException(
                status_code=404,
                detail={"error": "Skill not found", "skill_id": skill_id},
            )
        ab_test = metrics.get("ab_test") or {}
        ab_group = ab_test.get("ab_test_group")
        if not ab_group:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "Skill is not in an A/B test",
                    "skill_id": skill_id,
                },
            )
        result = await evolution.check_ab_test_resolution(
            ab_group, winner_id=winner_id,
        )
        result["skill_id"] = skill_id
        result["ab_test_group"] = ab_group
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except Exception as e:
        raise _to_http_500(e, "resolve_ab_test")


@router.post("/{skill_id}/share")
async def share_skill(
    skill_id: str,
    store: SkillStoreService = Depends(get_store),
) -> dict[str, Any]:
    """Promote a project-scoped skill to the global library.

    Implementation: clears the ``project_id`` column via
    ``store.update_skill``. The reverse ("global → project
    scope") is intentionally NOT routed here — a deliberate
    share-down is a Phase 7 admin operation.

    Args:
        skill_id: The skill to promote.
        store: Injected SkillStoreService via Depends.

    Returns:
        The updated skill row (no ``{"skill": …}`` envelope).

    Raises:
        HTTPException: 404 if no row matches; 400 on
            ``ValueError``; 500 on unexpected failure.
    """
    try:
        skill = await store.update_skill(skill_id, project_id=None)
        if skill is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "Skill not found", "skill_id": skill_id},
            )
        return _skill_to_dict(skill) or {}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    except Exception as e:
        raise _to_http_500(e, "share_skill")


__all__ = [
    "router",
    "set_skill_trigger_repo",
    "set_skill_job_dispatcher",
    "get_store",
    "get_search",
    "get_metrics",
    "get_evolution",
]
