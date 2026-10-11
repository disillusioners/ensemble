"""OAuthClient JWKS + ID-token validation tests — Phase 2 (T2.7 / G2.1).

Real RSA-2048 keys (``cryptography``, already a project dep) sign real
JWTs; the JWKS endpoint is an in-process ``httpx.MockTransport`` — no
network, no sleeps (sleeper seam refuses any call).

Covers [phase2-plan §4 / AC-9]: kid hit validates; kid miss triggers
exactly ONE refetch (fake counts); second miss → ``JWKS_UNAVAILABLE``;
wrong ``iss``/``aud``/``exp``/``nbf``/signature each reject.
"""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from daemon.config import AuthConfig
from daemon.services.oauth_client import (
    IdTokenInvalidError,
    JwksUnavailableError,
    OAuthClient,
)

_KID_PRIMARY = "test-key-1"
_KID_SECONDARY = "test-key-2"


def _rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


_KEY_PRIMARY = _rsa_key()
_KEY_SECONDARY = _rsa_key()

_PRIMARY_JWK: dict = jwt.algorithms.RSAAlgorithm.to_jwk(
    _KEY_PRIMARY.public_key(), as_dict=True
)
_PRIMARY_JWK["kid"] = _KID_PRIMARY
_PRIMARY_JWK["alg"] = "RS256"
_PRIMARY_JWK["use"] = "sig"


def _config(**overrides) -> AuthConfig:
    kwargs = dict(
        enabled=True,
        client_id="my-app",
        client_secret=SecretStr("s3cret"),
        issuer="https://idp.example.com",
        allowed_origins=["https://ensemble.example.com"],
        clock_skew_seconds=60,
    )
    kwargs.update(overrides)
    return AuthConfig(**kwargs)


class _JwksFake:
    """In-process JWKS endpoint + fetch counter (no network)."""

    def __init__(self, keys: list[dict], *, fail_all: bool = False):
        self.keys = keys
        self.fail_all = fail_all
        self.jwks_fetch_count = 0
        self.discovery_fetch_count = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ".well-known" in url:
            self.discovery_fetch_count += 1
            return httpx.Response(
                200,
                json={
                    "issuer": "https://idp.example.com",
                    "authorization_endpoint": "https://idp.example.com/authorize",
                    "token_endpoint": "https://idp.example.com/token",
                    "jwks_uri": "https://idp.example.com/jwks",
                },
            )
        self.jwks_fetch_count += 1
        if self.fail_all:
            return httpx.Response(503, text="jwks down")
        return httpx.Response(200, json={"keys": self.keys})


def _client(config: AuthConfig, fake: _JwksFake) -> OAuthClient:
    async def no_sleep(delay):  # pragma: no cover — any call fails the test
        raise AssertionError(f"unexpected sleep {delay}")

    return OAuthClient(
        config,
        transport=httpx.MockTransport(fake.handler),
        sleeper=no_sleep,
        monotonic=time.monotonic,
    )


def _mint(claims: dict, key=_KEY_PRIMARY, kid: str | None = _KID_PRIMARY) -> str:
    headers = {"kid": kid} if kid else None
    return jwt.encode(claims, key, algorithm="RS256", headers=headers)


def _claims(**overrides) -> dict:
    now = int(time.time())
    base = {
        "iss": "https://idp.example.com",
        "sub": "user-1",
        "aud": "my-app",
        "exp": now + 600,
        "iat": now,
    }
    base.update(overrides)
    return base


class TestKidHitValidates:
    async def test_valid_token_returns_claims(self):
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        claims = await client.validate_id_token(_mint(_claims()))
        assert claims["sub"] == "user-1"
        assert fake.jwks_fetch_count == 1  # lazy first fetch, then cached

    async def test_second_validation_uses_cached_jwks(self):
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        await client.validate_id_token(_mint(_claims(sub="a")))
        await client.validate_id_token(_mint(_claims(sub="b")))
        assert fake.jwks_fetch_count == 1  # process-lifetime cache


class TestKidMissSingleRefresh:
    async def test_kid_miss_triggers_exactly_one_refetch_then_success(self):
        # First JWKS response carries NO usable kid; the forced refresh
        # DOES — the token validates after exactly ONE extra fetch.
        fake = _JwksFake([])
        discovery_handler = fake.handler

        def flip(request: httpx.Request) -> httpx.Response:
            if "/jwks" not in str(request.url):
                return discovery_handler(request)  # discovery doc path
            fake.jwks_fetch_count += 1
            if fake.jwks_fetch_count == 1:
                return httpx.Response(200, json={"keys": []})  # kid miss
            return httpx.Response(200, json={"keys": [dict(_PRIMARY_JWK)]})

        fake.handler = flip
        client = _client(_config(), fake)
        claims = await client.validate_id_token(_mint(_claims()))
        assert claims["sub"] == "user-1"
        # discovery(1) + jwks(1: miss) + jwks(2: forced refresh) — the
        # forced refresh happened EXACTLY once (cached thereafter).
        assert fake.jwks_fetch_count == 2
        # A subsequent validation rides the refreshed cache — no third
        # JWKS fetch.
        await client.validate_id_token(_mint(_claims(sub="user-2")))
        assert fake.jwks_fetch_count == 2

    async def test_kid_miss_twice_is_jwks_unavailable(self):
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        with pytest.raises(JwksUnavailableError) as excinfo:
            await client.validate_id_token(_mint(_claims(), kid="unknown-kid"))
        assert excinfo.value.code == "JWKS_UNAVAILABLE"
        # Cache fetch (1) + forced refresh (2) — and NEVER a third.
        assert fake.jwks_fetch_count == 2


class TestJwksFetchFailures:
    async def test_unreachable_jwks_is_typed(self):
        fake = _JwksFake([], fail_all=True)
        client = _client(_config(), fake)
        with pytest.raises(JwksUnavailableError):
            await client.validate_id_token(_mint(_claims()))


class TestClaimRejections:
    async def test_wrong_iss_rejected_constant_time_path(self):
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        with pytest.raises(IdTokenInvalidError) as excinfo:
            await client.validate_id_token(
                _mint(_claims(iss="https://evil.example.com"))
            )
        assert excinfo.value.reason == "bad_iss"

    async def test_trailing_slash_iss_still_matches(self):
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        claims = await client.validate_id_token(
            _mint(_claims(iss="https://idp.example.com/"))
        )
        assert claims["sub"] == "user-1"  # normalized compare, no false reject

    async def test_wrong_aud_rejected_strict(self):
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        with pytest.raises(IdTokenInvalidError) as excinfo:
            await client.validate_id_token(_mint(_claims(aud="other-client")))
        assert excinfo.value.reason == "bad_aud"

    async def test_aud_multi_element_list_rejected_strict(self):
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        with pytest.raises(IdTokenInvalidError) as excinfo:
            await client.validate_id_token(
                _mint(_claims(aud=["my-app", "someone-else"]))
            )
        assert excinfo.value.reason == "bad_aud"

    async def test_expired_beyond_skew_rejected(self):
        now = int(time.time())
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        with pytest.raises(IdTokenInvalidError) as excinfo:
            await client.validate_id_token(
                _mint(_claims(exp=now - 3600))  # 1h past — far beyond 60 s skew
            )
        assert excinfo.value.reason == "expired"

    async def test_nbf_beyond_skew_rejected(self):
        now = int(time.time())
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        with pytest.raises(IdTokenInvalidError) as excinfo:
            await client.validate_id_token(
                _mint(_claims(nbf=now + 3600))  # 1h ahead — far beyond skew
            )
        assert excinfo.value.reason == "not_yet_valid"

    async def test_wrong_signature_rejected(self):
        """Token signed by a DIFFERENT key but carrying the primary kid —
        the JWKS key verifies the signature and rejects."""
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        with pytest.raises(IdTokenInvalidError) as excinfo:
            await client.validate_id_token(
                _mint(_claims(), key=_KEY_SECONDARY, kid=_KID_PRIMARY)
            )
        assert excinfo.value.reason == "bad_signature"

    async def test_non_rs256_alg_rejected(self):
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        hs_token = jwt.encode(
            _claims(), "symmetric-secret", algorithm="HS256",
            headers={"kid": _KID_PRIMARY},
        )
        with pytest.raises(IdTokenInvalidError) as excinfo:
            await client.validate_id_token(hs_token)
        assert excinfo.value.reason == "bad_signature"

    async def test_missing_required_claim_rejected(self):
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        claims = _claims()
        claims.pop("sub")
        with pytest.raises(IdTokenInvalidError) as excinfo:
            await client.validate_id_token(_mint(claims))
        assert excinfo.value.reason == "missing_claim"

    async def test_garbage_token_rejected_malformed(self):
        fake = _JwksFake([dict(_PRIMARY_JWK)])
        client = _client(_config(), fake)
        with pytest.raises(IdTokenInvalidError) as excinfo:
            await client.validate_id_token("not-a-jwt")
        assert excinfo.value.reason == "malformed"
