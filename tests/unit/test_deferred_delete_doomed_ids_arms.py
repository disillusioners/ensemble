"""Deferred DELETE_PHASE arm precedence — C5 remediation (council 2026-10-12).

Proves the D-P3.9 precedence is REAL, not a fail-open artifact:

  Arm 1 (+1 s) fires ONLY when the readiness composite is present AND
  still green, polled FRESH per tick from app.state (never cached —
  the old manager._last_readiness_result cache had NO writer anywhere,
  so the arm fired unconditionally at +1 s and the +5 s floor was
  unreachable).

  Arm 2 (+5 s) is the hard wall-clock floor and governs whenever the
  composite is absent OR not green.

Arm identity is pinned by BEHAVIOR (fire latency + DELETE call shape),
not by log strings: arm 1 must fire inside (1 s, 4 s); arm 2 must NOT
fire before ~5 s. The composite attribute is the production one —
``app.state.readiness_composite`` (byte-identical to the getattr
string in daemon/api.py, verified via function __code__ co_consts).
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest


class _FakeState:
    """Mirrors app.state: plain attribute namespace; the composite
    starts ABSENT (api.py sets ``app.state.readiness_composite = None``
    at app setup, before the first refresh tick)."""

    def __init__(self) -> None:
        self.readiness_composite = None


class _FakeApp:
    def __init__(self) -> None:
        self.state = _FakeState()


class _FakeQueueRepo:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bool]] = []

    def delete_doomed_ids(self, doomed_ids, *, preserve_in_flight=False, chunk_size=1000):
        self.calls.append((list(doomed_ids), bool(preserve_in_flight)))
        return len(doomed_ids)


class _FakeManager:
    def __init__(self) -> None:
        self._queue_repository = _FakeQueueRepo()
        self._pending_doomed_ids = ["id-1", "id-2"]


@pytest.mark.asyncio
async def test_arm1_fires_on_green_composite():
    """Green composite present from t=0 → the +1 s arm fires the
    DELETE at ~1 s (well inside (1, 4) s), with
    preserve_in_flight=True, and the manager's pending list is
    cleared in the finally-block."""
    from daemon.api import _deferred_delete_doomed_ids

    app, mgr = _FakeApp(), _FakeManager()
    app.state.readiness_composite = SimpleNamespace(ready=True)
    t0 = time.monotonic()
    await asyncio.wait_for(_deferred_delete_doomed_ids(mgr, app, ["a", "b"]), timeout=10)
    elapsed = time.monotonic() - t0
    assert mgr._queue_repository.calls == [(["a", "b"], True)]
    assert 0.9 <= elapsed < 4.0, (
        f"+1 s arm fired at {elapsed:.2f}s — expected ~1 s "
        f"(later than 4 s means the +5 s floor fired instead)"
    )
    assert mgr._pending_doomed_ids == []


@pytest.mark.asyncio
async def test_arm1_dormant_when_composite_missing():
    """FAIL-OPEN REGRESSION PIN (the C5 defect): composite NEVER
    appears → the +1 s arm must NOT fire at ~1 s. Only the +5 s floor
    may fire the DELETE (latency >= 4.9 s)."""
    from daemon.api import _deferred_delete_doomed_ids

    app, mgr = _FakeApp(), _FakeManager()
    t0 = time.monotonic()
    await asyncio.wait_for(_deferred_delete_doomed_ids(mgr, app, ["a"]), timeout=15)
    elapsed = time.monotonic() - t0
    assert mgr._queue_repository.calls == [(["a"], True)]
    assert elapsed >= 4.9, (
        f"DELETE fired at {elapsed:.2f}s with NO composite — the +1 s arm "
        f"fired unconditionally (the C5 fail-open defect is back)"
    )


@pytest.mark.asyncio
async def test_arm1_dormant_when_composite_not_green():
    """Composite present but ready=False → same floor-governed shape:
    the +1 s arm stays dormant, DELETE fires via the +5 s floor."""
    from daemon.api import _deferred_delete_doomed_ids

    app, mgr = _FakeApp(), _FakeManager()
    app.state.readiness_composite = SimpleNamespace(ready=False)
    t0 = time.monotonic()
    await asyncio.wait_for(_deferred_delete_doomed_ids(mgr, app, ["a"]), timeout=15)
    elapsed = time.monotonic() - t0
    assert mgr._queue_repository.calls == [(["a"], True)]
    assert elapsed >= 4.9, (
        f"+1 s arm fired on a NOT-GREEN composite at {elapsed:.2f}s "
        f"(ready=False must not satisfy the still-green re-check)"
    )
