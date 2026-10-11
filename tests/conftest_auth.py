"""Auth-aware conftest — oauth2-auth Phase 5 (T5.2 / D5.2 / FR-34).

Two-fixture estate:

* ``auth_disabled_app`` — the production OFF-state (``auth.enabled`` is
  ``False``); the middleware is NOT registered (D3.2 / D5 / FR-2);
  behavior is byte-identical to the pre-build app.
* ``auth_enabled_app`` — the production ON-state. The fake OIDC provider
  (T5.1) is composed UNDER the daemon's ASGI tree at the ``/_test_oidc/``
  prefix via a single ``ASGITransport`` (no Docker, no real port).

The fixture contract is:

    with auth_disabled_app() as (app, transport):
        client = httpx.AsyncClient(transport=transport, base_url="http://test")
        r = await client.get("/api/auth/login")
        assert r.status_code == 503  # no service attached

    with auth_enabled_app() as (app, fake, transport):
        client = httpx.AsyncClient(transport=transport, base_url="http://test")
        ...
        token = mint_test_token(fake)
        r = await client.get("/api/auth/me",
                              headers={"Authorization": f"Bearer {token}"})

The two-fixture split is deliberate (D5.2 rationale): one fixture with
a runtime flag flip mixes concerns; the OFF-state proof (AC-1, NFR-1)
needs a clean separation.

Host-recorder pre-warm helper (``prime_host_recorder``): the D3.5
chain returns ``None`` until Host-capture learns the base. The helper
seeds the recorder explicitly so ``build_login_url`` resolves the
redirect base on the FIRST login attempt.
"""

from __future__ import annotations

import contextlib
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon.api import create_app
from daemon.config import AuthConfig
from daemon.mcp.kb_keys import McpKbKeyStore
from daemon.repositories.auth_session.models import (
    AuthBootstrapConsumption,
    AuthSession,
    AuthState,
    BearerToken,
)
from daemon.repositories.auth_session.repository import AuthSessionRepository
from daemon.repositories.mcp_kb_keys.models import McpKbKey  # noqa: F401

from daemon.services.auth_service import AuthService
from daemon.services.live_views import HostRecorder
from daemon.services.oauth_client import OAuthClient
from daemon.services.timestamps import now_utc_iso

from tests.fakes.fake_oidc_provider import (
    ISSUER_DEFAULT,
    FakeOidcProvider,
)


# ── constants ──────────────────────────────────────────────────────────

#: The fake's "issuer" URL — used by the daemon's AuthConfig so the
#: OAuthClient validates iss with the same value the fake uses.
TEST_ISSUER = ISSUER_DEFAULT
TEST_CLIENT_ID = "test-app"
TEST_CLIENT_SECRET = "test-secret"
TEST_EXTERNAL_REDIRECT_BASE = "http://testserver"
TEST_ALLOWED_ORIGIN = "http://testserver"

AUTH_TABLES = [
    AuthSession.__table__,
    AuthState.__table__,
    BearerToken.__table__,
    AuthBootstrapConsumption.__table__,
    McpKbKey.__table__,
]


# ── AuthConfig builder ────────────────────────────────────────────────


def on_auth_config(**overrides: Any) -> AuthConfig:
    """An auth-ON AuthConfig valid under the §6.3 combination matrix."""
    fields: dict[str, Any] = {
        "enabled": True,
        "client_id": TEST_CLIENT_ID,
        "client_secret": SecretStr(TEST_CLIENT_SECRET),
        "issuer": TEST_ISSUER,
        "allow_insecure_cookie": True,
        "cookie_secure": False,
        "allowed_origins": [TEST_ALLOWED_ORIGIN],
        "external_redirect_base": TEST_EXTERNAL_REDIRECT_BASE,
    }
    fields.update(overrides)
    return AuthConfig(**fields)


# ── OIDC fake wrapper + transport factory ──────────────────────────────


class _WrappedApp:
    """Pure-ASGI wrapper: the daemon at ``/``, the fake OIDC at
    ``/_test_oidc/...``.

    The wrapper is intentionally NOT a Starlette :class:`Mount` — a
    direct ASGI dispatcher avoids the trailing-slash routing Starlette
    would otherwise need and keeps the fake's incoming path unchanged
    after we strip the prefix.
    """

    def __init__(self, app: FastAPI, fake: FakeOidcProvider) -> None:
        self._app = app
        self._fake = fake

    async def __call__(
        self, scope: dict[str, Any], receive: Any, send: Any
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith("/_test_oidc"):
            # Translate outbound so the fake sees the path WITHOUT the
            # ``/_test_oidc`` prefix (it defines its own routes from
            # ``/.well-known/openid-configuration`` etc.).
            translated = dict(scope)
            translated["path"] = path[len("/_test_oidc"):] or "/"
            translated["raw_path"] = translated["path"].encode("latin-1")
            await self._fake(translated, receive, send)
            return
        await self._app(scope, receive, send)


def _build_wrapped_app(
    *,
    failure_mode: str,
    tmp_path: Any,
) -> tuple[FastAPI, FakeOidcProvider, AuthSessionRepository, AuthService, _WrappedApp]:
    """Wire the daemon + Pinned fake + AuthService + bearer store."""
    db_engine = create_engine(f"sqlite:///{tmp_path}/auth.db", future=True)
    SQLModel.metadata.create_all(db_engine, tables=AUTH_TABLES)

    cfg = on_auth_config()
    app = create_app(auth_config=cfg)

    fake = FakeOidcProvider(
        issuer=TEST_ISSUER,
        client_id=TEST_CLIENT_ID,
        failure_mode=failure_mode,
    )
    repository = AuthSessionRepository(db_engine)
    wrapped = _WrappedApp(app, fake)

    # The OAuthClient's outbound HTTP is routed back through the wrapped
    # ASGI tree — the fake lives at the same origin as the test, so a
    # tiny ``MockTransport`` handler re-issues each outbound request as
    # an in-process call against the wrapped ASGI app. This keeps the
    # fake's URL the same as the test client's URL (no double-stack
    # translation) and avoids real network.
    outbound_calls: list[str] = []

    async def _handle_outbound(request: httpx.Request) -> httpx.Response:
        outbound_calls.append(str(request.url))
        # The OAuthClient speaks absolute URLs to the IdP (``{issuer}/.well-known/...``).
        # We re-issue the call against our wrapped app, rooted at ``/_test_oidc``.
        original = str(request.url)
        assert original.startswith(TEST_ISSUER), (
            f"unexpected outbound URL not on the fake issuer: {original}"
        )
        path = "/_test_oidc" + original[len(TEST_ISSUER):]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=wrapped),
            base_url="http://test",
        ) as outbound_client:
            method = request.method
            if method == "GET":
                r = await outbound_client.get(path, headers=dict(request.headers))
            else:
                r = await outbound_client.post(
                    path,
                    content=request.content,
                    headers=dict(request.headers),
                )
        return httpx.Response(
            status_code=r.status_code,
            headers=r.headers,
            content=r.content,
            request=request,
        )

    async def _noop_sleep(_seconds: float) -> None:
        return None

    transport = httpx.MockTransport(_handle_outbound)
    oauth_client = OAuthClient(cfg, transport=transport, sleeper=_noop_sleep)

    service = AuthService(
        config=cfg,
        repository=repository,
        oauth_client=oauth_client,
        bind_host="testserver",
        bind_port=80,
        host_recorder=None,
    )
    app.state.auth_config = cfg
    app.state.auth_session_repository = repository
    app.state.oauth_client = oauth_client
    app.state.auth_service = service
    app.state.mcp_kb_key_store = McpKbKeyStore(db_engine)
    return app, fake, repository, service, wrapped


# ── context-manager fixtures ──────────────────────────────────────────


@contextlib.contextmanager
def auth_disabled_app():
    """OFF-state app — the production D3.2 / D5 / FR-2 byte-equivalent.

    Yields ``(app, transport)``; the app has NO auth middleware
    registered and NO auth-service attribute on ``app.state``.
    """
    app = create_app()  # auth OFF — middleware NOT registered
    transport = httpx.ASGITransport(app=app)
    try:
        yield app, transport
    finally:
        pass


@contextlib.contextmanager
def auth_enabled_app(
    *,
    tmp_path: Any,
    failure_mode: str = "",
):
    """ON-state app + OIDC fake + transport — the canonical fixture.

    Yields ``(app, fake, transport)``. ``fake`` is the
    FakeOidcProvider for direct introspection (request log, the active
    failure_mode, the ``mint_id_token`` helper).
    """
    app, fake, _repository, _service, wrapped = _build_wrapped_app(
        failure_mode=failure_mode, tmp_path=tmp_path
    )
    transport = httpx.ASGITransport(app=wrapped)
    try:
        yield app, fake, transport
    finally:
        pass


# ── mint_test_token ───────────────────────────────────────────────────


def mint_test_token(
    provider: FakeOidcProvider,
    *,
    label: str = "test-token",  # noqa: ARG001 — decorative; surfaced for symmetry
    subject: str = "user-123",
    scopes: list[str] | tuple[str, ...] | None = None,
    audience: str | None = None,
    issuer: str | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Mint a signed JWT the OAuthClient will accept.

    Defaults the subject / audience / issuer to the matching AuthConfig
    so callers don't have to pass them. ``label`` is decorative (no v1
    mapping; the bearer lane reads the plaintext from the token
    material, the session lane gets the subject from ``sub``).
    """
    if scopes is not None:  # surface only; kept for future signature stability
        pass
    return provider.mint_id_token(subject=subject)


# ── host-recorder pre-warm ──────────────────────────────────────────


def prime_host_recorder(app: FastAPI, *, base: str = "http://testserver") -> None:
    """Seed the HostRecorder so ``build_login_url`` resolves on the
    very FIRST /api/auth/login call.

    Without this, the recorder is cold and the resolver chain returns
    ``None`` ⇒ 503 LOGIN_NOT_CONFIGURED (the AC-28 hostile leg). The
    helper constructs a recorder if absent and primes it with the
    given base.
    """
    recorder = getattr(app.state, "host_recorder", None)
    if recorder is None:
        recorder = HostRecorder()
        app.state.host_recorder = recorder
    recorder.record(host=base, scheme="http", path="/")


# ── bearer mint helper ───────────────────────────────────────────────


def mint_bearer_token(
    *,
    subject: str,
    scopes: tuple[str, ...],
    label: str = "test-bearer",
    ttl_seconds: int = 86400,
    repository: AuthSessionRepository | None = None,
    engine: Any | None = None,
) -> tuple[str, str]:
    """Insert one bearer row into the store and return
    ``(plaintext, sha256_hex)``.

    Hash-only discipline preserved (NFR-13 / D20b): the plaintext is
    NEVER persisted; only the SHA-256 hex is written to the row.
    """
    if repository is None:
        if engine is None:
            raise ValueError("either repository or engine is required")
        repository = AuthSessionRepository(engine)
    plaintext = "btok-" + secrets.token_urlsafe(24)
    id_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    now = now_utc_iso()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    row = BearerToken(
        id_hash=id_hash,
        subject=subject,
        scopes=",".join(scopes),
        label=label,
        created_at=now,
        expires_at=expires,
    )
    with Session(repository._engine) as session:
        session.add(row)
        session.commit()
    return plaintext, id_hash


# ── legacy attach (compatibility) ────────────────────────────────────


def attach_auth_state_for_p5(
    app: FastAPI,
    _fake: FakeOidcProvider | None = None,
    *,
    tmp_path: Any,
) -> AuthSessionRepository:
    """Compatibility wrapper around the lifespan's auth half.

    Returns the repository so the caller can mint sessions / bearers /
    states from the same SQLModel session. Mirrors the P3
    ``tests/helpers/auth_fixture.attach_auth_state`` shape so tests
    that already use the legacy fixture don't have to refactor.
    """
    return _build_wrapped_app(failure_mode="", tmp_path=tmp_path)[2]


# ── pytest fixtures (sync) ────────────────────────────────────────────


@pytest.fixture()
def on_auth_enabled_app(tmp_path):
    """ON-state sync fixture for tests that prefer pytest fixtures over
    context managers. Yields ``(app, fake, transport)``.
    """
    with auth_enabled_app(tmp_path=tmp_path) as triple:
        app, fake, transport = triple
        yield app, fake, transport


@pytest.fixture()
def on_auth_disabled_app():
    """OFF-state sync fixture. Yields ``(app, transport)``; the app
    has NO auth middleware registered.
    """
    with auth_disabled_app() as pair:
        app, transport = pair
        yield app, transport


__all__ = [
    "AUTH_TABLES",
    "TEST_ALLOWED_ORIGIN",
    "TEST_CLIENT_ID",
    "TEST_CLIENT_SECRET",
    "TEST_EXTERNAL_REDIRECT_BASE",
    "TEST_ISSUER",
    "attach_auth_state_for_p5",
    "auth_disabled_app",
    "auth_enabled_app",
    "mint_bearer_token",
    "mint_test_token",
    "on_auth_config",
    "prime_host_recorder",
]