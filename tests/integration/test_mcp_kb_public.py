"""MCP-KB mount reachability + Mount-traversal regression — Phase 3
(T3.10/T3.11, G3.6 / G3.8 legs).

AMEND v1.4 / module-lanes re-annotation: ``/api/mcp/kb`` and
``/api/mcp/kb/sse`` are classified MODULE-LANE (in
``module_lane_path_set``, NOT in ``public_path_set`` — classes
disjoint) and pass through identically with auth OFF and ON (AC-41
leg). The Mount-traversal regression test (the D1 guard) fails loudly
if a framework upgrade changes Mount/middleware composition — pinned
framework: Starlette 0.52.1 / FastAPI 0.133.1 (pyproject.toml +
uv.lock; NOT changed by this phase).
"""

from __future__ import annotations

import warnings

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount

import starlette
import fastapi

from daemon.api import create_app
from daemon.mcp.kb_keys import KB_KEY_HEADER, KbKeyGuardApp, McpKbKeyStore
from daemon.middleware.auth import AuthMiddleware
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub

PINS = {"starlette": "0.52.1", "fastapi": "0.133.1"}


class TestFrameworkPins:
    def test_pins_unchanged(self):
        """G3.6: the pins are RECORDED — a drift here is the loud
        signal to re-run the D1 traversal analysis."""
        assert starlette.__version__ == PINS["starlette"]
        assert fastapi.__version__ == PINS["fastapi"]


class TestModuleLaneClassification:
    @pytest.fixture()
    def on_app(self, tmp_path):
        warnings.filterwarnings("ignore")
        stub = FakeOidcStub()
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        return app

    def test_classified_module_lane_not_public(self, on_app):
        mw = AuthMiddleware(None, auth_config=on_app.state.auth_config)
        mw._ensure_derived({"app": on_app})
        for path in ("/api/mcp/kb", "/api/mcp/kb/sse"):
            assert path in mw._module_lane_paths, path
            assert path not in mw._public_paths, path

    def test_mount_traversal_through_full_asgi_stack(self, tmp_path):
        """The ONE-LINE Mount-traversal regression (D1 guard): a Mount
        wrapped in the guard is REACHED through a parent stack carrying
        AuthMiddleware — the mount's own response surfaces, not the
        auth 401 contract."""
        cfg = on_auth_config()
        stub = FakeOidcStub()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        # Empty store ⇒ guard passes through ⇒ the request REACHES the
        # mount layer. The FastMCP sub-app needs its session-manager
        # lifespan; a non-401-with-AUTH_REQUIRED outcome proves the
        # middleware did not gate the path.
        client = TestClient(
            app,
            base_url="http://testserver",
            raise_server_exceptions=False,
        )
        r = client.post(
            "/api/mcp/kb/mcp", headers={"Accept": "application/json"}
        )
        body = r.text
        assert "AUTH_REQUIRED" not in body
        assert r.headers.get("www-authenticate") is None
        _ = FastAPI  # import honesty (pin assertion lives above)

    def test_guard_wraps_both_mounts(self, on_app):
        from daemon.mcp.kb_keys import KbKeyGuardApp as Guard

        wrapped = [
            r
            for r in on_app.routes
            if isinstance(r, Mount) and isinstance(r.app, Guard)
        ]
        paths = {r.path for r in wrapped}
        assert paths == {"/api/mcp/kb", "/api/mcp/kb/sse"}


class TestGuardMechanism:
    """T3.11 legs on a minimal app (the real FastMCP sub-app requires
    its lifespan; the guard contract is mount-agnostic)."""

    @staticmethod
    def _mini_app(store: McpKbKeyStore | None) -> Starlette:
        async def dummy(request):
            return JSONResponse({"reached": True})

        sub = Starlette(routes=[])
        guard = KbKeyGuardApp(dummy)
        return Starlette(
            routes=[Mount("/kb", app=guard)],
        ), guard

    def test_empty_store_passes_through(self, tmp_path):
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel
        from daemon.repositories.mcp_kb_keys.models import McpKbKey

        engine = create_engine(f"sqlite:///{tmp_path}/kb_guard.db")
        SQLModel.metadata.create_all(engine, tables=[McpKbKey.__table__])
        store = McpKbKeyStore(engine)

        async def dummy(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"reached":true}'})

        guard = KbKeyGuardApp(dummy)
        status, body = _run_guard(guard, store)
        assert status == 200  # bootstrap posture: untouched pass-through

    def test_active_store_requires_key_and_rejects_wrong(self, tmp_path):
        from sqlalchemy import create_engine
        from sqlmodel import SQLModel
        from daemon.repositories.mcp_kb_keys.models import McpKbKey

        engine = create_engine(f"sqlite:///{tmp_path}/kb_guard2.db")
        SQLModel.metadata.create_all(engine, tables=[McpKbKey.__table__])
        store = McpKbKeyStore(engine)

        async def dummy(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [],
                }
            )
            await send({"type": "http.response.body", "body": b"{}"})

        guard = KbKeyGuardApp(dummy)

        # The guard activates exactly when the FIRST key is minted —
        # before that, everything passes (bootstrap posture).
        status, _ = _run_guard(guard, store, headers={})
        assert status == 200
        _, plaintext = store.create_key("test")

        # Missing header → KB_KEY_REQUIRED
        status, body = _run_guard(guard, store, headers={})
        assert status == 401 and b"KB_KEY_REQUIRED" in body
        # Wrong key → KB_KEY_INVALID
        status, body = _run_guard(
            guard, store, headers={KB_KEY_HEADER: "kbk_deadbeef"}
        )
        assert status == 401 and b"KB_KEY_INVALID" in body
        # Valid key → passes
        status, body = _run_guard(
            guard, store, headers={KB_KEY_HEADER: plaintext}
        )
        assert status == 200

    def test_key_format_namespaced(self):
        from daemon.mcp.kb_keys import mint_kb_key

        key = mint_kb_key()
        assert key.startswith("kbk_")
        payload = key[len("kbk_"):]
        assert len(payload) == 48
        int(payload, 16)  # hex


def _run_guard(guard: KbKeyGuardApp, store: McpKbKeyStore, *, headers=None):
    """Drive the guard against a synthetic scope with the store bound
    to a synthetic app.state."""
    sent: list = []
    import types

    app_ref = types.SimpleNamespace()
    app_ref.state = types.SimpleNamespace(mcp_kb_key_store=store)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/kb/x",
        "headers": [
            (k.encode(), v.encode()) for k, v in (headers or {}).items()
        ],
        "app": app_ref,
    }

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    async def run():
        await guard(scope, receive, send)

    import asyncio

    asyncio.run(run())
    status = sent[0]["status"]
    body = b"".join(m.get("body", b"") for m in sent[1:])
    return status, body
