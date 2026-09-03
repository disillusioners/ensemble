"""Mission HTTP surface — GET /missions + GET /missions/{mission_id}.

Mission-class M4-i pull-forward (2026-09-02, ``feature/mission-class``):
the user-approved early ship of the HTTP read surface from the M4(i)
gated option, ahead of the M2 agent tools (the spec's own M2). The
contract is documented in ``docs/job-task-system.md`` §8.4 — that
section is also the home of the FLAG matrix for every spec-silent
choice made here (the spec is silent on this endpoint's list contract;
the "W2 design" referenced by the planning tree is not in the tree).

Architecture mirrors ``daemon/routers/work.py`` (which mirrors
``daemon/routers/queues.py``):

* module-level ``_missions_resolver`` global
* ``set_missions_resolver(...)`` setter called from ``daemon/api.py``
  during lifespan startup (wired against the same READ-only
  ``InstanceRepository`` / ``JobRepository`` the WorkResolverService
  uses)
* ``get_missions_resolver()`` FastAPI Depends factory that raises 503
  if the service has not been wired in

Both routes are thin: the kill-switch gate (``is_mission_projection_enabled``),
query validation, and delegation to :class:`MissionResolver` — the
projection, ordering, pagination, and degradation contract all live in
the resolver (``resolve_page`` / ``resolve``). No writes, no JobItem
creation, no admission-state mutation; census frozen
(``daemon/job_state/constitution.py``: ``KNOWN_ADMISSION_STATE_WRITERS``).

Kill-switch (fail-closed): when ``ENSEMBLE_MISSION_PROJECTION_ENABLED``
is unset/blank/falsy (the OFF default) BOTH routes answer **404** while
remaining registered (OpenAPI still shows them — the docstrings and
§8.4 document the OFF behavior). ON ⇒ the normal contract below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query

from daemon.constants import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from daemon.routers.schemas import MissionListResponse, MissionResponse
from daemon.services.mission_resolver import (
    MISSION_LIVENESS_FILTER_VALUES,
    MissionRecord,
    is_mission_projection_enabled,
)

if TYPE_CHECKING:
    from daemon.services.mission_resolver import MissionResolver

# Create router. The prefix is ``/missions`` because the router is
# mounted under ``/api`` in ``daemon/api.py`` — the public URLs are
# ``GET /api/missions`` and ``GET /api/missions/{mission_id}``.
router = APIRouter(prefix="/missions", tags=["missions"])


# ── DI: MissionResolver ────────────────────────────────────────────────────
# Module-level singleton + setter + Depends factory (the queues.py /
# work.py pattern) so the lifespan wiring in ``daemon/api.py`` stays
# uniform across routers. 503 (not 500) is the correct status for
# "service not initialised" — same convention as work.py.

_missions_resolver: "MissionResolver | None" = None


def get_missions_resolver() -> "MissionResolver":
    """Return the wired-in :class:`MissionResolver`, or 503.

    Returns:
        The MissionResolver instance.

    Raises:
        HTTPException: 503 if the service has not been initialized
            via :func:`set_missions_resolver` during app startup.
    """
    if _missions_resolver is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Mission resolver service not initialized"},
        )
    return _missions_resolver


def set_missions_resolver(resolver: "MissionResolver") -> None:
    """Set the :class:`MissionResolver` instance.

    Called from ``daemon/api.py`` lifespan startup after the resolver
    is constructed (wired next to the ``set_work_resolver`` call).
    Idempotent — calling multiple times replaces the singleton.

    Args:
        resolver: The MissionResolver singleton (READ repositories
            only — ``SQLModelInstanceRepository`` + ``JobRepository``).
    """
    global _missions_resolver
    _missions_resolver = resolver


# ── Helpers ────────────────────────────────────────────────────────────────


def _parse_liveness_filter(liveness: str | None) -> list[str] | None:
    """Parse + validate the comma-separated ``liveness`` query param.

    Accepts a single value (``processing``) or a comma-separated
    multi-filter (``completed,failed`` — OR semantics, applied as one
    SQL ``IN``-clause by the resolver). Values are trimmed and
    lowercased before validation so ``Completed, FAILED`` is accepted.

    Args:
        liveness: Raw query param (``None`` = no filter).

    Returns:
        The parsed list of canonical liveness values, or ``None`` when
        no filter was requested (blank / whitespace-only also degrades
        to ``None``).

    Raises:
        HTTPException: 400 for any unknown value — a typo must not
            silently return an empty list (the work.py ``kind``
            precedent).
    """
    if liveness is None:
        return None
    parts = [p.strip().lower() for p in liveness.split(",")]
    parts = [p for p in parts if p]
    if not parts:
        return None
    unknown = [p for p in parts if p not in MISSION_LIVENESS_FILTER_VALUES]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"Invalid liveness value(s): {unknown!r}",
                "accepted": sorted(MISSION_LIVENESS_FILTER_VALUES),
            },
        )
    # De-duplicate while preserving order (e.g. "processing,processing").
    seen: set[str] = set()
    ordered = [p for p in parts if not (p in seen or seen.add(p))]
    return ordered


def _mission_record_to_response(record: MissionRecord) -> MissionResponse:
    """Map a :class:`MissionRecord` onto the wire schema (explicit).

    Explicit field-by-field construction (the ``InstanceInfo``
    precedent) rather than ``model_validate(from_attributes=True)`` —
    the mapping stays greppable if either side ever grows a field.
    """
    return MissionResponse(
        mission_id=record.mission_id,
        agent_id=record.agent_id,
        parent_mission_id=record.parent_mission_id,
        liveness=record.liveness,
        terminal_reason=record.terminal_reason,
        epoch=record.epoch,
        linked_jobs=list(record.linked_jobs),
        started_at=record.started_at,
        last_activity_at=record.last_activity_at,
    )


# ── Routes ────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=MissionListResponse,
    summary="List missions (mission read-model projection)",
    responses={
        404: {"description": "Mission projection disabled (kill-switch OFF)"},
        503: {"description": "Mission resolver service not initialized"},
    },
)
async def list_missions(
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
    liveness: str | None = Query(
        default=None,
        description=(
            "Canonical mission liveness filter — single value or "
            "comma-separated multi (OR): pending/processing/paused/"
            "completed/failed/cancelled. Applied in SQL via the "
            "inverted instance-status canonical map (§8.2)"
        ),
    ),
    agent_id: str | None = Query(
        default=None,
        description="Exact-match filter on Instance.agent_id",
    ),
    resolver: "MissionResolver" = Depends(get_missions_resolver),
) -> MissionListResponse:
    """List all instances' missions — one mission per instance.

    Identity is ``instance_id`` (§6.6 identity verdict); the list is
    deliberately UNSCOPED: every instance's mission is listed — no
    implicit non-terminal default, no leader/root filtering. Consumers
    that want a subtree filter on ``parent_mission_id`` client-side —
    that is the sanctioned pattern (§8.4; the record carries
    ``parent_mission_id`` for exactly this).

    Ordering (spec-silent choice, FLAGGED in §8.4):
    ``last_activity_at DESC NULLS LAST``, deterministic tiebreak
    ``mission_id`` (== instance_id) ASC — all in SQL, never a
    Python-side sort of the full table.

    Pagination (spec-silent choice, FLAGGED in §8.4): bounded
    limit/offset per the repo list-endpoint convention — default
    ``DEFAULT_PAGE_LIMIT`` (10), clamped to ``[1, MAX_PAGE_LIMIT]``
    (100); offset clamped to >= 0.

    Kill-switch: ``ENSEMBLE_MISSION_PROJECTION_ENABLED`` OFF ⇒ **404**
    (fail-closed — the whole surface is hidden, not field-masked; the
    spec is silent on OFF behavior for a dedicated endpoint and
    fail-closed is the task-directed choice, FLAGGED in §8.4). The
    route stays registered so OpenAPI documents it.

    Degradation (§8.2 contract — NO 500 anywhere in the projection
    path): a transient DB error on the count/page SQL leg ⇒ 200 with
    an empty page, ``total=null``, ``has_more=null``, ``degraded=true``
    and exactly one server-side warning. A transient error on the
    batched JobItem leg ⇒ rows still served with ``linked_jobs=[]``
    (the W4 sub-check falls back to the liveness-derived terminal
    reason — §8.2 indistinguishable-by-design).

    Args:
        limit: Page size (clamped to ``[1, MAX_PAGE_LIMIT]``).
        offset: Page offset (clamped to >= 0).
        liveness: Optional liveness filter (see above; unknown values
            ⇒ 400).
        agent_id: Optional exact-match agent filter (SQL).
        resolver: Injected MissionResolver (via Depends).

    Returns:
        :class:`MissionListResponse` — the page plus honesty-carrying
        pagination metadata.

    Raises:
        HTTPException: 404 when the kill-switch is OFF; 400 for an
            unknown ``liveness`` value; 503 when the resolver service
            is not wired.
    """
    if not is_mission_projection_enabled():
        # Fail-closed: the dedicated mission surface does not exist
        # while the projection kill-switch is OFF (default). 404 — not
        # 503 (the server is healthy; the feature is disabled) and not
        # an empty 200 (that would be an indistinguishable-from-real
        # empty page — the §8.2 lesson: absence must be explicit).
        raise HTTPException(
            status_code=404,
            detail={
                "error": (
                    "Mission projection is disabled "
                    f"(ENSEMBLE_MISSION_PROJECTION_ENABLED OFF — fail-closed)"
                ),
            },
        )

    liveness_values = _parse_liveness_filter(liveness)

    # Repo list-endpoint clamping convention (instances.py:421-422).
    limit = max(1, min(limit, MAX_PAGE_LIMIT))  # Clamp to 1-MAX_PAGE_LIMIT
    offset = max(0, offset)  # Ensure non-negative

    page = resolver.resolve_page(
        limit=limit,
        offset=offset,
        liveness=liveness_values,
        agent_id=agent_id,
    )

    has_more: bool | None = None
    if page.total is not None:
        has_more = (offset + limit) < page.total

    return MissionListResponse(
        missions=[_mission_record_to_response(m) for m in page.missions],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        has_more=has_more,
        degraded=page.degraded,
    )


@router.get(
    "/{mission_id}",
    response_model=MissionResponse,
    summary="Get one mission by id (identity == instance_id)",
    responses={
        404: {
            "description": (
                "Mission projection disabled (kill-switch OFF) or "
                "unknown mission id"
            )
        },
        503: {"description": "Mission resolver service not initialized"},
    },
)
async def get_mission(
    mission_id: str,
    resolver: "MissionResolver" = Depends(get_missions_resolver),
) -> MissionResponse:
    """Get one mission — full record incl. ``epoch`` + ``terminal_reason``.

    Identity is ``instance_id`` (one mission per instance, §6.6). The
    route MUST go through :meth:`MissionResolver.resolve` — the
    dead-link pre-fetch path — NEVER ``project()``: the
    ``dead_linked=False`` default there is the S4 bug class (a DEAD
    linked JobItem would surface ``failed`` instead of
    ``dead_letter``; fixed at 7852aeab and pinned at the HTTP binding
    by ``tests/unit/routers/test_missions_api.py``).

    ``epoch`` is constant 1 for every non-degraded projection until
    M4(ii) ``mission_events`` (§8.3); ``terminal_reason`` is
    W4-hazard-aware incl. ``dead_letter`` (DEAD admission overrides
    instance liveness, §8.3).

    Kill-switch: OFF ⇒ **404** (fail-closed, same rationale as the
    list route; FLAGGED in §8.4).

    Degradation (§8.2 — NO 500): a transient DB error inside
    ``resolve`` ⇒ **200** with the degraded shape (every field
    ``null``, ``linked_jobs=[]``) and one server-side warning. An
    unknown id ⇒ 404 (the only true-miss shape — distinct from the
    degraded 200).

    Args:
        mission_id: The mission id (== the instance id).
        resolver: Injected MissionResolver (via Depends).

    Returns:
        :class:`MissionResponse` — populated, or the degraded
        None-fields shape on a transient lookup failure.

    Raises:
        HTTPException: 404 when the kill-switch is OFF or the mission
            id is unknown; 503 when the resolver service is not wired.
    """
    if not is_mission_projection_enabled():
        raise HTTPException(
            status_code=404,
            detail={
                "error": (
                    "Mission projection is disabled "
                    f"(ENSEMBLE_MISSION_PROJECTION_ENABLED OFF — fail-closed)"
                ),
            },
        )

    # resolve() (dead-link pre-fetch) — NEVER project() (S4 hazard).
    record = resolver.resolve(mission_id)
    if record is None:
        # The only true-miss shape: no Instance row for this id.
        # Distinct from the degraded 200 (unknown-shape record with
        # mission_id=None) per the §8.3 null-vs-absent discipline.
        raise HTTPException(
            status_code=404,
            detail={"error": f"Mission not found: {mission_id!r}"},
        )
    return _mission_record_to_response(record)


__all__ = ["router", "set_missions_resolver", "get_missions_resolver"]
