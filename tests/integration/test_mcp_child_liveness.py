"""B3 child-liveness pin — REAL subprocess + REAL /proc census (no daemon, no PG).

WHAT THIS PIN MEASURES
----------------------
Two legs, per requirements.md §3-B3 and the S3 caveat ("a mocked
__aexit__ alone can pass while the real child survives"):

* LEG 1 (clean failure)  — the transport is opened and closed; the real
  child must be GONE from /proc afterwards.
* LEG 2 (timeout / cancellation mid-handshake) — the G1 route: the
  caller of ``__aenter__`` is cancelled while the inner transport is
  still entering. The real child must ALSO be gone.

Both legs run against the REAL pinned MCP SDK (``mcp==1.27.1``) in a
child process (the pytest root conftest mocks ``mcp``, so the real SDK
is only reachable outside pytest — see
``tests/support/mcp_warmup_probe.py``).

The census is NON-VACUOUS: a control phase proves the same helper
FINDS the live child, so "0 survivors" is a measurement and not an
artifact of a broken matcher. The match is starttime-checked, hence
PID-recycle safe.

FENCES: no daemon boot; no PG; no live-shaped HOME path (the probe
child gets a mktemp FAKE_HOME); the spawned children are plain
``python <script>`` processes owned by the probe.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support.mcp_warmup_probe import (  # noqa: E402
    child_cancellation_script,
    child_liveness_script,
    run_probe,
)


@pytest.mark.timeout(180)
def test_leg1_clean_failure_leaves_no_child():
    """LEG 1 — a clean close leaves ZERO surviving children."""
    result = run_probe(child_liveness_script(), timeout=150)
    # The control proves the census can SEE the live child.
    assert result["control_live"], (
        "census control failed: the live child was not found — the "
        f"census is not measuring anything: {result}"
    )
    # The child is gone after the close.
    assert result["survivors"] == [], (
        f"B3 LEAK (clean-failure leg): {result['survivors']} survived the "
        f"transport close: {result}"
    )


@pytest.mark.timeout(180)
def test_leg2_cancellation_leaves_no_child():
    """LEG 2 — a cancelled handshake leaves ZERO surviving children.

    This is the G1 route: the SDK's process-group shutdown lives in its
    async generator's ``finally`` AFTER the ``yield``, so a cancellation
    during the enter orphans the child unless the wrapper tears it down
    explicitly. The probe holds the real SDK's ``__aenter__`` return on
    a gate so the cancel window is deterministic.
    """
    result = run_probe(child_cancellation_script(), timeout=150)
    assert result["control_live"], (
        "census control failed: the live child was not found — the "
        f"census is not measuring anything: {result}"
    )
    assert result["survivors"] == [], (
        f"B3 LEAK (cancellation leg): {result['survivors']} survived the "
        f"cancelled handshake: {result}"
    )
