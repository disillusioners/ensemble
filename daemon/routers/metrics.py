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
  registry, NO auth bypass, NO per-metric filtering;
* the response is the registry's full text exposition, exactly as
  :func:`render_metrics` produces it.

AUTH: the route carries NO scope decoration and NO class tag, so it
follows the daemon's fail-safe default — OAuth2-PROTECTED when auth is
ON (an untagged route is protected by default; see
``daemon/auth/route_meta.py``), and open in the auth-OFF local mode
(the same posture as the other observe-only diagnostic families, e.g.
``/api/resources``). It is NOT a public probe: the metric registry
carries internal counters and must not be world-readable.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from daemon.checkpoint_metrics import render_metrics

router = APIRouter(prefix="/metrics", tags=["metrics"])

#: The Prometheus text-exposition media type (the format's own
#: declared content type — a scraper keys off the ``version=0.0.4``
#: parameter, not off ``application/json``).
METRICS_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get(
    "",
    response_class=PlainTextResponse,
    responses={
        200: {
            "description": (
                "The daemon's metric registry in Prometheus "
                "text-exposition format."
            ),
            "content": {METRICS_MEDIA_TYPE: {"example": "# TYPE ..."}},
        }
    },
)
async def get_metrics() -> PlainTextResponse:
    """Render the daemon's metric registry as Prometheus text-exposition.

    The body is exactly :func:`daemon.checkpoint_metrics.render_metrics`
    — no filtering, no reshaping.
    """
    return PlainTextResponse(content=render_metrics(), media_type=METRICS_MEDIA_TYPE)


__all__ = ["METRICS_MEDIA_TYPE", "router"]
