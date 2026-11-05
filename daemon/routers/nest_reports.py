"""The ``/api/nest-reports/*`` domain route — the nest's anomaly store.

WHY A DOMAIN ROUTE (R8)
------------------------
The central metrics door (``/api/metrics/families|snapshot|series``) is
FROZEN: three series-shaped routes and no incident-row route.  The
nest-anomaly lane's ROW-shaped reads (incident clusters + member
reports) therefore get their OWN domain route — NOT a second metrics
query path.  The metrics door is untouched by this module (the AM-1
route-set pin proves it).

THE GATE — IDENTICAL TO THE METRICS DOOR (copied VERBATIM)
-----------------------------------------------------------
* ``scope_required("maintenance:read", "metrics:read")`` — the door's
  any-of QUERY set, nothing broader;
* the fail-closed ``503 AUTH_NOT_CONFIGURED`` gate as the FIRST
  statement of every handler — BEFORE any read (a repository stub
  that RAISES if touched proves the ordering);
* the route is NOT switch-gated (the door's posture).

THE PROJECTION — ONE PROJECTOR, TWO TRANSPORTS (AM-1)
------------------------------------------------------
The route calls the ANOMALY SERVICE's projectors
(``service.list_clusters`` / ``service.list_cluster_reports``) — the
SAME methods the ``nest-report-read`` tools call.  The route never
re-implements the grouping, the peak-of-peaks, the derived ``open`` or
the excerpt exclusion, so the two transports CANNOT drift.

AM-12 — THE ``excerpt`` IS OPT-IN
----------------------------------
The LIST payload carries NO ``excerpt`` key at all; the DETAIL payload
includes a member excerpt ONLY behind ``?include_excerpt=true``.

THE ERROR VOCABULARY (closed)
------------------------------
``400 bad_request`` · ``401`` (the auth middleware) · ``403
SCOPE_INSUFFICIENT`` · ``404 anomaly_cluster_not_found`` · ``503
AUTH_NOT_CONFIGURED`` · ``503 anomaly_unavailable``.  The frozen door
vocabulary is NOT widened by this route.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from daemon.auth.decorators import scope_required

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nest-reports", tags=["nest-reports"])

#: The door's any-of QUERY scope set — EXACTLY the door's
#: ``scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)`` shape
#: (``daemon/routers/metrics.py``).  Nothing broader.
_SCOPE_READ = "maintenance:read"
_SCOPE_READ_METRICS = "metrics:read"

#: The auth-OFF posture error code (the door's code, verbatim).
_ERR_AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"
#: The closed error vocabulary of THIS route.
_ERR_BAD_REQUEST = "bad_request"
_ERR_NOT_FOUND = "anomaly_cluster_not_found"
_ERR_UNAVAILABLE = "anomaly_unavailable"

#: The cursor payload's shape version (the token is opaque to callers).
_CURSOR_VERSION = 1

#: The page caps (the tool's caps — one domain, one bound).
MAX_PAGE_LIMIT = 50
DEFAULT_PAGE_LIMIT = 20
MAX_WINDOW_S = 7200
DEFAULT_WINDOW_S = 3600


def _err(status_code: int, error: str, message: str) -> HTTPException:
    """The typed ``{error, message}`` body (the door's shape, verbatim).

    Matches the metrics door's helper (``daemon/routers/metrics.py``) —
    a plain dict detail, not a Pydantic model, so the JSON response is
    byte-stable across OpenAPI rendering versions.
    """
    return HTTPException(
        status_code=status_code, detail={"error": error, "message": message}
    )


def _require_auth_on(request: Request) -> None:
    """The fail-closed posture — auth config absent ⇒ 503.

    Copied VERBATIM from the metrics door (``daemon/routers/metrics.py``):
    ``create_app`` sets ``app.state.auth_config`` ONLY when auth is
    enabled; its absence is the authoritative OFF signal.  In that
    posture the anomaly store must NOT be served — the deployed default
    is auth-OFF on a ``0.0.0.0`` bind with permissive CORS, so an open
    route here would publish the nest's incident rows to anyone who can
    reach the port.

    THE CALLER MUST INVOKE THIS AS THE FIRST STATEMENT of the handler —
    BEFORE any repository read.
    """
    cfg = getattr(getattr(request.app, "state", None), "auth_config", None)
    if cfg is None:
        raise _err(
            503,
            _ERR_AUTH_NOT_CONFIGURED,
            "Auth not configured (fail-closed)",
        )


def _anomaly_service(request: Request) -> Any:
    """The anomaly service mounted on the manager (or ``None``).

    The house convention is a private manager attribute
    (``manager._anomaly_service``); the public alias is accepted as a
    second door.  Its ABSENCE is the typed ``503 anomaly_unavailable``
    condition (an unwired deployment answers a typed 503, never a 500).
    """
    manager = getattr(getattr(request.app, "state", None), "manager", None)
    if manager is None:
        return None
    for attribute in ("_anomaly_service", "anomaly_service"):
        service = getattr(manager, attribute, None)
        if service is not None:
            return service
    return None


def _encode_cursor(payload: dict[str, Any]) -> str:
    """Encode an OPAQUE cursor (base64url of a compact JSON payload)."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(raw: str) -> dict[str, Any]:
    """Decode an opaque cursor; raise ``ValueError`` when it is invalid."""
    padded = raw.strip()
    padding = "=" * (-len(padded) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded + padding)
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError(f"undecodable cursor: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("cursor payload is not an object")
    return payload


def _filter_fingerprint(
    *, window_s: int, severity: str | None, category: str | None, limit: int
) -> str:
    """The cursor's filter fingerprint.

    A cursor resumes ONE query: resuming it under different filters
    would silently answer a different question, so a mismatch is a
    typed ``400 bad_request`` (the cursor is never silent).
    """
    return json.dumps(
        {
            "window_s": window_s,
            "severity": severity,
            "category": category,
            "limit": limit,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _cursor_offset(
    request: Request, *, fingerprint: str, retention_days: int
) -> int:
    """The resume offset from ``?cursor=`` (0 when absent).

    An INVALID cursor is an EXPECTED condition: a typed ``400
    bad_request`` — never a 500, and never a silently answered gap.
    An EXPIRED cursor (older than the retention window — the rows it
    pointed at are gone) is refused the same way.
    """
    raw = request.query_params.get("cursor")
    if raw is None or not str(raw).strip():
        return 0
    try:
        payload = _decode_cursor(str(raw))
    except ValueError as exc:
        raise _err(400, _ERR_BAD_REQUEST, f"invalid cursor: {exc}") from exc
    offset = payload.get("o", 0)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise _err(400, _ERR_BAD_REQUEST, "the cursor carries an invalid position")
    if payload.get("q") != fingerprint:
        raise _err(
            400,
            _ERR_BAD_REQUEST,
            "the cursor was issued for different filters — re-issue the "
            "query without the cursor",
        )
    instant = payload.get("t")
    if not isinstance(instant, int) or isinstance(instant, bool):
        raise _err(400, _ERR_BAD_REQUEST, "the cursor carries no evaluation instant")
    if retention_days > 0:
        oldest = datetime.now(timezone.utc) - timedelta(days=retention_days)
        if datetime.fromtimestamp(instant, tz=timezone.utc) < oldest:
            raise _err(
                400,
                _ERR_BAD_REQUEST,
                "the cursor has expired (its rows are outside the retention "
                "window) — re-issue the query without the cursor",
            )
    return offset


def _bounded_int(
    request: Request, name: str, default: int, *, low: int, high: int
) -> int:
    """Read an integer query parameter, CLAMPED to ``[low, high]``."""
    raw = request.query_params.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise _err(
            400, _ERR_BAD_REQUEST, f"{name} must be an integer"
        ) from exc
    return max(low, min(high, value))


async def _build_off_loop(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Build the response body OFF the event loop (the ``/api/work``
    precedent — the same seam the metrics door uses).
    """
    from daemon.services.db_offload import offload as _offload

    return await _offload(fn, *args, **kwargs)


def _bound_payload(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """The byte-ceiling belt (the door's shape, ported).

    The trim measures the SHIPPED envelope and the bound block says so
    — a bound that drops something is never silent.
    """
    from daemon.services.metrics_query.constants import RESPONSE_BYTE_CEILING

    try:
        encoded = len(json.dumps(payload, indent=2, default=str).encode("utf-8"))
    except (TypeError, ValueError):  # pragma: no cover — defensive
        return payload
    if encoded <= RESPONSE_BYTE_CEILING:
        return payload
    items = list(payload.get(key) or [])
    low, high, best = 0, len(items), 0
    while low <= high:
        mid = (low + high) // 2
        probe = {**payload, key: items[:mid]}
        size = len(json.dumps(probe, indent=2, default=str).encode("utf-8"))
        if size <= RESPONSE_BYTE_CEILING:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    kept = items[:best]
    payload[key] = kept
    bound = dict(payload.get("bound") or {})
    bound["returned_count"] = len(kept)
    bound["truncated"] = True
    bound["narrow_hint"] = (
        "the response was trimmed to fit the daemon's byte ceiling — narrow "
        "the query (a smaller `window_s=`, a specific `severity=` or "
        "`category=`, or a smaller `limit`) and call again."
    )
    payload["bound"] = bound
    payload["byte_ceiling_caveat"] = (
        "The response was trimmed to fit the daemon's byte ceiling — the "
        "items below are a SUBSET of what matched."
    )
    return payload


@router.get(
    "/clusters",
    dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)],
    responses={
        200: {"description": "The decision-ready anomaly GROUPS."},
        400: {"description": "A filter or the cursor is invalid."},
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {
            "description": (
                "The caller lacks the required scope (``maintenance:read`` "
                "OR ``metrics:read`` — the query routes accept either)."
            )
        },
        503: {
            "description": (
                "Auth is OFF (fail-closed) or the anomaly substrate is "
                "unavailable."
            )
        },
    },
)
async def list_nest_report_clusters(
    request: Request,
) -> Any:
    """``GET /api/nest-reports/clusters`` — the grouped incident view.

    ONE entry per ``(tree_root_instance_id, category)`` (AM-6c), with
    the group's PEAK severity, the occurrence badge (the CLUSTER
    count), the summed report count, the member cluster ids and the
    SERVER-derived ``open`` flag.  NO excerpt key appears anywhere in
    this payload (AM-12a).
    """
    _require_auth_on(request)
    service = _anomaly_service(request)
    if service is None:
        raise _err(
            503,
            _ERR_UNAVAILABLE,
            "the anomaly service is not mounted on the manager",
        )
    window_s = _bounded_int(
        request, "window_s", DEFAULT_WINDOW_S, low=1, high=MAX_WINDOW_S
    )
    limit = _bounded_int(request, "limit", DEFAULT_PAGE_LIMIT, low=1, high=MAX_PAGE_LIMIT)
    severity = request.query_params.get("severity") or None
    category = request.query_params.get("category") or None
    fingerprint = _filter_fingerprint(
        window_s=window_s, severity=severity, category=category, limit=limit
    )
    offset = _cursor_offset(
        request,
        fingerprint=fingerprint,
        retention_days=int(getattr(getattr(service, "config", None), "retention_days", 0) or 0),
    )
    try:
        payload = await service.list_clusters(
            window_s=window_s,
            severity=severity,
            category=category,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise _err(400, _ERR_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — the route never 500s the door
        logger.warning("nest-reports clusters failed: %s", exc)
        raise _err(
            503,
            _ERR_UNAVAILABLE,
            "the anomaly store could not be read",
        ) from exc
    payload = await _build_off_loop(_bound_payload, payload, "groups")
    bound = dict(payload.get("bound") or {})
    if bound.get("more_available"):
        payload["cursor"] = _encode_cursor(
            {
                "v": _CURSOR_VERSION,
                "t": int(datetime.now(timezone.utc).timestamp()),
                "o": offset + len(payload.get("groups") or []),
                "q": fingerprint,
            }
        )
    return payload


@router.get(
    "/clusters/{cluster_id}",
    dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)],
    responses={
        200: {"description": "One cluster's member reports."},
        400: {"description": "A filter or the cursor is invalid."},
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {
            "description": (
                "The caller lacks the required scope (``maintenance:read`` "
                "OR ``metrics:read`` — the query routes accept either)."
            )
        },
        404: {"description": "No such anomaly cluster."},
        503: {
            "description": (
                "Auth is OFF (fail-closed) or the anomaly substrate is "
                "unavailable."
            )
        },
    },
)
async def get_nest_report_cluster(cluster_id: str, request: Request) -> Any:
    """``GET /api/nest-reports/clusters/{cluster_id}`` — one cluster.

    The members are FRESHEST-FIRST.  A member ``excerpt`` appears ONLY
    behind ``?include_excerpt=true`` (AM-12b).
    """
    _require_auth_on(request)
    service = _anomaly_service(request)
    if service is None:
        raise _err(
            503,
            _ERR_UNAVAILABLE,
            "the anomaly service is not mounted on the manager",
        )
    limit = _bounded_int(request, "limit", DEFAULT_PAGE_LIMIT, low=1, high=MAX_PAGE_LIMIT)
    include_excerpt = str(request.query_params.get("include_excerpt") or "").lower() in {
        "1",
        "true",
        "yes",
    }
    try:
        payload = await service.list_cluster_reports(
            cluster_id=cluster_id,
            limit=limit,
            include_excerpt=include_excerpt,
        )
    except ValueError as exc:
        raise _err(400, _ERR_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — the route never 500s the door
        logger.warning("nest-reports cluster detail failed: %s", exc)
        raise _err(
            503,
            _ERR_UNAVAILABLE,
            "the anomaly store could not be read",
        ) from exc
    if payload is None:
        raise _err(
            404,
            _ERR_NOT_FOUND,
            f"no anomaly cluster with id {cluster_id!r}",
        )
    payload = await _build_off_loop(_bound_payload, payload, "reports")
    return payload


__all__ = ["router"]
