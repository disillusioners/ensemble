"""OAuthClient.discovery() tests — oauth2-auth Phase 2 (T2.5 / G2.1).

All HTTP via ``httpx.MockTransport`` — NEVER real network. All waits
via the injected sleeper seam — NEVER real sleeps. Clock via the
injected ``monotonic`` seam.

Covers [phase2-plan §4]: discovery fetch + TTL cache; backoff schedule
observed on 503-then-200; persistent failure → cache invalidated +
typed unreachable error; manual-endpoint override skips fetch.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from daemon.config import AuthConfig
from daemon.services.oauth_client import (
    DiscoveryDoc,
    DiscoveryUnreachableError,
    OAuthClient,
)

DISCOVERY_BODY = {
    "issuer": "https://idp.example.com",
    "authorization_endpoint": "https://idp.example.com/authorize",
    "token_endpoint": "https://idp.example.com/token",
    "jwks_uri": "https://idp.example.com/jwks",
}


def _config(**overrides) -> AuthConfig:
    kwargs = dict(
        enabled=True,
        client_id="my-app",
        client_secret=__import__("pydantic").SecretStr("s3cret"),
        issuer="https://idp.example.com",
        allowed_origins=["https://ensemble.example.com"],
    )
    kwargs.update(overrides)
    return AuthConfig(**kwargs)


class _Recorder:
    """Fake transport + sleeper + clock seams (no real I/O, no sleeps)."""

    def __init__(self, responses):
        # responses: list of (status, json-or-None) consumed per request;
        # a callable entry is invoked with the request for dynamic cases.
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.clock = [0.0]

    async def sleeper(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.clock[0] += delay

    def monotonic(self) -> float:
        return self.clock[0]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        entry = self.responses.pop(0) if self.responses else (500, None)
        if callable(entry):
            return entry(request)
        status, payload = entry
        if payload is None:
            return httpx.Response(status, text="boom")
        return httpx.Response(status, json=payload)


def _client(recorder: _Recorder, config: AuthConfig | None = None) -> OAuthClient:
    return OAuthClient(
        config or _config(),
        transport=httpx.MockTransport(recorder.handler),
        sleeper=recorder.sleeper,
        monotonic=recorder.monotonic,
    )


class TestDiscoveryFetchAndCache:
    async def test_fetch_parses_doc(self):
        rec = _Recorder([(200, DISCOVERY_BODY)])
        doc = await _client(rec).discovery()
        assert isinstance(doc, DiscoveryDoc)
        assert doc.authorization_endpoint == DISCOVERY_BODY["authorization_endpoint"]
        assert doc.token_endpoint == DISCOVERY_BODY["token_endpoint"]
        assert doc.jwks_uri == DISCOVERY_BODY["jwks_uri"]
        assert doc.manual is False
        assert len(rec.requests) == 1

    async def test_ttl_cache_hits_within_window(self):
        rec = _Recorder([(200, DISCOVERY_BODY)])
        client = _client(rec)
        await client.discovery()
        await client.discovery()  # within TTL → served from cache
        assert len(rec.requests) == 1

    async def test_cache_expires_and_refetches(self):
        rec = _Recorder([(200, DISCOVERY_BODY), (200, DISCOVERY_BODY)])
        client = _client(rec)
        await client.discovery()
        rec.clock[0] += 3601  # past the default 3600 s TTL
        await client.discovery()
        assert len(rec.requests) == 2

    async def test_default_url_derives_from_issuer(self):
        rec = _Recorder([(200, DISCOVERY_BODY)])
        await _client(rec).discovery()
        assert (
            rec.requests[0].url == "https://idp.example.com/.well-known/openid-configuration"
        )

    async def test_discovery_url_override_used(self):
        rec = _Recorder([(200, DISCOVERY_BODY)])
        cfg = _config(discovery_url="https://idp.example.com/oidc/config")
        await _client(rec, cfg).discovery()
        assert rec.requests[0].url == "https://idp.example.com/oidc/config"


class TestBackoffSchedule:
    async def test_503_then_200_sleeps_once(self):
        rec = _Recorder([(503, None), (200, DISCOVERY_BODY)])
        doc = await _client(rec).discovery()
        assert doc.token_endpoint == DISCOVERY_BODY["token_endpoint"]
        assert rec.sleeps == [1.0]
        assert len(rec.requests) == 2

    async def test_persistent_503_full_schedule_then_typed_error(self):
        rec = _Recorder([(503, None)] * 4)
        with pytest.raises(DiscoveryUnreachableError):
            await _client(rec).discovery()
        assert rec.sleeps == [1.0, 4.0, 16.0]  # 4 attempts, 3 backoffs
        assert len(rec.requests) == 4

    async def test_transport_errors_retry_then_typed_error(self):
        rec = _Recorder([(503, None)] * 4)  # 500s model transport drops
        with pytest.raises(DiscoveryUnreachableError):
            await _client(rec).discovery()
        assert rec.sleeps == [1.0, 4.0, 16.0]

    async def test_4xx_fails_fast_no_backoff(self):
        rec = _Recorder([(404, None)])
        with pytest.raises(DiscoveryUnreachableError):
            await _client(rec).discovery()
        assert rec.sleeps == []
        assert len(rec.requests) == 1


class TestPersistentFailureInvalidatesCache:
    async def test_cache_invalidated_on_persistent_failure(self):
        rec = _Recorder([(200, DISCOVERY_BODY)])
        client = _client(rec)
        await client.discovery()  # cached
        # Now every further request fails: 4 × 500.
        rec.responses.extend([(503, None)] * 4)
        with pytest.raises(DiscoveryUnreachableError):
            await client.discovery(force=True)
        # The failure wiped the cache: next call re-fetches (does not
        # serve the stale doc).
        rec.responses.extend([(503, None)] * 4)
        with pytest.raises(DiscoveryUnreachableError):
            await client.discovery()
        assert len(rec.requests) == 1 + 4 + 4


class TestManualEndpointOverride:
    async def test_complete_overrides_skip_fetch(self):
        rec = _Recorder([])  # any request would 500 — must never happen
        client = OAuthClient(
            _config(),
            transport=httpx.MockTransport(rec.handler),
            sleeper=rec.sleeper,
            monotonic=rec.monotonic,
            authorization_endpoint_override="https://manual.example.com/authorize",
            token_endpoint_override="https://manual.example.com/token",
            jwks_uri_override="https://manual.example.com/jwks",
        )
        doc = await client.discovery()
        assert doc.manual is True
        assert doc.authorization_endpoint == "https://manual.example.com/authorize"
        assert doc.jwks_uri == "https://manual.example.com/jwks"
        assert len(rec.requests) == 0


class TestIssuerMismatchRuntimeHalf:
    async def test_mismatch_warns_and_proceeds(self, caplog):
        body = dict(DISCOVERY_BODY, issuer="https://other-tenant.example.com")
        rec = _Recorder([(200, body)])
        with caplog.at_level(logging.WARNING, logger="daemon.services.oauth_client"):
            doc = await _client(rec).discovery()
        # WARN-not-fatal (§6.3 row-11 runtime half): the doc proceeds.
        assert doc.issuer == "https://other-tenant.example.com"
        assert any("mismatch" in r.message for r in caplog.records)

    async def test_matching_issuer_no_warning(self, caplog):
        rec = _Recorder([(200, DISCOVERY_BODY)])
        with caplog.at_level(logging.WARNING, logger="daemon.services.oauth_client"):
            await _client(rec).discovery()
        assert not any("mismatch" in r.message for r in caplog.records)


class TestInsecureUrlRejected:
    async def test_http_jwks_uri_from_doc_rejected(self):
        """Defense-in-depth beyond the config gate: a discovery doc (or
        fallback) advertising an ``http://`` jwks_uri is refused at the
        client BEFORE any request hits it (JWKS-poisoning defense,
        D2.6). The config layer cannot see doc contents — this is the
        enforcing boundary."""
        body = dict(DISCOVERY_BODY, jwks_uri="http://idp.example.com/jwks")
        rec = _Recorder([(200, body)])
        from daemon.services.oauth_client import OAuthClientError

        with pytest.raises(OAuthClientError, match="https://"):
            await _client(rec).fetch_jwks()
        # Only the discovery request was made — the http jwks endpoint
        # was never contacted.
        assert len(rec.requests) == 1

    async def test_http_discovery_url_allowed_with_opt_in(self):
        body = dict(DISCOVERY_BODY, issuer="http://idp.lan")
        rec = _Recorder([(200, body)])
        cfg = _config(
            issuer="http://idp.lan",
            discovery_url="http://idp.lan/.well-known/openid-configuration",
            allow_insecure_cookie=True,
        )
        doc = await _client(rec, cfg).discovery()
        assert doc.token_endpoint == body["token_endpoint"]

    async def test_http_issuer_refused_at_config_layer(self):
        """P1 §6.3 row 4 owns the config-level https gate — an http
        issuer without the opt-in never even constructs a section."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="https://"):
            _config(issuer="http://idp.lan")
