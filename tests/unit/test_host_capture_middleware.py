"""Unit tests for ``daemon.api.HostCaptureMiddleware``.

Phase 2 follow-up — module-level class placement lets the test
import the middleware directly without the ``inspect.getsource``
extraction trick used for ``SelectiveAccessLogMiddleware`` (see
``tests/unit/test_selective_access_log_middleware.py``). The
wiring inside ``create_app()`` is unchanged: ``app.add_middleware(
HostCaptureMiddleware)`` at the same site, same behavior.

The middleware's job (Phase 2 of the live-view URL chain):

* On every inbound ``http`` ASGI request, look up the recorder
  at ``scope["app"].state.host_recorder`` (Starlette 0.30+
  writes ``scope["app"]`` for every request).
* Decode the ASGI ``headers`` list (raw bytes) into a
  lower-cased ``str: str`` map; drop any malformed pair (must
  never raise — middleware errors break the request path).
* If a ``host`` header is present, call ``recorder.record(host,
  x_forwarded_proto)``.

Pins the contract:

1. A served request records ``Host`` + ``X-Forwarded-Proto``
   into the recorder.
2. The exception path does NOT poison the recorder with a
   failing request's Host. The middleware MUST pass-through the
   request scope and MUST NOT swallow / re-raise the downstream
   exception; the recorder stays at its prior state for the
   succeeding test cycle (capture happens before the downstream
   call, so the recorder may legitimately have advanced — the
   pin is that the middleware does NOT clear / crash / leak).
3. The ``saw_https`` flag flips after an https capture so the
   resolver can warn on later http-downgrade mints.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from daemon.api import HostCaptureMiddleware
from daemon.services.live_views import HostRecorder


def _build_app_with_recorder() -> tuple[FastAPI, HostRecorder]:
    """Build a minimal FastAPI app wired with HostCaptureMiddleware.

    Returns ``(app, recorder)`` so the test can assert what the
    middleware wrote. The recorder is wired to ``app.state`` exactly
    the way the lifespan does in production.
    """
    recorder = HostRecorder()
    app = FastAPI()
    app.state.host_recorder = recorder
    app.add_middleware(HostCaptureMiddleware)

    @app.get("/probe")
    async def _probe():
        return JSONResponse(status_code=200, content={"ok": True})

    return app, recorder


# ===========================================================================
# Group 1 — A served request records Host + X-Forwarded-Proto
# ===========================================================================


class TestHostCaptureMiddlewareHappyPath:
    """A normal served request feeds the recorder."""

    def test_records_host_only(self):
        app, recorder = _build_app_with_recorder()
        with TestClient(app) as client:
            resp = client.get("/probe", headers={"Host": "example.com"})
        assert resp.status_code == 200
        latest = recorder.latest()
        assert latest is not None
        host, port, scheme = latest
        assert host == "example.com"
        assert port is None  # no port in the Host header
        assert scheme == "http"  # default when XFP absent

    def test_records_host_and_xfp_https(self):
        app, recorder = _build_app_with_recorder()
        with TestClient(app) as client:
            resp = client.get(
                "/probe",
                headers={
                    "Host": "ensemble.example.com",
                    "X-Forwarded-Proto": "https",
                },
            )
        assert resp.status_code == 200
        latest = recorder.latest()
        assert latest is not None
        host, port, scheme = latest
        assert host == "ensemble.example.com"
        assert scheme == "https"

    def test_records_host_with_port(self):
        app, recorder = _build_app_with_recorder()
        with TestClient(app) as client:
            resp = client.get(
                "/probe", headers={"Host": "example.com:8443"}
            )
        assert resp.status_code == 200
        latest = recorder.latest()
        assert latest is not None
        host, port, scheme = latest
        assert host == "example.com"
        assert port == 8443
        assert scheme == "http"

    def test_records_lowercased_xfp(self):
        """Case-insensitive scheme inference.

        ``X-Forwarded-Proto: HTTPS`` is recorded as scheme=https.
        """
        app, recorder = _build_app_with_recorder()
        with TestClient(app) as client:
            resp = client.get(
                "/probe",
                headers={
                    "Host": "example.com",
                    "X-Forwarded-Proto": "HTTPS",
                },
            )
        assert resp.status_code == 200
        latest = recorder.latest()
        assert latest is not None
        _, _, scheme = latest
        assert scheme == "https"


# ===========================================================================
# Group 2 — saw_https flag (Phase 2 follow-up)
# ===========================================================================


class TestHostCaptureMiddlewareSawHTTPSFlag:
    """The ``saw_https`` flag flips after an https capture.

    Phase 2 follow-up — the resolver uses this flag to log an
    operator warning when a downstream mint silently drops to
    ``http://...`` despite the deployment having produced https
    evidence at some prior point.
    """

    def test_saw_https_starts_false(self):
        recorder = HostRecorder()
        assert recorder.saw_https() is False

    def test_saw_https_flips_on_https_request(self):
        app, recorder = _build_app_with_recorder()
        with TestClient(app) as client:
            client.get(
                "/probe",
                headers={
                    "Host": "example.com",
                    "X-Forwarded-Proto": "https",
                },
            )
        assert recorder.saw_https() is True

    def test_saw_https_stays_false_on_http_only_requests(self):
        app, recorder = _build_app_with_recorder()
        with TestClient(app) as client:
            # Several http requests, no https.
            client.get("/probe", headers={"Host": "a.example.com"})
            client.get("/probe", headers={"Host": "b.example.com"})
        assert recorder.saw_https() is False

    def test_saw_https_latches(self):
        """Once flipped, stays flipped — even if later requests
        are http-only or malformed. The flag is monotonic
        (lifetime of the recorder)."""
        recorder = HostRecorder()
        recorder.record("a.example.com", "https")
        assert recorder.saw_https() is True
        recorder.record("b.example.com")  # http default
        assert recorder.saw_https() is True


# ===========================================================================
# Group 3 — Exception path does NOT crash the middleware
# ===========================================================================


class TestHostCaptureMiddlewareExceptionPath:
    """A downstream raise MUST NOT crash the middleware.

    The middleware is pass-through (no failure path); the
    recorder's ``record`` method already drops bad Host values.
    A failing downstream is unrelated to URL minting — we
    pin that the middleware propagates the exception cleanly
    without clearing the recorder.
    """

    def test_downstream_exception_propagates(self):
        """A 500 from a downstream handler MUST surface as
        a 500 to the TestClient (the middleware must not
        swallow the exception)."""
        recorder = HostRecorder()
        app = FastAPI()
        app.state.host_recorder = recorder
        app.add_middleware(HostCaptureMiddleware)

        @app.get("/boom")
        async def _boom():
            raise RuntimeError("downstream blew up")

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/boom", headers={"Host": "example.com"}
            )
        # The TestClient returns 500 instead of re-raising because
        # raise_server_exceptions=False. The middleware MUST have
        # passed through.
        assert resp.status_code == 500

    def test_recorder_still_functional_after_exception(self):
        """The recorder must be in a valid state after a
        failing request — the middleware does not clear it.

        We seed the recorder with a known state via the
        middleware's normal capture path, then issue a failing
        request, then issue a succeeding request and verify
        the recorder advanced normally (i.e. the exception
        path didn't poison state)."""
        recorder = HostRecorder()
        app = FastAPI()
        app.state.host_recorder = recorder
        app.add_middleware(HostCaptureMiddleware)

        @app.get("/probe")
        async def _probe():
            return JSONResponse(status_code=200, content={"ok": True})

        @app.get("/boom")
        async def _boom():
            raise RuntimeError("downstream blew up")

        with TestClient(app, raise_server_exceptions=False) as client:
            # Seed: succeed first.
            client.get("/probe", headers={"Host": "first.example.com"})
            assert recorder.latest() is not None
            assert recorder.latest()[0] == "first.example.com"
            # Now a failing request — recorder may advance
            # (capture happens pre-downstream), but must not crash.
            client.get(
                "/boom", headers={"Host": "boom.example.com"}
            )
            # And a second succeed — recorder MUST remain usable.
            client.get(
                "/probe", headers={"Host": "third.example.com"}
            )
        latest = recorder.latest()
        assert latest is not None
        host, _, _ = latest
        assert host == "third.example.com"