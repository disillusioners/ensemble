"""C-2 CSRF comparator legs — the PRODUCTION shape (2026-10-11 review).

The P3 CSRF suite (``test_auth_csrf.py``) runs every leg over
``http://testserver`` — zero production-shape coverage. These legs pin
the rewritten comparator contract:

* (C-2c) https Origin behind a FORWARDED proxy — the live-views trust
  signal (``live_view_external_base_url_fallback``, the one-shot copy
  of ``live_views.external_base_url``) makes ``X-Forwarded-Proto``
  trusted for that audience → the SPA mutation PASSES;
* (C-2c) untrusted XFP — same https Origin with NO external base →
  fail-closed 403 (the pre-fix bug shape: uvicorn passes no
  ``proxy_headers``, scope scheme stays http);
* (C-2c) trusted base, NO XFP — the operator-declared base scheme is
  the fallback → PASSES (operator declaration IS the trust);
* (C-2a) the DNS-rebinding pair — attacker Origin + attacker Host that
  AGREE, from a non-loopback peer → 403 (the allowlist breaks the
  tautology);
* (C-2a) the spoofed-localhost pair — ``Host: localhost`` +
  ``Origin: http://localhost:4199`` from a non-loopback peer → 403
  (the carve is peer-gated; a remote client cannot fake its peer);
* (C-2a/b) the dev carve — EXACT localhost names only, and only when
  BOTH the dev gate (``allow_insecure_cookie=true``) AND the loopback
  peer gate hold: loopback peer + ``localhost`` Origin → PASS;
  loopback peer + ``127.0.0.2`` (in the old /8, not exact) → 403;
  prod config (insecure opt-in OFF) + loopback peer + localhost → 403.

Harness notes: ``TestClient(client=...)`` controls the ASGI peer
(loopback vs the default non-loopback ``testclient``); the
``live_view_external_base_url_fallback`` field is set on the SAME
AuthConfig object the middleware holds (the real lifespan one-shot
copy shape). A test-local ``/api/_csrf_probe`` mutation route is added
AFTER ``create_app`` and BEFORE the first request (the middleware
derives at the first HTTP scope).
"""

from __future__ import annotations

import warnings
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from daemon.api import create_app
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub

LOOPBACK_PEER = ("127.0.0.1", 50000)
REMOTE_PEER = ("203.0.113.7", 4444)


def _build_app(cfg, tmp_path):
    app = create_app(auth_config=cfg)

    @app.post("/api/_csrf_probe")
    async def _probe_post() -> dict:
        return {"ok": True, "method": "POST"}

    attach_auth_state(app, cfg, FakeOidcStub(), tmp_path)
    return app


def _mint_session(client) -> None:
    """login → callback → the client carries ensemble_sid."""
    r = client.get("/api/auth/login", follow_redirects=False)
    assert r.status_code == 302, r.text
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    r = client.get(
        f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
    )
    assert r.status_code == 302
    assert client.cookies.get("ensemble_sid")


class TestC2ForwardedProxy:
    """Leg (d): the production shape — https Origin behind a proxy.

    The https origin is deliberately NOT in ``allowed_origins`` (the
    operator names the http origin there); the live-views external
    base is the ONLY https declaration — so these legs prove the C-2c
    XFP trust chain itself, not the operator-origin leg.
    """

    def _cfg(self) -> Any:
        from typing import Any

        return on_auth_config(
            allowed_origins=["http://ens.example.com"],
            external_redirect_base="http://ens.example.com",
        )

    def test_https_origin_behind_trusted_forwarded_proxy_passes(
        self, tmp_path
    ):
        cfg = self._cfg()
        app = _build_app(cfg, tmp_path)
        # The real lifespan one-shot copy — the C-2c trust signal.
        cfg.live_view_external_base_url_fallback = "https://ens.example.com"
        client = TestClient(
            app,
            base_url="http://ens.example.com",
            client=REMOTE_PEER,
            follow_redirects=False,
        )
        _mint_session(client)
        r = client.post(
            "/api/_csrf_probe",
            headers={
                "X-Forwarded-Proto": "https",
                "Origin": "https://ens.example.com",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "method": "POST"}

    def test_https_origin_without_external_base_fails_closed(self, tmp_path):
        """The pre-fix bug shape: TLS terminated upstream, uvicorn sees
        http, NO trust signal configured — the https Origin must NOT
        pass (fail-closed), even with a matching allowlist-able Host
        and the XFP header present (undeclared proxy ⇒ XFP untrusted)."""
        cfg = self._cfg()
        app = _build_app(cfg, tmp_path)
        assert cfg.live_view_external_base_url_fallback is None
        client = TestClient(
            app,
            base_url="http://ens.example.com",
            client=REMOTE_PEER,
            follow_redirects=False,
        )
        _mint_session(client)
        r = client.post(
            "/api/_csrf_probe",
            headers={
                "X-Forwarded-Proto": "https",
                "Origin": "https://ens.example.com",
            },
        )
        assert r.status_code == 403
        assert r.json()["error"] == "CSRF_ORIGIN_MISMATCH"

    def test_trusted_base_with_no_xfp_uses_declared_scheme(self, tmp_path):
        """Operator declared the front (external base set) but the
        proxy strips XFP: the DECLARED base scheme is the trusted
        fallback — the declaration itself is the trust signal."""
        cfg = self._cfg()
        app = _build_app(cfg, tmp_path)
        cfg.live_view_external_base_url_fallback = "https://ens.example.com"
        client = TestClient(
            app,
            base_url="http://ens.example.com",
            client=REMOTE_PEER,
            follow_redirects=False,
        )
        _mint_session(client)
        r = client.post(
            "/api/_csrf_probe",
            headers={"Origin": "https://ens.example.com"},
        )
        assert r.status_code == 200, r.text

    def test_https_origin_via_referer_only_also_passes(self, tmp_path):
        cfg = self._cfg()
        app = _build_app(cfg, tmp_path)
        cfg.live_view_external_base_url_fallback = "https://ens.example.com"
        client = TestClient(
            app,
            base_url="http://ens.example.com",
            client=REMOTE_PEER,
            follow_redirects=False,
        )
        _mint_session(client)
        r = client.post(
            "/api/_csrf_probe",
            headers={
                "X-Forwarded-Proto": "https",
                "Referer": "https://ens.example.com/views/x",
            },
        )
        assert r.status_code == 200, r.text

    def test_operator_named_origin_allowed_on_other_host(self, tmp_path):
        """Leg 2 — an operator-named origin (allowed_origins) passes
        even when the request Host is a DIFFERENT audience member
        (SPA-on-its-own-origin deployment; rule-4 opt-in mirror)."""
        cfg = on_auth_config(
            allowed_origins=[
                "https://ens.example.com",
                "https://spa.example.com",
            ],
            external_redirect_base="https://ens.example.com",
        )
        app = _build_app(cfg, tmp_path)
        client = TestClient(
            app,
            base_url="http://ens.example.com",
            client=REMOTE_PEER,
            follow_redirects=False,
        )
        _mint_session(client)
        r = client.post(
            "/api/_csrf_probe",
            headers={
                "X-Forwarded-Proto": "https",
                "Origin": "https://spa.example.com",
            },
        )
        assert r.status_code == 200, r.text


class TestC2RebindingPairs:
    """The pairs that authorized the pre-fix comparator."""

    def test_rebinding_pair_attacker_origin_plus_attacker_host(self, tmp_path):
        """Origin and Host AGREE by construction under rebinding — the
        bare comparison authorized this; the allowlist now refuses it
        (attacker host is not in the operator audience)."""
        cfg = on_auth_config()
        app = _build_app(cfg, tmp_path)
        client = TestClient(
            app,
            base_url="http://attacker-controlled.example",
            client=REMOTE_PEER,
            follow_redirects=False,
        )
        _mint_session(client)
        r = client.post(
            "/api/_csrf_probe",
            headers={"Origin": "http://attacker-controlled.example"},
        )
        assert r.status_code == 403
        assert r.json()["error"] == "CSRF_ORIGIN_MISMATCH"

    def test_spoofed_localhost_headers_from_remote_peer(self, tmp_path):
        cfg = on_auth_config()  # dev gate OPEN (allow_insecure_cookie)
        app = _build_app(cfg, tmp_path)
        client = TestClient(
            app,
            base_url="http://localhost",  # Host: localhost — spoofable
            client=REMOTE_PEER,  # ...but the peer is remote: gated
            follow_redirects=False,
        )
        _mint_session(client)
        r = client.post(
            "/api/_csrf_probe",
            headers={"Origin": "http://localhost:4199"},
        )
        assert r.status_code == 403
        assert r.json()["error"] == "CSRF_ORIGIN_MISMATCH"


class TestC2DevCarve:
    """The carve: EXACT names, dev-gated, peer-gated."""

    def test_dev_loopback_peer_exact_localhost_passes(self, tmp_path):
        cfg = on_auth_config()  # dev gate OPEN
        app = _build_app(cfg, tmp_path)
        client = TestClient(
            app,
            base_url="http://localhost",  # dev FE proxy on loopback
            client=LOOPBACK_PEER,
            follow_redirects=False,
        )
        _mint_session(client)
        r = client.post(
            "/api/_csrf_probe",
            headers={"Origin": "http://localhost:4199"},
        )
        assert r.status_code == 200, r.text

    def test_dev_loopback_peer_127_0_0_2_is_refused(self, tmp_path):
        """C-2b: the 127.0.0.0/8 blanket is GONE — only the EXACT
        spellings qualify. 127.0.0.2 is IN the old range, refused now."""
        cfg = on_auth_config()
        app = _build_app(cfg, tmp_path)
        client = TestClient(
            app,
            base_url="http://127.0.0.1",
            client=LOOPBACK_PEER,
            follow_redirects=False,
        )
        _mint_session(client)
        r = client.post(
            "/api/_csrf_probe",
            headers={"Origin": "http://127.0.0.2:4199"},
        )
        assert r.status_code == 403
        assert r.json()["error"] == "CSRF_ORIGIN_MISMATCH"

    def test_prod_config_refuses_localhost_even_from_loopback(self, tmp_path):
        """C-2b dev gate: without the insecure-cookie opt-in the carve
        is closed entirely — loopback peer + exact localhost → 403.
        (Cookie flags flip to the production shape; the IdP stays the
        https stub issuer so config validation + token validation
        still pass.)"""
        cfg = on_auth_config(
            allow_insecure_cookie=False,
            cookie_secure=True,
            allowed_origins=["https://testserver"],
            external_redirect_base="https://testserver",
        )
        app = _build_app(cfg, tmp_path)
        client = TestClient(
            app,
            base_url="https://localhost",
            client=LOOPBACK_PEER,
            follow_redirects=False,
        )
        _mint_session(client)
        r = client.post(
            "/api/_csrf_probe",
            headers={"Origin": "http://localhost:4199"},
        )
        assert r.status_code == 403
        assert r.json()["error"] == "CSRF_ORIGIN_MISMATCH"


class TestC2NonDefaultPort:
    """The Host header's EXPLICIT port participates in leg 1 (the
    browser puts a non-default port in Origin too) — a non-default-port
    deployment must not 403 its own SPA."""

    def test_non_default_port_same_origin_passes(self, tmp_path):
        cfg = on_auth_config(
            allowed_origins=["http://ens.example.com:8443"],
            external_redirect_base="http://ens.example.com:8443",
        )
        app = _build_app(cfg, tmp_path)
        client = TestClient(
            app,
            base_url="http://ens.example.com:8443",
            client=REMOTE_PEER,
            follow_redirects=False,
        )
        _mint_session(client)
        r = client.post(
            "/api/_csrf_probe",
            headers={"Origin": "http://ens.example.com:8443"},
        )
        assert r.status_code == 200, r.text

    def test_port_mismatch_fails_closed(self, tmp_path):
        cfg = on_auth_config(
            allowed_origins=["http://ens.example.com:8443"],
            external_redirect_base="http://ens.example.com:8443",
        )
        app = _build_app(cfg, tmp_path)
        client = TestClient(
            app,
            base_url="http://ens.example.com:8443",
            client=REMOTE_PEER,
            follow_redirects=False,
        )
        _mint_session(client)
        r = client.post(
            "/api/_csrf_probe",
            headers={"Origin": "http://ens.example.com:9999"},
        )
        assert r.status_code == 403
        assert r.json()["error"] == "CSRF_ORIGIN_MISMATCH"
