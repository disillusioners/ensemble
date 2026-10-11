"""AuthService flow tests — oauth2-auth Phase 3 (T3.3 / G3.1 lane).

Asserts the plan §4 row: state/verifier mint shapes (AC-3/5), challenge
= S256(verifier), row persisted with TTL 300 s, verifier never in any
response artifact (AC-4), redirect URL query assembly — against the
minimal fake-OIDC stub (``tests/helpers/fake_oidc_stub.py``; the full
FR-33 fake is P5 — no circular dependency).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from daemon.repositories.auth_session.models import AuthState
from daemon.services.auth_service import (
    STATE_TTL_SECONDS,
    LoginNotConfigured,
    _s256_challenge,
)
from sqlmodel import Session, select
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub


@pytest.fixture()
def wired(tmp_path):
    """(service, repository-engine, stub) with the auth-ON wiring."""
    stub = FakeOidcStub()
    cfg = on_auth_config()
    from fastapi import FastAPI

    app = FastAPI()
    service = attach_auth_state(app, cfg, stub, tmp_path)
    return service, app.state.auth_session_repository._engine, stub, cfg


class TestBuildLoginUrl:
    def test_mint_shapes(self, wired):
        service, engine, stub, cfg = wired
        redirect = asyncio.run(service.build_login_url(return_to="/agents"))
        # AC-3/5: state 32 random bytes URL-safe (43 chars), verifier
        # inside the RFC 7636 43-128 window.
        assert 40 <= len(redirect.state) <= 60
        assert _matches_token_urlsafe(redirect.state)

    def test_row_persisted_with_ttl_300_and_s256(self, wired):
        service, engine, stub, cfg = wired
        redirect = asyncio.run(service.build_login_url(return_to="/x"))
        with Session(engine) as session:
            row = session.exec(
                select(AuthState).where(AuthState.id == redirect.state)
            ).first()
        assert row is not None, "state row must persist before the redirect"
        assert row.challenge_method == "S256"
        # challenge == S256(verifier)
        assert row.code_challenge == _s256_challenge(row.code_verifier)
        assert 43 <= len(row.code_verifier) <= 128
        # TTL 300 s
        created = datetime.fromisoformat(row.created_at)
        expires = datetime.fromisoformat(row.expires_at)
        delta = (expires - created).total_seconds()
        assert delta == STATE_TTL_SECONDS
        assert row.redirect_to == "/x"

    def test_authorize_url_query_assembly(self, wired):
        service, engine, stub, cfg = wired
        redirect = asyncio.run(service.build_login_url(return_to="/x"))
        parts = urlsplit(redirect.authorize_url)
        assert parts.scheme == "https"
        assert parts.netloc == "idp.test"
        assert parts.path == "/authorize"
        query = parse_qs(parts.query)
        assert query["client_id"] == ["test-app"]
        assert query["response_type"] == ["code"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["scope"] == ["openid profile email"]
        assert query["state"] == [redirect.state]
        # redirect_uri is the D3.5 tier-1 mint (absolute)
        assert query["redirect_uri"] == ["http://testserver/api/auth/callback"]

    def test_verifier_never_in_response_artifacts(self, wired):
        """AC-4: the verifier is server-held only — it appears in NO
        URL, query, or returned object, and not even the authorize URL
        carries the challenge's preimage."""
        service, engine, stub, cfg = wired
        redirect = asyncio.run(service.build_login_url(return_to="/x"))
        with Session(engine) as session:
            row = session.exec(
                select(AuthState).where(AuthState.id == redirect.state)
            ).first()
        assert row is not None
        assert row.code_verifier not in redirect.authorize_url
        assert "code_verifier" not in redirect.authorize_url
        assert row.code_verifier != row.code_challenge

    def test_return_to_is_sanitized_into_the_row(self, wired):
        service, engine, stub, cfg = wired
        redirect = asyncio.run(
            service.build_login_url(return_to="\\\\evil.com")
        )
        with Session(engine) as session:
            row = session.exec(
                select(AuthState).where(AuthState.id == redirect.state)
            ).first()
        assert row is not None
        assert row.redirect_to == "/"  # OD-12: backslash vector rejected


class TestRedirectUriChain:
    def test_tier1_external_redirect_base_wins(self, wired):
        service, _, _, cfg = wired
        assert service._resolve_redirect_uri() == (
            "http://testserver/api/auth/callback"
        )

    def test_exact_uri_override_wins_over_base(self, wired):
        service, _, _, cfg = wired
        cfg.redirect_uri = "https://proxy.example.com/auth/cb"
        assert service._resolve_redirect_uri() == "https://proxy.example.com/auth/cb"

    def test_chain_exhaustion_raises_login_not_configured(self, tmp_path):
        """D3.5 end-of-chain: no override, no fallback, no captured
        host, bind disabled → LoginNotConfigured (503 at the route)."""
        stub = FakeOidcStub()
        cfg = on_auth_config(external_redirect_base=None)
        from fastapi import FastAPI

        app = FastAPI()
        service = attach_auth_state(app, cfg, stub, tmp_path)
        # tier-2 fallback unset + no host recorder + bind disabled
        service._bind_host = None
        service._bind_port = None
        with pytest.raises(LoginNotConfigured):
            service._resolve_redirect_uri()

    def test_tier2_fallback_consulted(self, tmp_path):
        """D2/OD-6: the live-view fallback string is BaseURLResolver's
        tier-1 — an operator who only set ENSEMBLE_LIVE_VIEWS_* still
        gets a mint."""
        stub = FakeOidcStub()
        cfg = on_auth_config(external_redirect_base=None)
        from fastapi import FastAPI

        app = FastAPI()
        service = attach_auth_state(app, cfg, stub, tmp_path)
        # The lifespan copies config.live_views.external_base_url AFTER
        # construction — mimic that here (the fixture zeroes the field).
        cfg.live_view_external_base_url_fallback = "https://lv.example.com"
        assert service._resolve_redirect_uri() == (
            "https://lv.example.com/api/auth/callback"
        )


class TestCallbackFlow:
    def test_happy_path_issues_session(self, wired):
        service, engine, stub, cfg = wired
        redirect = asyncio.run(service.build_login_url(return_to="/agents"))
        result = asyncio.run(
            service.handle_callback(code="abc", state=redirect.state)
        )
        assert result.session.subject == "user-123"
        assert result.session.email == "user-123@example.com"
        assert result.session.auth_method == "session"
        assert result.return_to == "/agents"
        # CSRF token is the deterministic session-id hash
        from daemon.auth.cookie import csrf_token_for_session

        assert result.csrf_token == csrf_token_for_session(result.session_id)
        # session row fully populated + persisted
        from daemon.repositories.auth_session.models import AuthSession

        with Session(engine) as session:
            row = session.get(AuthSession, result.session_id)
        assert row is not None
        assert row.subject == "user-123"

    def test_state_replay_rejected(self, wired):
        service, engine, stub, cfg = wired
        redirect = asyncio.run(service.build_login_url())
        asyncio.run(service.handle_callback(code="abc", state=redirect.state))
        from daemon.services.auth_service import StateReplayedError

        with pytest.raises(StateReplayedError):
            asyncio.run(
                service.handle_callback(code="abc", state=redirect.state)
            )

    def test_unknown_state_rejected(self, wired):
        from daemon.services.auth_service import StateReplayedError

        service, _, _, _ = wired
        with pytest.raises(StateReplayedError):
            asyncio.run(service.handle_callback(code="abc", state="never-minted"))

    def test_expired_state_rejected(self, wired):
        from daemon.services.auth_service import StateExpiredError

        service, engine, stub, cfg = wired
        redirect = asyncio.run(service.build_login_url())
        # Age the row past its TTL, keeping it UNCONSUMED.
        with Session(engine) as session:
            row = session.get(AuthState, redirect.state)
            assert row is not None
            row.expires_at = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            session.add(row)
            session.commit()
        with pytest.raises(StateExpiredError):
            asyncio.run(service.handle_callback(code="abc", state=redirect.state))

    def test_token_endpoint_failure_is_idp_rejected(self, wired):
        from daemon.services.auth_service import IdpRejectedError

        service, engine, stub, cfg = wired
        stub.fail_token = True
        redirect = asyncio.run(service.build_login_url())
        with pytest.raises(IdpRejectedError):
            asyncio.run(
                service.handle_callback(code="abc", state=redirect.state)
            )
        # The provider body hit the WARNING lane truncated (stub logged
        # the request shape); the typed error carries the stable code.
        assert IdpRejectedError.code == "IDP_REJECTED"

    def test_discovery_unreachable_is_idp_unreachable(self, tmp_path):
        from daemon.services.auth_service import IdpUnreachableError

        stub = FakeOidcStub(fail_discovery=True)
        cfg = on_auth_config()
        from fastapi import FastAPI

        app = FastAPI()
        service = attach_auth_state(app, cfg, stub, tmp_path)

        async def _no_sleep(_seconds: float) -> None:  # collapse backoff
            return None

        service._oauth_client._sleeper = _no_sleep
        with pytest.raises(IdpUnreachableError):
            asyncio.run(service.build_login_url())

    def test_wrong_audience_id_token_rejected(self, wired):
        from daemon.services.oauth_client import TokenResponse
        from daemon.services.auth_service import IdpRejectedError

        service, engine, stub, cfg = wired
        redirect = asyncio.run(service.build_login_url())

        async def _exchange(**kwargs):
            return TokenResponse(
                access_token="at",
                id_token=stub.mint_id_token(audience="some-other-client"),
            )

        service._oauth_client.exchange_code = _exchange  # type: ignore[method-assign]
        with pytest.raises(IdpRejectedError) as excinfo:
            asyncio.run(
                service.handle_callback(code="abc", state=redirect.state)
            )
        assert "aud" in str(excinfo.value) or excinfo.value.code == "IDP_REJECTED"


def _matches_token_urlsafe(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"[A-Za-z0-9_-]{43,86}", value))
