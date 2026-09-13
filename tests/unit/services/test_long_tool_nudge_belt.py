"""T9 — AD-9a stamp-TTL belt + B4 orphan hygiene tests.

Pins: stale-stamp force-clear at age > 7200 s (7199 s does NOT
trigger), episode close + dedup re-arm + WARN on the belt, a fresh
tool_call_id on the same child firing normally afterwards, orphan
``_active_episodes`` survives the wrapper's inter-batch gap (council
fix-cycle 1, C1), orphan ``_active_episodes`` is eventually discarded
once untouched for ``STALE_STAMP_TTL_SECONDS``, and the 7-key
``run_once`` stats shape.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import daemon.services.long_tool_nudge as lt
from daemon.services.long_tool_nudge import (
    STALE_STAMP_TTL_SECONDS,
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


def _scanner(registry: LongToolNudgeRegistry, fired: bool = True):
    repo = MagicMock()
    repo.get_metadata_value = MagicMock(return_value=None)
    parent = MagicMock()
    parent.status = "running"
    parent.parent_id = "parent-1"
    repo.get = MagicMock(return_value=parent)
    return LongToolNudgeScanner(
        repo,
        manager=None,
        registry=registry,
        handoff_stub_enabled=False,
        handoff_fn=AsyncMock(return_value=fired),
    )


async def _stamp(registry, child, call_id, clock, age):
    await registry.record_start(child, call_id, "bash", "parent-1")
    registry._stamps[child][call_id].started_at = clock.t - age


class TestTickStatsShape:
    @pytest.mark.asyncio
    async def test_seven_expected_keys(self, fake_clock):
        registry = LongToolNudgeRegistry()
        scanner = _scanner(registry)
        stats = await scanner.run_once()
        assert set(stats.keys()) == {
            "instances_scanned",
            "stamps_inspected",
            "fired",
            "skipped_disabled",
            "errors",
            "stale_stamps_force_cleared",
            "orphan_episodes_discarded",
        }
        assert all(isinstance(v, int) for v in stats.values())


class TestScannerStaleStampForceCloses:
    @pytest.mark.asyncio
    async def test_belt_force_clears_closes_rearms_and_warns(
        self, fake_clock, caplog
    ):
        registry = LongToolNudgeRegistry()
        scanner = _scanner(registry)
        # Episode opened by a first (stale-aged) crossing.
        await _stamp(registry, "child-1", "call-old", fake_clock, age=7300)
        scanner._fired_episodes.add(("child-1", "call-old"))
        scanner._active_episodes.add(("parent-1", "child-1"))
        with caplog.at_level("WARNING", logger="daemon.services.long_tool_nudge"):
            stats = await scanner.run_once()
        assert stats["stale_stamps_force_cleared"] == 1
        assert await registry.snapshot() == {}  # stamp force-cleared
        assert ("parent-1", "child-1") not in scanner._active_episodes
        assert ("child-1", "call-old") not in scanner._fired_episodes
        assert any("STALE_STAMP force-cleared" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_belt_rearms_fresh_call_fires_normally(self, fake_clock):
        registry = LongToolNudgeRegistry()
        scanner = _scanner(registry)
        await _stamp(registry, "child-1", "call-old", fake_clock, age=7300)
        scanner._fired_episodes.add(("child-1", "call-old"))
        await scanner.run_once()  # belt clears + re-arms
        # A FRESH tool_call_id on the same child fires normally.
        await _stamp(registry, "child-1", "call-new", fake_clock, age=1000)
        stats = await scanner.run_once()
        assert stats["fired"] == 1

    @pytest.mark.asyncio
    async def test_at_and_below_ttl_does_not_trigger_belt(self, fake_clock):
        registry = LongToolNudgeRegistry()
        scanner = _scanner(registry)
        await _stamp(registry, "child-1", "call-edge", fake_clock, age=7199)
        stats = await scanner.run_once()
        assert stats["stale_stamps_force_cleared"] == 0
        assert (await registry.snapshot()).get("child-1", {}).get("call-edge") is not None
        # At exactly TTL: not over → no force-clear.
        registry2 = LongToolNudgeRegistry()
        scanner2 = _scanner(registry2)
        await _stamp(registry2, "child-1", "call-ttl", fake_clock, age=STALE_STAMP_TTL_SECONDS)
        stats2 = await scanner2.run_once()
        assert stats2["stale_stamps_force_cleared"] == 0


class TestScannerOrphanEpisodeClose:
    @pytest.mark.asyncio
    async def test_orphan_active_episode_survives_inter_batch_gap(
        self, fake_clock
    ):
        """Council fix-cycle 1, C1 — REGRESSION PIN (replaces the
        pre-cycle ``test_orphan_active_episode_discarded``).

        An episode whose child has no live stamps on this tick MUST
        NOT be discarded — the wrapper's ``finally`` clears stamps
        between batches, so a 60s tick landing in the inter-batch
        gap must not re-arm the next long call (bd4b36ef replay
        must yield exactly 1 nudge, not 5). The episode survives
        until its ``_episode_last_seen`` anchor ages past
        ``STALE_STAMP_TTL_SECONDS``.
        """
        registry = LongToolNudgeRegistry()
        scanner = _scanner(registry)
        # Seed an OPEN episode with no live stamps — emulates the
        # scanner tick landing in the wrapper's inter-batch gap.
        scanner._active_episodes.add(("parent-1", "child-gone"))
        scanner._episode_last_seen[("parent-1", "child-gone")] = (
            fake_clock.t
        )
        stats = await scanner.run_once()
        # Episode survives the gap; orphan counter does NOT advance.
        assert ("parent-1", "child-gone") in scanner._active_episodes
        assert stats["orphan_episodes_discarded"] == 0

    @pytest.mark.asyncio
    async def test_orphan_active_episode_discarded_after_ttl(
        self, fake_clock
    ):
        """Council fix-cycle 1, C1 — orphan IS eventually discarded
        once untouched for ``STALE_STAMP_TTL_SECONDS``.

        Pairs with the survival pin above: the TTL gate is what
        keeps the wrapper's inter-batch gap from prematurely
        discarding an OPEN episode, while still pruning truly
        abandoned episodes (e.g., parent long-deleted via the
        ``clear_for_instance`` or any other path the wrapper's
        close-gate would have caught).
        """
        registry = LongToolNudgeRegistry()
        scanner = _scanner(registry)
        scanner._active_episodes.add(("parent-1", "child-gone"))
        # Episode last-seen well outside the TTL window.
        scanner._episode_last_seen[("parent-1", "child-gone")] = (
            fake_clock.t - STALE_STAMP_TTL_SECONDS - 1
        )
        stats = await scanner.run_once()
        assert ("parent-1", "child-gone") not in scanner._active_episodes
        assert ("parent-1", "child-gone") not in scanner._episode_last_seen
        assert stats["orphan_episodes_discarded"] == 1

    @pytest.mark.asyncio
    async def test_live_episode_not_discarded(self, fake_clock):
        registry = LongToolNudgeRegistry()
        scanner = _scanner(registry)
        await _stamp(registry, "child-1", "call-1", fake_clock, age=10)
        scanner._active_episodes.add(("parent-1", "child-1"))
        scanner._episode_last_seen[("parent-1", "child-1")] = (
            fake_clock.t
        )
        stats = await scanner.run_once()
        assert ("parent-1", "child-1") in scanner._active_episodes
        # The live stamp refreshes the TTL anchor — the anchor age
        # after the sweep must be ≈ now (the refresh ran in the
        # scan loop, before the orphan sweep).
        assert scanner._episode_last_seen[("parent-1", "child-1")] == (
            pytest.approx(fake_clock.t)
        )
        assert stats["orphan_episodes_discarded"] == 0

    @pytest.mark.asyncio
    async def test_episode_anchor_refreshed_when_child_has_live_stamps(
        self, fake_clock
    ):
        """A tick where the child has stamps (even below threshold)
        refreshes the episode TTL anchor — the inter-batch survival
        pin relies on this anchor being current."""
        registry = LongToolNudgeRegistry()
        scanner = _scanner(registry)
        scanner._active_episodes.add(("parent-1", "child-1"))
        scanner._episode_last_seen[("parent-1", "child-1")] = (
            fake_clock.t - 5000
        )
        # Live stamp BELOW threshold (no fire) — but anchor must still
        # refresh because the child is in the snapshot.
        await _stamp(registry, "child-1", "call-1", fake_clock, age=10)
        await scanner.run_once()
        assert scanner._episode_last_seen[("parent-1", "child-1")] == (
            pytest.approx(fake_clock.t)
        )


class TestScannerFiredEpisodesHygieneDiscard:
    @pytest.mark.asyncio
    async def test_orphan_fired_entry_discarded(self, fake_clock):
        registry = LongToolNudgeRegistry()
        scanner = _scanner(registry)
        scanner._fired_episodes.add(("child-gone", "call-gone"))
        await scanner.run_once()
        assert ("child-gone", "call-gone") not in scanner._fired_episodes

    @pytest.mark.asyncio
    async def test_live_fired_entry_kept(self, fake_clock):
        registry = LongToolNudgeRegistry()
        scanner = _scanner(registry)
        await _stamp(registry, "child-1", "call-1", fake_clock, age=1000)
        scanner._fired_episodes.add(("child-1", "call-1"))
        await scanner.run_once()
        assert ("child-1", "call-1") in scanner._fired_episodes
