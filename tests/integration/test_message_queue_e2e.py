"""
End-to-end test for the message queue system with debug logging.

This test:
1. Uses the SessionManager directly with debug level logging
2. Creates a coder session
3. Sends "hi" message via the async queue
4. Listens to events to capture all responses
5. Verifies only ONE LLM call happens (no duplicates)
6. Logs detailed information for debugging

Run with:
    pytest tests/integration/test_message_queue_e2e.py -v -s --run-integration
    
Or to run with specific verbose output:
    pytest tests/integration/test_message_queue_e2e.py -v -s --run-integration --log-cli-level=DEBUG
"""

import os
import pytest
import asyncio
import logging
import time
import traceback
from pathlib import Path
from typing import Optional
from unittest.mock import patch
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Increase daemon logging for debugging
logging.getLogger('daemon').setLevel(logging.DEBUG)

# Skip if no API key
pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Set OPENAI_API_KEY to run integration tests"
)


class LLMCallTracker:
    """Track LLM invocations for testing."""
    
    def __init__(self):
        self.call_count = 0
        self.call_details = []
        self._lock = asyncio.Lock()
    
    async def track(self, messages, **kwargs):
        async with self._lock:
            self.call_count += 1
            self.call_details.append({
                'call_number': self.call_count,
                'timestamp': datetime.now().isoformat(),
                'message_count': len(messages) if messages else 0,
                'stack': ''.join(traceback.format_stack()[-8:-1])  # Last 7 frames
            })
            logger.info(f"═══════════════════════════════════════════════════════════════")
            logger.info(f"[LLM TRACKER] ⚡ Invocation #{self.call_count}")
            logger.info(f"═══════════════════════════════════════════════════════════════")
            for i, msg in enumerate(messages or []):
                content = getattr(msg, 'content', str(msg))[:100]
                logger.info(f"  Message {i}: {type(msg).__name__}: {content}...")
            logger.info(f"  Stack trace:\n{self.call_details[-1]['stack']}")
    
    def reset(self):
        self.call_count = 0
        self.call_details = []


# Global tracker instance
llm_tracker = LLMCallTracker()


@pytest.fixture
def tracker():
    """Reset tracker for each test."""
    llm_tracker.reset()
    return llm_tracker


@pytest.fixture
def mock_llm_tracker(tracker):
    """Patch the LLM to track invocations."""
    from daemon import graph
    
    original_generate = graph.ThinkingChatOpenAI._generate
    
    def tracked_generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Sync wrapper for async tracking
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in async context, schedule tracking
            asyncio.create_task(tracker.track(messages, stop=stop, **kwargs))
        else:
            loop.run_until_complete(tracker.track(messages, stop=stop, **kwargs))
        return original_generate(self, messages, stop=stop, run_manager=run_manager, **kwargs)
    
    with patch.object(graph.ThinkingChatOpenAI, '_generate', tracked_generate):
        yield tracker


@pytest.fixture
def integration_config():
    """Load real configuration from config.yaml (uses .env)."""
    from daemon.config import load_config
    
    project_root = Path(__file__).parent.parent.parent
    config_path = project_root / "config.yaml"
    
    if not config_path.exists():
        pytest.skip(f"config.yaml not found at {config_path}")
    
    return load_config(str(config_path))


@pytest.fixture
def test_db_path():
    """Return a test database path and cleanup after."""
    project_root = Path(__file__).parent.parent.parent
    db_path = project_root / "test_e2e_queue.db"
    db_path.unlink(missing_ok=True)
    yield db_path
    # Cleanup
    if db_path.exists():
        db_path.unlink()


@pytest.mark.asyncio
async def test_single_message_no_duplicate_llm_calls(
    integration_config, 
    test_db_path,
    mock_llm_tracker
):
    """
    Test that sending a single message results in exactly ONE LLM call.
    
    This test reproduces and detects the bug where LLM is called twice for a single message.
    
    Debug output includes:
    - Full stack traces for each LLM invocation
    - Message counts at each step
    - Timing information
    """
    from daemon.manager import SessionManager
    
    tracker = mock_llm_tracker
    
    # Modify the persistence config for test isolation
    integration_config.persistence.db_path = str(test_db_path)
    
    # Create manager
    logger.info("=" * 60)
    logger.info("[TEST] Creating SessionManager...")
    logger.info("=" * 60)
    
    manager = SessionManager(integration_config)
    
    # Ensure main loop is set for event broadcasting
    manager.broadcaster.set_main_loop(asyncio.get_running_loop())
    
    # Spawn coder session
    project_root = Path(__file__).parent.parent.parent
    coder_agent_dir = str(project_root / "agents" / "coder")
    logger.info(f"[TEST] Creating session with agent: {coder_agent_dir}")
    
    session_id = manager.spawn_session(agent_dir=coder_agent_dir)
    logger.info(f"[TEST] ✅ Session created: {session_id}")
    
    # Track invocations before sending message
    initial_count = tracker.call_count
    
    # =================================================================
    # SEND MESSAGE
    # =================================================================
    logger.info("")
    logger.info("=" * 60)
    logger.info("[TEST] Sending 'hi' message via enqueue_message...")
    logger.info("=" * 60)
    
    start_time = time.time()
    
    result = await manager.enqueue_message(
        session_id=session_id,
        message="hi",
        source="test"
    )
    
    logger.info(f"[TEST] Message enqueued: {result.message_id}, status: {result.status}")
    
    # =================================================================
    # WAIT FOR PROCESSING
    # =================================================================
    logger.info("")
    logger.info("=" * 60)
    logger.info("[TEST] Waiting for message processing (max 30s)...")
    logger.info("=" * 60)
    
    # Wait for the completed event via broadcaster
    completed_received = False
    wait_timeout = 30
    
    while time.time() - start_time < wait_timeout:
        # Check if message was completed (check queue stats)
        stats = manager.get_queue_stats(session_id)
        logger.debug(f"[TEST] Queue stats: pending={stats.pending_count}, processing={stats.processing_count}")
        
        if stats.pending_count == 0 and stats.processing_count == 0:
            # Check if we got a response
            await asyncio.sleep(0.5)  # Small delay to ensure events are processed
            completed_received = True
            break
        
        await asyncio.sleep(0.5)
    
    elapsed = time.time() - start_time
    logger.info(f"[TEST] Processing completed in {elapsed:.2f}s")
    
    # =================================================================
    # VERIFY LLM CALL COUNT
    # =================================================================
    logger.info("")
    logger.info("=" * 60)
    logger.info("[TEST] VERIFICATION")
    logger.info("=" * 60)
    
    final_count = tracker.call_count
    llm_calls = final_count - initial_count
    
    logger.info(f"[TEST] LLM invocations: {llm_calls} (initial: {initial_count}, final: {final_count})")
    
    # Log all LLM call details for debugging
    for detail in tracker.call_details:
        logger.info("")
        logger.info(f"[TEST] 📞 LLM Call #{detail['call_number']} at {detail['timestamp']}")
        logger.info(f"[TEST]    Message count: {detail['message_count']}")
        if detail['call_number'] > 1:
            logger.error(f"[TEST] ⚠️  DUPLICATE LLM CALL DETECTED!")
            logger.error(f"[TEST]    Stack trace:\n{detail['stack']}")
    
    # =================================================================
    # ASSERTION
    # =================================================================
    if llm_calls != 1:
        logger.error("")
        logger.error("=" * 60)
        logger.error(f"[TEST] ❌ FAILED: Expected 1 LLM call, got {llm_calls}")
        logger.error("=" * 60)
        
        # Log detailed debugging info
        logger.error("[TEST] Checking queue state...")
        stats = manager.get_queue_stats(session_id)
        logger.error(f"[TEST]   Pending: {stats.pending_count}")
        logger.error(f"[TEST]   Processing: {stats.processing_count}")
        logger.error(f"[TEST]   Oldest age: {stats.oldest_message_age_seconds}s")
        
        logger.error("[TEST] Checking processing set...")
        logger.error(f"[TEST]   Sessions in _processing: {manager._processing}")
    
    assert llm_calls == 1, (
        f"Expected exactly 1 LLM call, but got {llm_calls} calls. "
        f"This indicates duplicate processing! See logs above for stack traces."
    )
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("[TEST] ✅ PASSED - Only one LLM call detected")
    logger.info("=" * 60)
    
    # Cleanup
    manager.terminate_session(session_id)
    logger.info("[TEST] Session terminated")


@pytest.mark.asyncio
async def test_sse_events_count(
    integration_config,
    test_db_path,
    mock_llm_tracker
):
    """
    Test that SSE events are correct for a single message.
    
    Verifies:
    1. Exactly one message_queued event
    2. Exactly one status_changed event (processing)
    3. Exactly one completed event
    4. No error events
    """
    from daemon.manager import SessionManager
    from daemon.events import Event
    
    tracker = mock_llm_tracker
    
    # Modify the persistence config for test isolation
    integration_config.persistence.db_path = str(test_db_path)
    
    manager = SessionManager(integration_config)
    manager.broadcaster.set_main_loop(asyncio.get_running_loop())
    
    # Track events
    events_received = []
    
    async def collect_events(session_id: str):
        """Collect events from the broadcaster."""
        queue = await manager.broadcaster.get_queue(session_id)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
                events_received.append(event)
                logger.info(f"[SSE] Event: {event.type}, message_id: {event.message_id}")
                
                if event.type == "completed" or event.type == "error":
                    # Wait a bit more to see if there are duplicate events
                    await asyncio.sleep(2)
                    break
            except asyncio.TimeoutError:
                logger.warning("[SSE] Timeout waiting for events")
                break
    
    project_root = Path(__file__).parent.parent.parent
    coder_agent_dir = str(project_root / "agents" / "coder")
    session_id = manager.spawn_session(agent_dir=coder_agent_dir)
    
    # Start collecting events
    collect_task = asyncio.create_task(collect_events(session_id))
    
    # Give the collector time to start
    await asyncio.sleep(0.5)
    
    # Send message
    logger.info("[SSE TEST] Sending message...")
    await manager.enqueue_message(
        session_id=session_id,
        message="hi",
        source="test"
    )
    
    # Wait for events
    await collect_task
    
    # Analyze events
    event_counts = {}
    for event in events_received:
        event_type = event.type
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("[SSE TEST] Event counts:")
    for event_type, count in event_counts.items():
        logger.info(f"  {event_type}: {count}")
    logger.info("=" * 60)
    
    # Verify LLM calls
    logger.info(f"[SSE TEST] LLM invocations: {tracker.call_count}")
    
    # Assertions
    assert event_counts.get("message_queued", 0) == 1, \
        f"Expected 1 message_queued, got {event_counts.get('message_queued', 0)}"
    
    assert event_counts.get("completed", 0) == 1, \
        f"Expected 1 completed, got {event_counts.get('completed', 0)}"
    
    assert event_counts.get("error", 0) == 0, \
        f"Expected 0 errors, got {event_counts.get('error', 0)}"
    
    assert tracker.call_count == 1, \
        f"Expected 1 LLM call, got {tracker.call_count}"
    
    logger.info("[SSE TEST] ✅ PASSED")
    
    # Cleanup
    manager.terminate_session(session_id)


if __name__ == "__main__":
    # Run directly for debugging
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s", "--log-cli-level=DEBUG"]))


# Additional test for debugging the duplicate issue
@pytest.mark.asyncio
async def test_debug_llm_invocation_count(
    integration_config,
    test_db_path
):
    """
    Debug test to trace exactly how many times LLM is invoked.
    
    This test adds detailed logging to understand the flow.
    """
    from daemon.manager import SessionManager
    import logging
    
    # Enable all daemon logging
    logging.getLogger('daemon').setLevel(logging.DEBUG)
    
    # Track LLM calls via log parsing
    llm_calls = []
    
    class LogHandler(logging.Handler):
        def emit(self, record):
            if 'LLM' in record.getMessage() and 'Invoking' in record.getMessage():
                llm_calls.append({
                    'time': record.created,
                    'message': record.getMessage()
                })
    
    handler = LogHandler()
    logging.getLogger('daemon.graph').addHandler(handler)
    
    try:
        integration_config.persistence.db_path = str(test_db_path)
        manager = SessionManager(integration_config)
        manager.broadcaster.set_main_loop(asyncio.get_running_loop())
        
        project_root = Path(__file__).parent.parent.parent
        coder_agent_dir = str(project_root / "agents" / "coder")
        session_id = manager.spawn_session(agent_dir=coder_agent_dir)
        
        logger.info(f"[DEBUG] Session: {session_id}")
        
        # Send message
        result = await manager.enqueue_message(
            session_id=session_id,
            message="hi",
            source="test"
        )
        
        logger.info(f"[DEBUG] Message enqueued: {result.message_id}")
        
        # Wait for processing
        await asyncio.sleep(10)
        
        # Check LLM call count
        logger.info(f"[DEBUG] LLM invocations detected: {len(llm_calls)}")
        for i, call in enumerate(llm_calls):
            logger.info(f"[DEBUG]   Call #{i+1}: {call['message']}")
        
        # Also check event history for completed events
        history = manager.broadcaster.get_events_since(session_id, 0)
        completed_events = [e for e in history if e.type == 'completed']
        logger.info(f"[DEBUG] Completed events in history: {len(completed_events)}")
        
        # Assertions
        assert len(llm_calls) == 1, f"Expected 1 LLM invocation, got {len(llm_calls)}"
        assert len(completed_events) == 1, f"Expected 1 completed event, got {len(completed_events)}"
        
        logger.info("[DEBUG] ✅ Test passed - only 1 LLM invocation")
        
        manager.terminate_session(session_id)
        
    finally:
        logging.getLogger('daemon.graph').removeHandler(handler)
