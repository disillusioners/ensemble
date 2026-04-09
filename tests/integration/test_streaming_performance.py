"""LEGACY Performance and load tests for progressive streaming feature.

DEPRECATED: These tests use EventBroadcaster which has been removed.
Use EventBus tests instead. This file is kept for reference until migrated.
"""

import pytest
import pytest_asyncio
import asyncio
import time
import tempfile
import sqlite3
import os
import statistics
from unittest.mock import Mock
from typing import List

# Try to import EventBroadcaster - will skip all tests if module doesn't exist
try:
    from daemon.events import EventBroadcaster, Event
except ImportError:
    EventBroadcaster = None
    Event = None

pytestmark = pytest.mark.skipif(
    EventBroadcaster is None,
    reason="Legacy tests - EventBroadcaster removed, migrate to EventBus"
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest_asyncio.fixture
async def mock_manager():
    """Create a mock InstanceManager."""
    manager = Mock()
    manager.broadcaster = EventBroadcaster()
    
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db_path = temp_db.name
    temp_db.close()
    conn = sqlite3.connect(temp_db_path)
    manager.conn = conn
    manager._temp_db_path = temp_db_path
    
    yield manager
    
    try:
        conn.close()
    except Exception:
        pass
    try:
        os.unlink(temp_db_path)
    except Exception:
        pass


# ============================================================================
# Throughput Tests
# ============================================================================

class TestEventThroughput:
    """Tests for event throughput performance."""

    @pytest.mark.asyncio
    async def test_single_instance_throughput(self, mock_manager):
        """Test throughput for single instance with many events."""
        broadcaster = EventBroadcaster(max_queue_size=10000, history_size=10000)
        instance_id = "throughput-test"
        
        num_events = 1000
        start_time = time.perf_counter()
        
        # Send events
        for i in range(num_events):
            await broadcaster.broadcast(Event(
                type=f"event{i}",
                instance_id=instance_id,
                data={"index": i}
            ))
        
        end_time = time.perf_counter()
        duration = end_time - start_time
        
        # Calculate throughput
        throughput = num_events / duration
        
        print(f"\nSingle instance throughput: {throughput:.0f} events/sec")
        print(f"Duration: {duration:.3f}s for {num_events} events")
        
        # Should handle at least 5000 events/second
        assert throughput > 5000

    @pytest.mark.asyncio
    async def test_multiple_instance_throughput(self, mock_manager):
        """Test throughput across multiple instances."""
        broadcaster = EventBroadcaster(max_queue_size=10000, history_size=10000)
        
        num_instances = 100
        num_events_per_instance = 100
        
        start_time = time.perf_counter()
        
        # Distribute events across instances
        for instance_idx in range(num_instances):
            instance_id = f"instance-{instance_idx}"
            for i in range(num_events_per_instance):
                await broadcaster.broadcast(Event(
                    type=f"event{i}",
                    instance_id=instance_id,
                    data={"index": i}
                ))
        
        end_time = time.perf_counter()
        duration = end_time - start_time
        total_events = num_instances * num_events_per_instance
        throughput = total_events / duration
        
        print(f"\nMulti-instance throughput: {throughput:.0f} events/sec")
        print(f"Duration: {duration:.3f}s for {total_events} events across {num_instances} instances")
        
        # Should handle at least 3000 events/second
        assert throughput > 3000

    @pytest.mark.asyncio
    async def test_concurrent_broadcast_throughput(self, mock_manager):
        """Test throughput with concurrent broadcasts."""
        broadcaster = EventBroadcaster(max_queue_size=10000, history_size=10000)
        
        num_concurrent = 50
        events_per_task = 100
        
        start_time = time.perf_counter()
        
        # Concurrent broadcast
        async def broadcast_events(task_id):
            for i in range(events_per_task):
                await broadcaster.broadcast(Event(
                    type=f"event{i}",
                    instance_id=f"instance-{task_id}",
                    data={"task": task_id, "index": i}
                ))
        
        await asyncio.gather(*[broadcast_events(i) for i in range(num_concurrent)])
        
        end_time = time.perf_counter()
        duration = end_time - start_time
        total_events = num_concurrent * events_per_task
        throughput = total_events / duration
        
        print(f"\nConcurrent throughput: {throughput:.0f} events/sec")
        print(f"Duration: {duration:.3f}s for {total_events} events")
        
        assert throughput > 2000


# ============================================================================
# Latency Tests
# ============================================================================

class TestEventLatency:
    """Tests for event delivery latency."""

    @pytest.mark.asyncio
    async def test_broadcast_to_queue_latency(self, mock_manager):
        """Test latency of broadcast to queue delivery."""
        broadcaster = EventBroadcaster()
        instance_id = "latency-test"
        
        # Pre-create the queue
        queue = await broadcaster.get_queue(instance_id)
        
        latencies = []
        num_samples = 100
        
        for i in range(num_samples):
            event = Event(type="test", instance_id=instance_id, data={"i": i})
            
            start = time.perf_counter()
            await broadcaster.broadcast(event)
            end = time.perf_counter()
            latencies.append((end - start) * 1000)  # Convert to ms
        
        avg_latency = statistics.mean(latencies)
        p50_latency = statistics.median(latencies)
        p99_latency = sorted(latencies)[98]
        
        print(f"\nLatency stats:")
        print(f"  Average: {avg_latency:.3f}ms")
        print(f"  P50: {p50_latency:.3f}ms")
        print(f"  P99: {p99_latency:.3f}ms")
        
        # Average should be under 50ms (relaxed for test environment)
        assert avg_latency < 50

    @pytest.mark.asyncio
    async def test_end_to_end_streaming_latency(self, mock_manager):
        """Test end-to-end latency from broadcast to SSE format."""
        broadcaster = EventBroadcaster()
        
        latencies = []
        num_samples = 100
        
        for i in range(num_samples):
            from daemon.events import event_to_sse
            
            event = Event(
                type="message",
                instance_id="instance-1",
                message_id=f"msg-{i}",
                data={"content": f"test{i}"}
            )
            
            start = time.perf_counter()
            await broadcaster.broadcast(event)
            sse = event_to_sse(event)
            end = time.perf_counter()
            
            latencies.append((end - start) * 1000)
        
        avg_latency = statistics.mean(latencies)
        
        print(f"\nEnd-to-end latency: {avg_latency:.3f}ms average")
        
        assert avg_latency < 15


# ============================================================================
# Concurrency Tests
# ============================================================================

class TestConcurrentConnections:
    """Tests for handling many concurrent connections."""

    @pytest.mark.asyncio
    async def test_many_concurrent_instances(self, mock_manager):
        """Test handling many concurrent instance queues."""
        broadcaster = EventBroadcaster()
        
        num_instances = 500
        
        # Create many instance queues concurrently
        start_time = time.perf_counter()
        
        async def create_instance(idx):
            instance_id = f"instance-{idx}"
            queue = await broadcaster.get_queue(instance_id)
            # Add one event
            await broadcaster.broadcast(Event(
                type="init",
                instance_id=instance_id,
                data={"idx": idx}
            ))
            return queue
        
        queues = await asyncio.gather(*[create_instance(i) for i in range(num_instances)])
        
        end_time = time.perf_counter()
        
        print(f"\nCreated {num_instances} instances in {(end_time - start_time)*1000:.1f}ms")
        
        # All instances should exist
        assert len(broadcaster._event_history) == num_instances

    @pytest.mark.asyncio
    async def test_instance_cleanup_performance(self, mock_manager):
        """Test performance of instance cleanup."""
        broadcaster = EventBroadcaster()
        
        # Create many instances
        num_instances = 100
        for i in range(num_instances):
            instance_id = f"instance-{i}"
            await broadcaster.get_queue(instance_id)
            await broadcaster.broadcast(Event(type="e", instance_id=instance_id))
        
        # Cleanup
        start_time = time.perf_counter()
        
        for i in range(num_instances):
            broadcaster.cleanup_instance(f"instance-{i}")
        
        end_time = time.perf_counter()
        
        print(f"\nCleaned up {num_instances} instances in {(end_time - start_time)*1000:.1f}ms")
        
        assert len(broadcaster._event_history) == 0


# ============================================================================
# Memory Usage Tests
# ============================================================================

class TestMemoryEfficiency:
    """Tests for memory efficiency."""

    @pytest.mark.asyncio
    async def test_history_memory_usage(self, mock_manager):
        """Test memory usage with large history."""
        broadcaster = EventBroadcaster(history_size=1000)
        instance_id = "memory-test"
        
        # Add many events
        num_events = 10000
        
        for i in range(num_events):
            await broadcaster.broadcast(Event(
                type=f"event{i}",
                instance_id=instance_id,
                data={"index": i, "payload": "x" * 100}
            ))
        
        # History should be capped
        history = broadcaster._event_history[instance_id]
        assert len(history) == 1000
        
        print(f"\nHistory capped at {len(history)} despite {num_events} events")

    @pytest.mark.asyncio
    async def test_many_instances_memory(self, mock_manager):
        """Test memory usage with many instances."""
        broadcaster = EventBroadcaster(history_size=10)
        
        num_instances = 100
        events_per_instance = 20
        
        for s in range(num_instances):
            instance_id = f"instance-{s}"
            for e in range(events_per_instance):
                await broadcaster.broadcast(Event(
                    type="e",
                    instance_id=instance_id,
                    data={"x": e}
                ))
        
        # Each instance should have limited history
        total_history = sum(
            len(broadcaster._event_history.get(f"instance-{s}", []))
            for s in range(num_instances)
        )
        
        print(f"\nTotal events in history: {total_history}")
        
        # Should be capped at num_instances * history_size
        assert total_history <= num_instances * 10


# ============================================================================
# Stress Tests
# ============================================================================

class TestStressScenarios:
    """Stress tests for extreme load scenarios."""

    @pytest.mark.asyncio
    async def test_sustained_load(self, mock_manager):
        """Test sustained load over time."""
        broadcaster = EventBroadcaster(max_queue_size=1000, history_size=100)
        
        duration_seconds = 2  # Run for 2 seconds
        events_per_second = 5000
        
        instance_id = "sustained-load"
        start_time = time.perf_counter()
        event_count = 0
        
        while time.perf_counter() - start_time < duration_seconds:
            # Try to send events at target rate
            tasks = []
            for _ in range(events_per_second // 10):  # Batch of 50
                tasks.append(broadcaster.broadcast(Event(
                    type="load",
                    instance_id=instance_id,
                    data={"count": event_count}
                )))
                event_count += 1
            
            await asyncio.gather(*tasks)
            await asyncio.sleep(0.1)  # Small sleep to allow other tasks
        
        actual_duration = time.perf_counter() - start_time
        actual_throughput = event_count / actual_duration
        
        print(f"\nSustained load: {actual_throughput:.0f} events/sec for {actual_duration:.1f}s")
        print(f"Total events: {event_count}")
        
        # Should maintain reasonable throughput
        assert actual_throughput > 1000

    @pytest.mark.asyncio
    async def test_rapid_burst_handling(self, mock_manager):
        """Test handling of rapid bursts of events."""
        broadcaster = EventBroadcaster(max_queue_size=10000, history_size=1000)
        
        num_bursts = 10
        events_per_burst = 500
        
        for burst in range(num_bursts):
            tasks = [
                broadcaster.broadcast(Event(
                    type=f"burst{burst}",
                    instance_id=f"burst-instance-{burst % 5}",  # 5 instances
                    data={"burst": burst, "i": i}
                ))
                for i in range(events_per_burst)
            ]
            
            await asyncio.gather(*tasks)
        
        print(f"\nHandled {num_bursts * events_per_burst} events in bursts")
        
        # All events should be in history (may be capped by history_size)
        # Check at least some events made it
        total_events = sum(
            broadcaster._event_counters.get(f"burst-instance-{i}", 0)
            for i in range(5)
        )
        assert total_events >= num_bursts * events_per_burst // 2  # At least 50%

    @pytest.mark.asyncio
    async def test_global_subscriber_load(self, mock_manager):
        """Test load on global subscribers."""
        broadcaster = EventBroadcaster()
        
        # Create multiple subscribers
        num_subscribers = 10
        for i in range(num_subscribers):
            await broadcaster.subscribe_all(f"subscriber-{i}")
        
        num_events = 1000
        
        start_time = time.perf_counter()
        
        for i in range(num_events):
            await broadcaster.broadcast(Event(
                type="broadcast",
                instance_id="instance-1",
                data={"i": i}
            ))
        
        end_time = time.perf_counter()
        
        # Each subscriber should have received all events
        for i in range(num_subscribers):
            sub = broadcaster._subscriber_refs.get(f"subscriber-{i}")
            if sub:
                assert sub.qsize() == num_events
        
        duration = end_time - start_time
        print(f"\nBroadcast to {num_subscribers} subscribers: {num_events/duration:.0f} events/sec")


# ============================================================================
# Benchmark Tests
# ============================================================================

class TestBenchmarks:
    """Benchmark tests for comparing performance."""

    @pytest.mark.asyncio
    async def test_benchmark_event_creation(self, mock_manager):
        """Benchmark event creation."""
        num_events = 10000
        
        start = time.perf_counter()
        for i in range(num_events):
            Event(type="test", instance_id="i1", data={"i": i})
        duration = time.perf_counter() - start
        
        print(f"\nEvent creation: {num_events/duration:.0f} events/sec")

    @pytest.mark.asyncio
    async def test_benchmark_sse_conversion(self, mock_manager):
        """Benchmark SSE conversion."""
        from daemon.events import event_to_sse
        
        events = [
            Event(type="test", instance_id="i1", message_id=f"m{i}", data={"i": i})
            for i in range(1000)
        ]
        
        start = time.perf_counter()
        for event in events:
            event_to_sse(event)
        duration = time.perf_counter() - start
        
        print(f"\nSSE conversion: {1000/duration:.0f} events/sec")

    @pytest.mark.asyncio
    async def test_benchmark_queue_operations(self, mock_manager):
        """Benchmark queue get/set operations."""
        broadcaster = EventBroadcaster()
        
        num_ops = 10000
        
        # Benchmark get_queue
        start = time.perf_counter()
        for i in range(num_ops):
            await broadcaster.get_queue(f"instance-{i % 100}")
        duration = time.perf_counter() - start
        
        print(f"\nQueue get: {num_ops/duration:.0f} ops/sec")
