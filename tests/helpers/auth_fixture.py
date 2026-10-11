"""ON-state auth fixture builder — oauth2-auth Phase 3 integration tests.

Builds a REAL ``create_app(auth_config=...)`` (the production wiring —
middleware, route tagging, docs/CORS posture) with a synthetic auth
service attached: the P2 SQLite scratch store + the P2 OAuthClient on
the :class:`tests.helpers.fake_oidc_stub.FakeOidcStub` MockTransport.
The daemon lifespan is NEVER entered (it boots the manager) — the
fixture performs exactly the lifespan's auth half (the D3.5 fallback
copy + the AuthService/store construction) inline, which is the same
code shape the lifespan runs.

All scratch state lives in ``tmp_path``; no real network (the stub is
an httpx.MockTransport); no real PG (SQLite file engine per the
auth_pg_scratch_helper convention).
"""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from daemon.config import AuthConfig
from daemon.repositories.auth_session.models import (  # noqa: F401
    AuthBootstrapConsumption,
    AuthSession,
    AuthState,
    BearerToken,
)
from daemon.repositories.mcp_kb_keys.models import McpKbKey  # noqa: F401
from daemon.repositories.auth_session.repository import AuthSessionRepository
from daemon.services.auth_session.lifecycle import AUTH_STATE_ATTRS
from daemon.services.oauth_client import OAuthClient
from sqlmodel import SQLModel
from tests.helpers.fake_oidc_stub import ISSUER, FakeOidcStub

AUTH_TABLES = [
    AuthSession.__table__,
    AuthState.__table__,
    BearerToken.__table__,
    AuthBootstrapConsumption.__table__,
    McpKbKey.__table__,
]


def on_auth_config(**overrides: Any) -> AuthConfig:
    """An auth-ON AuthConfig valid under the §6.3 combination matrix."""
    fields: dict[str, Any] = {
        "enabled": True,
        "client_id": "test-app",
        "client_secret": SecretStr("test-secret"),
        "issuer": ISSUER,
        "allow_insecure_cookie": True,
        "cookie_secure": False,
        "allowed_origins": ["http://testserver"],
        "external_redirect_base": "http://testserver",
    }
    fields.update(overrides)
    return AuthConfig(**fields)


def attach_auth_state(
    app: Any,
    config: AuthConfig,
    stub: FakeOidcStub,
    tmp_path: Any,
) -> Any:
    """Perform the lifespan's auth half on a bare app (no lifespan).

    Creates the SQLite scratch auth tables, the repository, the
    OAuthClient (stub transport), the AuthService, and the module-owned
    MCP-KB key store; mirrors the app.state wiring the real lifespan
    does (same attribute names).
    """
    engine = _sqlite_engine(tmp_path)
    SQLModel.metadata.create_all(engine, tables=AUTH_TABLES)
    repository = AuthSessionRepository(engine)
    client = OAuthClient(config, transport=stub.transport)
    config.live_view_external_base_url_fallback = None

    from daemon.services.auth_service import AuthService
    from daemon.mcp.kb_keys import McpKbKeyStore

    service = AuthService(
        config=config,
        repository=repository,
        oauth_client=client,
        bind_host="testserver",
        bind_port=80,
        host_recorder=None,
    )
    app.state.auth_config = config
    # AUTH_STATE_ATTRS mirror (the P2 lifecycle contract) — the tests
    # introspect the same names the real lifespan sets.
    app.state.auth_session_repository = repository
    app.state.oauth_client = client
    app.state.auth_service = service
    app.state.mcp_kb_key_store = McpKbKeyStore(engine)
    return service


def _sqlite_engine(tmp_path: Any) -> Any:
    from sqlalchemy import create_engine

    return create_engine(f"sqlite:///{tmp_path}/auth_test.db", future=True)


__all__ = [
    "AUTH_STATE_ATTRS",
    "attach_auth_state",
    "on_auth_config",
]
