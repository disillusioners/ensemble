"""Module-lane derivation + pass-through — oauth2-auth Phase 3
(T3.10 / G3.8, AMEND v1.4).

Asserts the plan §4 row: ``module_lane_path_set`` contains
``/api/webhooks/*`` + ``/api/mcp/kb`` + ``/api/mcp/kb/sse`` and is
DISJOINT from ``public_path_set``; BOTH sets are logged at startup;
module-lane requests carry NO auth requirement with auth ON
(pass-through) and byte-identical behavior with auth OFF (FR-42 /
AC-41 groundwork).
"""

from __future__ import annotations

import logging
import warnings

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount

from daemon.api import create_app
from daemon.middleware.auth import AuthMiddleware
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub

EXPECTED_MODULE_LANE = {
    "/api/webhooks/{source_id}",
    "/api/mcp/kb",
    "/api/mcp/kb/sse",
}


@pytest.fixture()
def on_app(tmp_path):
    warnings.filterwarnings("ignore")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)
    attach_auth_state(app, cfg, stub, tmp_path)
    return app


class TestDerivation:
    def test_module_lane_set_members(self, on_app):
        mw = AuthMiddleware(None, auth_config=on_app.state.auth_config)
        mw._ensure_derived({"app": on_app})
        assert EXPECTED_MODULE_LANE <= set(mw._module_lane_paths)

    def test_disjoint_from_public(self, on_app):
        mw = AuthMiddleware(None, auth_config=on_app.state.auth_config)
        mw._ensure_derived({"app": on_app})
        assert not (set(mw._module_lane_paths) & set(mw._public_paths))

    def test_membership_static_independent_of_auth_state(self, tmp_path):
        """FR-42: module-lane membership identical in BOTH auth states
        (AC-41 groundwork) — the tags are set unconditionally in
        create_app and the derivation reads tags, not auth state."""
        warnings.filterwarnings("ignore")
        off_app = create_app()  # auth OFF — tags still applied
        mw_probe = AuthMiddleware(None, auth_config=on_auth_config())
        mw_probe._ensure_derived({"app": off_app})
        assert EXPECTED_MODULE_LANE <= set(mw_probe._module_lane_paths)
        assert not (set(mw_probe._module_lane_paths) & set(mw_probe._public_paths))

    def test_both_sets_logged_at_startup(self, on_app, caplog):
        client = TestClient(on_app, base_url="http://testserver")
        with caplog.at_level(logging.INFO, logger="daemon.middleware.auth"):
            client.get("/livez")
        messages = [r.message for r in caplog.records]
        assert any("public_path_set" in m for m in messages)
        assert any("module_lane_path_set" in m for m in messages)


class TestPassThrough:
    def test_webhook_module_lane_no_auth_required_with_auth_on(self, on_app):
        """A module-lane request with NO credential is NOT handed the
        OAuth2 401 contract — it reaches the module (its own guard
        decides; 404 source-not-found here proves the middleware
        passed it through)."""
        class _Repo:
            def get_source_config(self, source_id):
                return None

        class _Manager:
            _source_repository = _Repo()
            source_registry = {}
            is_write_paused = False

        on_app.state.manager = _Manager()
        client = TestClient(
            on_app, base_url="http://testserver", follow_redirects=False
        )
        r = client.post("/api/webhooks/any-source", json={"x": 1})
        assert r.status_code == 404  # module layer answered (source 404)
        assert "AUTH_REQUIRED" not in r.text

    def test_mount_subpath_classified_module_lane(self, on_app):
        """The Mount boundary rule: /api/mcp/kb/mcp is INSIDE the
        module-lane mount (prefix semantics), NOT protected."""
        mw = AuthMiddleware(None, auth_config=on_app.state.auth_config)
        mw._ensure_derived({"app": on_app})
        assert mw._resolve_tagged("/api/mcp/kb/mcp") == "module_lane"
        assert mw._resolve_tagged("/api/mcp/kb") == "module_lane"
        # …but the OAuth2-managed keys family wins by registration order.
        assert mw._resolve_tagged("/api/mcp/kb/keys") == "protected"
        assert (
            mw._resolve_tagged("/api/mcp/kb/keys/abc/revoke") == "protected"
        )

    def test_kb_keys_family_not_swallowed_by_mount(self, on_app):
        """The T3.12 OAuth2-human-lane family is PROTECTED even though
        the module-lane Mount path-prefixes it."""
        mw = AuthMiddleware(None, auth_config=on_app.state.auth_config)
        mw._ensure_derived({"app": on_app})
        assert "/api/mcp/kb/keys" not in mw._module_lane_paths
        assert "/api/mcp/kb/keys" not in mw._public_paths

    def test_module_lanes_identical_behavior_both_states(self, tmp_path):
        """AC-41 leg (module-lane behavior identical in both states):
        with auth OFF the middleware is ABSENT (nothing gates at all)
        and the webhook module layer answers identically; the KB-mount
        guard legs live in test_mcp_kb_public (the real FastMCP sub-app
        needs its daemon lifespan)."""
        warnings.filterwarnings("ignore")
        off_app = create_app()
        assert all(
            m.cls.__name__ != "AuthMiddleware" for m in off_app.user_middleware
        )

        class _Repo:
            def get_source_config(self, source_id):
                return None

        class _Manager:
            _source_repository = _Repo()
            source_registry = {}
            is_write_paused = False

        off_app.state.manager = _Manager()
        off_client = TestClient(off_app, base_url="http://testserver")
        assert off_client.post("/api/webhooks/x", json={}).status_code == 404
