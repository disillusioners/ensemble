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

from support.mcp_warmup_probe import run_probe, starters_script  # noqa: E402


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
