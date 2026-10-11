"""Existing security-gates parity — oauth2-auth Phase 3 (T3.8, NFR-14).

Asserts the plan §4 row (AMEND v1.4): the webhook leg is MODULE-LANE
parity — the 401 comes from the MODULE guard on a source WITH a
configured secret (webhooks.py:63-76); a source WITHOUT a secret
remains open ingest (:67, pre-existing posture); an OAuth2 cookie or
bearer is NOT accepted as a webhook credential (FR-40 leg). The
maintenance Origin guard is unchanged (NFR-14).
"""

from __future__ import annotations

import hashlib
import warnings
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from daemon.api import create_app
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub

WEBHOOK_SECRET = "whsec_test_12345"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Auth-ON app + a webhook source WITH a secret + one WITHOUT.

    The source repository is the real manager-backed one — unavailable
    without the daemon lifespan — so this fixture injects a minimal
    in-memory source repository double carrying the two sources
    (the webhooks router reads ONLY ``get_source_config`` + the
    adapter registry; the 401-under-test fires before both).
    """
    warnings.filterwarnings("ignore")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)
    attach_auth_state(app, cfg, stub, tmp_path)
    client = TestClient(app, base_url="http://testserver", follow_redirects=False)

    class _Source:
        def __init__(self, source_id: str, config: dict):
            self.source_id = source_id
            self.source_type = "webhook"
            self.config = config

    class _SourceRepo:
        def __init__(self):
            self._sources = {
                "hook-with-secret": _Source(
                    "hook-with-secret",
                    {"webhook_secret": WEBHOOK_SECRET},
                ),
                "hook-open": _Source("hook-open", {}),
            }

        def get_source_config(self, source_id: str):
            return self._sources.get(source_id)

    class _Manager:
        def __init__(self, repo):
            self._source_repository = repo
            self.source_registry = {}
            self.is_write_paused = False

    app.state.manager = _Manager(_SourceRepo())

    # A valid session + a valid bearer (the FR-40 cross-lane probes).
    r = client.get("/api/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)

    token = "parity-bearer-token"
    with Session(app.state.auth_session_repository._engine) as session:
        session.add(
            _bearer_row(token)
        )
        session.commit()
    return client, app, token


def _bearer_row(token: str) -> BearerToken:
    from daemon.repositories.auth_session.models import BearerToken

    now = datetime.now(timezone.utc)
    return BearerToken(
        id_hash=hashlib.sha256(token.encode()).hexdigest(),
        subject="parity",
        scopes="jobs:read",
        label="t",
        created_at=now.isoformat(),
        expires_at=(now + timedelta(days=1)).isoformat(),
    )


from daemon.repositories.auth_session.models import BearerToken  # noqa: E402


class TestWebhookModuleLane:
    def test_webhook_without_secret_still_401_from_module_guard(self, env):
        """NFR-14 unchanged: missing/wrong X-Webhook-Secret on a source
        WITH a configured secret → the MODULE's 401 (not the OAuth2
        401-contract shape)."""
        client, app, _ = env
        r = client.post("/api/webhooks/hook-with-secret", json={"x": 1})
        assert r.status_code == 401
        # The module guard's error envelope (INVALID_REQUEST), NOT the
        # middleware's AUTH_REQUIRED — the OAuth2 layer never gated it.
        assert "webhook secret" in str(r.json()).lower()

    def test_webhook_with_correct_secret_passes_module(self, env):
        client, app, _ = env
        r = client.post(
            "/api/webhooks/hook-with-secret",
            json={"x": 1},
            headers={"X-Webhook-Secret": WEBHOOK_SECRET},
        )
        # The module guard PASSED; the request proceeds to the adapter
        # registry (503: no registry in this fixture) — never a
        # secret failure.
        assert r.status_code == 503
        assert "registry" in str(r.json()).lower()

    def test_open_ingest_source_remains_open(self, env):
        """webhooks.py:67 pre-existing posture: a source WITHOUT a
        configured secret accepts unauthenticated ingest (the module
        guard is the SOLE credential check)."""
        client, app, _ = env
        r = client.post("/api/webhooks/hook-open", json={"x": 1})
        assert r.status_code == 503  # module guard passed; no adapter
        assert "webhook secret" not in str(r.json()).lower()

    def test_oauth2_cookie_is_not_a_webhook_credential(self, env):
        """FR-40 (HARD): a valid OAuth2 session cookie does NOT
        satisfy the webhook secret check."""
        client, app, _ = env
        r = client.post(
            "/api/webhooks/hook-with-secret", json={"x": 1}
        )  # client carries the session cookie; NO X-Webhook-Secret
        assert r.status_code == 401
        assert "webhook secret" in str(r.json()).lower()

    def test_oauth2_bearer_is_not_a_webhook_credential(self, env):
        """FR-40 (HARD): a valid OAuth2 bearer token does NOT satisfy
        the webhook secret check."""
        client, app, token = env
        r = client.post(
            "/api/webhooks/hook-with-secret",
            json={"x": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401
        assert "webhook secret" in str(r.json()).lower()

    def test_webhook_lane_needs_no_oauth2_when_logged_out(self, tmp_path):
        """FR-42: the module lane behaves identically with NO session
        at all — the secret check alone decides."""
        warnings.filterwarnings("ignore")
        stub = FakeOidcStub()
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        fresh = TestClient(
            app, base_url="http://testserver", follow_redirects=False
        )

        class _Source:
            source_id = "hook-with-secret"
            source_type = "webhook"
            config = {"webhook_secret": WEBHOOK_SECRET}

        class _Repo:
            def get_source_config(self, source_id):
                return _Source() if source_id == "hook-with-secret" else None

        class _Manager:
            _source_repository = _Repo()
            source_registry = {}
            is_write_paused = False

        app.state.manager = _Manager()
        # No credentials at all: the middleware passed the path through
        # (module-lane), so the MODULE guard's 401 is what we see.
        r = fresh.post("/api/webhooks/hook-with-secret", json={"x": 1})
        assert r.status_code == 401
        assert "webhook secret" in str(r.json()).lower()


class TestMaintenanceOriginGuard:
    def test_origin_guard_unchanged_cross_origin_403(self, env, monkeypatch):
        """NFR-14: cross-origin POST to a guarded maintenance endpoint
        without a trusted origin → the SAME 403 as pre-build (the
        guard runs at the route layer, beneath the OAuth2 gate)."""
        client, app, _ = env
        r = client.post(
            "/api/maintenance/checkpoint-cleanup/run",
            headers={"Origin": "http://evil.example"},
        )
        assert r.status_code == 403
        assert "origin" in str(r.json()).lower()

    def test_maintenance_availability_canary_still_public(self, env):
        """§5.1: /api/maintenance/checkpoint-cleanup/availability is
        EXEMPT from both the Origin guard and auth (public-probe)."""
        client, app, _ = env
        r = client.get("/api/maintenance/checkpoint-cleanup/availability")
        assert r.status_code != 401
        assert r.status_code != 403
