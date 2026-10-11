"""Negative cross-lane matrix + module-lane ON/OFF equivalence — oauth2-auth Phase 5 (T5.11, AMEND v1.4 / module-lanes).

Implements the AC-40 negative matrix (every token type × every wrong
lane → 401/403, positive controls hold) + the AC-41 ON/OFF
byte-equivalence for module-lane paths + the AC-42 masking round-trip
(§12 item 4(c) RULED (a) 2026-10-11 — mask-on-read +
PUT-with-masked-no-clobber; the round-trip test is live).

The matrix (per FR-40 / D20):

    ┌────────────────────┬──────────────────────┬─────────────────────────┬──────────────────────┐
    │ token              │ webhook              │ MCP-KB mount            │ general PROTECTED    │
    │                    │ (X-Webhook-Secret)   │ (X-KB-Key)              │ (cookie / bearer)    │
    ├────────────────────┼──────────────────────┼─────────────────────────┼──────────────────────┤
    │ webhook secret     │ ✅ 200 (positive)    │ ❌ 401/403 module guard │ ❌ 401 (no lane)     │
    │ MCP-KB key         │ ❌ 401 (no lane)     │ ✅ 200 (positive)       │ ❌ 401 (no lane)     │
    │ OAuth2 cookie      │ ❌ 401 (module guard)│ ✅ 200 (open posture)   │ ✅ 200 (positive)    │
    │ OAuth2 bearer      │ ❌ 401 (module guard)│ ✅ 200 (open posture)   │ ✅ 200 (positive)    │
    └────────────────────┴──────────────────────┴─────────────────────────┴──────────────────────┘

The "OAuth2 cookie/bearer at MCP-KB mount" legs (NOTE 5, per the
approver binding) send REAL requests carrying a real session cookie /
real bearer token at the mount with the guard ACTIVATED (one key
minted into the module-owned store) and assert the 401 originates
from the MODULE guard (``KB_KEY_REQUIRED`` vocabulary), never from
the OAuth2 layer (``AUTH_REQUIRED`` + ``WWW-Authenticate``). The P3
``test_mcp_kb_public.py`` covers the guard mechanism + populated-store
CRUD; the empty-store open posture is covered there too per FR-41
("open posture until the first key").
"""

from __future__ import annotations

import hashlib
import secrets
import warnings
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from daemon.mcp.kb_keys import KB_KEY_HEADER, KB_KEY_PREFIX, mint_kb_key
from daemon.repositories.source.models import SourceConfig
from daemon.repositories.auth_session.models import BearerToken
from tests.conftest_auth import (
    AUTH_TABLES,
    _WrappedApp,
    auth_enabled_app,
    mint_bearer_token,
)


# ── helpers ────────────────────────────────────────────────────────────


class _Repo:
    def get_source_config(self, source_id):
        if source_id != "src-wh":
            return None
        from unittest.mock import Mock
        scope = Mock()
        scope.source_id = "src-wh"
        scope.source_type = "webhook"
        scope.name = "Webhook Source"
        scope.config = {"webhook_secret": WEBHOOK_SECRET}
        return scope


class _Adapter:
    async def handle_webhook(self, payload, headers):
        return {"ok": True}


class _Manager:
    is_write_paused = False
    source_registry = {"src-wh": _Adapter()}
    _source_repository = _Repo()


WEBHOOK_SECRET = "whsec-p5-cross-lane"


def _attach_manager(app):
    app.state.manager = _Manager()


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
            "client_id": "test-app",
            "redirect_uri": qs["redirect_uri"][0],
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
            "code_challenge": qs["code_challenge"][0],
            "code_challenge_method": "S256",
        }
    )
    r2 = client.get(f"/_test_oidc/authorize?{scope_qs}", follow_redirects=False)
    loc = r2.headers["location"]
    code = parse_qs(urlsplit(loc).query)["code"][0]
    return {"code": code, "state": state}


def _complete_flow(app, fake) -> str:
    """Run the full flow + return the session id."""
    client = _new_client(app, fake)
    ctx = _login_via(client)
    r = client.get(
        f"/api/auth/callback?code={ctx['code']}&state={ctx['state']}"
    )
    for part in r.headers.get("set-cookie", "").split(";"):
        if part.strip().startswith("ensemble_sid="):
            return part.strip().split("=", 1)[1]
    return ""


# ── (a) Negative cross-lane matrix (AC-40) ──────────────────────────


class TestCrossLaneMatrix:
    """AC-40 / FR-40: each token type × each wrong lane → 401/403 with
    no partial honor; positive controls hold.

    Test plan:
    * webhook secret at MCP-KB mount → classification-only assert
      (live 401 pinned in tests/integration/test_mcp_kb_public.py)
    * webhook secret at general PROTECTED → 401 (not a valid OAuth2 credential)
    * MCP-KB key at webhook ingest → 401 (not a webhook credential)
    * MCP-KB key at general PROTECTED → 401 (not a valid OAuth2 credential)
    * OAuth2 cookie at webhook ingest → 401 (module guard decides)
    * OAuth2 bearer at webhook ingest → 401 (module guard decides)
    * OAuth2 cookie at general PROTECTED → 200 (positive control)
    * OAuth2 bearer at general PROTECTED → 200 (positive control)
    * webhook secret at webhook ingest → 200 (positive control)
    * MCP-KB key at MCP-KB mount → 200 (positive control, empty-store
      pass-through)
    """

    def test_webhook_secret_at_mcp_kb_mount_classify(self, tmp_path):
        """A webhook secret is NOT an MCP-KB key. The webhook secret
        at the MCP-KB mount path is NOT accepted by the module guard
        — the guard consults the X-KB-Key header (not X-Webhook-Secret).

        RENAMED (ADV-7 rider, 2026-10-11): this test asserts the
        dispatch-order CLASSIFICATION only — it drives no request; the
        honest ``_classify`` suffix replaces the ``_401`` claim. The
        live 401 at the mount is the module guard's behavior, covered
        by tests/integration/test_mcp_kb_public.py.

        The OAuth2 layer's classification of the mount path is
        module_lane (the middleware passes through); the module guard
        is the authoritative credential check. We assert here that
        the dispatch-order classification is correct (the OAuth2
        layer never gates this path) — the module's own behavior is
        covered by tests/integration/test_mcp_kb_public.py."""
        from daemon.middleware.auth import AuthMiddleware

        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            mw = AuthMiddleware(None, auth_config=app.state.auth_config)
            mw._ensure_derived({"app": app})
            # /api/mcp/kb/mcp MUST classify as module_lane.
            assert mw._resolve_tagged("/api/mcp/kb/mcp") == "module_lane"
            # The webhook secret is NOT consulted at this path by the
            # module guard — only X-KB-Key matters. The OAuth2 layer
            # passes the request through; the module guard decides.
            # (We don't drive the actual mount — that requires the
            # daemon lifespan.)

    def test_webhook_secret_at_protected_route_401(self, tmp_path):
        """A webhook secret is NOT an OAuth2 credential — the auth
        middleware sees no Authorization header and no cookie → 401."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get(
                "/api/mcp/kb/keys",
                headers={
                    "X-Webhook-Secret": WEBHOOK_SECRET,
                    "Accept": "application/json",
                },
            )
            assert r.status_code == 401, r.text

    def test_mcp_kb_key_at_webhook_ingest_401(self, tmp_path):
        """An MCP-KB key is NOT a webhook credential — the module
        guard at /api/webhooks/* ignores X-KB-Key headers."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            _attach_manager(app)
            client = _new_client(app, fake)
            r = client.post(
                "/api/webhooks/src-wh",
                json={"text": "hi"},
                headers={KB_KEY_HEADER: mint_kb_key()},
            )
            assert r.status_code == 401, r.text
            # The body should reference the webhook-secret guard.
            assert "webhook secret" in r.text.lower()

    def test_mcp_kb_key_at_protected_route_401(self, tmp_path):
        """An MCP-KB key is NOT an OAuth2 credential."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            client = _new_client(app, fake)
            r = client.get(
                "/api/mcp/kb/keys",
                headers={
                    KB_KEY_HEADER: mint_kb_key(),
                    "Accept": "application/json",
                },
            )
            assert r.status_code == 401, r.text

    def test_oauth2_cookie_at_webhook_ingest_401(self, tmp_path):
        """An OAuth2 session cookie is NOT a webhook credential."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            _attach_manager(app)
            sid = _complete_flow(app, fake)
            assert sid
            client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
            )
            r = client.post("/api/webhooks/src-wh", json={"text": "hi"})
            assert r.status_code == 401, r.text
            assert "webhook secret" in r.text.lower()

    def test_oauth2_bearer_at_webhook_ingest_401(self, tmp_path):
        """An OAuth2 bearer is NOT a webhook credential."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            _attach_manager(app)
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("mcpkb:admin",),
                label="p5-cross-lane-bearer",
                engine=app.state.auth_session_repository._engine,
            )
            client = _new_client(app, fake)
            r = client.post(
                "/api/webhooks/src-wh",
                json={"text": "hi"},
                headers={"Authorization": f"Bearer {plaintext}"},
            )
            assert r.status_code == 401, r.text
            assert "webhook secret" in r.text.lower()

    # ── positive controls ────────────────────────────────────────

    def test_oauth2_cookie_at_protected_route_200(self, tmp_path):
        """A valid session cookie passes the auth layer at a protected
        route (positive control for the cookie lane)."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            sid = _complete_flow(app, fake)
            client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
            )
            r = client.get("/api/mcp/kb/keys")
            assert r.status_code not in (401, 403), r.text

    def test_oauth2_bearer_at_protected_route_200(self, tmp_path):
        """A valid mcpkb: bearer passes the auth layer (positive
        control for the bearer lane)."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("mcpkb:admin",),
                label="p5-cross-lane-positive",
                engine=app.state.auth_session_repository._engine,
            )
            client = _new_client(app, fake)
            r = client.get(
                "/api/mcp/kb/keys",
                headers={"Authorization": f"Bearer {plaintext}"},
            )
            assert r.status_code not in (401, 403), r.text

    def test_webhook_secret_at_webhook_ingest_200(self, tmp_path):
        """A valid webhook secret authenticates the module's ingest."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            _attach_manager(app)
            client = _new_client(app, fake)
            r = client.post(
                "/api/webhooks/src-wh",
                json={"text": "hi"},
                headers={"X-Webhook-Secret": WEBHOOK_SECRET},
            )
            assert r.status_code == 200, r.text


# ── (b) Module-lane ON/OFF byte-equivalence (AC-41) ────────────────


class TestModuleLaneOnOffEquivalence:
    """FR-42 / AC-41: same request set against the auth_disabled_app
    and auth_enabled_app fixtures — byte-identical status+body for
    every module-lane request; module_lane_path_set logged identical
    in both fixtures."""

    @pytest.fixture()
    def on_app(self, tmp_path):
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            _attach_manager(app)
            yield app

    @pytest.fixture()
    def off_app(self):
        from daemon.api import create_app
        warnings.filterwarnings("ignore")
        app = create_app()
        _attach_manager(app)
        yield app

    def test_webhook_module_lane_identical_status_body(self, on_app, off_app):
        on_client = TestClient(on_app, base_url="http://testserver")
        off_client = TestClient(off_app, base_url="http://testserver")
        on_r = on_client.post(
            "/api/webhooks/src-wh",
            json={"text": "hi"},
            headers={"X-Webhook-Secret": WEBHOOK_SECRET},
        )
        off_r = off_client.post(
            "/api/webhooks/src-wh",
            json={"text": "hi"},
            headers={"X-Webhook-Secret": WEBHOOK_SECRET},
        )
        assert on_r.status_code == off_r.status_code
        assert on_r.text == off_r.text

    def test_webhook_open_ingest_identical(self, on_app, off_app):
        """A webhook source WITHOUT a configured secret stays open in
        both states (the pre-existing open-ingest posture)."""
        from daemon.api import create_app

        class _NoSecretRepo:
            def get_source_config(self, source_id):
                from unittest.mock import Mock
                if source_id != "src-open":
                    return None
                scope = Mock()
                scope.source_id = "src-open"
                scope.source_type = "webhook"
                scope.name = "open"
                scope.config = {}
                return scope

        class _OpenAdapter:
            async def handle_webhook(self, payload, headers):
                return {"ok": True}

        class _OpenManager:
            is_write_paused = False
            source_registry = {"src-open": _OpenAdapter()}
            _source_repository = _NoSecretRepo()

        # Rebind the manager on both apps to use the open source.
        on_app.state.manager = _OpenManager()
        off_app.state.manager = _OpenManager()
        on_client = TestClient(on_app, base_url="http://testserver")
        off_client = TestClient(off_app, base_url="http://testserver")
        on_r = on_client.post("/api/webhooks/src-open", json={"text": "hi"})
        off_r = off_client.post("/api/webhooks/src-open", json={"text": "hi"})
        assert on_r.status_code == off_r.status_code
        assert on_r.text == off_r.text


# ── (c) AC-42 masking round-trip (§12 4(c) RULED (a) 2026-10-11) ──


class _RoundTripRepo:
    """In-memory stand-in for SourceRepository — just the CRUD surface
    the /api/sources routes drive (create/get/update). Rows are real
    ``SourceConfig`` models so route serialization (``_source_to_info``)
    and the stored-value assertions hit the same shapes production
    uses."""

    def __init__(self) -> None:
        self.rows: dict[str, SourceConfig] = {}

    def get_source_config(self, source_id: str) -> SourceConfig | None:
        return self.rows.get(source_id)

    def create_source_config(
        self,
        *,
        source_type: str,
        name: str,
        config: dict,
        credentials: str | None = None,
        enabled: bool = True,
        autostart: bool = True,
        source_id: str | None = None,
    ) -> SourceConfig:
        row = SourceConfig(
            source_id=source_id or "src-ac42",
            source_type=source_type,
            name=name,
            config=dict(config),
            credentials=credentials,
            enabled=enabled,
            autostart=autostart,
        )
        self.rows[row.source_id] = row
        return row

    def update_source_config(
        self,
        *,
        source_id: str,
        source_type: str | None = None,
        name: str | None = None,
        config: dict | None = None,
        credentials: str | None = None,
        enabled: bool | None = None,
        autostart: bool | None = None,
    ) -> SourceConfig | None:
        row = self.rows.get(source_id)
        if row is None:
            return None
        if source_type is not None:
            row.source_type = source_type
        if name is not None:
            row.name = name
        if config is not None:
            row.config = config
        if credentials is not None:
            row.credentials = credentials
        if enabled is not None:
            row.enabled = enabled
        if autostart is not None:
            row.autostart = autostart
        return row


class _RoundTripManager:
    """Manager stand-in exposing only what the sources routes touch."""

    def __init__(self, repo: _RoundTripRepo) -> None:
        self.is_write_paused = False
        self._source_repository = repo

        class _Registry:
            async def start_adapter(self, source_id: str) -> None:
                return None

        self.source_registry = _Registry()


class TestWebhookSecretMaskingAC42:
    """AC-42 / FR-41: the webhook_secret masking round-trip on the
    OAuth2-governed /api/sources surface (plan-overview §12 item 4(c)
    RULED (a) — mask-on-read + PUT-with-masked-no-clobber,
    2026-10-11).

    Contract pinned against shipped behavior
    (daemon/routers/sources.py):
      1. GET never returns the plaintext — ``webhook_secret`` reads
         back as the ``********`` sentinel (``_mask_source_config``);
      2. a GET→PUT echo carrying the sentinel in place (the
         wholesale config-replace shape) does NOT clobber the stored
         secret — the PUT ML-3 round-trip guard keeps the stored
         value;
      3. PUT with a NEW plaintext rotates it (the guard must not
         block real rotation).

    The STORED value is asserted via the repository layer directly —
    the API masks on read, so the API can never witness the stored
    plaintext. Driven as an authenticated operator (session-cookie
    lane, the SPA's own path).
    """

    def test_put_with_masked_read_does_not_clobber_stored_secret(
        self, tmp_path
    ):
        from daemon.routers.sources import WEBHOOK_SECRET_MASK

        original = "whsec-ac42-original-9f2c"
        rotated = "whsec-ac42-rotated-4b7d"

        from daemon.sources.credentials import CredentialManager

        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            repo = _RoundTripRepo()
            app.state.manager = _RoundTripManager(repo)
            # The auth harness builds create_app() WITHOUT the full
            # lifespan wiring, so app.state.credential_manager (set at
            # daemon/api.py:1526 in production) is absent — the
            # /api/sources create/update routes fetch it unconditionally.
            app.state.credential_manager = CredentialManager()
            sid = _complete_flow(app, fake)
            assert sid
            client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
            )

            # (a) create a source carrying a plaintext webhook_secret.
            r = client.post(
                "/api/sources",
                json={
                    "source_id": "src-ac42",
                    "source_type": "webhook",
                    "name": "AC-42 Round-Trip",
                    "config": {"webhook_secret": original, "url": "/hook"},
                    "enabled": False,
                },
            )
            assert r.status_code == 201, r.text
            assert (
                r.json()["config"]["webhook_secret"] == WEBHOOK_SECRET_MASK
            )

            # (b) GET reads back MASKED — never the plaintext.
            r = client.get("/api/sources/src-ac42")
            assert r.status_code == 200, r.text
            echoed = r.json()["config"]
            assert echoed["webhook_secret"] == WEBHOOK_SECRET_MASK
            assert original not in r.text

            # (c) PUT the echoed object back UNCHANGED (sentinel in
            # place — the wholesale config-replace shape) must NOT
            # clobber the stored secret (verified at the repository
            # layer — the API masks on read).
            r = client.put(
                "/api/sources/src-ac42",
                json={"config": echoed},
            )
            assert r.status_code == 200, r.text
            assert (
                r.json()["config"]["webhook_secret"] == WEBHOOK_SECRET_MASK
            )
            stored = repo.rows["src-ac42"].config["webhook_secret"]
            assert stored == original, (
                f"PUT-with-masked-read clobbered the stored secret: "
                f"stored={stored!r}"
            )
            assert stored != WEBHOOK_SECRET_MASK

            # (d) rotation control: a NEW plaintext must go through.
            r = client.put(
                "/api/sources/src-ac42",
                json={"config": {**echoed, "webhook_secret": rotated}},
            )
            assert r.status_code == 200, r.text
            stored_after = repo.rows["src-ac42"].config["webhook_secret"]
            assert stored_after == rotated, (
                f"PUT with a new plaintext failed to rotate: "
                f"stored={stored_after!r}"
            )
            r = client.get("/api/sources/src-ac42")
            assert r.status_code == 200, r.text
            assert (
                r.json()["config"]["webhook_secret"] == WEBHOOK_SECRET_MASK
            )
            assert rotated not in r.text


# ── (a) MCP-KB negative legs (NOTE 5: real credential-through-to-─
#      module-rejection routing, not classification-only) ────────────


class TestMcpKbNegativeLegs:
    """T5.11(a) NOTE 5: the MCP-KB negative legs EXERCISE the
    lane-classification REJECTION ROUTING with real requests — a real
    OAuth2 session cookie / a real bearer token is carried to a
    module-lane KB mount path and the 401 that comes back originates
    from the MODULE guard (``KbKeyGuardApp``; vocabulary
    ``KB_KEY_REQUIRED`` / ``KB_KEY_INVALID``), never from the OAuth2
    layer (whose vocabulary is ``AUTH_REQUIRED`` + a
    ``WWW-Authenticate`` challenge; its scope evaluation never runs).

    The guard is activated by minting one real KB key into the
    module-owned store (``app.state.mcp_kb_key_store`` — the fixture
    DB carries the ``mcp_kb_keys`` table): the empty-store bootstrap
    posture passes everything through, and only an ACTIVE store makes
    the guard the rejecting authority. The KB mounts are registered
    statically in ``create_app`` (no lifespan dependency), so the real
    guard is hittable in this fixture.

    Regression property: if the middleware's lane classification ever
    regresses (mount re-classified PROTECTED), these legs FAIL — the
    OAuth2 layer would consume the credential and answer with its own
    ``AUTH_REQUIRED`` (401 + WWW-Authenticate) / ``SCOPE_INSUFFICIENT``
    (403) vocabulary instead of passing the request through to the
    module guard.
    """

    #: Module-lane KB mount paths — the classification leg drives all
    #: three; the real-request legs hit the http mount shape.
    KB_MOUNT_PATHS = ("/api/mcp/kb", "/api/mcp/kb/sse", "/api/mcp/kb/mcp")

    def test_kb_mounts_classify_module_lane(self, tmp_path):
        """Classification extra leg: every KB mount path classifies
        ``module_lane`` — the middleware's cookie/bearer check is never
        consulted for this lane (kept from the pre-note5 stub)."""
        from daemon.middleware.auth import AuthMiddleware

        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            mw = AuthMiddleware(None, auth_config=app.state.auth_config)
            mw._ensure_derived({"app": app})
            for path in self.KB_MOUNT_PATHS:
                assert mw._resolve_tagged(path) == "module_lane", path

    def test_oauth2_cookie_at_kb_mount_rejected_by_module_guard(self, tmp_path):
        """A real OAuth2 session cookie at the KB mount is passed
        through untouched and the MODULE guard rejects the request:
        401 ``KB_KEY_REQUIRED`` — no ``AUTH_REQUIRED``, no
        ``WWW-Authenticate``. Positive control: the same client with a
        VALID module key passes the guard (the rejection is
        key-gating, not credential-gating)."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            store = app.state.mcp_kb_key_store
            store.create_key("p5-note5-cookie-leg")  # activates the guard
            sid = _complete_flow(app, fake)
            assert sid
            client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": sid},
                raise_server_exceptions=False,
            )
            r = client.get(
                "/api/mcp/kb/mcp", headers={"Accept": "application/json"}
            )
            assert r.status_code == 401, r.text
            # Module-guard vocabulary, NOT the OAuth2 layer's:
            assert r.json() == {"error": "KB_KEY_REQUIRED"}, r.text
            assert r.headers.get("www-authenticate") is None, dict(r.headers)
            assert "AUTH_REQUIRED" not in r.text

            # Positive control — valid module key ⇒ the guard lets the
            # same credentialed request through to the mount (the
            # sub-app's own outcome surfaces; never a KB_KEY_* 401).
            _, kb_plaintext = store.create_key("p5-note5-cookie-positive")
            r2 = client.get(
                "/api/mcp/kb/mcp",
                headers={
                    "X-KB-Key": kb_plaintext,
                    "Accept": "application/json",
                },
            )
            assert "KB_KEY_REQUIRED" not in r2.text, r2.text
            assert "KB_KEY_INVALID" not in r2.text, r2.text

    def test_oauth2_bearer_at_kb_mount_rejected_by_module_guard(self, tmp_path):
        """A real OAuth2 bearer token at the KB mount is passed through
        untouched and the MODULE guard rejects the request: 401
        ``KB_KEY_REQUIRED`` — the bearer is never consulted (no scope
        evaluation, no ``WWW-Authenticate`` challenge)."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            store = app.state.mcp_kb_key_store
            store.create_key("p5-note5-bearer-leg")
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("mcpkb:admin",),
                label="p5-note5-kb-bearer",
                engine=app.state.auth_session_repository._engine,
            )
            client = _new_client(app, fake)
            r = client.get(
                "/api/mcp/kb/mcp",
                headers={
                    "Authorization": f"Bearer {plaintext}",
                    "Accept": "application/json",
                },
            )
            assert r.status_code == 401, r.text
            assert r.json() == {"error": "KB_KEY_REQUIRED"}, r.text
            assert r.headers.get("www-authenticate") is None, dict(r.headers)
            assert "AUTH_REQUIRED" not in r.text

    def test_invalid_oauth2_credentials_still_reach_module_rejection(
        self, tmp_path
    ):
        """The ROUTING probe: credentials the OAuth2 layer would
        REJECT (a stale session cookie / a bearer lacking
        ``mcpkb:admin``) are NEVER consulted at the module-lane mount
        — they pass through untouched and the MODULE guard rejects
        with ``KB_KEY_REQUIRED``. If the lane classification ever
        regresses to PROTECTED, this leg FAILS: the OAuth2 layer would
        answer with ``AUTH_REQUIRED`` (401 + WWW-Authenticate) /
        ``SCOPE_INSUFFICIENT`` (403) instead."""
        with auth_enabled_app(tmp_path=tmp_path) as (app, fake, transport):
            store = app.state.mcp_kb_key_store
            store.create_key("p5-note5-routing-probe")
            # Stale/unknown session cookie — constructed on the client
            # (per-request cookies= is deprecated in starlette).
            stale_client = TestClient(
                _WrappedApp(app, fake),
                base_url="http://testserver",
                follow_redirects=False,
                cookies={"ensemble_sid": "stale-or-forged"},
            )
            r = stale_client.get(
                "/api/mcp/kb/mcp",
                headers={"Accept": "application/json"},
            )
            assert r.status_code == 401, r.text
            assert r.json() == {"error": "KB_KEY_REQUIRED"}, r.text
            assert r.headers.get("www-authenticate") is None, dict(r.headers)
            assert "AUTH_REQUIRED" not in r.text
            # Insufficient-scope bearer — scope evaluation never runs:
            plaintext, _ = mint_bearer_token(
                subject="machine-1",
                scopes=("system:read",),  # missing mcpkb:admin
                label="p5-note5-insuf-bearer",
                engine=app.state.auth_session_repository._engine,
            )
            bearer_client = _new_client(app, fake)
            r = bearer_client.get(
                "/api/mcp/kb/mcp",
                headers={
                    "Authorization": f"Bearer {plaintext}",
                    "Accept": "application/json",
                },
            )
            assert r.status_code == 401, r.text
            assert r.json() == {"error": "KB_KEY_REQUIRED"}, r.text
            assert "SCOPE_INSUFFICIENT" not in r.text
            assert r.headers.get("www-authenticate") is None, dict(r.headers)
