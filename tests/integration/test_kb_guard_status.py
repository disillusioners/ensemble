"""M-4 KB guard-status probe + minor #9 touch-gate — 2026-10-11 review.

M-4 names the KB empty-store window two ways:
* the boot-time WARN (lifespan half — asserted here at the store/API
  level; the lifespan log leg is covered by the M-4 code path in
  ``daemon/api.py``), and
* an admin-only runtime probe, ``GET /api/mcp/kb/keys/guard-status``
  (``mcpkb:admin`` — the same scope as the key CRUD; the probe
  discloses guard posture, which is exactly what key administrators
  may see).

Minor #9: the mount-path ``last_used_at`` touch is 60s-batched (the
P2 ``touch_is_due`` precedent) — one UPDATE per key per minute, not
one per verified request.
"""

from __future__ import annotations

import asyncio
import warnings

import pytest
from fastapi.testclient import TestClient

from daemon.api import create_app
from tests.helpers.auth_fixture import attach_auth_state, on_auth_config
from tests.helpers.fake_oidc_stub import FakeOidcStub
from urllib.parse import parse_qs, urlsplit


@pytest.fixture()
def kb_env(tmp_path):
    """Auth-ON real app + a live module key store + a minted session."""
    warnings.filterwarnings("ignore")
    cfg = on_auth_config()
    app = create_app(auth_config=cfg)
    attach_auth_state(app, cfg, FakeOidcStub(), tmp_path)
    client = TestClient(app, base_url="http://testserver", follow_redirects=False)
    r = client.get("/api/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    r = client.get(
        f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
    )
    assert r.status_code == 302
    assert client.cookies.get("ensemble_sid")
    return client, app


class TestGuardStatusProbe:
    def test_bootstrap_posture_reported_when_store_empty(self, kb_env):
        client, app = kb_env
        r = client.get("/api/mcp/kb/keys/guard-status")
        assert r.status_code == 200
        body = r.json()
        assert body == {
            "guard_active": False,
            "active_keys": 0,
            "posture": "bootstrap_passthrough",
        }

    def test_enforcing_posture_after_first_mint(self, kb_env):
        client, app = kb_env
        mint = client.post("/api/mcp/kb/keys", json={"label": "m4"})
        assert mint.status_code == 201
        r = client.get("/api/mcp/kb/keys/guard-status")
        assert r.status_code == 200
        body = r.json()
        assert body["guard_active"] is True
        assert body["active_keys"] == 1
        assert body["posture"] == "enforcing"

    def test_probe_requires_session(self, tmp_path):
        warnings.filterwarnings("ignore")
        cfg = on_auth_config()
        app = create_app(auth_config=cfg)
        attach_auth_state(app, cfg, FakeOidcStub(), tmp_path)
        fresh = TestClient(
            app, base_url="http://testserver", follow_redirects=False
        )
        r = fresh.get("/api/mcp/kb/keys/guard-status")
        # Protected family, no credential → the FR-24 unauthenticated
        # contract (302 browser bounce; 401 for an API Accept).
        assert r.status_code in (302, 401)
        if r.status_code == 302:
            assert r.headers["location"].startswith("/api/auth/login?")

    def test_probe_wins_over_param_route(self, kb_env):
        """The static segment must beat ``GET /{key_id}`` first-match —
        a key literally named ``guard-status`` would otherwise shadow
        the probe."""
        client, app = kb_env
        r = client.get("/api/mcp/kb/keys/guard-status")
        assert r.status_code == 200
        assert "posture" in r.json()  # not a KbKeyInfo shape


class TestTouchGate:
    """Minor #9 — 60s minimum interval between last_used_at writes."""

    def _store(self, tmp_path, clock):
        import sqlalchemy

        from daemon.mcp.kb_keys import McpKbKeyStore

        engine = sqlalchemy.create_engine(
            f"sqlite:///{tmp_path}/kb_touch.db", future=True
        )
        from sqlmodel import SQLModel

        from daemon.repositories.mcp_kb_keys.models import McpKbKey

        SQLModel.metadata.create_all(engine, tables=[McpKbKey.__table__])
        return McpKbKeyStore(engine, now_iso_fn=lambda: clock[0]), engine

    def test_second_verify_within_60s_does_not_rewrite(self, tmp_path):
        clock = ["2026-10-11T08:00:00+00:00"]
        store, engine = self._store(tmp_path, clock)
        _, plaintext = store.create_key("tg")
        # Verify #1 — never-used key: touch fires.
        ok1, _ = store.verify(plaintext)
        assert ok1
        first = store.get_key(
            [k.id for k in store.list_keys()][0]
        ).last_used_at
        assert first == clock[0]
        # Verify #2 twenty seconds later — inside the window: NO write.
        clock[0] = "2026-10-11T08:00:20+00:00"
        ok2, _ = store.verify(plaintext)
        assert ok2
        row = store.get_key([k.id for k in store.list_keys()][0])
        assert row.last_used_at == first  # unchanged

    def test_touch_fires_again_after_window(self, tmp_path):
        clock = ["2026-10-11T08:00:00+00:00"]
        store, engine = self._store(tmp_path, clock)
        _, plaintext = store.create_key("tg2")
        store.verify(plaintext)
        first = store.list_keys()[0].last_used_at
        clock[0] = "2026-10-11T08:01:30+00:00"  # past the 60s window
        store.verify(plaintext)
        assert store.list_keys()[0].last_used_at == clock[0]
        assert store.list_keys()[0].last_used_at != first
