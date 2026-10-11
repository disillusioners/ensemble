"""Auth service — flow orchestration (oauth2-auth Phase 3, T3.3).

The browser-lane orchestrator between the IdP (via the P2
:class:`~daemon.services.oauth_client.OAuthClient`), the P2 session
store (:class:`~daemon.repositories.auth_session.repository.AuthSessionRepository`)
and the P3 middleware/routes. DI-clean per [TA] Debt #3 / D3.5: this
module NEVER imports ``live_views`` — the resolver inputs (bind host/
port, host recorder) and the tier-2 fallback string are injected.

D3.5 redirect_uri chain (each tier consulted in order, first absolute
base wins)::

    auth.external_redirect_base            # tier-1 operator override
    → auth.live_view_external_base_url_fallback   # tier-2 one-shot lifespan copy
      (passed to BaseURLResolver as ITS tier-1)
    → BaseURLResolver.resolve()            # Host-capture → bind
    → None ⇒ 503 LOGIN_NOT_CONFIGURED      # never a relative mint

CSRF token derivation lives in :mod:`daemon.auth.cookie` (hash of the
session id — no schema change). Session validation is PG-only per
FR-30: an unreachable IdP never blocks an existing session.

Typed error surface (routes map to HTTP):
``LoginNotConfigured`` → 503 LOGIN_NOT_CONFIGURED ·
``IdpUnreachableError`` → 503 IDP_UNREACHABLE ·
``StateReplayedError``/``StateExpiredError`` → 400 ·
``IdpRejectedError`` → 401 IDP_REJECTED.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import logging
import re
import secrets
from typing import TYPE_CHECKING, Any, Literal

from daemon.auth.context import AUTH_METHOD_SESSION, AuthContext
from daemon.auth.cookie import SESSION_COOKIE_NAME, csrf_token_for_session
from daemon.config import AuthConfig
from daemon.repositories.auth_session.models import AuthSession, AuthState
from daemon.repositories.auth_session.repository import (
    AuthSessionRepository,
    StateConsumeOutcome,
)
from daemon.services.oauth_client import (
    DiscoveryUnreachableError,
    IdTokenInvalidError,
    JwksUnavailableError,
    OAuthClient,
    OAuthClientError,
    TokenExchangeError,
)
from daemon.services.timestamps import now_utc_iso

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from daemon.services.live_views import BaseURLResolver, HostRecorder

logger = logging.getLogger(__name__)

#: ``auth_states`` TTL — the authorization-request state lives 300 s
#: (plan T3.3; the 60 s state sweeper reaps consumed/expired rows).
STATE_TTL_SECONDS = 300

#: PKCE verifier length in BYTES before URL-safe encoding
#: (86 chars — inside the RFC 7636 §4.1 43-128 char window).
VERIFIER_BYTES = 64

#: Session id length in BYTES before URL-safe encoding (~43 chars,
#: ≈256-bit entropy per [TA] §Session Model).
SESSION_ID_BYTES = 32


# ── typed errors (routes map these to HTTP) ─────────────────────────


class AuthServiceError(Exception):
    """Base typed error — carries the stable HTTP-body ``code``."""

    code = "AUTH_SERVICE_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class LoginNotConfigured(AuthServiceError):
    """No absolute redirect base resolvable (D3.5 end-of-chain).

    503 body names the exact env vars so the operator's fix is in the
    response (runbook Enable section leads with the override).
    """

    code = "LOGIN_NOT_CONFIGURED"


class IdpUnreachableError(AuthServiceError):
    """Discovery/token-endpoint unreachable (FR-29/FR-30)."""

    code = "IDP_UNREACHABLE"


class StateReplayedError(AuthServiceError):
    """The callback ``state`` was unknown or already consumed (CAS)."""

    code = "STATE_REPLAYED"


class StateExpiredError(AuthServiceError):
    """The callback ``state`` was won by the CAS but past its TTL."""

    code = "STATE_EXPIRED"


class IdpRejectedError(AuthServiceError):
    """Token exchange failed or the ID-token failed validation."""

    code = "IDP_REJECTED"


# ── result shapes ───────────────────────────────────────────────────


class LoginRedirect:
    """What ``/api/auth/login`` hands back to the browser."""

    def __init__(self, authorize_url: str, state: str) -> None:
        self.authorize_url = authorize_url
        self.state = state


class CallbackSuccess:
    """Successful callback: the issued session + its cookie token."""

    def __init__(
        self,
        session: AuthSession,
        csrf_token: str,
        return_to: str = "/",
    ) -> None:
        self.session = session
        self.csrf_token = csrf_token
        #: The sanitized pre-login path recovered from the consumed
        #: state row (the browser bounce target).
        self.return_to = return_to

    @property
    def session_id(self) -> str:
        return self.session.id

    @property
    def cookie_name(self) -> str:
        return SESSION_COOKIE_NAME


# ── the service ─────────────────────────────────────────────────────


class AuthService:
    """Login URL build, callback handling, session validate/invalidate.

    Constructed ONCE per app when ``auth.enabled`` is true (lifespan) —
    see :func:`daemon.api.lifespan`. All repository access bridges the
    sync repository through ``asyncio.to_thread`` (the P2 sweeper
    precedent) so the event loop is never blocked on PG.
    """

    def __init__(
        self,
        *,
        config: AuthConfig,
        repository: AuthSessionRepository,
        oauth_client: OAuthClient,
        bind_host: str | None,
        bind_port: int | None,
        host_recorder: "HostRecorder | None",
        now_iso_fn: Any = now_utc_iso,
    ) -> None:
        self._config = config
        self._repository = repository
        self._oauth_client = oauth_client
        self._bind_host = bind_host
        self._bind_port = bind_port
        self._host_recorder = host_recorder
        self._now_iso_fn = now_iso_fn

    # ── redirect_uri resolution (D3.5) ──────────────────────────────

    def _build_resolver(self) -> "BaseURLResolver":
        from daemon.services.live_views import BaseURLResolver

        return BaseURLResolver(
            external_base_url=self._config.live_view_external_base_url_fallback,
            bind_host=self._bind_host,
            bind_port=self._bind_port,
            host_recorder=self._host_recorder,
        )

    def _resolve_redirect_uri(self) -> str:
        """The D3.5 chain — raises :class:`LoginNotConfigured` at None.

        The exact-URI override (``auth.redirect_uri``) wins outright;
        otherwise the base chain mints ``<base>/api/auth/callback``.
        """
        exact = self._config.redirect_uri
        if exact:
            return exact
        if self._config.external_redirect_base:
            base = self._config.external_redirect_base.rstrip("/")
            return f"{base}/api/auth/callback"
        base = self._build_resolver().resolve()
        if base is None:
            raise LoginNotConfigured(
                "login is not configured: no absolute callback base is "
                "resolvable. Set ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE or "
                "ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL (or send one request "
                "through the daemon so Host-capture learns the base)"
            )
        return f"{base}/api/auth/callback"

    # ── login (FR-4/5/6) ────────────────────────────────────────────

    async def build_login_url(self, *, return_to: str = "/") -> LoginRedirect:
        """Mint state+PKCE, persist the row, return the authorize URL.

        State: 32 URL-safe random bytes (``secrets.token_urlsafe``);
        verifier: 64 random bytes (86 chars — RFC 7636 window); challenge:
        BASE64URL(SHA256(verifier)) with padding stripped (S256 — the
        ``plain`` method is never minted, [TA] §Session Model).
        """
        state = secrets.token_urlsafe(SESSION_ID_BYTES)
        verifier = secrets.token_urlsafe(VERIFIER_BYTES)
        challenge = _s256_challenge(verifier)

        now_iso = self._now_iso_fn()
        row = AuthState(
            id=state,
            code_verifier=verifier,
            code_challenge=challenge,
            challenge_method="S256",
            redirect_to=_clamp_return_to(return_to),
            created_at=now_iso,
            expires_at=_iso_plus_seconds(now_iso, STATE_TTL_SECONDS),
            consumed_at=None,
        )
        await asyncio.to_thread(self._repository.insert_state, row)

        redirect_uri = self._resolve_redirect_uri()
        authorization_endpoint = await self._authorization_endpoint()
        query = _urlencode(
            {
                "client_id": self._config.client_id or "",
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(self._config.scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if "?" in authorization_endpoint else "?"
        return LoginRedirect(f"{authorization_endpoint}{separator}{query}", state)

    async def _authorization_endpoint(self) -> str:
        """Resolve the IdP authorization endpoint (discovery-backed).

        ``DiscoveryUnreachableError`` maps to
        :class:`IdpUnreachableError` (503 IDP_UNREACHABLE, FR-29) — the
        P2 typed error never escapes raw.
        """
        try:
            doc = await self._oauth_client.discovery()
        except DiscoveryUnreachableError as exc:
            raise IdpUnreachableError(str(exc)) from exc
        except OAuthClientError as exc:
            raise IdpUnreachableError(str(exc)) from exc
        return doc.authorization_endpoint

    # ── callback (FR-7/8/9) ─────────────────────────────────────────

    async def handle_callback(
        self,
        *,
        code: str | None,
        state: str | None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> CallbackSuccess:
        """CAS-consume the state → exchange the code → issue a session.

        Errors are the typed surface in the module docstring; the route
        maps them 1:1 onto the [REQ] callback matrix (400
        STATE_REPLAYED / STATE_EXPIRED, 401 IDP_REJECTED, 503
        IDP_UNREACHABLE).
        """
        if not state:
            raise StateReplayedError("callback missing state parameter")
        outcome = await asyncio.to_thread(
            self._repository.consume_state,
            state,
            now=self._now_iso_fn(),
            now_iso=self._now_iso_fn(),
        )
        if outcome == StateConsumeOutcome.REPLAYED:
            raise StateReplayedError("state already consumed (replay rejected)")
        if outcome == StateConsumeOutcome.NOT_FOUND:
            raise StateReplayedError("unknown state parameter")
        if outcome == StateConsumeOutcome.EXPIRED:
            raise StateExpiredError("state expired")

        if not code:
            raise IdpRejectedError("callback missing code parameter")

        # Re-derive the redirect_uri with the SAME chain used at
        # build_login_url — the IdP enforces exact registration match.
        redirect_uri = self._resolve_redirect_uri()

        state_row = await asyncio.to_thread(self._repository.get_state, state)
        verifier = state_row.code_verifier if state_row else ""
        try:
            token = await self._oauth_client.exchange_code(
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=verifier,
            )
        except TokenExchangeError as exc:
            raise IdpRejectedError(
                f"token endpoint rejected the exchange: {exc}"
            ) from exc
        except DiscoveryUnreachableError as exc:
            raise IdpUnreachableError(str(exc)) from exc
        except OAuthClientError as exc:
            raise IdpRejectedError(str(exc)) from exc

        if not token.id_token:
            raise IdpRejectedError("token endpoint returned no id_token")
        try:
            claims = await self._oauth_client.validate_id_token(token.id_token)
        except IdTokenInvalidError as exc:
            raise IdpRejectedError(f"id_token invalid ({exc.reason})") from exc
        except JwksUnavailableError as exc:
            # A missing JWKS is an IdP availability failure, not a
            # caller fault → the 503 lane (mirrors discovery mapping).
            raise IdpUnreachableError(str(exc)) from exc
        except OAuthClientError as exc:
            raise IdpRejectedError(str(exc)) from exc

        subject = str(claims.get("sub", ""))
        if not subject:
            raise IdpRejectedError("id_token missing sub claim")

        session = await self.issue_session(
            subject=subject,
            email=claims.get("email"),
            client_ip=client_ip,
            user_agent=user_agent,
        )
        return CallbackSuccess(
            session,
            csrf_token_for_session(session.id),
            return_to=_clamp_return_to(
                state_row.redirect_to if state_row else None
            ),
        )

    # ── session issue / validate / invalidate (FR-10/11/12/14) ─────

    async def issue_session(
        self,
        *,
        subject: str,
        email: str | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> AuthSession:
        """Insert a fully-populated ``auth_sessions`` row (AC-10)."""
        now_iso = self._now_iso_fn()
        ip_subnet = (
            _ip_subnet(client_ip) if self._config.fingerprint_by_ip_subnet else None
        )
        row = AuthSession(
            id=secrets.token_urlsafe(SESSION_ID_BYTES),
            subject=subject,
            email=email,
            scopes=" ".join(self._config.scopes),
            auth_method=AUTH_METHOD_SESSION,
            user_agent=user_agent,
            ip_subnet=ip_subnet,
            created_at=now_iso,
            last_used_at=now_iso,
            expires_at=_iso_plus_seconds(now_iso, self._config.session_ttl_seconds),
        )
        # Detached snapshot for the caller: the repository's commit
        # expires the inserted instance (SQLAlchemy expire-on-commit),
        # and reading an expired attribute outside its Session raises
        # DetachedInstanceError. The snapshot carries the same values
        # pre-insert, stays detached with loaded attributes, and is
        # safe to read from any context.
        snapshot = AuthSession(
            id=row.id,
            subject=row.subject,
            email=row.email,
            scopes=row.scopes,
            auth_method=row.auth_method,
            user_agent=row.user_agent,
            ip_subnet=row.ip_subnet,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            expires_at=row.expires_at,
        )
        await asyncio.to_thread(self._repository.insert_session, row)
        logger.info(
            "[AuthAudit] event=session_issued subject=%s auth_method=session "
            "ip_subnet=%s expires_at=%s",
            snapshot.subject,
            snapshot.ip_subnet or "-",
            snapshot.expires_at,
        )
        return snapshot

    async def validate_session(
        self,
        session_id: str,
        *,
        client_ip: str | None = None,
        touch: bool = True,
    ) -> AuthContext | None:
        """PG-only session validation (FR-30) → :class:`AuthContext`.

        Expiry → ``None`` (the sweeper reaps the row). When
        ``fingerprint_by_ip_subnet`` is set, a /24 (or /64) mismatch
        invalidates the session immediately and returns ``None``
        (defense: cookie replayed from a foreign network block).
        Touch is the P2 1-minute-batched ``last_used_at`` update;
        sliding renewal (when configured) extends ``expires_at``
        through the same batched seam.
        """
        row = await asyncio.to_thread(self._repository.get_session, session_id)
        if row is None:
            return None
        now_iso = self._now_iso_fn()
        if row.expires_at < now_iso:
            return None
        row_expires_at = row.expires_at
        if self._config.fingerprint_by_ip_subnet:
            if not row.ip_subnet or _ip_subnet(client_ip) != row.ip_subnet:
                logger.warning(
                    "[AuthAudit] event=session_ip_mismatch subject=%s "
                    "bound_subnet=%s seen_subnet=%s",
                    row.subject,
                    row.ip_subnet or "-",
                    _ip_subnet(client_ip) or "-",
                )
                await asyncio.to_thread(
                    self._repository.invalidate_session, session_id
                )
                return None
        if touch:
            now_iso_for_write = self._now_iso_fn()
            if self._config.sliding_renewal:
                await asyncio.to_thread(
                    self._repository.renew_sessions,
                    [session_id],
                    now_iso=now_iso_for_write,
                    ttl_seconds=int(self._config.session_ttl_seconds),
                )
                # The renewal UPDATE lands after the row read above —
                # reflect the new expiry in the returned context
                # instead of the one-step-stale stored value.
                row_expires_at = _iso_plus_seconds(
                    now_iso_for_write, int(self._config.session_ttl_seconds)
                )
            else:
                await asyncio.to_thread(
                    self._repository.touch_sessions,
                    [session_id],
                    now_iso=now_iso_for_write,
                )
        return AuthContext(
            subject=row.subject,
            scopes=_parse_scopes(row.scopes),
            auth_method=row.auth_method or AUTH_METHOD_SESSION,
            expires_at=row_expires_at,
        )

    async def invalidate_session(self, session_id: str) -> bool:
        """Logout: DELETE the session row (single-CAS delete, P2 repo)."""
        deleted = await asyncio.to_thread(
            self._repository.invalidate_session, session_id
        )
        if deleted:
            logger.info(
                "[AuthAudit] event=session_invalidated auth_method=session"
            )
        return deleted

    # ── machine lane (FR-16/17 groundwork — P4 mints via CLI) ──────

    async def verify_static_token(self, token: str) -> AuthContext | None:
        """Bearer-lane validation: SHA-256 the presented token and look
        it up hash-only in ``auth_bearer_tokens`` (P2 store).

        Full surface in P3 for the middleware lane; the MINT path is the
        P4 CLI — this method is a pure delegate to the P2 repository.
        Revoked/expired rows return ``None`` (repo contract).
        """
        id_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        row = await asyncio.to_thread(
            self._repository.get_bearer_by_hash,
            id_hash,
            now_iso=self._now_iso_fn(),
        )
        if row is None:
            return None
        if touch_is_due(row.last_used_at, self._now_iso_fn()):
            await asyncio.to_thread(
                self._repository.touch_bearer_tokens,
                [id_hash],
                now_iso=self._now_iso_fn(),
            )
        return AuthContext(
            subject=row.subject,
            scopes=_parse_scopes(row.scopes),
            auth_method="bearer",
            expires_at=row.expires_at,
        )

    # ── readiness (T3.9 / FR-30 / OD-9) ────────────────────────────

    def get_readiness_state(self) -> Literal["ok", "degraded", "unconfigured"]:
        """The FR-30 enum for the ``/readyz`` body sub-field.

        * ``"unconfigured"`` — enabled but the redirect chain is dead
          (``LoginNotConfigured`` at resolution time; FR-28/AC-28).
        * ``"degraded"`` — the IdP is unreachable (last discovery
          attempt failed): existing sessions STILL validate (PG-only);
          new logins 503 (FR-29/AC-30).
        * ``"ok"`` — default: IdP reachable (or not yet probed — the
          boot hook has not fired; sessions validate PG-only either
          way, so optimism never masks a session failure).

        NEVER influences the readiness verdict (OD-9) — the caller rides
        it as an advisory body sub-field.
        """
        try:
            self._resolve_redirect_uri()
        except LoginNotConfigured:
            return "unconfigured"
        if self._oauth_client.last_discovery_reachable() is False:
            return "degraded"
        return "ok"


# ── module helpers (pure; the class stays I/O-only) ──────────────────


def _s256_challenge(verifier: str) -> str:
    """RFC 7636 §4.2 S256: BASE64URL-ENCODE(SHA256(ASCII(verifier))),
    padding stripped."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _parse_scopes(stored: str | None) -> set[str]:
    """Scope-string → set, accepting BOTH the space-joined session
    convention (``openid profile email``) and the CSV bearer-table
    convention ([TA] §Session Model: "CSV of granted scopes"). Empty
    fragments never become scopes."""
    if not stored:
        return set()
    return {fragment for fragment in re.split(r"[,\s]+", stored.strip()) if fragment}


def _ip_subnet(client_ip: str | None) -> str | None:
    """/24 for IPv4, /64 for IPv6 (the AuthSession.ip_subnet contract)."""
    if not client_ip:
        return None
    try:
        addr = ipaddress.ip_address(client_ip.split("%")[0])
    except ValueError:
        return None
    if addr.version == 4:
        return str(ipaddress.ip_network(f"{addr}/24", strict=False).network_address) + "/24"
    return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address) + "/64"


def _clamp_return_to(return_to: str | None) -> str:
    """Sanitize the pre-login return path (D3.8 normalization shared
    with the middleware — same rules, applied to the state row)."""
    from daemon.middleware.auth import sanitize_return_to

    return sanitize_return_to(return_to or "/")


def _urlencode(params: dict[str, str]) -> str:
    from urllib.parse import urlencode

    return urlencode(params)


def _iso_plus_seconds(iso: str, seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + timedelta(seconds=seconds)).isoformat()


def touch_is_due(last_used_at: str | None, now_iso: str) -> bool:
    """True when the 1-minute touch window has elapsed (or never set)."""
    if not last_used_at:
        return True
    from datetime import datetime, timezone

    last = datetime.fromisoformat(last_used_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    now = datetime.fromisoformat(now_iso)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - last).total_seconds() >= 60


__all__ = [
    "AuthService",
    "AuthServiceError",
    "CallbackSuccess",
    "IdpRejectedError",
    "IdpUnreachableError",
    "LoginNotConfigured",
    "LoginRedirect",
    "STATE_TTL_SECONDS",
    "StateExpiredError",
    "StateReplayedError",
]
