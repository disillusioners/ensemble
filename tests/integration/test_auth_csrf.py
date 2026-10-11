"""C1 CSRF predicate behavioral legs — oauth2-auth Phase 3 (T3.4 / AC-39).

The major P3-review item: the predicate in ``daemon/auth/cookie.py``
(csrf_applies / validate_browser_lane_origin / validate_csrf_header)
plus its middleware application (``daemon/middleware/auth.py``
``_csrf_verdict``) previously had NO behavioral test — this file runs
the REAL ``create_app(auth ON)`` composition (the auth-fixture harness)
through every D8-v1.2 C1 leg:

(a) same-origin Origin + session cookie + state-changing request
    SUCCEEDS — AC-39: the unchanged-SPA mutation allowed BY CONSTRUCTION;
(b) cross-origin Origin + cookie → 403 ``CSRF_ORIGIN_MISMATCH``;
(c) headerless (no Origin AND no Referer) requests are EXEMPT — the
    cookie lane without browser headers proceeds, the bearer lane is
    exempt entirely (no ambient authority), and headerless with NO
    credential gets the 401 machine contract (never 403);
(d) ``X-CSRF-Token`` echo: matching header vs the readable
    ``ensemble_csrf`` cookie passes; mismatched → 403
    ``CSRF_TOKEN_MISMATCH``; the echo is consulted ONLY when the
    header is present (OD-15); the origin check precedes the echo;
(e) Referer fallback (no Origin): same-origin Referer passes,
    cross-origin Referer → 403;
(f) present-but-EMPTY Origin → fail-closed 403 (cookie.py:192-195);
(g) GET (the SSE method) is exempt — state-changing methods only.
"""

from __future__ import annotations

import warnings
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from daemon.api import create_app
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub

SAME_ORIGIN = "http://testserver"
CROSS_ORIGIN = "http://evil.example"


@pytest.fixture()
def csrf_env(tmp_path):
    """Auth-ON real app + a test-local PROTECTED mutation probe.

    ``/api/_csrf_probe`` is added AFTER ``create_app`` (real wiring)
    and BEFORE the first request (the middleware's route walk derives
    at the first HTTP scope, so the probe is visible). Untagged routes
    default to PROTECTED (AC-23 default-deny) — the probe is a clean
    2xx mutation target that no other guard (maintenance Origin guard,
    webhook secrets) can intercept.
    """
    warnings.filterwarnings("ignore")
    stub = FakeOidcStub()
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)

    @app.post("/api/_csrf_probe")
    async def _probe_post() -> dict:
        return {"ok": True, "method": "POST"}

    attach_auth_state(app, cfg, stub, tmp_path)
    client = TestClient(app, base_url=SAME_ORIGIN, follow_redirects=False)

    # Mint a real session (sets ensemble_sid + readable ensemble_csrf).
    r = client.get("/api/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert client.cookies.get("ensemble_sid")
    csrf_cookie = client.cookies.get("ensemble_csrf")
    assert csrf_cookie

    fresh = TestClient(app, base_url=SAME_ORIGIN, follow_redirects=False)

    # Mint a bearer row (the lane-exemption leg needs a valid bearer).
    import hashlib
    from datetime import datetime, timedelta, timezone

    from sqlmodel import Session

    from daemon.repositories.auth_session.models import BearerToken

    token = "csrf-bearer-token"
    now = datetime.now(timezone.utc)
    with Session(app.state.auth_session_repository._engine) as session:
        session.add(
            BearerToken(
                id_hash=hashlib.sha256(token.encode()).hexdigest(),
                subject="csrf-machine",
                scopes="jobs:read",
                label="t",
                created_at=now.isoformat(),
                expires_at=(now + timedelta(days=1)).isoformat(),
            )
        )
        session.commit()

    return client, fresh, csrf_cookie, token


class TestC1BrowserLane:
    """Legs (a), (b), (e), (f) — the Origin/Referer browser-lane gate."""

    def test_same_origin_mutation_succeeds_no_csrf_header(self, csrf_env):
        """(a) AC-39: cookie + same-origin Origin + NO X-CSRF-Token →
        the SPA mutation succeeds BY CONSTRUCTION (NFR-18: the SPA
        never echoes a token)."""
        client, _, _, _ = csrf_env
        r = client.post(
            "/api/_csrf_probe", headers={"Origin": SAME_ORIGIN}
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "method": "POST"}

    def test_cross_origin_mutation_is_403_origin_mismatch(self, csrf_env):
        """(b) cross-origin Origin + valid cookie → the middleware's
        typed 403 envelope (NOT the route layer's)."""
        client, _, _, _ = csrf_env
        r = client.post(
            "/api/_csrf_probe", headers={"Origin": CROSS_ORIGIN}
        )
        assert r.status_code == 403
        assert r.json()["error"] == "CSRF_ORIGIN_MISMATCH"
        assert r.json()["message"] == "Request rejected by CSRF guard"

    def test_referer_fallback_same_origin_passes(self, csrf_env):
        """(e) no Origin, same-origin Referer → the browser lane is
        still satisfied (the predicate branches on Origin OR Referer)."""
        client, _, _, _ = csrf_env
        r = client.post(
            "/api/_csrf_probe",
            headers={"Referer": f"{SAME_ORIGIN}/views/x"},
        )
        assert r.status_code == 200

    def test_referer_fallback_cross_origin_is_403(self, csrf_env):
        """(e) no Origin, cross-origin Referer → 403
        CSRF_ORIGIN_MISMATCH (the fallback does not widen the gate)."""
        client, _, _, _ = csrf_env
        r = client.post(
            "/api/_csrf_probe",
            headers={"Referer": f"{CROSS_ORIGIN}/views/x"},
        )
        assert r.status_code == 403
        assert r.json()["error"] == "CSRF_ORIGIN_MISMATCH"

    def test_empty_origin_fails_closed(self, csrf_env):
        """(f) present-but-EMPTY Origin → fail-closed 403
        (cookie.py:192-195 — the lane exists, the value does not)."""
        client, _, _, _ = csrf_env
        r = client.post("/api/_csrf_probe", headers={"Origin": ""})
        assert r.status_code == 403
        assert r.json()["error"] == "CSRF_ORIGIN_MISMATCH"

    def test_origin_precedes_token_echo(self, csrf_env):
        """Ordering: a MATCHING echo token cannot rescue a cross-origin
        Origin — the origin verdict fires first (auth.py:432-447)."""
        client, _, csrf_cookie, _ = csrf_env
        r = client.post(
            "/api/_csrf_probe",
            headers={
                "Origin": CROSS_ORIGIN,
                "X-CSRF-Token": csrf_cookie,
            },
        )
        assert r.status_code == 403
        assert r.json()["error"] == "CSRF_ORIGIN_MISMATCH"


class TestC1Exemptions:
    """Leg (c) + (g) — presence-based branching and method scoping."""

    def test_headerless_cookie_request_is_exempt(self, csrf_env):
        """(c) cookie present, NO Origin AND NO Referer → EXEMPT (the
        rule-1 mirror: no browser lane ⇒ no CSRF surface)."""
        client, _, _, _ = csrf_env
        r = client.post("/api/_csrf_probe")
        assert r.status_code == 200

    def test_bearer_lane_exempt_even_cross_origin(self, csrf_env):
        """(c) the bearer lane OWNS the request before the cookie lane
        (FR-13) — the CSRF verdict is a cookie-lane-only check, so a
        valid bearer + cross-origin Origin is never 403'd."""
        client, fresh, _, token = csrf_env
        r = fresh.post(
            "/api/_csrf_probe",
            headers={
                "Origin": CROSS_ORIGIN,
                "Authorization": f"Bearer {token}",
            },
        )
        assert r.status_code == 200

    def test_headerless_no_credential_is_401_never_403(self, csrf_env):
        """(c) AC-39 negative leg: headerless + NO credential → the
        401 machine contract (Accept: application/json), NOT a CSRF
        403 — the exemption must not widen the auth surface either."""
        _, fresh, _, _ = csrf_env
        r = fresh.post(
            "/api/_csrf_probe", headers={"Accept": "application/json"}
        )
        assert r.status_code == 401
        assert r.json()["error"] == "AUTH_REQUIRED"

    def test_get_is_exempt_even_cross_origin(self, csrf_env):
        """(g) GET (the SSE method) is not a state-changing method —
        even cross-origin it traverses (method scoping, [TA]
        §Session Model CSRF). Probe path is a REAL protected GET
        family (``/api/agents``) — a test-local GET probe would be
        fully-matched by the SPA catch-all registered first."""
        client, _, _, _ = csrf_env
        r = client.get("/api/agents", headers={"Origin": CROSS_ORIGIN})
        assert r.status_code == 200


class TestC1TokenEcho:
    """Leg (d) — the OD-15 double-submit echo (defense-in-depth)."""

    def test_matching_echo_passes(self, csrf_env):
        client, _, csrf_cookie, _ = csrf_env
        r = client.post(
            "/api/_csrf_probe",
            headers={"Origin": SAME_ORIGIN, "X-CSRF-Token": csrf_cookie},
        )
        assert r.status_code == 200

    def test_mismatched_echo_is_403_token_mismatch(self, csrf_env):
        client, _, _, _ = csrf_env
        r = client.post(
            "/api/_csrf_probe",
            headers={
                "Origin": SAME_ORIGIN,
                "X-CSRF-Token": "not-the-cookie-value",
            },
        )
        assert r.status_code == 403
        assert r.json()["error"] == "CSRF_TOKEN_MISMATCH"

    def test_echo_without_csrf_cookie_fails(self, csrf_env):
        """Header present but the readable cookie is absent → the
        double-submit compare fails (absent cookie ⇒ fail)."""
        client, _, _, _ = csrf_env
        client.cookies.delete("ensemble_csrf")
        r = client.post(
            "/api/_csrf_probe",
            headers={"Origin": SAME_ORIGIN, "X-CSRF-Token": "anything"},
        )
        assert r.status_code == 403
        assert r.json()["error"] == "CSRF_TOKEN_MISMATCH"

    def test_echo_consulted_only_when_header_present(self, csrf_env):
        """OD-15: absent header ⇒ the echo check never runs — the
        unchanged SPA (which never sends the header) is unaffected
        even though the readable cookie IS present."""
        client, _, _, _ = csrf_env
        assert client.cookies.get("ensemble_csrf")  # cookie present…
        r = client.post("/api/_csrf_probe", headers={"Origin": SAME_ORIGIN})
        assert r.status_code == 200  # …no header ⇒ no echo verdict
