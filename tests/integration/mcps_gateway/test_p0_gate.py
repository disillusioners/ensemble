"""Pytest wrapper around the Phase-D1 P0 GATE.

The gate itself lives in ``p0_gate.py`` and runs in its OWN process: the
gateway knobs and the database target are read when ``daemon.api`` is
IMPORTED, so a pytest process that has already imported the daemon (e.g.
the fake-client suite next door) can no longer change either.  This
wrapper therefore shells out to the script and asserts on its VERDICT.

Run it (it is an ``integration`` test, like its siblings — the default
``addopts`` deselects it)::

    .venv/bin/python -m pytest tests/integration/mcps_gateway/test_p0_gate.py -v -m integration

The gate can also be run directly, without pytest::

    .venv/bin/python tests/integration/mcps_gateway/p0_gate.py 1 /tmp/mcps-d1/gate "$(whoami)"
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_SCRIPT = REPO_ROOT / "tests" / "integration" / "mcps_gateway" / "p0_gate.py"

#: The fence database user — the local PostgreSQL cluster is reached with
#: the shell user unless the caller overrides it.
POSTGRES_USER = os.environ.get("POSTGRES_USER", "") or ""

pytestmark = pytest.mark.integration


@pytest.mark.timeout(300)
def test_p0_gate_real_client_against_real_gateway() -> None:
    """The real Go client completes a TLS handshake and a command against the real gateway."""
    env = dict(os.environ)
    env["POSTGRES_USER"] = POSTGRES_USER
    proc = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "1", "/tmp/mcps-d1/gate", POSTGRES_USER],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    assert proc.returncode == 0, (
        f"the P0 gate FAILED (exit {proc.returncode}) — see the report above"
    )
    assert "P0 GATE VERDICT: PASS" in proc.stdout, "the gate did not report PASS"
