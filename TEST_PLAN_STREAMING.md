# Progressive Streaming Feature - Test Plan

This document describes the comprehensive test plan for the progressive streaming (SSE) feature in the Ensemble Daemon.

## Overview

The progressive streaming feature enables real-time event delivery to clients using Server-Sent Events (SSE). The system uses an event broadcasting architecture where:

- **EventBroadcaster**: Manages per-session event queues for real-time delivery
- **SSE Endpoint**: `/sessions/{session_id}/events` streams events to clients
- **Event Types**: message_queued, status_changed, content_chunk, thinking, tool_call, tool_complete, completed, error

## Test Categories

### 1. Unit Tests

**Location**: `tests/test_events.py`

Tests individual components in isolation:

| Test Class | Description |
|------------|-------------|
| `TestEvent` | Tests Event dataclass creation and properties |
| `TestEventBroadcaster` | Tests queue management, broadcasting, history |
| `TestEventBroadcasterGlobalSubscribers` | Tests global subscriber functionality |
| `TestEventBroadcasterThreadSafety` | Tests thread-safe operations |
| `TestEventToSSE` | Tests SSE format conversion |
| `TestEventBroadcasterQueueOverflow` | Tests queue overflow handling |

#### Running Unit Tests

```bash
# Run all event tests
pytest tests/test_events.py -v

# Run specific test class
pytest tests/test_events.py::TestEventBroadcaster -v

# Run with coverage
pytest tests/test_events.py --cov=daemon.events --cov-report=term-missing
```

#### Key Test Cases

```python
# Test: Event creation with all fields
def test_event_creation(self):
    event = Event(
        type="message_queued",
        session_id="test-session-123",
        message_id="msg-456",
        data={"content": "Hello"}
    )
    assert event.type == "message_queued"
    assert event.event_id == 0  # Default

# Test: Broadcast pushes to queue
@pytest.mark.asyncio
async def test_broadcast_pushes_to_queue(self, broadcaster):
    event = Event(type="message", session_id="session-1")
    await broadcaster.broadcast(event)
    
    queue = await broadcaster.get_queue("session-1")
    assert queue.qsize() == 1
```

---

### 2. Integration Tests

**Location**: `tests/integration/test_sse_streaming.py`

Tests component interactions and end-to-end flows:

| Test Class | Description |
|------------|-------------|
| `TestSSEStreamConnection` | Tests SSE endpoint connection |
| `TestSSEEventTypes` | Tests all event type broadcasting |
| `TestSSEEventToSSEConversion` | Tests event to SSE format conversion |
| `TestSSEReconnection` | Tests reconnection support |
| `TestSSEErrorHandling` | Tests error handling in streaming |
| `TestEndToEndStreaming` | Tests complete streaming pipeline |
| `TestSessionCleanup` | Tests proper session cleanup |

#### Running Integration Tests

```bash
# Run all integration tests for streaming
pytest tests/integration/test_sse_streaming.py -v

# Run specific test
pytest tests/integration/test_sse_streaming.py::TestSSEEventTypes -v
```

#### Key Test Cases

```python
# Test: Full streaming pipeline
@pytest.mark.asyncio
async def test_full_streaming_pipeline(self, mock_manager):
    session_id = "test-session-e2e"
    broadcaster = mock_manager.broadcaster
    
    # 1. Message queued
    await broadcaster.broadcast(Event(
        type="message_queued",
        session_id=session_id,
        message_id="msg-1",
        data={"content": "Hello"}
    ))
    
    # 2. Status changed
    await broadcaster.broadcast(Event(
        type="status_changed",
        session_id=session_id,
        message_id="msg-1",
        data={"status": "processing"}
    ))
    
    # 3. Content chunks
    for chunk in ["Hello", " ", "world"]:
        await broadcaster.broadcast(Event(
            type="content_chunk",
            session_id=session_id,
            message_id="msg-1",
            data={"chunk": chunk}
        ))
    
    # 4. Completed
    await broadcaster.broadcast(Event(
        type="completed",
        session_id=session_id,
        message_id="msg-1",
        data={"content": "Hello world"}
    ))
    
    # Verify all events
    queue = await broadcaster.get_queue(session_id)
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    
    assert len(events) == 6

# Test: Reconnection support
@pytest.mark.asyncio
async def test_get_events_since_for_reconnection(self, mock_manager):
    broadcaster = mock_manager.broadcaster
    
    # Add 5 events
    for i in range(5):
        await broadcaster.broadcast(Event(
            type=f"event{i}",
            session_id="session-1",
            event_id=i+1
        ))
    
    # Client reconnects with last event ID 3
    missed_events = broadcaster.get_events_since("session-1", 3)
    
    # Should get events 4 and 5
    assert len(missed_events) == 2
```

---

### 3. Error Scenario Tests

**Location**: `tests/integration/test_streaming_errors.py`

Tests edge cases, failure scenarios, and error conditions:

| Test Class | Description |
|------------|-------------|
| `TestEventValidation` | Tests event data validation |
| `TestEventBroadcasterErrorScenarios` | Tests error conditions |
| `TestSSEErrorScenarios` | Tests SSE-specific errors |
| `TestReconnectionErrorScenarios` | Tests reconnection edge cases |
| `TestConcurrentAccessErrorScenarios` | Tests concurrent access errors |
| `TestErrorRecovery` | Tests error recovery |
| `TestHistoryManagement` | Tests history size limits |
| `TestGlobalSubscriberErrorScenarios` | Tests subscriber errors |
| `TestAPISSEErrorHandling` | Tests API error responses |

#### Running Error Tests

```bash
# Run all error scenario tests
pytest tests/integration/test_streaming_errors.py -v

# Run with verbose output
pytest tests/integration/test_streaming_errors.py -v -s
```

#### Key Test Cases

```python
# Test: Rapid burst of events
@pytest.mark.asyncio
async def test_rapid_burst_of_events(self, mock_manager):
    broadcaster = EventBroadcaster(max_queue_size=100)
    
    # Send 200 events rapidly
    tasks = [
        broadcaster.broadcast(Event(
            type=f"event{i}",
            session_id="burst-session",
            data={"index": i}
        ))
        for i in range(200)
    ]
    
    await asyncio.gather(*tasks)
    
    # All stored in history despite queue overflow
    stats = broadcaster.get_stats("burst-session")
    assert stats["history_size"] == 200

# Test: Many sessions at once
@pytest.mark.asyncio
async def test_many_sessions_at_once(self, mock_manager):
    broadcaster = EventBroadcaster()
    
    # Create 50 sessions with events
    for i in range(50):
        await broadcaster.broadcast(Event(
            type="init",
            session_id=f"session-{i}",
            data={"index": i}
        ))
    
    assert len(broadcaster._event_history) == 50

# Test: Queue full doesn't crash
@pytest.mark.asyncio
async def test_queue_full_does_not_crash_broadcaster(self, mock_manager):
    broadcaster = EventBroadcaster(max_queue_size=1)
    
    # Fill queue
    await broadcaster.broadcast(Event(type="e1", session_id="s1"))
    
    # Should not crash
    await broadcaster.broadcast(Event(type="e2", session_id="s1"))
```

---

### 4. Performance/Load Tests

**Location**: `tests/integration/test_streaming_performance.py`

Tests throughput, latency, and concurrent load:

| Test Class | Description |
|------------|-------------|
| `TestEventThroughput` | Tests event processing throughput |
| `TestEventLatency` | Tests event delivery latency |
| `TestConcurrentConnections` | Tests concurrent session handling |
| `TestMemoryEfficiency` | Tests memory usage |
| `TestStressScenarios` | Tests extreme load scenarios |
| `TestBenchmarks` | Performance benchmarks |

#### Running Performance Tests

```bash
# Run all performance tests
pytest tests/integration/test_streaming_performance.py -v

# Run with timing output
pytest tests/integration/test_streaming_performance.py -v -s

# Run specific benchmark
pytest tests/integration/test_streaming_performance.py::TestBenchmarks -v -s
```

#### Performance Targets

| Metric | Target | Test |
|--------|--------|------|
| Single session throughput | >5000 events/sec | `test_single_session_throughput` |
| Multi-session throughput | >3000 events/sec | `test_multiple_session_throughput` |
| Concurrent throughput | >2000 events/sec | `test_concurrent_broadcast_throughput` |
| Average latency | <10ms | `test_broadcast_to_queue_latency` |
| End-to-end latency | <15ms | `test_end_to_end_streaming_latency` |

#### Key Test Cases

```python
# Test: Throughput benchmark
@pytest.mark.asyncio
async def test_single_session_throughput(self, mock_manager):
    broadcaster = EventBroadcaster(max_queue_size=10000)
    num_events = 1000
    
    start_time = time.perf_counter()
    for i in range(num_events):
        await broadcaster.broadcast(Event(
            type=f"event{i}",
            session_id="throughput-test",
            data={"index": i}
        ))
    duration = time.perf_counter() - start_time
    
    throughput = num_events / duration
    assert throughput > 5000

# Test: Latency measurement
@pytest.mark.asyncio
async def test_broadcast_to_queue_latency(self, mock_manager):
    latencies = []
    
    for i in range(100):
        event = Event(type="test", session_id="latency-test")
        
        start = time.perf_counter()
        await broadcaster.broadcast(event)
        end = time.perf_counter()
        
        latencies.append((end - start) * 1000)
    
    avg_latency = statistics.mean(latencies)
    assert avg_latency < 10  # ms

# Test: Sustained load
@pytest.mark.asyncio
async def test_sustained_load(self, mock_manager):
    duration_seconds = 2
    events_per_second = 5000
    
    start_time = time.perf_counter()
    event_count = 0
    
    while time.perf_counter() - start_time < duration_seconds:
        tasks = [
            broadcaster.broadcast(Event(
                type="load",
                session_id="sustained-load",
                data={"count": event_count}
            ))
            for _ in range(500)  # Batch of 500
        ]
        await asyncio.gather(*tasks)
        event_count += 500
    
    throughput = event_count / (time.perf_counter() - start_time)
    assert throughput > 1000
```

---

## Running All Tests

### Complete Test Suite

```bash
# Run all tests related to streaming
pytest tests/test_events.py \
       tests/integration/test_sse_streaming.py \
       tests/integration/test_streaming_errors.py \
       tests/integration/test_streaming_performance.py \
       -v

# Run with coverage
pytest tests/ -v --cov=daemon.events --cov-report=term-missing
```

### CI/CD Integration

```bash
# Quick test (unit only)
pytest tests/test_events.py -v

# Full test (all categories)
pytest tests/test_events.py \
       tests/integration/test_sse_streaming.py \
       tests/integration/test_streaming_errors.py \
       tests/integration/test_streaming_performance.py \
       -v --tb=short

# Performance tests only (for benchmarking)
pytest tests/integration/test_streaming_performance.py -v -s
```

---

## Event Types Reference

| Event Type | Description | Data Fields |
|------------|-------------|-------------|
| `connected` | Initial connection | session_id |
| `message_queued` | Message enqueued | message_id, content, source, priority, status |
| `status_changed` | Processing status | message_id, status, is_retry |
| `content_chunk` | Token-level content | message_id, chunk |
| `thinking` | Extended thinking | message_id, content |
| `tool_call` | Tool invocation started | message_id, id, name, arguments |
| `tool_complete` | Tool execution finished | message_id, id, name, output |
| `completed` | Message processing done | message_id, content, thinking, tool_calls |
| `error` | Error occurred | message_id, error, status |
| `cancelled` | Message cancelled | message_id, reason |
| `keepalive` | Connection keepalive | (none) |

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Manager       │────▶│  EventBroadcaster│────▶│   SSE Queue     │
│                 │     │                  │     │                 │
│ - enqueue_msg  │     │ - per-session    │     │ - maxsize=100   │
│ - process_msg  │     │   queues         │     │ - drop on full  │
│ - broadcast    │     │ - history        │     └────────┬────────┘
└─────────────────┘     │ - subscribers    │              │
                       └──────────────────┘              │
                                                          ▼
                                               ┌─────────────────┐
                                               │  Client (SSE)   │
                                               │                 │
                                               │ /sessions/{id}/ │
                                               │   /events       │
                                               └─────────────────┘
```

---

## Troubleshooting

### Tests Failing

1. **Import errors**: Ensure all dependencies installed
   ```bash
   pip install pytest pytest-asyncio httpx
   ```

2. **Async warnings**: Use `@pytest.mark.asyncio` for all async tests

3. **Mock issues**: Use the fixtures from `conftest.py`

### Performance Issues

1. Check queue size configuration
2. Review history size limits
3. Monitor memory usage with many sessions
4. Use benchmark tests to identify bottlenecks

---

## Coverage

Run with coverage to see what's tested:

```bash
pytest tests/test_events.py \
       tests/integration/test_sse_streaming.py \
       -v --cov=daemon.events \
       --cov-report=term-missing
```

Expected coverage for `daemon/events.py`: **100%**
