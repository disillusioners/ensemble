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

#: The role the FENCE database is reached with.
#:
#: WHY NOT THE AMBIENT ``POSTGRES_USER``: the repository's ``.env``
#: exports ``POSTGRES_USER=ensemble`` — the role of the PRODUCTION
#: database — and the ambient shell inherits it.  The fence database
#: (``ensemble_dev_test_only``) is owned by the SHELL USER, so the
#: ``ensemble`` role does not own its tables: the daemon's boot
#: ``ALTER TABLE task …`` raises ``InsufficientPrivilege`` ("must be
#: owner of table task"), the lifespan dies and the gate used to hang
#: for 60 s on a generic "the server failed to start" error.  The fence
#: role is therefore resolved by the harness (the fence owner, unless
#: ``MCPS_GATEWAY_FENCE_PG_USER`` names one explicitly).
sys.path.insert(0, str(REPO_ROOT))
from tests.integration.mcps_gateway import tls_harness  # noqa: E402

FENCE_ROLE = tls_harness.resolve_fence_role()

pytestmark = pytest.mark.integration


@pytest.mark.timeout(300)
def test_p0_gate_real_client_against_real_gateway() -> None:
    """The real Go client completes a TLS handshake and a command against the real gateway."""
    env = dict(os.environ)
    # The subprocess talks to the FENCE database only — the fence role
    # is pinned here so that nothing in the gate process can inherit the
    # ambient (production) role.
    env["POSTGRES_USER"] = FENCE_ROLE
    proc = subprocess.run(
        [sys.executable, str(GATE_SCRIPT), "1", "/tmp/mcps-d1/gate", FENCE_ROLE],
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
