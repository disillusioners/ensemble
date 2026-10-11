"""Auth middleware latency benchmark — oauth2-auth Phase 5 (T5.8 / NFR-9).

ASGITransport benchmark of the per-request auth middleware latency.
Generous CI thresholds (per D5.4); strict budgets enforced only in
the opt-in perf lane (the 1000-RPS/60s sustained numbers are recorded
in :mod:`test_auth_sustained_rps`).

Measured in microseconds because the per-request budget is tight
(<1 ms p99 target — NFR-9). The fixtures run with the auth-enabled
app + the in-process FakeOidcProvider to mirror production
composition.

Uses ``httpx.AsyncClient`` because ASGITransport is async-only in
httpx 0.27+. The async flavor requires the test functions to be
``async def`` (pytest-asyncio's ``asyncio_mode = "auto"`` makes this
implicit per the project pyproject.toml).
"""

from __future__ import annotations

import asyncio
import statistics
import time

import httpx
import pytest

from tests.conftest_auth import auth_enabled_app


@pytest.fixture()
def perf_app(tmp_path):
    """Auth-enabled app wired with a fast-sleeper OAuthClient (no real
    sleeps during backoff). Returns (app, fake, transport)."""
    with auth_enabled_app(tmp_path=tmp_path) as triple:
        yield triple


class TestAuthMiddlewareLatency:
    async def test_public_path_p99_under_threshold(self, perf_app):
        """The public-path fast path is a pure-CPU frozenset lookup
        (D11 structural guarantee). p99 < 5 ms on the ASGITransport."""
        app, fake, transport = perf_app
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            await client.get("/livez")  # warm-up
            samples_us: list[float] = []
            for _ in range(200):
                t0 = time.perf_counter_ns()
                r = await client.get("/livez")
                samples_us.append((time.perf_counter_ns() - t0) / 1_000)
                assert r.status_code == 200
        p99 = statistics.quantiles(samples_us, n=100)[98]
        # Generous CI threshold (NFR-9 is <1 ms target; CI gets 5x).
        assert p99 < 5_000, f"p99={p99}us exceeds 5ms threshold"

    async def test_protected_path_401_path_latency(self, perf_app):
        """A request to a protected path WITHOUT credentials lands on
        the 401 fast path (Accept: application/json branch)."""
        app, fake, transport = perf_app
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            await client.get("/api/mcp/kb/keys")  # warm-up
            samples_us: list[float] = []
            for _ in range(200):
                t0 = time.perf_counter_ns()
                r = await client.get(
                    "/api/mcp/kb/keys",
                    headers={"Accept": "application/json"},
                )
                samples_us.append((time.perf_counter_ns() - t0) / 1_000)
                assert r.status_code == 401
        p99 = statistics.quantiles(samples_us, n=100)[98]
        # The 401 path is also pure-CPU; generous threshold.
        assert p99 < 5_000, f"p99={p99}us exceeds 5ms threshold"

    async def test_module_lane_pass_through_latency(self, perf_app):
        """Module-lane paths pass through the OAuth2 layer untouched;
        the latency is dominated by the route handler."""
        app, fake, transport = perf_app
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            await client.get("/api/webhooks/x")  # warm-up
            samples_us: list[float] = []
            for _ in range(200):
                t0 = time.perf_counter_ns()
                r = await client.post("/api/webhooks/x", json={"text": "hi"})
                samples_us.append((time.perf_counter_ns() - t0) / 1_000)
        # Module-lane latency is comparable to the public-path latency
        # (the OAuth2 layer short-circuits; the route handler decides).
        p99 = statistics.quantiles(samples_us, n=100)[98]
        assert p99 < 10_000, f"p99={p99}us exceeds 10ms threshold"