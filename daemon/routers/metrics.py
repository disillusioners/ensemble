"""Metrics router — the daemon's internal metric surface.

``GET /api/metrics`` renders
:func:`daemon.checkpoint_metrics.render_metrics` as Prometheus
text-exposition (the format reference:
https://prometheus.io/docs/instrumenting/exposition_formats/).

WHY THIS ROUTER EXISTS: the metric registry already existed
(``daemon/checkpoint_metrics.py``) with a renderer, but the renderer
had NO HTTP surface — its own docstring called the endpoint "future
work". The checkpoint read-path commission (plan Track-1 item 4) needs
the read instrumentation (``checkpoint_read_*`` counters + the
``checkpoint_read_size_bytes`` histogram) to be OBSERVABLE from outside
the process, and the read-path PR-2 gate needs a scrapeable baseline.

SCOPE GUARD (the exposition route) — this is the MINIMAL wiring,
deliberately:

* ONE endpoint, calling ONE existing function;
* NO Prometheus client dependency, NO scrape framework, NO new metric
  registry, NO per-metric filtering;
* the response is the registry's full text exposition, exactly as
:func:`render_metrics` produces it.

THE QUERY DOOR (the three JSON routes) — a SEPARATE half of this
module:

* ``GET /api/metrics/families``, ``GET /api/metrics/snapshot`` and
  ``GET /api/metrics/series`` are the query door.  They are the
  READ side of the same registry, served from the query package
  (:mod:`daemon.services.metrics_query`) behind the SAME
  ``maintenance:read`` OR ``metrics:read`` scope (the any-of set minted
  by the metrics-agent-tools lane) and the scope declaration rides the
  route's ``dependencies=[scope_required(...)]`` form exactly as the
  exposition route's does;
* they are BOUNDED: the ``offset``/``limit`` pair pages the body, the
  byte ceiling bounds the page, and a bounded body carries the opaque
  ``next_cursor`` that resumes it — a bounded body is never a SILENT
  truncation;
* the cursor is OPAQUE and pins the evaluation instant; an invalid or
  EXPIRED cursor is an EXPECTED condition (``400 invalid_cursor``),
  never a 500 and never a silently answered gap;
* the body is built OFF the event loop (the off-loop seam) and the
  cursor-expiry probe runs inside that off-loop build — the event loop
  never pays for the registry read or the body encode.

The published contract (the wire vocabulary, the bounds and the error
codes) lives in ``docs/architecture/metrics-query-contract.md`` — this
docstring states the SHAPE, the doc states the CONTRACT.

AUTH POSTURE — the metric registry carries INTERNAL counters, so the
route is deliberately NOT world-readable:

* **SCOPE** — the TEXT route carries the ``maintenance:read`` scope (the
  EXISTING taxonomy entry for a maintenance/diagnostic read — the same
  scope the ``/api/wedge-captures`` family carries). The three QUERY
  routes carry the ANY-OF set ``maintenance:read`` OR ``metrics:read``
  (the second taxonomy entry was minted by the metrics-agent-tools lane
  so a machine lane can read the query door WITHOUT the destructive
  checkpoint-cleanup capability that ``maintenance:read`` also confers).
  The declaration rides the route's
  ``dependencies=[scope_required(...)]`` form, so the handler signature
  is unchanged by the scope check.
* **AUTH-ON** — the route carries NO class tag, so it is protected by
  default (an untagged route is protected; see
  ``daemon/auth/route_meta.py``). The middleware 401s an
  unauthenticated caller, and the scope check 403s an authenticated
  caller WITHOUT a sufficient scope (``SCOPE_INSUFFICIENT``).
* **AUTH-OFF** — the route REFUSES: the inline
  :func:`_require_auth_on` gate returns ``503 AUTH_NOT_CONFIGURED``
  (the D3 fail-closed contract of the prompts family, mirrored
  exactly). The deployed posture is auth-OFF + ``0.0.0.0`` bind +
  permissive CORS, so serving the internal counters in that posture
  would publish them to anyone who can reach the port. The gate runs
  BEFORE the registry is rendered — a refusal never renders a metric.

The gate is INLINE (a handler-body call, not a route dependency) so it
runs AFTER the middleware's authentication decision and BEFORE any
metric is read — the same shape the prompts family uses.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from daemon.auth.decorators import scope_required
from daemon.checkpoint_metrics import render_metrics

router = APIRouter(prefix="/metrics", tags=["metrics"])

#: The Prometheus text-exposition media type (the format's own
#: declared content type — a scraper keys off the ``version=0.0.4``
#: parameter, not off ``application/json``).
METRICS_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: The scope the TEXT-exposition route carries — the EXISTING taxonomy
#: entry for a maintenance/diagnostic read (deliberate scope REUSE, the
#: same convention the ``/api/wedge-captures`` family follows).  The
#: THREE QUERY routes accept this scope OR ``metrics:read`` (the
#: any-of set minted by the metrics-agent-tools lane).
_SCOPE_READ = "maintenance:read"

#: The SECOND scope of the query door's any-of set (the
#: metrics-agent-tools lane, 2026-11-03).  ``maintenance:read`` also
#: confers the destructive checkpoint-cleanup capability (AM-20), so a
#: machine lane that only needs to READ the query door carries this
#: scope instead.  The three QUERY routes accept EITHER scope; the
#: text-exposition route keeps ``maintenance:read`` ALONE (D6 step 3 —
#: Prometheus text exposition is the scraping surface).
_SCOPE_READ_METRICS = "metrics:read"

#: The auth-OFF posture error code (D3 fail-closed). The prompts family
#: (``daemon/routers/prompts.py``) and the custom-agents family use
#: the SAME code for the SAME posture — the body shape is stable across
#: the daemon.
_ERR_AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"


def _err(status_code: int, error: str, message: str) -> HTTPException:
    """Return an HTTPException carrying the typed ``{error, message}`` body.

    Matches the prompts-family shape (``daemon/routers/prompts.py``) —
    a plain dict detail, not a Pydantic model, so the JSON response is
    byte-stable across OpenAPI rendering versions.
    """
    return HTTPException(
        status_code=status_code, detail={"error": error, "message": message}
    )


def _require_auth_on(request: Request) -> None:
    """The D3 fail-closed posture — auth_config absent ⇒ 503.

    ``create_app`` sets ``app.state.auth_config`` ONLY when auth is
    enabled; its absence is the authoritative OFF signal. In that
    posture the metric registry (internal counters) must NOT be served
    — the deployed default is auth-OFF on a ``0.0.0.0`` bind with
    permissive CORS, so an open route here would publish the daemon's
    internals to anyone who can reach the port.
    """
    cfg = getattr(getattr(request.app, "state", None), "auth_config", None)
    if cfg is None:
        raise _err(
            503,
            _ERR_AUTH_NOT_CONFIGURED,
            "Auth not configured (fail-closed)",
        )


@router.get(
    "",
    response_class=PlainTextResponse,
    dependencies=[scope_required(_SCOPE_READ)],
    responses={
        200: {
            "description": (
                "The daemon's metric registry in Prometheus "
                "text-exposition format."
            ),
            "content": {METRICS_MEDIA_TYPE: {"example": "# TYPE ..."}},
        },
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {
            "description": (
                "The caller lacks the ``maintenance:read`` scope (the "
                "text-exposition route carries that scope ALONE — it is the "
                "scraping surface, not the query door)."
            )
        },
        503: {"description": "Auth is OFF — the route refuses (fail-closed)."},
    },
)
async def get_metrics(request: Request) -> PlainTextResponse:
    """Render the daemon's metric registry as Prometheus text-exposition.

    The body is exactly :func:`daemon.checkpoint_metrics.render_metrics`
    — no filtering, no reshaping.

    Raises:
        HTTPException: ``503 AUTH_NOT_CONFIGURED`` when auth is OFF
            (the fail-closed gate fires BEFORE the registry is read).
    """
    _require_auth_on(request)
    return PlainTextResponse(content=render_metrics(), media_type=METRICS_MEDIA_TYPE)


# ── the query door (central-metrics-v1, phase 3) ───────────────────────────
#
# ONE door, THREE routes, ONE provider (decisions.md D4 — Option A):
#
# * ``GET /api/metrics/families`` — the family catalog AS SERVED;
# * ``GET /api/metrics/snapshot`` — the current published values;
# * ``GET /api/metrics/series``   — the windowed trend.
#
# The routes EXTEND the existing family — no new router module, no new
# mount; the query routes' scope is the any-of set
# ``maintenance:read`` OR ``metrics:read``.
#
# POSTURE (the contract's truth table — docs/architecture/
# metrics-query-contract.md §6):
#
# * the SAME fail-closed ``_require_auth_on`` seam runs BEFORE any
#   provider read: an auth-OFF deployment answers 503
#   AUTH_NOT_CONFIGURED and never reads the registry;
# * the door is NEVER switch-gated (AM-17/AM-18).  The kill-switch's
#   emergency-off lever for THIS surface is TOKEN REVOCATION of the
#   the query routes' grant (``maintenance:read`` / ``metrics:read``)
#   — not the switch.  The query package
#   never reads the switch (the ``census_gated`` reason comes from the
#   public ``CensusSampler.gated`` property);
# * the response BODY is BUILT off the event loop (``db_offload.offload``
#   — the ``/api/work`` precedent).  AM-12 is BUILD-OFF-LOOP ONLY: the
#   offload returns a Python object and FastAPI still ENCODES it on the
#   loop, so the residual encode is BOUNDED and PINNED — ``max_points``
#   ≤ 240 × the selector ceiling ≤ 50 ⇒ ≤ 12 000 points ≈ 3.6 ms.

#: The error vocabulary of the query door (the contract's closed set).
_ERR_UNKNOWN_FAMILY = "unknown_family"
_ERR_INVALID_LABEL_FILTER = "invalid_label_filter"
_ERR_INVALID_CURSOR = "invalid_cursor"
_ERR_REGISTRY_UNAVAILABLE = "registry_unavailable"

#: The cursor payload's shape version (the token is opaque to callers;
#: the version lets the daemon evolve the payload without confusing an
#: old token with a new one).
_CURSOR_VERSION = 1


class _UnknownFamily(LookupError):
    """A requested ``?family=`` is not registered (→ 404 unknown_family)."""


class _ExpiredCursor(ValueError):
    """A cursor that can no longer be resumed (→ 400 invalid_cursor)."""


def _query_provider(request: Request) -> Any:
    """The provider the door serves from (``app.state.metrics_query``).

    Its ABSENCE is the "registry unavailable" condition (503) — the
    lifespan stores it there at boot, so an unwired deployment answers
    a typed 503 instead of a 500.
    """
    provider = getattr(getattr(request.app, "state", None), "metrics_query", None)
    if provider is None:
        raise _err(
            503,
            _ERR_REGISTRY_UNAVAILABLE,
            "The metrics query provider is not available",
        )
    return provider


def _oldest_sample_timestamp(provider: Any) -> int | None:
    """The provider's OLDEST buffered sample (``None`` when unknown)."""
    getter = getattr(provider, "oldest_sample_timestamp", None)
    if getter is None:
        return None
    try:
        value = getter()
    except Exception:  # pragma: no cover — a broken provider never 500s the door
        return None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):  # pragma: no cover
        return None


def _cursor_state(request: Request) -> dict[str, Any]:
    """Decode + shape-validate ``?cursor=`` into the resume state.

    An INVALID cursor is an EXPECTED condition: it is a typed
    ``400 invalid_cursor`` — never a 500, and never a silently answered
    gap.

    NO PROVIDER READ happens here: the EXPIRY probe needs the ring's
    oldest sample, so it runs INSIDE the off-loop body build — never on
    the event loop.
    """
    raw = request.query_params.get("cursor")
    if raw is None or not str(raw).strip():
        return {"offset": 0, "instant": None, "present": False}
    from daemon.services.metrics_query.envelope import (
        InvalidCursor,
        decode_cursor,
    )

    try:
        cursor = decode_cursor(str(raw))
    except InvalidCursor as exc:
        raise _err(400, _ERR_INVALID_CURSOR, str(exc)) from exc
    instant = cursor.get("t")
    if not isinstance(instant, int) or isinstance(instant, bool):
        raise _err(
            400, _ERR_INVALID_CURSOR, "The cursor carries no evaluation instant"
        )
    offset = cursor.get("o", 0)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise _err(400, _ERR_INVALID_CURSOR, "The cursor carries an invalid position")
    return {"offset": offset, "instant": instant, "present": True}


def _bounded_int_param(
    request: Request, name: str, default: int, *, low: int, high: int
) -> int:
    """Read an integer query parameter, CLAMPED to ``[low, high]``.

    A malformed value is a typed ``400 invalid_label_filter`` — never a
    500 and never a silently ignored parameter.
    """
    raw = request.query_params.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise _err(
            400, _ERR_INVALID_LABEL_FILTER, f"{name} must be an integer"
        ) from exc
    return max(low, min(value, high))


def _expire_cursor(provider: Any, cursor: dict[str, Any]) -> None:
    """The cursor's EXPIRY probe — runs OFF the event loop.

    A cursor whose pinned evaluation instant predates the buffer's
    oldest sample can no longer be resumed faithfully, so it is refused
    (``400 invalid_cursor``) rather than silently answered with a gap.
    """
    instant = cursor.get("instant")
    if instant is None:
        return
    oldest = _oldest_sample_timestamp(provider)
    if oldest is not None and int(instant) < oldest:
        raise _ExpiredCursor("The cursor has expired")


def _encoded_size(payload: Any) -> int:
    """The encoded byte size of a payload (the byte-ceiling probe)."""
    try:
        return len(
            json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        )
    except (TypeError, ValueError):  # pragma: no cover — defensive
        return 0


def _trim_to_ceiling(envelope: dict[str, Any], *, key: str, items: list[Any]) -> list[Any]:
    """Trim ``items`` until the encoded body fits the byte ceiling.

    The trim is a BOUND, never a silent truncation: the caller mints
    the cursor that resumes exactly where the trim stopped.
    """
    from daemon.services.metrics_query.constants import RESPONSE_BYTE_CEILING

    low, high, best = 0, len(items), 0
    while low <= high:
        mid = (low + high) // 2
        if _encoded_size({**envelope, key: items[:mid]}) <= RESPONSE_BYTE_CEILING:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return items[:best]


def _bound_page(
    envelope: dict[str, Any],
    *,
    key: str,
    offset: int,
    instant: int,
    more: bool,
) -> dict[str, Any]:
    """Apply the byte ceiling and mint the continuation cursor.

    A bounded body is never SILENTLY truncated: it carries the opaque
    ``next_cursor`` that resumes it (AM-6).

    THE CONTINUATION SIGNAL IS COMPUTED BEFORE THE BODY FLAG MUTATES
    ------------------------------------------------------------------
    There are TWO different facts in this function and they must never
    be conflated (ND-2):

    * the CONTINUATION signal — "is there MORE to fetch?" — is the
      BUILDER's own flag (``more`` / the envelope's ``truncated`` as the
      builder set it) plus the byte-ceiling TRIM.  Only these may mint
      a cursor.
    * the DISPLAY flag — ``offset > 0`` means "this page is a partial
      view of a larger set".  It is a true statement about the BODY and
      it belongs in the body's ``truncated`` field, but it says NOTHING
      about whether another page exists.

    OR-ing the display flag into the cursor minting (the delta-3 bug)
    broke the walk on every route: the LAST non-empty page minted a
    cursor, the client followed it into an EMPTY page, and the empty
    page re-minted the byte-identical cursor (``o=`` unchanged) — an
    endless walk.  Pre-delta the chains TERMINATED.

    THE BYTE-CEILING PROBE AND THE TRIM RUN INSIDE THE OFF-LOOP BUILD
    ------------------------------------------------------------------
    The probe is a FULL JSON dump of the body and the trim is a binary
    search over it (worst case ~9 dumps of ~1 MiB ⇒ 20-30 ms).  Both
    run inside the page builders — the off-loop body — never on the
    event loop (W-3).
    """
    from daemon.services.metrics_query.constants import RESPONSE_BYTE_CEILING
    from daemon.services.metrics_query.envelope import encode_cursor

    items = list(envelope.get(key) or [])
    # THE CONTINUATION SIGNAL — captured FIRST, before the display flag
    # mutates the envelope.  `more` is the route's restatement of the
    # builder's own "more remain" flag.
    builder_more = bool(envelope.get("truncated")) or bool(more)
    truncated = builder_more
    if offset > 0:
        # The DISPLAY fact only: this page is a partial view.  It must
        # never feed cursor minting (ND-2).
        truncated = True
    kept = items
    if _encoded_size(envelope) > RESPONSE_BYTE_CEILING:
        kept = _trim_to_ceiling(envelope, key=key, items=items)
    trimmed = len(kept) < len(items)
    if trimmed:
        truncated = True
    # The cursor is minted ONLY for a real continuation: the builder
    # says more remain, or the byte-ceiling trim shortened the page.
    continuation = builder_more or trimmed
    envelope[key] = kept
    envelope["truncated"] = truncated
    if continuation:
        envelope["next_cursor"] = encode_cursor(
            {"v": _CURSOR_VERSION, "t": int(instant), "o": offset + len(kept)}
        )
    else:
        envelope.pop("next_cursor", None)
    return envelope


def _selectors(request: Request, *keys: str) -> list[str]:
    """The raw selector values of the given query parameters.

    Both the REPEATED form (``?names=a&names=b``) and the COMMA-SEPARATED
    form (``?names=a,b``) are accepted — the comma form is what a browser
    address bar produces.
    """
    out: list[str] = []
    for key in keys:
        for raw in request.query_params.getlist(key):
            for piece in str(raw).split(","):
                piece = piece.strip()
                if piece:
                    out.append(piece)
    return out


async def _build_off_loop(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Build the response body OFF the event loop (AM-12).

    The offload is the ``/api/work`` precedent (``db_offload.offload``).
    The claim is BUILD-OFF-LOOP ONLY — the offload returns a Python
    object and FastAPI still encodes it on the loop; the residual is
    bounded by the ``max_points``/selector ceilings.
    """
    from daemon.services.db_offload import offload as _offload

    return await _offload(fn, *args, **kwargs)


def _build_families_page(
    provider: Any, *, offset: int, instant: int, cursor: dict[str, Any]
) -> dict[str, Any]:
    """The ``/families`` page — built AND bounded off the loop.

    The byte-ceiling probe and the trim live here (W-3): they are the
    expensive part of the body build and they must never run on the
    event loop.
    """
    from daemon.services.metrics_query.envelope import build_families_envelope

    _expire_cursor(provider, cursor)
    envelope = build_families_envelope(provider, offset=offset)
    return _bound_page(
        envelope,
        key="families",
        offset=offset,
        instant=instant,
        more=bool(envelope.get("truncated")),
    )


def _build_snapshot_page(
    provider: Any,
    *,
    family: str | None,
    name_prefix: str | None,
    offset: int,
    limit: int,
    instant: int,
    cursor: dict[str, Any],
) -> dict[str, Any]:
    """The ``/snapshot`` page — built AND bounded off the loop.

    An UNKNOWN ``?family=`` is a typed 404 (the contract's error
    vocabulary) — never an empty 200 that reads like "no observations".

    The route is BOUNDED like the other two (W-4): ``offset``/``limit``
    page the series and the byte ceiling bounds the page — a bounded
    body carries the continuation cursor, never a silent truncation.

    THE EXPIRY PROBE RUNS HERE TOO (ND-4): this builder was the only
    one of the three that did NOT call ``_expire_cursor``, so a stale
    snapshot cursor never expired — the route's ``except _ExpiredCursor
    → 400 invalid_cursor`` was DEAD on this route.  The probe is the
    same O(rings) read the sibling builders make (and it runs OFF the
    event loop, inside this off-loop build).
    """
    from daemon.services.metrics_query.envelope import build_snapshot_envelope

    _expire_cursor(provider, cursor)
    if family is not None:
        known = {entry.get("id") for entry in provider.families()}
        if family not in known:
            raise _UnknownFamily(family)
    envelope = build_snapshot_envelope(
        provider,
        family=family,
        name_prefix=name_prefix,
        offset=offset,
        limit=limit,
    )
    return _bound_page(
        envelope,
        key="series",
        offset=offset,
        instant=instant,
        more=bool(envelope.get("truncated")),
    )


def _build_series_page(
    provider: Any,
    *,
    names: list[str] | None,
    series: list[str] | None,
    window_s: int | None,
    max_points: int,
    instant: int,
    offset: int,
    cursor: dict[str, Any],
) -> dict[str, Any]:
    """The ``/series`` page — built AND bounded off the loop."""
    from daemon.services.metrics_query.envelope import build_series_envelope

    _expire_cursor(provider, cursor)
    envelope = build_series_envelope(
        provider,
        names=names,
        series=series,
        window_s=window_s,
        max_points=max_points,
        now=instant,
        offset=offset,
    )
    return _bound_page(
        envelope,
        key="series",
        offset=offset,
        instant=instant,
        more=bool(envelope.get("truncated")),
    )


@router.get(
    "/families",
    dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)],
    responses={
        200: {"description": "The registered metric families, as served."},
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {
            "description": (
                "The caller lacks the required scope (``maintenance:read`` "
                "OR ``metrics:read`` — the query routes accept either)."
            )
        },
        503: {
            "description": (
                "Auth is OFF (fail-closed) or the query provider is "
                "unavailable."
            )
        },
    },
)
async def get_metrics_families(request: Request) -> dict[str, Any]:
    """``GET /api/metrics/families`` — the family catalog AS SERVED.

    The catalog is DERIVED FROM THE REGISTRY (the registered objects +
    their registration-time family metadata) — there is no second family
    table, so a newly wired family is discoverable without a door edit.

    Raises:
        HTTPException: ``503 AUTH_NOT_CONFIGURED`` when auth is OFF
            (the fail-closed gate fires BEFORE any provider read);
            ``503 registry_unavailable`` when the provider is not wired.
    """
    _require_auth_on(request)
    provider = _query_provider(request)
    cursor = _cursor_state(request)
    offset = cursor["offset"]
    instant = cursor["instant"] if cursor["instant"] is not None else int(time.time())
    try:
        return await _build_off_loop(
            _build_families_page,
            provider,
            offset=offset,
            instant=instant,
            cursor=cursor,
        )
    except _ExpiredCursor as exc:
        raise _err(400, _ERR_INVALID_CURSOR, str(exc)) from exc


@router.get(
    "/snapshot",
    dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)],
    responses={
        200: {"description": "The current published values."},
        400: {"description": "The requested filters are invalid."},
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {
            "description": (
                "The caller lacks the required scope (``maintenance:read`` "
                "OR ``metrics:read`` — the query routes accept either)."
            )
        },
        404: {"description": "The requested family is not registered."},
        503: {
            "description": (
                "Auth is OFF (fail-closed) or the query provider is "
                "unavailable."
            )
        },
    },
)
async def get_metrics_snapshot(
    request: Request,
) -> dict[str, Any]:
    """``GET /api/metrics/snapshot`` — the current published values.

    Staleness is EXPLICIT metadata (``last_publish_age_s``), never a
    silently stale number.

    Raises:
        HTTPException: ``503 AUTH_NOT_CONFIGURED`` (auth OFF — before any
            provider read); ``404 unknown_family`` for an unregistered
            ``?family=``; ``503 registry_unavailable`` when unwired.
    """
    _require_auth_on(request)
    provider = _query_provider(request)
    family = request.query_params.get("family") or None
    name_prefix = request.query_params.get("name_prefix") or None
    from daemon.services.metrics_query.constants import MAX_SNAPSHOT_SERIES

    cursor = _cursor_state(request)
    # The offset comes from the CURSOR when one is supplied (the cursor
    # pins the position); otherwise from ``?offset=``.  The page size
    # comes from ``?limit=`` and is CLAMPED to the ceiling (W-4).
    offset = (
        cursor["offset"]
        if cursor["present"]
        else _bounded_int_param(request, "offset", 0, low=0, high=10**9)
    )
    limit = _bounded_int_param(
        request, "limit", MAX_SNAPSHOT_SERIES, low=1, high=MAX_SNAPSHOT_SERIES
    )
    instant = cursor["instant"] if cursor["instant"] is not None else int(time.time())
    try:
        return await _build_off_loop(
            _build_snapshot_page,
            provider,
            family=family,
            name_prefix=name_prefix,
            offset=offset,
            limit=limit,
            instant=instant,
            cursor=cursor,
        )
    except _UnknownFamily as exc:
        raise _err(
            404, _ERR_UNKNOWN_FAMILY, f"No such metric family: {exc.args[0]}"
        ) from exc
    except _ExpiredCursor as exc:
        raise _err(400, _ERR_INVALID_CURSOR, str(exc)) from exc


@router.get(
    "/series",
    dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)],
    responses={
        200: {"description": "The windowed trend series."},
        400: {"description": "The selector or the cursor is invalid."},
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {
            "description": (
                "The caller lacks the required scope (``maintenance:read`` "
                "OR ``metrics:read`` — the query routes accept either)."
            )
        },
        503: {
            "description": (
                "Auth is OFF (fail-closed) or the query provider is "
                "unavailable."
            )
        },
    },
)
async def get_metrics_series(request: Request) -> dict[str, Any]:
    """``GET /api/metrics/series`` — the windowed trend.

    ``?series=`` is the LABEL-AWARE selector (``<name>{<k>=<v>,…}``) and
    ``?names=`` is NAME-level (every label variant).  ``?max_points=`` is
    the server-side bucket-max downsample; it is CLAMPED to the
    ``max_points`` ceiling so the residual on-loop encode stays bounded.

    Raises:
        HTTPException: ``503 AUTH_NOT_CONFIGURED`` (auth OFF — before any
            provider read); ``400 invalid_label_filter`` for a malformed
            or over-ceiling selector; ``400 invalid_cursor`` for an
            invalid or expired cursor; ``503 registry_unavailable`` when
            unwired.
    """
    _require_auth_on(request)
    provider = _query_provider(request)
    from daemon.services.metrics_query.constants import (
        DEFAULT_MAX_POINTS,
        MAX_NAMES,
        MAX_POINTS_CEILING,
        WINDOW_SECONDS,
    )
    from daemon.services.metrics_query.selectors import (
        InvalidLabelFilter,
        parse_series_selector,
    )

    names = _selectors(request, "names")
    series = _selectors(request, "series")
    # The selector ceiling bounds the residual on-loop encode
    # (max_points × selectors ≤ 240 × 50 = 12 000 points ≈ 3.6 ms).
    # An over-ceiling request is REJECTED, never silently truncated.
    if len(set(names)) + len(series) > MAX_NAMES:
        raise _err(
            400,
            _ERR_INVALID_LABEL_FILTER,
            f"at most {MAX_NAMES} selectors may be requested",
        )
    for selector in series:
        try:
            parse_series_selector(selector)
        except InvalidLabelFilter as exc:
            raise _err(400, _ERR_INVALID_LABEL_FILTER, str(exc)) from exc

    window_raw = request.query_params.get("window_s")
    window_s: int | None = None
    if window_raw is not None and str(window_raw).strip():
        try:
            window_s = int(str(window_raw).strip())
        except ValueError as exc:
            raise _err(
                400, _ERR_INVALID_LABEL_FILTER, "window_s must be an integer"
            ) from exc
        window_s = max(1, min(window_s, WINDOW_SECONDS))

    max_raw = request.query_params.get("max_points")
    max_points = DEFAULT_MAX_POINTS
    if max_raw is not None and str(max_raw).strip():
        try:
            requested = int(str(max_raw).strip())
        except ValueError as exc:
            raise _err(
                400, _ERR_INVALID_LABEL_FILTER, "max_points must be an integer"
            ) from exc
        if requested < 1:
            raise _err(
                400, _ERR_INVALID_LABEL_FILTER, "max_points must be positive"
            )
        # CLAMPED, never honoured above the ceiling — the encode bound
        # is the daemon's own budget, not the caller's to widen.
        max_points = min(requested, MAX_POINTS_CEILING)

    cursor = _cursor_state(request)
    offset = cursor["offset"]
    instant = cursor["instant"] if cursor["instant"] is not None else int(time.time())
    try:
        return await _build_off_loop(
            _build_series_page,
            provider,
            names=names or None,
            series=series or None,
            window_s=window_s,
            max_points=max_points,
            instant=instant,
            offset=offset,
            cursor=cursor,
        )
    except _ExpiredCursor as exc:
        raise _err(400, _ERR_INVALID_CURSOR, str(exc)) from exc
    except InvalidLabelFilter as exc:
        raise _err(400, _ERR_INVALID_LABEL_FILTER, str(exc)) from exc


__all__ = ["METRICS_MEDIA_TYPE", "router"]
