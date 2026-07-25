"""C3 smoke test — Verify ``/vscode`` mount is not shadowed by the SPA catch-all.

The C3 reviewer concern: Starlette's catch-all ``@app.get("/{path:path}")``
route could intercept ``/vscode/*`` requests before they reach the mounted
proxy sub-application, returning the SPA ``index.html`` instead of the
code-server proxy response.

Two defenses exist in ``daemon/api.py``:

1. **Route ordering in lifespan** (lines ~615-647 of ``daemon/api.py``):
   After ``app.mount("/vscode", ...)``, the code explicitly moves the
   mount to before the catch-all ``/{path:path}`` route.

2. **Defense-in-depth prefix guard** (the catch-all at lines ~1474-1486):
   The SPA fallback rejects any path starting with ``api``, ``ws``, or
   ``vscode`` — so even if the mount ordering were ever broken, the
   catch-all would not serve ``index.html`` for ``/vscode/*`` paths.

These tests pin both behaviors:

* **TestMountRoutingIsolation** builds a minimal parent FastAPI app
  that mirrors the real structure (``/vscode`` mount + ``/{path:path}``
  catch-all) and confirms the routing decision. We don't drive the
  real daemon lifespan — that pulls in DBs, agents, langgraph, etc.
  Instead we test the routing principle in isolation.

* **TestRealAppCatchAllGuard** introspects ``daemon.api.create_app()``
  to verify the catch-all route is registered AND has the ``vscode``
  prefix guard. This pins the defense-in-depth.

* **TestRealAppCatchAllIsLastRoute** sanity-checks that the catch-all
  is registered AFTER all other API routes in the static route table
  produced by ``create_app()``. (The dynamic ``/vscode`` mount is added
  during lifespan startup, so it doesn't appear here — that's expected.)

Run only this file::

    pytest tests/integration/test_vscode_routing.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from daemon.api import create_app
from daemon.routers.vscode_proxy import create_vscode_proxy_app


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_fastapi_api_route_default_response_model():
    """Same fixture as ``test_vscode_proxy.py``.

    FastAPI >= 0.120 rejects ``StreamingResponse | JSONResponse`` as a
    return annotation on ``api_route``. The proxy factory registers a
    catch-all via ``api_route`` without overriding ``response_model``,
    so calling it raises at registration time. This fixture defaults
    ``response_model=None`` so the factory can run unmodified.

    Auto-restored after each test to prevent leakage.
    """
    from fastapi.applications import FastAPI as FastAPIClass

    original = FastAPIClass.api_route

    def _patched(self, *args, **kwargs):
        if "response_model" not in kwargs:
            kwargs["response_model"] = None
        return original(self, *args, **kwargs)

    FastAPIClass.api_route = _patched
    try:
        yield
    finally:
        FastAPIClass.api_route = original


def _make_mock_manager(*, running: bool = False, port: int | None = None):
    """Minimal manager stub. The proxy only reads ``is_running`` and ``get_port``."""
    mgr = MagicMock(name="vscode_manager")
    mgr.is_running.return_value = running
    mgr.get_port.return_value = port
    return mgr


def _find_catchall_route(app):
    """Return the ``/{path:path}`` GET route registered by ``create_app``.

    Raises ``AssertionError`` if not found. The catch-all endpoint is the
    SPA fallback; tests need its actual function to invoke it directly.
    """
    for route in app.routes:
        if (
            getattr(route, "path", None) == "/{path:path}"
            and "GET" in getattr(route, "methods", set())
        ):
            return route
    raise AssertionError(
        "Catch-all /{path:path} GET route not found in create_app() output."
    )


def _invoke_catchall_directly(captured_path: str):
    """Invoke the real catch-all endpoint without driving the lifespan.

    ``create_app()`` registers the route and ``TestClient`` would trigger
    the lifespan (which requires Postgres). We sidestep both by calling
    the endpoint function directly with the captured path string.

    The endpoint signature is ``(path: str)`` — Starlette populates the
    ``path`` param from the URL's ``path_params`` during normal routing.
    Calling directly bypasses that wiring, so we pass the value ourselves.

    Returns the Response object the handler produced.
    """
    import asyncio
    import inspect

    app = create_app()
    catchall_route = _find_catchall_route(app)
    endpoint = catchall_route.endpoint

    result = endpoint(path=captured_path)
    if inspect.iscoroutine(result):
        # Use asyncio.run() rather than get_event_loop() — the latter is
        # deprecated in Python 3.12+ when no loop is set, and pytest-asyncio
        # may not have one bound during fixture setup.
        result = asyncio.run(result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 1. Isolated routing: /vscode mount vs catch-all
# ─────────────────────────────────────────────────────────────────────────────


class TestMountRoutingIsolation:
    """Verify the routing principle behind the C3 fix.

    Build a minimal parent FastAPI app that mirrors the real structure:

    * A mounted sub-app at ``/vscode`` with a readiness gate (503).
    * A ``/{path:path}`` catch-all that simulates the SPA fallback.

    The test asserts that Starlette's router sends ``/vscode/*`` paths
    to the mount (not the catch-all), while preserving the catch-all's
    behavior for paths that are NOT under the mount prefix.

    This pins the *principle* of the fix without depending on the real
    daemon lifespan, DBs, agents, or langgraph wiring.
    """

    def test_vscode_path_reaches_mount_not_catchall(self):
        """``/vscode/some-path`` must reach the proxy, not the SPA fallback.

        If the catch-all shadowed the mount, this would return 200 with
        ``{"error": "SPA fallback"}``. Correct routing returns 503 from
        the proxy's readiness gate (because the mock manager is not running).
        """
        parent = FastAPI()
        manager = _make_mock_manager(running=False, port=None)
        proxy_app = create_vscode_proxy_app(manager)
        parent.mount("/vscode", proxy_app)

        @parent.get("/{path:path}")
        async def _spa_fallback(path: str):
            return JSONResponse({"error": "SPA fallback"}, status_code=200)

        with TestClient(parent) as client:
            response = client.get("/vscode/some-path")

        assert response.status_code == 503, (
            f"Expected 503 from proxy readiness gate, got "
            f"{response.status_code}. The catch-all may be shadowing the "
            f"/vscode mount — Starlette's catch-all matched /vscode/some-path "
            f"before the mount was consulted."
        )
        # The 503 body must come from the proxy, not the SPA.
        body = response.json()
        assert "SPA" not in str(body), (
            f"Got SPA response body: {body!r}. Mount was shadowed."
        )
        assert "not ready" in str(body).lower(), (
            f"Expected 503 'not ready' from proxy, got body: {body!r}"
        )

    def test_vscode_root_reaches_mount_not_catchall(self):
        """``GET /vscode/`` (with trailing slash) reaches the proxy.

        Starlette mount prefix matching quirk: ``mount("/vscode", app)``
        matches ``/vscode/*`` but NOT bare ``/vscode`` (no trailing slash).
        Real-world requests from the editor always carry a trailing slash
        or a sub-path, so we test the canonical form here. Bare ``/vscode``
        is covered separately by ``test_catchall_rejects_vscode_prefix``
        via the prefix guard on the real SPA catch-all.
        """
        parent = FastAPI()
        manager = _make_mock_manager(running=False, port=None)
        proxy_app = create_vscode_proxy_app(manager)
        parent.mount("/vscode", proxy_app)

        # Mirror the real SPA catch-all's prefix guard exactly.
        @parent.get("/{path:path}")
        async def _spa_fallback(path: str):
            if (
                path.startswith("api")
                or path.startswith("ws")
                or path.startswith("vscode")
            ):
                return JSONResponse({"error": "Not found"}, status_code=404)
            return JSONResponse({"error": "SPA fallback"}, status_code=200)

        with TestClient(parent) as client:
            response = client.get("/vscode/")

        # The mount matches /vscode/ before the catch-all does, so the
        # proxy's 503 readiness gate fires.
        assert response.status_code == 503, (
            f"Expected 503 from proxy mount for /vscode/, got "
            f"{response.status_code}. Body: {response.text!r}"
        )

    def test_vscode_prefix_does_not_swallow_unrelated_paths(self):
        """``/vscodefoo`` MUST fall through to the SPA catch-all.

        Starlette mount prefix matching is exact-match on the prefix
        boundary, NOT a prefix-string match. So ``/vscodefoo`` does NOT
        match the ``/vscode`` mount — it goes to the catch-all.

        If mount matching were a string-prefix match instead, the mount
        would swallow ``/vscodefoo`` (incorrectly). This test pins that
        boundary behavior.
        """
        parent = FastAPI()
        manager = _make_mock_manager(running=False, port=None)
        proxy_app = create_vscode_proxy_app(manager)
        parent.mount("/vscode", proxy_app)

        @parent.get("/{path:path}")
        async def _spa_fallback(path: str):
            return JSONResponse({"error": "SPA fallback"}, status_code=200)

        with TestClient(parent) as client:
            response = client.get("/vscodefoo")

        # /vscodefoo must NOT reach the proxy mount (which would 503).
        # It should reach the SPA catch-all.
        assert response.status_code == 200, (
            f"Expected 200 from SPA catch-all for /vscodefoo, got "
            f"{response.status_code}. The /vscode mount is incorrectly "
            f"matching path-prefix strings instead of exact prefixes."
        )
        assert response.json() == {"error": "SPA fallback"}

    def test_vscode_mount_takes_precedence_over_catchall(self):
        """When a real manager is running, the mount returns the upstream.

        Belt-and-suspenders: even with a running manager + valid port,
        Starlette's routing decision should still pick the mount over the
        catch-all. We can't reach a real upstream here, but we can verify
        that the request reaches the proxy (and not the SPA) by checking
        that the SPA's body shape (``{"error": "SPA fallback"}``) is NOT
        what comes back.
        """
        parent = FastAPI()
        manager = _make_mock_manager(running=True, port=1)
        proxy_app = create_vscode_proxy_app(manager)
        parent.mount("/vscode", proxy_app)

        @parent.get("/{path:path}")
        async def _spa_fallback(path: str):
            return JSONResponse({"error": "SPA fallback"}, status_code=200)

        with TestClient(parent) as client:
            # Manager reports running=True but port=1 is unreachable;
            # proxy will try httpx and fail. We just need to confirm
            # the SPA did NOT respond.
            try:
                response = client.get("/vscode/index.html")
            except Exception:
                # Upstream connection failure is acceptable — it means
                # we got PAST the catch-all and reached the proxy.
                return

            # If the response is from the SPA, the body shape gives it
            # away; if it's from the proxy, body shape is different.
            try:
                body = response.json()
            except Exception:
                # Non-JSON response (e.g. streaming) means we hit the proxy.
                return

            assert body != {"error": "SPA fallback"}, (
                "SPA fallback matched /vscode/index.html — mount shadowed."
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Real catch-all has the vscode prefix guard (defense in depth)
# ─────────────────────────────────────────────────────────────────────────────


class TestRealAppCatchAllGuard:
    """Pin the defense-in-depth guard on the SPA catch-all route.

    The real ``create_app()`` registers a catch-all that rejects paths
    starting with ``api``, ``ws``, AND ``vscode`` before attempting to
    serve a frontend asset. This is the belt to the mount-ordering
    suspenders — even if the mount ordering broke, ``/vscode/*`` paths
    would get a 404 instead of being served ``index.html``.
    """

    def test_catchall_route_registered_in_create_app(self):
        """``create_app()`` produces an app with a ``/{path:path}`` GET route.

        Confirms the SPA fallback exists at all. If this assertion fails,
        someone removed the route entirely — investigate before fixing
        downstream tests.
        """
        app = create_app()
        catchall_routes = [
            r for r in app.routes
            if getattr(r, "path", None) == "/{path:path}"
            and "GET" in getattr(r, "methods", set())
        ]
        assert catchall_routes, (
            "Expected a GET /{path:path} route in create_app() output, "
            "found none. The SPA fallback appears to be missing."
        )

    def test_catchall_rejects_vscode_prefix(self):
        """The real catch-all's prefix guard returns 404 for ``/vscode/*`` paths.

        This is the defense-in-depth: even if the mount were ever
        unregistered or its ordering broken, the catch-all's prefix
        guard ensures the SPA does not swallow proxy traffic.

        We invoke the endpoint function DIRECTLY (no TestClient, no
        lifespan) — ``create_app()`` registers the route, but driving it
        via TestClient would trigger the lifespan which requires Postgres.
        Calling the endpoint directly with ``path="vscode/some-path"``
        exercises the actual handler body — including the prefix guard.
        """
        result = _invoke_catchall_directly("vscode/some-path")

        # The guard fires BEFORE the SPA fallback's index.html lookup, so
        # we get a 404 JSONResponse with error="Not found".
        assert result.status_code == 404, (
            f"Expected 404 from catch-all's vscode-prefix guard, got "
            f"{result.status_code}. The SPA may be intercepting /vscode/* "
            f"paths and serving index.html."
        )
        # JSONResponse exposes the body via .body (bytes).
        import json as _json
        body = _json.loads(result.body)
        assert body == {"error": "Not found"}, (
            f"Expected 'Not found' error body, got: {body!r}"
        )

    def test_catchall_passes_through_unrelated_paths(self):
        """Belt-and-suspenders: unrelated paths don't trip the prefix guard.

        A path like ``/some-frontend-asset.js`` does NOT start with
        ``api``, ``ws``, or ``vscode``, so the guard skips and the
        endpoint proceeds to the asset-serving logic. We don't pin the
        exact response (which depends on FRONTEND_DIST contents), just
        that it's NOT a 404-from-guard. If FRONTEND_DIST is not built,
        the endpoint returns 404 with a different body shape
        (``{"error": "Asset not found"}``), which is fine — we just
        distinguish it from the prefix-guard 404.
        """
        import json as _json

        result = _invoke_catchall_directly("totally-unrelated-path")

        # If we get a JSON body, it must NOT say "Not found" — that's the
        # prefix-guard's signature. An "Asset not found" body or a 200
        # FileResponse both indicate the guard correctly skipped this path.
        try:
            body = _json.loads(result.body)
        except Exception:
            # Non-JSON (e.g. FileResponse serving a real asset) → fine.
            return

        assert body.get("error") != "Not found", (
            f"Prefix guard fired for an unrelated path — body: {body!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Static route ordering — the catch-all is last in create_app() output
# ─────────────────────────────────────────────────────────────────────────────


class TestRealAppCatchAllIsLastRoute:
    """Verify the catch-all comes after all API routes in ``create_app()``.

    Starlette matches routes in registration order; the first registered
    route that matches wins. To keep API routes from being shadowed by
    the SPA fallback, the catch-all MUST be registered AFTER all other
    routes inside ``create_app()``.

    The dynamic ``/vscode`` mount is added during lifespan startup, so
    it doesn't appear in this list — that's expected and tested
    separately by the lifespan ordering logic.
    """

    def test_catchall_is_last_in_route_list(self):
        """The SPA catch-all ``/{path:path}`` must be the last GET route
        in ``create_app()``'s route list.

        Anything after a GET ``/{path:path}`` in Starlette is unreachable
        for GET requests — Starlette stops on first match.
        """
        app = create_app()
        catchall_idx = None
        for i, route in enumerate(app.routes):
            if getattr(route, "path", None) == "/{path:path}":
                catchall_idx = i
                break

        assert catchall_idx is not None, (
            "Catch-all /{path:path} not found in create_app() routes."
        )
        assert catchall_idx == len(app.routes) - 1, (
            f"Catch-all /{{path:path}} is at index {catchall_idx} but "
            f"there are {len(app.routes)} routes total — the catch-all "
            f"must be the LAST registered route so it doesn't shadow "
            f"other GET routes. Routes after the catch-all: "
            f"{[getattr(r, 'path', '?') for r in app.routes[catchall_idx + 1:]]!r}"
        )
