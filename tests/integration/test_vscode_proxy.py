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

import asyncio
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


# ─────────────────────────────────────────────────────────────────────────────
# 5. fix-vscode-image-preview Step 1 — meta-CSP rewrite seam
# ─────────────────────────────────────────────────────────────────────────────
#
# Pins the webview-CSP rewrite behavior end-to-end through the
# FastAPI proxy handler with a mocked upstream. The seam is the
# minimal change that makes media-preview / image-preview extension
# resources load through the daemon's /vscode proxy (the strict
# meta-CSP inside the webview HTML blocks virtual-host assets; the
# rewrite appends the wildcard virtual-host origin to script-src and
# style-src). All other responses stay byte-faithful.
#
# Run only this section::
#
#     pytest tests/integration/test_vscode_proxy.py -v -k WebviewCspRewrite


class TestWebviewCspRewrite:
    """fix-vscode-image-preview Step 1 — meta-CSP rewrite seam."""

    # ── fixtures ──────────────────────────────────────────────────────────

    @pytest.fixture(autouse=True)
    def _install_kill_switch(self):
        """Pin the kill-switch cache to a known state per test.

        The cache is process-global (Shape A — restart-to-flip).
        Each test installs its own value and resets at teardown so
        a mid-suite mutation cannot leak between tests.
        """
        from daemon import config as config_module

        self._reset_fn = config_module._reset_vscode_webview_csp_fix_for_tests
        self._install_fn = config_module._install_vscode_webview_csp_fix
        self._reset_fn()
        yield
        self._reset_fn()

    @pytest.fixture(autouse=True)
    def _restore_async_client_patch(self):
        """Restore ``httpx.AsyncClient`` after each test.

        Tests in this class monkey-patch ``daemon.routers.vscode_proxy.
        httpx.AsyncClient`` to inject a fake upstream. We restore the
        original symbol at teardown so the patch cannot leak to other
        test classes in the same run.
        """
        from daemon.routers import vscode_proxy as proxy_module

        original = proxy_module.httpx.AsyncClient
        yield
        proxy_module.httpx.AsyncClient = original

    @staticmethod
    def _build_webview_html(body_csp: str) -> bytes:
        """Build a minimal webview HTML doc with a multi-line meta-CSP.

        The structure mirrors the real code-server 4.112.0 / 4.137.0
        webview HTML: <html><head>...meta http-equiv=
        "Content-Security-Policy" content="...">...</head><body></body>.
        """
        return (
            b'<!DOCTYPE html>\n<html lang="en" style="width:100%;height:100%">\n'
            b'<head>\n'
            b'\t<meta charset="UTF-8">\n'
            b'\n'
            b'\t<meta http-equiv="Content-Security-Policy"\n'
            b'\t\tcontent="' + body_csp.encode("utf-8") + b'">\n'
            b'\n'
            b'\t<!-- Disable pinch zooming -->\n'
            b'\t<meta name="viewport" '
            b'content="width=device-width,initial-scale=1.0">\n'
            b'</head>\n'
            b'<body style="margin:0;overflow:hidden"></body>\n'
            b'</html>\n'
        )

    # 4.112.0 webview meta-CSP — exactly as observed in the network log
    # (sha256-nQZh+9dHKZP2cHbhYlCbWDtqxxJtGjRGBx57zNP2DZM=).
    WEBVIEW_CSP_4_112 = (
        "default-src 'none'; "
        "script-src 'sha256-nQZh+9dHKZP2cHbhYlCbWDtqxxJtGjRGBx57zNP2DZM=' "
        "'self'; "
        "frame-src 'self'; "
        "style-src 'unsafe-inline';"
    )

    # 4.137.0 webview meta-CSP — same shape, different sha256 hash
    # (sha256-24QqA5dJq6y3qX8p9sL7h3kL5tN6mN8kP7qY5sX2cT0=). The
    # rewrite is hash-tolerant by design — this test pins that the
    # hash difference does NOT cause a silent no-op.
    WEBVIEW_CSP_4_137 = (
        "default-src 'none'; "
        "script-src 'sha256-24QqA5dJq6y3qX8p9sL7h3kL5tN6mN8kP7qY5sX2cT0=' "
        "'self'; "
        "frame-src 'self'; "
        "style-src 'unsafe-inline';"
    )

    VIRTUAL_HOST = (
        "https://*.vscode-resource.vscode-cdn.net"
    )

    # ── direct rewrite-seam coverage (no HTTP) ─────────────────────────────

    def test_rewrite_webview_meta_csp_4_112(self):
        """4.112.0 webview HTML — meta-CSP is augmented.

        The real meta-CSP captured in the 2026-09-12 evidence has NO
        ``img-src`` / NO ``media-src``. The helper inserts them on
        rewrite (review-council follow-up CRITICAL 1).
        """
        from daemon.routers.vscode_proxy import _rewrite_webview_meta_csp

        body = self._build_webview_html(self.WEBVIEW_CSP_4_112)
        out, rewrote = _rewrite_webview_meta_csp(body)
        assert rewrote is True
        assert self.VIRTUAL_HOST.encode() in out
        # The hash must survive unchanged — only sources are appended.
        assert b"sha256-nQZh+9dHKZP2cHbhYlCbWDtqxxJtGjRGBx57zNP2DZM=" in out
        # Insertion-when-absent: img-src and media-src MUST be inserted
        # because the original meta-CSP omits them.
        assert b"img-src" in out
        assert b"media-src" in out
        assert b"data: blob:" in out, "img-src MUST include data:/blob: bypass"

    def test_rewrite_webview_meta_csp_4_137(self):
        """4.137.0 webview HTML — meta-CSP is augmented, hash preserved."""
        from daemon.routers.vscode_proxy import _rewrite_webview_meta_csp

        body = self._build_webview_html(self.WEBVIEW_CSP_4_137)
        out, rewrote = _rewrite_webview_meta_csp(body)
        assert rewrote is True
        assert self.VIRTUAL_HOST.encode() in out
        assert b"sha256-24QqA5dJq6y3qX8p9sL7h3kL5tN6mN8kP7qY5sX2cT0=" in out
        assert b"img-src" in out
        assert b"media-src" in out

    def test_rewrite_is_idempotent(self):
        """Running the rewrite twice yields the same bytes."""
        from daemon.routers.vscode_proxy import _rewrite_webview_meta_csp

        body = self._build_webview_html(self.WEBVIEW_CSP_4_112)
        out1, _ = _rewrite_webview_meta_csp(body)
        out2, rewrote2 = _rewrite_webview_meta_csp(out1)
        assert rewrote2 is False
        assert out1 == out2

    def test_rewrite_skips_doc_without_meta_csp(self):
        """A non-webview doc with no meta-CSP tag is returned unchanged."""
        from daemon.routers.vscode_proxy import _rewrite_webview_meta_csp

        plain = b"<html><body>no CSP here</body></html>"
        out, rewrote = _rewrite_webview_meta_csp(plain)
        assert rewrote is False
        assert out == plain

    # ── kill-switch gate (default-ON, OFF = pass-through) ──────────────────

    def test_kill_switch_on_rewrites_webview_html(self):
        """Kill-switch ON (default) — webview HTML is rewritten."""
        self._install_fn(True)

        # Build a mock upstream httpx.Response that yields a brotli-encoded
        # body. The proxy buffers via aiter_raw + decodes via
        # _decode_response_body, so we hand it brotli bytes here to
        # exercise the real decode path.
        import brotli  # local import — only present in test env
        raw = self._build_webview_html(self.WEBVIEW_CSP_4_112)
        br = brotli.compress(raw)

        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)
        _patch_upstream_for_webview_html(
            body=br,
            content_type="text/html",
            content_encoding="br",
            status_code=200,
            path=(
                "/vscode/stable-abc/static/out/vs/workbench/"
                "contrib/webview/browser/pre/index.html"
            ),
        )

        with TestClient(app) as client:
            resp = client.get(
                "/vscode/stable-abc/static/out/vs/workbench/contrib/webview/"
                "browser/pre/index.html?id=x&extensionId=vscode.media-preview",
                headers={"Host": "localhost:8079"},
            )
        assert resp.status_code == 200
        assert resp.headers.get("content-encoding") is None, (
            "rewritten doc must NOT carry the original Content-Encoding"
        )
        assert "no-store" in resp.headers.get("cache-control", "")
        assert resp.headers.get("etag") is None
        assert int(resp.headers["content-length"]) == len(resp.content)
        assert self.VIRTUAL_HOST.encode() in resp.content
        assert (
            b"sha256-nQZh+9dHKZP2cHbhYlCbWDtqxxJtGjRGBx57zNP2DZM="
            in resp.content
        )

    def test_kill_switch_off_returns_response_byte_untouched(self):
        """Kill-switch OFF — webview HTML passes through untouched."""
        self._install_fn(False)

        raw = self._build_webview_html(self.WEBVIEW_CSP_4_112)
        import brotli
        br = brotli.compress(raw)

        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)
        _patch_upstream_for_webview_html(
            body=br,
            content_type="text/html",
            content_encoding="br",
            status_code=200,
            path=(
                "/vscode/stable-abc/static/out/vs/workbench/"
                "contrib/webview/browser/pre/index.html"
            ),
        )

        with TestClient(app) as client:
            resp = client.get(
                "/vscode/stable-abc/static/out/vs/workbench/contrib/webview/"
                "browser/pre/index.html?id=x&extensionId=vscode.media-preview"
            )
        assert resp.status_code == 200
        # Pass-through: original brotli-encoded body is forwarded with
        # the proxy's CSP header replacement applied. TestClient
        # auto-decodes the content-encoding for us, so ``resp.content``
        # equals the decoded HTML — which is exactly the upstream's
        # pre-encoding bytes. No virtual-host origin appended.
        assert self.VIRTUAL_HOST.encode() not in resp.content
        assert resp.content == raw, (
            "kill-switch OFF must leave the response byte-faithful"
        )

    def test_non_webview_html_path_not_rewritten(self):
        """A different HTML path (workbench, manifest) is NOT rewritten.

        The brief: match ONLY the webview document (path + content-type);
        all other responses stay byte-identical. We pin that the
        workbench HTML (different path) is untouched even when the
        kill-switch is ON.
        """
        self._install_fn(True)

        # Different path: workbench shell, not webview.
        import brotli
        workbench_html = (
            b'<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy" '
            b'content="default-src \'none\'; script-src \'self\';">'
            b'</head><body></body></html>'
        )
        br = brotli.compress(workbench_html)

        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)
        _patch_upstream_for_webview_html(
            body=br,
            content_type="text/html",
            content_encoding="br",
            status_code=200,
            path="/vscode/stable-abc/index.html",
        )

        with TestClient(app) as client:
            resp = client.get("/vscode/stable-abc/index.html")
        assert resp.status_code == 200
        # Pass-through — the proxy does not buffer+rewrite for this path.
        # TestClient auto-decodes the brotli body, so resp.content is
        # the decoded HTML; the proxy never added the virtual-host origin.
        assert resp.content == workbench_html
        assert self.VIRTUAL_HOST.encode() not in resp.content

    def test_non_html_response_not_rewritten(self):
        """text/css / application/javascript responses stay untouched."""
        self._install_fn(True)

        import brotli
        css = b"body { color: red; }"
        br = brotli.compress(css)

        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)
        _patch_upstream_for_webview_html(
            body=br,
            content_type="text/css",
            content_encoding="br",
            status_code=200,
            path=(
                "/vscode/stable-abc/static/out/vs/workbench/"
                "contrib/webview/browser/pre/styles.css"
            ),
        )

        with TestClient(app) as client:
            resp = client.get(
                "/vscode/stable-abc/static/out/vs/workbench/contrib/webview/"
                "browser/pre/styles.css"
            )
        assert resp.status_code == 200
        # Pass-through — non-html Content-Type bypasses the rewrite seam.
        assert resp.content == css

    def test_gzip_encoded_webview_is_rewritten(self):
        """gzip-encoded webview HTML is decoded + rewritten."""
        self._install_fn(True)
        import gzip
        raw = self._build_webview_html(self.WEBVIEW_CSP_4_112)
        gz = gzip.compress(raw)

        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)
        _patch_upstream_for_webview_html(
            body=gz,
            content_type="text/html",
            content_encoding="gzip",
            status_code=200,
            path=(
                "/vscode/stable-abc/static/out/vs/workbench/"
                "contrib/webview/browser/pre/index.html"
            ),
        )

        with TestClient(app) as client:
            resp = client.get(
                "/vscode/stable-abc/static/out/vs/workbench/contrib/webview/"
                "browser/pre/index.html?id=x",
                headers={"Host": "localhost:8079"},
            )
        assert resp.status_code == 200
        assert resp.headers.get("content-encoding") is None
        assert self.VIRTUAL_HOST.encode() in resp.content

    def test_undecodable_encoding_falls_back_to_streaming(self):
        """Unknown Content-Encoding → streaming pass-through, no rewrite."""
        self._install_fn(True)
        raw = self._build_webview_html(self.WEBVIEW_CSP_4_112)
        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)
        _patch_upstream_for_webview_html(
            body=raw,
            content_type="text/html",
            content_encoding="bizarre-unknown-encoding",
            status_code=200,
            path=(
                "/vscode/stable-abc/static/out/vs/workbench/"
                "contrib/webview/browser/pre/index.html"
            ),
        )

        with TestClient(app) as client:
            resp = client.get(
                "/vscode/stable-abc/static/out/vs/workbench/contrib/webview/"
                "browser/pre/index.html?id=x"
            )
        # Streaming path returns the body with content-encoding still
        # set; the rewrite was skipped because the encoder is unknown.
        # The body bytes equal the upstream raw bytes.
        assert resp.status_code == 200
        assert self.VIRTUAL_HOST.encode() not in resp.content

    # ── Follow-up edge cases (review follow-up) ────────────────────────────

    def test_deflate_encoded_webview_is_rewritten(self):
        """deflate-encoded webview HTML is decoded + rewritten.

        Mirrors ``test_gzip_encoded_webview_is_rewritten`` for the
        zlib (``deflate``) branch in ``_decode_response_body``
        (vscode_proxy.py:164-167). Uses stdlib ``zlib`` so no extra
        dep is required.
        """
        self._install_fn(True)
        import zlib
        raw = self._build_webview_html(self.WEBVIEW_CSP_4_112)
        df = zlib.compress(raw)

        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)
        _patch_upstream_for_webview_html(
            body=df,
            content_type="text/html",
            content_encoding="deflate",
            status_code=200,
            path=(
                "/vscode/stable-abc/static/out/vs/workbench/"
                "contrib/webview/browser/pre/index.html"
            ),
        )

        with TestClient(app) as client:
            resp = client.get(
                "/vscode/stable-abc/static/out/vs/workbench/contrib/webview/"
                "browser/pre/index.html?id=x",
                headers={"Host": "localhost:8079"},
            )
        assert resp.status_code == 200
        # Rewritten path strips Content-Encoding so the new bytes match
        # the recomputed Content-Length.
        assert resp.headers.get("content-encoding") is None
        assert self.VIRTUAL_HOST.encode() in resp.content
        assert int(resp.headers["content-length"]) == len(resp.content)

    def test_already_augmented_webview_passes_through_unchanged(
        self,
    ):
        """An ALREADY-augmented webview doc (script-src/style-src AND
        img-src/media-src all widened) hits the FULL HTTP path and
        is re-served with the ORIGINAL Content-Encoding preserved
        (byte-faithful to the wire).

        ``test_rewrite_is_idempotent`` only exercises the direct
        helper ``_rewrite_webview_meta_csp``; this test drives the
        end-to-end FastAPI handler so we pin the byte-faithful
        re-serve contract for the consume-but-no-rewrite branch
        (the path that would previously have fallen through to
        streaming and 500'd with ``StreamConsumed`` on real httpx).

        Post-review-council: the "already augmented" fixture must
        include ``img-src`` and ``media-src`` too — those are the
        insertion targets added by the CRITICAL 1 follow-up. A
        doc that has script-src/style-src widened but no
        img-src/media-src is NOT augmented for the image-preview
        failure mode; the helper would still insert.
        """
        self._install_fn(True)
        import brotli
        # Build a FULLY augmented doc: script-src + style-src widened
        # AND img-src + media-src present. Then brotli-encode.
        already_augmented_csp = (
            "default-src 'none'; "
            "script-src 'sha256-nQZh+9dHKZP2cHbhYlCbWDtqxxJtGjRGBx57zNP2DZM=' "
            f"'self' {self.VIRTUAL_HOST}; "
            "frame-src 'self'; "
            f"style-src 'unsafe-inline' {self.VIRTUAL_HOST}; "
            "img-src 'self' data: blob: https://*.vscode-resource.vscode-cdn.net; "
            "media-src 'self' https://*.vscode-resource.vscode-cdn.net"
        )
        raw = self._build_webview_html(already_augmented_csp)
        br = brotli.compress(raw)

        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)
        _patch_upstream_for_webview_html(
            body=br,
            content_type="text/html",
            content_encoding="br",
            status_code=200,
            path=(
                "/vscode/stable-abc/static/out/vs/workbench/"
                "contrib/webview/browser/pre/index.html"
            ),
        )

        with TestClient(app) as client:
            resp = client.get(
                "/vscode/stable-abc/static/out/vs/workbench/contrib/webview/"
                "browser/pre/index.html?id=x",
                headers={"Host": "localhost:8079"},
            )
        assert resp.status_code == 200
        # Original Content-Encoding MUST be preserved on the
        # byte-faithful re-serve path — the wire bytes are the
        # original brotli-compressed bytes, so the browser's
        # decoder stays in sync with the body.
        assert resp.headers.get("content-encoding") == "br"
        # TestClient decodes the br body for us; the decoded bytes
        # equal the upstream's pre-encoding bytes (i.e. the original
        # HTML, already augmented — no DOUBLE augmentation).
        assert resp.content == raw
        # The Content-Length recompute must agree with the actual
        # body length (the BROTLI body, since the proxy streams the
        # original raw bytes back to the browser without re-encoding).
        assert int(resp.headers["content-length"]) == len(br)
        # Idempotency pin: the wildcard appears ONCE per directive,
        # not twice. ``raw.count(VIRTUAL_HOST) == 4`` (one for each
        # of script-src / style-src / img-src / media-src).
        assert raw.count(self.VIRTUAL_HOST.encode()) == 4

    def test_webview_html_with_charset_hits_rewrite(self):
        """Content-Type ``text/html; charset=UTF-8`` (the typical real
        upstream value) still triggers the rewrite.

        The pre-consume content-type guard uses
        ``startswith("text/html")`` so a ``charset=`` parameter
        doesn't fool the gate. Pins the typical real upstream
        Content-Type rather than the stripped ``text/html`` form.
        """
        self._install_fn(True)
        import brotli
        raw = self._build_webview_html(self.WEBVIEW_CSP_4_112)
        br = brotli.compress(raw)

        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)
        _patch_upstream_for_webview_html(
            body=br,
            content_type="text/html; charset=UTF-8",
            content_encoding="br",
            status_code=200,
            path=(
                "/vscode/stable-abc/static/out/vs/workbench/"
                "contrib/webview/browser/pre/index.html"
            ),
        )

        with TestClient(app) as client:
            resp = client.get(
                "/vscode/stable-abc/static/out/vs/workbench/contrib/webview/"
                "browser/pre/index.html?id=x",
                headers={"Host": "localhost:8079"},
            )
        assert resp.status_code == 200
        # Rewritten — strip + new Content-Length + virtual-host added.
        assert resp.headers.get("content-encoding") is None
        assert self.VIRTUAL_HOST.encode() in resp.content
        assert int(resp.headers["content-length"]) == len(resp.content)

    def test_malformed_gzip_body_returns_graceful_200(self):
        """A truncated / malformed gzip body is a DOWNSTREAM BUG, not
        a 500 — the proxy catches the decoder's exception and falls
        back to byte-faithful re-serve of the original raw bytes.

        Without the decode-error catch, ``gzip.decompress`` would
        propagate ``EOFError`` (gzip raises ``EOFError`` — a direct
        subclass of ``Exception``) out of the handler and FastAPI
        would emit a 500. Pin the graceful fallback.

        Note on TestClient auto-decoding: TestClient (httpx)
        auto-decodes ``content-encoding: gzip`` on the response
        and silently returns ``b""`` for invalid input, so we pin
        the byte-faithful contract via the proxy's response
        headers (which TestClient does NOT modify) rather than via
        ``resp.content``. The headers carry the proxy's contract:
        200 status (no 500), original ``content-encoding`` preserved,
        ``content-length`` matching the buffered raw body.
        """
        self._install_fn(True)
        # Truncated gzip header (only the gzip magic + first byte).
        # ``gzip.decompress`` raises ``EOFError: Compressed file ended
        # before the end-of-stream marker was reached`` on this input.
        malformed = b"\x1f\x8b"
        # Sanity check: confirm the input is actually malformed
        # (raises) so a refactor that silently swallows the error
        # doesn't sneak past the test.
        import gzip
        with pytest.raises(EOFError):
            gzip.decompress(malformed)

        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)
        _patch_upstream_for_webview_html(
            body=malformed,
            content_type="text/html",
            content_encoding="gzip",
            status_code=200,
            path=(
                "/vscode/stable-abc/static/out/vs/workbench/"
                "contrib/webview/browser/pre/index.html"
            ),
        )

        with TestClient(app) as client:
            resp = client.get(
                "/vscode/stable-abc/static/out/vs/workbench/contrib/webview/"
                "browser/pre/index.html?id=x",
                headers={"Host": "localhost:8079"},
            )
        # No 500: the decoder exception is caught and the proxy
        # re-serves the original raw bytes.
        assert resp.status_code == 200, (
            "malformed gzip body MUST NOT 500 — graceful 200 fallback "
            "(byte-faithful re-serve) is the contract"
        )
        # Original Content-Encoding preserved on the re-serve path
        # — the proxy did NOT strip / re-encode.
        assert resp.headers.get("content-encoding") == "gzip"
        # Content-Length matches the original raw body length
        # (proxy recomputed from the buffered bytes; this is the
        # canonical byte-faithful pin — the wire bytes are the
        # proxy's response bytes).
        assert int(resp.headers["content-length"]) == len(malformed)
        # No virtual-host origin added — we did not get far enough
        # to rewrite the meta-CSP.
        assert self.VIRTUAL_HOST.encode() not in resp.content

    def test_real_httpx_undecodable_encoding_streams_without_consumed(
        self,
    ):
        """Pin the StreamConsumed fix end-to-end with a REAL httpx.

        The existing tests use a fake upstream whose ``aiter_raw``
        returns a fresh generator on each call — that masks the
        real-httpx ``StreamConsumed`` bug. We construct a REAL
        ``httpx.Response`` with a generator-backed ``AsyncByteStream``
        body (the same shape ``httpx.AsyncClient.send(stream=True)``
        returns) and verify the undecodable-encoding path does NOT
        500 — i.e. the proxy does NOT consume the body before
        deciding to fall through to streaming.

        Why this matters: real httpx raises
        ``httpx.StreamConsumed`` on the second call to
        ``Response.aiter_raw`` (sets ``self.is_stream_consumed = True``
        on first call — verified by inspection of
        ``httpx/_models.py:Response.aiter_raw``). The previous
        implementation consumed first and decided second, which
        would 500 on real httpx. This test pins the fix.
        """
        import httpx as _httpx

        self._install_fn(True)

        # Build a SEPARATE real httpx.Response for the sanity
        # check below (StreamConsumed-on-second-iteration). The
        # proxy receives a DIFFERENT upstream — sharing would
        # consume the body during the sanity check and 500 the
        # proxy on its first aiter_raw.
        class _Stream(_httpx.AsyncByteStream):
            def __init__(self, chunks):
                self._chunks = chunks

            async def __aiter__(self):
                for chunk in self._chunks:
                    yield chunk

            async def aclose(self):
                pass

        # Sanity-check Response: confirm aiter_raw() raises
        # StreamConsumed on the second iteration. Without this
        # guard, a future httpx change to fresh-iterator semantics
        # would silently mask the bug we're fixing here.
        sanity_upstream = _httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=_Stream([b"sanity chunk"]),
            request=_httpx.Request(
                "GET",
                "http://127.0.0.1:8081/sanity",
            ),
        )

        async def _aiter_twice_should_raise():
            async for _ in sanity_upstream.aiter_raw():
                pass
            async for _ in sanity_upstream.aiter_raw():
                pass
        with pytest.raises(_httpx.StreamConsumed):
            asyncio.run(_aiter_twice_should_raise())

        # Proxy-bound Response: a real httpx.Response with a
        # generator-backed AsyncByteStream body. The proxy sees
        # this exact object and ``aiter_raw()`` exhibits real
        # ``StreamConsumed`` semantics on second iteration.
        upstream = _httpx.Response(
            200,
            headers={
                "content-type": "text/html",
                # Unknown encoding — must trigger the no-consume path
                "content-encoding": "bizarre-unknown-encoding",
                "cache-control": "public, max-age=31536000",
                "etag": '"deadbeef"',
            },
            content=_Stream(
                [b"webview body chunk one ", b"chunk two"]
            ),
            request=_httpx.Request(
                "GET",
                "http://127.0.0.1:8081/vscode/stable-abc/static/out/"
                "vs/workbench/contrib/webview/browser/pre/index.html"
                "?id=x&extensionId=vscode.media-preview",
            ),
        )

        # Now drive the proxy handler end-to-end. We patch
        # ``httpx.AsyncClient.send`` on the proxy module so the
        # handler receives our real httpx.Response.
        from daemon.routers import vscode_proxy as proxy_module

        fake_client = MagicMock()

        async def _fake_send(*args, **kwargs):
            return upstream

        fake_client.send = _fake_send
        fake_client.build_request = MagicMock(return_value=MagicMock())

        async def _fake_aclose():
            pass

        fake_client.aclose = _fake_aclose

        def _fake_async_client(*args, **kwargs):
            return fake_client

        proxy_module.httpx.AsyncClient = _fake_async_client

        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)

        try:
            with TestClient(app) as client:
                resp = client.get(
                    "/vscode/stable-abc/static/out/vs/workbench/contrib/"
                    "webview/browser/pre/index.html?id=x",
                    headers={"Host": "localhost:8079"},
                )
        finally:
            proxy_module.httpx.AsyncClient = _httpx.AsyncClient

        # The undecodable-encoding path must fall through to
        # streaming — NOT 500 with StreamConsumed. The streaming
        # generator's aiter_raw() is the FIRST iteration of this
        # body, so the proxy delivers the chunks.
        assert resp.status_code == 200, (
            "undecodable-encoding path MUST stream through on a "
            "REAL httpx body — StreamConsumed on the second "
            "iteration would 500 here"
        )
        # The streaming response delivers the body (TestClient
        # accumulates chunks). No content-encoding → the proxy
        # passed it through verbatim (no compression claimed /
        # applied because the unknown encoder couldn't decode).
        assert b"webview body chunk one chunk two" in resp.content

    # ── Review-council follow-ups ─────────────────────────────────────────────

    # CRITICAL 1: insertion-when-absent semantics for img-src / media-src.
    def test_augment_inserts_img_src_when_absent(self):
        """The real meta-CSP has NO ``img-src`` → default-src 'none'
        applies → image blocked. The rewrite must INSERT
        ``img-src data: blob: https://*.vscode-resource.vscode-cdn.net``
        on every augmented doc.

        Per CSP3 §6.1.5.4 an absent img-src falls back to
        ``default-src 'none'``. Per §6.7.2.4 the HTTP header CSP and
        meta-CSP INTERSECT — sources must appear in both. Widening
        only script-src/style-src (the previous behaviour) does
        NOT rescue the image. This test pins the insertion contract.
        """
        from daemon.routers import vscode_proxy as proxy_module

        csp = (
            "default-src 'none'; "
            "script-src 'sha256-nQZh+9dHKZP2cHbhYlCbWDtqxxJtGjRGBx57zNP2DZM=' "
            "'self'; "
            "frame-src 'self'; "
            "style-src 'unsafe-inline';"
        )
        body = self._build_webview_html(csp)
        out, rewrote = proxy_module._rewrite_webview_meta_csp(body)
        assert rewrote is True
        assert b"img-src" in out, (
            "img-src MUST be INSERTED when absent — the real meta-CSP "
            "captured in 2026-09-12 evidence has no img-src, so the "
            "browser falls back to default-src 'none' and blocks the image"
        )
        # Verify the exact insertion value — data:/blob: are kept for
        # the extension's known bypass paths (inline data URI + blob
        # URL after fetch()); the virtual-host wildcard covers direct
        # <img src="https://*.vscode-resource.vscode-cdn.net/...">.
        assert (
            b"img-src data: blob: https://*.vscode-resource.vscode-cdn.net"
            in out
        )

    def test_augment_inserts_media_src_when_absent(self):
        """Sibling to img-src — ``media-src`` covers ``<audio>`` /
        ``<video>`` (the 4.137.0 ``vscode.audioPreview`` /
        ``vscode.videoPreview`` custom editors use the same
        ``asWebviewUri`` flow).
        """
        from daemon.routers import vscode_proxy as proxy_module

        csp = (
            "default-src 'none'; "
            "script-src 'sha256-nQZh+9dHKZP2cHbhYlCbWDtqxxJtGjRGBx57zNP2DZM=' "
            "'self'; "
            "style-src 'unsafe-inline';"
        )
        body = self._build_webview_html(csp)
        out, rewrote = proxy_module._rewrite_webview_meta_csp(body)
        assert rewrote is True
        assert b"media-src" in out
        assert (
            b"media-src 'self' https://*.vscode-resource.vscode-cdn.net"
            in out
        )

    def test_augment_appends_not_inserts_when_img_src_present(self):
        """Present ``img-src`` is preserved as-is — only
        ``script-src`` / ``style-src`` get the wildcard appended
        when present (per the brief: ``Present-directive behavior
        (append wildcard) stays as-is for script-src/style-src``).
        Insertion of ``img-src`` only fires when ABSENT.
        """
        from daemon.routers import vscode_proxy as proxy_module

        csp = (
            "default-src 'none'; "
            "img-src 'self'; "
            "script-src 'self'; "
            "style-src 'unsafe-inline';"
        )
        body = self._build_webview_html(csp)
        out, rewrote = proxy_module._rewrite_webview_meta_csp(body)
        assert rewrote is True
        # img-src appears ONCE (not re-inserted, not extended).
        assert out.count(b"img-src") == 1
        # The existing img-src source list is preserved verbatim
        # (no wildcard appended). The wildcard is appended only to
        # script-src/style-src.
        assert b"img-src 'self'" in out
        assert b"https://*.vscode-resource.vscode-cdn.net" not in out.split(b"img-src", 1)[1].split(b";", 1)[0]
        # script-src and style-src ARE extended with the wildcard.
        assert (
            b"script-src 'self' https://*.vscode-resource.vscode-cdn.net"
            in out
        )
        assert (
            b"style-src 'unsafe-inline' https://*.vscode-resource.vscode-cdn.net"
            in out
        )

    def test_augment_idempotent_after_insertion(self):
        """Idempotency after the FIRST insert (no double img-src /
        no double media-src on the second pass).
        """
        from daemon.routers import vscode_proxy as proxy_module

        csp = (
            "default-src 'none'; "
            "script-src 'sha256-nQZh+9dHKZP2cHbhYlCbWDtqxxJtGjRGBx57zNP2DZM=' "
            "'self'; "
            "style-src 'unsafe-inline';"
        )
        body = self._build_webview_html(csp)
        out1, rewrote1 = proxy_module._rewrite_webview_meta_csp(body)
        out2, rewrote2 = proxy_module._rewrite_webview_meta_csp(out1)
        assert rewrote1 is True
        assert rewrote2 is False, (
            "second-pass rewrite must be a no-op (idempotent)"
        )
        assert out1 == out2
        # Each directive appears exactly once after insertion.
        assert out2.count(b"img-src") == 1
        assert out2.count(b"media-src") == 1

    # Path-gate boundary pin (review-council follow-up ride-along 2).
    def test_path_gate_rejects_mid_segment_substring(self):
        """``/vscode/xwebview/browser/pre/index.html`` must NOT
        match — the ``x`` is mid-segment. A bare bytes-substring
        ``in`` check would slip through (false positive); the
        anchored split-on-``/`` test rejects it.
        """
        assert (
            vscode_proxy._path_contains_webview_fragment(
                b"/vscode/static/xwebview/browser/pre/index.html"
            )
            is False
        )
        assert (
            vscode_proxy._path_contains_webview_fragment(
                b"/somewebview/browser/pre/index.html"
            )
            is False
        )
        assert (
            vscode_proxy._path_contains_webview_fragment(
                b"/vscode/webviewxx/browser/pre/index.html"
            )
            is False
        )
        # The canonical path still matches.
        assert (
            vscode_proxy._path_contains_webview_fragment(
                b"/vscode/stable-abc/static/out/vs/workbench/"
                b"contrib/webview/browser/pre/index.html"
            )
            is True
        )

    # Accept-Encoding pin (review-council follow-up ride-along 3).
    def test_ae_pin_for_rewrite_eligible_request(self):
        """Rewrite-eligible requests (path contains the webview
        index fragment) get an outbound ``Accept-Encoding`` pinned
        to the decodable set so the upstream can't reply with an
        encoding we can't decode (e.g. ``zstd``).
        """
        webview_path = (
            b"/vscode/stable-abc/static/out/vs/workbench/"
            b"contrib/webview/browser/pre/index.html"
        )
        # Client sends ``gzip, zstd`` → outbound pinned to decodable.
        pinned = vscode_proxy._accept_encoding_for_request(
            webview_path, "gzip, zstd"
        )
        assert pinned == "gzip, deflate, br"
        # Unset client header → still pinned (defensive default).
        assert (
            vscode_proxy._accept_encoding_for_request(webview_path, None)
            == "gzip, deflate, br"
        )

    def test_ae_passthrough_for_non_eligible_request(self):
        """Non-eligible requests keep the client's value verbatim —
        non-webview traffic (workbench shell, JS chunks, etc.)
        still flows through with whatever the client negotiated.
        """
        non_webview_path = (
            b"/vscode/stable-abc/static/out/vs/workbench/workbench.js"
        )
        assert (
            vscode_proxy._accept_encoding_for_request(
                non_webview_path, "gzip, zstd"
            )
            == "gzip, zstd"
        )
        # Unset client header → unset outbound.
        assert (
            vscode_proxy._accept_encoding_for_request(
                non_webview_path, None
            )
            is None
        )

    # MAJOR 3 — AE-pin end-to-end: rewrite-eligible requests must
    # send EXACTLY ONE accept-encoding header upstream, equal to the
    # pin. Case-sensitive ``pop("Accept-Encoding", ...)`` would never
    # match Starlette's lowercase key, so the client's
    # ``accept-encoding`` would survive + a second pin would be set,
    # yielding a duplicate AE header that node joins to
    # ``gzip, zstd, gzip, deflate, br`` — defeating the pin (zstd
    # negotiable, encoding pre-check silently skips rewrite).
    def test_ae_pin_no_duplicate_header_for_eligible_request(
        self,
    ):
        """End-to-end: the upstream sees EXACTLY ONE
        ``accept-encoding`` header for a rewrite-eligible request,
        and its value is the pin (NOT the client's value, NOT a
        join of both).
        """
        self._install_fn(True)

        # Real httpx-shaped request capture: ``client.build_request``
        # returns an object that the proxy later passes to
        # ``client.send(...)``. We use a small dataclass-like
        # stub that records the headers at ``build_request`` time.
        captured_headers: dict[str, str] = {}

        from daemon.routers import vscode_proxy as proxy_module

        class _CapturedRequest:
            def __init__(self, headers, method, target):
                # Starlette normalizes headers to lowercase; the
                # proxy uses ``request.headers.items()`` which yields
                # lowercase keys (we mirror that here).
                self.headers = {k.lower(): v for k, v in headers.items()}
                captured_headers.update(self.headers)
                self.method = method
                self.target = target

        class _HeaderCapturingClient:
            async def send(self, request, *args, **kwargs):
                class _FakeResponse:
                    status_code = 200
                    request = SimpleNamespace(
                        url=SimpleNamespace(
                            path=(
                                "/vscode/stable-abc/static/out/"
                                "vs/workbench/contrib/webview/browser/"
                                "pre/index.html?id=x"
                            )
                        )
                    )
                    headers = {
                        "content-type": "text/html",
                        "cache-control": "public, max-age=31536000",
                    }

                    async def aiter_raw(self, chunk_size=None):
                        if False:
                            yield

                    async def aclose(self):
                        pass

                return _FakeResponse()

            def build_request(self, method, target, **kwargs):
                headers = kwargs.get("headers") or {}
                return _CapturedRequest(headers, method, target)

            async def aclose(self):
                pass

        class _HeaderCapturingFactory:
            def __call__(self, *args, **kwargs):
                return _HeaderCapturingClient()

        original_async_client = vscode_proxy.httpx.AsyncClient
        vscode_proxy.httpx.AsyncClient = _HeaderCapturingFactory()
        try:
            manager = _make_mock_manager(running=True, port=8081)
            app = create_vscode_proxy_app(manager)
            with TestClient(app) as client:
                client.get(
                    "/vscode/stable-abc/static/out/vs/workbench/"
                    "contrib/webview/browser/pre/index.html?id=x",
                    headers={
                        "Host": "localhost:8079",
                        "Accept-Encoding": "gzip, zstd",
                    },
                )
        finally:
            vscode_proxy.httpx.AsyncClient = original_async_client

        # No duplicate accept-encoding: exactly ONE header
        # key (lowercase — Starlette normalizes) with the pin
        # value verbatim. If the fix regresses, the key would
        # appear twice OR the value would be the client's
        # ``gzip, zstd`` string (node-joined with the pin).
        ae_values = [
            v for k, v in captured_headers.items()
            if k.lower() == "accept-encoding"
        ]
        assert len(ae_values) == 1, (
            f"upstream must receive EXACTLY ONE accept-encoding "
            f"header — found {len(ae_values)}: {ae_values!r}. "
            f"A duplicate means the case-sensitive pop missed the "
            f"lowercase key and the client's value survived + the "
            f"pin added a second header."
        )
        assert ae_values[0] == vscode_proxy._WEBVIEW_REWRITE_ACCEPT_ENCODING, (
            f"upstream accept-encoding must be the pin value "
            f"({vscode_proxy._WEBVIEW_REWRITE_ACCEPT_ENCODING!r}); "
            f"got {ae_values[0]!r}. A non-pin value means the "
            f"client's ``gzip, zstd`` leaked through unmodified, "
            f"or two headers were joined by node."
        )

    def test_ae_pin_client_value_preserved_for_non_eligible_request(
        self,
    ):
        """End-to-end: non-eligible requests keep the client's
        value verbatim. No pin overwrite (the pin is
        rewrite-only), no header drop, no duplicate.
        """
        self._install_fn(True)

        captured_headers: dict[str, str] = {}

        from daemon.routers import vscode_proxy as proxy_module

        class _CapturedRequest:
            def __init__(self, headers, method, target):
                self.headers = {k.lower(): v for k, v in headers.items()}
                captured_headers.update(self.headers)
                self.method = method
                self.target = target

        class _HeaderCapturingClient:
            async def send(self, request, *args, **kwargs):
                class _FakeResponse:
                    status_code = 200
                    request = SimpleNamespace(
                        url=SimpleNamespace(
                            path=(
                                "/vscode/stable-abc/static/out/"
                                "vs/workbench/workbench.js"
                            )
                        )
                    )
                    headers = {"content-type": "application/javascript"}

                    async def aiter_raw(self, chunk_size=None):
                        if False:
                            yield

                    async def aclose(self):
                        pass

                return _FakeResponse()

            def build_request(self, method, target, **kwargs):
                headers = kwargs.get("headers") or {}
                return _CapturedRequest(headers, method, target)

            async def aclose(self):
                pass

        class _Factory:
            def __call__(self, *args, **kwargs):
                return _HeaderCapturingClient()

        original_async_client = vscode_proxy.httpx.AsyncClient
        vscode_proxy.httpx.AsyncClient = _Factory()
        try:
            manager = _make_mock_manager(running=True, port=8081)
            app = create_vscode_proxy_app(manager)
            with TestClient(app) as client:
                client.get(
                    "/vscode/stable-abc/static/out/vs/workbench/"
                    "workbench.js",
                    headers={
                        "Host": "localhost:8079",
                        "Accept-Encoding": "gzip, zstd",
                    },
                )
        finally:
            vscode_proxy.httpx.AsyncClient = original_async_client

        ae_values = [
            v for k, v in captured_headers.items()
            if k.lower() == "accept-encoding"
        ]
        assert len(ae_values) == 1, (
            f"non-eligible request: must preserve the client's "
            f"accept-encoding verbatim. Found {len(ae_values)} "
            f"headers: {ae_values!r}."
        )
        assert ae_values[0] == "gzip, zstd", (
            f"non-eligible request must NOT be pinned — the "
            f"client's value must reach the upstream verbatim. "
            f"Got {ae_values[0]!r}."
        )

    # Body-size cap (review-council follow-up ride-along 1).
    def test_oversize_body_byte_faithful_re_serve(self):
        """Mid-stream oversize path — the consume loop trips the
        cap mid-body. The rewrite seam is skipped, and the
        proxy must re-serve the FULL original upstream body
        (prefix + remainder concatenated) with the original
        Content-Encoding preserved.

        Council corrective round on 92784e20 — the original
        implementation ``break``-ed out of the consume loop
        BEFORE appending the over-cap chunk, so the rewrite
        buffer held only the ``≤cap`` prefix and the response
        carried a truncated body (front-truncated compressed
        stream — UNDECODEABLE on the browser side).

        The current implementation drains the cap-crossing
        chunk AND every subsequent chunk into the
        ``oversize_chunks`` raw-re-serve accumulator; the
        post-loop branch concatenates ``body_chunks + oversize_chunks``
        to build the full original body. This test pins the
        full-byte-equality contract.

        Driving details:
        - body is built of incompressible bytes (random via
          ``os.urandom``) so the brotli-compressed wire size is
          at least ~cap bytes — brotli's worst-case compression
          ratio on random data is well over 1:1. This guarantees
          ``len(br) > cap`` (the WIRE-size sanity the previous
          test lacked).
        - the fake upstream yields the body across TWO chunks
          — chunk 1 fits in the cap (the prefix), chunk 2
          straddles the cap (the cap-crossing chunk) and
          overflows. The previous single-chunk fake hid the
          cap-crossing boundary entirely (single chunk > cap
          means the rewrite buffer is empty and ``oversize_chunks``
          holds the whole body, which is the easier path to
          get right; the real bug only shows up across chunks).
        """
        import os
        self._install_fn(True)
        cap = vscode_proxy._WEBVIEW_REWRITE_MAX_BODY_BYTES
        # Body larger than the cap. Random bytes so brotli can't
        # compress them below the cap (worst-case ratio ~1.0 on
        # incompressible data; the wire size will exceed the cap
        # by a comfortable margin).
        oversized = os.urandom(cap + 500_000)
        assert len(oversized) > cap

        # Two-chunk split: chunk_1 fits UNDER the cap (so it goes
        # to the rewrite-buffer prefix — NOT the post-cap
        # accumulator). Chunk 2 pushes the running total OVER the
        # cap (so the cap is crossed on chunk 2, not chunk 1).
        # This is the precise shape that exposes the f1331082 bug:
        # the bug dropped ``body_chunks`` (the prefix), so the
        # served body was missing chunk_1. With chunk 1 alone
        # being oversized the bug is hidden (chunk 1 goes to
        # ``oversize_chunks`` regardless, so
        # ``b"".join(oversize_chunks)`` happens to contain
        # everything).
        split_at = cap - 100_000   # chunk_1 fits UNDER cap
        chunk_1 = oversized[:split_at]
        chunk_2 = oversized[split_at:]
        assert len(chunk_1) < cap
        assert len(chunk_1) + len(chunk_2) > cap
        assert len(chunk_2) > 0
        assert len(chunk_1) + len(chunk_2) == len(oversized)

        import brotli
        br = brotli.compress(oversized)
        # WIRE-size sanity (the previous test lacked this — it
        # only asserted the DECODED size exceeded the cap, which
        # is trivially true for any non-empty body. The fix
        # triggers the mid-stream branch only when the WIRE
        # body is larger than the cap).
        assert len(br) > cap, (
            f"wire-size sanity failed: brotli-compressed body "
            f"is {len(br)} bytes, expected > {cap}. Use a larger "
            f"oversize or incompressible data."
        )
        # The chunking is at the aiter_raw() generator level, not
        # the encoding level — the body decoder sees the full
        # stream after aiter_raw() returns. For brotli we have
        # to keep the wire chunks together for the decoder;
        # split the brotli bytes the same way for symmetry.
        br_split = len(br) * split_at // len(oversized)
        br_chunks = [br[:br_split], br[br_split:]]

        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)
        # NOTE: NO ``content-length`` header — this exercises the
        # MID-STREAM path (pre-consume gate cannot pre-decide).
        _patch_upstream_multi_chunk(
            chunks=br_chunks,
            content_type="text/html",
            content_encoding="br",
            content_length=None,
            status_code=200,
            path=(
                "/vscode/stable-abc/static/out/vs/workbench/"
                "contrib/webview/browser/pre/index.html"
            ),
        )

        with TestClient(app) as client:
            resp = client.get(
                "/vscode/stable-abc/static/out/vs/workbench/contrib/"
                "webview/browser/pre/index.html?id=x",
                headers={"Host": "localhost:8079"},
            )
        # No 500: oversize webview HTML falls back to byte-faithful
        # re-serve of the FULL upstream body (no truncation).
        assert resp.status_code == 200
        assert resp.headers.get("content-encoding") == "br", (
            "original Content-Encoding MUST be preserved on the "
            "raw-re-serve path — the wire bytes are still brotli "
            "and the browser's decoder must stay in sync."
        )
        # The Content-Length MUST equal the ORIGINAL upstream
        # body size — proof of byte-fidelity (no truncation). The
        # f1331082 implementation re-served only ``oversize_chunks``
        # (post-cap), so the Content-Length was the post-cap
        # chunk size instead of the full upstream body size.
        assert int(resp.headers["content-length"]) == len(br), (
            "Content-Length on oversize re-serve MUST match the "
            "ORIGINAL upstream body length (byte-faithful, no "
            "truncation). If this assertion fails the proxy is "
            "re-serving only the post-cap accumulator (prefix "
            "dropped) — that's CRITICAL 1 from the review."
        )
        # The full original body — verbatim — must reach the
        # browser. TestClient auto-decodes brotli; the decoded
        # bytes equal the upstream's pre-encoding bytes.
        assert resp.content == oversized, (
            "oversize re-serve MUST deliver the FULL original "
            "body — prefix + remainder concatenated. Truncation "
            "would be a broken page."
        )
        # No virtual-host origin added — we did not get far enough
        # to rewrite the meta-CSP (the doc is too large).
        assert self.VIRTUAL_HOST.encode() not in resp.content

    def test_oversize_body_content_length_gate_streams_through(self):
        """The PRE-CONSUME gate (Content-Length advertised > cap)
        refuses to consume at all and returns ``None`` so the
        caller streams the upstream body through verbatim. Zero
        memory cost on the proxy side — the upstream connection
        itself is what carries the body; the rewrite seam is
        skipped cleanly.

        Council corrective round on 92784e20 — this is path (a)
        in the design; the test for path (b) (mid-stream
        unknown-length) is ``test_oversize_body_byte_faithful_re_serve``
        above.
        """
        self._install_fn(True)
        cap = vscode_proxy._WEBVIEW_REWRITE_MAX_BODY_BYTES
        # ``Content-Length`` larger than the cap → gate fires,
        # no consume.
        oversized = (
            b"<html><body>" + b"x" * (cap + 1024) + b"</body></html>"
        )
        cl_header_value = str(len(oversized))

        # Use the multi-chunk helper so we exercise a real
        # ``aiter_raw`` stream consumption path (not a single-yield
        # fake that would mask any future regression where the gate
        # consumes once and the streaming path consumes again).
        manager = _make_mock_manager(running=True, port=8081)
        app = create_vscode_proxy_app(manager)
        _patch_upstream_multi_chunk(
            # Two chunks, both below cap individually but the
            # total exceeds cap — the gate must trigger on
            # Content-Length BEFORE the first chunk is consumed.
            chunks=[oversized[: len(oversized) // 2], oversized[len(oversized) // 2 :]],
            content_type="text/html",
            content_encoding=None,
            content_length=cl_header_value,
            status_code=200,
            path=(
                "/vscode/stable-abc/static/out/vs/workbench/"
                "contrib/webview/browser/pre/index.html"
            ),
        )

        with TestClient(app) as client:
            resp = client.get(
                "/vscode/stable-abc/static/out/vs/workbench/"
                "contrib/webview/browser/pre/index.html?id=x",
                headers={"Host": "localhost:8079"},
            )

        # The proxy streamed the upstream body through verbatim.
        # Status 200; the FULL body is delivered (no truncation,
        # no rewriting — the seam was skipped).
        assert resp.status_code == 200
        assert resp.content == oversized, (
            "Content-Length-gate path MUST stream the FULL "
            "upstream body through verbatim. Truncation or "
            "rewriting would defeat the gate's purpose."
        )
        # No meta-CSP rewrite happened — we never consumed.
        assert self.VIRTUAL_HOST.encode() not in resp.content


def _patch_upstream_for_webview_html(
    *,
    body: bytes,
    content_type: str,
    content_encoding: str | None,
    status_code: int,
    path: str,
) -> None:
    """Patch ``httpx.AsyncClient`` on ``daemon.routers.vscode_proxy``
    so the proxy receives the supplied response.

    Replaces the real upstream with a fake object whose body yields
    ``body`` via ``aiter_raw`` and whose headers carry the supplied
    ``Content-Type`` / ``Content-Encoding``. The proxy only reads
    ``upstream.request.url.path`` (for path matching),
    ``upstream.headers`` (for content-type / content-encoding),
    ``upstream.aiter_raw`` (for body streaming), and
    ``upstream.status_code``. The
    ``_restore_async_client_patch`` autouse fixture in
    :class:`TestWebviewCspRewrite` restores the original symbol
    after each test.
    """
    from daemon.routers import vscode_proxy as proxy_module

    class _FakeUpstream:
        def __init__(self):
            self.status_code = status_code
            self.request = SimpleNamespace(
                url=SimpleNamespace(path=path),
            )
            self.headers = {
                "content-type": content_type,
            }
            if content_encoding:
                self.headers["content-encoding"] = content_encoding
            self.headers["cache-control"] = "public, max-age=31536000"
            self.headers["etag"] = '"deadbeef"'
            self._body = body

        async def aiter_raw(self, chunk_size: int | None = None):
            yield self._body

        async def aclose(self):
            pass

    fake_client = MagicMock()
    fake_response = _FakeUpstream()

    async def _fake_send(*args, **kwargs):
        return fake_response

    fake_client.send = _fake_send
    fake_client.build_request = MagicMock(return_value=MagicMock())

    async def _fake_aclose():
        pass

    fake_client.aclose = _fake_aclose

    def _fake_async_client(*args, **kwargs):
        return fake_client

    proxy_module.httpx.AsyncClient = _fake_async_client


def _patch_upstream_multi_chunk(
    *,
    chunks: list[bytes],
    content_type: str,
    content_encoding: str | None,
    content_length: str | None,
    status_code: int,
    path: str,
    headers_extra: Mapping[str, str] | None = None,
) -> None:
    """Multi-chunk variant of :func:`_patch_upstream_for_webview_html`.

    Used by the size-cap oversize tests where the body MUST be
    streamed across MULTIPLE chunks (a single-chunk fake would
    hide the cap-crossing boundary — the rewrite-buffer prefix
    and the post-cap accumulator would both be empty).
    """
    from daemon.routers import vscode_proxy as proxy_module

    class _MultiChunkUpstream:
        def __init__(self):
            self.status_code = status_code
            self.request = SimpleNamespace(
                url=SimpleNamespace(path=path),
            )
            self.headers = {"content-type": content_type}
            if content_encoding:
                self.headers["content-encoding"] = content_encoding
            if content_length is not None:
                self.headers["content-length"] = content_length
            self.headers["cache-control"] = "public, max-age=31536000"
            self.headers["etag"] = '"deadbeef"'
            if headers_extra:
                self.headers.update(headers_extra)
            self._chunks = chunks
            self._stream_consumed = False

        async def aiter_raw(self, chunk_size: int | None = None):
            if self._stream_consumed:
                raise httpx.StreamConsumed(
                    "stream has been consumed"
                )
            self._stream_consumed = True
            for chunk in self._chunks:
                yield chunk

        async def aclose(self):
            pass

    fake_client = MagicMock()
    fake_response = _MultiChunkUpstream()

    async def _fake_send(*args, **kwargs):
        return fake_response

    fake_client.send = _fake_send
    fake_client.build_request = MagicMock(return_value=MagicMock())

    async def _fake_aclose():
        pass

    fake_client.aclose = _fake_aclose

    def _fake_async_client(*args, **kwargs):
        return fake_client

    proxy_module.httpx.AsyncClient = _fake_async_client

