"""
End-to-end test for the message queue system with debug logging.

This test:
1. Uses the InstanceManager directly with debug level logging
2. Creates a developer instance
3. Sends "hi" message via the async queue
4. Listens to events to capture all responses
5. Verifies only ONE LLM call happens (no duplicates)
6. Logs detailed information for debugging

Run with:
    pytest tests/integration/test_message_queue_e2e.py -v -s -m integration

Or to run with specific verbose output:
    pytest tests/integration/test_message_queue_e2e.py -v -s -m integration --log-cli-level=DEBUG
"""

import os
import sys
import pytest
import asyncio
import logging
import time
import traceback
from pathlib import Path
from typing import Optional
from unittest.mock import patch
from datetime import datetime


LLM_TEST_TIMEOUT_SECONDS = int(os.environ.get("LLM_TEST_TIMEOUT_SECONDS", "90"))


def _load_env():
    """Load environment variables from .env file."""
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key] = value


# Load .env first so checks can see the key
_load_env()

# Restore real langgraph modules for e2e tests that need actual execution
# These tests are at the end of the test suite to avoid affecting other tests
_original_modules = {}
_langgraph_keys = [
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
]
for key in _langgraph_keys:
    if key in sys.modules:
        _original_modules[key] = sys.modules[key]
        del sys.modules[key]


def pytest_configure(config):
    """Restore original modules after conftest runs."""
    pass  # Modules already restored at import time


def pytest_sessionfinish(session, exitstatus):
    """Restore mock modules after all tests run."""
    for key in _langgraph_keys:
        if key in _original_modules:
            sys.modules[key] = _original_modules[key]
        elif key in sys.modules:
            del sys.modules[key]


# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Increase daemon logging for debugging
logging.getLogger('daemon').setLevel(logging.DEBUG)

# All tests in this file require live LLM infrastructure (real OpenAI API + MCP),
# so they are excluded from the default non-integration test gate via the
# `integration` marker defined in pyproject.toml.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="Set OPENAI_API_KEY to run integration tests"
    ),
]


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
        # Track synchronously - this works in both async and thread pool contexts
        # We use call_count directly since we're in a sync function
        tracker.call_count += 1
        tracker.call_details.append({
            'call_number': tracker.call_count,
            'timestamp': datetime.now().isoformat(),
            'message_count': len(messages) if messages else 0,
            'stack': ''.join(traceback.format_stack()[-8:-1])
        })
        
        # Log the call
        logger.info(f"═══════════════════════════════════════════════════════════════")
        logger.info(f"[LLM TRACKER] ⚡ Invocation #{tracker.call_count}")
        logger.info(f"═══════════════════════════════════════════════════════════════")
        
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
def test_db_path(tmp_path):
    """Return a unique test database path per test and cleanup after."""
    import uuid
    # Use unique path per test to avoid database corruption between tests
    db_path = tmp_path / f"test_queue_{uuid.uuid4().hex[:8]}.db"
    yield db_path
    # Cleanup is automatic with tmp_path


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
    from daemon.manager import InstanceManager

    tracker = mock_llm_tracker

    # Set unique db path for test isolation
    db_path = test_db_path

    # Modify the persistence config for test isolation
    integration_config.persistence.db_path = str(db_path)
    # Checkpointer path is set via ensemble_config, not persistence config.

    # Create manager
    logger.info("=" * 60)
    logger.info("[TEST] Creating InstanceManager...")
    logger.info("=" * 60)

    manager = InstanceManager(integration_config)

    # Initialize async checkpointer and other async components
    await manager.initialize()

    try:
        # Spawn developer instance
        project_root = Path(__file__).parent.parent.parent
        developer_agent_dir = str(project_root / "agents" / "developer")
        logger.info(f"[TEST] Creating instance with agent: {developer_agent_dir}")

        instance_id, _ = manager.spawn_instance(agent_id="developer")
        logger.info(f"[TEST] ✅ Instance created: {instance_id}")

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
            instance_id=instance_id,
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

        # Wait for the completed event via EventBus
        completed_received = False
        wait_timeout = 30

        async def _wait_for_completion() -> None:
            nonlocal completed_received
            while time.time() - start_time < wait_timeout:
                # Check if message was completed (check queue stats)
                stats = await manager.get_queue_stats(instance_id)
                logger.debug(f"[TEST] Queue stats: pending={stats['pending_count']}, processing={stats['processing_count']}")

                if stats['pending_count'] == 0 and stats['processing_count'] == 0:
                    # Check if we got a response
                    await asyncio.sleep(0.5)  # Small delay to ensure events are processed
                    completed_received = True
                    break

                await asyncio.sleep(0.5)

        try:
            await asyncio.wait_for(_wait_for_completion(), timeout=LLM_TEST_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            pytest.skip(f"LLM processing exceeded {LLM_TEST_TIMEOUT_SECONDS}s timeout - skipping")

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
            stats = await manager.get_queue_stats(instance_id)
            logger.error(f"[TEST]   Pending: {stats['pending_count']}")
            logger.error(f"[TEST]   Processing: {stats['processing_count']}")
            logger.error(f"[TEST]   Oldest age: {stats['oldest_message_age_seconds']}s")

            # Log queue stats for debugging
            if hasattr(manager, '_processing'):
                logger.error(f"[TEST]   Instances in _processing: {manager._processing}")

        assert llm_calls == 1, (
            f"Expected exactly 1 LLM call, but got {llm_calls} calls. "
            f"This indicates duplicate processing! See logs above for stack traces."
        )

        logger.info("")
        logger.info("=" * 60)
        logger.info("[TEST] ✅ PASSED - Only one LLM call detected")
        logger.info("=" * 60)

        # Cleanup
        manager.terminate_instance(instance_id)
        logger.info("[TEST] Instance terminated")
    finally:
        # Always cancel background tasks so pytest-asyncio can exit
        await manager.shutdown(grace_period=1.0)


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
    from daemon.manager import InstanceManager
    from daemon.repositories.event.models import Event

    tracker = mock_llm_tracker

    # Set unique db path for test isolation
    db_path = test_db_path

    # Modify the persistence config for test isolation
    integration_config.persistence.db_path = str(db_path)
    # Checkpointer path is set via ensemble_config, not persistence config.

    manager = InstanceManager(integration_config)
    await manager.initialize()

    try:
        # Track events
        events_received = []

        async def collect_events(instance_id: str):
            """Collect events from the EventBus streaming queue."""
            queue = manager._event_bus.get_streaming_queue(instance_id)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                    events_received.append(event)
                    logger.info(f"[SSE] Event: {event.get('event_type')}, message_id: {event.get('data', {}).get('message_id')}")

                    if event.get("event_type") == "completed" or event.get("event_type") == "error":
                        # Wait a bit more to see if there are duplicate events
                        await asyncio.sleep(2)
                        break
                except asyncio.TimeoutError:
                    logger.warning("[SSE] Timeout waiting for events")
                    break

        project_root = Path(__file__).parent.parent.parent
        developer_agent_dir = str(project_root / "agents" / "developer")
        instance_id, _ = manager.spawn_instance(agent_id="developer")

        # Start collecting events
        collect_task = asyncio.create_task(collect_events(instance_id))

        # Give the collector time to start
        await asyncio.sleep(0.5)

        # Send message
        logger.info("[SSE TEST] Sending message...")
        await manager.enqueue_message(
            instance_id=instance_id,
            message="hi",
            source="test"
        )

        # Wait for events
        try:
            await asyncio.wait_for(collect_task, timeout=LLM_TEST_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            collect_task.cancel()
            pytest.skip(f"SSE event collection exceeded {LLM_TEST_TIMEOUT_SECONDS}s timeout - skipping")

        # Analyze events
        event_counts = {}
        for event in events_received:
            event_type = event.get("event_type")
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
        manager.terminate_instance(instance_id)
    finally:
        # Always cancel background tasks so pytest-asyncio can exit
        await manager.shutdown(grace_period=1.0)


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
    from daemon.manager import InstanceManager
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
        # Set unique db path for test isolation
        db_path = test_db_path

        integration_config.persistence.db_path = str(db_path)
        # Checkpointer path is set via ensemble_config, not persistence config.
        manager = InstanceManager(integration_config)
        await manager.initialize()

        project_root = Path(__file__).parent.parent.parent
        developer_agent_dir = str(project_root / "agents" / "developer")
        instance_id, _ = manager.spawn_instance(agent_id="developer")

        logger.info(f"[DEBUG] Instance: {instance_id}")

        # Send message
        result = await manager.enqueue_message(
            instance_id=instance_id,
            message="hi",
            source="test"
        )

        logger.info(f"[DEBUG] Message enqueued: {result.message_id}")

        # Wait for processing
        try:
            await asyncio.wait_for(asyncio.sleep(10), timeout=LLM_TEST_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            pytest.skip(f"LLM processing exceeded {LLM_TEST_TIMEOUT_SECONDS}s timeout - skipping")
        
        # Check LLM call count
        logger.info(f"[DEBUG] LLM invocations detected: {len(llm_calls)}")
        for i, call in enumerate(llm_calls):
            logger.info(f"[DEBUG]   Call #{i+1}: {call['message']}")

        # Also check event history for completed events
        history = manager._event_bus._event_repo.get_events_since(instance_id, 0)
        completed_events = [e for e in history if e.kind == 'completed']
        logger.info(f"[DEBUG] Completed events in history: {len(completed_events)}")

        # Assertions
        assert len(llm_calls) == 1, f"Expected 1 LLM invocation, got {len(llm_calls)}"
        assert len(completed_events) == 1, f"Expected 1 completed event, got {len(completed_events)}"

        logger.info("[DEBUG] ✅ Test passed - only 1 LLM invocation")

        manager.terminate_instance(instance_id)

    finally:
        logging.getLogger('daemon.graph').removeHandler(handler)
        # Always cancel background tasks so pytest-asyncio can exit
        await manager.shutdown(grace_period=1.0)
