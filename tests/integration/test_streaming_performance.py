"""Performance and load tests for progressive streaming feature.

Tests for concurrent load, throughput, latency, and resource usage.
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

from daemon.events import EventBroadcaster, Event


# ============================================================================
# Fixtures
# ============================================================================

@pytest_asyncio.fixture
async def mock_manager():
    """Create a mock SessionManager."""
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
    async def test_single_session_throughput(self, mock_manager):
        """Test throughput for single session with many events."""
        broadcaster = EventBroadcaster(max_queue_size=10000, history_size=10000)
        session_id = "throughput-test"
        
        num_events = 1000
        start_time = time.perf_counter()
        
        # Send events
        for i in range(num_events):
            await broadcaster.broadcast(Event(
                type=f"event{i}",
                session_id=session_id,
                data={"index": i}
            ))
        
        end_time = time.perf_counter()
        duration = end_time - start_time
        
        # Calculate throughput
        throughput = num_events / duration
        
        print(f"\nSingle session throughput: {throughput:.0f} events/sec")
        print(f"Duration: {duration:.3f}s for {num_events} events")
        
        # Should handle at least 5000 events/second
        assert throughput > 5000

    @pytest.mark.asyncio
    async def test_multiple_session_throughput(self, mock_manager):
        """Test throughput across multiple sessions."""
        broadcaster = EventBroadcaster(max_queue_size=10000, history_size=10000)
        
        num_sessions = 100
        num_events_per_session = 100
        
        start_time = time.perf_counter()
        
        # Distribute events across sessions
        for session_idx in range(num_sessions):
            session_id = f"session-{session_idx}"
            for i in range(num_events_per_session):
                await broadcaster.broadcast(Event(
                    type=f"event{i}",
                    session_id=session_id,
                    data={"index": i}
                ))
        
        end_time = time.perf_counter()
        duration = end_time - start_time
        total_events = num_sessions * num_events_per_session
        throughput = total_events / duration
        
        print(f"\nMulti-session throughput: {throughput:.0f} events/sec")
        print(f"Duration: {duration:.3f}s for {total_events} events across {num_sessions} sessions")
        
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
                    session_id=f"session-{task_id}",
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
        session_id = "latency-test"
        
        # Pre-create the queue
        queue = await broadcaster.get_queue(session_id)
        
        latencies = []
        num_samples = 100
        
        for i in range(num_samples):
            event = Event(type="test", session_id=session_id, data={"i": i})
            
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
                session_id="session-1",
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
    async def test_many_concurrent_sessions(self, mock_manager):
        """Test handling many concurrent session queues."""
        broadcaster = EventBroadcaster()
        
        num_sessions = 500
        
        # Create many session queues concurrently
        start_time = time.perf_counter()
        
        async def create_session(idx):
            session_id = f"session-{idx}"
            queue = await broadcaster.get_queue(session_id)
            # Add one event
            await broadcaster.broadcast(Event(
                type="init",
                session_id=session_id,
                data={"idx": idx}
            ))
            return queue
        
        queues = await asyncio.gather(*[create_session(i) for i in range(num_sessions)])
        
        end_time = time.perf_counter()
        
        print(f"\nCreated {num_sessions} sessions in {(end_time - start_time)*1000:.1f}ms")
        
        # All sessions should exist
        assert len(broadcaster._event_history) == num_sessions

    @pytest.mark.asyncio
    async def test_session_cleanup_performance(self, mock_manager):
        """Test performance of session cleanup."""
        broadcaster = EventBroadcaster()
        
        # Create many sessions
        num_sessions = 100
        for i in range(num_sessions):
            session_id = f"session-{i}"
            await broadcaster.get_queue(session_id)
            await broadcaster.broadcast(Event(type="e", session_id=session_id))
        
        # Cleanup
        start_time = time.perf_counter()
        
        for i in range(num_sessions):
            broadcaster.cleanup_session(f"session-{i}")
        
        end_time = time.perf_counter()
        
        print(f"\nCleaned up {num_sessions} sessions in {(end_time - start_time)*1000:.1f}ms")
        
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
        session_id = "memory-test"
        
        # Add many events
        num_events = 10000
        
        for i in range(num_events):
            await broadcaster.broadcast(Event(
                type=f"event{i}",
                session_id=session_id,
                data={"index": i, "payload": "x" * 100}
            ))
        
        # History should be capped
        history = broadcaster._event_history[session_id]
        assert len(history) == 1000
        
        print(f"\nHistory capped at {len(history)} despite {num_events} events")

    @pytest.mark.asyncio
    async def test_many_sessions_memory(self, mock_manager):
        """Test memory usage with many sessions."""
        broadcaster = EventBroadcaster(history_size=10)
        
        num_sessions = 100
        events_per_session = 20
        
        for s in range(num_sessions):
            session_id = f"session-{s}"
            for e in range(events_per_session):
                await broadcaster.broadcast(Event(
                    type="e",
                    session_id=session_id,
                    data={"x": e}
                ))
        
        # Each session should have limited history
        total_history = sum(
            len(broadcaster._event_history.get(f"session-{s}", []))
            for s in range(num_sessions)
        )
        
        print(f"\nTotal events in history: {total_history}")
        
        # Should be capped at num_sessions * history_size
        assert total_history <= num_sessions * 10


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
        
        session_id = "sustained-load"
        start_time = time.perf_counter()
        event_count = 0
        
        while time.perf_counter() - start_time < duration_seconds:
            # Try to send events at target rate
            tasks = []
            for _ in range(events_per_second // 10):  # Batch of 50
                tasks.append(broadcaster.broadcast(Event(
                    type="load",
                    session_id=session_id,
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
                    session_id=f"burst-session-{burst % 5}",  # 5 sessions
                    data={"burst": burst, "i": i}
                ))
                for i in range(events_per_burst)
            ]
            
            await asyncio.gather(*tasks)
        
        print(f"\nHandled {num_bursts * events_per_burst} events in bursts")
        
        # All events should be in history (may be capped by history_size)
        # Check at least some events made it
        total_events = sum(
            broadcaster._event_counters.get(f"burst-session-{i}", 0)
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
                session_id="session-1",
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
            Event(type="test", session_id="s1", data={"i": i})
        duration = time.perf_counter() - start
        
        print(f"\nEvent creation: {num_events/duration:.0f} events/sec")

    @pytest.mark.asyncio
    async def test_benchmark_sse_conversion(self, mock_manager):
        """Benchmark SSE conversion."""
        from daemon.events import event_to_sse
        
        events = [
            Event(type="test", session_id="s1", message_id=f"m{i}", data={"i": i})
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
            await broadcaster.get_queue(f"session-{i % 100}")
        duration = time.perf_counter() - start
        
        print(f"\nQueue get: {num_ops/duration:.0f} ops/sec")
