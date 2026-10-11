"""OFF-state app-graph introspection — oauth2-auth Phase 3 (G3.2 / FR-2).

Asserts the plan §4 row: with auth disabled the middleware is ABSENT
from ``app.user_middleware`` (AC-2), no auth cookie is ever set, no
auth routes / keys routes exist, no ``auth_*`` app.state attribute
exists, and the public + protected route behavior is identical to the
pre-build app (the module-level convenience app built from the same
OFF config).
"""

from __future__ import annotations

import warnings

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount

import daemon.api as api_module
from daemon.api import create_app
from daemon.mcp.kb_keys import KbKeyGuardApp

AUTH_STATE_ATTR_PREFIX = "auth_"


@pytest.fixture(scope="module")
def off_app():
    warnings.filterwarnings("ignore")
    return create_app()  # default (auth OFF) config


class TestOffStateGraph:
    def test_middleware_absent(self, off_app):
        """AC-2 / D3.2 not-registered: no AuthMiddleware anywhere in
        the user_middleware list."""
        names = [m.cls.__name__ for m in off_app.user_middleware]
        assert "AuthMiddleware" not in names
        assert names == [
            "HostCaptureMiddleware",
            "SelectiveAccessLogMiddleware",
            "CORSMiddleware",
        ][::-1][: len(names)] or set(names) == {
            "HostCaptureMiddleware",
            "SelectiveAccessLogMiddleware",
            "CORSMiddleware",
        }

    def test_no_auth_routes(self, off_app):
        paths = {getattr(r, "path", "") for r in off_app.routes}
        assert not any(p.startswith("/api/auth/") for p in paths)
        assert not any(p.startswith("/api/mcp/kb/keys") for p in paths)

    def test_no_auth_state_attributes(self, off_app):
        for attr in vars(off_app.state):
            assert not attr.startswith(AUTH_STATE_ATTR_PREFIX), attr

    def test_kb_still_mounted_in_both_states(self, off_app):
        """The KB mounts (guarded, pass-through) exist with auth OFF —
        module lanes are auth-state independent (FR-42)."""
        from starlette.routing import Mount

        mounts = {
            r.path for r in off_app.routes if isinstance(r, Mount)
        }
        assert {"/api/mcp/kb", "/api/mcp/kb/sse"} <= mounts


class TestOffStateBehavior:
    def test_protected_route_serves_unauthenticated(self, off_app):
        """Byte-identical pre-build behavior: /api/agents serves the
        real handler with NO credentials and NO auth artifacts."""
        client = TestClient(off_app, base_url="http://testserver")
        r = client.get("/api/agents")
        assert r.status_code == 200
        assert "agents" in r.json()
        assert "set-cookie" not in r.headers
        assert "www-authenticate" not in r.headers

    def test_public_probes_serve(self, off_app):
        client = TestClient(off_app, base_url="http://testserver")
        assert client.get("/livez").status_code == 200
        r = client.get("/readyz")
        # The full daemon lifespan never ran in this fixture — the
        # probe returns the fail-closed 503 body (pre-build behavior),
        # NOT an auth contract response.
        assert r.status_code in (200, 503)
        assert "www-authenticate" not in r.headers

    def test_auth_routes_404_via_api_carveout(self, off_app):
        """Auth endpoints don't exist OFF — the SPA catch-all's API
        carve-out 404s them (pre-build behavior). NOTE: /api/mcp/kb/keys
        is NOT in this loop — with auth OFF the keys router is absent
        and the /api/mcp/kb Mount claims the prefix (its sub-app answers,
        mock-or-real); absence of the CRUD family is pinned by
        ``test_no_auth_routes``."""
        client = TestClient(off_app, base_url="http://testserver")
        for path in (
            "/api/auth/login",
            "/api/auth/callback",
            "/api/auth/me",
        ):
            r = client.get(path)
            assert r.status_code == 404, path

    def test_module_app_equals_fresh_create_app(self):
        """The module-level convenience app and a fresh create_app()
        produce the same route table shape (the double module-level
        construction is idempotent)."""
        fresh = create_app()
        module_paths = sorted(
            str(getattr(r, "path", "")) for r in api_module.app.routes
        )
        fresh_paths = sorted(str(getattr(r, "path", "")) for r in fresh.routes)
        assert module_paths == fresh_paths

    def test_kb_mount_lifecycle_needs_daemon_lifespan(self, off_app):
        """The KB mounts exist OFF; DRIVING them requires the daemon
        lifespan (FastMCP session-manager init) — the guard mechanics
        themselves are proven transport-agnostically in
        ``test_mcp_kb_public.py::TestGuardMechanism``. Here we assert
        the wiring only: each mount wraps a KbKeyGuardApp."""
        wrapped = {
            r.path
            for r in off_app.routes
            if isinstance(r, Mount) and isinstance(r.app, KbKeyGuardApp)
        }
        assert wrapped == {"/api/mcp/kb", "/api/mcp/kb/sse"}
