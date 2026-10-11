"""OAuth2 merge-gate rider tests — PART A cheap minors (2026-10-11).

Covers the riders that are not big enough for their own file:

* **minor-6** — cookie parsing determinism: duplicate cookie names
  resolve FIRST-WINS (RFC 6265 §5.4), never last-wins (the residual
  C-2(a)-family ambiguity where a smuggled second ``ensemble_sid=``
  pair would override the legitimate one).
* **Unicode log-injection hardening** — the C-1 choke point
  (``sanitize_return_to``) also rejects U+0085 / U+2028 / U+2029
  (line-break controls ASCII filters miss).
* **Comparator bytes-compare** — the C-2 origin comparator's allow
  legs compare via constant-time byte equality.
* **Callback audit truncation** — the callback success audit leg caps
  the sanitized return_to at the 256-char audit budget.
* **Sources PUT-sentinel round-trip** — the ML-3 guard on the sources
  router (schedules leg lives in test_scheduler_api.py).
"""

from __future__ import annotations

import logging
from sqlalchemy.orm import Session as SqlaSession
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from daemon.api import create_app
from daemon.auth.cookie import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    parse_cookies_from_scope,
    validate_browser_lane_origin,
    CSRFOriginPolicy,
)
from daemon.middleware.auth import sanitize_return_to
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub


# ── minor-6: duplicate cookie names resolve FIRST-wins ──────────────


def _scope_with_cookie(raw: str) -> dict:
    return {
        "headers": [(b"cookie", raw.encode("latin-1"))],
    }


class TestMinor6CookieFirstWins:
    def test_duplicate_session_cookie_first_wins(self):
        raw = f"{SESSION_COOKIE_NAME}=legit; other=x; {SESSION_COOKIE_NAME}=smuggled"
        cookies = parse_cookies_from_scope(_scope_with_cookie(raw))
        assert cookies[SESSION_COOKIE_NAME] == "legit"

    def test_duplicate_names_across_any_cookie_first_wins(self):
        raw = f"{CSRF_COOKIE_NAME}=first; {CSRF_COOKIE_NAME}=second"
        cookies = parse_cookies_from_scope(_scope_with_cookie(raw))
        assert cookies[CSRF_COOKIE_NAME] == "first"

    def test_single_pair_behavior_unchanged(self):
        raw = f"{SESSION_COOKIE_NAME}=abc; theme=dark"
        cookies = parse_cookies_from_scope(_scope_with_cookie(raw))
        assert cookies == {SESSION_COOKIE_NAME: "abc", "theme": "dark"}

    def test_malformed_pairs_skipped_unchanged(self):
        raw = "nonsense; =empty-name; ok=1"
        cookies = parse_cookies_from_scope(_scope_with_cookie(raw))
        assert cookies == {"ok": "1"}

    def test_middleware_lane_uses_first_value(self, tmp_path):
        """The middleware cookie lane validates the FIRST value: a
        scope carrying a legitimate ``ensemble_sid`` followed by a
        smuggled duplicate dispatches (the first value validated); the
        reverse order (smuggled first) fails authentication."""
        import asyncio

        from daemon.middleware.auth import AuthMiddleware

        import warnings

        warnings.filterwarnings("ignore")
        stub = FakeOidcStub()
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)
        service = attach_auth_state(app, cfg, stub, tmp_path)
        session = asyncio.run(
            service.issue_session(subject="operator", email=None)
        )
        mw = AuthMiddleware(None, auth_config=cfg)

        async def _drive(raw_cookie: str):
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/instances",
                "headers": [(b"cookie", raw_cookie.encode("latin-1"))],
                "app": app,
                "client": ("127.0.0.1", 50000),
                "query_string": b"",
                "scheme": "http",
            }
            return await mw._authorize(scope)

        legit_first = (
            f"{SESSION_COOKIE_NAME}={session.id}; "
            f"{SESSION_COOKIE_NAME}=forged-value"
        )
        action = asyncio.run(_drive(legit_first))
        assert action[0] == "dispatch"
        assert action[1].subject == "operator"

        smuggled_first = (
            f"{SESSION_COOKIE_NAME}=forged-value; "
            f"{SESSION_COOKIE_NAME}={session.id}"
        )
        action2 = asyncio.run(_drive(smuggled_first))
        # The forged value never validates → the unauthenticated
        # contract (302/401), never a dispatch on the legit value.
        assert action2[0] in ("302", "401")


# ── unicode log-injection hardening (C-1 extension) ──────────────────


class TestUnicodeLineBreakHardening:
    @pytest.mark.parametrize(
        "label,snippet",
        [
            ("NEL", "\u0085"),
            ("LINE_SEPARATOR", "\u2028"),
            ("PARAGRAPH_SEPARATOR", "\u2029"),
        ],
    )
    def test_line_break_controls_rejected(self, label, snippet):
        value = f"/dashboard{snippet}FAKE [AuthAudit] event=session_issued"
        assert sanitize_return_to(value) == "/"

    def test_c0_and_del_still_rejected(self):
        assert sanitize_return_to("/a\r\nInjected: 1") == "/"
        assert sanitize_return_to("/a\x00b") == "/"
        assert sanitize_return_to("/a\x7fb") == "/"

    def test_benign_paths_still_pass(self):
        assert sanitize_return_to("/instances/abc?tab=logs") == (
            "/instances/abc?tab=logs"
        )
        assert sanitize_return_to(None) == "/"


# ── comparator bytes-compare (C-2 cheap minor) ───────────────────────


def _policy(**overrides) -> CSRFOriginPolicy:
    fields = dict(
        request_host="ensemble.example.com",
        request_scheme="https",
        host_is_trusted=True,
        localhost_carve_allowed=False,
        allowlisted_origins=frozenset(
            {"https://mirror.example.org", "https://other.example.net"}
        ),
    )
    fields.update(overrides)
    return CSRFOriginPolicy(**fields)


class TestComparatorBytesCompare:
    def test_allowlisted_origin_still_allows(self):
        headers = {"origin": "https://mirror.example.org"}
        assert validate_browser_lane_origin(headers, policy=_policy()) is True

    def test_same_origin_trusted_host_still_allows(self):
        headers = {
            "origin": "https://ensemble.example.com",
            "host": "ensemble.example.com",
        }
        assert validate_browser_lane_origin(headers, policy=_policy()) is True

    def test_foreign_origin_still_rejected(self):
        headers = {"origin": "https://evil.example.com"}
        assert validate_browser_lane_origin(headers, policy=_policy()) is False

    def test_prefix_lookalike_rejected(self):
        headers = {"origin": "https://mirror.example.org.evil.com"}
        assert validate_browser_lane_origin(headers, policy=_policy()) is False


# ── callback audit truncation ─────────────────────────────────────────


class TestCallbackAuditTruncation:
    def _client(self, tmp_path):
        import warnings

        warnings.filterwarnings("ignore")
        stub = FakeOidcStub()
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        return TestClient(
            app, base_url="http://testserver", follow_redirects=False
        ), app, stub

    def _login_state(self, client) -> str:
        from urllib.parse import parse_qs, urlsplit

        r = client.get("/api/auth/login", follow_redirects=False)
        assert r.status_code == 302
        return parse_qs(urlsplit(r.headers["location"]).query)["state"][0]

    def test_long_return_to_truncated_in_audit_log(self, tmp_path, caplog):
        client, app, stub = self._client(tmp_path)
        state = self._login_state(client)
        # Bind a LONG state-bound return_to (the trusted value) — the
        # audit leg must carry the 256-cap + marker, never the whole.
        from sqlalchemy import update

        from daemon.repositories.auth_session.models import AuthState

        long_path = "/" + "a" * 900
        repo = app.state.auth_session_repository
        with SqlaSession(repo._engine) as s:
            s.execute(
                update(AuthState)
                .where(AuthState.id == state)
                .values(redirect_to=long_path)
            )
            s.commit()

        with caplog.at_level(logging.INFO, logger="daemon.routers.auth"):
            r = client.get(
                f"/api/auth/callback?code=abc&state={state}",
                follow_redirects=False,
            )
        assert r.status_code == 302
        callback_lines = [
            rec.getMessage()
            for rec in caplog.records
            if "callback_success" in rec.getMessage()
        ]
        assert callback_lines, "expected the callback_success audit line"
        line = callback_lines[0]
        assert "...[TRUNCATED]" in line
        assert ("a" * 900) not in line


# ── sources PUT-sentinel round-trip (ML-3 guard, sources leg) ────────


class TestSourcesPutSentinelRoundTrip:
    """The sources router's PUT guard: an incoming webhook_secret equal
    to the WEBHOOK_SECRET_MASK sentinel keeps the STORED value — the
    mask never persists over the real secret, and the response masks."""

    def _app_with_source(self, tmp_path, stored_secret: str):
        """Auth-OFF app (the sentinel guard is auth-state-independent)
        with a mock manager carrying one webhook source."""
        import warnings

        warnings.filterwarnings("ignore")
        app = create_app()  # auth OFF — no middleware
        source = Mock()
        source.source_id = "src-1"
        source.source_type = "webhook"
        source.name = "Webhook Source"
        source.config = {
            "webhook_secret": stored_secret,
            "agent": "./agents/developer",
        }
        source.credentials = {}
        source.enabled = True
        source.autostart = True
        source.status = "stopped"
        source.error_message = None
        source.created_at = "2026-10-11T00:00:00+00:00"
        source.updated_at = "2026-10-11T00:00:00+00:00"

        manager = Mock()
        manager.is_write_paused = False
        manager._source_repository.get_source_config = Mock(return_value=source)
        manager._source_repository.update_source_config = Mock(return_value=source)
        manager._source_repository.get_latest_execution = Mock(return_value=None)
        manager.source_registry = Mock()
        manager.source_registry.get = Mock(return_value=None)
        app.state.manager = manager
        # The sources PUT path resolves the credential manager even when
        # the request carries no credentials (handler line ~347).
        app.state.credential_manager = Mock()
        app.state.credential_manager.encrypt = Mock(return_value="{}")
        return app, manager

    def test_put_sentinel_keeps_stored_secret_and_masks_response(
        self, tmp_path
    ):
        from daemon.routers.sources import WEBHOOK_SECRET_MASK

        app, manager = self._app_with_source(tmp_path, "the-real-secret")
        client = TestClient(
            app, base_url="http://testserver", follow_redirects=False
        )
        r = client.put(
            "/api/sources/src-1",
            json={
                "config": {
                    "agent": "./agents/developer",
                    "webhook_secret": WEBHOOK_SECRET_MASK,
                }
            },
        )
        assert r.status_code == 200
        persisted = manager._source_repository.update_source_config.call_args
        assert persisted.kwargs["config"]["webhook_secret"] == "the-real-secret"
        assert r.json()["config"]["webhook_secret"] == WEBHOOK_SECRET_MASK
        assert "the-real-secret" not in r.text
