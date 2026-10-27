"""Hang-wedge pin: ONE hanging server must not starve the other starters.

WHAT THIS PIN MEASURES
----------------------
``pool.warmup()`` gathers the per-server warmups. A server that never
speaks the MCP protocol must NOT prevent:

* ``pool.start_health_check(...)`` — the health-check task comes up, and
* the schema/discovery cache of a HEALTHY server — populated, i.e. the
  eager-warm starter's observable effect.

The belt-and-braces that makes this true is
``WARMUP_TOTAL_BUDGET_S`` (90 s in the shipped constant; the probe
lowers it so the pin runs in seconds) wrapped around the per-server
gather — see ``daemon/mcp/warmup_pool.py``.

The probe runs in a child process because the pytest root conftest
mocks the ``mcp`` SDK (the real SDK is needed for a REAL hanging
server and a REAL healthy server).

FENCES: no daemon boot; no PG; mktemp FAKE_HOME for the child.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support.mcp_warmup_probe import (  # noqa: E402
    run_probe,
    sibling_reap_script,
    starters_script,
    warmup_cancel_script,
)


@pytest.mark.timeout(180)
def test_one_hanging_server_does_not_starve_the_starters():
    """The starters run even though one server hangs forever."""
    result = run_probe(starters_script(), timeout=150)
    assert result["warmup_returned"] is True, (
        f"warmup() did not return despite the hanging server: {result}"
    )
    assert result["health_task_alive"] is True, (
        f"the health-check starter never came up: {result}"
    )
    assert result["schema_cache_populated"] is True, (
        f"the schema/discovery cache of the healthy server was never "
        f"populated — the hanging server starved the starters: {result}"
    )


@pytest.mark.timeout(180)
def test_cancelling_the_warmup_task_propagates_cancelled_error():
    """A cancelled warmup task must RE-RAISE ``CancelledError``.

    The observable the reviewer's CRITICAL-1 is about: a manager-shaped
    ``await self._warmup_task`` must see ``CancelledError`` (and the
    task must report ``cancelled() is True``) when the warmup task is
    cancelled. If the total-budget ``except`` swallowed the
    cancellation, the task would complete SUCCESSFULLY, the manager's
    ``_warmup_and_report`` would continue past the cancel and would
    call ``start_health_check`` / ``eager_warm_schemas`` DURING
    SHUTDOWN — spawning fresh MCP subprocesses while the daemon goes
    down and blocking the shutdown on that discovery.
    """
    result = run_probe(warmup_cancel_script(), timeout=150)
    assert result["raised"] == "CancelledError", (
        "the cancelled warmup task did NOT raise CancelledError — the "
        f"cancellation was swallowed: {result}"
    )
    assert result["cancelled"] is True, (
        f"the warmup task completed instead of being cancelled: {result}"
    )


@pytest.mark.timeout(180)
def test_a_failing_session_does_not_reap_a_live_sibling():
    """A failing session's reap must not kill a live sibling's child.

    The sibling uses the SAME command basename with DIFFERENT args (the
    common case — two npx-based MCP servers). The reap's baseline
    snapshot and its candidate set MUST use the SAME matcher; when they
    diverged, the sibling was absent from the baseline, present in the
    candidate set, and (until its own stamp claimed it) vulnerable to
    a cross-session process-group kill.
    """
    result = run_probe(sibling_reap_script(), timeout=150)
    assert result["sibling_alive"] is True, (
        "a failing session's reap KILLED a live sibling's child — the "
        f"baseline and the reap candidate set use different matchers: {result}"
    )
