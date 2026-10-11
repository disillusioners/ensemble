"""Webhook dual-credential — oauth2-auth Phase 4 (T4.5, §4 row 6).

**Superseded-shape reconciliation (AMEND v1.4 / module-lanes).** The
plan's §4 row names ``test_webhook_dual_credential.py``; the v1.3
"middleware-side webhook-secret dispatch" semantics it was written for
are SUPERSEDED (D4.6 / decisions.md D19 / FR-22a): ``/api/webhooks/*``
is a MODULE-LANE path — the OAuth2 middleware makes NO auth decision
on it in EITHER auth state, and the ``X-Webhook-Secret`` check is the
route's OWN pre-existing guard (webhooks.py:63-76, ``compare_digest``;
open ingest when unset; 401 on missing/mismatch). This file keeps the
plan-named name and asserts the v1.4 semantics:

* valid ``X-Webhook-Secret`` + no OAuth2 credential → 200 (auth ON) —
  the module lane, untouched by the middleware;
* invalid secret + no bearer → 401 (the MODULE guard, not the
  middleware's 401 contract);
* an OAuth2 session cookie or bearer is NOT accepted as a webhook
  credential (FR-40): a valid session alone does NOT authenticate a
  secret-configured webhook ingest.

(The full negative cross-lane matrix — AC-40 — lives in P5 T5.11.)
"""

from __future__ import annotations

import warnings
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from daemon.api import create_app
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub

WEBHOOK_SECRET = "whsec-test-material"


class _FakeRepo:
    def __init__(self, secret: str | None):
        self._secret = secret

    def get_source_config(self, source_id):
        if source_id != "src-wh":
            return None
        source = Mock()
        source.source_id = "src-wh"
        source.source_type = "webhook"
        source.name = "Webhook Source"
        source.config = {"webhook_secret": self._secret} if self._secret else {}
        return source


class _FakeAdapter:
    async def handle_webhook(self, payload, headers):
        return {"ok": True}


class _FakeManager:
    is_write_paused = False
    source_registry = {"src-wh": _FakeAdapter()}

    def __init__(self, secret: str | None):
        self._source_repository = _FakeRepo(secret)


@pytest.fixture()
def on_app_factory(tmp_path):
    def make(secret: str | None = WEBHOOK_SECRET):
        warnings.filterwarnings("ignore")
        stub = FakeOidcStub()
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)  # auth ON
        db_dir = tmp_path / f"db-{secret}"
        db_dir.mkdir(parents=True, exist_ok=True)
        attach_auth_state(app, cfg, stub, db_dir)
        app.state.manager = _FakeManager(secret)
        return app

    return make


@pytest.fixture()
def client(on_app_factory):
    app = on_app_factory()
    return TestClient(
        app, base_url="http://testserver", follow_redirects=False
    ), app


class TestWebhookModuleLaneV14:
    def test_valid_secret_no_bearer_200_with_auth_on(self, client):
        """THE parity leg: auth ON, no OAuth2 credential at all, valid
        module secret → the ingest ANSWERS 200 (the middleware never
        gated it; the module guard decided)."""
        http, _ = client
        r = http.post(
            "/api/webhooks/src-wh",
            json={"text": "hello"},
            headers={"X-Webhook-Secret": WEBHOOK_SECRET},
        )
        assert r.status_code == 200
        assert r.json()["received"] is True

    def test_invalid_secret_no_bearer_401_module_guard(self, client):
        http, _ = client
        r = http.post(
            "/api/webhooks/src-wh",
            json={"text": "hello"},
            headers={"X-Webhook-Secret": "wrong-secret"},
        )
        assert r.status_code == 401
        # The MODULE guard's body (INVALID_REQUEST), not the middleware's
        # AUTH_REQUIRED — the module owns this path's auth.
        assert "webhook secret" in r.text

    def test_missing_secret_on_configured_source_401(self, client):
        http, _ = client
        r = http.post("/api/webhooks/src-wh", json={"text": "hello"})
        assert r.status_code == 401

    def test_open_ingest_source_stays_open_with_auth_on(self, on_app_factory):
        """A source WITHOUT a configured secret keeps the pre-existing
        open-ingest posture in BOTH auth states (unchanged by this
        plan; runbook hardening note only)."""
        app = on_app_factory(secret=None)
        http = TestClient(app, base_url="http://testserver")
        r = http.post("/api/webhooks/src-wh", json={"text": "hello"})
        assert r.status_code == 200

    def test_origin_guard_composes_with_module_guard(self, on_app_factory):
        """NFR-14 composition: the middleware-level CSRF machinery does
        not apply (module lane passes through), and the module guard
        remains the sole credential check."""
        app = on_app_factory()
        http = TestClient(app, base_url="http://testserver")
        # A browser-shaped request (Origin present) with the right
        # secret still succeeds — no OAuth2 CSRF predicate on the lane.
        r = http.post(
            "/api/webhooks/src-wh",
            json={"text": "hello"},
            headers={
                "X-Webhook-Secret": WEBHOOK_SECRET,
                "Origin": "https://evil.example.com",
            },
        )
        assert r.status_code == 200


class TestOAuth2NotAWebhookCredential:
    def test_valid_session_cookie_does_not_authenticate_ingest(
        self, client, tmp_path
    ):
        """FR-40: an OAuth2 session is NOT a webhook credential — with a
        secret-configured source, a valid session cookie and NO secret
        still 401s (module guard decides)."""
        import asyncio

        http, app = client
        service = app.state.auth_service
        session = asyncio.run(service.issue_session(subject="operator"))
        r = http.post(
            "/api/webhooks/src-wh",
            json={"text": "hello"},
            cookies={"ensemble_sid": session.id},
        )
        assert r.status_code == 401
        assert "webhook secret" in r.text

    def test_valid_bearer_does_not_authenticate_ingest(self, client):
        """Same FR-40 leg on the machine lane: a valid mcpkb:admin
        bearer is not a webhook credential."""
        import hashlib

        from sqlmodel import Session

        from daemon.repositories.auth_session.models import BearerToken

        http, app = client
        token = "some-bearer-token"
        engine = app.state.auth_session_repository._engine
        with Session(engine) as s:
            s.add(
                BearerToken(
                    id_hash=hashlib.sha256(token.encode()).hexdigest(),
                    subject="machine",
                    scopes="mcpkb:admin,instances:write",
                    label="machine",
                    created_at="2026-10-11T00:00:00+00:00",
                    expires_at="2099-01-01T00:00:00+00:00",
                )
            )
            s.commit()
        r = http.post(
            "/api/webhooks/src-wh",
            json={"text": "hello"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401
        assert "webhook secret" in r.text
