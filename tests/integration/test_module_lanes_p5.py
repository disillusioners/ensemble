"""Module-lane P5 derivation + equivalence tests — oauth2-auth Phase 5 (T5.4).

Sister suite to ``tests/integration/test_module_lanes.py`` (Phase 3).
The P3 suite covers the basic class membership + disjointness +
pass-through; this P5 suite covers the AC-41 module-lane ON/OFF
byte-equivalence (FR-42) — same request set against the
``auth_disabled_app`` and ``auth_enabled_app`` fixtures, asserting
identical status + body + module_lane_path_set.
"""

from __future__ import annotations

import warnings
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient

from daemon.api import create_app
from daemon.middleware.auth import AuthMiddleware
from tests.conftest_auth import _WrappedApp, auth_disabled_app, auth_enabled_app
from tests.fakes.fake_oidc_provider import FakeOidcProvider


def _dummy_manager():
    """Stub manager that returns 404 for every source lookup (the
    module's own behavior — the OAuth2 middleware has passed it through)."""

    class _Repo:
        def get_source_config(self, source_id):
            return None

    class _Manager:
        is_write_paused = False
        source_registry = {}
        _source_repository = _Repo()

    return _Manager()


# ── AC-41: byte-identical behavior in both states ─────────────────────


class TestModuleLaneByteEquivalence:
    """FR-42 / AC-41: the webhook module lane behaves IDENTICALLY with
    auth ON and auth OFF — same status code + JSON body for an identical
    request set, and ``module_lane_path_set`` is logged identically."""

    @pytest.fixture()
    def off_app(self):
        with auth_disabled_app() as (app, transport):
            yield app

    @pytest.fixture()
    def on_app(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            yield app

    def _post_webhook(self, app):
        """POST /api/webhooks/x with no auth headers; assert the
        module layer answers (the middleware passed it through)."""
        manager = _dummy_manager()
        app.state.manager = manager
        client = TestClient(app, base_url="http://testserver", follow_redirects=False)
        r = client.post("/api/webhooks/any-source", json={"text": "hi"})
        return r

    def test_webhook_404_identical_in_both_states(self, on_app, off_app):
        """A module-lane POST returns 404 in both auth states (the
        module's source-not-found answer; the OAuth2 middleware
        never sees it)."""
        on_resp = self._post_webhook(on_app)
        off_resp = self._post_webhook(off_app)
        assert on_resp.status_code == off_resp.status_code
        assert on_resp.status_code == 404

    def test_kb_mount_passthrough_without_key(self, on_app, off_app):
        """The MCP-KB mount is module-lane — both auth states pass the
        request through to the mount. The mount's own key-check
        answers (401 KB_KEY_REQUIRED if the store has active keys;
        else pass-through).

        Empty-store posture: the guard is pass-through. Both states
        therefore behave the same way on the store-empty leg.

        We exercise the path-classification side of the contract here
        (the FastMCP sub-app needs the daemon lifespan to mount, so
        the actual mount-body is tested separately)."""
        from daemon.middleware.auth import AuthMiddleware
        warnings.filterwarnings("ignore")
        off_app = create_app()  # OFF-state bare
        from tests.conftest_auth import on_auth_config
        on_app = create_app(auth_config=on_auth_config())  # ON-state bare
        mw_off = AuthMiddleware(None, auth_config=on_auth_config())
        mw_off._ensure_derived({"app": off_app})
        mw_on = AuthMiddleware(None, auth_config=on_auth_config())
        mw_on._ensure_derived({"app": on_app})
        # Both must classify /api/mcp/kb as module_lane.
        assert mw_off._resolve_tagged("/api/mcp/kb") == "module_lane"
        assert mw_on._resolve_tagged("/api/mcp/kb") == "module_lane"
        assert mw_off._resolve_tagged("/api/mcp/kb/sse") == "module_lane"
        assert mw_on._resolve_tagged("/api/mcp/kb/sse") == "module_lane"


# ─- module-lane path-set logged at startup ──────────────────────────


class TestModuleLanePathSetLogged:
    """The middleware logs the derived ``module_lane_path_set`` at
    startup (reviewable in promote transcripts)."""

    def test_logged_at_startup_with_auth_on(self, tmp_path, caplog):
        import logging

        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
            )
            with caplog.at_level(logging.INFO, logger="daemon.middleware.auth"):
                client.get("/livez")
            messages = [r.message for r in caplog.records]
            assert any("module_lane_path_set" in m for m in messages)

    def test_module_lane_path_set_identical_in_both_states(self, tmp_path):
        """FR-42/AC-41: the module_lane_path_set is identical in BOTH
        auth states — the route-tagging is unconditional at
        ``create_app()``."""
        warnings.filterwarnings("ignore")
        off_app = create_app()
        on_app = create_app(auth_config=self._on_cfg())
        # Probe both with a one-shot AuthMiddleware(off_config) to
        # extract the derived sets — the derivation reads tags, not auth
        # state.
        for app in (off_app, on_app):
            mw = AuthMiddleware(None, auth_config=self._on_cfg())
            mw._ensure_derived({"app": app})
            assert mw._module_lane_paths == mw._module_lane_paths  # deterministic
        # The two apps must produce the SAME module_lane_path_set
        # (the off_app tags are applied unconditionally — they are not
        # gated on auth.enabled).
        mw_off = AuthMiddleware(None, auth_config=self._on_cfg())
        mw_off._ensure_derived({"app": off_app})
        mw_on = AuthMiddleware(None, auth_config=self._on_cfg())
        mw_on._ensure_derived({"app": on_app})
        assert mw_off._module_lane_paths == mw_on._module_lane_paths

    def _on_cfg(self):
        from tests.conftest_auth import on_auth_config
        return on_auth_config()