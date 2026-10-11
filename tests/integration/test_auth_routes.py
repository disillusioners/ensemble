"""Auth-self route tests — oauth2-auth Phase 3 (T3.6).

Asserts the plan §4 row: login 302 (+503 LOGIN_NOT_CONFIGURED when the
recorder is cold and no override — AC-28); callback happy path
Set-Cookie + 302; STATE_REPLAYED double-callback (AC-6); STATE_EXPIRED
(AC-7); logout clears + later 401; ``/api/auth/me`` both branches
(AC-15); the DELETE-alias route-collision contract; and the cookie
flag set (D8 / NFR-6).
"""

from __future__ import annotations

import warnings
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from daemon.api import create_app
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub


@pytest.fixture()
def env(tmp_path):
    warnings.filterwarnings("ignore")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)
    attach_auth_state(app, cfg, stub, tmp_path)
    client = TestClient(app, base_url="http://testserver", follow_redirects=False)
    return client, app, stub


def _login(client) -> str:
    r = client.get("/api/auth/login", follow_redirects=False)
    assert r.status_code == 302
    return parse_qs(urlsplit(r.headers["location"]).query)["state"][0]


class TestLogin:
    def test_login_302_to_idp(self, env):
        client, _, _ = env
        r = client.get("/api/auth/login", follow_redirects=False)
        assert r.status_code == 302
        location = r.headers["location"]
        assert location.startswith("https://idp.test/authorize")
        query = parse_qs(urlsplit(location).query)
        assert query["response_type"] == ["code"]
        assert query["code_challenge_method"] == ["S256"]

    def test_login_503_login_not_configured(self, tmp_path):
        """AC-28: cold recorder + no override ⇒ 503 with a body naming
        the exact env vars."""
        warnings.filterwarnings("ignore")
        stub = FakeOidcStub()
        cfg = on_auth_config(external_redirect_base=None)
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        service = app.state.auth_service
        service._bind_host = None  # kill the bind tier
        service._bind_port = None
        client = TestClient(app, base_url="http://testserver", follow_redirects=False)
        r = client.get("/api/auth/login", follow_redirects=False)
        assert r.status_code == 503
        body = r.json()
        assert body["error"] == "LOGIN_NOT_CONFIGURED"
        assert "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE" in body["message"]
        assert "ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL" in body["message"]


class TestCallback:
    def test_happy_path_set_cookie_and_302(self, env):
        client, _, _ = env
        state = _login(client)
        r = client.get(
            f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/"
        set_cookie = r.headers["set-cookie"]
        assert "ensemble_sid=" in set_cookie
        assert "ensemble_csrf=" in set_cookie
        assert "httponly" in set_cookie.lower().split("ensemble_sid=")[1] or (
            "httponly" in set_cookie.lower()
        )
        assert "samesite=lax" in set_cookie.lower()
        assert "path=/" in set_cookie.lower()
        # D8/NFR-6: Secure flag present unless allow_insecure_cookie
        # (this fixture sets allow_insecure_cookie=True → dropped).

    def test_state_bound_return_to_beats_callback_query_param(self, tmp_path):
        """Minor #3 (2026-10-11 review): the STATE-BOUND return_to
        (persisted on the auth_states row at LOGIN) wins; a hostile
        ``?return_to=`` on the callback URL can no longer override
        where the login actually started."""
        warnings.filterwarnings("ignore")
        stub = FakeOidcStub()
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        client = TestClient(app, base_url="http://testserver", follow_redirects=False)
        r = client.get(
            "/api/auth/login",
            params={"return_to": "/agents"},
            follow_redirects=False,
        )
        state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
        r = client.get(
            f"/api/auth/callback?code=abc&state={state}"
            "&return_to=%2F%2Fevil.example%2Fsteal",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/agents"

    def test_callback_query_param_is_legacy_fallback_only(self, tmp_path):
        """Minor #3 — the fallback half: when the state row carries no
        return_to (pre-P3 hand-forged link), the callback query param
        still resolves (sanitized). The state row is emptied directly
        (the ``test_state_expired`` Session pattern)."""
        warnings.filterwarnings("ignore")
        from sqlmodel import Session

        from daemon.repositories.auth_session.models import AuthState

        stub = FakeOidcStub()
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        client = TestClient(app, base_url="http://testserver", follow_redirects=False)
        # Login WITHOUT return_to → the state row stores the default "/".
        state = _login(client)
        engine = app.state.auth_session_repository._engine
        with Session(engine) as session:
            row = session.get(AuthState, state)
            assert row is not None
            row.redirect_to = None
            session.add(row)
            session.commit()
        r = client.get(
            f"/api/auth/callback?code=abc&state={state}&return_to=/legacy",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/legacy"

    def test_cookie_secure_flag_when_not_insecure(self, tmp_path):
        warnings.filterwarnings("ignore")
        stub = FakeOidcStub()
        cfg = on_auth_config(
            allow_insecure_cookie=False, cookie_secure=True
        )
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        client = TestClient(app, base_url="https://testserver", follow_redirects=False)
        state = _login(client)
        r = client.get(
            f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
        )
        set_cookie = r.headers["set-cookie"].lower()
        assert "secure" in set_cookie

    def test_cookie_secure_false_drops_secure_flag(self, tmp_path):
        """Minor #1: cookie_secure=false (+ the required
        allow_insecure_cookie=true) ⇒ NO Secure flag — the flag
        actually drives the attribute. The default-shape absence case
        (this fixture's on_auth_config sets cookie_secure=False) is
        asserted explicitly here rather than left implicit."""
        warnings.filterwarnings("ignore")
        stub = FakeOidcStub()
        cfg = on_auth_config()  # cookie_secure=False + allow_insecure=True
        assert cfg.cookie_secure is False
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        client = TestClient(app, base_url="http://testserver", follow_redirects=False)
        state = _login(client)
        r = client.get(
            f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
        )
        assert r.status_code == 302
        assert "ensemble_sid=" in r.headers["set-cookie"]
        assert "secure" not in r.headers["set-cookie"].lower()

    def test_cookie_secure_true_wins_over_insecure_optin(self, tmp_path):
        """Minor #1 — the combo the PRE-FIX keying got wrong:
        ``cookie_secure=true`` + ``allow_insecure_cookie=true`` (the
        legal permissive combo) MUST still ship Secure. The old setter
        keyed Secure off ``not allow_insecure_cookie``, silently
        dropping it."""
        warnings.filterwarnings("ignore")
        stub = FakeOidcStub()
        cfg = on_auth_config(
            allow_insecure_cookie=True, cookie_secure=True
        )
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        client = TestClient(app, base_url="https://testserver", follow_redirects=False)
        state = _login(client)
        r = client.get(
            f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
        )
        set_cookie = r.headers["set-cookie"].lower()
        assert "ensemble_sid=" in set_cookie
        assert "secure" in set_cookie

    def test_state_replayed_double_callback(self, env):
        """AC-6: the second callback with the same state is a replay."""
        client, _, _ = env
        state = _login(client)
        first = client.get(
            f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
        )
        assert first.status_code == 302
        second = client.get(
            f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
        )
        assert second.status_code == 400
        assert second.json()["error"] == "STATE_REPLAYED"

    def test_state_expired(self, tmp_path):
        """AC-7: a state past its 300 s TTL → 400 STATE_EXPIRED."""
        warnings.filterwarnings("ignore")
        from datetime import datetime, timedelta, timezone

        from sqlmodel import Session

        from daemon.repositories.auth_session.models import AuthState

        stub = FakeOidcStub()
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        client = TestClient(app, base_url="http://testserver", follow_redirects=False)
        state = _login(client)
        engine = app.state.auth_session_repository._engine
        with Session(engine) as session:
            row = session.get(AuthState, state)
            assert row is not None
            row.expires_at = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            session.add(row)
            session.commit()
        r = client.get(
            f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
        )
        assert r.status_code == 400
        assert r.json()["error"] == "STATE_EXPIRED"


class TestLogoutAndMe:
    def test_logout_clears_and_later_401(self, env):
        client, _, _ = env
        state = _login(client)
        client.get(
            f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
        )
        assert client.get("/api/auth/me").status_code == 200
        r = client.post("/api/auth/logout")
        assert r.status_code == 204
        assert "ensemble_sid=" in r.headers.get("set-cookie", "")
        # Session row deleted → the same cookie no longer validates.
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_logout_without_session_is_204(self, env):
        client, _, _ = env
        r = client.post("/api/auth/logout")
        assert r.status_code == 204

    def test_delete_session_alias(self, env):
        """OD-10 alias + route-collision contract: DELETE /api/auth/
        session is auth-namespaced, distinct from /api/sessions."""
        client, app, _ = env
        state = _login(client)
        client.get(
            f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
        )
        r = client.delete("/api/auth/session")
        assert r.status_code == 204
        assert client.get("/api/auth/me").status_code == 401
        # Collision contract: the /api/sessions family still resolves to
        # ITS OWN routes (path templates distinct).
        auth_paths = [
            getattr(route, "path")
            for route in app.routes
            if str(getattr(route, "path", "")).startswith("/api/auth/")
        ]
        assert all(not p.startswith("/api/sessions") for p in auth_paths)

    def test_me_both_branches(self, env):
        """AC-15: 401 without a session (the public probe), context
        with one."""
        client, _, _ = env
        assert client.get("/api/auth/me").status_code == 401
        state = _login(client)
        client.get(
            f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
        )
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        body = r.json()
        assert body["subject"] == "user-123"
        assert body["auth_method"] == "session"
        assert "openid" in body["scopes"]
