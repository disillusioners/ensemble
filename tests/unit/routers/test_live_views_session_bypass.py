"""R1-R5 tests — views session bypass + origin gate + never-JSON-on-humans.

views-session-bypass (2026-11-08), task 20261109-views-session-bypass.
Mirrors the P2 pattern at ``test_live_views_public_lane.py`` (fresh
FastAPI app, services on ``app.state``, no DB / manager / lifespan)
and covers the five requirements of the commission:

* R1 session bypass — ``resolve_views_session`` + the POST/GET/board
  lane entry legs (valid operator session ⇒ grant + 303 / content
  render, no passcode, no rate-limit / lockout / origin interference;
  stale/garbage ⇒ EXACTLY anonymous).
* R2 navigation-correct origin gate — ``Origin: null`` allow,
  www/apex reconciliation, forwarded hosts, operator audience,
  tautology fail-closed, cross-origin 403 (no attempt consumed).
* R3 never-JSON-on-human-pages — Accept discrimination: HTML 404 /
  403 for ``text/html`` navigations, byte-identical JSON envelopes
  for everyone else; 429 / locked flip to JSON only on an EXPLICIT
  ``application/json`` accept.
* R4 anonymous-unchanged — the wrong-code failure page stays the
  HTML form re-render for every accept shape.
* R5 view_write covered-by-construction — the tool's pages are
  served by the SAME ``/views/public`` handlers asserted here (no
  separate route family exists; asserted structurally).

Direct-lane composition (R1 requirement 5): the
``/views/<root>/<rel>`` family is UNTAGGED ⇒ oauth2-protected
(pinned by ``test_auth_public_lane_tag.py``); a valid session cookie
dispatches through ``AuthMiddleware`` and serves; a garbage cookie
falls to the 401 / 302 contracts. Asserted here with the middleware
mounted on the test app.

File-size rationale (band obligation: 1000–3000): one file per requirement family, mirrors the P2 public-lane pack.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from daemon.auth.context import AuthContext
from daemon.auth.cookie import SESSION_COOKIE_NAME
from daemon.config import AuthConfig, LiveViewsConfig, LiveViewsRootConfig
from daemon.middleware.auth import AuthMiddleware
from daemon.repositories.view_passcodes.models import (
    LIVE_VIEW_PASSCODE_STRIKE_CEILING,
)
from daemon.routers.live_views import (
    _same_host_origin_check,
    _views_trusted_hosts,
    build_router,
)
from daemon.services.live_views import LiveViewsService
from daemon.services.view_passcode_service import (
    GRANT_COOKIE_NAME,
    ViewPasscodeCipher,
    ViewPasscodeService,
    VerifyRateLimiter,
    generate_passcode,
)
from tests.helpers.auth_fixture import on_auth_config

_TEST_FERNET_KEY: bytes = (
    b"Z6gPqrtSzfIcd1aVvbiGoeY9N-99lxx7dx1lzh4fDoE="
)

_VIEW_PATH = "/views/public/planning/ens/feat/design/mockups/landing.html"
_VIEW_REL = "ens/feat/design/mockups/landing.html"

# ─────────────────────────────────────────────────────────────────
# Service / fixture scaffolding (mirror of the P2 public-lane file)
# ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _scrub_live_operator_env(monkeypatch):
    """Hermetic env — the ambient shell carries LIVE operator auth vars.

    The 2026-11-08 worktree shell exports ``ENSEMBLE_AUTH_*`` (live
    discovery/issuer/client). Any ``AuthConfig`` construction in these
    tests must not see them (the discovery-vs-issuer origin validator
    refuses the mix). Scrubbed per-variable — the ``env -u POSTGRES_*``
    glob idiom is a silent no-op.
    """
    for var in (
        "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE",
        "ENSEMBLE_AUTH_ISSUER",
        "ENSEMBLE_AUTH_DISCOVERY_URL",
        "ENSEMBLE_AUTH_CLIENT_ID",
        "ENSEMBLE_AUTH_CLIENT_SECRET",
        "ENSEMBLE_AUTH_ALLOWED_ORIGINS",
        "ENSEMBLE_AUTH_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)


# ─────────────────────────────────────────────────────────────────
# Service / fixture scaffolding (mirror of the P2 public-lane file)
# ─────────────────────────────────────────────────────────────────


@dataclass
class _PasscodeRow:
    """SQLModel-shape row (same shape the P2 file pins)."""

    id: str
    root_name: str
    rel_path: str
    wrapped_passcode: bytes
    generation: int
    failed_attempts: int
    locked: bool
    created_at: str
    last_attempt_at: Optional[str] = None


class _InMemoryPasscodeStore:
    """In-memory passcode store (same surface the P2 file pins)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], _PasscodeRow] = {}

    def upsert(
        self,
        root_name: str,
        rel_path: str,
        *,
        wrapped_passcode: bytes,
        generation: int = 1,
        failed_attempts: int = 0,
        locked: bool = False,
    ) -> _PasscodeRow:
        key = (root_name, rel_path)
        existing = self.rows.get(key)
        if existing is None:
            row = _PasscodeRow(
                id=f"row-{len(self.rows)+1}",
                root_name=root_name,
                rel_path=rel_path,
                wrapped_passcode=wrapped_passcode,
                generation=generation,
                failed_attempts=failed_attempts,
                locked=locked,
                created_at="2026-11-08T00:00:00Z",
                last_attempt_at=None,
            )
            self.rows[key] = row
        else:
            existing.wrapped_passcode = wrapped_passcode
            existing.generation = generation
            existing.failed_attempts = failed_attempts
            existing.locked = locked
            row = existing
        return row

    def get_row(
        self, root_name: str, rel_path: str
    ) -> Optional[_PasscodeRow]:
        return self.rows.get((root_name, rel_path))


class _InMemoryStorePort:
    """Adapter exposing the methods the router surface touches."""

    def __init__(
        self,
        store: _InMemoryPasscodeStore,
        cipher: ViewPasscodeCipher,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self.audit_rows: list[dict] = []

    def get_row(self, root_name: str, rel_path: str) -> Optional[dict]:
        return self._store.get_row(root_name, rel_path)

    def insert_with_audit(
        self, row: dict, *, actor: Optional[str] = None
    ) -> dict:
        self._store.rows[(row["root_name"], row["rel_path"])] = row
        return row

    def refresh_with_audit(
        self,
        root_name: str,
        rel_path: str,
        new_wrapped_passcode: bytes,
        new_generation: int,
        *,
        actor: Optional[str] = None,
    ) -> Optional[dict]:
        row = self._store.get_row(root_name, rel_path)
        if row is None:
            return None
        row.wrapped_passcode = new_wrapped_passcode
        row.generation = new_generation
        row.failed_attempts = 0
        row.locked = False
        return row

    def record_failure_with_audit(
        self,
        root_name: str,
        rel_path: str,
        *,
        actor: Optional[str] = None,
        ip_hmac: Optional[str] = None,
    ) -> tuple[int, bool]:
        row = self._store.get_row(root_name, rel_path)
        if row is None:
            return (-1, False)
        if row.locked:
            return (row.failed_attempts, True)
        row.failed_attempts += 1
        if row.failed_attempts >= LIVE_VIEW_PASSCODE_STRIKE_CEILING:
            row.locked = True
        return (row.failed_attempts, row.locked)

    def append_audit(
        self,
        *,
        root_name: str,
        rel_path: str,
        generation: int,
        action: str,
        actor: Optional[str] = None,
        ip_hmac: Optional[str] = None,
    ) -> None:
        self.audit_rows.append(
            {
                "action": action,
                "root_name": root_name,
                "rel_path": rel_path,
                "generation": generation,
                "actor": actor,
                "ip_hmac": ip_hmac,
            }
        )


class _StubAuthService:
    """Session-store stand-in — the same seam the middleware uses.

    ``validate_session`` returns an :class:`AuthContext` for the ids in
    ``valid`` and ``None`` for everything else (absent / expired /
    replayed collapse to anonymous — the production contract).
    """

    def __init__(self, valid: set[str]) -> None:
        self.valid = set(valid)
        self.calls: list[str] = []

    async def validate_session(
        self,
        session_id: str,
        *,
        client_ip: str | None = None,
        touch: bool = True,
    ) -> AuthContext | None:
        self.calls.append(session_id)
        if session_id in self.valid:
            return AuthContext(
                subject="operator-test",
                scopes={"jobs:read"},
                auth_method="session",
                expires_at="2099-01-01T00:00:00Z",
            )
        return None


@pytest.fixture
def filesystem_root_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    workdir = tmp_path / "project"
    mockup = workdir / ".agents" / "shared" / "planning" / "feat" / "design" / "mockups"
    mockup.mkdir(parents=True)
    (mockup / "landing.html").write_text("<h1>Landing</h1>")
    return workdir


@pytest.fixture
def cipher() -> ViewPasscodeCipher:
    return ViewPasscodeCipher(encryption_key=_TEST_FERNET_KEY)


@pytest.fixture
def in_memory_store() -> _InMemoryPasscodeStore:
    return _InMemoryPasscodeStore()


@pytest.fixture
def passcode_service(
    cipher: ViewPasscodeCipher,
    in_memory_store: _InMemoryPasscodeStore,
) -> ViewPasscodeService:
    service = ViewPasscodeService.__new__(ViewPasscodeService)
    service._engine = None
    service._cipher = cipher
    service._store = _InMemoryStorePort(in_memory_store, cipher)
    return service


@pytest.fixture
def limiter() -> VerifyRateLimiter:
    return VerifyRateLimiter(capacity=50, refill_per_minute=50)


@pytest.fixture
def tight_limiter() -> VerifyRateLimiter:
    return VerifyRateLimiter(capacity=3, refill_per_minute=1)


@pytest.fixture
def auth_stub() -> _StubAuthService:
    return _StubAuthService({"sess-op-valid"})

def _mock_request(
    headers: dict[str, str],
    auth_config=None,
    *,
    service=None,
) -> StarletteRequest:
    scope = {
        "type": "http",
        "headers": [
            (k.lower().encode(), v.encode())
            for k, v in headers.items()
        ],
        "app": SimpleNamespace(
            state=SimpleNamespace(
                auth_config=auth_config,
                live_views_service=service,
            )
        ),
    }
    return StarletteRequest(scope)

def _build_app(
    filesystem_root_dir: pathlib.Path,
    passcode_service: ViewPasscodeService,
    cipher: ViewPasscodeCipher,
    limiter: VerifyRateLimiter,
    auth_stub: _StubAuthService | None = None,
    auth_config: AuthConfig | None = None,
    with_middleware: bool = False,
    live_views_external_base_url: str | None = None,
) -> FastAPI:
    cfg = LiveViewsConfig()
    if live_views_external_base_url is not None:
        # FIX-7: the second operator-declaration source — the
        # ``live_views.external_base_url`` knob itself.
        cfg.external_base_url = live_views_external_base_url
    workdir = filesystem_root_dir
    cfg.roots["planning"] = LiveViewsRootConfig(
        type="project_scoped",
        path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"],
        allowed_extensions=["html", "mmd", "mermaid", "md"],
    )
    service = LiveViewsService(
        config=cfg,
        project_workdir_by_shortname_resolver=lambda shortname: (
            str(workdir) if shortname == "ens" else None
        ),
    )
    app = FastAPI()
    app.include_router(build_router())
    app.state.live_views_service = service
    app.state.view_passcode_service = passcode_service
    app.state.view_passcode_cipher = cipher
    app.state.view_passcode_grant_ttl_seconds = 86400
    app.state.verify_rate_limiter = limiter
    if auth_stub is not None:
        app.state.auth_service = auth_stub
    if auth_config is not None:
        app.state.auth_config = auth_config
    if with_middleware:
        # The production registration order (daemon/api.py): the
        # /views/public prefix tag lands AFTER include_router — the
        # middleware derives its public set from these attributes.
        from daemon.auth.route_meta import tag_routes_by_path

        tag_routes_by_path(app, path_prefix="/views/public")
        app.add_middleware(AuthMiddleware, auth_config=auth_config)
    return app

@pytest.fixture
def client(
    filesystem_root_dir: pathlib.Path,
    passcode_service: ViewPasscodeService,
    cipher: ViewPasscodeCipher,
    limiter: VerifyRateLimiter,
    auth_stub: _StubAuthService,
) -> TestClient:
    """The anonymous-flow client — auth_service wired (R1 posture:
    a deployment with auth ON), NO auth_config (audience empty on
    purpose so the origin gate's trusted set is the Host-only leg
    unless a test opts in)."""
    app = _build_app(
        filesystem_root_dir, passcode_service, cipher, limiter,
        auth_stub=auth_stub,
    )
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def audience_client(
    filesystem_root_dir: pathlib.Path,
    passcode_service: ViewPasscodeService,
    cipher: ViewPasscodeCipher,
    limiter: VerifyRateLimiter,
    auth_stub: _StubAuthService,
) -> TestClient:
    """Client with an operator audience configured (R2 leg d)."""
    cfg = on_auth_config(
        allowed_origins=["https://audience.example"],
        external_redirect_base="https://portal.example",
    )
    app = _build_app(
        filesystem_root_dir, passcode_service, cipher, limiter,
        auth_stub=auth_stub, auth_config=cfg,
    )
    return TestClient(app, follow_redirects=False)


def _mint(
    in_memory_store: _InMemoryPasscodeStore,
    cipher: ViewPasscodeCipher,
    root_name: str = "planning",
    rel_path: str = _VIEW_REL,
    *, locked: bool = False,
) -> str:
    plaintext = generate_passcode()
    wrapped = cipher.wrap(plaintext.encode("ascii"))
    in_memory_store.upsert(
        root_name, rel_path, wrapped_passcode=wrapped, locked=locked
    )
    return plaintext


# ─────────────────────────────────────────────────────────────────
# R2 — origin gate (navigation-correct)

@pytest.fixture
def proxy_front_client(
    filesystem_root_dir: pathlib.Path,
    passcode_service: ViewPasscodeService,
    cipher: ViewPasscodeCipher,
    limiter: VerifyRateLimiter,
    auth_stub: _StubAuthService,
) -> TestClient:
    """FIX-1 set-state client — the operator DECLARED a proxy front.

    Uses the ONLY legitimate writer of
    ``live_view_external_base_url_fallback``: the post-construction
    attribute assignment (the lifespan one-shot-copy shape; the field
    is stripped from every construction source by
    ``AuthConfig._strip_non_env_fields``).
    """
    cfg = on_auth_config()
    # FIX-6 (pin-vacuity): the declared front host MUST differ from
    # the forwarded/Origin host under test — parse_operator_audience
    # absorbs the fallback into leg (d), so an identical host would
    # let every leg-(b) test pass via the AUDIENCE leg (the
    # reviewer's mut_xfh_only mutant scored 66/66). With
    # ``elsewhere.example`` declared, ``views.example.com`` can only
    # enter the trust set through the forwarded leg itself —
    # deleting leg (b) must fail these tests.
    cfg.live_view_external_base_url_fallback = "https://elsewhere.example"
    app = _build_app(
        filesystem_root_dir, passcode_service, cipher, limiter,
        auth_stub=auth_stub, auth_config=cfg,
    )
    return TestClient(app, follow_redirects=False)

@pytest.fixture
def mw_client(
    filesystem_root_dir: pathlib.Path,
    passcode_service: ViewPasscodeService,
    cipher: ViewPasscodeCipher,
    limiter: VerifyRateLimiter,
    auth_stub: _StubAuthService,
) -> TestClient:
    cfg = on_auth_config()
    app = _build_app(
        filesystem_root_dir, passcode_service, cipher, limiter,
        auth_stub=auth_stub, auth_config=cfg, with_middleware=True,
    )
    return TestClient(
        app, base_url="http://testserver", follow_redirects=False
    )

# ─────────────────────────────────────────────────────────────────
# R1 — session bypass
# ─────────────────────────────────────────────────────────────────

class TestSessionBypassPost:
    """Valid session on the verify POST ⇒ grant + 303 without a
    passcode, immune to gates #1/#3/#4."""

    def test_valid_session_cookie_grants_303_full_attributes(
        self, client: TestClient, in_memory_store, cipher
    ):
        """Obligation 5 — the cookie form; the grant carries the
        SAME attributes a passcode POST mints."""
        _mint(in_memory_store, cipher)
        client.cookies.set(SESSION_COOKIE_NAME, "sess-op-valid")
        resp = client.post(
            _VIEW_PATH,
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        cookie_header = resp.headers.get("set-cookie", "")
        assert f"{GRANT_COOKIE_NAME}=" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "Secure" in cookie_header
        assert "SameSite=Lax" in cookie_header
        assert "Max-Age=86400" in cookie_header
        assert (
            "Path=/views/public/planning/ens/feat/design/mockups/landing.html"
            in cookie_header
        )
        assert resp.headers["location"].endswith(_VIEW_PATH)

    def test_session_bypasses_locked_short_circuit(
        self, client: TestClient, in_memory_store, cipher
    ):
        """R1(a) — no lockout interference: a LOCKED row still
        grants for a session holder (lockout gates the ENTRY lane,
        not established trust; the session outranks a grant)."""
        _mint(in_memory_store, cipher, locked=True)
        client.cookies.set(SESSION_COOKIE_NAME, "sess-op-valid")
        resp = client.post(
            _VIEW_PATH,
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_session_bypasses_origin_gate(
        self, client: TestClient, in_memory_store, cipher
    ):
        """R1(a) — no origin interference: cross-origin evil Origin
        + valid session ⇒ 303 (the session IS the authority)."""
        _mint(in_memory_store, cipher)
        client.cookies.set(SESSION_COOKIE_NAME, "sess-op-valid")
        resp = client.post(
            _VIEW_PATH,
            headers={
                "Origin": "https://evil.example",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_session_bypasses_rate_limiter(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        tight_limiter: VerifyRateLimiter,
        auth_stub: _StubAuthService,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """R1(a) — no rate-limit interference: a drained bucket
        still grants for a session holder."""
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher,
            tight_limiter, auth_stub=auth_stub,
        )
        client = TestClient(app, follow_redirects=False)
        _mint(in_memory_store, cipher)
        client.cookies.set(SESSION_COOKIE_NAME, "sess-op-valid")
        resp = client.post(
            _VIEW_PATH,
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_session_with_unminted_row_uniform_404(
        self, client: TestClient, in_memory_store: _InMemoryPasscodeStore
    ):
        """R1(b) — information-leak parity: an unminted path yields
        the SAME uniform 404 for a session holder as for the
        anonymous prober (JSON envelope under the */* accept)."""
        assert len(in_memory_store.rows) == 0
        client.cookies.set(SESSION_COOKIE_NAME, "sess-op-valid")
        resp = client.post(
            _VIEW_PATH,
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}
        assert len(in_memory_store.rows) == 0  # no row minted

    def test_session_grant_appends_actor_audit(
        self, client: TestClient, in_memory_store, cipher, passcode_service
    ):
        """The session lane records the operator subject as the
        audit ``actor`` (closed action set unchanged)."""
        _mint(in_memory_store, cipher)
        client.cookies.set(SESSION_COOKIE_NAME, "sess-op-valid")
        resp = client.post(
            _VIEW_PATH,
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        port = passcode_service._store
        grants = [
            a for a in port.audit_rows if a["action"] == "grant"
        ]
        assert grants and grants[-1]["actor"] == "operator-test"

class TestSessionBypassGet:
    """Valid session on GET ⇒ content directly; NO grant minted."""

    def test_valid_session_serves_view_no_interstitial_no_grant(
        self, client: TestClient, in_memory_store, cipher
    ):
        """Obligation 4 — the logged-in defect's GET face: content
        200, no interstitial, and NO view_grant cookie minted on
        GET (write-amplification pin)."""
        _mint(in_memory_store, cipher)
        client.cookies.set(SESSION_COOKIE_NAME, "sess-op-valid")
        resp = client.get(_VIEW_PATH)
        assert resp.status_code == 200
        assert "Landing" in resp.text
        assert "Enter passcode" not in resp.text
        assert GRANT_COOKIE_NAME not in resp.headers.get("set-cookie", "")

    def test_valid_session_serves_locked_view_on_get(
        self, client: TestClient, in_memory_store, cipher
    ):
        """A session outranks the locked page on GET too."""
        _mint(in_memory_store, cipher, locked=True)
        client.cookies.set(SESSION_COOKIE_NAME, "sess-op-valid")
        resp = client.get(_VIEW_PATH)
        assert resp.status_code == 200
        assert "Landing" in resp.text
        assert "locked" not in resp.text.lower()

    def test_valid_session_unminted_row_uniform_404(
        self, client: TestClient, in_memory_store: _InMemoryPasscodeStore
    ):
        """R1(b) — GET parity: unminted path ⇒ the same uniform 404
        a sessionless prober gets."""
        assert len(in_memory_store.rows) == 0
        client.cookies.set(SESSION_COOKIE_NAME, "sess-op-valid")
        resp = client.get(_VIEW_PATH)
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_valid_session_board_renders_no_interstitial(
        self, client: TestClient, in_memory_store, cipher
    ):
        """R1 board lane — valid session ⇒ the board RENDER (200,
        page headers, no-store), no interstitial, no grant minted.
        The board backend is unwired here ⇒ the explicit
        unavailable notice — which is STILL the post-gate render,
        proving the gate opened."""
        _mint(
            in_memory_store, cipher,
            root_name="board", rel_path="index.html",
        )
        client.cookies.set(SESSION_COOKIE_NAME, "sess-op-valid")
        resp = client.get("/views/public/board/index.html")
        assert resp.status_code == 200
        assert "Work board" in resp.text
        assert "Enter passcode" not in resp.text
        assert GRANT_COOKIE_NAME not in resp.headers.get("set-cookie", "")
        assert resp.headers["cache-control"] == "no-store"

    def test_anonymous_board_still_interstitial(
        self, client: TestClient, in_memory_store, cipher
    ):
        """R4 — the anonymous board flow is unchanged."""
        _mint(
            in_memory_store, cipher,
            root_name="board", rel_path="index.html",
        )
        resp = client.get("/views/public/board/index.html")
        assert resp.status_code == 200
        assert "Enter passcode" in resp.text

class TestStaleGarbageSessionIsAnonymous:
    """Obligation 6 — stale / garbage / absent ensemble_sid ⇒ the
    EXACT anonymous flow; the session itself NEVER forbidden."""

    def test_garbage_session_get_renders_interstitial(
        self, client: TestClient, in_memory_store, cipher
    ):
        _mint(in_memory_store, cipher)
        client.cookies.set(SESSION_COOKIE_NAME, "garbage-sid")
        resp = client.get(_VIEW_PATH)
        assert resp.status_code == 200
        assert "Enter passcode" in resp.text

    def test_garbage_session_post_requires_passcode(
        self, client: TestClient, in_memory_store, cipher
    ):
        """No code + garbage session ⇒ the anonymous no-code
        failure page (NOT 403, NOT 303)."""
        _mint(in_memory_store, cipher)
        client.cookies.set(SESSION_COOKIE_NAME, "garbage-sid")
        resp = client.post(
            _VIEW_PATH,
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert "Enter passcode" in resp.text

    def test_garbage_session_correct_code_still_grants(
        self, client: TestClient, in_memory_store, cipher
    ):
        """The anonymous chain works byte-identically under a
        garbage session cookie (Origin null included)."""
        plaintext = _mint(in_memory_store, cipher)
        client.cookies.set(SESSION_COOKIE_NAME, "garbage-sid")
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={"Origin": "null", "X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_no_auth_service_wired_is_anonymous(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """auth_service unwired (auth OFF / early traffic) ⇒ the
        bypass seam is inert — pure anonymous flow."""
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub=None,
        )
        client = TestClient(app, follow_redirects=False)
        _mint(in_memory_store, cipher)
        client.cookies.set(SESSION_COOKIE_NAME, "sess-op-valid")
        resp = client.get(_VIEW_PATH)
        assert resp.status_code == 200
        assert "Enter passcode" in resp.text

class TestDirectLaneSessionComposition:
    """The direct lane ``/views/<root>/<rel>`` is UNTAGGED ⇒
    oauth2-protected. A valid session cookie dispatches through
    ``AuthMiddleware`` and serves; a garbage cookie falls to the
    401 (json) / 302 (browser) contracts — composing with the
    fe-spa-401-login-redirect lane already in this base."""

    def test_valid_session_serves_direct_lane(
        self, mw_client: TestClient
    ):
        resp = mw_client.get(
            "/views/planning/ens/feat/design/mockups/landing.html",
            cookies={SESSION_COOKIE_NAME: "sess-op-valid"},
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200
        assert "Landing" in resp.text

    def test_no_session_json_accept_is_401(
        self, mw_client: TestClient
    ):
        resp = mw_client.get(
            "/views/planning/ens/feat/design/mockups/landing.html",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 401

    def test_no_session_browser_accept_redirects_to_login(
        self, mw_client: TestClient
    ):
        """The fe-spa-401 composition: a browser-shaped request with
        no session 302s to the login route with a return_to."""
        resp = mw_client.get(
            "/views/planning/ens/feat/design/mockups/landing.html",
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/api/auth/login")

    def test_garbage_session_json_accept_is_401(
        self, mw_client: TestClient, auth_stub: _StubAuthService
    ):
        resp = mw_client.get(
            "/views/planning/ens/feat/design/mockups/landing.html",
            cookies={SESSION_COOKIE_NAME: "garbage-sid"},
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 401
        assert "garbage-sid" in auth_stub.calls

    def test_public_lane_untouched_by_middleware(
        self, mw_client: TestClient
    ):
        """The public lane passes the middleware untouched (no 401
        shape change, no session requirement) — the exemption is
        structural, unaffected by this commission."""
        resp = mw_client.get(
            _VIEW_PATH, headers={"Accept": "application/json"}
        )
        assert resp.status_code in (200, 303, 404)
        assert resp.status_code != 401

# ─────────────────────────────────────────────────────────────────
# R2 — origin gate (navigation-correct)
# ─────────────────────────────────────────────────────────────────

class TestOriginGateNavigationCorrect:
    """The live defect + the reconciliation legs (R2)."""

    def test_correct_code_origin_null_grants_303(
        self, client: TestClient, in_memory_store, cipher
    ):
        """Obligation 1 — the LIVE DEFECT repro: a top-level
        navigation serializes ``Origin: null``; the old raw
        Host-inequality 403'd every legit form POST. Now: 303 +
        grant cookie."""
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Origin": "null",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        cookie_header = resp.headers.get("set-cookie", "")
        assert f"{GRANT_COOKIE_NAME}=" in cookie_header
        assert "HttpOnly" in cookie_header
        assert "Secure" in cookie_header
        assert "SameSite=Lax" in cookie_header

    def test_correct_code_no_origin_grants_303_control(
        self, client: TestClient, in_memory_store, cipher
    ):
        """Obligation 8 — the control: no Origin at all ⇒ 303
        (unchanged by the rewrite)."""
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"{GRANT_COOKIE_NAME}=" in resp.headers.get("set-cookie", "")

    def test_correct_code_apex_origin_www_host_grants(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        auth_stub: _StubAuthService,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """Obligation 2 — www/apex reconciliation: Origin names the
        apex, the Host header carries www (fronting divergence the
        raw-Host check always 403'd). RE-HOMED (G2.7/FIX-9): the
        www name is now operator-VOUCHED (audience
        ``https://www.example.com``) — the raw Host is no longer an
        unconditional anchor, so the reconciliation runs under the
        audience vouch: the Host name is vouched ⇒ leg (a) anchors
        it; the apex Origin www-folds onto that anchor."""
        cfg = on_auth_config(
            allowed_origins=["https://www.example.com"],
            external_redirect_base="https://www.example.com",
        )
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub=auth_stub, auth_config=cfg,
        )
        anchored = TestClient(app, follow_redirects=False)
        plaintext = _mint(in_memory_store, cipher)
        resp = anchored.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "www.example.com",
                "Origin": "https://example.com",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_cross_origin_evil_gets_html_403_no_attempt(
        self, client: TestClient, in_memory_store, cipher
    ):
        """Obligation 3 — a genuine cross-origin POST is rejected
        with an HTML 403 for a browser (text/html accept), never
        JSON, and the origin gate STILL precedes the compare (no
        strike consumed)."""
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Origin": "https://evil.example",
                "X-Forwarded-Proto": "https",
                "Accept": "text/html,application/xhtml+xml",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403
        assert resp.headers["content-type"].startswith("text/html")
        assert "evil.example" not in resp.text  # no origin echo
        assert _VIEW_REL not in resp.text  # no path disclosure
        assert "application/json" not in resp.headers.get(
            "content-type", ""
        )
        row = in_memory_store.get_row("planning", _VIEW_REL)
        assert row is not None and row.failed_attempts == 0

    def test_cross_origin_evil_json_accept_keeps_json_envelope(
        self, client: TestClient, in_memory_store, cipher
    ):
        """R3 — the programmatic shape keeps the historical JSON
        envelope byte-for-byte."""
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Origin": "https://evil.example",
                "X-Forwarded-Proto": "https",
                "Accept": "application/json",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403
        assert resp.json() == {"error": "forbidden"}

    def test_x_forwarded_host_first_value_trusted(
        self, proxy_front_client: TestClient, in_memory_store, cipher
    ):
        """R2 leg (b) — behind a fronting proxy the browser-facing
        host rides XFH, not the internal Host. FIX-1: the leg is
        active ONLY under a declared proxy front, so this runs on
        the proxy_front_client (the undeclared state is pinned
        negative by TestSelfAnchoringTrustGating)."""
        plaintext = _mint(in_memory_store, cipher)
        resp = proxy_front_client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "internal-svc:8079",
                "X-Forwarded-Host": "views.example.com, internal:8079",
                "Origin": "https://views.example.com",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_rfc7239_forwarded_host_trusted(
        self, proxy_front_client: TestClient, in_memory_store, cipher
    ):
        """R2 leg (c) — the standardized Forwarded spelling (FIX-1:
        declared-proxy-front state)."""
        plaintext = _mint(in_memory_store, cipher)
        resp = proxy_front_client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "internal-svc:8079",
                "Forwarded": 'for=10.0.0.9;host="views.example.com";proto=https',
                "Origin": "https://views.example.com",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_operator_audience_host_trusted(
        self, audience_client: TestClient, in_memory_store, cipher
    ):
        """R2 leg (d) — the operator audience (allowed_origins +
        external base) reconciles a portal-origin POST against an
        unrelated Host."""
        plaintext = _mint(in_memory_store, cipher)
        resp = audience_client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "internal-svc:8079",
                "Origin": "https://portal.example/some/page",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_case_insensitive_and_default_port_normalized(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        auth_stub: _StubAuthService,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """R2 comparison semantics — scheme/case/default-port are
        non-participating. RE-HOMED (G2.7/FIX-9): runs under the
        default operator audience (``{testserver}``) so the Host
        name is VOUCHED — this is also the ANCHORED-Host grant pin:
        a vouched Host==Origin shape grants; the unanchored shape
        rejects (TestHostVouchingG27)."""
        cfg = on_auth_config()  # audience hosts = {testserver}
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub=auth_stub, auth_config=cfg,
        )
        anchored = TestClient(app, follow_redirects=False)
        plaintext = _mint(in_memory_store, cipher)
        resp = anchored.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "TESTSERVER",
                "Origin": "HTTP://TestServer:80",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_explicit_port_mismatch_rejected(
        self, client: TestClient, in_memory_store, cipher
    ):
        """Port-normalized ≠ port-blind: two EXPLICIT distinct ports
        are different trust anchors."""
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "example.com:8443",
                "Origin": "https://example.com:9999",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403

class TestOriginGateUnit:
    """Direct function-level pins on ``_same_host_origin_check``."""

    def test_both_absent_allows(self):
        request = _mock_request({"Host": "example.com"})
        assert _same_host_origin_check(request) is True

    def test_no_host_and_empty_audience_rejects_present_origin(self):
        """Tautology guard: NO trusted host derivable ⇒ a present
        non-null origin is REJECTED (fail closed); absent origin
        still passes."""
        request = _mock_request({"Origin": "https://example.com"})
        assert _same_host_origin_check(request) is False
        request = _mock_request({})
        assert _same_host_origin_check(request) is True

    def test_origin_null_allows_regardless_of_anchor(self):
        request = _mock_request({"Origin": "null"})
        assert _same_host_origin_check(request) is True
        request = _mock_request({"Origin": "NULL"})
        assert _same_host_origin_check(request) is True

    def test_origin_precedence_over_referer(self):
        """C-2 precedence: Origin judges; a mismatching Referer is
        NOT consulted when Origin matches. FLIPPED (G2.7/FIX-9): the
        Host name must now be operator-VOUCHED for the same-origin
        leg — the positive shape runs under a vouching audience; the
        UNVOUCHED identical shape is the rebinding self-match and
        rejects (pinned alongside)."""
        vouching = SimpleNamespace(
            allowed_origins=["https://example.com"],
            external_redirect_base=None,
            live_view_external_base_url_fallback=None,
        )
        request = _mock_request(
            {
                "Host": "example.com",
                "Origin": "https://example.com",
                "Referer": "https://evil.example/x",
            },
            auth_config=vouching,
        )
        assert _same_host_origin_check(request) is True
        # G2.7 — the SAME shape with NO vouching: attacker-controlled
        # Host+Origin self-match ⇒ reject.
        request = _mock_request(
            {
                "Host": "example.com",
                "Origin": "https://example.com",
                "Referer": "https://evil.example/x",
            }
        )
        assert _same_host_origin_check(request) is False

    def test_referer_fallback_when_origin_absent(self):
        """FLIPPED (G2.7/FIX-9): the positive shape runs under a
        vouching audience (www-folds onto the vouched apex); the
        evil-Referer negative is unchanged."""
        vouching = SimpleNamespace(
            allowed_origins=["https://example.com"],
            external_redirect_base=None,
            live_view_external_base_url_fallback=None,
        )
        request = _mock_request(
            {
                "Host": "example.com",
                "Referer": "https://www.example.com/page?q=1#frag",
            },
            auth_config=vouching,
        )
        assert _same_host_origin_check(request) is True
        request = _mock_request(
            {
                "Host": "example.com",
                "Referer": "https://evil.example/page",
            }
        )
        assert _same_host_origin_check(request) is False

    def test_unparseable_origin_rejected(self):
        request = _mock_request(
            {"Host": "example.com", "Origin": ":::not-a-url:::"}
        )
        assert _same_host_origin_check(request) is False

    def test_forwarded_host_first_element_only(self):
        """Only the FIRST Forwarded element's host= is trusted — the
        client-appendable later elements never join the anchor set.
        (FIX-1: the leg requires a declared proxy front, so this
        unit test constructs one via the lifespan-writer shape.)"""
        declared = on_auth_config()
        # FIX-6: declared host ≠ the Forwarded host under test —
        # ``edge.example.com`` must enter ONLY via the Forwarded leg.
        declared.live_view_external_base_url_fallback = (
            "https://elsewhere.example"
        )
        request = _mock_request(
            {
                "Host": "internal:8079",
                "Forwarded": 'for=10.0.0.1;host="edge.example.com", '
                'for=10.0.0.2;host="evil.example.com"',
            },
            auth_config=declared,
        )
        hosts = {h for h, _p in _views_trusted_hosts(request)}
        assert "edge.example.com" in hosts
        assert "evil.example.com" not in hosts

class TestSecondDeclarationSource:
    """FIX-7 (NEW-ISSUE-1) — auth-OFF behind a Host-rewriting proxy
    can now declare the front via ``live_views.external_base_url``
    (the auth fallback's single writer sits inside
    ``if config.auth.enabled``, so auth-OFF previously had NO
    declaration path and the forwarded legs were dead code).
    HARD CONSTRAINTS pinned: OPERATOR CONFIG ONLY (never a client
    header); undeclared ⇒ legs (b)/(c) stay INERT (the closed
    self-anchoring exploit stays closed)."""

    def _authoff_client(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        external_base: str | None,
    ) -> TestClient:
        """A TRUE auth-OFF app shape: NO auth_service, NO
        auth_config on app.state — only the live-views declaration
        varies."""
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub=None, auth_config=None,
            live_views_external_base_url=external_base,
        )
        return TestClient(app, follow_redirects=False)

    def test_xfh_honored_when_auth_off_external_base_declared(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        client = self._authoff_client(
            filesystem_root_dir, passcode_service, cipher, limiter,
            "https://views.example.com",
        )
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "internal-svc:8079",
                "X-Forwarded-Host": "views.example.com, internal:8079",
                "Origin": "https://views.example.com",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_forwarded_honored_when_auth_off_external_base_declared(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        client = self._authoff_client(
            filesystem_root_dir, passcode_service, cipher, limiter,
            "https://views.example.com",
        )
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "internal-svc:8079",
                "Forwarded": 'for=10.0.0.9;host="views.example.com";proto=https',
                "Origin": "https://views.example.com",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_forwarded_rejected_when_auth_off_undeclared(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """The closed exploit stays closed: auth-OFF + NO declaration
        anywhere ⇒ an echoed forwarded anchor 403s (legs inert)."""
        client = self._authoff_client(
            filesystem_root_dir, passcode_service, cipher, limiter,
            None,
        )
        plaintext = _mint(in_memory_store, cipher)
        for forwarded_headers in (
            {
                "Host": "internal-svc:8079",
                "X-Forwarded-Host": "evil.example",
                "Origin": "https://evil.example",
            },
            {
                "Host": "internal-svc:8079",
                "Forwarded": 'for=10.0.0.9;host="evil.example"',
                "Origin": "https://evil.example",
            },
        ):
            resp = client.post(
                _VIEW_PATH,
                data={"code": plaintext},
                headers={
                    **forwarded_headers,
                    "X-Forwarded-Proto": "https",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 403, forwarded_headers
        row = in_memory_store.get_row("planning", _VIEW_REL)
        assert row.failed_attempts == 0  # gates preceded the compare

    def test_declared_external_base_accessor_is_raw_config(
        self, filesystem_root_dir: pathlib.Path
    ):
        """The accessor returns the RAW operator config — not the
        resolved chain (host-capture / bind tiers must never count
        as a declaration)."""
        from daemon.services.live_views import LiveViewsService

        cfg = LiveViewsConfig()
        cfg.external_base_url = "https://views.example.com"
        service = LiveViewsService(
            config=cfg,
            project_workdir_by_shortname_resolver=lambda _s: None,
        )
        assert service.declared_external_base_url == (
            "https://views.example.com"
        )

        unset_cfg = LiveViewsConfig()
        unset_service = LiveViewsService(
            config=unset_cfg,
            project_workdir_by_shortname_resolver=lambda _s: None,
        )
        # Unset config ⇒ None (or ''), NEVER a resolved/host-captured
        # base — the declaration is the operator's, nothing else.
        assert not unset_service.declared_external_base_url

class TestDeclaredBaseAnchors:
    """FIX-8 (NEW-ISSUE-1r, fix-iteration 3) — the DECLARED base's
    own host joins the trust anchor set next to legs (a)/(d).

    Fixes the half-broken topology FIX-7 enabled: under auth-OFF +
    ``external_base_url`` set, the operator's declared Origin (no
    forwarded header) was 403'd because nothing supplied the
    declared host (the audience leg needs an auth config to absorb
    through). The anchor is server-side by construction — the value
    comes from operator config, never a request header.
    """

    def test_declared_base_origin_grants_when_auth_off(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """P1 — THE functional fix (currently 403, must be 303):
        auth-OFF + ``external_base_url`` declared + the declared
        Origin, NO forwarded headers ⇒ grant."""
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub=None, auth_config=None,
            live_views_external_base_url="https://views.example.com",
        )
        client = TestClient(app, follow_redirects=False)
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Origin": "https://views.example.com",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"{GRANT_COOKIE_NAME}=" in resp.headers.get("set-cookie", "")

    def test_declared_base_origin_grants_auth_on_service_source(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        auth_stub: _StubAuthService,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """P2 — anchor self-sufficiency under auth-ON, MUTATION-
        DISCRIMINATING from leg (d): the declared base arrives via
        the SERVICE source (``external_base_url``), which
        ``parse_operator_audience`` never absorbs (it reads only the
        auth config — FIX-6 severance discipline). Audience here is
        the testserver family only; Host is testserver; NO forwarded
        headers — ``elsewhere.example`` can ONLY enter via the
        FIX-8 declared anchor. Deleting the declared-anchor block
        must fail this test."""
        cfg = on_auth_config()  # audience = {testserver}; fallback unset
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub=auth_stub, auth_config=cfg,
            live_views_external_base_url="https://elsewhere.example",
        )
        client = TestClient(app, follow_redirects=False)
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Origin": "https://elsewhere.example",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_undeclared_probes_still_closed(
        self, client: TestClient, in_memory_store, cipher
    ):
        """P3 — re-assert alongside the new pins: with NO declaration
        anywhere, echoed forwarded anchors STILL 403 with zero
        strikes (FIX-1/FIX-7 closures undisturbed by FIX-8)."""
        plaintext = _mint(in_memory_store, cipher)
        for extra in (
            {"X-Forwarded-Host": "evil.example"},
            {"Forwarded": 'for=10.0.0.9;host="evil.example"'},
        ):
            resp = client.post(
                _VIEW_PATH,
                data={"code": plaintext},
                headers={
                    **extra,
                    "Origin": "https://evil.example",
                    "X-Forwarded-Proto": "https",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 403, extra
        row = in_memory_store.get_row("planning", _VIEW_REL)
        assert row.failed_attempts == 0

    def test_trust_set_contains_declared_host_both_sources(self):
        """P4 — unit pin: the declared HOST is in the trust set under
        BOTH declaration sources, absent otherwise; port-bearing
        declaration anchors carry the port; malformed declarations
        contribute nothing (FIX-3 discipline)."""
        def _service(base: str | None):
            cfg = LiveViewsConfig()
            if base is not None:
                cfg.external_base_url = base
            return LiveViewsService(
                config=cfg,
                project_workdir_by_shortname_resolver=lambda _s: None,
            )

        headers = {"Host": "internal-svc:8079"}

        # Source A — auth fallback ONLY. The namespace auth config
        # carries no allowed_origins ⇒ the audience parse is inert ⇒
        # the declared host enters ONLY via the FIX-8 anchor
        # (mutation-discriminating for the auth-fallback source).
        ns_auth = SimpleNamespace(
            live_view_external_base_url_fallback=(
                "https://declared-a.example"
            ),
        )
        hosts_a = {
            h for h, _p in _views_trusted_hosts(
                _mock_request(headers, auth_config=ns_auth)
            )
        }
        assert "declared-a.example" in hosts_a
        # G2.7/FIX-9 — the unvouched raw Host name no longer anchors
        # itself (not in the namespace audience, not loopback-peer).
        assert "internal-svc" not in hosts_a

        # Source B — service declaration ONLY (no auth config at
        # all): auth-OFF posture, audience leg structurally absent.
        hosts_b = {
            h for h, _p in _views_trusted_hosts(
                _mock_request(headers, service=_service("https://declared-b.example"))
            )
        }
        assert "declared-b.example" in hosts_b
        assert "internal-svc" not in hosts_b  # G2.7: unvouched Host

        # Port-bearing declaration — the anchor carries the explicit
        # port (explicit-vs-explicit strictness downstream).
        trusted_port = _views_trusted_hosts(
            _mock_request(headers, service=_service("https://views.example.com:8443"))
        )
        assert ("views.example.com", 8443) in trusted_port

        # Malformed declaration — contributes NOTHING (fail closed).
        hosts_malformed = {
            h for h, _p in _views_trusted_hosts(
                _mock_request(headers, service=_service("https://bad host:"))
            )
        }
        assert "bad host" not in hosts_malformed
        assert "internal-svc" not in hosts_malformed  # G2.7: unvouched

        # Neither source ⇒ neither declared host present, and the
        # unvouched raw Host contributes nothing (G2.7).
        hosts_none = {
            h for h, _p in _views_trusted_hosts(_mock_request(headers))
        }
        assert "declared-a.example" not in hosts_none
        assert "declared-b.example" not in hosts_none
        assert "internal-svc" not in hosts_none

class TestSelfAnchoringTrustGating:
    """FIX-1 (MAJOR-1) — legs (b)/(c) trust NOTHING unless the
    operator declared a proxy front. A client-echoed X-Forwarded-Host
    / Forwarded host= that happens to match a cross Origin must NOT
    become its own trust anchor when the external base is unset."""

    def test_echoed_xfh_rejected_when_proxy_front_undeclared(
        self, client: TestClient, in_memory_store, cipher
    ):
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "internal-svc:8079",
                "X-Forwarded-Host": "evil.example",
                "Origin": "https://evil.example",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403
        row = in_memory_store.get_row("planning", _VIEW_REL)
        assert row.failed_attempts == 0  # gate precedes compare

    def test_echoed_forwarded_rejected_when_proxy_front_undeclared(
        self, client: TestClient, in_memory_store, cipher
    ):
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "internal-svc:8079",
                "Forwarded": 'for=10.0.0.9;host="evil.example"',
                "Origin": "https://evil.example",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403
        row = in_memory_store.get_row("planning", _VIEW_REL)
        assert row.failed_attempts == 0

    def test_xfh_honored_when_proxy_front_declared(
        self, proxy_front_client: TestClient,
        in_memory_store, cipher,
    ):
        """SET-state: the operator's declared external base makes the
        forwarded spellings authoritative — leg (b) works as before."""
        plaintext = _mint(in_memory_store, cipher)
        resp = proxy_front_client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "internal-svc:8079",
                "X-Forwarded-Host": "views.example.com, internal:8079",
                "Origin": "https://views.example.com",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_forwarded_honored_when_proxy_front_declared(
        self, proxy_front_client: TestClient,
        in_memory_store, cipher,
    ):
        """SET-state leg (c)."""
        plaintext = _mint(in_memory_store, cipher)
        resp = proxy_front_client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "internal-svc:8079",
                "Forwarded": 'for=10.0.0.9;host="views.example.com";proto=https',
                "Origin": "https://views.example.com",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_trusted_hosts_unit_unset_vs_set(self):
        """Function-level pin: the forwarded anchors enter the trust
        set ONLY under a declared proxy front."""
        common = {
            "Host": "internal-svc:8079",
            "X-Forwarded-Host": "views.example.com",
            "Forwarded": 'for=10.0.0.9;host="fwd.example.com"',
        }

        unset_cfg = on_auth_config()
        assert getattr(
            unset_cfg, "live_view_external_base_url_fallback", None
        ) is None
        hosts_unset = {
            h for h, _p in _views_trusted_hosts(_mock_request(common, unset_cfg))
        }
        assert "views.example.com" not in hosts_unset
        assert "fwd.example.com" not in hosts_unset
        # G2.7/FIX-9 — the raw client Host is NO longer an
        # unconditional anchor: an unvouched internal name (not in
        # the audience, not a declared base) contributes nothing.
        assert "internal-svc" not in hosts_unset
        assert "testserver" in hosts_unset  # leg (d) audience intact

        set_cfg = on_auth_config()
        # FIX-6: declared host ≠ forwarded hosts under test — each
        # forwarded anchor must enter ONLY via its own leg.
        set_cfg.live_view_external_base_url_fallback = (
            "https://elsewhere.example"
        )
        hosts_set = {
            h for h, _p in _views_trusted_hosts(_mock_request(common, set_cfg))
        }
        assert "views.example.com" in hosts_set  # via leg (b) ONLY
        assert "fwd.example.com" in hosts_set  # via leg (c) ONLY
        assert "elsewhere.example" in hosts_set  # via leg (d)

class TestWwwPrefixAbuseMatrix:
    """T1 — the www-equivalence folds ONE ``www.`` label and is
    suffix-safe: lookalike hosts that merely CONTAIN the trusted
    name are rejected.

    RE-HOMED (G2.7/FIX-9): each row now runs on a client whose
    AUDIENCE VOUCHES the row's Host name — under the vouch gate an
    unanchored lookalike rejects at the HOST stage (before the
    www/parsing comparison runs), which made the www/suffix/prefix
    parsing mutants pass vacuously. Anchored, the parsing is what
    decides — mut_www / mut_suffix / mut_startswith discriminate
    again."""

    @pytest.mark.parametrize(
        ("host", "origin"),
        [
            ("ensem.dev", "https://ensem.dev.evil.example"),
            ("ensem.dev", "https://eviliensem.dev"),
            ("www.example.com", "https://www.www.example.com"),
            ("example.com", "https://example.com.evil.example"),
        ],
    )
    def test_lookalike_hosts_rejected(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        auth_stub: _StubAuthService,
        in_memory_store: _InMemoryPasscodeStore,
        host: str, origin: str,
    ):
        cfg = on_auth_config(
            allowed_origins=[f"https://{host}"],
            external_redirect_base=f"https://{host}",
        )
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub=auth_stub, auth_config=cfg,
        )
        anchored = TestClient(app, follow_redirects=False)
        plaintext = _mint(in_memory_store, cipher)
        resp = anchored.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": host,
                "Origin": origin,
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403
        row = in_memory_store.get_row("planning", _VIEW_REL)
        assert row.failed_attempts == 0

class TestSecFetchSiteDiscriminator:
    """FIX-2 (MAJOR-2) — ``Origin: null`` allows the live-defect
    navigation shapes but rejects the browser-attested cross-site
    sandboxed-iframe POST."""

    def test_null_origin_cross_site_rejected_no_attempt(
        self, client: TestClient, in_memory_store, cipher
    ):
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Origin": "null",
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "navigate",
                "X-Forwarded-Proto": "https",
                "Accept": "text/html,application/xhtml+xml",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403
        assert resp.headers["content-type"].startswith("text/html")
        row = in_memory_store.get_row("planning", _VIEW_REL)
        assert row.failed_attempts == 0  # gate precedes compare

    @pytest.mark.parametrize(
        "sec_fetch_site",
        [
            "same-site",
            "same-origin",
            # ``none`` = user-initiated top-level navigation — the exact
            # shape the live fix protects.
            "none",
        ],
    )
    def test_null_origin_non_cross_site_attestation_grants(
        self, client: TestClient, in_memory_store, cipher, sec_fetch_site: str
    ):
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Origin": "null",
                "Sec-Fetch-Site": sec_fetch_site,
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_null_origin_absent_sfs_header_grants(
        self, client: TestClient, in_memory_store, cipher
    ):
        """The live-fix preservation pin: an OLD browser that sends
        ``Origin: null`` with NO Sec-Fetch-Site at all still grants."""
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Origin": "null",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"{GRANT_COOKIE_NAME}=" in resp.headers.get("set-cookie", "")

class TestPortSemantics:
    """T4 — port normalization is elision-honest and malformed-port
    fail-closed (FIX-3), RE-HOMED under anchored fixtures after
    G2.7 (FIX-9): the unanchored Host==Origin self-match shapes
    these pins used are now REJECTS (the raw client Host is no
    longer an unconditional trust anchor), so the elision/strict
    coverage lives on a DECLARED-BASE-VOUCHED Host."""

    @staticmethod
    def _declared_client(
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        auth_stub: _StubAuthService,
        declared_base: str,
    ) -> TestClient:
        """A client whose declared base vouches the test's Host name
        (the sanctioned post-construction fallback writer)."""
        cfg = on_auth_config()  # audience = {testserver} only
        cfg.live_view_external_base_url_fallback = declared_base
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub=auth_stub, auth_config=cfg,
        )
        return TestClient(app, follow_redirects=False)

    def test_portless_host_vs_explicit_origin_port_self_match_now_rejected(
        self, client: TestClient, in_memory_store, cipher
    ):
        """FLIPPED (G2.7/FIX-9) — the OLD permissive pin asserted 303
        here: a portless Host against an explicit origin port on an
        UNANCHORED name self-matched. The raw Host is attacker-
        controlled on a rebinding victim; an unvouched name is no
        anchor ⇒ 403. The elision behavior itself is re-homed under
        an anchored fixture below."""
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "example.com",
                "Origin": "https://example.com:8443",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_portless_elision_preserved_under_declared_anchor(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        auth_stub: _StubAuthService,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """RE-HOMED elision pin: Host ∈ declared base (vouched) +
        explicit origin port ⇒ portless↔explicit elision still
        grants (the FIX-3 disclosed behavior survives, anchored)."""
        anchored = self._declared_client(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub, "https://example.com",
        )
        plaintext = _mint(in_memory_store, cipher)
        resp = anchored.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "example.com",
                "Origin": "https://example.com:8443",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    @pytest.mark.parametrize(
        "host",
        [
            # FIX-3 — ``example.com:notaport`` vouches for NOTHING: no
            # trusted anchor survives ⇒ the present non-null origin is
            # rejected by the tautology guard.
            "example.com:notaport",
            # :99999 raises ValueError in urlsplit — same fail-closed
            # treatment as the malformed spelling.
            "example.com:99999",
        ],
    )
    def test_malformed_or_out_of_range_host_port_fails_closed(
        self, client: TestClient, in_memory_store, cipher, host: str
    ):
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": host,
                "Origin": "https://example.com",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_malformed_origin_port_fails_closed(
        self, client: TestClient, in_memory_store, cipher
    ):
        """FIX-3 symmetry — a malformed ORIGIN port makes the origin
        unparseable ⇒ reject (the old portless mapping would have
        ridden the elision the other way)."""
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "example.com",
                "Origin": "https://example.com:notaport",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403

    @staticmethod
    def _service_declared_client(
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        auth_stub: _StubAuthService,
        declared_base: str,
    ) -> TestClient:
        """A client whose declared base arrives via the SERVICE
        source — deliberately NOT the auth fallback, which
        ``parse_operator_audience`` absorbs PORTLESSLY into leg (d)
        (a portless anchor elides with any origin port and would
        void the strictness under test)."""
        cfg = on_auth_config()  # audience = {testserver} only
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub=auth_stub, auth_config=cfg,
            live_views_external_base_url=declared_base,
        )
        return TestClient(app, follow_redirects=False)

    def test_malformed_host_port_fails_closed_anchored(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        auth_stub: _StubAuthService,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """FIX-3 discriminator under an ANCHORED fixture (G2.7
        re-home): a port-bearing declared base vouches
        ``example.com``; a malformed Host port leaves the Host
        unparseable ⇒ UNVOUCHED ⇒ no leg-(a) anchor ⇒ the explicit
        origin port :9999 mismatches the declared :8443 anchor ⇒
        403. Under the mut_port mutant (malformed → portless) the
        Host WOULD vouch portlessly and elide ⇒ 303 — this pin
        fails there."""
        anchored = self._service_declared_client(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub, "https://example.com:8443",
        )
        plaintext = _mint(in_memory_store, cipher)
        resp = anchored.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "example.com:notaport",
                "Origin": "https://example.com:9999",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_malformed_origin_port_fails_closed_anchored(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        auth_stub: _StubAuthService,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """mut_originport discriminator under an ANCHORED fixture
        (G2.7 re-home): vouched Host (declared :8443) + malformed
        ORIGIN port ⇒ the origin is unparseable ⇒ 403. Under the
        mutant (malformed origin port → portless) the origin would
        elide against the :8443 anchor ⇒ 303 — this pin fails
        there."""
        anchored = self._service_declared_client(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub, "https://example.com:8443",
        )
        plaintext = _mint(in_memory_store, cipher)
        resp = anchored.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "example.com:8443",
                "Origin": "https://example.com:notaport",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_explicit_vs_explicit_mismatch_rejected(
        self, client: TestClient, in_memory_store, cipher
    ):
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "example.com:8443",
                "Origin": "https://example.com:9999",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_explicit_vs_explicit_match_self_match_now_rejected(
        self, client: TestClient, in_memory_store, cipher
    ):
        """FLIPPED (G2.7/FIX-9) — the OLD permissive pin asserted 303
        for an unanchored explicit-vs-explicit Host==Origin
        self-match; the raw Host is no longer an unconditional
        anchor ⇒ 403. The strictness coverage is re-homed under an
        anchored fixture below."""
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "example.com:8443",
                "Origin": "https://example.com:8443",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_explicit_vs_explicit_match_preserved_under_declared_anchor(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        auth_stub: _StubAuthService,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """RE-HOMED explicit-vs-explicit pin: Host ∈ declared base
        (port-bearing declaration ⇒ port-bearing vouch anchor) +
        identical explicit origin port ⇒ 303; strictness (a
        DIFFERENT explicit port) stays rejected by the existing
        mismatch pin above."""
        anchored = self._service_declared_client(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub, "https://example.com:8443",
        )
        plaintext = _mint(in_memory_store, cipher)
        resp = anchored.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "example.com:8443",
                "Origin": "https://example.com:8443",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

# ─────────────────────────────────────────────────────────────────
# R3/R4 — never JSON on human pages / content-negotiation
# ─────────────────────────────────────────────────────────────────

class TestNeverJsonOnHumanPages:
    """Accept discrimination on the miss / throttle / lock shapes."""

    def test_unminted_html_accept_html_404_no_path_leak(
        self, client: TestClient
    ):
        """Obligation 9a — Accept: text/html ⇒ the HTML 404 page,
        no path leakage."""
        resp = client.get(
            "/views/public/planning/ens/feat/design/mockups/never_minted.html",
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        assert resp.status_code == 404
        assert resp.headers["content-type"].startswith("text/html")
        body = resp.text
        assert "never_minted" not in body
        assert "planning" not in body
        assert "ens/" not in body
        assert "view not found" in body.lower() or "not found" in body.lower()

    def test_unminted_wildcard_accept_json_byte_identical(
        self, client: TestClient, in_memory_store: _InMemoryPasscodeStore
    ):
        """Obligation 9b — Accept: */* ⇒ the historical JSON
        envelope byte-for-byte."""
        resp = client.get(
            "/views/public/planning/ens/feat/design/mockups/never_minted.html",
            headers={"Accept": "*/*"},
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}
        assert resp.headers["content-type"].startswith("application/json")

    def test_unminted_explicit_json_accept_json_envelope(
        self, client: TestClient
    ):
        """Obligation 9b — Accept: application/json ⇒ the same JSON
        envelope."""
        resp = client.get(
            "/views/public/planning/ens/feat/design/mockups/never_minted.html",
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_wrong_code_html_form_never_json(
        self, client: TestClient, in_memory_store, cipher
    ):
        """Obligation 7 — wrong code ⇒ the HTML form re-render under
        EVERY accept shape; never application/json."""
        plaintext = _mint(in_memory_store, cipher)
        for accept in (
            "text/html,application/xhtml+xml",
            "*/*",
            "application/json",
        ):
            resp = client.post(
                _VIEW_PATH,
                data={"code": "wrong0"},
                headers={
                    "Accept": accept,
                    "X-Forwarded-Proto": "https",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/html")
            assert 'name="code"' in resp.text  # the form marker
            assert "not recognised" in resp.text

    def test_rate_limit_429_html_for_browser_json_for_api(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        tight_limiter: VerifyRateLimiter,
        auth_stub: _StubAuthService,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """R3 — 429: browsers (text/html) and the historical ``*/*``
        shape keep the HTML notice; an explicit application/json
        client gets the JSON envelope. Neither discloses the strike
        count."""
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher,
            tight_limiter, auth_stub=auth_stub,
        )
        _mint(in_memory_store, cipher)
        html_client = TestClient(app, follow_redirects=False)
        for _ in range(4):
            html_client.post(
                _VIEW_PATH,
                data={"code": "wrong0"},
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "X-Forwarded-Proto": "https",
                },
                follow_redirects=False,
            )
        resp = html_client.post(
            _VIEW_PATH,
            data={"code": "wrong0"},
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 429
        assert resp.headers["content-type"].startswith("text/html")

        api_client = TestClient(app, follow_redirects=False)
        resp = api_client.post(
            _VIEW_PATH,
            data={"code": "wrong0"},
            headers={
                "Accept": "application/json",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 429
        assert resp.json() == {"error": "rate limited"}

    def test_locked_html_for_wildcard_json_for_api(
        self, client: TestClient, in_memory_store, cipher
    ):
        """R3 — the locked short-circuit: ``*/*`` (the historical
        shape) keeps the HTML locked page; explicit application/json
        gets the envelope. No strike-count disclosure in either."""
        _mint(in_memory_store, cipher)
        for _ in range(LIVE_VIEW_PASSCODE_STRIKE_CEILING):
            client.post(
                _VIEW_PATH,
                data={"code": "wrong0"},
                headers={"X-Forwarded-Proto": "https"},
                follow_redirects=False,
            )
        resp = client.get(
            _VIEW_PATH, headers={"Accept": "*/*"}
        )
        assert resp.status_code == 200
        assert "locked" in resp.text.lower()

        resp = client.get(
            _VIEW_PATH, headers={"Accept": "application/json"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"error": "view locked"}

    def test_mermaid_asset_500_stays_json(self, client: TestClient):
        """R3 scope guard — the script-consumed asset envelope is
        untouched (a corrupted asset fails the fetch, the wrapper
        degrades; an HTML page here would break the script
        consumer)."""
        from daemon.services import live_views as lv

        original = lv._vendored_mermaid_asset

        def _boom():
            raise FileNotFoundError("no asset")

        lv._vendored_mermaid_asset = _boom
        try:
            resp = client.get(
                "/views/public/assets/mermaid/mermaid.min.js",
                headers={"Accept": "text/html"},
            )
        finally:
            lv._vendored_mermaid_asset = original
        assert resp.status_code == 500
        assert resp.json() == {"error": "asset unavailable"}

# ─────────────────────────────────────────────────────────────────
# R5 — view_write covered by construction
# ─────────────────────────────────────────────────────────────────

class TestViewWriteCoveredByConstruction:
    """R5 — ``view_write`` owns NO HTTP surface: it writes the file
    + mints the passcode (agent tool); the serving rides the SAME
    ``/views/public`` handlers every R1/R2/R3 fix changed.
    Structurally: (1) no view_write route family exists on the
    router; (2) a reports-root page flows through the same POST
    verify + GET gates asserted above."""

    def test_no_view_write_route_family_on_router(self):
        from daemon.routers.live_views import build_router as _br

        paths = {getattr(r, "path", "") for r in _br().routes}
        assert not any("view_write" in p or "view-write" in p for p in paths)
        # The whole /views surface is exactly the direct family +
        # the public family + the asset alias — view_write adds no
        # route of its own (its pages ARE public-lane pages).
        assert paths == {
            "/views/assets/mermaid/mermaid.min.js",
            "/views/public/assets/mermaid/mermaid.min.js",
            "/views/public/board/index.html",
            "/views/public/{root_name}/{rel_path:path}",
            "/views/{root_name}/{rel_path:path}",
        }

    def test_reports_root_page_rides_same_gates(
        self, client: TestClient, in_memory_store, cipher
    ):
        """A view_write-shaped page (reports root, minted passcode)
        verifies through the SAME POST handler: Origin null (the
        R2 defect shape) ⇒ 303; cross-origin evil ⇒ 403 with no
        attempt consumed."""
        plaintext = generate_passcode()
        wrapped = cipher.wrap(plaintext.encode("ascii"))
        in_memory_store.upsert(
            "reports", "page.html", wrapped_passcode=wrapped
        )
        resp = client.post(
            "/views/public/reports/page.html",
            data={"code": plaintext},
            headers={"Origin": "null", "X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"{GRANT_COOKIE_NAME}=" in resp.headers.get("set-cookie", "")

        plaintext2 = generate_passcode()
        wrapped2 = cipher.wrap(plaintext2.encode("ascii"))
        in_memory_store.upsert(
            "reports", "page2.html", wrapped_passcode=wrapped2
        )
        resp = client.post(
            "/views/public/reports/page2.html",
            data={"code": plaintext2},
            headers={
                "Origin": "https://evil.example",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403
        row = in_memory_store.get_row("reports", "page2.html")
        assert row.failed_attempts == 0


# ─────────────────────────────────────────────────────────────────
# G2.7 / FIX-9 — Host anchor requires operator vouching
# ─────────────────────────────────────────────────────────────────


class TestHostVouchingG27:
    """G2.7 (anomaly 2c719281) — the DNS-rebinding self-match exploit.

    ``Host: evil.example`` + ``Origin: https://evil.example`` (BOTH
    values attacker-controlled on a rebinding victim) used to
    self-match via the unconditional Host anchor and mint a FULL
    view_grant. FIX-9 mirrors the auth lane's ``host_is_trusted``
    hardening: leg (a) anchors the Host ONLY when its name is
    operator-vouched (audience ∪ declared bases ∪ the loopback
    carve). Mutation-pinned via ``mut_rawhost`` (restore
    unconditional Host trust ⇒ the reject pin MUST fail).
    """

    def test_g2_7_rebinding_self_match_rejected_no_strikes(
        self, client: TestClient, in_memory_store, cipher
    ):
        """THE G2.7 reject pin — the exact tester exploit shape:
        Host evil.example + Origin https://evil.example, no anchors
        ⇒ 403, HTML for the browser, ZERO strikes consumed (the
        gate precedes the compare)."""
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "evil.example",
                "Origin": "https://evil.example",
                "X-Forwarded-Proto": "https",
                "Accept": "text/html,application/xhtml+xml",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403
        assert resp.headers["content-type"].startswith("text/html")
        assert "evil.example" not in resp.text  # no origin echo
        row = in_memory_store.get_row("planning", _VIEW_REL)
        assert row is not None and row.failed_attempts == 0

    def test_anchored_host_grant_via_declared_base(
        self, proxy_front_client: TestClient,
        in_memory_store, cipher,
    ):
        """Anchored-Host grant — Host==Origin with the Host ∈ the
        declared base ⇒ 303 (the strict contract still admits the
        vouched self-shape)."""
        plaintext = _mint(in_memory_store, cipher)
        resp = proxy_front_client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "elsewhere.example",
                "Origin": "https://elsewhere.example",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    @staticmethod
    def _loopback_client(
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
    ) -> TestClient:
        """Unconfigured dev posture (auth-OFF, no declarations) with
        a LOOPBACK TCP peer — the carve's sound-by-construction
        gate."""
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub=None, auth_config=None,
        )
        return TestClient(
            app, follow_redirects=False, client=("127.0.0.1", 54321)
        )

    def test_loopback_carve_grants_real_same_origin(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """Degradation (iii) — unconfigured DIRECT loopback access:
        a REAL same-origin browser form POST keeps working via the
        carve (exact loopback Host name + loopback TCP peer — the
        sound-by-construction fallback; a remote rebinding victim
        can never connect FROM loopback)."""
        anchored = self._loopback_client(
            filesystem_root_dir, passcode_service, cipher, limiter,
        )
        plaintext = _mint(in_memory_store, cipher)
        resp = anchored.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "localhost:9797",
                "Origin": "http://localhost:9797",
                # The secure-context gate (D6:79) is orthogonal to
                # the origin gate — the harness convention (XFP
                # header) isolates the carve under test.
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    @pytest.mark.parametrize(
        "host",
        ["localhost.evil.example", "127.0.0.1.evil.example"],
    )
    def test_loopback_lookalikes_rejected_even_from_loopback(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        in_memory_store: _InMemoryPasscodeStore,
        host: str,
    ):
        """The carve is EXACT-name only (C-2b mirror): loopback
        LOOKALIKES fail even with a loopback peer."""
        anchored = self._loopback_client(
            filesystem_root_dir, passcode_service, cipher, limiter,
        )
        plaintext = _mint(in_memory_store, cipher)
        resp = anchored.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": host,
                "Origin": f"https://{host}",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_loopback_carve_requires_loopback_peer(
        self,
        filesystem_root_dir: pathlib.Path,
        passcode_service: ViewPasscodeService,
        cipher: ViewPasscodeCipher,
        limiter: VerifyRateLimiter,
        in_memory_store: _InMemoryPasscodeStore,
    ):
        """The carve's soundness gate: a LOOPBACK-lookalike-less
        exact loopback Host+Origin from a NON-loopback peer (the
        TestClient default ``testclient`` peer is not loopback) is
        REJECTED — a remote client can send ``Host: localhost`` and
        ``Origin: http://localhost`` but cannot make its peer
        loopback (the C-2a argument, views-lane mirror)."""
        app = _build_app(
            filesystem_root_dir, passcode_service, cipher, limiter,
            auth_stub=None, auth_config=None,
        )
        remote = TestClient(app, follow_redirects=False)
        plaintext = _mint(in_memory_store, cipher)
        resp = remote.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "localhost:9797",
                "Origin": "http://localhost:9797",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_degradation_iv_unconfigured_non_loopback_real_origin(
        self, client: TestClient, in_memory_store, cipher
    ):
        """Degradation (iv) — the INTENDED strict-contract
        degradation: unconfigured NON-loopback direct access
        (default testclient peer — bare-IP/LAN shape, no audience,
        no declared base): a same-origin POST with a REAL Origin now
        REJECTS (the cure is the operator declaring a base or an
        audience)."""
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={
                "Host": "testserver",
                "Origin": "https://testserver",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 403
        row = in_memory_store.get_row("planning", _VIEW_REL)
        assert row.failed_attempts == 0

    def test_degradation_iv_absent_and_null_origin_still_pass(
        self, client: TestClient, in_memory_store, cipher
    ):
        """Degradation (iv) boundary — the headerless and null
        flows are UNCHANGED on the same unconfigured non-loopback
        posture: absent Origin ⇒ allow; null Origin (no
        cross-site attestation) ⇒ allow. Both grant."""
        plaintext = _mint(in_memory_store, cipher)
        resp = client.post(
            _VIEW_PATH,
            data={"code": plaintext},
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        plaintext_null = _mint(
            in_memory_store, cipher,
            rel_path="ens/feat/design/mockups/pricing.html",
        )
        resp = client.post(
            "/views/public/planning/ens/feat/design/mockups/pricing.html",
            data={"code": plaintext_null},
            headers={
                "Origin": "null",
                "X-Forwarded-Proto": "https",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
