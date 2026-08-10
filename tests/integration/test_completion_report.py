"""
End-to-end test for sub-agent completion reporting.

This test:
1. Creates a Leader instance
2. Sends a message asking Leader to spawn Developer and say "hi"
3. Waits for Developer to complete and send report back to Leader
4. Verifies Leader receives the completion report

Run with:
    pytest tests/integration/test_completion_report.py -v -s --log-cli-level=DEBUG

Or with integration marker:
    pytest tests/integration/test_completion_report.py -v -s -m integration
"""

import os
import pytest
import asyncio
import logging
import time
from pathlib import Path
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
    db_path = project_root / "test_e2e_completion_report.db"
    db_path.unlink(missing_ok=True)
    yield db_path
    # Cleanup
    if db_path.exists():
        db_path.unlink()


@pytest.mark.asyncio
async def test_leader_spawns_developer_and_receives_report(
    integration_config,
    test_db_path
):
    """
    End-to-end test: Leader spawns Developer, Developer completes, report sent back.
    
    Flow:
    1. Create Leader instance
    2. Send message: "Spawn a Developer agent and tell it to say 'hi'"
    3. Leader spawns Developer (child instance)
    4. Leader sends "hi" to Developer via send_message
    5. Developer processes message and queue becomes empty
    6. Developer's completion triggers _send_completion_report()
    7. Leader receives report in its queue: "Developer has done: ..."
    8. Leader processes the report message
    """
    from daemon.manager import InstanceManager
    
    # Modify the persistence config for test isolation
    integration_config.persistence.db_path = str(test_db_path)
    
    # Create manager
    logger.info("=" * 70)
    logger.info("[TEST] Creating InstanceManager...")
    logger.info("=" * 70)
    
    manager = InstanceManager(integration_config)
    await manager.initialize()
    
    project_root = Path(__file__).parent.parent.parent
    
    # Spawn Leader instance
    leader_agent_dir = str(project_root / "agents" / "leader")
    logger.info(f"[TEST] Creating Leader instance with agent: {leader_agent_dir}")
    
    leader_instance_id, _ = manager.spawn_instance(agent_id="leader")
    logger.info(f"[TEST] ✅ Leader instance created: {leader_instance_id[:8]}...")
    
    # Track all events
    events_received = []
    
    async def collect_events(instance_id: str, stop_event: asyncio.Event):
        """Collect events from the EventBus streaming queue."""
        queue = manager._event_bus.get_streaming_queue(instance_id)
        while not stop_event.is_set():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2)
                events_received.append(event)
                logger.info(f"[EVENT] {event.get('event_type')} | instance: {event.get('instance_id', '')[:8]}... | msg: {event.get('data', {}).get('message_id', 'N/A')[:8]}...")
                
                data = event.get("data") or {}
                if data:
                    # Log key data
                    if event.get("event_type") == "status_changed" and data.get("type") == "completion_report":
                        logger.info(f"[EVENT] 📋 COMPLETION REPORT received!")
                        logger.info(f"[EVENT]    Agent: {data.get('agent_name')}")
                        logger.info(f"[EVENT]    Summary: {str(data.get('summary'))[:100]}...")
            except asyncio.TimeoutError:
                continue
    
    # Start event collector for Leader
    stop_event = asyncio.Event()
    collect_task = asyncio.create_task(collect_events(leader_instance_id, stop_event))
    
    # Give the collector time to start
    await asyncio.sleep(0.5)
    
    # =========================================================================
    # STEP 1: Send task to Leader
    # =========================================================================
    logger.info("")
    logger.info("=" * 70)
    logger.info("[TEST] STEP 1: Sending task to Leader...")
    logger.info("=" * 70)
    
    task_message = """Spawn a Developer agent and send it a message saying "hi". 
Wait for it to respond. Do not use any other tools."""
    
    start_time = time.time()
    
    result = await manager.enqueue_message(
        instance_id=leader_instance_id,
        message=task_message,
        source="test"
    )
    
    logger.info(f"[TEST] Message enqueued: {result.message_id[:8]}...")
    
    # =========================================================================
    # STEP 2: Wait for completion report
    # =========================================================================
    logger.info("")
    logger.info("=" * 70)
    logger.info("[TEST] STEP 2: Waiting for completion report (max 120s)...")
    logger.info("=" * 70)
    
    completion_report_received = False
    max_wait = 120  # 2 minutes max
    
    while time.time() - start_time < max_wait:
        # Check for completion report in Leader's queue
        # The report is enqueued as a regular message
        stats = await manager.get_queue_stats(leader_instance_id)
        
        # Check if we received a completion report event
        for event in events_received:
            if (event.type == "status_changed" and 
                event.data and 
                event.data.get("type") == "completion_report"):
                completion_report_received = True
                logger.info(f"[TEST] ✅ Completion report event detected!")
                break
        
        if completion_report_received:
            break
        
        # Also check Leader's messages for report content
        messages = manager.get_messages(leader_instance_id)
        for msg in messages:
            content = msg.get("content", "")
            if "has done:" in content.lower() or "developer" in content.lower():
                if "report:" in msg.get("role", "").lower() or len(content) > 50:
                    logger.info(f"[TEST] Found potential report message: {content[:100]}...")
        
        await asyncio.sleep(2)
        
        # Log progress
        elapsed = time.time() - start_time
        if int(elapsed) % 10 == 0:
            logger.info(f"[TEST] Still waiting... {elapsed:.0f}s elapsed, events: {len(events_received)}")
    
    # Stop event collector
    stop_event.set()
    collect_task.cancel()
    
    elapsed = time.time() - start_time
    logger.info(f"[TEST] Wait completed in {elapsed:.2f}s")
    
    # =========================================================================
    # STEP 3: Verify results
    # =========================================================================
    logger.info("")
    logger.info("=" * 70)
    logger.info("[TEST] STEP 3: Verification")
    logger.info("=" * 70)
    
    # Check Leader's metadata for children
    leader_meta = manager.get_instance_info(leader_instance_id)
    logger.info(f"[TEST] Leader has {len(leader_meta.get('children', []))} child instance(s)")
    
    if leader_meta.get('children'):
        for child_id in leader_meta['children']:
            child_meta = manager.get_instance_info(child_id)
            logger.info(f"[TEST]   Child: {child_meta.get('agent_name')} ({child_id[:8]}...)")
    
    # Log all events
    logger.info("")
    logger.info("[TEST] Events received:")
    event_counts = {}
    for event in events_received:
        event_type = event.type
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
    for event_type, count in sorted(event_counts.items()):
        logger.info(f"  {event_type}: {count}")
    
    # Check for completion report in events
    report_events = [
        e for e in events_received 
        if e.type == "status_changed" and e.data and e.data.get("type") == "completion_report"
    ]
    
    logger.info("")
    logger.info(f"[TEST] Completion report events: {len(report_events)}")
    
    if report_events:
        for event in report_events:
            logger.info(f"[TEST]   📋 Report from: {event.data.get('agent_name')}")
            logger.info(f"[TEST]   📋 Summary: {event.data.get('summary')}")
    
    # Check Leader's message history for report
    leader_messages = manager.get_messages(leader_instance_id)
    logger.info(f"[TEST] Leader has {len(leader_messages)} messages")
    
    report_in_messages = False
    for msg in leader_messages:
        content = msg.get("content", "")
        if "has done:" in content:
            report_in_messages = True
            logger.info(f"[TEST] 📋 Found report in messages: {content[:200]}...")
    
    # =========================================================================
    # ASSERTIONS
    # =========================================================================
    logger.info("")
    logger.info("=" * 70)
    logger.info("[TEST] Assertions")
    logger.info("=" * 70)
    
    # 1. Leader should have spawned at least one child
    assert leader_meta.get('children'), "Leader should have spawned child instances"
    logger.info("[TEST] ✅ Leader spawned child instance(s)")
    
    # 2. Completion report should be received
    assert completion_report_received or report_in_messages or len(report_events) > 0, \
        "Leader should have received a completion report from Developer"
    logger.info("[TEST] ✅ Completion report received")
    
    # 3. Report should mention Developer
    if report_events:
        report_summary = report_events[0].data.get('summary', '')
        assert 'Developer' in report_summary or 'developer' in report_summary.lower() or 'hi' in report_summary.lower(), \
            f"Report should mention Developer or the task. Got: {report_summary}"
        logger.info("[TEST] ✅ Report mentions Developer/task")
    
    logger.info("")
    logger.info("=" * 70)
    logger.info("[TEST] ✅ ALL TESTS PASSED")
    logger.info("=" * 70)
    
    # Cleanup
    manager.terminate_instance(leader_instance_id)
    # Also terminate any child instances
    if leader_meta.get('children'):
        for child_id in leader_meta['children']:
            try:
                manager.terminate_instance(child_id)
            except Exception as e:
                logger.warning(f"[TEST] Could not terminate child {child_id[:8]}...: {e}")
    logger.info("[TEST] Instances terminated")


@pytest.mark.asyncio
async def test_completion_report_message_format(
    integration_config,
    test_db_path
):
    """
    Test that completion report has correct format: "{AgentName} has done: {summary}"
    
    This test directly spawns a Developer as a child of Leader, sends a message,
    and verifies the report format when Developer completes.
    """
    from daemon.manager import InstanceManager
    
    integration_config.persistence.db_path = str(test_db_path)
    
    manager = InstanceManager(integration_config)
    await manager.initialize()
    
    project_root = Path(__file__).parent.parent.parent
    
    # Create Leader (parent)
    leader_agent_dir = str(project_root / "agents" / "leader")
    leader_instance_id, _ = manager.spawn_instance(agent_id="leader")
    
    # Create Developer as child of Leader
    developer_agent_dir = str(project_root / "agents" / "developer")
    developer_instance_id, _ = manager.spawn_instance(
        agent_id="developer",
        parent_id=leader_instance_id
    )
    
    logger.info(f"[TEST] Leader: {leader_instance_id[:8]}...")
    logger.info(f"[TEST] Developer (child): {developer_instance_id[:8]}...")
    
    # Verify parent-child relationship
    developer_meta = manager.get_instance_info(developer_instance_id)
    assert developer_meta['parent_id'] == leader_instance_id, "Developer should have Leader as parent"
    assert developer_meta['agent_name'] == 'Developer', "Agent name should be 'Developer'"
    logger.info("[TEST] ✅ Parent-child relationship verified")
    
    # Track events for completion report
    report_received = asyncio.Event()
    report_data = {}
    
    async def wait_for_report():
        """Wait for completion report event."""
        queue = manager._event_bus.get_streaming_queue(leader_instance_id)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=60)
                logger.info(f"[EVENT] {event.get('event_type')}")
                if (event.get("event_type") == "status_changed" and 
                    event.get("data") and 
                    event.get("data").get("type") == "completion_report"):
                    report_data['event'] = event
                    report_received.set()
                    return
            except asyncio.TimeoutError:
                logger.warning("[TEST] Timeout waiting for report event")
                return
    
    # Start waiting for report
    report_task = asyncio.create_task(wait_for_report())
    await asyncio.sleep(0.5)
    
    # Send message to Developer
    logger.info("[TEST] Sending message to Developer...")
    await manager.enqueue_message(
        instance_id=developer_instance_id,
        message="Say hello and tell me what you can do.",
        source="test"
    )
    
    # Wait for Developer to process and send report
    logger.info("[TEST] Waiting for Developer to complete and send report...")
    
    # Wait for queue to be empty (indicates completion)
    start_time = time.time()
    while time.time() - start_time < 60:
        stats = await manager.get_queue_stats(developer_instance_id)
        if stats.pending_count == 0 and stats.processing_count == 0:
            logger.info("[TEST] Developer queue is empty")
            # Give time for report to be sent
            await asyncio.sleep(3)
            break
        await asyncio.sleep(1)
    
    # Wait a bit more for report event
    try:
        await asyncio.wait_for(report_received.wait(), timeout=10)
    except asyncio.TimeoutError:
        pass
    
    # Check Leader's queue for report message
    leader_stats = await manager.get_queue_stats(leader_instance_id)
    logger.info(f"[TEST] Leader queue stats: pending={leader_stats.pending_count}, processing={leader_stats.processing_count}")
    
    # Check for report in Leader's queue (direct DB query)
    cursor = manager.conn.execute(
        "SELECT content, source, metadata FROM message_queue WHERE instance_id = ? AND source LIKE 'internal_report:%'",
        (leader_instance_id,)
    )
    report_rows = cursor.fetchall()
    
    if report_rows:
        for row in report_rows:
            content, source, metadata = row
            logger.info(f"[TEST] Found report in Leader's queue:")
            logger.info(f"[TEST]   Source: {source}")
            logger.info(f"[TEST]   Content: {content}")
            
            # Verify format
            assert "has done:" in content, f"Report should contain 'has done:', got: {content}"
            assert "Developer" in content, f"Report should mention 'Developer', got: {content}"
            logger.info("[TEST] ✅ Report format verified")
    
    # Also check via event if we got it
    if report_data.get('event'):
        event = report_data['event']
        summary = event.data.get('summary', '')
        logger.info(f"[TEST] Report via event: {summary}")
        assert "has done:" in summary, f"Summary should contain 'has done:', got: {summary}"
        logger.info("[TEST] ✅ Event report format verified")
    
    # Cleanup
    manager.terminate_instance(developer_instance_id)
    manager.terminate_instance(leader_instance_id)
    logger.info("[TEST] ✅ Test completed")


# =============================================================================
# Unhappy-path report repair integration test
#
# This test does NOT require a live LLM — it mocks the LLM call but uses a
# real ``ChildReportsService`` with mocked checkpointer to verify the
# ``_get_last_assistant_message_raw`` repair path end-to-end. It exercises
# the same code path that runs when a real child instance completes with a
# truncated final message.
# =============================================================================


@pytest.mark.asyncio
async def test_unhappy_path_report_repair_returns_repaired_content():
    """Child produces short last message after 2 long messages → parent receives repaired content.

    This test constructs a real ``ChildReportsService`` with a mock checkpointer
    that returns a realistic message history (2 long assistant messages +
    1 short sign-off). It verifies the repair path triggers and the parent
    would receive repaired (or combined) content — NOT just the short last
    message.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from daemon.services.child_reports import ChildReportsService
    from daemon.config import Config

    # Build a realistic message history where the child did substantive
    # work in messages n-2 and n-1, then sent a short sign-off.
    long_1 = " ".join(f"word{i}" for i in range(50))
    long_2 = " ".join(f"step{i}" for i in range(40))
    short_signoff = "done"

    messages = [
        {"role": "user", "content": "Please implement feature X"},
        {"role": "assistant", "content": long_1},
        {"role": "user", "content": "Looks good, continue"},
        {"role": "assistant", "content": long_2},
        {"role": "assistant", "content": short_signoff},
    ]

    manager = MagicMock(name="InstanceManager")
    manager.config = Config()
    checkpointer_adapter = MagicMock()
    checkpointer_adapter.raw_saver = MagicMock()
    manager._checkpointer = checkpointer_adapter

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None

    # Mock LLM to return a repaired report
    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke = MagicMock(
        return_value=MagicMock(content="Repaired: I implemented feature X with tests.")
    )
    mock_llm_class = MagicMock(return_value=mock_llm_instance)

    with (
        patch(
            "daemon.services.child_reports.get_instance_messages",
            new=AsyncMock(return_value=messages),
        ),
        patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ),
    ):
        result = await service._get_last_assistant_message_raw("child-instance-123")

    # The parent should receive the repaired content, NOT just "done"
    assert result is not None
    assert result != short_signoff
    assert "feature X" in result or long_1[:20] in result or long_2[:20] in result
    # LLM was actually called
    mock_llm_class.assert_called_once()


@pytest.mark.asyncio
async def test_unhappy_path_report_repair_combine_fallback():
    """When LLM fails, the combine fallback ensures the parent gets full content.

    The parent should still receive content containing the substantive work
    from earlier messages, not just the short sign-off.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    from daemon.services.child_reports import ChildReportsService
    from daemon.config import Config

    # Earlier messages are padded to trigger the truncation heuristic.
    # Spec (2026-08-08): no earlier_wc floor — substantive earlier messages
    # trigger repair purely on the 2× ratio. The padding mirrors the W5-era
    # setup but is no longer strictly required; kept for readability.
    long_1 = "alpha report content detailed findings " + " ".join(f"item{i}" for i in range(20))
    long_2 = "beta report content implementation details " + " ".join(f"item{i}" for i in range(20))
    short_signoff = "ok"

    messages = [
        {"role": "assistant", "content": long_1},
        {"role": "assistant", "content": long_2},
        {"role": "assistant", "content": short_signoff},
    ]

    manager = MagicMock(name="InstanceManager")
    manager.config = Config()
    checkpointer_adapter = MagicMock()
    checkpointer_adapter.raw_saver = MagicMock()
    manager._checkpointer = checkpointer_adapter

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None

    # LLM fails → combine fallback should include the substantive content
    mock_llm_class = MagicMock(side_effect=RuntimeError("LLM unavailable"))

    with (
        patch(
            "daemon.services.child_reports.get_instance_messages",
            new=AsyncMock(return_value=messages),
        ),
        patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ),
    ):
        result = await service._get_last_assistant_message_raw("child-instance-456")

    assert result is not None
    assert result != short_signoff
    # Combined content should include both long messages
    assert "alpha report content" in result
    assert "beta report content" in result


if __name__ == "__main__":
    # Run directly for debugging
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s", "--log-cli-level=DEBUG"]))
