"""Tests for DiscordThreadManager.

Covers TTL-based expiry, LRU eviction at capacity, archive-state
tracking, shutdown lifecycle (idempotency, per-instance failure
isolation), and concurrent guild access.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.sources.adapters.discord.thread_manager import (
    DiscordThreadManager,
    ThreadInstance,
)


# ==================== Fixtures ====================


@pytest.fixture
def mock_manager():
    """Mock InstanceManager whose terminate_instance is async and trackable."""
    m = MagicMock()
    m.terminate_instance = AsyncMock(return_value=None)
    return m


@pytest.fixture
def thread_mgr(mock_manager):
    """Default manager: 24h TTL, 50 threads per guild."""
    return DiscordThreadManager(manager=mock_manager)


# ==================== Construction ====================


class TestConstruction:
    def test_defaults(self, thread_mgr, mock_manager):
        assert thread_mgr._manager is mock_manager
        assert thread_mgr._ttl_seconds == DiscordThreadManager.DEFAULT_TTL_SECONDS
        assert (
            thread_mgr._max_threads
            == DiscordThreadManager.DEFAULT_MAX_THREADS_PER_GUILD
        )
        assert thread_mgr._shutdown_called is False

    def test_custom_ttl_and_max(self, mock_manager):
        mgr = DiscordThreadManager(
            manager=mock_manager, ttl_seconds=60.0, max_threads_per_guild=5
        )
        assert mgr._ttl_seconds == 60.0
        assert mgr._max_threads == 5


# ==================== Register / Get ====================


class TestRegisterGet:
    @pytest.mark.asyncio
    async def test_register_creates_thread(self, thread_mgr, mock_manager):
        thread = await thread_mgr.register_thread(
            guild_id="111", channel_id="222", thread_id="333", instance_id="inst-1"
        )
        assert isinstance(thread, ThreadInstance)
        assert thread.thread_id == "333"
        assert thread.channel_id == "222"
        assert thread.guild_id == "111"
        assert thread.instance_id == "inst-1"
        assert thread.is_archived is False

    @pytest.mark.asyncio
    async def test_register_appears_in_get(self, thread_mgr):
        await thread_mgr.register_thread(
            guild_id="111", channel_id="222", thread_id="333", instance_id="inst-1"
        )
        t = await thread_mgr.get_thread("111", "333")
        assert t is not None
        assert t.thread_id == "333"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, thread_mgr):
        assert await thread_mgr.get_thread("111", "missing") is None

    @pytest.mark.asyncio
    async def test_register_existing_updates_last_accessed(self, thread_mgr):
        t1 = await thread_mgr.register_thread(
            "111", "222", "333", instance_id="inst-1"
        )
        original = t1.last_accessed
        await asyncio.sleep(0.01)
        t2 = await thread_mgr.register_thread(
            "111", "222", "333", instance_id="inst-1"
        )
        assert t2.last_accessed >= original
        # No new entry created.
        assert len(thread_mgr._threads["111"]) == 1

    @pytest.mark.asyncio
    async def test_register_updates_instance_id(self, thread_mgr):
        await thread_mgr.register_thread("111", "222", "333", instance_id="inst-1")
        await thread_mgr.register_thread("111", "222", "333", instance_id="inst-2")
        # Old mapping removed; new one present.
        assert "inst-1" not in thread_mgr._instance_to_thread
        assert thread_mgr._instance_to_thread["inst-2"] == ("111", "333")

    @pytest.mark.asyncio
    async def test_register_lru_move_to_end(self, thread_mgr):
        await thread_mgr.register_thread("111", "222", "t1")
        await thread_mgr.register_thread("111", "222", "t2")
        await thread_mgr.register_thread("111", "222", "t3")
        # Re-touch t1 -> should move to end.
        await thread_mgr.register_thread("111", "222", "t1")
        keys = list(thread_mgr._threads["111"].keys())
        assert keys[-1] == "t1"


# ==================== TTL eviction ====================


class TestTTLEviction:
    @pytest.mark.asyncio
    async def test_expired_thread_returns_none_on_get(self, mock_manager):
        mgr = DiscordThreadManager(manager=mock_manager, ttl_seconds=0.05)
        await mgr.register_thread("111", "222", "333", instance_id="inst-1")
        # Wait past the TTL.
        await asyncio.sleep(0.1)
        t = await mgr.get_thread("111", "333")
        assert t is None

    @pytest.mark.asyncio
    async def test_evict_expired_terminates_instance(self, mock_manager):
        mgr = DiscordThreadManager(manager=mock_manager, ttl_seconds=0.05)
        await mgr.register_thread("111", "222", "333", instance_id="inst-1")
        await asyncio.sleep(0.1)
        evicted = await mgr.evict_expired()
        assert len(evicted) == 1
        mock_manager.terminate_instance.assert_awaited_with("inst-1")

    @pytest.mark.asyncio
    async def test_evict_expired_with_no_expiry(self, thread_mgr):
        await thread_mgr.register_thread("111", "222", "333")
        evicted = await thread_mgr.evict_expired()
        assert evicted == []

    @pytest.mark.asyncio
    async def test_terminate_failure_does_not_abort_sweep(self, mock_manager):
        mock_manager.terminate_instance = AsyncMock(
            side_effect=[Exception("boom"), None]
        )
        mgr = DiscordThreadManager(manager=mock_manager, ttl_seconds=0.05)
        await mgr.register_thread("111", "222", "t1", instance_id="inst-1")
        await mgr.register_thread("111", "222", "t2", instance_id="inst-2")
        await asyncio.sleep(0.1)
        # Both should be evicted; failure of inst-1 must not abort the
        # eviction of inst-2.
        evicted = await mgr.evict_expired()
        assert len(evicted) == 2
        assert mock_manager.terminate_instance.await_count == 2


# ==================== LRU eviction ====================


class TestLRUEviction:
    @pytest.mark.asyncio
    async def test_lru_eviction_at_capacity(self, mock_manager):
        mgr = DiscordThreadManager(
            manager=mock_manager,
            max_threads_per_guild=3,
        )
        await mgr.register_thread("111", "222", "t1", instance_id="i1")
        await mgr.register_thread("111", "222", "t2", instance_id="i2")
        await mgr.register_thread("111", "222", "t3", instance_id="i3")
        # Touch t1 to make it most-recently-used.
        await mgr.register_thread("111", "222", "t1", instance_id="i1")
        # Adding t4 should evict t2 (now the oldest).
        await mgr.register_thread("111", "222", "t4", instance_id="i4")
        keys = list(mgr._threads["111"].keys())
        assert "t2" not in keys
        assert set(keys) == {"t1", "t3", "t4"}


# ==================== Archive tracking ====================


class TestArchiveTracking:
    @pytest.mark.asyncio
    async def test_mark_archived_sets_flag(self, thread_mgr):
        await thread_mgr.register_thread("111", "222", "333")
        await thread_mgr.mark_archived("111", "333", archived=True)
        t = await thread_mgr.get_thread("111", "333")
        assert t.is_archived is True
        assert t.archive_timestamp is not None

    @pytest.mark.asyncio
    async def test_mark_unarchive_clears_flag(self, thread_mgr):
        await thread_mgr.register_thread("111", "222", "333")
        await thread_mgr.mark_archived("111", "333", archived=True)
        await thread_mgr.mark_archived("111", "333", archived=False)
        t = await thread_mgr.get_thread("111", "333")
        assert t.is_archived is False
        assert t.archive_timestamp is None

    @pytest.mark.asyncio
    async def test_mark_archived_missing_thread_no_op(self, thread_mgr):
        # Should not raise.
        await thread_mgr.mark_archived("111", "missing", archived=True)

    @pytest.mark.asyncio
    async def test_mark_archived_idempotent_preserves_timestamp(self, thread_mgr):
        await thread_mgr.register_thread("111", "222", "333")
        await thread_mgr.mark_archived("111", "333", archived=True)
        t1 = await thread_mgr.get_thread("111", "333")
        first_ts = t1.archive_timestamp
        await asyncio.sleep(0.01)
        # Second mark_archived with same state should NOT update timestamp.
        await thread_mgr.mark_archived("111", "333", archived=True)
        t2 = await thread_mgr.get_thread("111", "333")
        assert t2.archive_timestamp == first_ts


# ==================== Shutdown lifecycle ====================


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_terminates_all(self, mock_manager):
        mgr = DiscordThreadManager(manager=mock_manager)
        for i in range(3):
            await mgr.register_thread(
                "111", "222", f"t{i}", instance_id=f"i{i}"
            )
        await mgr.shutdown()
        assert mock_manager.terminate_instance.await_count == 3

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self, mock_manager):
        mgr = DiscordThreadManager(manager=mock_manager)
        await mgr.register_thread("111", "222", "t1", instance_id="i1")
        await mgr.shutdown()
        # Second call should be a no-op (terminate_instance not called again).
        await mgr.shutdown()
        assert mock_manager.terminate_instance.await_count == 1

    @pytest.mark.asyncio
    async def test_shutdown_continues_past_failures(self, mock_manager):
        # First terminate raises, second succeeds.
        mock_manager.terminate_instance = AsyncMock(
            side_effect=[Exception("boom"), None]
        )
        mgr = DiscordThreadManager(manager=mock_manager)
        await mgr.register_thread("111", "222", "t1", instance_id="i1")
        await mgr.register_thread("111", "222", "t2", instance_id="i2")
        await mgr.shutdown()
        assert mock_manager.terminate_instance.await_count == 2

    @pytest.mark.asyncio
    async def test_shutdown_clears_state(self, thread_mgr):
        await thread_mgr.register_thread("111", "222", "t1")
        await thread_mgr.register_thread("222", "333", "t2")
        await thread_mgr.shutdown()
        assert thread_mgr._threads == {}
        assert thread_mgr._guild_locks == {}
        assert thread_mgr._instance_to_thread == {}

    @pytest.mark.asyncio
    async def test_shutdown_handles_threads_without_instance(self, mock_manager):
        mgr = DiscordThreadManager(manager=mock_manager)
        await mgr.register_thread("111", "222", "t1")  # no instance_id
        # Should not raise even with no instance to terminate.
        await mgr.shutdown()
        mock_manager.terminate_instance.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_register_after_shutdown_resets_latch(self, mock_manager):
        """FIX 7 regression: after ``shutdown()``, a subsequent
        ``register_thread()`` (the natural first action of a restart
        cycle) must NOT no-op. Previously ``_shutdown_called`` stayed
        True and any post-shutdown register would silently skip the
        eviction sweep.
        """
        mgr = DiscordThreadManager(manager=mock_manager)
        await mgr.register_thread("111", "222", "t1", instance_id="i1")
        await mgr.shutdown()
        assert mgr._shutdown_called is True
        # Now a fresh register — must work and the latch must re-arm.
        thread = await mgr.register_thread("111", "222", "t2", instance_id="i2")
        assert mgr._shutdown_called is False
        assert thread is not None
        assert thread.thread_id == "t2"
        # And the new thread is reachable.
        assert await mgr.get_thread("111", "t2") is not None

    @pytest.mark.asyncio
    async def test_concurrent_register_same_new_guild_id(self, mock_manager):
        """FIX 8 regression: concurrent ``register_thread`` for the same
        brand-new ``guild_id`` must serialize on ``_guilds_guard`` so
        the dict assignment is race-free.
        """
        mgr = DiscordThreadManager(manager=mock_manager)
        async def reg(i):
            await mgr.register_thread(
                "brand-new", "222", f"t{i}", instance_id=f"i{i}"
            )
        await asyncio.gather(*[reg(i) for i in range(20)])
        # Exactly one OrderedDict was created, with 20 entries — not 20
        # clobbering OrderedDict instances.
        assert len(mgr._threads) == 1
        assert len(mgr._threads["brand-new"]) == 20


# ==================== Concurrent guild access ====================


class TestConcurrentGuildAccess:
    @pytest.mark.asyncio
    async def test_concurrent_registers_across_guilds(self, thread_mgr):
        async def reg(guild, tid):
            await thread_mgr.register_thread(guild, "ch", tid)

        # 10 guilds * 10 threads each, in parallel.
        tasks = []
        for g in range(10):
            for t in range(10):
                tasks.append(reg(f"g{g}", f"t{t}"))
        await asyncio.gather(*tasks)

        assert len(thread_mgr._threads) == 10
        for g in range(10):
            assert len(thread_mgr._threads[f"g{g}"]) == 10

    @pytest.mark.asyncio
    async def test_concurrent_register_same_thread(self, thread_mgr):
        """Multiple coros registering the same thread — should converge."""
        async def reg():
            await thread_mgr.register_thread("111", "222", "333", instance_id="i1")

        await asyncio.gather(*[reg() for _ in range(20)])
        assert len(thread_mgr._threads["111"]) == 1
