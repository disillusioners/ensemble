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

SCOPE GUARD — this is the MINIMAL wiring, deliberately:

* ONE endpoint, calling ONE existing function;
* NO Prometheus client dependency, NO scrape framework, NO new metric
  registry, NO per-metric filtering;
* the response is the registry's full text exposition, exactly as
:func:`render_metrics` produces it.

AUTH POSTURE — the metric registry carries INTERNAL counters, so the
route is deliberately NOT world-readable:

* **SCOPE** — the route carries the ``maintenance:read`` scope (the
  EXISTING taxonomy entry for a maintenance/diagnostic read — the same
  scope the ``/api/wedge-captures`` family carries; no new scope is
  minted). The declaration rides the route's
  ``dependencies=[scope_required(...)]`` form, so the handler signature
  is unchanged by the scope check.
* **AUTH-ON** — the route carries NO class tag, so it is protected by
  default (an untagged route is protected; see
  ``daemon/auth/route_meta.py``). The middleware 401s an
  unauthenticated caller, and the scope check 403s an authenticated
  caller WITHOUT ``maintenance:read`` (``SCOPE_INSUFFICIENT``).
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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from daemon.auth.decorators import scope_required
from daemon.checkpoint_metrics import render_metrics

router = APIRouter(prefix="/metrics", tags=["metrics"])

#: The Prometheus text-exposition media type (the format's own
#: declared content type — a scraper keys off the ``version=0.0.4``
#: parameter, not off ``application/json``).
METRICS_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: The scope the family carries — the EXISTING taxonomy entry for a
#: maintenance/diagnostic read (deliberate scope REUSE, the same
#: convention the ``/api/wedge-captures`` family follows; NO new scope
#: is minted for the metrics surface).
_SCOPE_READ = "maintenance:read"

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
        403: {"description": "The caller lacks the ``maintenance:read`` scope."},
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


__all__ = ["METRICS_MEDIA_TYPE", "router"]
