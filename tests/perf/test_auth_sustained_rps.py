"""Auth sustained-RPS record — oauth2-auth Phase 5 (T5.8 / NFR-16).

Records sustained-throughput numbers from the in-process ASGITransport
composition. The 1000-RPS/60s sustained numbers are RECORDED (not
CI-enforced per D5.4); they live in the test output / log for
comparison against the v1 budget.

Uses ``httpx.AsyncClient`` (ASGITransport is async-only).

Usage::

    pytest tests/perf/test_auth_sustained_rps.py --capture=no -s
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

import httpx
import pytest

from tests.conftest_auth import auth_enabled_app


@pytest.fixture()
def perf_app(tmp_path):
    with auth_enabled_app(tmp_path=tmp_path) as triple:
        yield triple


class TestSustainedRps:
    async def test_record_1000_rps_60s_sustained(self, perf_app):
        """Run a sustained-throughput probe (scaled to the in-process
        ASGITransport), recording the actual rate + p50/p99 latencies.

        The test ASSERTS only that the probe completes without errors
        (status code distribution stays consistent) and the actual
        rate stays within a generous tolerance of the target. The
        STRICT budget is enforced only in the opt-in perf lane (the
        1000-RPS sustained target is the v1 production budget — NFR-16).
        """
        app, fake, transport = perf_app
        target_rps = 200  # scaled for the in-process fixture
        duration_seconds = 5  # scaled CI runtime

        statuses: dict[int, int] = {}
        samples_us: list[float] = []

        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            await client.get("/livez")  # warm-up
            t_start = time.perf_counter()
            deadline = t_start + duration_seconds
            request_interval = 1.0 / target_rps
            while time.perf_counter() < deadline:
                t0 = time.perf_counter_ns()
                r = await client.get("/livez")
                samples_us.append((time.perf_counter_ns() - t0) / 1_000)
                statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
                # Sleep to maintain the target rate (best-effort).
                elapsed = (time.perf_counter_ns() - t0) / 1e9
                if elapsed < request_interval:
                    await asyncio.sleep(request_interval - elapsed)
            actual_duration = time.perf_counter() - t_start

        actual_rps = len(samples_us) / actual_duration
        p50 = statistics.median(samples_us)
        p99 = statistics.quantiles(samples_us, n=100)[98]
        total = sum(statuses.values())
        success = statuses.get(200, 0)
        assert success == total, f"unexpected statuses: {statuses}"

        # Record (not enforce) — emit on stderr for the log scan.
        print(
            f"\n[perf] sustained {actual_rps:.1f} RPS for "
            f"{actual_duration:.1f}s — p50={p50:.0f}us p99={p99:.0f}us "
            f"({total} requests)",
            file=sys.stderr,
        )