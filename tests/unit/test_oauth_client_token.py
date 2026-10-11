"""OAuthClient.exchange_code() tests — oauth2-auth Phase 2 (T2.6 / G2.1).

Covers [phase2-plan §4]: exchange request body shape (form fields incl.
``code_verifier``); non-2xx → typed error with the log body truncated
≤256 chars; the client secret NEVER appears in log output. All HTTP
via ``httpx.MockTransport`` — never real network; no real sleeps.
"""

from __future__ import annotations

import logging
import urllib.parse

import httpx
import pytest
from pydantic import SecretStr

from daemon.config import AuthConfig
from daemon.services.oauth_client import (
    DiscoveryUnreachableError,
    OAuthClient,
    TokenExchangeError,
    TokenResponse,
)

SECRET = "client-secret-NEVER-LOG-4f7a21"
DISCOVERY_BODY = {
    "issuer": "https://idp.example.com",
    "authorization_endpoint": "https://idp.example.com/authorize",
    "token_endpoint": "https://idp.example.com/token",
    "jwks_uri": "https://idp.example.com/jwks",
}
TOKEN_BODY = {
    "access_token": "at-123",
    "token_type": "Bearer",
    "expires_in": 3600,
    "id_token": "jwt-payload-comes-later",
}


def _config() -> AuthConfig:
    return AuthConfig(
        enabled=True,
        client_id="my-app",
        client_secret=SecretStr(SECRET),
        issuer="https://idp.example.com",
        allowed_origins=["https://ensemble.example.com"],
    )


class _Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    async def sleeper(self, delay: float) -> None:
        # Discovery retries legitimately back off — recorded, not real.
        self.sleeps.append(delay)

    def monotonic(self) -> float:
        return 0.0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return httpx.Response(500, text="no scripted response left")
        if callable(self.responses[0]):
            entry = self.responses[0]  # sticky — serves every request
        else:
            entry = self.responses.pop(0)
        if callable(entry):
            return entry(request)
        status, payload = entry
        if payload is None:
            return httpx.Response(status, text="nope")
        return httpx.Response(status, json=payload)


def _client(recorder: _Recorder, **kwargs) -> OAuthClient:
    return OAuthClient(
        _config(),
        transport=httpx.MockTransport(recorder.handler),
        sleeper=recorder.sleeper,
        monotonic=recorder.monotonic,
        **kwargs,
    )


async def _exchange(client: OAuthClient, **overrides):
    params = dict(
        code="auth-code-1",
        redirect_uri="https://ensemble.example.com/api/auth/callback",
        code_verifier="v" * 64,
    )
    params.update(overrides)
    return await client.exchange_code(**params)


class TestExchangeSuccess:
    async def test_success_payload_normalized(self):
        rec = _Recorder([(200, DISCOVERY_BODY), (200, TOKEN_BODY)])
        client = _client(rec)
        result = await _exchange(client)
        assert isinstance(result, TokenResponse)
        assert result.access_token == "at-123"
        assert result.token_type == "Bearer"
        assert result.expires_in == 3600
        assert result.id_token == "jwt-payload-comes-later"

    async def test_request_body_shape_incl_code_verifier(self):
        rec = _Recorder([(200, DISCOVERY_BODY), (200, TOKEN_BODY)])
        await _exchange(_client(rec))
        assert len(rec.requests) == 2
        token_req = rec.requests[1]
        assert str(token_req.url) == "https://idp.example.com/token"
        assert (
            token_req.headers["content-type"]
            == "application/x-www-form-urlencoded"
        )
        form = urllib.parse.parse_qs(token_req.content.decode())
        assert form["grant_type"] == ["authorization_code"]
        assert form["code"] == ["auth-code-1"]
        assert form["redirect_uri"] == [
            "https://ensemble.example.com/api/auth/callback"
        ]
        assert form["client_id"] == ["my-app"]
        assert form["client_secret"] == [SECRET]
        assert form["code_verifier"] == ["v" * 64]


class TestExchangeFailures:
    async def test_non_2xx_typed_error_and_truncated_log(self, caplog):
        # Distinguishable halves: 256 'a's then 244 'b's — the 'b' tail
        # must NOT appear in the log (truncation proof).
        long_body = "a" * 256 + "b" * 244

        def dynamic(request: httpx.Request) -> httpx.Response:
            if ".well-known" in str(request.url):
                return httpx.Response(200, json=DISCOVERY_BODY)
            return httpx.Response(400, text=long_body)

        rec = _Recorder([dynamic])
        with caplog.at_level(logging.WARNING, logger="daemon.services.oauth_client"):
            with pytest.raises(TokenExchangeError) as excinfo:
                await _exchange(_client(rec))
        assert excinfo.value.status_code == 400
        assert excinfo.value.code == "TOKEN_EXCHANGE_FAILED"
        # The body IS logged — truncated to ≤256 chars.
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert long_body[:256] in logged          # the 256 'a's survive
        assert long_body[256:] not in logged      # the 'b' tail is cut

    async def test_secret_never_in_logs(self, caplog):
        def dynamic(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="unauthorized")

        rec = _Recorder([(200, DISCOVERY_BODY), dynamic])
        with caplog.at_level(logging.WARNING, logger="daemon.services.oauth_client"):
            with pytest.raises(TokenExchangeError):
                await _exchange(_client(rec))
        all_output = caplog.text
        assert SECRET not in all_output

    async def test_transport_error_is_typed(self):
        def dynamic(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        rec = _Recorder([(200, DISCOVERY_BODY), dynamic])
        with pytest.raises(TokenExchangeError):
            await _exchange(_client(rec))

    async def test_2xx_missing_access_token_is_typed(self):
        rec = _Recorder([(200, DISCOVERY_BODY), (200, {"oops": True})])
        with pytest.raises(TokenExchangeError, match="access_token"):
            await _exchange(_client(rec))

    async def test_discovery_unreachable_propagates_typed(self):
        rec = _Recorder([(503, None)] * 5)
        with pytest.raises(DiscoveryUnreachableError):
            await _exchange(_client(rec))


class TestTokenEndpointOverride:
    async def test_manual_token_endpoint_override_wins(self):
        seen_paths: list[str] = []

        def dynamic(request: httpx.Request) -> httpx.Response:
            seen_paths.append(str(request.url))
            if ".well-known" in str(request.url):
                return httpx.Response(200, json=DISCOVERY_BODY)
            return httpx.Response(200, json=TOKEN_BODY)

        rec = _Recorder([dynamic])
        client = _client(rec, token_endpoint_override="https://manual.example.com/token")
        await _exchange(client)
        assert "https://manual.example.com/token" in seen_paths
