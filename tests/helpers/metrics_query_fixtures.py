"""Shared fixtures for the central-metrics-v1 query-door packs.

The query door is ONE provider behind THREE routes, so every pack needs
the same two things:

* a MOUNTED app — the family is mounted under ``/api`` exactly as
  ``daemon/api.py`` mounts it (the house router-test shape, the
  ``test_metrics_route.py`` precedent);
* a duck-typed provider that RECORDS what the door read — so a pack can
  assert the READ CONTRACT (no registry read before the fail-closed
  seam; the body built OFF the event loop) and not only the response
  shape.

The provider is a duck-typed fake on purpose: the door's substrate is
the :class:`~daemon.services.metrics_query.protocol.MetricsQueryProvider`
PROTOCOL, so a fake is a first-class consumer of the contract.
"""

from __future__ import annotations

import threading
import weakref
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from daemon.auth.context import AuthContext

#: The prefix the family is mounted under (``daemon/api.py`` mounts the
#: metrics router on the ``/api`` APIRouter).
API_PREFIX = "/api"

#: The scope the family carries — the EXISTING taxonomy entry for a
#: maintenance/diagnostic read.
READ_SCOPE = "maintenance:read"

#: The three query paths (relative to the mount prefix).
QUERY_PATHS: tuple[str, ...] = (
    "/api/metrics/families",
    "/api/metrics/snapshot",
    "/api/metrics/series",
)


class RecordingProvider:
    """A duck-typed provider that RECORDS the door's reads.

    ``reads`` names the view each call touched; ``threads`` records the
    THREAD each read ran on — the off-loop build assertion reads the
    latter (a ``db-offload*`` worker thread, never the event loop).
    """

    def __init__(
        self,
        *,
        families: list[dict[str, Any]] | None = None,
        snapshot: list[dict[str, Any]] | None = None,
        history: list[dict[str, Any]] | None = None,
        listener_enabled: bool = True,
        census_enabled: bool = True,
        effective_interval_s: float = 15.0,
        last_sample_age_s: float = 0.0,
        oldest_sample_timestamp: int | None = None,
    ) -> None:
        self._families = list(families or [])
        self._snapshot = list(snapshot or [])
        self._history = list(history or [])
        self.listener_enabled = listener_enabled
        self.census_enabled = census_enabled
        self.effective_interval_s = effective_interval_s
        self.last_sample_age_s = last_sample_age_s
        self._oldest = oldest_sample_timestamp
        #: The view names, in call order.
        self.reads: list[str] = []
        #: The thread each read ran on.
        self.threads: list[str] = []
    # ── the three views (the provider protocol) ────────────────────────

    def families(self) -> list[dict[str, Any]]:
        self.reads.append("families")
        self.threads.append(threading.current_thread().name)
        return list(self._families)

    def snapshot(
        self, *, family: str | None = None, name_prefix: str | None = None
    ) -> list[dict[str, Any]]:
        self.reads.append("snapshot")
        self.threads.append(threading.current_thread().name)
        if family is not None:
            return [
                entry
                for entry in self._snapshot
                if entry.get("family") == family or entry.get("name") == family
            ]
        if name_prefix is not None:
            return [
                entry
                for entry in self._snapshot
                if str(entry.get("name", "")).startswith(name_prefix)
            ]
        return list(self._snapshot)

    def history(
        self,
        *,
        names: Any = None,
        series: Any = None,
        window_s: int | None = None,
        max_points: int | None = None,
        now: int | None = None,
    ) -> list[dict[str, Any]]:
        self.reads.append("history")
        self.threads.append(threading.current_thread().name)
        return list(self._history)

    def oldest_sample_timestamp(self) -> int | None:
        self.threads.append(threading.current_thread().name)
        return self._oldest


def build_query_app(
    provider: Any | None,
    *,
    auth_on: bool = True,
    scopes: tuple[str, ...] = (READ_SCOPE,),
    mount_prefix: str = API_PREFIX,
) -> FastAPI:
    """A minimal app carrying the metrics family under the mount prefix.

    ``auth_on=True`` sets ``app.state.auth_config`` (its PRESENCE is the
    authoritative auth-ON signal) and attaches an ``AuthContext`` with
    the granted scopes, exactly as the auth middleware does in
    production.  ``auth_on=False`` NEVER sets the attribute — the
    auth-OFF posture the 503 gate exists for.

    ``provider=None`` leaves ``app.state.metrics_query`` UNSET — the
    "registry unavailable" (503) condition.
    """
    from daemon.routers.metrics import router as metrics_router

    api_router = APIRouter(prefix=mount_prefix)
    api_router.include_router(metrics_router)
    app = FastAPI()
    app.include_router(api_router)

    if provider is not None:
        app.state.metrics_query = provider

    if auth_on:

        @app.middleware("http")
        async def _attach_auth_context(request, call_next):  # noqa: ANN001
            request.state.auth_context = AuthContext(
                subject="test-operator",
                scopes=set(scopes),
                auth_method="session",
                expires_at="2099-01-01T00:00:00Z",
            )
            return await call_next(request)

        app.state.auth_config = object()

    return app


def build_client(
    provider: Any | None,
    *,
    auth_on: bool = True,
    scopes: tuple[str, ...] = (READ_SCOPE,),
    mount_prefix: str = API_PREFIX,
) -> TestClient:
    """The house router-test client (the router mounted under ``/api``).

    The client's portal is ENTERED, so every request of the client shares
    ONE event loop, and the off-loop seam is re-bound to it.  The seam
    (``daemon.services.db_offload``) is a PROCESS-level singleton bound
    to the loop that first used it — in production that is the daemon's
    single loop, which lives as long as the process.  A bare
    ``TestClient`` opens a FRESH portal (and a fresh loop) per request,
    so the seam's captured loop would go stale after the first request.
    The portal is closed when the client is garbage collected.
    """
    from daemon.services import db_offload

    app = build_query_app(
        provider,
        auth_on=auth_on,
        scopes=scopes,
        mount_prefix=mount_prefix,
    )
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    # Re-bind the seam to THIS client's loop (``loop=None`` ⇒ the seam
    # binds lazily to the loop of the first request).
    db_offload.configure(loop=None)
    weakref.finalize(client, _close_client, client)
    return client


def _close_client(client: TestClient) -> None:
    """Close the client's portal (the GC-time teardown)."""
    try:
        client.__exit__(None, None, None)
    except Exception:  # pragma: no cover — a teardown never fails a test
        pass


@contextmanager
def query_client(
    provider: Any | None,
    *,
    auth_on: bool = True,
    scopes: tuple[str, ...] = (READ_SCOPE,),
    mount_prefix: str = API_PREFIX,
):
    """A client whose requests share ONE event loop.

    The off-loop seam (``daemon.services.db_offload``) is a PROCESS-level
    singleton bound to the loop that first used it — in production that
    is the daemon's single loop, which lives as long as the process.  A
    bare ``TestClient`` opens a FRESH portal (and a fresh loop) per
    request, so the seam's captured loop would go stale after the first
    request.  Entering the client keeps ONE portal (and one loop) for
    every request of the block, and the seam is re-bound to it — the
    production shape, reproduced in the test.
    """
    from daemon.services import db_offload

    app = build_query_app(
        provider,
        auth_on=auth_on,
        scopes=scopes,
        mount_prefix=mount_prefix,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        # Re-bind the seam to THIS client's loop (``loop=None`` ⇒ the
        # seam binds lazily to the loop of the first request).
        db_offload.configure(loop=None)
        yield client


def error_code(response: Any) -> str | None:
    """The typed error code of a response body (the ``detail.error``)."""
    try:
        body = response.json()
    except Exception:  # pragma: no cover — a non-JSON body has no code
        return None
    detail = body.get("detail", body) if isinstance(body, dict) else {}
    if isinstance(detail, dict):
        code = detail.get("error")
        return str(code) if code else None
    return None


__all__ = [
    "API_PREFIX",
    "QUERY_PATHS",
    "READ_SCOPE",
    "RecordingProvider",
    "build_client",
    "build_query_app",
    "error_code",
    "query_client",
]
