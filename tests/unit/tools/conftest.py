"""Shared fixtures for the tests/unit/tools pin files.

SINGLE HOME for the per-boot quarantine-registry reset (item 17 of the
tidier hygiene fold): every case in this directory starts AND ends from
a clean ``daemon.mcp.init_guard`` registry, so a test that quarantines a
server cannot leak that state into a later test in the same process.
Production code never calls ``reset_for_tests``; the guard module's
registry is per-process (per-boot) state.
"""

import pytest

from daemon.mcp import init_guard


@pytest.fixture(autouse=True)
def _clean_init_guard_registry():
    """Every case starts from a clean per-boot quarantine registry."""
    init_guard.reset_for_tests()
    yield
    init_guard.reset_for_tests()
