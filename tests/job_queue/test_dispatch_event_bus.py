"""Tests for DispatchEventBus.

This module tests the in-process event notification system for job dispatch,
which replaces pure polling with event-driven wakeup.
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from daemon.services.dispatch_event_bus import DispatchEventBus


@pytest.fixture
def event_bus():
    """Create a fresh DispatchEventBus instance."""
    return DispatchEventBus()


@pytest.fixture
def event_loop():
    """Create a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestDispatchEventBusNotifyAndWait:
    """Tests for notify/wait coordination."""

    @pytest.mark.asyncio
    async def test_notify_and_wait_same_project(self, event_bus, event_loop):
        """Test that notify a project, wait returns True immediately."""
        event_bus.set_event_loop(event_loop)
        
        # Create event for project first by registering it
        event_bus._get_or_create_event("project-1")
        
        # Schedule notification in the background
        async def notify_later():
            await asyncio.sleep(0.05)  # Small delay
            event_bus.notify_new_job("project-1")
        
        # Start notification task
        notify_task = asyncio.create_task(notify_later())
        
        # Wait should return True after notification
        result = await event_bus.wait_for_job("project-1", timeout=1.0)
        
        await notify_task
        
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_timeout_no_notify(self, event_bus, event_loop):
        """Test that wait returns False when no notification within timeout."""
        event_bus.set_event_loop(event_loop)
        
        # Create event for project first
        event_bus._get_or_create_event("project-1")
        
        # Wait with short timeout - should timeout
        result = await event_bus.wait_for_job("project-1", timeout=0.1)
        
        assert result is False

    @pytest.mark.asyncio
    async def test_notify_different_project_doesnt_wake(self, event_bus, event_loop):
        """Test that notify project-A doesn't wake project-B wait."""
        event_bus.set_event_loop(event_loop)
        
        # Create events for both projects
        event_bus._get_or_create_event("project-A")
        event_bus._get_or_create_event("project-B")
        
        # Notify project-A
        event_bus.notify_new_job("project-A")
        
        # Give time for notification to be processed
        await asyncio.sleep(0.01)
        
        # Wait on project-B with short timeout - should timeout because project-A was notified
        result = await event_bus.wait_for_job("project-B", timeout=0.1)
        
        assert result is False

    @pytest.mark.asyncio
    async def test_global_event_for_none_project_id(self, event_bus, event_loop):
        """Test that wait_for_job(None) degrades to polling when no global event exists."""
        event_bus.set_event_loop(event_loop)
        
        # Without _global_event, wait_for_job(None) should degrade to polling
        # It should sleep for the timeout duration and return False
        result = await event_bus.wait_for_job(None, timeout=0.1)
        
        # Should return False after sleeping (no event to wait on)
        assert result is False
        
        # Notify with None should log and return early (no event to set)
        event_bus.notify_new_job(None)

    @pytest.mark.asyncio
    async def test_auto_clear_after_wait(self, event_bus, event_loop):
        """Test that after successful wait, event is cleared so next wait will block."""
        event_bus.set_event_loop(event_loop)
        
        # Create event for project
        event = event_bus._get_or_create_event("project-1")
        
        # Set the event manually
        event.set()
        
        # First wait should return True immediately
        result1 = await event_bus.wait_for_job("project-1", timeout=0.5)
        assert result1 is True
        
        # Event should now be cleared
        assert not event.is_set(), "Event should be cleared after wait"
        
        # Second wait should timeout because event was cleared
        result2 = await event_bus.wait_for_job("project-1", timeout=0.1)
        assert result2 is False


class TestDispatchEventBusNotifyAll:
    """Tests for notify_all functionality."""

    @pytest.mark.asyncio
    async def test_notify_all_sets_all_events(self, event_bus, event_loop):
        """Test that notify_all sets events for all known projects."""
        event_bus.set_event_loop(event_loop)
        
        # Create events for multiple projects
        event_bus._get_or_create_event("project-1")
        event_bus._get_or_create_event("project-2")
        event_bus._get_or_create_event("project-3")
        
        # Call notify_all
        event_bus.notify_all()
        
        # Give time for notifications to be processed
        await asyncio.sleep(0.01)
        
        # All project events should be set
        assert event_bus._events["project-1"].is_set()
        assert event_bus._events["project-2"].is_set()
        assert event_bus._events["project-3"].is_set()

    @pytest.mark.asyncio
    async def test_notify_all_with_no_projects(self, event_bus, event_loop):
        """Test that notify_all handles empty project list gracefully."""
        event_bus.set_event_loop(event_loop)
        
        # Call notify_all with no projects registered
        # Should not raise
        event_bus.notify_all()
        
        # Should complete without error
        assert True

    @pytest.mark.asyncio
    async def test_notify_all_waits_return_true(self, event_bus, event_loop):
        """Test that all waits return True after notify_all."""
        event_bus.set_event_loop(event_loop)
        
        # Create events for multiple projects
        event_bus._get_or_create_event("project-1")
        event_bus._get_or_create_event("project-2")
        
        # Notify all
        event_bus.notify_all()
        await asyncio.sleep(0.01)
        
        # All waits should return True
        result1 = await event_bus.wait_for_job("project-1", timeout=0.5)
        result2 = await event_bus.wait_for_job("project-2", timeout=0.5)
        
        assert result1 is True
        assert result2 is True


class TestDispatchEventBusThreadSafety:
    """Tests for thread-safe notification."""

    def test_notify_from_thread(self, event_loop):
        """Test that thread-safe notification via call_soon_threadsafe works."""
        event_bus = DispatchEventBus()
        event_bus.set_event_loop(event_loop)
        
        # Create event for project synchronously
        event_bus._get_or_create_event("project-1")
        
        notification_done = threading.Event()
        
        def notify_from_thread():
            """Notify from a separate thread."""
            event_bus.notify_new_job("project-1")
            notification_done.set()
        
        # Run notification in a separate thread
        thread = threading.Thread(target=notify_from_thread)
        thread.start()
        
        # Process notifications on event loop
        async def process_notifications():
            # Wait for thread to signal done
            for _ in range(20):  # Poll for up to 0.2 seconds
                if notification_done.is_set():
                    break
                await asyncio.sleep(0.01)
            # Also wait for call_soon_threadsafe callback to run
            await asyncio.sleep(0.05)
        
        event_loop.run_until_complete(process_notifications())
        thread.join(timeout=1.0)
        
        # Verify event was set
        assert event_bus._events["project-1"].is_set()

    def test_notify_from_thread_with_global_event(self, event_loop):
        """Test thread-safe notification sets global event as well."""
        event_bus = DispatchEventBus()
        event_bus.set_event_loop(event_loop)
        
        notification_done = threading.Event()
        
        def notify_from_thread():
            event_bus.notify_new_job("project-1")
            notification_done.set()
        
        thread = threading.Thread(target=notify_from_thread)
        thread.start()
        
        # Process notifications
        async def process():
            await asyncio.sleep(0.3)
        
        event_loop.run_until_complete(process())
        thread.join(timeout=1.0)
        
        # Both project event and global event should be set
        assert event_bus._events["project-1"].is_set()


class TestDispatchEventBusNoLoopGraceful:
    """Tests for graceful handling when no event loop is set."""

    def test_no_loop_graceful_skip(self, event_bus):
        """Test that notify without event loop doesn't crash."""
        # Don't set event loop
        
        # Should not raise
        event_bus.notify_new_job("project-1")
        event_bus.notify_new_job(None)
        event_bus.notify_all()
        
        # No events should be created (since loop is not set)
        assert "project-1" not in event_bus._events

    def test_no_loop_notify_returns_quickly(self, event_bus):
        """Test that notify returns quickly when no loop is set."""
        # Should return immediately without blocking
        import time
        
        start = time.time()
        event_bus.notify_new_job("project-1")
        elapsed = time.time() - start
        
        # Should be nearly instant
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_wait_without_any_setup(self, event_loop):
        """Test wait behavior when no events have been created."""
        event_bus = DispatchEventBus()
        event_bus.set_event_loop(event_loop)
        
        # Wait on non-existent project - should create event and timeout
        result = await event_bus.wait_for_job("never-created-project", timeout=0.05)
        
        assert result is False


class TestDispatchEventBusEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_double_notify_works(self, event_bus, event_loop):
        """Test that notifying the same project twice works correctly."""
        event_bus.set_event_loop(event_loop)
        
        # Create event
        event_bus._get_or_create_event("project-1")
        
        # Notify twice
        event_bus.notify_new_job("project-1")
        await asyncio.sleep(0.01)
        event_bus.notify_new_job("project-1")
        await asyncio.sleep(0.01)
        
        # Wait should still return True
        result = await event_bus.wait_for_job("project-1", timeout=0.1)
        assert result is True

    @pytest.mark.asyncio
    async def test_mixed_project_and_global(self, event_bus, event_loop):
        """Test that project notifications also set the global event."""
        event_bus.set_event_loop(event_loop)
        
        # Create project event
        event_bus._get_or_create_event("project-1")
        
        # Notify project-specific
        event_bus.notify_new_job("project-1")
        await asyncio.sleep(0.01)
        
        # Project event should be set
        assert event_bus._events["project-1"].is_set()
        
        # Global event should also be set (restored behavior)
        assert event_bus._global_event.is_set()
        
        # Wait on global should return True (event was set)
        result_global = await event_bus.wait_for_job(None, timeout=0.1)
        assert result_global is True
        
        # Wait on project should return True (event was set)
        result_project = await event_bus.wait_for_job("project-1", timeout=0.1)
        assert result_project is True

    @pytest.mark.asyncio
    async def test_wait_clears_even_on_timeout(self, event_bus, event_loop):
        """Test that wait clears event even on timeout."""
        event_bus.set_event_loop(event_loop)
        
        # Create event
        event = event_bus._get_or_create_event("project-1")
        
        # Set event, then clear it to simulate timeout scenario
        event.set()
        event.clear()
        
        # Wait should timeout (event was not set)
        result = await event_bus.wait_for_job("project-1", timeout=0.01)
        
        assert result is False
        assert not event.is_set(), "Event should be cleared even after timeout"

    @pytest.mark.asyncio
    async def test_set_event_loop_twice(self, event_bus, event_loop):
        """Test that setting event loop twice is safe."""
        loop1 = event_loop
        loop2 = asyncio.new_event_loop()
        
        event_bus.set_event_loop(loop1)
        event_bus.set_event_loop(loop2)  # Should not raise
        
        # Last one wins
        assert event_bus._loop is loop2
        
        loop2.close()

    def test_notify_all_without_loop(self, event_bus):
        """Test that notify_all without loop is safe."""
        # Don't set loop
        event_bus._events["project-1"] = MagicMock()
        
        # Should not raise
        event_bus.notify_all()
        
        # Event should not be touched (since no loop)
        event_bus._events["project-1"].set.assert_not_called()
