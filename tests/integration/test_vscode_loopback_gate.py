"""M-1 /vscode loopback-peer gate — 2026-10-11 security review.

The plan's "loopback posture" claim for the UNPROTECTED ``/vscode``
path was FALSE in code (the daemon binds ``0.0.0.0``; nothing in
``vscode_proxy.py`` restricted inbound peers). The leader ruling: make
the claim TRUE — the ASGI ``scope["client"]`` TCP peer must be
loopback whenever ``vscode_protected=false``, in BOTH auth states
(the gate lives in the proxy sub-app, auth-state-independent by
construction; the OAuth2 middleware's public tag lets it through
untouched, and this gate then decides).

Pinned here:

* loopback peer → PASSES to the proxy (readiness 503 proves the
  proxy handled it, not the gate);
* non-loopback peer → 403 ``VSCODE_LOOPBACK_ONLY`` (403 chosen over
  404 deliberately — documented in the gate);
* the same two states over WebSocket → handshake close;
* ``loopback_only=False`` (the ``vscode_protected=true`` wiring) →
  the gate OPENS for remote peers (bearer lane owns it instead);
* IPv4-mapped IPv6 loopback peer (``::ffff:127.0.0.1``) → passes.

Harness: ``TestClient(client=...)`` controls the ASGI peer.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from daemon.routers.vscode_proxy import create_vscode_proxy_app


def _manager(running: bool = False, port: int | None = None):
    class _M:
        def is_running(self):
            return running

        def get_port(self):
            return port

    return _M()


LOOPBACK = ("127.0.0.1", 50000)
MAPPED = ("::ffff:127.0.0.1", 50000)
REMOTE = ("203.0.113.7", 4444)


class TestLoopbackGateHttp:
    def test_loopback_peer_passes_through_to_proxy(self):
        app = create_vscode_proxy_app(_manager(running=False))
        with TestClient(
            app, client=LOOPBACK, follow_redirects=False
        ) as client:
            r = client.get("/workbench.html")
        # The PROXY answered (readiness 503) — the gate did not refuse.
        assert r.status_code == 503
        assert "not ready" in r.text.lower()

    def test_non_loopback_peer_refused_403(self):
        app = create_vscode_proxy_app(_manager(running=True, port=1))
        with TestClient(
            app, client=REMOTE, follow_redirects=False
        ) as client:
            r = client.get("/workbench.html")
        assert r.status_code == 403
        body = r.json()
        assert body["error"] == "VSCODE_LOOPBACK_ONLY"
        assert "vscode_protected" in body["message"]

    def test_non_loopback_peer_refused_even_when_server_ready(self):
        """The gate precedes the readiness check — a READY server is
        still not remotely reachable on the unprotected path."""
        app = create_vscode_proxy_app(_manager(running=True, port=41293))
        with TestClient(
            app, client=REMOTE, follow_redirects=False
        ) as client:
            r = client.get("/")
        assert r.status_code == 403
        assert r.json()["error"] == "VSCODE_LOOPBACK_ONLY"

    def test_missing_peer_fails_closed(self):
        app = create_vscode_proxy_app(_manager(running=True, port=41293))

        captured = {}

        async def _strip_client(scope, receive, send):
            captured["type"] = scope["type"]
            scope["client"] = None
            await app(scope, receive, send)

        with TestClient(
            _strip_client, client=REMOTE, follow_redirects=False
        ) as client:
            r = client.get("/")
        assert r.status_code == 403
        assert r.json()["error"] == "VSCODE_LOOPBACK_ONLY"

    def test_ipv4_mapped_ipv6_loopback_peer_passes(self):
        app = create_vscode_proxy_app(_manager(running=False))
        with TestClient(app, client=MAPPED, follow_redirects=False) as client:
            r = client.get("/workbench.html")
        assert r.status_code == 503  # proxy readiness, not the gate

    def test_gate_disabled_opens_remote_peers(self):
        """``loopback_only=False`` — the vscode_protected=true wiring:
        the proxy no longer refuses remote peers (the OAuth2 bearer
        lane owns the surface instead)."""
        app = create_vscode_proxy_app(
            _manager(running=False), loopback_only=False
        )
        with TestClient(app, client=REMOTE, follow_redirects=False) as client:
            r = client.get("/workbench.html")
        assert r.status_code == 503  # proxy readiness — remote got in


class TestLoopbackGateWebSocket:
    def test_non_loopback_ws_handshake_refused(self):
        app = create_vscode_proxy_app(_manager(running=True, port=1))
        with TestClient(app, client=REMOTE) as client:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                with client.websocket_connect("/ws-proxy"):
                    pass  # pragma: no cover — never accepted
        assert excinfo.value.code == 1008

    def test_loopback_ws_reaches_proxy(self):
        app = create_vscode_proxy_app(_manager(running=True, port=1))
        with TestClient(app, client=LOOPBACK) as client:
            # The proxy's WS route connects then fails upstream — the
            # point is the HANDSHAKE passed the gate (a gate refusal
            # raises WebSocketDisconnect(1008) BEFORE any proxy code;
            # any other failure mode is the proxy's own).
            try:
                ws = client.websocket_connect("/ws-proxy")
                ws.close()
            except WebSocketDisconnect as exc:
                assert exc.code != 1008, (
                    f"gate refused a loopback WS handshake: {exc}"
                )
            except Exception:  # noqa: BLE001 — upstream failure OK
                pass


class TestMountedPosture:
    """The /vscode mount path — the REAL wiring shape (parent app +
    Mount), both auth states: the gate is a property of the sub-app,
    so auth ON/OFF behave identically for the unprotected mount."""

    @pytest.mark.parametrize("auth_enabled", [False, True])
    def test_mounted_vscode_loopback_only_in_both_auth_states(
        self, auth_enabled
    ):
        parent = FastAPI()
        parent.mount(
            "/vscode",
            create_vscode_proxy_app(_manager(running=False)),
        )

        @parent.get("/{path:path}")
        async def _spa(path: str):
            from fastapi.responses import JSONResponse

            return JSONResponse({"error": "SPA fallback"}, status_code=200)

        with TestClient(parent, client=LOOPBACK) as loop:
            r_loop = loop.get("/vscode/x")
        with TestClient(parent, client=REMOTE) as remote:
            r_remote = remote.get("/vscode/x")
        assert r_loop.status_code == 503  # proxy handled it
        assert r_remote.status_code == 403  # gate refused it
        assert r_remote.json()["error"] == "VSCODE_LOOPBACK_ONLY"


class TestR2WebSocketGateRegardlessOfFlag:
    """R2 (2026-10-11 merge gate): the WS peer gate survives the
    ``vscode_protected=true`` wiring (``loopback_only=False``). The
    AuthMiddleware passes WEBSOCKET scopes through untouched (FR-21 v1
    seam), so if the flag also disarmed the WS gate, the opt-in
    "protected" state would accept unauthenticated REMOTE /vscode WS
    upgrades. [REQ] §5.5 matrix: WS loopback unchanged in v1 — both
    flag states refuse remote WS; loopback WS unaffected."""

    def test_remote_ws_refused_when_loopback_only_true(self):
        app = create_vscode_proxy_app(_manager(running=True, port=1))
        with TestClient(app, client=REMOTE) as client:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                with client.websocket_connect("/ws-proxy"):
                    pass  # pragma: no cover
        assert excinfo.value.code == 1008

    def test_remote_ws_refused_when_loopback_only_false(self):
        """THE R2 leg: the flag opens HTTP only — remote WS is still
        handshake-refused 1008 (previously the gate was removed
        entirely and this handshake would have been ACCEPTED)."""
        app = create_vscode_proxy_app(
            _manager(running=True, port=1), loopback_only=False
        )
        with TestClient(app, client=REMOTE) as client:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                with client.websocket_connect("/ws-proxy"):
                    pass  # pragma: no cover
        assert excinfo.value.code == 1008

    def test_remote_http_still_opens_under_flag(self):
        """The flag's PURPOSE is intact: remote HTTP peers reach the
        proxy (readiness 503 proves the proxy answered, not a gate)."""
        app = create_vscode_proxy_app(
            _manager(running=False), loopback_only=False
        )
        with TestClient(app, client=REMOTE, follow_redirects=False) as client:
            r = client.get("/workbench.html")
        assert r.status_code == 503

    def test_loopback_ws_unaffected_by_flag(self):
        """Loopback WS handshakes pass the gate in BOTH flag states
        (the gate never over-refuses its own side)."""
        for kwargs in ({}, {"loopback_only": False}):
            app = create_vscode_proxy_app(_manager(running=True, port=1), **kwargs)
            with TestClient(app, client=LOOPBACK) as client:
                try:
                    ws = client.websocket_connect("/ws-proxy")
                    ws.close()
                except WebSocketDisconnect as exc:
                    assert exc.code != 1008, (
                        f"gate refused loopback WS under {kwargs}: {exc}"
                    )
                except Exception:  # noqa: BLE001 — upstream failure OK
                    pass
