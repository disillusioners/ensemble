"""Shared test helpers for the long-tool-call-nudge test suite.

M2 — consolidated scaffold (2026-09-13). The pre-consolidation test
files each carried near-identical copies of:
  * ``_FakeClock`` class (5 files: belt, detector, observability,
    resolve, wrapper)
  * ``_scanner(...)`` factory (4 files: belt, detector, threshold,
    test_long_tool_nudge_root)
  * ``_stamp(...)`` async helper (3 files: belt, detector, resolve)
  * ``_ctx(...)`` factory (2 files: test_long_tool_nudge_root,
    hand_off)
  * the ``lt_real`` fixture + ``evict_langgraph_mocks`` harness
    (2 files: observability, wrapper — they were equivalent)

These duplicates were drift hazards — a clock-class shape tweak
in detector.py could silently leave observability.py behind, and a
new attach surface in long_tool_nudge.py might land in only one
scanner factory. This module pins the canonical shapes once. Test
files now import from here and keep only the test-specific
fixtures/overrides.

Public surface (everything else is module-private):

  * ``_FakeClock`` — unconditional monotonic clock for fake-clock
    tests; ``fake_clock`` is a pytest fixture that monkeypatches
    ``daemon.services.long_tool_nudge.time`` to it.
  * ``fake_clock_fixture`` — alias of ``fake_clock`` (older name
    kept for tests that referenced it directly).
  * ``lt_real`` — context-manager / fixture that swaps the
    global langgraph mock out and yields a freshly-imported
    ``daemon.services.long_tool_nudge`` module. Mirrors the
    pre-consolidation inline fixture in observability.py and
    wrapper.py.
  * ``make_episode_ctx(**overrides)`` — build a
    ``LongToolNudgeEpisodeCtx`` with the canonical 7-field shape.
    Replaces the two ``_ctx`` copies.
  * ``make_scanner(registry, **overrides)`` — build a
    ``LongToolNudgeScanner`` with the canonical MagicMock repo /
    AsyncMock manager / optional stub / handoff_fn shape. Callers
    that need the slightly different parent-status default (paused
    for the AM-7 test series) override via kwargs.
  * ``stamp_started_at_ago(registry, child, call_id, clock, age)``
    — record a stamp and rewind ``started_at`` by ``age`` seconds.
    Replaces the three ``_stamp`` copies.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from typing import Any, Optional

import pytest
from langchain_core.tools import tool
from tests.helpers.checkpoint_prune_pg import (
    evict_langgraph_mocks,
    restore_langgraph_mocks,
)

from daemon.services.long_tool_nudge import (
    LongToolNudgeEpisodeCtx,
    LongToolNudgeRegistry,
    LongToolNudgeScanner,
)


class FakeClock:
    """Deterministic monotonic clock for fake-clock tests.

    Pinned to ``t=10_000.0`` so the absolute value is irrelevant —
    tests only care about the relative ``clock.t += N`` deltas.
    """

    def __init__(self) -> None:
        self.t: float = 10_000.0

    def monotonic(self) -> float:
        return self.t


@pytest.fixture
def fake_clock(monkeypatch):
    """Pytest fixture: monkeypatch
    ``daemon.services.long_tool_nudge.time`` with a fresh
    ``FakeClock``; returns the clock instance for the test to
    advance (``fake_clock.t += 60``).

    The patch target is a fully-qualified dotted path so the
    monkeypatch resolves against the LIVE module — the suite's
    root conftest does not install a global langgraph mock, but
    the ``lt_real`` fixture swaps the module reference under our
    feet; this fixture patches by name and is therefore
    independent of which module reference the test sees.
    """
    clock = FakeClock()
    monkeypatch.setattr("daemon.services.long_tool_nudge.time", clock)
    return clock


@pytest.fixture
def lt_real(monkeypatch):
    """Pytest fixture: yield a freshly-imported
    ``daemon.services.long_tool_nudge`` module with REAL langgraph
    (the suite's root conftest installs a global langgraph mock —
    this fixture evicts it for the duration of the test and
    restores it on teardown).

    Mirrors the pre-consolidation inline fixture in
    observability.py + wrapper.py (they were equivalent)."""
    saved = evict_langgraph_mocks()
    saved_lt = sys.modules.pop("daemon.services.long_tool_nudge", None)
    try:
        yield importlib.import_module("daemon.services.long_tool_nudge")
    finally:
        sys.modules.pop("daemon.services.long_tool_nudge", None)
        if saved_lt is not None:
            sys.modules["daemon.services.long_tool_nudge"] = saved_lt
        restore_langgraph_mocks(saved)


def make_episode_ctx(**overrides: Any) -> LongToolNudgeEpisodeCtx:
    """Build a ``LongToolNudgeEpisodeCtx`` with the canonical 7-field
    shape. Any kwarg overrides the matching field. The phase-2
    contract pins this field set as additive (additive fields
    allowed) — shrinking it is a breaking change for the
    delivery-seam contract.
    """
    fields = dict(
        child_id="child-1",
        parent_id="parent-1",
        tool_name="bash",
        tool_call_id="call-1",
        elapsed_seconds=950.0,
        threshold_seconds=900,
        episode_started_at=100.0,
    )
    fields.update(overrides)
    return LongToolNudgeEpisodeCtx(**fields)


def make_scanner(
    registry: Optional[LongToolNudgeRegistry] = None,
    *,
    parent_status: str = "running",
    manager: Any = None,
    repo: Any = None,
    threshold_metadata: Any = None,
    handoff_stub_enabled: bool = False,
    handoff_fn: Any = None,
) -> LongToolNudgeScanner:
    """Build a ``LongToolNudgeScanner`` with the canonical mock surface.

    Callers that need a specific parent-status default (paused for
    the AM-7 test series, or a literal terminal for AD-40) override
    via kwargs. The registry defaults to a fresh instance — pass an
    explicit one if the test owns the registry.
    """
    from unittest.mock import AsyncMock, MagicMock

    if repo is None:
        repo = MagicMock()
        repo.get_metadata_value = MagicMock(return_value=threshold_metadata)
        parent = MagicMock()
        parent.status = parent_status
        parent.parent_id = "grand-1"
        repo.get = MagicMock(return_value=parent)
    if manager is None:
        manager = AsyncMock()
        manager.enqueue_message = AsyncMock()
    if registry is None:
        registry = LongToolNudgeRegistry()
    return LongToolNudgeScanner(
        repo,
        manager=manager,
        registry=registry,
        enabled=True,
        handoff_stub_enabled=handoff_stub_enabled,
        handoff_fn=handoff_fn,
    )


async def stamp_started_at_ago(
    registry: LongToolNudgeRegistry,
    child: str,
    call_id: str,
    clock: FakeClock,
    age: float,
    parent_id: str = "parent-1",
    tool_name: str = "bash",
) -> None:
    """Record a stamp and rewind ``started_at`` by ``age`` seconds.

    The wrapper's ``record_start`` stamps with the live
    ``time.monotonic()``; this helper overrides that with a value
    that places the stamp ``age`` seconds in the past — the
    scanner-tick fixture pattern.
    """
    await registry.record_start(child, call_id, tool_name, parent_id)
    registry._stamps[child][call_id].started_at = clock.t - age


__all__ = [
    "FakeClock",
    "fake_clock",
    "lt_real",
    "make_episode_ctx",
    "make_scanner",
    "stamp_started_at_ago",
]
