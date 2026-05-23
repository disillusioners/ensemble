"""Unit tests for LiveEventHub service."""

import pytest
import asyncio

from daemon.services.live_event_hub import LiveEventHub


# ============================================================================
# Test Connection Management
# ============================================================================


class TestConnectionManagement:
    """Tests for connection management operations."""

    @pytest.mark.asyncio
    async def test_add_connection(self):
        """Adding a connection registers it for an instance."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)

        count = await hub.get_connection_count("instance-1")
        assert count == 1

    @pytest.mark.asyncio
    async def test_remove_connection(self):
        """Removing a connection unregisters it."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        assert await hub.get_connection_count("instance-1") == 1

        await hub.remove_connection("instance-1", queue)
        assert await hub.get_connection_count("instance-1") == 0

    @pytest.mark.asyncio
    async def test_remove_nonexistent_connection(self):
        """Removing a non-existent connection does not raise."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        # Should not raise
        await hub.remove_connection("instance-1", queue)
        assert await hub.get_connection_count("instance-1") == 0

    @pytest.mark.asyncio
    async def test_multiple_connections_per_instance(self):
        """Multiple connections can be registered for the same instance."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        queue3 = asyncio.Queue()

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-1", queue2)
        await hub.add_connection("instance-1", queue3)

        assert await hub.get_connection_count("instance-1") == 3

    @pytest.mark.asyncio
    async def test_add_same_queue_twice(self):
        """Adding the same queue twice results in only one connection."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.add_connection("instance-1", queue)

        # set() deduplicates, so count should be 1
        assert await hub.get_connection_count("instance-1") == 1

    @pytest.mark.asyncio
    async def test_remove_one_of_multiple_connections(self):
        """Removing one connection leaves others intact."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-1", queue2)

        await hub.remove_connection("instance-1", queue1)

        assert await hub.get_connection_count("instance-1") == 1

    @pytest.mark.asyncio
    async def test_get_connection_count_nonexistent_instance(self):
        """Count for non-existent instance returns 0."""
        hub = LiveEventHub()

        count = await hub.get_connection_count("nonexistent")
        assert count == 0

    @pytest.mark.asyncio
    async def test_connections_across_multiple_instances(self):
        """Connections are properly isolated between instances."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-2", queue2)

        assert await hub.get_connection_count("instance-1") == 1
        assert await hub.get_connection_count("instance-2") == 1

    @pytest.mark.asyncio
    async def test_instance_removed_after_last_connection(self):
        """Instance is removed from registry when last connection is removed."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        assert await hub.get_connection_count("instance-1") == 1

        await hub.remove_connection("instance-1", queue)
        assert await hub.get_connection_count("instance-1") == 0


# ============================================================================
# Test Event Streaming
# ============================================================================


class TestEventStreaming:
    """Tests for event streaming to connections."""

    @pytest.mark.asyncio
    async def test_stream_message_delivered(self):
        """Message events are delivered to registered connections."""
        hub = LiveEventHub()
        queue = asyncio.Queue()
        message = {"content": "Hello", "role": "user"}

        await hub.add_connection("instance-1", queue)
        await hub.stream_message("instance-1", message, event_type="message")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == "instance-1"
        assert event["event_type"] == "message"
        assert event["message"] == message

    @pytest.mark.asyncio
    async def test_event_dropped_when_no_connections(self):
        """Events are silently dropped when no connections exist."""
        hub = LiveEventHub()
        message = {"content": "test", "role": "user"}

        # Should not raise
        await hub.stream_message("instance-1", message, event_type="message")

    @pytest.mark.asyncio
    async def test_multiple_connections_receive_same_event(self):
        """All connections receive the same event."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        message = {"content": "Broadcast", "role": "assistant"}

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-1", queue2)
        await hub.stream_message("instance-1", message, event_type="message")

        event1 = await asyncio.wait_for(queue1.get(), timeout=1.0)
        event2 = await asyncio.wait_for(queue2.get(), timeout=1.0)

        assert event1 == event2
        assert event1["message"] == message

    @pytest.mark.asyncio
    async def test_stream_to_multiple_instances(self):
        """Events are only sent to the correct instance."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        message = {"content": "For instance 1", "role": "user"}

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-2", queue2)
        await hub.stream_message("instance-1", message, event_type="message")

        event = await asyncio.wait_for(queue1.get(), timeout=1.0)
        assert event["instance_id"] == "instance-1"
        assert event["message"] == message

        # Queue2 should be empty
        assert queue2.empty()

    @pytest.mark.asyncio
    async def test_stream_checkpoint(self):
        """Checkpoint events are delivered with correct structure."""
        hub = LiveEventHub()
        queue = asyncio.Queue()
        messages = [
            {"message_id": "msg-1", "role": "user", "content": "Hello"},
            {"message_id": "msg-2", "role": "assistant", "content": "Hi"},
        ]

        await hub.add_connection("instance-1", queue)
        await hub.stream_checkpoint(
            "instance-1",
            messages=messages,
            checkpoint_id="seq_0",
            tool_outputs={"tool-1": "result"},
        )

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == "instance-1"
        assert event["event_type"] == "checkpoint"
        assert event["checkpoint_id"] == "seq_0"
        assert event["messages"] == messages
        assert event["tool_outputs"]["tool-1"] == "result"

    @pytest.mark.asyncio
    async def test_stream_checkpoint_empty_messages_skipped(self):
        """Checkpoint with empty messages is not sent."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.stream_checkpoint("instance-1", messages=[], checkpoint_id="seq_0")

        # Queue should remain empty
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_event_structure_preserved(self):
        """Event structure is correctly preserved during streaming."""
        hub = LiveEventHub()
        queue = asyncio.Queue()
        message = {
            "message_id": "msg-123",
            "role": "assistant",
            "content": "Test content",
            "tool_calls": [
                {"id": "call-1", "name": "bash", "arguments": '{"cmd": "ls"}'}
            ],
        }

        await hub.add_connection("instance-1", queue)
        await hub.stream_message(
            "instance-1",
            message,
            event_type="message",
            checkpoint_id="seq_5",
        )

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["event_id"] == "msg-123"
        assert event["checkpoint_id"] == "seq_5"
        assert event["message"]["tool_calls"][0]["name"] == "bash"


# ============================================================================
# Test Queue Full Handling
# ============================================================================


class TestQueueFullHandling:
    """Tests for handling of full queues (dead connections)."""

    @pytest.mark.asyncio
    async def test_queue_full_removes_dead_connection(self):
        """Full queue triggers connection cleanup."""
        hub = LiveEventHub(max_queue_size=1)
        queue = asyncio.Queue(maxsize=1)

        await hub.add_connection("instance-1", queue)
        assert await hub.get_connection_count("instance-1") == 1

        # Fill the queue
        await queue.put("dummy")

        # Stream should mark queue as dead and remove it
        await hub.stream_message("instance-1", {"content": "test"}, event_type="message")

        # Connection should be removed
        assert await hub.get_connection_count("instance-1") == 0

    @pytest.mark.asyncio
    async def test_other_connections_survive_queue_full(self):
        """Other connections are not affected when one queue is full."""
        hub = LiveEventHub(max_queue_size=1)
        queue1 = asyncio.Queue(maxsize=1)
        queue2 = asyncio.Queue(maxsize=10)

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-1", queue2)

        # Fill queue1
        await queue1.put("dummy")

        # Stream should remove queue1, keep queue2
        await hub.stream_message("instance-1", {"content": "test"}, event_type="message")

        assert await hub.get_connection_count("instance-1") == 1

        # Queue2 should still receive the event
        event = await asyncio.wait_for(queue2.get(), timeout=1.0)
        assert event["message"]["content"] == "test"

    @pytest.mark.asyncio
    async def test_multiple_queues_full(self):
        """Multiple full queues are all removed."""
        hub = LiveEventHub(max_queue_size=1)
        queue1 = asyncio.Queue(maxsize=1)
        queue2 = asyncio.Queue(maxsize=1)
        queue3 = asyncio.Queue(maxsize=10)

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-1", queue2)
        await hub.add_connection("instance-1", queue3)

        # Fill queue1 and queue2
        await queue1.put("dummy")
        await queue2.put("dummy")

        # Stream should remove both full queues
        await hub.stream_message("instance-1", {"content": "test"}, event_type="message")

        assert await hub.get_connection_count("instance-1") == 1

        # Queue3 should still receive the event
        event = await asyncio.wait_for(queue3.get(), timeout=1.0)
        assert event["message"]["content"] == "test"


# ============================================================================
# Test Lifecycle Events
# ============================================================================


class TestLifecycleEvents:
    """Tests for lifecycle event streaming."""

    @pytest.mark.asyncio
    async def test_stream_error(self):
        """Error events are delivered with correct structure."""
        hub = LiveEventHub()
        queue = asyncio.Queue()
        error_data = {"code": "LLM_ERROR", "message": "API failed"}

        await hub.add_connection("instance-1", queue)
        await hub.stream_error("instance-1", error=error_data)

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == "instance-1"
        assert event["event_type"] == "error"
        assert event["error"] == error_data

    @pytest.mark.asyncio
    async def test_stream_error_none(self):
        """Error events with no error data are still delivered."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.stream_error("instance-1", error=None)

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == "instance-1"
        assert event["event_type"] == "error"
        assert event["error"] is None

    @pytest.mark.asyncio
    async def test_stream_lifecycle(self):
        """Lifecycle events are delivered with correct structure."""
        hub = LiveEventHub()
        queue = asyncio.Queue()
        data = {"status": "completed", "duration": 123}

        await hub.add_connection("instance-1", queue)
        await hub.stream_lifecycle("instance-1", event_type="completed", data=data)

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == "instance-1"
        assert event["event_type"] == "completed"
        assert event["data"] == data

    @pytest.mark.asyncio
    async def test_stream_lifecycle_no_data(self):
        """Lifecycle events without data are still delivered."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.stream_lifecycle("instance-1", event_type="started")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == "instance-1"
        assert event["event_type"] == "started"
        assert "data" not in event

    @pytest.mark.asyncio
    async def test_lifecycle_events_to_multiple_connections(self):
        """Lifecycle events reach all registered connections."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-1", queue2)
        await hub.stream_lifecycle("instance-1", event_type="completed")

        event1 = await asyncio.wait_for(queue1.get(), timeout=1.0)
        event2 = await asyncio.wait_for(queue2.get(), timeout=1.0)

        assert event1["event_type"] == "completed"
        assert event2["event_type"] == "completed"

    @pytest.mark.asyncio
    async def test_stream_status_change(self):
        """Status change events are delivered with correct structure."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.stream_status_change("instance-1", status="running")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == "instance-1"
        assert event["event_type"] == "status_change"
        assert event["status"] == "running"

    @pytest.mark.asyncio
    async def test_stream_status_change_terminated(self):
        """Status change to terminated is delivered correctly."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.stream_status_change("instance-1", status="terminated")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == "instance-1"
        assert event["event_type"] == "status_change"
        assert event["status"] == "terminated"

    @pytest.mark.asyncio
    async def test_stream_status_change_idle(self):
        """Status change to idle is delivered correctly."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.stream_status_change("instance-1", status="idle")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == "instance-1"
        assert event["event_type"] == "status_change"
        assert event["status"] == "idle"

    @pytest.mark.asyncio
    async def test_stream_status_change_to_multiple_connections(self):
        """Status change events reach all registered connections."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-1", queue2)
        await hub.stream_status_change("instance-1", status="running")

        event1 = await asyncio.wait_for(queue1.get(), timeout=1.0)
        event2 = await asyncio.wait_for(queue2.get(), timeout=1.0)

        assert event1["status"] == "running"
        assert event2["status"] == "running"
        assert event1["event_type"] == "status_change"
        assert event2["event_type"] == "status_change"


# ============================================================================
# Test KB Agent Filtering
# ============================================================================


class TestKBAgentFiltering:
    """Tests for KB agent ID filtering in status change events.

    KB agents (experiencer, kb-importer) should not broadcast status changes
    to avoid polluting SSE with internal agent events.
    """

    @pytest.mark.asyncio
    async def test_stream_status_change_experiencer_filtered(self):
        """Status change for 'experiencer' agent is not broadcast."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.stream_status_change("instance-1", status="running", agent_id="experiencer")

        # Queue should remain empty - no event delivered
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_stream_status_change_kb_importer_filtered(self):
        """Status change for 'kb-importer' agent is not broadcast."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.stream_status_change("instance-1", status="running", agent_id="kb-importer")

        # Queue should remain empty - no event delivered
        assert queue.empty()

    @pytest.mark.asyncio
    async def test_stream_status_change_none_agent_broadcasts(self):
        """Status change with agent_id=None is still broadcast."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.stream_status_change("instance-1", status="running", agent_id=None)

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == "instance-1"
        assert event["event_type"] == "status_change"
        assert event["status"] == "running"

    @pytest.mark.asyncio
    async def test_stream_status_change_other_agent_broadcasts(self):
        """Status change for non-KB agents is broadcast normally."""
        hub = LiveEventHub()
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.stream_status_change("instance-1", status="running", agent_id="other-agent")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == "instance-1"
        assert event["event_type"] == "status_change"
        assert event["status"] == "running"
        assert event["agent_id"] == "other-agent"

    @pytest.mark.asyncio
    async def test_stream_status_change_multiple_connections_kb_filtered(self):
        """KB agent filtering works with multiple registered connections."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-1", queue2)
        await hub.stream_status_change("instance-1", status="running", agent_id="experiencer")

        # Both queues should remain empty
        assert queue1.empty()
        assert queue2.empty()


# ============================================================================
# Test Cleanup
# ============================================================================


class TestCleanup:
    """Tests for cleanup operations."""

    @pytest.mark.asyncio
    async def test_cleanup_instance(self):
        """cleanup_instance removes all connections for an instance."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-1", queue2)
        assert await hub.get_connection_count("instance-1") == 2

        await hub.cleanup_instance("instance-1")

        assert await hub.get_connection_count("instance-1") == 0

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_instance(self):
        """Cleaning up non-existent instance does not raise."""
        hub = LiveEventHub()

        # Should not raise
        await hub.cleanup_instance("nonexistent")

    @pytest.mark.asyncio
    async def test_cleanup_only_affects_target_instance(self):
        """Cleaning up one instance does not affect others."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-2", queue2)

        await hub.cleanup_instance("instance-1")

        assert await hub.get_connection_count("instance-1") == 0
        assert await hub.get_connection_count("instance-2") == 1

    @pytest.mark.asyncio
    async def test_shutdown_clears_all_connections(self):
        """shutdown removes all connections across all instances."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        queue3 = asyncio.Queue()

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-1", queue2)
        await hub.add_connection("instance-2", queue3)

        await hub.shutdown()

        assert await hub.get_connection_count("instance-1") == 0
        assert await hub.get_connection_count("instance-2") == 0

    @pytest.mark.asyncio
    async def test_shutdown_on_empty_hub(self):
        """Shutdown on empty hub does not raise."""
        hub = LiveEventHub()

        # Should not raise
        await hub.shutdown()


# ============================================================================
# Test Edge Cases
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_very_large_queue_size(self):
        """Hub works with large max_queue_size."""
        hub = LiveEventHub(max_queue_size=10000)
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.stream_message("instance-1", {"content": "test"}, event_type="message")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["message"]["content"] == "test"

    @pytest.mark.asyncio
    async def test_very_small_queue_size(self):
        """Hub works with minimal max_queue_size."""
        hub = LiveEventHub(max_queue_size=1)
        queue = asyncio.Queue()

        await hub.add_connection("instance-1", queue)
        await hub.stream_message("instance-1", {"content": "test"}, event_type="message")

        event = queue.get_nowait()
        assert event["message"]["content"] == "test"

    @pytest.mark.asyncio
    async def test_zero_queue_size(self):
        """Hub works when queue is immediately full (maxsize=1 with one item)."""
        hub = LiveEventHub(max_queue_size=1)
        queue = asyncio.Queue(maxsize=1)

        await hub.add_connection("instance-1", queue)
        # Fill the queue
        await queue.put("dummy")

        # Queue is full, connection should be removed on stream
        await hub.stream_message("instance-1", {"content": "test"}, event_type="message")
        assert await hub.get_connection_count("instance-1") == 0

    @pytest.mark.asyncio
    async def test_message_with_empty_content(self):
        """Messages with empty content are still delivered."""
        hub = LiveEventHub()
        queue = asyncio.Queue()
        message = {"content": "", "role": "assistant"}

        await hub.add_connection("instance-1", queue)
        await hub.stream_message("instance-1", message, event_type="message")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["message"]["content"] == ""

    @pytest.mark.asyncio
    async def test_special_characters_in_instance_id(self):
        """Instance IDs with special characters work correctly."""
        hub = LiveEventHub()
        queue = asyncio.Queue()
        instance_id = "instance_1/test-2@3#4"

        await hub.add_connection(instance_id, queue)
        assert await hub.get_connection_count(instance_id) == 1

        await hub.stream_message(instance_id, {"content": "test"}, event_type="message")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == instance_id

    @pytest.mark.asyncio
    async def test_empty_checkpoint_id(self):
        """Checkpoint events with empty checkpoint_id are still delivered."""
        hub = LiveEventHub()
        queue = asyncio.Queue()
        messages = [{"message_id": "msg-1", "content": "test"}]

        await hub.add_connection("instance-1", queue)
        await hub.stream_checkpoint("instance-1", messages=messages, checkpoint_id="")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["checkpoint_id"] == ""

    @pytest.mark.asyncio
    async def test_concurrent_add_remove_connections(self):
        """Concurrent add/remove operations are handled safely."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        # Run multiple operations concurrently
        await asyncio.gather(
            hub.add_connection("instance-1", queue1),
            hub.add_connection("instance-1", queue2),
            hub.remove_connection("instance-1", queue1),
        )

        # Final state should be consistent
        count = await hub.get_connection_count("instance-1")
        assert count in [0, 1, 2]  # Could be any valid state


# ============================================================================
# Test Queue ShutDown Handling
# ============================================================================


class TestQueueShutDownHandling:
    """Tests for handling of shut-down queues (dead connections)."""

    @pytest.mark.asyncio
    async def test_shutdown_queue_removed_gracefully(self):
        """Shut-down queue triggers connection cleanup without raising."""
        hub = LiveEventHub()
        queue = asyncio.Queue()
        queue.shutdown()

        await hub.add_connection("instance-1", queue)
        assert await hub.get_connection_count("instance-1") == 1

        # Stream should mark queue as dead and remove it without raising
        await hub.stream_message("instance-1", {"content": "test"}, event_type="message")

        # Connection should be removed
        assert await hub.get_connection_count("instance-1") == 0

    @pytest.mark.asyncio
    async def test_shutdown_queue_does_not_affect_healthy_queues(self):
        """When one queue is shut down, healthy queues still receive events."""
        hub = LiveEventHub()
        dead_queue = asyncio.Queue()
        dead_queue.shutdown()
        healthy_queue = asyncio.Queue()

        await hub.add_connection("instance-1", dead_queue)
        await hub.add_connection("instance-1", healthy_queue)
        assert await hub.get_connection_count("instance-1") == 2

        # Stream should remove dead_queue, keep healthy_queue
        await hub.stream_message("instance-1", {"content": "test"}, event_type="message")

        assert await hub.get_connection_count("instance-1") == 1

        # Healthy queue should still receive the event
        event = await asyncio.wait_for(healthy_queue.get(), timeout=1.0)
        assert event["message"]["content"] == "test"

    @pytest.mark.asyncio
    async def test_mixed_full_and_shutdown_queues(self):
        """Multiple dead queues: one QueueFull, one QueueShutDown, and one healthy."""
        hub = LiveEventHub(max_queue_size=1)
        full_queue = asyncio.Queue(maxsize=1)
        shutdown_queue = asyncio.Queue()
        healthy_queue = asyncio.Queue()

        await hub.add_connection("instance-1", full_queue)
        await hub.add_connection("instance-1", shutdown_queue)
        await hub.add_connection("instance-1", healthy_queue)

        # Fill the full queue and shut down the shutdown queue
        await full_queue.put("dummy")
        shutdown_queue.shutdown()

        # Stream should remove both dead queues, keep healthy queue
        await hub.stream_message("instance-1", {"content": "test"}, event_type="message")

        assert await hub.get_connection_count("instance-1") == 1

        # Healthy queue should still receive the event
        event = await asyncio.wait_for(healthy_queue.get(), timeout=1.0)
        assert event["message"]["content"] == "test"

    @pytest.mark.asyncio
    async def test_all_queues_shutdown(self):
        """All queues shut down - no exception raised and count drops to 0."""
        hub = LiveEventHub()
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()

        queue1.shutdown()
        queue2.shutdown()

        await hub.add_connection("instance-1", queue1)
        await hub.add_connection("instance-1", queue2)
        assert await hub.get_connection_count("instance-1") == 2

        # Stream should remove all dead queues without raising
        await hub.stream_message("instance-1", {"content": "test"}, event_type="message")

        assert await hub.get_connection_count("instance-1") == 0

    @pytest.mark.asyncio
    async def test_shutdown_queue_via_stream_status_change(self):
        """Shut-down queue is removed via stream_status_change, not just stream_message."""
        hub = LiveEventHub()
        queue = asyncio.Queue()
        queue.shutdown()

        await hub.add_connection("instance-1", queue)
        assert await hub.get_connection_count("instance-1") == 1

        # stream_status_change should also remove dead queues
        await hub.stream_status_change("instance-1", status="running")

        # Connection should be removed
        assert await hub.get_connection_count("instance-1") == 0
