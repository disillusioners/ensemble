"""T12 — AM-7 stamp-level dedup advance + per-tick threshold memoization.

``TestFiredEpisodesOnlySetOnSuccessfulFire``: the stamp-level
``_fired_episodes`` set advances ONLY when the seam reports a
successful fire. In phase 1 the PAUSED-parent refusal is represented
by the injected seam returning False (phase 2's U5b re-pins this
with the real PAUSED repo status).

``TestResolveThresholdPerTickMemoization``: two stamps on the same
child resolve the threshold via ONE ``get_metadata_value`` call per
tick.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import daemon.services.long_tool_nudge as lt
from daemon.services.long_tool_nudge import (
    LongToolNudgeRegistry,
    LongToolNudgeScanner,
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


def _repo(parent_status: str = "paused"):
    repo = MagicMock()
    repo.get_metadata_value = MagicMock(return_value=None)
    parent = MagicMock()
    parent.status = parent_status
    parent.parent_id = "parent-1"
    repo.get = MagicMock(return_value=parent)
    return repo


async def _stamp(registry, child, call_id, clock, age):
    await registry.record_start(child, call_id, "bash", "parent-1")
    registry._stamps[child][call_id].started_at = clock.t - age


class TestFiredEpisodesOnlySetOnSuccessfulFire:
    @pytest.mark.asyncio
    async def test_refused_fire_does_not_advance_dedup(self, fake_clock):
        registry = LongToolNudgeRegistry()
        repo = _repo(parent_status="paused")  # phase-2: PAUSED parent
        seam = AsyncMock(return_value=False)  # delivery refused
        scanner = LongToolNudgeScanner(
            repo,
            manager=None,
            registry=registry,
            handoff_stub_enabled=False,
            handoff_fn=seam,
        )
        await _stamp(registry, "child-1", "call-1", fake_clock, age=1000)
        stats = await scanner.run_once()
        assert stats["fired"] == 0
        assert ("child-1", "call-1") not in scanner._fired_episodes

    @pytest.mark.asyncio
    async def test_successful_fire_advances_dedup_then_dedups(
        self, fake_clock
    ):
        registry = LongToolNudgeRegistry()
        repo = _repo(parent_status="running")
        seam = AsyncMock(return_value=True)
        scanner = LongToolNudgeScanner(
            repo,
            manager=None,
            registry=registry,
            handoff_stub_enabled=False,
            handoff_fn=seam,
        )
        await _stamp(registry, "child-1", "call-1", fake_clock, age=1000)
        first = await scanner.run_once()
        assert first["fired"] == 1
        assert ("child-1", "call-1") in scanner._fired_episodes
        # Second tick on the SAME in-flight stamp — dedup holds.
        fake_clock.t += 60
        second = await scanner.run_once()
        assert second["fired"] == 0
        assert seam.await_count == 1  # seam not re-called

    @pytest.mark.asyncio
    async def test_refused_then_retry_eligible_on_resume(self, fake_clock):
        """AM-7 clarified semantics: retry-every-tick, fire-on-resume."""
        registry = LongToolNudgeRegistry()
        repo = _repo(parent_status="paused")
        seam = AsyncMock(return_value=False)
        scanner = LongToolNudgeScanner(
            repo,
            manager=None,
            registry=registry,
            handoff_stub_enabled=False,
            handoff_fn=seam,
        )
        await _stamp(registry, "child-1", "call-1", fake_clock, age=1000)
        await scanner.run_once()  # refused (paused)
        # Parent resumed → the seam now succeeds.
        seam = AsyncMock(return_value=True)
        scanner._handoff_fn = seam
        fake_clock.t += 60
        stats = await scanner.run_once()
        assert stats["fired"] == 1  # same crossing fires with fresh numbers
        assert ("child-1", "call-1") in scanner._fired_episodes


class TestResolveThresholdPerTickMemoization:
    @pytest.mark.asyncio
    async def test_one_metadata_read_per_child_per_tick(self, fake_clock):
        registry = LongToolNudgeRegistry()
        repo = _repo(parent_status="running")
        scanner = LongToolNudgeScanner(
            repo,
            manager=None,
            registry=registry,
            handoff_stub_enabled=True,
        )
        await _stamp(registry, "child-1", "call-1", fake_clock, age=1000)
        await _stamp(registry, "child-1", "call-2", fake_clock, age=1001)
        await _stamp(registry, "child-1", "call-3", fake_clock, age=1002)
        stats = await scanner.run_once()
        assert stats["stamps_inspected"] == 3
        assert repo.get_metadata_value.call_count == 1  # memoized per tick
        assert stats["fired"] == 3  # stub seam succeeds per crossing

    @pytest.mark.asyncio
    async def test_distinct_children_each_read_once(self, fake_clock):
        registry = LongToolNudgeRegistry()
        repo = _repo(parent_status="running")
        scanner = LongToolNudgeScanner(
            repo,
            manager=None,
            registry=registry,
            handoff_stub_enabled=True,
        )
        await _stamp(registry, "child-1", "call-1", fake_clock, age=1000)
        await _stamp(registry, "child-2", "call-2", fake_clock, age=1000)
        await scanner.run_once()
        assert repo.get_metadata_value.call_count == 2
