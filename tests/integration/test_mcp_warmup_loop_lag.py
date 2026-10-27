"""Loop-lag pin: the event loop stays responsive while an MCP warmup runs.

WHAT THIS PIN MEASURES
----------------------
The B1 starvation mechanism: a warmup that never completes must not
block the daemon's single asyncio thread. The acceptance thresholds
are NORMATIVE in requirements.md §4.6 (P6):

    p95 event-loop scheduling lag <= 250 ms  AND  max <= 500 ms

A periodic heartbeat task samples the scheduling lag of the event loop
while the warmup is in flight.

TWO LANES
---------
* LANE A (always runs) — the REAL pinned MCP SDK, a REAL child that
  never speaks the protocol, the REAL warmup pool, and a REAL event
  loop. It runs in a child process because the pytest root conftest
  mocks the ``mcp`` SDK (see ``tests/support/mcp_warmup_probe.py``).
* LANE B (env-gated, tester-executed) — the full-fidelity daemon boot
  (REAL ``create_app()`` + REAL ``lifespan``) with the warmup in
  flight and ``/livez`` + ``/readyz`` probed through the ASGI
  transport. Gated on ``ENSEMBLE_MCP_WARMUP_PIN=1`` (plus a scratch PG
  via ``PG_TEST_*``) because it needs a provisioned scratch database —
  the house convention is that the tester lane owns that provisioning
  and executes the boot lane. The BODY is real (it is not a stub).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support.mcp_warmup_probe import (  # noqa: E402
    daemon_boot_loop_lag_script,
    loop_lag_script,
    run_probe,
)

#: The normative acceptance thresholds (requirements.md §4.6, P6).
P95_LIMIT_MS = 250.0
MAX_LIMIT_MS = 500.0


@pytest.mark.timeout(180)
def test_lane_a_loop_lag_under_a_hanging_warmup():
    """LANE A — the real warmup against a real hanging server."""
    result = run_probe(loop_lag_script(), timeout=150)
    print(
        "LOOP_LAG lane A: p95=%.1fms max=%.1fms samples=%d elapsed=%.1fs"
        % (
            result["p95_ms"],
            result["max_ms"],
            result["samples"],
            result["warmup_elapsed_s"],
        )
    )
    assert result["samples"] > 0, (
        f"the heartbeat collected no samples — the measurement is vacuous: {result}"
    )
    assert result["p95_ms"] <= P95_LIMIT_MS, (
        f"p95 loop lag {result['p95_ms']:.1f}ms exceeds {P95_LIMIT_MS}ms: {result}"
    )
    assert result["max_ms"] <= MAX_LIMIT_MS, (
        f"max loop lag {result['max_ms']:.1f}ms exceeds {MAX_LIMIT_MS}ms: {result}"
    )


@pytest.mark.timeout(300)
@pytest.mark.skipif(
    os.environ.get("ENSEMBLE_MCP_WARMUP_PIN") != "1",
    reason=(
        "LANE B needs BOTH gates: ENSEMBLE_MCP_WARMUP_PIN=1 (this lane) "
        "and a provisioned scratch PG (PG_TEST_* — the tester lane owns "
        "that provisioning and executes this boot lane)"
    ),
)
def test_lane_b_daemon_boot_loop_lag():
    """LANE B — the full daemon boot with the warmup in flight."""
    result = run_probe(daemon_boot_loop_lag_script(), timeout=280)
    print(
        "LOOP_LAG lane B: p95=%.1fms max=%.1fms samples=%d livez=%s readyz=%s"
        % (
            result["p95_ms"],
            result["max_ms"],
            result["samples"],
            result["livez"],
            result["readyz"],
        )
    )
    assert result["samples"] > 0, f"the measurement is vacuous: {result}"
    assert result["p95_ms"] <= P95_LIMIT_MS, (
        f"p95 loop lag {result['p95_ms']:.1f}ms exceeds {P95_LIMIT_MS}ms: {result}"
    )
    assert result["max_ms"] <= MAX_LIMIT_MS, (
        f"max loop lag {result['max_ms']:.1f}ms exceeds {MAX_LIMIT_MS}ms: {result}"
    )
    # /livez and /readyz must answer (200) throughout the warmup window.
    assert result["livez"] == 200, f"/livez did not answer 200: {result}"
    assert result["readyz"] == 200, f"/readyz did not answer 200: {result}"
