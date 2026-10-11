"""Minimal fake-OIDC provider stub — oauth2-auth Phase 3 (G3.1 lane).

NOT the full FR-33 fake (that is P5's ``tests/fakes/fake_oidc_provider.py``
— deliberately no circular dependency). This stub provides EXACTLY what
the P3 tests need, as an ``httpx.MockTransport`` handler:

* ``GET  <issuer>/.well-known/openid-configuration`` — discovery doc
  carrying issuer/authorization_endpoint/token_endpoint/jwks_uri.
* ``POST <token_endpoint>`` — validates the bare shape of the request
  (grant_type/code/code_verifier/client_id) and returns an RS256-signed
  id_token. Failure knobs: ``fail_token=True`` (401) for the
  IDP_REJECTED leg, ``fail_code`` (wrong code) ditto.
* ``GET  <jwks_uri>`` — the matching public JWK.
* Transport-failure knob: ``fail_discovery=True`` makes every discovery
  request raise (httpx.ConnectError → the P2 backoff → 503
  IDP_UNREACHABLE leg) without real network.

The RSA keypair is generated ONCE per test process (cryptography is
already a dependency via ``pyjwt[crypto]``, P2 T2.7). Signing is RS256
with a ``kid``; JWKS exposes the public half. Tokens are minted with
``pyjwt`` — the same library the OAuthClient validates with, so the
round-trip exercises the real validation path.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.test"
_KID = "test-key-1"


def _int_to_b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class FakeOidcStub:
    """In-process OIDC stub on an ``httpx.MockTransport``."""

    def __init__(
        self,
        *,
        client_id: str = "test-app",
        fail_discovery: bool = False,
        fail_token: bool = False,
        token_status: int = 401,
    ) -> None:
        self.client_id = client_id
        self.fail_discovery = fail_discovery
        self.fail_token = fail_token
        self.token_status = token_status
        self.token_requests: list[dict[str, str]] = []
        self.discovery_requests = 0
        self._private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        public = self._private_key.public_key()
        numbers = public.public_numbers()
        self._jwk = {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": _KID,
            "n": _int_to_b64(numbers.n),
            "e": _int_to_b64(numbers.e),
        }
        self.transport = httpx.MockTransport(self._handle)

    # ── token minting (test helper side) ────────────────────────────

    def mint_id_token(
        self,
        *,
        subject: str = "user-123",
        issuer: str = ISSUER,
        audience: str | None = None,
        lifetime: int = 300,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": issuer,
            "sub": subject,
            "aud": audience or self.client_id,
            "exp": now + lifetime,
            "iat": now,
            "email": f"{subject}@example.com",
        }
        return jwt.encode(claims, self._private_key, algorithm="RS256", headers={"kid": _KID})

    # ── transport handler (OAuthClient side) ────────────────────────

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = urlsplit(str(request.url)).path
        if path.endswith("/.well-known/openid-configuration"):
            self.discovery_requests += 1
            if self.fail_discovery:
                raise httpx.ConnectError("stub: discovery unreachable", request=request)
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if path.endswith("/jwks"):
            return httpx.Response(200, json={"keys": [self._jwk]})
        if path.endswith("/token"):
            form = parse_qs(request.content.decode("utf-8"))
            flat = {k: v[0] for k, v in form.items()}
            self.token_requests.append(flat)
            if self.fail_token:
                return httpx.Response(
                    self.token_status,
                    json={"error": "invalid_grant", "error_description": "stub failure"},
                )
            if (
                flat.get("grant_type") != "authorization_code"
                or not flat.get("code")
                or not flat.get("code_verifier")
                or flat.get("client_id") != self.client_id
            ):
                return httpx.Response(400, json={"error": "invalid_request"})
            id_token = self.mint_id_token(subject="user-123")
            return httpx.Response(
                200,
                json={
                    "access_token": "at-stub",
                    "token_type": "Bearer",
                    "expires_in": 300,
                    "id_token": id_token,
                    "scope": "openid profile email",
                },
            )
        return httpx.Response(404, json={"error": "not_found"})

    # ── PKCE helper (assert-shape side) ─────────────────────────────

    @staticmethod
    def s256(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def request_to_dict(request: httpx.Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items()}


def dump_json(payload: Any) -> bytes:
    return json.dumps(payload).encode("utf-8")
