"""Route-metadata derivation tests — oauth2-auth Phase 3 (T3.5/T3.10, G3.5).

Asserts the plan §4 row (AMENDED v1.1 / D18): the public-path set is
DERIVED from ``app.routes`` at middleware construction; the startup log
records the derived set; a route added with the ``public-probe`` tag is
picked up; a route WITHOUT the tag is NOT in the derived set
(default-deny proof).
"""

from __future__ import annotations

import logging
import warnings

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Mount

from daemon.api import create_app
from daemon.auth.route_meta import (
    AUTH_MODULE_LANE_ATTR,
    AUTH_PROTECTED_ATTR,
    AUTH_PUBLIC_ATTR,
    tag_public,
)
from daemon.middleware.auth import AuthMiddleware
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub


@pytest.fixture()
def on_app(tmp_path):
    warnings.filterwarnings("ignore")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)
    attach_auth_state(app, cfg, stub, tmp_path)
    return app


def derive(app) -> AuthMiddleware:
    mw = AuthMiddleware(None, auth_config=app.state.auth_config)
    mw._ensure_derived({"app": app})
    return mw


class TestDerivedSets:
    def test_public_set_derived_from_tags(self, on_app):
        mw = derive(on_app)
        expected = {
            "/livez",
            "/readyz",
            "/api/health",
            "/api/info",
            "/views/livez",
            "/api/maintenance/checkpoint-cleanup/availability",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/",
            "/api/auth/login",
            "/api/auth/callback",
            "/api/auth/logout",
            "/api/auth/session",
            "/api/auth/me",
        }
        assert expected <= set(mw._public_paths)
        # Every tagged route IS in the derived set.
        for route in on_app.routes:
            if getattr(route, AUTH_PUBLIC_ATTR, False):
                assert route.path in mw._public_paths

    def test_module_lane_set_derived_and_disjoint(self, on_app):
        mw = derive(on_app)
        assert {
            "/api/mcp/kb",
            "/api/mcp/kb/sse",
            "/api/webhooks/{source_id}",
        } <= set(mw._module_lane_paths)
        # Classes are DISJOINT (T3.10 / AC-22a v1.4 leg).
        assert not (set(mw._public_paths) & set(mw._module_lane_paths))
        # Mounts classified with boundary semantics.
        mounts = {
            r.path for r in on_app.routes if isinstance(r, Mount)
        }
        assert {"/api/mcp/kb", "/api/mcp/kb/sse"} <= mounts

    def test_untagged_route_is_default_deny(self, on_app):
        """Default-deny proof: a route WITHOUT the tag is in NEITHER
        set (protected by default)."""
        mw = derive(on_app)
        untagged = [
            getattr(r, "path")
            for r in on_app.routes
            if getattr(r, "path", None)
            and not getattr(r, AUTH_PUBLIC_ATTR, False)
            and not getattr(r, AUTH_MODULE_LANE_ATTR, False)
            and not getattr(r, AUTH_PROTECTED_ATTR, False)
        ]
        assert untagged, "expected untagged (protected) routes to exist"
        for path in untagged:
            assert path not in mw._public_paths
            assert path not in mw._module_lane_paths
        assert "/api/agents" not in mw._public_paths
        assert "/api/instances" not in mw._public_paths

    def test_new_tagged_route_picked_up(self, tmp_path):
        """A route added with the public-probe tag IS derived."""
        cfg = on_auth_config()
        stub = FakeOidcStub()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)

        @app.get("/custom-probe")
        async def custom_probe():
            return {"ok": True}

        for route in app.routes:
            if getattr(route, "path", None) == "/custom-probe":
                tag_public(route)
        mw = derive(app)
        assert "/custom-probe" in mw._public_paths

    def test_untagged_new_route_not_derived(self, tmp_path):
        cfg = on_auth_config()
        stub = FakeOidcStub()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)

        @app.get("/custom-secret-route")
        async def custom_secret():
            return {"secret": True}

        mw = derive(app)
        assert "/custom-secret-route" not in mw._public_paths
        assert "/custom-secret-route" not in mw._module_lane_paths

    def test_derivation_happens_once(self, on_app):
        mw = derive(on_app)
        assert mw._derived
        first = mw._public_paths
        # A second call is a no-op.
        mw._ensure_derived({"app": on_app})
        assert mw._public_paths is first

    def test_conflicting_tags_fail_closed(self, on_app, caplog):
        """Both tags on one route → DISJOINT contract violation → loud
        ERROR + protected default (fail-closed)."""
        for route in on_app.routes:
            if getattr(route, "path", None) == "/api/agents":
                tag_public(route)
                setattr(route, AUTH_MODULE_LANE_ATTR, True)
        with caplog.at_level(logging.ERROR, logger="daemon.middleware.auth"):
            mw = derive(on_app)
        assert "/api/agents" not in mw._public_paths
        assert "/api/agents" not in mw._module_lane_paths
        kinds = {
            e.kind for e in mw._ordered if e.template == "/api/agents"
        }
        assert kinds == {"protected"}  # (both GET+POST routes re-classed)
        assert any("BOTH" in record.message for record in caplog.records)


class TestStartupLog:
    def test_both_sets_logged_at_first_request(self, tmp_path, caplog):
        cfg = on_auth_config()
        stub = FakeOidcStub()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        client = TestClient(app, base_url="http://testserver")
        with caplog.at_level(logging.INFO, logger="daemon.middleware.auth"):
            client.get("/livez")
        messages = [record.message for record in caplog.records]
        assert any("public_path_set" in m for m in messages)
        assert any("module_lane_path_set" in m for m in messages)
        # The logged sets carry the actual path members.
        public_line = next(m for m in messages if "public_path_set" in m)
        assert "/livez" in public_line
        module_line = next(m for m in messages if "module_lane_path_set" in m)
        assert "/api/mcp/kb" in module_line
