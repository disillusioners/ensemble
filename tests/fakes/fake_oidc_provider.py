"""In-process fake OIDC provider — oauth2-auth Phase 5 (T5.1 / D5.1 / FR-33).

Pure-ASGI sub-app (zero network, zero Docker; NFR-10 budget). Five routes
(router), one test RSA keypair minted at fixture setup, PKCE enforced on
``/token``, configurable failure toggles (503-all / wrong-issuer /
wrong-aud / bad-signature / wrong-kid) so every validation branch is a
table case rather than an ad-hoc patch.

Mirrors the in-repo pattern at ``tests/e2e/mock_source_server.py`` and
the lighter ``tests/helpers/fake_oidc_stub.py`` (the P3 transport stub
that pre-dates this file — they share the same JWKS / RS256 round-trip
but the stub bypasses HTTP; this provider exercises real redirect /
302 / form-POST semantics through ``httpx.ASGITransport``).

Mounting contract
-----------------

The fake is composed UNDER the daemon's ``create_app()`` at the
internal path ``/_test_oidc/`` (the prefix the lifespan never sees —
the daemon has no such route; the prefix is in this provider's
namespace only). Composing via ``Mount("/_test_oidc", app=fake_app)``
mounted on a thin wrapping ``ASGI``-app yields a single
``ASGITransport`` instance the tests can mint ``httpx.AsyncClient``
against — no real port binding, no double-stack CORS, no daemon
lifespan noise.

Failure toggles (all default OFF; pytest parametrize flips per-case):

    failure_mode              e2e effect
    ──────────────────────  ──────────────────────────────────────
    ``"503_all"``           every route returns 503 (IdP-down leg)
    ``"wrong_issuer"``       id_token carries ``iss != config.issuer``
    ``"wrong_aud"``         id_token carries ``aud != client_id``
    ``"bad_signature"``     id_token signed with a SECONDARY key (the
                             primary stays in JWKS so the client
                             resolves kid → rejects with bad signature)
    ``"wrong_kid"``         id_token carries a kid absent from JWKS
                             (forces one forced JWKS refresh → still
                             absent → 401 JWKS_UNAVAILABLE)

The provider is stateful (one in-memory ``pending_codes`` dict for the
single-use PKCE flow) — it is NOT safe for parallel requests. The
fixtures mint one provider per test, so this is the right granularity.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

logger = logging.getLogger(__name__)


# Defaults overridable per FakeOidcProvider construction.
ISSUER_DEFAULT = "https://idp.test.local"
DEFAULT_KID = "test-key-1"
SUPPORTED_FAILURE_MODES: frozenset[str] = frozenset(
    {"503_all", "wrong_issuer", "wrong_aud", "bad_signature", "wrong_kid"}
)


def _int_to_b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _s256(verifier: str) -> str:
    """SHA-256 the verifier, base64url-encode, strip padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ── Provider ──────────────────────────────────────────────────────────


@dataclass
class _PendingCode:
    """One accepted /authorize awaiting /token roundtrip."""

    code: str
    state: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    client_id: str
    issued_at: float
    # scopes the operator requested, restored on the id_token (so the
    # OAuthClient's downstream ID-token validation can sanity-check the
    # mirror).
    scopes: str = ""


@dataclass
class FakeOidcProvider:
    """In-process OIDC provider ASGI sub-app (T5.1 / D5.1).

    Constructed once per fixture; ``app`` is the ASGI callable the
    ``Mount`` wrapper attaches. ``failure_mode`` is the active failure
    knob (any of :data:`SUPPORTED_FAILURE_MODES`).

    Threading / concurrency: NOT safe. Single-thread test use only
    (the plan calls for ASGITransport + httpx.AsyncClient, which
    runs the provider on the same event loop; no shared state across
    multiple in-flight requests because each test mints its own
    provider).
    """

    issuer: str = ISSUER_DEFAULT
    client_id: str = "test-app"
    failure_mode: str = ""
    # request log (every route hit), useful for AC-29 legs that assert
    # exactly-one discovery / one JWKS refresh.
    requests: list[dict[str, Any]] = field(default_factory=list)
    # Single-use code table — one entry per /authorize roundtrip.
    _pending_codes: dict[str, _PendingCode] = field(default_factory=dict)
    # Key material: a primary (signs valid id_tokens; listed in JWKS)
    # and a decoy (used only for the bad_signature failure toggle so
    # the JWKS resolves the kid but the signature fails).
    _private_key: Any = None
    _signing_kid: str = DEFAULT_KID
    _decoy_private_key: Any = None
    _decoy_kid: str = "test-key-decoy"

    def __post_init__(self) -> None:
        if self.failure_mode and self.failure_mode not in SUPPORTED_FAILURE_MODES:
            raise ValueError(
                f"unknown failure_mode={self.failure_mode!r}; "
                f"choose one of {sorted(SUPPORTED_FAILURE_MODES)}"
            )
        # Primary key.
        self._private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        # Decoy key only minted on bad_signature — saves a 2048-bit
        # primegen on every test.
        if self.failure_mode == "bad_signature":
            self._decoy_private_key = rsa.generate_private_key(
                public_exponent=65537, key_size=2048
            )

    # ── public route → used by ASGITransport ────────────────────────

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            # Lifespan / websocket — ignore; tests never exercise these.
            return
        path = scope.get("path", "")
        method = (scope.get("method") or "GET").upper()
        # Strip the leading prefix the wrapper Mount adds (/test_oidc/...).
        # Convention: the wrapper Mount is Mount("/_test_oidc", app=self);
        # the Mount prepends "/_test_oidc" only on sub-mounts, NOT on a
        # bare ASGI app. We receive the full path the caller mounted AT,
        # so we strip ourselves.
        relative = path
        if relative.startswith("/_test_oidc"):
            relative = relative[len("/_test_oidc"):] or "/"
        if not relative.startswith("/"):
            relative = "/" + relative
        body = await _read_body(receive)
        self.requests.append({"method": method, "path": relative, "body": body})
        try:
            if self.failure_mode == "503_all":
                # Every route returns 503 — the contract is "IdP is
                # fully offline" and the OAuthClient's backoff retries
                # should exhaust without ever reaching a 200.
                await _send_json(send, 503, {"error": "idp_unavailable"})
                return
            if method == "GET" and relative == "/.well-known/openid-configuration":
                await _send_json(send, 200, self._discovery_doc())
            elif method == "GET" and relative == "/jwks":
                await _send_json(send, 200, {"keys": self._jwks()})
            elif method == "GET" and relative == "/authorize":
                await self._handle_authorize(send, scope, body)
            elif method == "POST" and relative == "/token":
                await self._handle_token(send, scope, body)
            elif method == "POST" and relative == "/logout":
                # Optional federated-logout stub — the v1 plan does not
                # exercise end_session_endpoint (FR-31 reserved), but the
                # route is part of the 5-route surface so the discovery
                # doc references it without a 404.
                await _send_json(send, 204, {})
            else:
                await _send_json(send, 404, {"error": "not_found"})
        except _ShortCircuit as short:
            await _send_json(send, short.status, short.payload)

    # ── discovery / JWKS (in-memory) ────────────────────────────────

    def _discovery_doc(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
            "end_session_endpoint": f"{self.issuer}/logout",
            # OIDC-required bonus keys (the OAuthClient only reads
            # the four required ones; presence here is for parity).
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }

    def _jwk(self, *, kid: str, public_key: Any) -> dict[str, Any]:
        numbers = public_key.public_numbers()
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": kid,
            "n": _int_to_b64(numbers.n),
            "e": _int_to_b64(numbers.e),
        }

    def _jwks(self) -> list[dict[str, Any]]:
        keys = [self._jwk(kid=self._signing_kid, public_key=self._private_key.public_key())]
        # Decoy key is ONLY listed in JWKS when bad_signature is the
        # active failure (otherwise the OAuthClient would never see it
        # and the kid-miss leg couldn't isolate the signature failure).
        if self.failure_mode == "bad_signature" and self._decoy_private_key is not None:
            keys.append(
                self._jwk(
                    kid=self._decoy_kid, public_key=self._decoy_private_key.public_key()
                )
            )
        return keys

    # ── /authorize (302 + state CAS + PKCE persistence) ────────────

    async def _handle_authorize(
        self, send: Any, scope: dict[str, Any], _body: bytes
    ) -> None:
        if self.failure_mode == "503_all":
            await _send_json(send, 503, {"error": "idp_unavailable"})
            return
        qs = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        # Conventional OIDC query keys (RFC 6749 §4.1.1 + RFC 7636 §4.2).
        client_id = (qs.get("client_id") or [""])[0]
        redirect_uri = (qs.get("redirect_uri") or [""])[0]
        response_type = (qs.get("response_type") or [""])[0]
        state = (qs.get("state") or [""])[0]
        code_challenge = (qs.get("code_challenge") or [""])[0]
        code_challenge_method = (qs.get("code_challenge_method") or [""])[0]
        scopes = (qs.get("scope") or [""])[0]
        if response_type != "code" or not state or not code_challenge:
            # RFC 6749 §4.1.2.1 — invalid_request at the IdP, never a 302.
            await _send_json(send, 400, {"error": "invalid_request"})
            return
        if client_id != self.client_id:
            await _send_json(send, 400, {"error": "invalid_client"})
            return
        code = _mint_opaque(16)
        self._pending_codes[code] = _PendingCode(
            code=code,
            state=state,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            client_id=client_id,
            issued_at=time.time(),
            scopes=scopes,
        )
        # 302 to the registered redirect_uri carrying the code + state.
        # Same browser-shaped contract as a real IdP; tests consume it
        # via ``follow_redirects=False`` and then issue the /token call.
        location = redirect_uri
        sep = "&" if "?" in location else "?"
        location = f"{location}{sep}{urlencode({'code': code, 'state': state})}"
        await _send_redirect(send, 302, location)

    # ── /token (PKCE enforcement + id_token mint) ──────────────────

    async def _handle_token(
        self, send: Any, scope: dict[str, Any], body: bytes
    ) -> None:
        if self.failure_mode == "503_all":
            await _send_json(send, 503, {"error": "idp_unavailable"})
            return
        form = parse_qs(body.decode("utf-8"))
        flat = {k: v[0] for k, v in form.items()}
        grant_type = flat.get("grant_type", "")
        code = flat.get("code", "")
        client_id = flat.get("client_id", "")
        code_verifier = flat.get("code_verifier", "")
        redirect_uri = flat.get("redirect_uri", "")
        if grant_type != "authorization_code":
            await _send_json(
                send,
                400,
                {"error": "unsupported_grant_type", "error_description": "grant_type must be authorization_code"},
            )
            return
        pending = self._pending_codes.pop(code, None)
        if pending is None:
            # Single-use CAS — replay or unknown → invalid_grant.
            await _send_json(
                send,
                400,
                {"error": "invalid_grant", "error_description": "code unknown or already consumed"},
            )
            return
        if pending.client_id != client_id or pending.redirect_uri != redirect_uri:
            await _send_json(
                send,
                400,
                {"error": "invalid_grant", "error_description": "client_id or redirect_uri mismatch"},
            )
            return
        # PKCE enforcement: verify code_verifier hashes to the stored
        # code_challenge (RFC 7636 §4.6). The plain method is intentionally
        # REJECTED at the server — we never accept it (the OAuthClient
        # only ever mints S256 challenges).
        if pending.code_challenge_method != "S256":
            await _send_json(
                send,
                400,
                {"error": "invalid_grant", "error_description": "code_challenge_method must be S256"},
            )
            return
        computed = _s256(code_verifier)
        if not hmac.compare_digest(computed, pending.code_challenge):
            await _send_json(
                send,
                400,
                {"error": "invalid_grant", "error_description": "code_verifier does not match challenge"},
            )
            return
        # Mint the id_token — failure knobs mutate the JOSE body or
        # signing key without breaking JWKS round-trip:
        id_token = self._mint_id_token(subject=pending.scopes or "user-123")
        await _send_json(
            send,
            200,
            {
                "access_token": "at-fake-" + code[:8],
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": id_token,
                "scope": pending.scopes or "openid profile email",
            },
        )

    # ── id_token mint (test helper side; callable by tests too) ─────

    def _mint_id_token(self, *, subject: str = "user-123") -> str:
        """Mint a JWT whose claims/header respect the active failure_mode.

        Used by /token; tests can call this directly when they want a
        token without exercising the full /authorize + /token dance.
        """
        now = int(time.time())
        iss = self.issuer
        aud = self.client_id
        # wrong_issuer: sign with a payload whose `iss` does NOT match
        # the discovery doc. The OAuthClient's normalize-then-compare
        # catches the mismatch.
        if self.failure_mode == "wrong_issuer":
            iss = self.issuer + ".attacker"
        if self.failure_mode == "wrong_aud":
            aud = "different-client-id"
        # wrong_kid: use a kid absent from JWKS (forces JWKS refresh,
        # then 401 JWKS_UNAVAILABLE at the OAuthClient).
        if self.failure_mode == "wrong_kid":
            kid = "missing-from-jwks"
            private_key = self._private_key
        elif self.failure_mode == "bad_signature" and self._decoy_private_key is not None:
            # Sign with the decoy key but advertise the canonical kid —
            # JWKS resolves the kid to the PRIMARY public key, the
            # signature was made by the decoy → bad_signature.
            kid = self._signing_kid
            private_key = self._decoy_private_key
        else:
            kid = self._signing_kid
            private_key = self._private_key
        claims = {
            "iss": iss,
            "sub": subject,
            "aud": aud,
            "exp": now + 300,
            "iat": now,
            "email": f"{subject}@example.com",
        }
        return jwt.encode(
            claims,
            private_key,
            algorithm="RS256",
            headers={"kid": kid},
        )

    def mint_id_token(self, **kw: Any) -> str:
        """Public mint helper — used by tests that want a free-standing
        token without exercising the flow (e.g. bearer-lane contract
        legs)."""
        return self._mint_id_token(**kw)


# ── ASGI helpers (send_json / send_redirect / read_body / ShortCircuit) ──


class _ShortCircuit(Exception):
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self.payload = payload


async def _read_body(receive: Any) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        body = message.get("body", b"")
        chunks.append(body)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


async def _send_json(send: Any, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_redirect(send: Any, status: int, location: str) -> None:
    # The redirect body is intentionally empty — a 302 with body bytes
    # would be served, but RFC 7231 §6.4.3 says the body SHOULD be
    # omitted. Mirrors how Keycloak + Dex render the response.
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"location", location.encode("latin-1")),
                (b"content-length", b"0"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b""})


def _mint_opaque(nbytes: int) -> str:
    return base64.urlsafe_b64encode(
        hashlib.sha256(str(time.time_ns()).encode()).digest()[:nbytes]
    ).rstrip(b"=").decode("ascii")


__all__ = [
    "DEFAULT_KID",
    "ISSUER_DEFAULT",
    "FakeOidcProvider",
    "SUPPORTED_FAILURE_MODES",
]


# ASGI app factory for ``uvicorn tests.fakes.fake_oidc_provider:app
# --factory`` — instantiates the provider with sensible defaults. The
# boot-e2e-auth-daemon.sh script invokes this when --fake-idp is set.
def app() -> FakeOidcProvider:  # noqa: D401 — uvicorn factory name
    """ASGI factory: returns a fresh FakeOidcProvider instance.

    The ``--factory`` flag makes uvicorn call this on app startup;
    the returned object is the ASGI callable.
    """
    return FakeOidcProvider()