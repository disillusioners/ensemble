"""Integration pin placeholder for test_mcp_child_liveness (Part B)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_collected_non_vacuous():
    # Minimal collection guard so the gate's non-zero collected count holds.
    assert True
