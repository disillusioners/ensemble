"""
End-to-end test for sub-agent completion reporting.

This test:
1. Creates a Leader instance
2. Sends a message asking Leader to spawn Coder and say "hi"
3. Waits for Coder to complete and send report back to Leader
4. Verifies Leader receives the completion report

Run with:
    pytest tests/integration/test_completion_report.py -v -s --log-cli-level=DEBUG
    
Or with integration marker:
    pytest tests/integration/test_completion_report.py -v -s --run-integration
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

# Skip if no API key
pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Set OPENAI_API_KEY to run integration tests"
)


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
async def test_leader_spawns_coder_and_receives_report(
    integration_config,
    test_db_path
):
    """
    End-to-end test: Leader spawns Coder, Coder completes, report sent back.
    
    Flow:
    1. Create Leader instance
    2. Send message: "Spawn a Coder agent and tell it to say 'hi'"
    3. Leader spawns Coder (child instance)
    4. Leader sends "hi" to Coder via send_message
    5. Coder processes message and queue becomes empty
    6. Coder's completion triggers _send_completion_report()
    7. Leader receives report in its queue: "Coder has done: ..."
    8. Leader processes the report message
    """
    from daemon.manager import InstanceManager
    from daemon.persistence import get_instance_metadata
    
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
    
    leader_instance_id = manager.spawn_instance(agent_id="leader")
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
    
    task_message = """Spawn a Coder agent and send it a message saying "hi". 
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
        stats = manager.get_queue_stats(leader_instance_id)
        
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
            if "has done:" in content.lower() or "coder" in content.lower():
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
    leader_meta = get_instance_metadata(manager.conn, leader_instance_id)
    logger.info(f"[TEST] Leader has {len(leader_meta.get('children', []))} child instance(s)")
    
    if leader_meta.get('children'):
        for child_id in leader_meta['children']:
            child_meta = get_instance_metadata(manager.conn, child_id)
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
        "Leader should have received a completion report from Coder"
    logger.info("[TEST] ✅ Completion report received")
    
    # 3. Report should mention Coder
    if report_events:
        report_summary = report_events[0].data.get('summary', '')
        assert 'Coder' in report_summary or 'coder' in report_summary.lower() or 'hi' in report_summary.lower(), \
            f"Report should mention Coder or the task. Got: {report_summary}"
        logger.info("[TEST] ✅ Report mentions Coder/task")
    
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
    
    This test directly spawns a Coder as a child of Leader, sends a message,
    and verifies the report format when Coder completes.
    """
    from daemon.manager import InstanceManager
    from daemon.persistence import get_instance_metadata
    
    integration_config.persistence.db_path = str(test_db_path)
    
    manager = InstanceManager(integration_config)
    await manager.initialize()
    
    project_root = Path(__file__).parent.parent.parent
    
    # Create Leader (parent)
    leader_agent_dir = str(project_root / "agents" / "leader")
    leader_instance_id = manager.spawn_instance(agent_id="leader")
    
    # Create Coder as child of Leader
    coder_agent_dir = str(project_root / "agents" / "coder")
    coder_instance_id = manager.spawn_instance(
        agent_id="coder",
        parent_id=leader_instance_id
    )
    
    logger.info(f"[TEST] Leader: {leader_instance_id[:8]}...")
    logger.info(f"[TEST] Coder (child): {coder_instance_id[:8]}...")
    
    # Verify parent-child relationship
    coder_meta = get_instance_metadata(manager.conn, coder_instance_id)
    assert coder_meta['parent_id'] == leader_instance_id, "Coder should have Leader as parent"
    assert coder_meta['agent_name'] == 'Coder', "Agent name should be 'Coder'"
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
    
    # Send message to Coder
    logger.info("[TEST] Sending message to Coder...")
    await manager.enqueue_message(
        instance_id=coder_instance_id,
        message="Say hello and tell me what you can do.",
        source="test"
    )
    
    # Wait for Coder to process and send report
    logger.info("[TEST] Waiting for Coder to complete and send report...")
    
    # Wait for queue to be empty (indicates completion)
    start_time = time.time()
    while time.time() - start_time < 60:
        stats = manager.get_queue_stats(coder_instance_id)
        if stats.pending_count == 0 and stats.processing_count == 0:
            logger.info("[TEST] Coder queue is empty")
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
    leader_stats = manager.get_queue_stats(leader_instance_id)
    logger.info(f"[TEST] Leader queue stats: pending={leader_stats.pending_count}, processing={leader_stats.processing_count}")
    
    # Check for report in Leader's queue (direct DB query)
    cursor = manager.conn.execute(
        "SELECT content, source, metadata FROM message_queue WHERE instance_id = ? AND source LIKE 'report:%'",
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
            assert "Coder" in content, f"Report should mention 'Coder', got: {content}"
            logger.info("[TEST] ✅ Report format verified")
    
    # Also check via event if we got it
    if report_data.get('event'):
        event = report_data['event']
        summary = event.data.get('summary', '')
        logger.info(f"[TEST] Report via event: {summary}")
        assert "has done:" in summary, f"Summary should contain 'has done:', got: {summary}"
        logger.info("[TEST] ✅ Event report format verified")
    
    # Cleanup
    manager.terminate_instance(coder_instance_id)
    manager.terminate_instance(leader_instance_id)
    logger.info("[TEST] ✅ Test completed")


if __name__ == "__main__":
    # Run directly for debugging
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s", "--log-cli-level=DEBUG"]))
