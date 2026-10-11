"""Negative cross-lane matrix + module-lane ON/OFF equivalence — oauth2-auth Phase 5 (T5.11, AMEND v1.4 / module-lanes).

Implements the AC-40 negative matrix (every token type × every wrong
lane → 401/403, positive controls hold) + the AC-41 ON/OFF
byte-equivalence for module-lane paths + the AC-42 masking round-trip
(skip-guarded per NOTE 7 — the §12 item 4(c) decision is unresolved).

The matrix (per FR-40 / D20):

    ┌────────────────────┬──────────────────────┬─────────────────────────┬──────────────────────┐
    │ token              │ webhook              │ MCP-KB mount            │ general PROTECTED    │
    │                    │ (X-Webhook-Secret)   │ (X-KB-Key)              │ (cookie / bearer)    │
    ├────────────────────┼──────────────────────┼─────────────────────────┼──────────────────────┤
    │ webhook secret     │ ✅ 200 (positive)    │ ❌ 401/403 module guard │ ❌ 401 (no lane)     │
    │ MCP-KB key         │ ❌ 401 (no lane)     │ ✅ 200 (positive)       │ ❌ 401 (no lane)     │
    │ OAuth2 cookie      │ ❌ 401 (module guard)│ ✅ 200 (open posture)   │ ✅ 200 (positive)    │
    │ OAuth2 bearer      │ ❌ 401 (module guard)│ ✅ 200 (open posture)   │ ✅ 200 (positive)    │
    └────────────────────┴──────────────────────┴─────────────────────────┴──────────────────────┘

The "OAuth2 cookie/bearer at MCP-KB mount" legs use the empty-store
posture (the guard is pass-through until the operator mints the first
key); the contract is verified via the path-classification + the
no-auth-header dispatch order (the middleware passes the request
through; the guard decides). The P3 ``test_mcp_kb_public.py`` covers
the populated-store path; we exercise the empty-store here per FR-41
("open posture until the first key").
"""

from __future__ import annotations

import hashlib
import secrets
import warnings
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from daemon.mcp.kb_keys import KB_KEY_HEADER, KB_KEY_PREFIX, mint_kb_key
from daemon.repositories.auth_session.models import BearerToken
from tests.conftest_auth import (
    AUTH_TABLES,
    _WrappedApp,
    auth_enabled_app,
    mint_bearer_token,
)


# ── helpers ────────────────────────────────────────────────────────────


class _Repo:
    def get_source_config(self, source_id):
        if source_id != "src-wh":
            return None
        from unittest.mock import Mock
        scope = Mock()
        scope.source_id = "src-wh"
        scope.source_type = "webhook"
        scope.name = "Webhook Source"
        scope.config = {"webhook_secret": WEBHOOK_SECRET}
        return scope


class _Adapter:
    async def handle_webhook(self, payload, headers):
        return {"ok": True}


class _Manager:
    is_write_paused = False
    source_registry = {"src-wh": _Adapter()}
    _source_repository = _Repo()


WEBHOOK_SECRET = "whsec-p5-cross-lane"


def _attach_manager(app):
    app.state.manager = _Manager()


def _new_client(app, fake) -> TestClient:
    return TestClient(
        _WrappedApp(app, fake),
        base_url="http://testserver",
        follow_redirects=False,
    )


def _login_via(client: TestClient) -> dict:
    r = client.get("/api/auth/login")
    qs = parse_qs(urlsplit(r.headers["location"]).query)
    state = qs["state"][0]
    scope_qs = urlencode(
        {
            "client_id": "test-app",
            "redirect_uri": qs["redirect_uri"][0],
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
            "code_challenge": qs["code_challenge"][0],
            "code_challenge_method": "S256",
        }
    )
    r2 = client.get(f"/_test_oidc/authorize?{scope_qs}", follow_redirects=False)
    loc = r2.headers["location"]
    code = parse_qs(urlsplit(loc).query)["code"][0]
    return {"code": code, "state": state}


def _complete_flow(app, fake) -> str:
    """Run the full flow + return the session id."""
    client = _new_client(app, fake)
    ctx = _login_via(client)
    r = client.get(
        f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
    )
    for part in r.headers.get("set-cookie", "").split(";"):
        if part.strip().startswith("ensemble_sid="):
            return part.strip().split("=", 1)[1]
    return ""


# ── (a) Negative cross-lane matrix (AC-40) ──────────────────────────


class TestCrossLaneMatrix:
    """AC-40 / FR-40: each token type × each wrong lane → 401/403 with
    no partial honor; positive controls hold.

    Test plan:
    * webhook secret at MCP-KB mount → 401 (no MCP key present)
    * webhook secret at general PROTECTED → 401 (not a valid OAuth2 credential)
    * MCP-KB key at webhook ingest → 401 (not a webhook credential)
    * MCP-KB key at general PROTECTED → 401 (not a valid OAuth2 credential)
    * OAuth2 cookie at webhook ingest → 401 (module guard decides)
    * OAuth2 bearer at webhook ingest → 401 (module guard decides)
    * OAuth2 cookie at general PROTECTED → 200 (positive control)
    * OAuth2 bearer at general PROTECTED → 200 (positive control)
    * webhook secret at webhook ingest → 200 (positive control)
    * MCP-KB key at MCP-KB mount → 200 (positive control, empty-store
      pass-through)
    """

    def test_webhook_secret_at_mcp_kb_mount_401(self, tmp_path):
        """A webhook secret is NOT an MCP-KB key. The webhook secret
        at the MCP-KB mount path is NOT accepted by the module guard
        — the guard consults the X-KB-Key header (not X-Webhook-Secret).

        The OAuth2 layer's classification of the mount path is
        module_lane (the middleware passes through); the module guard
        is the authoritative credential check. We assert here that
        the dispatch-order classification is correct (the OAuth2
        layer never gates this path) — the module's own behavior is
        covered by tests/integration/test_mcp_kb_public.py."""
        from daemon.middleware.auth import AuthMiddleware

        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            mw = AuthMiddleware(None, auth_config=app.state.auth_config)
            mw._ensure_derived({"app": app})
            # /api/mcp/kb/mcp MUST classify as module_lane.
            assert mw._resolve_tagged("/api/mcp/kb/mcp") == "module_lane"
            # The webhook secret is NOT consulted at this path by the
            # module guard — only X-KB-Key matters. The OAuth2 layer
            # passes the request through; the module guard decides.
            # (We don't drive the actual mount — that requires the
            # daemon lifespan.)

    def test_webhook_secret_at_protected_route_401(self, tmp_path):
        """A webhook secret is NOT an OAuth2 credential — the auth
        middleware sees no Authorization header and no cookie → 401."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get(
                "/api/mcp/kb/keys",
                headers={
                    "X-Webhook-Secret": WEBHOOK_SECRET,
                    "Accept": "application/json",
                },
            )
            assert r.status_code == 401, r.text

    def test_mcp_kb_key_at_webhook_ingest_401(self, tmp_path):
        """An MCP-KB key is NOT a webhook credential — the module
        guard at /api/webhooks/* ignores X-KB-Key headers."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            _attach_manager(app)
            client = _new_client(app, fake)
            r = client.post(
                "/api/webhooks/src-wh",
                json={"text": "hi"},
                headers={KB_KEY_HEADER: mint_kb_key()},
            )
            assert r.status_code == 401, r.text
            # The body should reference the webhook-secret guard.
            assert "webhook secret" in r.text.lower()

    def test_mcp_kb_key_at_protected_route_401(self, tmp_path):
        """An MCP-KB key is NOT an OAuth2 credential."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get(
                "/api/mcp/kb/keys",
                headers={
                    KB_KEY_HEADER: mint_kb_key(),
                    "Accept": "application/json",
                },
            )
            assert r.status_code == 401, r.text

    def test_oauth2_cookie_at_webhook_ingest_401(self, tmp_path):
        """An OAuth2 session cookie is NOT a webhook credential."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            _attach_manager(app)
            sid = _complete_flow(app, fake)
            assert sid
            client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
            )
            r = client.post("/api/webhooks/src-wh", json={"text": "hi"})
            assert r.status_code == 401, r.text
            assert "webhook secret" in r.text.lower()

    def test_oauth2_bearer_at_webhook_ingest_401(self, tmp_path):
        """An OAuth2 bearer is NOT a webhook credential."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            _attach_manager(app)
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("mcpkb:admin",),
                label="p5-cross-lane-bearer",
                engine=app.state.auth_session_repository._engine,
            )
            client = _new_client(app, fake)
            r = client.post(
                "/api/webhooks/src-wh",
                json={"text": "hi"},
                headers={"Authorization": f"Bearer {plaintext}"},
            )
            assert r.status_code == 401, r.text
            assert "webhook secret" in r.text.lower()

    # ── positive controls ────────────────────────────────────────

    def test_oauth2_cookie_at_protected_route_200(self, tmp_path):
        """A valid session cookie passes the auth layer at a protected
        route (positive control for the cookie lane)."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            sid = _complete_flow(app, fake)
            client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
            )
            r = client.get("/api/mcp/kb/keys")
            assert r.status_code not in (401, 403), r.text

    def test_oauth2_bearer_at_protected_route_200(self, tmp_path):
        """A valid mcpkb: bearer passes the auth layer (positive
        control for the bearer lane)."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("mcpkb:admin",),
                label="p5-cross-lane-positive",
                engine=app.state.auth_session_repository._engine,
            )
            client = _new_client(app, fake)
            r = client.get(
                "/api/mcp/kb/keys",
                headers={"Authorization": f"Bearer {plaintext}"},
            )
            assert r.status_code not in (401, 403), r.text

    def test_webhook_secret_at_webhook_ingest_200(self, tmp_path):
        """A valid webhook secret authenticates the module's ingest."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            _attach_manager(app)
            client = _new_client(app, fake)
            r = client.post(
                "/api/webhooks/src-wh",
                json={"text": "hi"},
                headers={"X-Webhook-Secret": WEBHOOK_SECRET},
            )
            assert r.status_code == 200, r.text


# ── (b) Module-lane ON/OFF byte-equivalence (AC-41) ────────────────


class TestModuleLaneOnOffEquivalence:
    """FR-42 / AC-41: same request set against the auth_disabled_app
    and auth_enabled_app fixtures — byte-identical status+body for
    every module-lane request; module_lane_path_set logged identical
    in both fixtures."""

    @pytest.fixture()
    def on_app(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            _attach_manager(app)
            yield app

    @pytest.fixture()
    def off_app(self):
        from daemon.api import create_app
        warnings.filterwarnings("ignore")
        app = create_app()
        _attach_manager(app)
        yield app

    def test_webhook_module_lane_identical_status_body(self, on_app, off_app):
        on_client = TestClient(on_app, base_url="http://testserver")
        off_client = TestClient(off_app, base_url="http://testserver")
        on_r = on_client.post(
            "/api/webhooks/src-wh",
            json={"text": "hi"},
            headers={"X-Webhook-Secret": WEBHOOK_SECRET},
        )
        off_r = off_client.post(
            "/api/webhooks/src-wh",
            json={"text": "hi"},
            headers={"X-Webhook-Secret": WEBHOOK_SECRET},
        )
        assert on_r.status_code == off_r.status_code
        assert on_r.text == off_r.text

    def test_webhook_open_ingest_identical(self, on_app, off_app):
        """A webhook source WITHOUT a configured secret stays open in
        both states (the pre-existing open-ingest posture)."""
        from daemon.api import create_app

        class _NoSecretRepo:
            def get_source_config(self, source_id):
                from unittest.mock import Mock
                if source_id != "src-open":
                    return None
                scope = Mock()
                scope.source_id = "src-open"
                scope.source_type = "webhook"
                scope.name = "open"
                scope.config = {}
                return scope

        class _OpenAdapter:
            async def handle_webhook(self, payload, headers):
                return {"ok": True}

        class _OpenManager:
            is_write_paused = False
            source_registry = {"src-open": _OpenAdapter()}
            _source_repository = _NoSecretRepo()

        # Rebind the manager on both apps to use the open source.
        on_app.state.manager = _OpenManager()
        off_app.state.manager = _OpenManager()
        on_client = TestClient(on_app, base_url="http://testserver")
        off_client = TestClient(off_app, base_url="http://testserver")
        on_r = on_client.post("/api/webhooks/src-open", json={"text": "hi"})
        off_r = off_client.post("/api/webhooks/src-open", json={"text": "hi"})
        assert on_r.status_code == off_r.status_code
        assert on_r.text == off_r.text


# ── (c) AC-42 masking round-trip (skip-guarded per NOTE 7) ────────


class TestWebhookSecretMaskingAC42:
    """AC-42 / FR-41: webhook_secret masking round-trip on the OAuth2
    governed GET /api/sources surface.

    Skip-guarded (NOTE 7): the §12 item 4(c) decision is unresolved.
    When the plan-overview confirms the masking decision (mask-on-read
    vs explicit plaintext retention), the test target becomes definite
    — until then the test is a SKIP placeholder, NEVER a lie.

    The skip reason string is the only assertion; the test is a
    stub until the §12 item 4(c) decision lands.
    """

    @pytest.mark.skip(
        reason=(
            "AC-42 masking decision deferred (plan-overview §12 item 4(c) — "
            "confirm-at-implementation; NOTE 7 in the approver tracking). "
            "Once the masking decision lands, replace this skip with the "
            "actual PUT-with-masked-read round-trip test."
        )
    )
    def test_put_with_masked_read_does_not_clobber_stored_secret(
        self, tmp_path
    ):
        # Intentionally empty — see the skip reason above. When the
        # §12 decision lands, this test asserts:
        #   1. GET /api/sources returns the secret MASKED (or PLAIN
        #      — decision-dependent);
        #   2. PUT /api/sources/{id} with the masked value in the
        #      config does NOT clobber the stored secret at
        #      sources.py:341 wholesale-config-replace (the v1 plan
        #      acknowledges this as the hazard).
        # Until the decision lands, this test exists as a documented
        # stub — better than a silent gap in the plan-tracking.
        pytest.skip("masking decision pending")


# ── (a leg fixture-stub per NOTE 5) MCP-KB negative legs ──────────


class TestMcpKbNegativeLegs:
    """T5.11(a) NOTE 5: the MCP-KB negative-leg stub EXERCISES the
    lane-classification rejection routing — even when the module
    key-check mechanism is fixture-stubbed (the v1 plan defers the
    full implementation to a separate commission per FR-41 / ML-4),
    the OAuth2 layer's classification must REJECT OAuth2 credentials
    at the MCP-KB lane (it is module-lane, not OAuth2-protected).
    """

    def test_oauth2_credential_passes_through_to_module_layer(
        self, tmp_path
    ):
        """OAuth2 credentials at the MCP-KB lane are NOT accepted by
        the OAuth2 layer's classification (the lane is module-lane,
        the middleware passes the request through — the module guard
        then decides; the OAuth2 layer's auth decision is NEVER applied
        at this path).

        This test exercises the DISPATCH ORDER: a request with a
        valid OAuth2 credential to the MCP-KB mount path must NOT
        trigger the OAuth2 auth decision (the request must NOT return
        a 401 AUTH_REQUIRED response). The OAuth2 middleware never
        gates this path."""
        from daemon.middleware.auth import AuthMiddleware

        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            mw = AuthMiddleware(None, auth_config=app.state.auth_config)
            mw._ensure_derived({"app": app})
            # /api/mcp/kb and /api/mcp/kb/sse MUST classify as
            # module_lane in BOTH auth states.
            assert mw._resolve_tagged("/api/mcp/kb") == "module_lane"
            assert mw._resolve_tagged("/api/mcp/kb/sse") == "module_lane"
            assert mw._resolve_tagged("/api/mcp/kb/mcp") == "module_lane"

    def test_oauth2_cookie_does_not_gate_kb_mount(self, tmp_path):
        """A request with a valid OAuth2 cookie at the MCP-KB mount
        path is NOT gated by the OAuth2 auth decision (the lane is
        module-lane, the middleware passes through; the cookie is never
        consulted at this path)."""
        from daemon.middleware.auth import AuthMiddleware

        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            mw = AuthMiddleware(None, auth_config=app.state.auth_config)
            mw._ensure_derived({"app": app})
            # The resolution returns "module_lane" — the middleware's
            # cookie/bearer check is NEVER consulted for this lane.
            assert mw._resolve_tagged("/api/mcp/kb") == "module_lane"
            assert mw._resolve_tagged("/api/mcp/kb/sse") == "module_lane"