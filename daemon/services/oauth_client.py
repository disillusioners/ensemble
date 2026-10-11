"""OAuth2/OIDC client service — oauth2-auth Phase 2 (T2.5–T2.7).

The provider-facing half of the auth subsystem: OIDC discovery, code
exchange, JWKS fetch + ID-token validation. Constructed ONLY when
``auth.enabled`` is true (T2.8 / FR-2 extended to the service layer —
OFF constructs nothing).

Design contracts
----------------
* **httpx lane discipline (D2.5)**: a fresh ``httpx.AsyncClient`` is
  constructed per HTTP call (the ``plane_http_client.py`` precedent —
  no module-level singleton, no new persistent pool, sidesteps the
  event-loop-binding hazard). Tests inject an ``httpx.MockTransport``
  via the ``transport`` constructor seam — NEVER real network.
* **Never-raises at the boundary**: every failure mode surfaces as a
  typed :class:`OAuthClientError` subclass — raw ``httpx``/``jwt``
  exceptions never escape a public method. The lifespan caller (P2
  wiring) and the P3 callback each catch the one base type.
  Discovery/JWKS unreachability is WARN-not-fatal at the lifespan
  (NFR-7 / C4) — this module NEVER adds a second exit-78 site; the P1
  ``_boot_auth_config_preflight`` remains the single exit-78 owner.
* **Backoff seam**: retries wait via an injected ``sleeper`` coroutine
  (default ``asyncio.sleep``) — tests observe the 1s/4s/16s schedule
  with zero real sleeps.
* **Cache seam**: TTL caches (discovery ``discovery_cache_ttl_seconds``,
  JWKS ``jwks_cache_ttl_seconds`` — both process-lifetime) read the
  clock via an injected ``monotonic`` callable.
* **Manual-endpoint overrides**: optional constructor kwargs
  (``authorization_endpoint_override`` / ``token_endpoint_override`` /
  ``jwks_uri_override``). All three set ⇒ discovery returns a manual
  doc with NO fetch ([REQ] A1 fallback; OQ-REQ-10). Partial overrides
  win over the discovery doc's corresponding fields post-fetch. NOTE
  (P1 surface deviation, named): the frozen P1 ``AuthConfig`` 23-env-var
  surface carries NO endpoint-override fields, so the overrides are a
  constructor-level DI seam here; P3 lifespan wiring decides whether to
  plumb them from config.
* **Security posture**: HTTPS-only URLs (``allow_insecure_cookie`` is
  the sole opt-out, consistent with the P1 config gate), redirects
  refused (``follow_redirects=False`` — JWKS-poisoning defense, D2.6),
  constant-time issuer comparison (``hmac.compare_digest`` on
  trailing-slash-normalized values), strict ``aud`` (exactly
  ``client_id``), ``exp``/``nbf`` via the ``clock_skew_seconds``
  leeway, signature verification pinned to ``RS256`` (algorithm-
  confusion defense), ``kid``-miss triggers EXACTLY ONE forced JWKS
  refresh then :class:`JwksUnavailableError`.
* **Secret hygiene**: ``client_secret`` reaches only the token-exchange
  form body (``SecretStr`` unwrapped at the last moment); it never
  appears in logs or exception messages. Provider response bodies are
  logged truncated to 256 chars on failure — provider-side bodies
  cannot contain our secret, and we never log the request.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
from pydantic import BaseModel

from daemon.config import AuthConfig

logger = logging.getLogger(__name__)

#: Per-request HTTP timeout (plan T2.5: discovery 5 s; the same window
#: is used for token exchange and JWKS fetch).
REQUEST_TIMEOUT_SECONDS = 5.0

#: Backoff schedule between discovery attempts (plan T2.5 / FR-29).
DISCOVERY_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 4.0, 16.0)

#: Max provider-body characters emitted in a WARNING log (T2.6).
_ERROR_BODY_LOG_MAX_CHARS = 256

#: Required discovery-document fields ([REQ] A1 OIDC discovery contract).
_REQUIRED_DISCOVERY_FIELDS = (
    "issuer",
    "authorization_endpoint",
    "token_endpoint",
    "jwks_uri",
)


class OAuthClientError(Exception):
    """Base typed error — the ONLY exception type escaping this module.

    ``code`` is a stable string enum for callers (P3 HTTP mapping):
    ``DISCOVERY_UNREACHABLE`` | ``INVALID_DISCOVERY_DOC`` |
    ``DISCOVERY_ISSUER_MISMATCH`` | ``INSECURE_URL_REJECTED`` |
    ``TOKEN_EXCHANGE_FAILED`` | ``JWKS_UNAVAILABLE`` |
    ``ID_TOKEN_INVALID``.
    """

    code = "OAUTH_CLIENT_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class DiscoveryUnreachableError(OAuthClientError):
    """Discovery fetch failed after the full backoff schedule."""

    code = "DISCOVERY_UNREACHABLE"


class JwksUnavailableError(OAuthClientError):
    """JWKS fetch failed, or the token ``kid`` stayed unknown after the
    single forced refresh (D2.6 → 401 JWKS_UNAVAILABLE at P3)."""

    code = "JWKS_UNAVAILABLE"


class TokenExchangeError(OAuthClientError):
    """Token endpoint returned a non-2xx (or an unusable 2xx body)."""

    code = "TOKEN_EXCHANGE_FAILED"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.status_code = status_code


class IdTokenInvalidError(OAuthClientError):
    """ID-token failed validation (iss/aud/exp/nbf/signature/shape).

    ``reason`` is a stable token (``bad_iss`` | ``bad_aud`` |
    ``expired`` | ``not_yet_valid`` | ``bad_signature`` | ``malformed``
    | ``missing_claim``) for the P3 mapping; exception MESSAGES never
    carry token material.
    """

    code = "ID_TOKEN_INVALID"

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class DiscoveryDoc(BaseModel):
    """Normalized discovery document (or manual-override equivalent)."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    #: True when the doc came from manual endpoint overrides (no fetch).
    manual: bool = False
    raw: dict[str, Any] | None = None


class TokenResponse(BaseModel):
    """Normalized token-endpoint success payload."""

    access_token: str
    token_type: str | None = None
    expires_in: int | None = None
    id_token: str | None = None
    refresh_token: str | None = None
    scope: str | None = None
    raw: dict[str, Any] | None = None


def _normalize_issuer(url: str) -> str:
    """Trailing-slash-normalize an issuer/URL for comparison."""
    return url.rstrip("/")


def _is_https(url: str) -> bool:
    return urlsplit(url).scheme.lower() == "https"


class OAuthClient:
    """OIDC discovery / code-exchange / JWKS / ID-token validation.

    See the module docstring for the contract set. Constructed only
    when ``auth.enabled`` is true.
    """

    def __init__(
        self,
        config: AuthConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        authorization_endpoint_override: str | None = None,
        token_endpoint_override: str | None = None,
        jwks_uri_override: str | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._authorization_endpoint_override = authorization_endpoint_override
        self._token_endpoint_override = token_endpoint_override
        self._jwks_uri_override = jwks_uri_override

        # TTL caches (process-lifetime; invalidated on persistent failure)
        self._discovery_cache: DiscoveryDoc | None = None
        self._discovery_cached_at: float | None = None
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_cached_at: float | None = None
        # IdP-reachability signal for the readiness composite (P3 T3.9 /
        # FR-30): tri-state — None = never attempted, True = last
        # discovery attempt succeeded, False = last attempt failed after
        # the full backoff. Bookkeeping only; NEVER gates a request path
        # (the enforcing boundaries stay at the fetch/validate sites).
        self._last_discovery_attempted: bool = False
        self._last_discovery_reachable: bool | None = None

    # ── internals ───────────────────────────────────────────────────

    def _insecure_allowed(self) -> bool:
        return bool(self._config.allow_insecure_cookie)

    def _require_url_scheme(self, url: str, *, what: str) -> None:
        """HTTPS-only enforcement (``allow_insecure_cookie`` opt-out)."""
        if not _is_https(url) and not self._insecure_allowed():
            raise OAuthClientError(
                f"{what} must be https:// (got {urlsplit(url).scheme!r}); "
                "set auth.allow_insecure_cookie=true for LAN-only http IdPs",
                code="INSECURE_URL_REJECTED",
            )

    def _new_client(self) -> httpx.AsyncClient:
        """Fresh per-call client (plane_http_client precedent — no
        persistent pool, no event-loop binding). Redirects are refused
        at the client level (D2.6)."""
        return httpx.AsyncClient(
            transport=self._transport,
            follow_redirects=False,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    @property
    def _discovery_ttl(self) -> int:
        return int(self._config.discovery_cache_ttl_seconds)

    @property
    def _jwks_ttl(self) -> int:
        return int(self._config.jwks_cache_ttl_seconds)

    def _expected_issuer(self) -> str:
        issuer = self._config.issuer
        return _normalize_issuer(str(issuer)) if issuer else ""

    # ── discovery (T2.5) ────────────────────────────────────────────

    def last_discovery_reachable(self) -> bool | None:
        """Tri-state IdP reachability for the readiness composite (T3.9).

        ``None`` = no discovery attempt made yet (the sweeper boot hook
        has not fired); ``True`` = the most recent attempt succeeded
        (manual-override docs count — no fetch needed means nothing to
        be unreachable); ``False`` = the most recent attempt exhausted
        the backoff schedule. Pure read — never fetches.
        """
        return self._last_discovery_reachable

    def _manual_overrides_complete(self) -> bool:
        return all(
            (
                self._authorization_endpoint_override,
                self._token_endpoint_override,
                self._jwks_uri_override,
            )
        )

    def _manual_doc(self) -> DiscoveryDoc:
        return DiscoveryDoc(
            issuer=self._expected_issuer(),
            authorization_endpoint=str(self._authorization_endpoint_override),
            token_endpoint=str(self._token_endpoint_override),
            jwks_uri=str(self._jwks_uri_override),
            manual=True,
        )

    async def discovery(self, *, force: bool = False) -> DiscoveryDoc:
        """Resolve the OIDC discovery document (TTL-cached).

        Order: manual overrides (no fetch) → TTL cache → network fetch
        (5 s timeout, 1s/4s/16s backoff on transport errors and 5xx;
        4xx fails fast) → typed :class:`DiscoveryUnreachableError` with
        the cache invalidated. A post-fetch issuer mismatch is a WARN
        (§6.3 row-11 runtime half) — the doc proceeds; the P3 ID-token
        ``iss`` check is the enforcing boundary.
        """
        if self._manual_overrides_complete():
            # Manual-override posture: no network fetch exists, so
            # "reachable" is vacuously true (readiness 'ok' semantics).
            self._last_discovery_attempted = True
            self._last_discovery_reachable = True
            return self._manual_doc()

        now = self._monotonic()
        if (
            not force
            and self._discovery_cache is not None
            and self._discovery_cached_at is not None
            and (now - self._discovery_cached_at) < self._discovery_ttl
        ):
            return self._discovery_cache

        url = (
            str(self._config.discovery_url)
            if self._config.discovery_url
            else _normalize_issuer(str(self._config.issuer))
            + "/.well-known/openid-configuration"
        )
        self._require_url_scheme(url, what="auth discovery url")

        doc = await self._fetch_discovery_with_backoff(url)

        self._discovery_cache = doc
        self._discovery_cached_at = self._monotonic()
        return doc

    async def _fetch_discovery_with_backoff(self, url: str) -> DiscoveryDoc:
        last_error: str = "no attempts made"
        for attempt in range(1 + len(DISCOVERY_BACKOFF_SECONDS)):
            if attempt > 0:
                await self._sleeper(DISCOVERY_BACKOFF_SECONDS[attempt - 1])
            try:
                async with self._new_client() as client:
                    response = await client.get(url)
            except httpx.HTTPError as exc:
                last_error = f"transport error: {type(exc).__name__}"
                logger.warning(
                    "[OAuthClient] discovery attempt %d failed: %s",
                    attempt + 1,
                    last_error,
                )
                continue
            if response.status_code >= 500:
                last_error = f"status {response.status_code}"
                logger.warning(
                    "[OAuthClient] discovery attempt %d failed: %s",
                    attempt + 1,
                    last_error,
                )
                continue
            if response.status_code != 200:
                # 4xx — fail fast (a retry cannot fix a bad URL)
                last_error = f"status {response.status_code}"
                break
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError):
                last_error = "non-JSON discovery body"
                continue
            if not isinstance(payload, dict) or any(
                not payload.get(field) for field in _REQUIRED_DISCOVERY_FIELDS
            ):
                last_error = (
                    "discovery doc missing required fields "
                    f"{_REQUIRED_DISCOVERY_FIELDS}"
                )
                continue

            doc_issuer = _normalize_issuer(str(payload.get("issuer", "")))
            expected = self._expected_issuer()
            # Constant-time comparison on normalized issuers.
            if not hmac.compare_digest(doc_issuer, expected):
                # WARN-and-proceed per NFR-7/C4 (§6.3 row-11 runtime
                # half): the config-time gate already pinned the
                # discovery URL's origin; the enforcing iss boundary
                # is the ID-token check. NEVER fatal at lifespan.
                logger.warning(
                    "[OAuthClient] discovery issuer mismatch: doc issuer "
                    "does not match auth.issuer (doc=%r expected=%r) — "
                    "proceeding with WARN; ID-token iss validation "
                    "remains enforcing",
                    doc_issuer,
                    expected,
                )
            self._last_discovery_attempted = True
            self._last_discovery_reachable = True
            return DiscoveryDoc(
                issuer=doc_issuer,
                authorization_endpoint=str(payload["authorization_endpoint"]),
                token_endpoint=str(payload["token_endpoint"]),
                jwks_uri=str(payload["jwks_uri"]),
                raw=payload,
            )

        # Persistent failure: invalidate any stale cache, raise typed.
        self._discovery_cache = None
        self._discovery_cached_at = None
        self._last_discovery_attempted = True
        self._last_discovery_reachable = False
        raise DiscoveryUnreachableError(
            f"OIDC discovery unreachable after backoff ({last_error}): {url}"
        )

    async def _resolve_doc(self) -> DiscoveryDoc:
        """Doc for endpoint resolution: manual-first, else discovery()."""
        if self._manual_overrides_complete():
            return self._manual_doc()
        return await self.discovery()

    # ── code exchange (T2.6) ────────────────────────────────────────

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> TokenResponse:
        """POST the authorization code to the token endpoint (T2.6).

        Form-body POST incl. ``code_verifier`` (PKCE). Non-2xx ⇒
        WARNING with the provider body truncated to 256 chars, then
        typed :class:`TokenExchangeError`. The client secret lives only
        in the request body — never in logs, messages, or reprs.
        """
        doc = await self._resolve_doc()
        token_endpoint = (
            self._token_endpoint_override or doc.token_endpoint
        )
        self._require_url_scheme(token_endpoint, what="token endpoint")

        form: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._config.client_id or "",
            "code_verifier": code_verifier,
        }
        secret = self._config.client_secret
        if secret is not None:
            form["client_secret"] = secret.get_secret_value()

        try:
            async with self._new_client() as client:
                response = await client.post(
                    token_endpoint,
                    data=form,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise TokenExchangeError(
                f"token endpoint transport error: {type(exc).__name__}"
            ) from exc

        if response.status_code != 200:
            body = response.text[:_ERROR_BODY_LOG_MAX_CHARS]
            logger.warning(
                "[OAuthClient] token exchange failed: status=%s body(≤%d)=%r "
                "(request form never logged)",
                response.status_code,
                _ERROR_BODY_LOG_MAX_CHARS,
                body,
            )
            raise TokenExchangeError(
                f"token endpoint returned {response.status_code}",
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise TokenExchangeError("token endpoint returned non-JSON body") from exc
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise TokenExchangeError(
                "token endpoint response missing access_token",
                status_code=response.status_code,
            )
        return TokenResponse(
            access_token=str(payload["access_token"]),
            token_type=payload.get("token_type"),
            expires_in=payload.get("expires_in"),
            id_token=payload.get("id_token"),
            refresh_token=payload.get("refresh_token"),
            scope=payload.get("scope"),
            raw=payload,
        )

    # ── JWKS (T2.7) ─────────────────────────────────────────────────

    def _resolve_jwks_uri(self, doc: DiscoveryDoc | None) -> str:
        if self._jwks_uri_override:
            return str(self._jwks_uri_override)
        if doc is None:
            raise JwksUnavailableError(
                "no jwks_uri available (no override and no discovery doc)"
            )
        return doc.jwks_uri

    async def fetch_jwks(self, *, force: bool = False) -> dict[str, Any]:
        """Fetch (and cache) the IdP JWKS (T2.7, D2.6).

        Lazy first fetch; process-lifetime TTL cache; HTTPS-only;
        redirects refused. Persistent failure invalidates the cache and
        raises :class:`JwksUnavailableError`.
        """
        now = self._monotonic()
        if (
            not force
            and self._jwks_cache is not None
            and self._jwks_cached_at is not None
            and (now - self._jwks_cached_at) < self._jwks_ttl
        ):
            return self._jwks_cache

        doc: DiscoveryDoc | None = None
        if not self._jwks_uri_override:
            doc = await self._resolve_doc()
        jwks_uri = self._resolve_jwks_uri(doc)
        self._require_url_scheme(jwks_uri, what="jwks_uri")

        try:
            async with self._new_client() as client:
                response = await client.get(jwks_uri)
        except httpx.HTTPError as exc:
            self._jwks_cache = None
            self._jwks_cached_at = None
            raise JwksUnavailableError(
                f"jwks fetch transport error: {type(exc).__name__}"
            ) from exc

        if response.status_code != 200:
            self._jwks_cache = None
            self._jwks_cached_at = None
            body = response.text[:_ERROR_BODY_LOG_MAX_CHARS]
            logger.warning(
                "[OAuthClient] jwks fetch failed: status=%s body(≤%d)=%r",
                response.status_code,
                _ERROR_BODY_LOG_MAX_CHARS,
                body,
            )
            raise JwksUnavailableError(
                f"jwks endpoint returned {response.status_code}"
            )

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            self._jwks_cache = None
            self._jwks_cached_at = None
            raise JwksUnavailableError("jwks endpoint returned non-JSON body") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
            self._jwks_cache = None
            self._jwks_cached_at = None
            raise JwksUnavailableError("jwks payload missing 'keys' list")

        self._jwks_cache = payload
        self._jwks_cached_at = self._monotonic()
        return payload

    def _find_key(self, jwks: dict[str, Any], kid: str | None) -> dict[str, Any] | None:
        for key in jwks.get("keys", []):
            if isinstance(key, dict) and key.get("kid") == kid:
                return key
        return None

    # ── ID-token validation (T2.7) ──────────────────────────────────

    async def validate_id_token(self, jwt_str: str) -> dict[str, Any]:
        """Validate an OIDC ID-token and return its claims (T2.7).

        Verification order: ``kid`` lookup (cache → ONE forced refresh →
        :class:`JwksUnavailableError`), RS256 signature (algorithm
        pinned), ``exp``/``nbf`` with the ``clock_skew_seconds`` leeway
        (pyjwt), then constant-time ``iss`` and strict ``aud`` (exact
        match against ``client_id``). Failures raise typed
        :class:`IdTokenInvalidError` with a stable ``reason``; exception
        messages never carry token material.
        """
        try:
            header = jwt.get_unverified_header(jwt_str)
        except jwt.PyJWTError as exc:
            raise IdTokenInvalidError("malformed", f"unparseable token header: {type(exc).__name__}") from exc
        kid = header.get("kid")
        if header.get("alg") != "RS256":
            raise IdTokenInvalidError(
                "bad_signature", f"unexpected alg {header.get('alg')!r} (RS256 only)"
            )

        jwks = await self.fetch_jwks()
        jwk = self._find_key(jwks, kid)
        if jwk is None:
            # kid miss → EXACTLY ONE forced refresh (D2.6), then typed.
            jwks = await self.fetch_jwks(force=True)
            jwk = self._find_key(jwks, kid)
            if jwk is None:
                raise JwksUnavailableError(
                    f"kid {kid!r} not found in JWKS after forced refresh"
                )

        try:
            pyjwk = jwt.PyJWK.from_dict(jwk)
        except (jwt.PyJWTError, ValueError) as exc:
            raise IdTokenInvalidError(
                "bad_signature", f"unusable JWK for kid {kid!r}: {type(exc).__name__}"
            ) from exc

        try:
            claims: dict[str, Any] = jwt.decode(
                jwt_str,
                key=pyjwk.key,
                algorithms=["RS256"],
                leeway=int(self._config.clock_skew_seconds),
                options={
                    # iss/aud verified MANUALLY below (constant-time /
                    # strict semantics beyond pyjwt's).
                    "verify_iss": False,
                    "verify_aud": False,
                    "require": ["iss", "sub", "aud", "exp"],
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise IdTokenInvalidError("expired", "token signature expired") from exc
        except jwt.ImmatureSignatureError as exc:
            raise IdTokenInvalidError("not_yet_valid", "token not yet valid (nbf)") from exc
        except jwt.InvalidSignatureError as exc:
            raise IdTokenInvalidError("bad_signature", "token signature invalid") from exc
        except jwt.MissingRequiredClaimError as exc:
            raise IdTokenInvalidError("missing_claim", f"missing required claim(s): {exc}") from exc
        except jwt.PyJWTError as exc:
            raise IdTokenInvalidError(
                "bad_signature", f"token validation failed: {type(exc).__name__}"
            ) from exc

        # Constant-time issuer compare (normalized).
        expected = self._expected_issuer()
        token_iss = _normalize_issuer(str(claims.get("iss", "")))
        if not expected or not hmac.compare_digest(token_iss, expected):
            raise IdTokenInvalidError(
                "bad_iss", "token issuer does not match auth.issuer"
            )

        # Strict aud: exactly client_id (string or single-element list).
        expected_aud = self._config.client_id or ""
        aud = claims.get("aud")
        aud_matches = (
            aud == expected_aud
            if isinstance(aud, str)
            else isinstance(aud, list) and len(aud) == 1 and aud[0] == expected_aud
        )
        if not expected_aud or not aud_matches:
            raise IdTokenInvalidError("bad_aud", "token audience mismatch")

        return claims


__all__ = [
    "DiscoveryDoc",
    "DiscoveryUnreachableError",
    "IdTokenInvalidError",
    "JwksUnavailableError",
    "OAuthClient",
    "OAuthClientError",
    "TokenExchangeError",
    "TokenResponse",
]
