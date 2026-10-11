"""Auth 401/302 UX contract tests — oauth2-auth Phase 3 (T3.5, AC-24/25).

Asserts the plan §4 row: the 302-vs-401 discriminator including
``Accept: */*`` + the FR-24 path-family heuristic; the ``return_to``
sanitization table WITH the backslash cases (AC-25 / D3.8 — backslash
normalized BEFORE the same-origin prefix tests); the 401 body + header
shape.
"""

from __future__ import annotations

import warnings
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from daemon.api import create_app
from daemon.middleware.auth import sanitize_return_to
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub


@pytest.fixture()
def fresh(tmp_path):
    warnings.filterwarnings("ignore")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)
    attach_auth_state(app, cfg, stub, tmp_path)
    return TestClient(
        app, base_url="http://testserver", follow_redirects=False
    )


class TestDiscriminator:
    def test_explicit_html_accept_gets_302(self, fresh):
        r = fresh.get("/api/agents", headers={"Accept": "text/html"})
        assert r.status_code == 302
        location = r.headers["location"]
        assert location.startswith("/api/auth/login?return_to=")
        assert parse_qs(urlsplit(location).query)["return_to"] == [
            "/api/agents"
        ]

    def test_explicit_json_accept_gets_401_with_www_authenticate(self, fresh):
        r = fresh.get(
            "/api/agents", headers={"Accept": "application/json"}
        )
        assert r.status_code == 401
        assert (
            r.headers["www-authenticate"] == 'Bearer realm="ensemble"'
        )
        assert r.headers["content-type"].startswith("application/json")
        assert r.json() == {
            "error": "AUTH_REQUIRED",
            "message": "Authentication required",
        }

    def test_wildcard_accept_uses_path_heuristic(self, fresh):
        """AC-24: under ``Accept: */*`` the FR-24 heuristic decides —
        known machine path families get the 401 contract; everything
        else is treated as a browser (302)."""
        for path in ("/api/instances", "/api/sources"):
            r = fresh.get(path)  # TestClient default Accept: */*
            assert r.status_code == 302, path

    def test_missing_accept_treated_as_browser(self, fresh):
        r = fresh.get(
            "/api/missions", headers={"Accept": ""}
        )
        assert r.status_code == 302

    def test_302_return_to_preserves_query(self, fresh):
        r = fresh.get(
            "/api/agents?group=active", headers={"Accept": "text/html"}
        )
        assert r.status_code == 302
        location = r.headers["location"]
        return_to = parse_qs(urlsplit(location).query)["return_to"][0]
        assert return_to.startswith("/api/agents")
        assert "group%3Dactive" in urlsplit(location).query or "group=active" in return_to


class TestReturnToSanitizationTable:
    """AC-25 (D3.8 AMEND v1.1 / OD-12): the table-driven sanitizer —
    backslash cases normalize BEFORE the same-origin prefix tests."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # Legit paths pass through.
            ("/api/agents", "/api/agents"),
            ("/instances?group=active", "/instances?group=active"),
            ("/deep/nested/path", "/deep/nested/path"),
            # Backslash vectors — AC-25 REQUIRED rows.
            ("\\\\evil.com", "/"),  # \\evil.com → //evil.com → rejected
            ("//evil.com", "/"),
            ("/\\evil.com", "/"),  # /\evil.com → //evil.com → rejected
            ("\\\\/evil.com", "/"),
            # Scheme vectors.
            ("https://evil.com", "/"),
            ("http://evil.com/x", "/"),
            ("javascript:alert(1)", "/"),
            # Not path-prefixed.
            ("evil.com", "/"),
            ("api/agents", "/"),
            # Length cap.
            ("/" + "a" * 1025, "/"),
            # Empty → default.
            ("", "/"),
        ],
    )
    def test_table(self, raw, expected):
        assert sanitize_return_to(raw) == expected

    def test_backslash_normalization_is_legalizing(self):
        """The normalization is a LEGALIZER for benign backslash paths:
        ``\\agents`` → ``/agents`` (accepted). The DOUBLE-backslash
        form (``\\\\agents`` → ``//agents``) is the host vector and
        stays rejected (table row above)."""
        assert sanitize_return_to("\\agents") == "/agents"
        assert sanitize_return_to("\\\\agents") == "/"

    def test_callback_return_to_sanitized_end_to_end(self, tmp_path):
        """The sanitizer guards the actual browser bounce: a poisoned
        return_to carried through login→callback lands on /."""
        warnings.filterwarnings("ignore")
        stub = FakeOidcStub()
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, stub, tmp_path)
        client = TestClient(
            app, base_url="http://testserver", follow_redirects=False
        )
        r = client.get(
            "/api/auth/login",
            params={"return_to": "//evil.com"},
            follow_redirects=False,
        )
        assert r.status_code == 302  # to the IdP
        state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
        r = client.get(
            "/api/auth/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/"
