"""The ``/api/tool-usage/*`` read-only domain door (Phase 1).

Phase 4's FE tab reads the aggregated ledger through this door. The auth
posture is replicated VERBATIM from the metrics door (``metrics.py``) —
the fail-closed ``_require_auth_on`` body byte-matches, the any-of scope
set is the same, and DB errors become a typed 503 with redacted
server-side logging. There is no shared helper (helper extraction is out
of phase-1 scope); copy-paste across the doors is the deliberate contract
so the doors cannot drift independently.

SCOPING (frozen): ``project_id`` is a FILTER, not a boundary — under the
single-operator / single-tenant threat model any holder of the read
scopes sees all projects. ``project_id`` omitted = all rows INCLUDING the
``__none__`` bucket (total truth); explicit ``project_id=__none__`` filters
to the un-attributable bucket.

ABUSE BOUNDS: no pagination in v1; a 1 MB serialized-response guard; the
nested per-agent list is capped at top-50 per tool (repository-side).

CLOSED ERROR VOCABULARY: ``AUTH_NOT_CONFIGURED`` (503),
``SCOPE_INSUFFICIENT`` (403, from the scope decorator), ``bad_request``
(400), ``unknown_tool`` (404), ``tool_usage_unavailable`` (503).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from daemon._redact import redact_exc_str
from daemon.auth.decorators import scope_required

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tool-usage", tags=["tool-usage"])

_SCOPE_READ = "maintenance:read"
_SCOPE_READ_METRICS = "metrics:read"

_ERR_AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"
_ERR_BAD_REQUEST = "bad_request"
_ERR_UNKNOWN_TOOL = "unknown_tool"
_ERR_UNAVAILABLE = "tool_usage_unavailable"

#: The 1 MB serialized-response guard (D21 pin 3).
MAX_RESPONSE_BYTES = 1_000_000


def _err(status_code: int, error: str, message: str) -> HTTPException:
    """Typed ``{error, message}`` body — the metrics door's shape, verbatim."""
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


def _repository(request: Request) -> Any | None:
    """The ledger repository mounted on the manager (or ``None``)."""
    manager = getattr(getattr(request.app, "state", None), "manager", None)
    if manager is None:
        return None
    return getattr(manager, "_tool_usage_repository", None)


def _size_guard(payload: dict[str, Any]) -> dict[str, Any]:
    """Reject an oversized serialized response (the 1 MB abuse bound)."""
    encoded = json.dumps(payload, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise _err(
            503,
            _ERR_UNAVAILABLE,
            "tool-usage response exceeded the size bound",
        )
    return payload


@router.get(
    "/summary",
    dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)],
    responses={
        200: {"description": "Per-tool aggregated usage rows."},
        400: {"description": "A query parameter is invalid."},
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {"description": "The caller lacks a required read scope."},
        503: {"description": "Auth is OFF (fail-closed) or the ledger is unavailable."},
    },
)
async def get_tool_usage_summary(request: Request) -> Any:
    """``GET /api/tool-usage/summary`` — per-tool aggregate (FE sort client-side)."""
    _require_auth_on(request)
    repo = _repository(request)
    if repo is None:
        raise _err(
            503,
            _ERR_UNAVAILABLE,
            "the tool-usage repository is not mounted on the manager",
        )
    days = _bounded_int(request, "days", 7, low=1, high=90)
    project_id = request.query_params.get("project_id") or None
    try:
        import asyncio

        rows = await asyncio.to_thread(repo.query_summary, project_id, days)
    except Exception as exc:  # noqa: BLE001 — the door never 500s
        logger.warning(
            "tool-usage summary failed: %s", redact_exc_str(exc, limit=200)
        )
        raise _err(
            503,
            _ERR_UNAVAILABLE,
            "the tool-usage ledger could not be read",
        ) from exc
    # tool_category is resolved from the frozen registry vocabulary at
    # door time (the ledger stores NAMES ONLY).
    from daemon.services.tool_usage_observation import resolve_tool_category

    for row in rows:
        row["tool_category"] = resolve_tool_category(row.get("tool_name", ""))
    return _size_guard(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": rows,
        }
    )


@router.get(
    "/daily",
    dependencies=[scope_required(_SCOPE_READ, _SCOPE_READ_METRICS)],
    responses={
        200: {"description": "A zero-filled per-UTC-day call series for one tool."},
        400: {"description": "A query parameter is invalid."},
        401: {"description": "Auth is ON and the caller is unauthenticated."},
        403: {"description": "The caller lacks a required read scope."},
        404: {"description": "The tool has no ledger rows at all."},
        503: {"description": "Auth is OFF (fail-closed) or the ledger is unavailable."},
    },
)
async def get_tool_usage_daily(request: Request) -> Any:
    """``GET /api/tool-usage/daily`` — one zero-filled point per UTC day."""
    _require_auth_on(request)
    repo = _repository(request)
    if repo is None:
        raise _err(
            503,
            _ERR_UNAVAILABLE,
            "the tool-usage repository is not mounted on the manager",
        )
    tool_name = request.query_params.get("tool_name")
    if not tool_name or not tool_name.strip():
        raise _err(400, _ERR_BAD_REQUEST, "tool_name is required")
    days = _bounded_int(request, "days", 30, low=1, high=90)
    project_id = request.query_params.get("project_id") or None
    try:
        import asyncio

        if not await asyncio.to_thread(repo.has_tool, tool_name):
            raise _err(404, _ERR_UNKNOWN_TOOL, f"unknown tool: {tool_name}")
        series = await asyncio.to_thread(
            repo.query_daily, project_id, tool_name, days
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — the door never 500s
        logger.warning(
            "tool-usage daily failed: %s", redact_exc_str(exc, limit=200)
        )
        raise _err(
            503,
            _ERR_UNAVAILABLE,
            "the tool-usage ledger could not be read",
        ) from exc
    return _size_guard({"series": series})


__all__ = ["router", "MAX_RESPONSE_BYTES"]
