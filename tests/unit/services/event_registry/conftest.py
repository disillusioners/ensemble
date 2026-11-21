"""Event-registry pack fixtures — a clean registry per test."""

from __future__ import annotations

import pytest

from daemon.services import event_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset ALL registry state around every test (isolation)."""
    event_registry.reset_for_tests()
    event_registry.reset_warning_state_for_tests()
    yield
    event_registry.reset_for_tests()
    event_registry.reset_warning_state_for_tests()
