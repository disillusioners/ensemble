"""Bearer-lane P5 contract tests — oauth2-auth Phase 5 (T5.3, §4 row 3).

End-to-end bearer-lane exercise against the FakeOidcProvider. Sister
suite to ``tests/unit/test_auth_cli.py`` (Phase 4) — the P4 tests
cover the CLI's mint/revoke against a bare repository; this P5 suite
covers the BEARER VERIFY path through the AuthMiddleware + OAuthClient
against the in-process IdP.

Reconciliation rule: P5 adds (does not modify) existing files. The
existing P4 tests cover mint/revoke/shape; this file covers verify +
dispatch + the FR-13 bearer-over-cookie precedence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from daemon.config import AuthConfig
from daemon.repositories.auth_session.models import BearerToken
from daemon.repositories.mcp_kb_keys.models import McpKbKey  # noqa: F401
from daemon.services.oauth_client import OAuthClient
from tests.conftest_auth import (
    AUTH_TABLES,
    TEST_CLIENT_ID,
    TEST_ISSUER,
    _WrappedApp,
    auth_enabled_app,
    mint_bearer_token,
    on_auth_config,
)
from tests.fakes.fake_oidc_provider import FakeOidcProvider

# Tables the auth-enabled fixture creates (extended with McpKbKey).
ALL_TABLES = AUTH_TABLES + [McpKbKey.__table__]


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
            "client_id": TEST_CLIENT_ID,
            "redirect_uri": qs["redirect_uri"][0],
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
            "code_challenge": qs["code_challenge"][0],
            "code_challenge_method": "S256",
        }
    )
    r2 = client.get(f"/_test_oidc/authorize?{scope_qs}", follow_redirects=False)
    code = parse_qs(urlsplit(r2.headers["location"]).query)["code"][0]
    return {"code": code, "state": state}


# ── bearer verify path ──────────────────────────────────────────────


class TestBearerVerify:
    def test_bearer_authenticates_protected_route(self, tmp_path):
        """A valid bearer header reaches the route handler — the auth
        layer passes. We probe /api/mcp/kb/keys (an OAuth2-managed
        route with an owner-scoped CRUD family) since the handlers
        there are minimal.

        Even though the manager fixture is incomplete and a 500 from
        the handler is expected, the AUTH LAYER's decision must be
        PASS — we assert this by checking that the response is NOT
        a 401/403 (which would mean the auth layer rejected us)."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("mcpkb:admin",),
                label="p5-bearer-1",
                engine=app.state.auth_session_repository._engine,
            )
            client = _new_client(app, fake)
            r = client.get(
                "/api/mcp/kb/keys",
                headers={"Authorization": f"Bearer {plaintext}"},
            )
            assert r.status_code not in (401, 403), r.text

    def test_bearer_authentication_logged_at_info(self, tmp_path, caplog):
        """The middleware emits ``[AuthAudit] event=bearer_validated``."""
        import logging
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("mcpkb:admin",),
                label="p5-bearer-audit",
                engine=app.state.auth_session_repository._engine,
            )
            client = _new_client(app, fake)
            with caplog.at_level(logging.INFO, logger="daemon.middleware.auth"):
                client.get(
                    "/api/mcp/kb/keys",
                    headers={"Authorization": f"Bearer {plaintext}"},
                )
            messages = [r.message for r in caplog.records]
            assert any("bearer_validated" in m for m in messages)

    def test_invalid_bearer_returns_401(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get(
                "/api/instances",
                headers={"Authorization": "Bearer not-a-real-token"},
            )
            assert r.status_code == 401
            assert "invalid_token" in r.headers.get("www-authenticate", "")

    def test_revoked_bearer_returns_401(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("instances:read",),
                label="p5-bearer-revoked",
                engine=app.state.auth_session_repository._engine,
            )
            # Revoke via SQLModel (the CLI is tested separately in P4).
            id_hash = hashlib.sha256(plaintext.encode()).hexdigest()
            from datetime import datetime, timezone
            with Session(app.state.auth_session_repository._engine) as s:
                row = s.get(BearerToken, id_hash)
                row.revoked_at = datetime.now(timezone.utc).isoformat()
                s.add(row)
                s.commit()
            client = _new_client(app, fake)
            r = client.get(
                "/api/instances",
                headers={"Authorization": f"Bearer {plaintext}"},
            )
            assert r.status_code == 401

    def test_bearer_over_cookie_precedence(self, tmp_path):
        """FR-13: a bearer header with an INVALID token REJECTS the
        request even when the cookie lane could have authenticated
        (a stale cookie). Bearer NEVER falls back to cookie silently."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            # Mint a real session cookie via the flow.
            client = _new_client(app, fake)
            ctx = _login_via(client)
            r = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            sid = ""
            for part in r.headers.get("set-cookie", "").split(";"):
                if part.strip().startswith("ensemble_sid="):
                    sid = part.strip().split("=", 1)[1]
            assert sid
            # Now send BOTH a valid session cookie AND an invalid bearer.
            client2 = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
            )
            r = client2.get(
                "/api/mcp/kb/keys",
                headers={"Authorization": "Bearer invalid-bearer-token"},
            )
            # Bearer wins → invalid bearer → 401. NOT 200 (no silent
            # fall-through to cookie).
            assert r.status_code == 401


# ── scope enforcement ──────────────────────────────────────────────


class TestScopeEnforcement:
    def test_bearer_without_required_scope_returns_403(self, tmp_path):
        """A bearer with insufficient scope hits the decorated
        family's @require_scope and returns 403 SCOPE_INSUFFICIENT."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            # Mint with a scope that does NOT cover mcpkb:admin.
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("system:read",),  # missing mcpkb:admin
                label="p5-bearer-noscope",
                engine=app.state.auth_session_repository._engine,
            )
            client = _new_client(app, fake)
            r = client.get(
                "/api/mcp/kb/keys",
                headers={"Authorization": f"Bearer {plaintext}"},
            )
            # The decorated route's @require_scope dependency returns
            # 403 SCOPE_INSUFFICIENT for a token whose scope set
            # doesn't include mcpkb:admin.
            assert r.status_code == 403, r.text

    def test_bearer_with_required_scope_passes_auth_layer(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("mcpkb:admin",),
                label="p5-bearer-mcpkb",
                engine=app.state.auth_session_repository._engine,
            )
            client = _new_client(app, fake)
            r = client.get(
                "/api/mcp/kb/keys",
                headers={"Authorization": f"Bearer {plaintext}"},
            )
            # The handler may 5xx if the manager fixture is incomplete;
            # we assert the auth layer passed (NOT 401/403).
            assert r.status_code not in (401, 403), r.text

    def test_session_lane_carries_full_operator_grant(self, tmp_path):
        """D9: the session lane persists the configured OIDC scopes ∪
        the operator family, so a logged-in human authenticates to every
        decorated route (no 403)."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            ctx = _login_via(client)
            r = client.get(
                f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
            )
            sid = ""
            for part in r.headers.get("set-cookie", "").split(";"):
                if part.strip().startswith("ensemble_sid="):
                    sid = part.strip().split("=", 1)[1]
            client2 = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
            )
            r2 = client2.get("/api/mcp/kb/keys")
            assert r2.status_code not in (401, 403), r2.text


# ── bootstrap consumption (one-shot via CLI) ───────────────────────


class TestBootstrapConsumption:
    def test_bootstrap_token_secret_str_no_leak_in_stdout(
        self, tmp_path, monkeypatch, capsys
    ):
        """The CLI never logs the bootstrap token material in any
        output stream (stdout/stderr/exit message). NFR-12 hygiene."""
        # Monkeypatch the DB path the CLI uses. The CLI reads from the
        # live ensemble config — instead we just assert the secret is
        # never echoed when we issue a bootstrap mint through a
        # fresh DB scope.
        env_value = "bootstrap-material-p5-2026"
        monkeypatch.setenv("ENSEMBLE_AUTH_BOOTSTRAP_TOKEN", env_value)

        # This test only proves the SECRET HYGIENE — the P4 test_cli
        # already covers the full bootstrap consumption contract via
        # the real DB. Here we just verify that the env var value
        # never appears in any captured output.
        from daemon.auth.cli import main as cli_main

        # We can't actually issue (no DB) — assert that the rejection
        # path names "missing" or "DB" but NEVER the env value.
        try:
            cli_main(["issue-token", "--label", "x", "--bootstrap"])
        except SystemExit:
            pass
        captured = capsys.readouterr()
        assert env_value not in captured.out
        assert env_value not in captured.err