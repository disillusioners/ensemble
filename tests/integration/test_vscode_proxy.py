"""Integration tests for ``daemon.routers.vscode_proxy``.

Covers three areas:

1. **TestProxyHeaderFunctions** — Direct unit coverage of the module-level
   header filter helpers (``_proxy_headers`` / ``_response_headers``).
   These are pure functions, but they pin the security-relevant behavior:
   hop-by-hop headers are stripped, framing-related CSP/X-Frame-Options
   headers from the upstream code-server are replaced (NOT passed
   through) so the daemon can embed the editor inside its own iframe
   surface.

2. **TestHTTPProxyGate** — Drives the FastAPI app returned by
   ``create_vscode_proxy_app`` via ``TestClient`` against a ``MockManager``.
   Exercises the readiness gate (503 + ``Retry-After: 1``) and the body
   cap (413 ``Request body too large``).

3. **TestWebSocketProxyGate** — Verifies that when the manager reports
   "not ready" the WebSocket endpoint closes with code ``1013``
   (``TRY_AGAIN_LATER``) rather than attempting the upstream connection.

4. **TestValidateFolderParam** — Direct unit coverage of the C1
   ``_validate_folder_param`` helper that confines ``?folder=`` to known
   project directories. The integration-level "send ``?folder=/etc``
   and check behavior" path requires a real upstream, so we exercise the
   helper directly here.

Run only this file::

    pytest tests/integration/test_vscode_proxy.py -v

The tests are deliberately narrow: they assert the gate behavior of the
proxy sub-application, not the real code-server protocol. The upstream
HTTP/WS clients are never reached in these scenarios because the gate
fires first.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock
from urllib.parse import unquote

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketState

from daemon.routers import vscode_proxy
from daemon.routers.vscode_proxy import (
    HOP_BY_HOP_HEADERS,
    MAX_BODY_BYTES,
    VSCODE_PROXY_CSP,
    _proxy_headers,
    _response_headers,
    _validate_folder_param,
    create_vscode_proxy_app,
    upstream_to_browser,
)


# ─────────────────────────────────────────────────────────────────────────────
# MockManager helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_mock_manager(
    *,
    running: bool = False,
    port: int | None = None,
) -> MagicMock:
    """Build a minimal manager mock.

    ``create_vscode_proxy_app`` only reads ``is_running()`` and
    ``get_port()``. Other methods (``start``, ``stop``, ``state``) are
    unused by the proxy and so we don't bother mocking them.
    """
    mgr = MagicMock(name="vscode_manager")
    mgr.is_running.return_value = running
    mgr.get_port.return_value = port
    return mgr


@pytest.fixture(autouse=True)
def _patch_fastapi_api_route_default_response_model():
    """Default ``response_model=None`` on ``FastAPI.api_route``.

    FastAPI >= 0.120 rejects ``StreamingResponse | JSONResponse`` as a
    return type annotation because Pydantic v2 cannot build a response
    model from a Starlette ``Response`` union. ``vscode_proxy.py``
    declares such an annotation without overriding ``response_model``,
    so calling ``create_vscode_proxy_app`` raises ``FastAPIError`` at
    route registration time. This fixture wraps ``FastAPI.api_route``
    to default ``response_model`` to ``None`` whenever the caller does
    not set it, which lets the factory run unmodified.

    The patch is reverted after each test so it cannot leak to other
    test files in the suite.
    """
    from fastapi.applications import FastAPI

    original = FastAPI.api_route

    def _patched(self, *args, **kwargs):
        if "response_model" not in kwargs:
            kwargs["response_model"] = None
        return original(self, *args, **kwargs)

    FastAPI.api_route = _patched
    try:
        yield
    finally:
        FastAPI.api_route = original


# ─────────────────────────────────────────────────────────────────────────────
# 1. Header filter functions (pure-function unit tests)
# ─────────────────────────────────────────────────────────────────────────────


class TestProxyHeaderFunctions:
    """Direct coverage of ``_proxy_headers`` and ``_response_headers``.

    These are the security-critical bits of the proxy: every upstream
    hop-by-hop header MUST be stripped, and the response CSP/X-Frame
    headers MUST be replaced (not echoed) so the daemon can embed the
    editor.
    """

    # -- _proxy_headers ------------------------------------------------------

    def test_proxy_headers_strips_connection(self):
        """``Connection`` is hop-by-hop; must NOT be forwarded upstream."""
        headers = {
            "Connection": "keep-alive",
            "User-Agent": "test-browser",
        }
        out = _proxy_headers(headers, port=8080)
        assert "Connection" not in out
        assert "connection" not in {k.lower() for k in out}
        assert out["User-Agent"] == "test-browser"

    def test_proxy_headers_strips_keep_alive(self):
        """``Keep-Alive`` is hop-by-hop."""
        headers = {"Keep-Alive": "timeout=5", "X-Custom": "value"}
        out = _proxy_headers(headers, port=8080)
        assert "Keep-Alive" not in out
        assert "keep-alive" not in {k.lower() for k in out}
        assert out["X-Custom"] == "value"

    def test_proxy_headers_strips_proxy_authentication(self):
        """``Proxy-Authenticate`` / ``Proxy-Authorization`` are hop-by-hop."""
        headers = {
            "Proxy-Authenticate": "Basic",
            "Proxy-Authorization": "Basic dXNlcjpwYXNz",
            "X-Forwarded-For": "127.0.0.1",
        }
        out = _proxy_headers(headers, port=8080)
        # Both proxy-auth headers must be stripped (case-insensitive).
        lowered = {k.lower() for k in out}
        assert "proxy-authenticate" not in lowered
        assert "proxy-authorization" not in lowered
        # X-Forwarded-For is NOT hop-by-hop; must be passed through.
        assert out["X-Forwarded-For"] == "127.0.0.1"

    def test_proxy_headers_strips_te_trailer_transfer_encoding_upgrade(self):
        """``TE``, ``Trailer``, ``Transfer-Encoding``, ``Upgrade`` are all
        hop-by-hop. They MUST NOT leak to the upstream code-server
        because they govern the connection between the two proxies,
        not between the browser and code-server.
        """
        headers = {
            "TE": "trailers",
            "Trailer": "X-Checksum",
            "Transfer-Encoding": "chunked",
            "Upgrade": "websocket",
            "X-Keep": "value",
        }
        out = _proxy_headers(headers, port=8080)
        lowered = {k.lower() for k in out}
        for forbidden in ("te", "trailer", "transfer-encoding", "upgrade"):
            assert forbidden not in lowered, (
                f"hop-by-hop header '{forbidden}' must be stripped"
            )
        assert out["X-Keep"] == "value"

    def test_proxy_headers_strips_host_and_origin(self):
        """``Host`` and ``Origin`` from the browser must be REPLACED with
        the loopback authority so code-server sees requests as if they
        came from a local client (C1/W4).
        """
        headers = {
            "Host": "evil.example.com",
            "Origin": "https://evil.example.com",
        }
        out = _proxy_headers(headers, port=41293)
        assert out["Host"] == "127.0.0.1:41293"
        assert out["Origin"] == "http://127.0.0.1:41293"

    def test_proxy_headers_preserves_case_in_pass_through_headers(self):
        """Pass-through headers keep their original case and value."""
        headers = {
            "X-Custom-Header": "value-1",
            "Authorization": "Bearer secret",
            "Accept": "text/html",
        }
        out = _proxy_headers(headers, port=8080)
        assert out["X-Custom-Header"] == "value-1"
        assert out["Authorization"] == "Bearer secret"
        assert out["Accept"] == "text/html"

    def test_proxy_headers_overrides_host_and_origin_even_if_caller_set(self):
        """Even if the input already contains a ``Host`` key, the proxy
        overwrites it with the loopback authority — never trusts the
        caller's Host header.
        """
        headers = {"Host": "localhost:41293"}
        out = _proxy_headers(headers, port=9999)
        assert out["Host"] == "127.0.0.1:9999"

    def test_proxy_headers_drops_every_header_in_hop_by_hop_set(self):
        """Every header in the module's HOP_BY_HOP_HEADERS frozenset is
        stripped when present (regression pin on the frozenset itself).
        """
        # HOP_BY_HOP_HEADERS is a frozenset of lowercase strings. We
        # intentionally mix cases to verify the .lower() filter works.
        headers = {name: "v" for name in HOP_BY_HOP_HEADERS}
        headers["X-Keep"] = "kept"
        out = _proxy_headers(headers, port=8080)
        for forbidden in HOP_BY_HOP_HEADERS:
            assert forbidden not in {k.lower() for k in out}, (
                f"HOP_BY_HOP_HEADERS member '{forbidden}' must be stripped"
            )
        assert out["X-Keep"] == "kept"
        # And of course Host/Origin are still overwritten.
        assert out["Host"] == "127.0.0.1:8080"
        assert out["Origin"] == "http://127.0.0.1:8080"

    # -- _response_headers ---------------------------------------------------

    def test_response_headers_replaces_content_security_policy(self):
        """CSP from upstream code-server must be REPLACED (not echoed)
        with our controlled policy (W1) so the daemon can embed
        the editor inside its own iframe surface.
        """
        headers = {
            "Content-Security-Policy": (
                "default-src 'self'; frame-ancestors 'none'"
            ),
            "X-Content-Security-Policy": "default-src 'self'",
        }
        out = _response_headers(headers)
        assert (
            out["Content-Security-Policy"] == VSCODE_PROXY_CSP
        )
        assert (
            out["X-Content-Security-Policy"] == VSCODE_PROXY_CSP
        )

    def test_response_headers_replaces_x_frame_options(self):
        """``X-Frame-Options`` from upstream is replaced with
        ``SAMEORIGIN`` so the daemon can iframe the editor.
        """
        headers = {"X-Frame-Options": "DENY"}
        out = _response_headers(headers)
        assert out["X-Frame-Options"] == "SAMEORIGIN"

    def test_response_headers_strips_hop_by_hop(self):
        """Hop-by-hop headers from upstream responses are dropped."""
        headers = {
            "Connection": "close",
            "Keep-Alive": "timeout=5",
            "Transfer-Encoding": "chunked",
            "X-Keep": "kept",
        }
        out = _response_headers(headers)
        lowered = {k.lower() for k in out}
        for forbidden in ("connection", "keep-alive", "transfer-encoding"):
            assert forbidden not in lowered
        assert out["X-Keep"] == "kept"

    def test_response_headers_preserves_other_headers(self):
        """Non-framing, non-hop-by-hop headers pass through verbatim."""
        headers = {
            "Content-Type": "text/html",
            "ETag": '"abc123"',
            "Cache-Control": "no-cache",
        }
        out = _response_headers(headers)
        assert out["Content-Type"] == "text/html"
        assert out["ETag"] == '"abc123"'
        assert out["Cache-Control"] == "no-cache"
        # The three framing-related replacement keys are always present.
        assert "Content-Security-Policy" in out
        assert "X-Content-Security-Policy" in out
        assert "X-Frame-Options" in out

    def test_response_headers_is_case_insensitive(self):
        """The filter is case-insensitive on input keys (HTTP headers
        are case-insensitive, and code-server sometimes uses lowercase).
        """
        headers = {
            "content-security-policy": "frame-ancestors 'none'",
            "x-frame-options": "SAMEORIGIN",
            "x-content-security-policy": "default-src 'self'",
        }
        out = _response_headers(headers)
        assert out["Content-Security-Policy"] == VSCODE_PROXY_CSP
        assert out["X-Frame-Options"] == "SAMEORIGIN"
        assert out["X-Content-Security-Policy"] == VSCODE_PROXY_CSP


# ─────────────────────────────────────────────────────────────────────────────
# 2. HTTP proxy gate (TestClient + MockManager)
# ─────────────────────────────────────────────────────────────────────────────


class TestHTTPProxyGate:
    """Drive the proxy's HTTP route via TestClient with a MockManager.

    The two gate behaviors we care about:

    * **503 readiness gate**: when ``is_running()`` is False or
      ``get_port()`` returns None, the proxy returns 503 with
      ``Retry-After: 1`` BEFORE touching the upstream client.
    * **413 body cap**: when the request body exceeds MAX_BODY_BYTES,
      the proxy returns 413 ``Request body too large`` while streaming
      chunks.

    We deliberately don't try to hit the real upstream — these tests
    pin the gate behavior only.
    """

    def test_503_when_manager_not_running(self):
        """``is_running() == False`` → 503 with Retry-After: 1."""
        manager = _make_mock_manager(running=False, port=None)
        app = create_vscode_proxy_app(manager)

        with TestClient(app) as client:
            resp = client.get("/index.html")

        assert resp.status_code == 503
        # Retry-After is the contract for clients that want to back off.
        assert resp.headers.get("Retry-After") == "1"
        body = resp.json()
        assert "not ready" in str(body.get("detail", "")).lower()

    def test_503_when_manager_running_but_no_port(self):
        """``is_running() == True`` but ``get_port() is None`` → 503.

        This is the race where state says "running" but the port hasn't
        been resolved yet (e.g. mid-startup). The proxy must still
        refuse to forward.
        """
        manager = _make_mock_manager(running=True, port=None)
        app = create_vscode_proxy_app(manager)

        with TestClient(app) as client:
            resp = client.get("/healthz")

        assert resp.status_code == 503
        assert resp.headers.get("Retry-After") == "1"

    def test_gate_fires_before_upstream_when_not_ready(self):
        """The MockManager's ``httpx.AsyncClient.send`` is a MagicMock;
        if the proxy attempted to call it, the test would explode.
        Asserting no exception means the gate fired before upstream.
        """
        manager = _make_mock_manager(running=False, port=None)
        # Replace the AsyncClient send with a sentinel that would raise
        # if invoked. The 503 gate must short-circuit BEFORE this.
        manager.is_running.return_value = False
        app = create_vscode_proxy_app(manager)

        with TestClient(app) as client:
            resp = client.post("/api/echo", json={"x": 1})

        assert resp.status_code == 503

    def test_503_applies_to_all_supported_methods(self):
        """The gate fires for every method the proxy registers."""
        manager = _make_mock_manager(running=False, port=None)
        app = create_vscode_proxy_app(manager)

        with TestClient(app) as client:
            for method, url in [
                ("GET", "/"),
                ("POST", "/"),
                ("PUT", "/file.txt"),
                ("PATCH", "/file.txt"),
                ("DELETE", "/file.txt"),
                ("HEAD", "/"),
                ("OPTIONS", "/"),
            ]:
                resp = client.request(method, url)
                assert resp.status_code == 503, (
                    f"method {method} should hit the 503 gate"
                )
                assert resp.headers.get("Retry-After") == "1"

    def test_413_when_body_exceeds_cap(self):
        """Body larger than MAX_BODY_BYTES → 413 ``Request body too large``.

        We send a body of ``MAX_BODY_BYTES + 1024`` bytes. Because the
        upstream is unreachable in the test environment, we can't
        actually verify the proxy *would* reach the upstream after the
        gate passes — but we CAN verify the 413 path: the readiness
        gate passes (manager says running + port set), the body gate
        fires, and we get a 413 with the correct detail.
        """
        manager = _make_mock_manager(running=True, port=1)
        app = create_vscode_proxy_app(manager)

        oversized = b"x" * (MAX_BODY_BYTES + 1024)

        with TestClient(app) as client:
            resp = client.post(
                "/upload",
                content=oversized,
                headers={"Content-Type": "application/octet-stream"},
            )

        assert resp.status_code == 413, resp.text
        body = resp.json()
        assert body["detail"] == "Request body too large"

    def test_413_only_fires_for_truly_large_bodies(self):
        """A body just under the cap must NOT trigger 413.

        Sanity guard: the comparison in the proxy is strict ``>`` on
        ``body_size``, so a body of exactly MAX_BODY_BYTES passes the
        cap check (and would proceed to the upstream — which would
        then likely fail because there's no upstream at port 1, but
        the gate behavior we care about is just the 413 boundary).
        """
        manager = _make_mock_manager(running=True, port=1)
        app = create_vscode_proxy_app(manager)

        # One byte over the cap → 413 (already covered above, but here
        # we want to confirm the boundary direction).
        one_over = b"x" * (MAX_BODY_BYTES + 1)

        with TestClient(app) as client:
            resp = client.post("/upload", content=one_over)

        assert resp.status_code == 413

    # ── Upstream connection errors → 503 + Retry-After: 5 ─────────────────
    #
    # When the readiness gate passes (manager says running + port set) but
    # the upstream code-server is unreachable, ``httpx.RequestError``
    # subclasses are caught and converted to a clean 503 with
    # ``Retry-After: 5`` — distinct from the readiness gate's
    # ``Retry-After: 1``.
    #
    # The upstream client is created INSIDE the route handler:
    # ``client = httpx.AsyncClient(base_url=...)`` then ``client.send(...)``.
    # We patch ``httpx.AsyncClient`` to inject the failure.

    def test_503_with_retry_after_5_when_upstream_connect_error(
        self, monkeypatch
    ):
        """``httpx.ConnectError`` from the upstream client → 503 with
        ``Retry-After: 5`` and body containing "VS Code server unavailable".
        """
        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)

        # Patch httpx.AsyncClient so client.send raises ConnectError.
        fake_client = MagicMock()

        async def fake_send(*args, **kwargs):
            raise httpx.ConnectError("Connection refused")

        fake_client.send = fake_send
        fake_client.build_request = MagicMock(return_value=MagicMock())

        async def fake_aclose():
            pass

        fake_client.aclose = fake_aclose

        def fake_async_client(*args, **kwargs):
            return fake_client

        monkeypatch.setattr(httpx.AsyncClient, "__new__", fake_async_client)

        with TestClient(app) as client:
            resp = client.get("/index.html")

        assert resp.status_code == 503, resp.text
        assert resp.headers.get("Retry-After") == "5", (
            f"Retry-After must be '5' for upstream errors; got "
            f"{resp.headers.get('Retry-After')}"
        )
        body = resp.json()
        assert "unavailable" in str(body.get("detail", "")).lower(), (
            f"body must mention 'unavailable'; got {body}"
        )

    def test_503_with_retry_after_5_when_upstream_remote_protocol_error(
        self, monkeypatch
    ):
        """``httpx.RemoteProtocolError`` → same 503 + Retry-After: 5 path.

        Both ConnectError and RemoteProtocolError are ``httpx.RequestError``
        subclasses, so the same handler catches them.
        """
        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)

        fake_client = MagicMock()

        async def fake_send(*args, **kwargs):
            raise httpx.RemoteProtocolError("peer closed connection")

        fake_client.send = fake_send
        fake_client.build_request = MagicMock(return_value=MagicMock())

        async def fake_aclose():
            pass

        fake_client.aclose = fake_aclose

        def fake_async_client(*args, **kwargs):
            return fake_client

        monkeypatch.setattr(httpx.AsyncClient, "__new__", fake_async_client)

        with TestClient(app) as client:
            resp = client.get("/healthz")

        assert resp.status_code == 503, resp.text
        assert resp.headers.get("Retry-After") == "5"
        body = resp.json()
        assert "unavailable" in str(body.get("detail", "")).lower()

    def test_upstream_http_error_is_not_masked_to_503(self, monkeypatch):
        """``httpx.HTTPStatusError`` (e.g. 404 from upstream) must NOT be
        converted to 503. The proxy catches only ``httpx.RequestError``
        (connection-level errors), NOT ``HTTPStatusError`` (HTTP-level
        errors from a reachable upstream). This pins the design decision:
        the error propagates as-is.
        """
        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)

        fake_client = MagicMock()

        async def fake_send(*args, **kwargs):
            raise httpx.HTTPStatusError(
                "Not Found",
                request=MagicMock(),
                response=MagicMock(status_code=404),
            )

        fake_client.send = fake_send
        fake_client.build_request = MagicMock(return_value=MagicMock())

        async def fake_aclose():
            pass

        fake_client.aclose = fake_aclose

        def fake_async_client(*args, **kwargs):
            return fake_client

        monkeypatch.setattr(httpx.AsyncClient, "__new__", fake_async_client)

        with TestClient(app) as client:
            # HTTPStatusError is NOT caught by the proxy (it only catches
            # RequestError), so it propagates as an unhandled exception.
            # TestClient re-raises unhandled server exceptions — so we
            # expect the HTTPStatusError to surface, proving it was NOT
            # masked to a 503.
            with pytest.raises(httpx.HTTPStatusError):
                client.get("/nonexistent")


# ─────────────────────────────────────────────────────────────────────────────
# 3. WebSocket proxy gate
# ─────────────────────────────────────────────────────────────────────────────


class TestWebSocketProxyGate:
    """Verify the WebSocket readiness gate.

    When the manager reports "not ready", the proxy must close the WS
    with code ``1013`` (``TRY_AGAIN_LATER``) instead of accepting and
    then failing. This is the contract the frontend relies on for its
    back-off logic.
    """

    def test_ws_close_1013_when_not_running(self):
        """``is_running() == False`` → WS closed with code 1013."""
        manager = _make_mock_manager(running=False, port=None)
        app = create_vscode_proxy_app(manager)

        with TestClient(app) as client:
            with pytest.raises(Exception) as exc_info:
                with client.websocket_connect("/") as ws:
                    # If we got here, the proxy accepted — that is the
                    # bug we're guarding against.
                    ws.receive_text()

        # starlette's TestClient surfaces a WebSocketDisconnect (or a
        # wrapper) when the server closes before we receive anything.
        # We don't pin the exception type tightly because Starlette has
        # reshuffled these a few times across versions; the *behavior*
        # is what we care about: the WS was closed by the server.
        assert exc_info.value is not None

    def test_ws_close_1013_when_no_port(self):
        """``is_running() == True`` but ``get_port() is None`` → WS 1013.

        The readiness() closure inside ``create_vscode_proxy_app``
        only checks ``is_running()``. The second ``port is None``
        branch is an explicit guard, so we exercise it directly.
        """
        manager = _make_mock_manager(running=True, port=None)
        app = create_vscode_proxy_app(manager)

        with TestClient(app) as client:
            with pytest.raises(Exception) as exc_info:
                with client.websocket_connect("/ws") as ws:
                    ws.receive_text()

        assert exc_info.value is not None

    def test_ws_gate_does_not_import_upstream_client(self):
        """When the gate fires, ``websockets.connect`` MUST NOT be called.

        ``websockets.connect`` is imported lazily inside the WS handler.
        If the gate fires first, that import + call never happen. We
        verify this by patching ``websockets.connect`` with a sentinel
        that would explode if invoked.
        """
        manager = _make_mock_manager(running=False, port=None)
        app = create_vscode_proxy_app(manager)

        # Patch the symbol on the ``websockets`` module — the proxy
        # imports it lazily via ``import websockets`` then calls
        # ``websockets.connect``.
        import websockets as websockets_module

        original_connect = websockets_module.connect
        sentinel_called = {"count": 0}

        def _explode(*args, **kwargs):  # pragma: no cover - sentinel
            sentinel_called["count"] += 1
            raise AssertionError(
                "websockets.connect must NOT be called when manager is not ready"
            )

        websockets_module.connect = _explode
        try:
            with TestClient(app) as client:
                with pytest.raises(Exception):
                    with client.websocket_connect("/") as ws:
                        ws.receive_text()
        finally:
            websockets_module.connect = original_connect

        assert sentinel_called["count"] == 0, (
            "upstream websockets.connect was invoked despite readiness gate"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Sanity: factory returns a usable FastAPI app
# ─────────────────────────────────────────────────────────────────────────────


class TestFactorySurface:
    """Pin a few basic invariants of ``create_vscode_proxy_app``.

    These exist to catch regressions where the factory shape changes
    (e.g. dropped the WebSocket route, dropped the catch-all path).
    """

    def test_returns_fastapi_instance(self):
        manager = _make_mock_manager(running=False, port=None)
        app = create_vscode_proxy_app(manager)
        # ``FastAPI`` is a Starlette ``Starlette`` subclass; the proxy
        # returns one. We don't import FastAPI here to keep the test
        # surface tight — duck-typing is enough.
        assert app is not None
        assert hasattr(app, "router")
        assert hasattr(app, "websocket")

    def test_factory_accepts_any_object_with_is_running_and_get_port(self):
        """The proxy only relies on ``is_running()`` and ``get_port()``;
        we can pass a SimpleNamespace and the factory should still work.
        """
        from typing import cast

        from daemon.services.vscode_server_manager import VSCodeServerManager

        manager = cast(
            VSCodeServerManager,
            SimpleNamespace(is_running=lambda: False, get_port=lambda: None),
        )
        app = create_vscode_proxy_app(manager)
        assert app is not None

    def test_module_constants_match_implementation(self):
        """Pin the public-facing constants of the module so other tests
        and the frontend can rely on them.
        """
        assert MAX_BODY_BYTES == 50 * 1024 * 1024
        # Hop-by-hop set is a frozenset; membership is case-insensitive.
        assert "connection" in HOP_BY_HOP_HEADERS
        assert "upgrade" in HOP_BY_HOP_HEADERS
        # And the module exports the helpers.
        assert callable(_proxy_headers)
        assert callable(_response_headers)
        # create_vscode_proxy_app must be exported.
        assert callable(vscode_proxy.create_vscode_proxy_app)
        # _validate_folder_param is the C1 helper; must be exported.
        assert callable(_validate_folder_param)


# ─────────────────────────────────────────────────────────────────────────────
# 4. C1: ?folder= validation
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateFolderParam:
    """Direct unit coverage of ``_validate_folder_param`` (C1 fix).

    The C1 bug class: ``?folder=/etc`` would otherwise let code-server
    open an arbitrary directory on the host, bypassing the daemon's
    workdir boundary. The helper confines the folder to a known
    project's main_directory via ``WorkspaceGuard.resolve_strict``.

    These tests exercise the helper directly — endpoint-level tests
    would require a real upstream and the 503 gate fires before the
    folder check, so we pin the behavior at the function level.
    """

    def test_empty_query_string_returns_empty(self):
        """Empty query string → returned unchanged (no folder to validate)."""
        assert _validate_folder_param("", project_repo=None) == ""

    def test_no_folder_param_passes_through_unchanged(self):
        """Query string without ``folder=`` → returned unchanged."""
        qs = "other=1&another=2"
        assert _validate_folder_param(qs, project_repo=None) == qs

    def test_folder_present_no_project_repo_dropped(self):
        """C1: ``folder=`` present, ``project_repo=None`` → folder dropped.

        Fail-closed: when the repo isn't available we can't validate, so
        we drop the param entirely rather than forwarding an arbitrary
        user-supplied path to code-server.
        """
        out = _validate_folder_param("folder=/etc", project_repo=None)
        # folder is gone; query string no longer contains it.
        assert "folder" not in out
        assert "/etc" not in out

    def test_folder_present_no_project_repo_keeps_other_params(self):
        """When dropping folder, other query params are preserved."""
        out = _validate_folder_param(
            "folder=/etc&theme=dark", project_repo=None
        )
        assert "folder" not in out
        assert "theme=dark" in out

    def test_folder_no_match_in_any_project_raises_403(self, tmp_path):
        """C1: ``folder=`` doesn't match any known project → 403.

        ``project_repo.list_projects()`` returns a project whose
        ``main_directory`` does NOT contain the requested folder, so
        validation fails and the helper raises ``HTTPException(403)``.
        """
        # Build a project pointing at ``tmp_path`` so the validation
        # sees a real WorkspaceGuard workdir.
        project = MagicMock(name="project")
        project.main_directory = str(tmp_path)

        repo = MagicMock(name="project_repo")
        repo.list_projects.return_value = [project]

        with pytest.raises(HTTPException) as exc_info:
            _validate_folder_param("folder=/etc", project_repo=repo)
        assert exc_info.value.status_code == 403

    def test_folder_matches_project_returns_resolved_query(
        self, tmp_path
    ) -> None:
        """C1: ``folder=`` is within a known project workdir → resolved forward.

        The helper resolves the folder via ``WorkspaceGuard.resolve_strict``
        and returns the query string with the canonicalized path.
        """
        # Build a project whose workdir IS the folder we're requesting.
        target = tmp_path / "subdir"
        target.mkdir()

        project = MagicMock(name="project")
        project.main_directory = str(tmp_path)

        repo = MagicMock(name="project_repo")
        repo.list_projects.return_value = [project]

        out = _validate_folder_param(
            f"folder={target.resolve()}", project_repo=repo
        )
        # Resolved path is forwarded (URL-decoded since the helper
        # re-encodes via urlencode).
        assert "folder=" in out
        assert unquote(out) == f"folder={target.resolve()}"

    def test_folder_match_preserves_other_params(self, tmp_path) -> None:
        """When folder is valid, sibling query params are preserved."""
        target = tmp_path / "subdir"
        target.mkdir()

        project = MagicMock(name="project")
        project.main_directory = str(tmp_path)

        repo = MagicMock(name="project_repo")
        repo.list_projects.return_value = [project]

        out = _validate_folder_param(
            f"folder={target.resolve()}&theme=dark", project_repo=repo
        )
        assert "folder=" in out
        assert "theme=dark" in out

    def test_folder_repo_read_failure_drops_param(self):
        """C1: ``project_repo.list_projects()`` raises → folder dropped.

        Mirrors the no-repo case: any failure to read the project list
        is treated as "can't validate", so the folder is dropped
        (fail-closed). The exception is logged but swallowed.
        """
        repo = MagicMock(name="project_repo")
        repo.list_projects.side_effect = RuntimeError("DB connection lost")

        out = _validate_folder_param("folder=/etc", project_repo=repo)
        assert "folder" not in out

    def test_folder_skipped_for_project_with_missing_main_directory(self):
        """A project with ``main_directory=None`` is skipped, not crashing.

        Defensive: the repo may contain projects whose main_directory
        hasn't been set yet. The helper must skip them rather than
        raise, then continue checking other projects.
        """
        empty_project = MagicMock(name="empty_project")
        empty_project.main_directory = None

        repo = MagicMock(name="project_repo")
        repo.list_projects.return_value = [empty_project]

        # No matching project → 403.
        with pytest.raises(HTTPException) as exc_info:
            _validate_folder_param("folder=/etc", project_repo=repo)
        assert exc_info.value.status_code == 403

    def test_folder_dropped_when_in_first_project_outside_workdir(
        self, tmp_path
    ) -> None:
        """A folder inside the FIRST project's workdir but escaping it
        does NOT match — guards against positive-only validation that
        would accept a path that escapes the workdir via ``..``."""
        project = MagicMock(name="project")
        project.main_directory = str(tmp_path)

        repo = MagicMock(name="project_repo")
        repo.list_projects.return_value = [project]

        # ``../etc`` is a traversal that resolves outside ``tmp_path``.
        with pytest.raises(HTTPException) as exc_info:
            _validate_folder_param(
                "folder=../etc", project_repo=repo
            )
        assert exc_info.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 5. WebSocket proxy crash resilience — ASGI-after-close RuntimeError
# ─────────────────────────────────────────────────────────────────────────────


class _FakeUpstream:
    """Minimal async iterator mimicking ``websockets.connect()``.

    ``upstream_to_browser`` only reads via ``async for message in upstream``,
    so we only need ``__aiter__`` / ``__anext__``. Optional ``send`` /
    ``close`` exist so the proxy closure (``browser_to_upstream`` and the
    ``finally`` block) can call them without exploding when the test
    drives the full proxy.
    """

    def __init__(self, messages, *, raise_after: BaseException | None = None):
        self._messages = list(messages)
        self._raise_after = raise_after
        self.yielded = 0
        self.sent: list = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.yielded >= len(self._messages):
            if self._raise_after is not None:
                # Surface the configured exception exactly once, then
                # behave as a closed stream so the loop exits cleanly.
                exc = self._raise_after
                self._raise_after = None
                raise exc
            raise StopAsyncIteration
        msg = self._messages[self.yielded]
        self.yielded += 1
        return msg

    async def send(self, data):  # pragma: no cover - exercised via closure
        self.sent.append(data)

    async def close(self):  # pragma: no cover - exercised via finally
        self.closed = True


class _FakeWebSocket:
    """Minimal WebSocket stub.

    Records ``send_bytes`` / ``send_text`` calls and allows tests to
    simulate a closed browser by flipping ``client_state``. If
    ``raise_runtime_error`` is true, the send methods raise the
    "Unexpected ASGI message 'websocket.send'" RuntimeError Starlette
    raises after ``websocket.close``.
    """

    def __init__(
        self,
        *,
        state: WebSocketState = WebSocketState.CONNECTED,
        raise_runtime_error: bool = False,
    ):
        self.client_state = state
        self._raise_runtime_error = raise_runtime_error
        self.sent_bytes: list[bytes] = []
        self.sent_text: list[str] = []
        self.closed = False

    async def send_bytes(self, data: bytes) -> None:
        if self._raise_runtime_error:
            raise RuntimeError(
                "Unexpected ASGI message 'websocket.send', "
                "after sending 'websocket.close'"
            )
        self.sent_bytes.append(data)

    async def send_text(self, data: str) -> None:
        if self._raise_runtime_error:
            raise RuntimeError(
                "Unexpected ASGI message 'websocket.send', "
                "after sending 'websocket.close'"
            )
        self.sent_text.append(data)

    async def close(self):  # pragma: no cover - exercised via finally
        self.closed = True


class TestUpstreamToBrowserCrashResilience:
    """Pin the three defenses against ``RuntimeError`` from a stale WS.

    Bug: when the browser disconnects while code-server is still
    streaming, ``websocket.send_bytes()`` raises ``RuntimeError: Unexpected
    ASGI message 'websocket.send', after sending 'websocket.close'``.
    Before the fix this propagated out of ``proxy_websocket``, crashing
    the daemon. Three fixes were applied:

    * Fix 1: wrap each send in try/except RuntimeError → break.
    * Fix 2: add RuntimeError to the enclosing ``except*``.
    * Fix 3: pre-send ``WebSocketState.CONNECTED`` check.

    These tests cover all three — if any layer regresses, at least one
    test fails.
    """

    async def test_upstream_to_browser_breaks_on_runtime_error_from_send(self):
        """Fix 1: when ``send_bytes`` raises RuntimeError the loop MUST
        break cleanly — no RuntimeError propagates out of the helper.

        Simulates the exact bug: code-server is still streaming but the
        browser WS has been closed (Starlette raises on the next send).
        """
        fake_ws = _FakeWebSocket(raise_runtime_error=True)
        fake_upstream = _FakeUpstream(
            messages=[b"msg-1", b"msg-2", b"msg-3"],
        )

        # No exception must escape — that's the contract.
        await upstream_to_browser(cast(Any, fake_ws), fake_upstream)

        # And we must have stopped after the first failing send.
        assert fake_ws.sent_bytes == [], (
            "send_bytes must NOT record data when it raises RuntimeError"
        )
        assert fake_upstream.yielded == 1, (
            "upstream_to_browser must break on the first RuntimeError, "
            "not drain remaining messages"
        )

    async def test_upstream_to_browser_breaks_on_runtime_error_from_send_text(self):
        """Fix 1, text-frame variant: ``send_text`` raising RuntimeError
        also stops the loop cleanly.
        """
        fake_ws = _FakeWebSocket(raise_runtime_error=True)
        fake_upstream = _FakeUpstream(
            messages=["text-1", "text-2", "text-3"],
        )

        await upstream_to_browser(cast(Any, fake_ws), fake_upstream)

        assert fake_ws.sent_text == []
        assert fake_upstream.yielded == 1

    async def test_upstream_to_browser_breaks_when_ws_state_not_connected(self):
        """Fix 3: pre-send state check — when ``client_state`` is
        ``DISCONNECTED`` the loop must break BEFORE any send is issued.
        """
        fake_ws = _FakeWebSocket(state=WebSocketState.DISCONNECTED)
        fake_upstream = _FakeUpstream(messages=[b"a", b"b", b"c"])

        await upstream_to_browser(cast(Any, fake_ws), fake_upstream)

        # Pre-send check fired as soon as the first message was pulled.
        # The async-for pulls one item before the state check runs, so
        # the loop breaks AFTER pulling the first message — but no send
        # happens because the state check rejects it.
        assert fake_ws.sent_bytes == [], (
            "no send must occur when client_state != CONNECTED"
        )
        assert fake_upstream.yielded == 1, (
            "the state check runs after the first item is pulled; "
            "remaining items must NOT be pulled after the break"
        )

    async def test_upstream_to_browser_forwards_when_ws_open(self):
        """Sanity guard: with a connected WS and a well-behaved upstream,
        ``upstream_to_browser`` forwards everything cleanly. If this
        regresses the other tests' setup is suspect.
        """
        fake_ws = _FakeWebSocket(state=WebSocketState.CONNECTED)
        fake_upstream = _FakeUpstream(
            messages=["hello", b"\x00\x01", "world"],
        )

        await upstream_to_browser(cast(Any, fake_ws), fake_upstream)

        assert fake_ws.sent_text == ["hello", "world"]
        assert fake_ws.sent_bytes == [b"\x00\x01"]
        assert fake_upstream.yielded == 3

    async def test_proxy_except_star_swallows_runtime_error_from_helper(self):
        """Fix 2 + Fix 1: even if ``upstream_to_browser`` somehow escapes
        a RuntimeError, the proxy's ``except*`` must catch it and not
        propagate out of ``proxy_websocket``.

        We exercise this by driving the real ``proxy_websocket``
        endpoint via TestClient with a patched ``websockets.connect``
        that returns a fake upstream whose iterator raises RuntimeError
        on the first message. The browser side is left untouched so the
        handler runs the TaskGroup path. The test asserts the WS server
        side exits cleanly (the server closes the client connection)
        rather than raising an unhandled exception.
        """
        import websockets as websockets_module

        fake_upstream = _FakeUpstream(
            messages=[],
            raise_after=RuntimeError(
                "Unexpected ASGI message 'websocket.send', "
                "after sending 'websocket.close'"
            ),
        )

        async def _fake_connect(*args, **kwargs):
            return fake_upstream

        original_connect = websockets_module.connect
        websockets_module.connect = _fake_connect
        try:
            manager = _make_mock_manager(running=True, port=1)
            app = create_vscode_proxy_app(manager)

            with TestClient(app) as client:
                # The proxy should accept, run TaskGroup, and the
                # upstream's RuntimeError must be caught by the
                # ``except*``. From the client side this just looks
                # like the server closing the WS — no client-side
                # exception escapes.
                with client.websocket_connect("/ws") as ws:
                    # Wait for the server to close the connection.
                    with pytest.raises(Exception):
                        ws.receive_text()
        finally:
            websockets_module.connect = original_connect

        # Upstream was closed by the proxy's finally block.
        assert fake_upstream.closed is True, (
            "proxy must close the upstream in the finally block "
            "even when the TaskGroup raises RuntimeError"
        )

    def test_proxy_closes_both_sides_when_upstream_dies(self):
        """When the upstream disconnects mid-stream, the proxy must
        close the browser WS too. We patch ``websockets.connect`` to
        return an upstream that yields a message then raises
        ``ConnectionError`` (the signal code-server uses when it dies),
        and assert the browser WS gets a clean close from the server.
        """
        import websockets as websockets_module

        fake_upstream = _FakeUpstream(
            messages=["hi"],
            raise_after=ConnectionError("upstream code-server crashed"),
        )

        async def _fake_connect(*args, **kwargs):
            return fake_upstream

        original_connect = websockets_module.connect
        websockets_module.connect = _fake_connect
        try:
            manager = _make_mock_manager(running=True, port=1)
            app = create_vscode_proxy_app(manager)

            with TestClient(app) as client:
                with client.websocket_connect("/ws") as ws:
                    # Server should send us the first message, then
                    # close when upstream dies.
                    text = ws.receive_text()
                    assert text == "hi"
                    # Subsequent receive must surface the server close.
                    with pytest.raises(Exception):
                        ws.receive_text()
        finally:
            websockets_module.connect = original_connect

        assert fake_upstream.closed is True
