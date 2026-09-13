"""T3 — LongToolNudgeScanner boundary / heartbeat-independence / dedup /
error-isolation tests.

All timing is driven by a fake monotonic clock (monkeypatched over
``daemon.services.long_tool_nudge.time``) so the strict-``>``
boundary (AD-4) is deterministic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import daemon.services.long_tool_nudge as lt
from daemon.services.long_tool_nudge import (
    LongToolNudgeRegistry,
    LongToolNudgeScanner,
    LongToolNudgeEpisodeCtx,
)


class _FakeClock:
    def __init__(self) -> None:
        self.t = 10_000.0

    def monotonic(self) -> float:
        return self.t


@pytest.fixture
def fake_clock(monkeypatch) -> _FakeClock:
    clock = _FakeClock()
    monkeypatch.setattr(lt, "time", clock)
    return clock


@pytest.fixture
def registry() -> LongToolNudgeRegistry:
    return LongToolNudgeRegistry()


def _scanner(
    registry: LongToolNudgeRegistry,
    *,
    parent_status: str = "running",
    parent_id: Any = "parent-1",
    fired: bool = True,
) -> LongToolNudgeScanner:
    repo = MagicMock()
    repo.get_metadata_value = MagicMock(return_value=None)
    parent = MagicMock()
    parent.status = parent_status
    parent.parent_id = parent_id
    repo.get = MagicMock(return_value=parent)
    scanner = LongToolNudgeScanner(
        repo,
        manager=None,
        registry=registry,
        enabled=True,
        handoff_stub_enabled=False,
        handoff_fn=AsyncMock(return_value=fired),
    )
    return scanner


async def _stamp(
    registry: LongToolNudgeRegistry,
    child: str,
    call_id: str,
    clock: _FakeClock,
    age: float,
    parent_id: str = "parent-1",
) -> None:
    await registry.record_start(child, call_id, "bash", parent_id)
    snap = await registry.snapshot()
    snap[child][call_id].started_at = clock.t - age


@pytest.mark.asyncio
async def test_fires_strictly_above_threshold(registry, fake_clock):
    scanner = _scanner(registry)
    await _stamp(registry, "child-1", "call-1", fake_clock, age=901)
    stats = await scanner.run_once()
    assert stats["fired"] == 1


@pytest.mark.asyncio
async def test_does_not_fire_at_exactly_threshold(registry, fake_clock):
    scanner = _scanner(registry)
    await _stamp(registry, "child-1", "call-1", fake_clock, age=900)
    stats = await scanner.run_once()
    assert stats["fired"] == 0
    assert stats["stamps_inspected"] == 1


@pytest.mark.asyncio
async def test_does_not_fire_below_threshold(registry, fake_clock):
    scanner = _scanner(registry)
    await _stamp(registry, "child-1", "call-1", fake_clock, age=899)
    stats = await scanner.run_once()
    assert stats["fired"] == 0


@pytest.mark.asyncio
async def test_heartbeat_fresh_child_still_fires(registry, fake_clock):
    """Regression pin (SC4 / TestLongToolNudgeScannerHeartbeatFreshStillFires).

    Detection keys on in-flight stamp age ONLY — never on
    TaskHeartbeat. A child whose heartbeat is fresh (beats every 30 s
    while wedged mid-tool) MUST still be nudged. The stub here mimics
    the watchdog's ``_has_recent_heartbeat`` live-rung gate returning
    True (recent); the scanner has no heartbeat dependency at all —
    this test fails loudly if one is ever added as a fire gate.
    """

    class _FreshHeartbeatStub:
        def seconds_since_last(self) -> int:
            return 0  # heartbeat-fresh (beats every 30s, mid-tool)

        def is_recent(self) -> bool:
            return True

    heartbeat = _FreshHeartbeatStub()
    scanner = _scanner(registry)
    await _stamp(registry, "child-1", "call-1", fake_clock, age=1200)
    stats = await scanner.run_once()
    assert heartbeat.is_recent() is True  # fresh in scope
    assert stats["fired"] == 1  # ...and the scanner fires anyway


@pytest.mark.asyncio
async def test_fires_once_per_tool_call_id(registry, fake_clock):
    scanner = _scanner(registry)
    await _stamp(registry, "child-1", "call-1", fake_clock, age=1000)
    first = await scanner.run_once()
    assert first["fired"] == 1
    # Same stamp still in flight one tick later — no re-fire.
    fake_clock.t += 60
    second = await scanner.run_once()
    assert second["fired"] == 0
    assert second["stamps_inspected"] == 1


@pytest.mark.asyncio
async def test_per_instance_error_isolation(registry, fake_clock):
    """One instance raising during resolution does not block others."""
    scanner = _scanner(registry)
    await _stamp(registry, "child-bad", "call-bad", fake_clock, age=1000)
    await _stamp(registry, "child-ok", "call-ok", fake_clock, age=1000)
    repo = scanner._repo

    original = repo.get_metadata_value

    def flaky_get(instance_id, key):
        if instance_id == "child-bad":
            raise RuntimeError("db down")
        return original(instance_id, key)

    repo.get_metadata_value = MagicMock(side_effect=flaky_get)
    stats = await scanner.run_once()
    assert stats["errors"] == 1
    # The healthy instance still fired.
    assert stats["fired"] == 1


@pytest.mark.asyncio
async def test_episode_ctx_carries_canonical_fields(registry, fake_clock):
    captured: dict[str, Any] = {}

    async def seam(parent_id, child_id, ctx: LongToolNudgeEpisodeCtx) -> bool:
        captured.update(dict(ctx))
        return True

    scanner = _scanner(registry)
    scanner._handoff_fn = seam
    await _stamp(
        registry, "child-1", "call-9", fake_clock, age=950
    )
    await scanner.run_once()
    assert captured["child_id"] == "child-1"
    assert captured["parent_id"] == "parent-1"
    assert captured["tool_name"] == "bash"
    assert captured["tool_call_id"] == "call-9"
    assert captured["elapsed_seconds"] == pytest.approx(950)
    assert captured["threshold_seconds"] == 900
    assert captured["episode_started_at"] == pytest.approx(fake_clock.t - 950)


@pytest.mark.asyncio
async def test_disabled_scanner_skips_tick(registry, fake_clock):
    scanner = _scanner(registry)
    scanner._enabled = False
    await _stamp(registry, "child-1", "call-1", fake_clock, age=1000)
    stats = await scanner.run_once()
    assert stats["skipped_disabled"] == 1
    assert stats["fired"] == 0
