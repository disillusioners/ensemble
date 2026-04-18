"""
End-to-end test for instance title generation feature.

This test:
1. Creates an instance using real config
2. Sends "hi" as the first message
3. Waits for processing to complete
4. Verifies that:
   - The instance has a title set (not None)
   - The title is stored in metadata
   - A `title_updated` SSE event was broadcast
   - The title appears in `list_instances()` response

Run with:
    pytest tests/integration/test_instance_title_e2e.py -v -s --run-integration
"""

import os
import pytest
import asyncio
import logging
import time
import uuid
from pathlib import Path


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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

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
def test_db_path(tmp_path):
    """Return a unique test database path per test and cleanup after."""
    import uuid
    # Use unique path per test to avoid database corruption between tests
    db_path = tmp_path / f"test_title_{uuid.uuid4().hex[:8]}.db"
    # Also set checkpointer db path to avoid conflicts
    checkpointer_path = tmp_path / f"test_checkpoints_{uuid.uuid4().hex[:8]}.db"
    yield db_path, checkpointer_path
    # Cleanup is automatic with tmp_path


@pytest.mark.asyncio
async def test_instance_title_generation_e2e(
    integration_config,
    test_db_path
):
    """
    Test that instance title is generated after first message.
    
    Verifies:
    1. Instance has title set after first message
    2. Title is stored in metadata
    3. title_updated SSE event is broadcast
    4. Title appears in list_instances()
    """
    from daemon.manager import InstanceManager
    
    # Use the manager's instance repository instead of standalone functions
    # The manager._instance_repository is a SQLModelInstanceRepository
    
    # Unpack unique db paths
    db_path, checkpointer_path = test_db_path
    
    # Modify the persistence config for test isolation
    integration_config.persistence.db_path = str(db_path)
    integration_config.persistence.checkpointer_db_path = str(checkpointer_path)
    
    # Create manager
    logger.info("[TEST] Creating InstanceManager...")
    manager = InstanceManager(integration_config)
    
    # Initialize async components (checkpointer, etc.)
    await manager.initialize()
    
    # Spawn coder instance
    project_root = Path(__file__).parent.parent.parent
    coder_agent_dir = str(project_root / "agents" / "coder")
    logger.info(f"[TEST] Creating instance with agent: {coder_agent_dir}")
    
    instance_id = manager.spawn_instance(agent_id="coder")
    logger.info(f"[TEST] Instance created: {instance_id}")
    
    # Verify initial state - no title
    instance = manager._instance_repository.get(instance_id)
    assert instance is not None
    initial_meta = instance.instance_metadata or {}
    assert initial_meta.get("title") is None, "Title should be None before first message"
    logger.info(f"[TEST] Initial title: {initial_meta.get('title')}")
    
    # Track events
    events_received = []
    title_updated_received = False
    
    async def collect_events(instance_id: str):
        """Collect events from the EventBus streaming queue."""
        nonlocal title_updated_received
        queue = manager._event_bus.get_streaming_queue(instance_id)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=60)
                events_received.append(event)
                logger.info(f"[SSE] Event: {event.get('event_type')}, instance_id: {event.get('instance_id')}")
                
                if event.get("event_type") == "title_updated":
                    title_updated_received = True
                    logger.info(f"[SSE] Title updated event received: {event.get('data')}")
                
                if event.get("event_type") == "completed":
                    # Wait a bit more to ensure all events are collected
                    await asyncio.sleep(1)
                    break
            except asyncio.TimeoutError:
                logger.warning("[SSE] Timeout waiting for events")
                break
    
    # Start collecting events
    collect_task = asyncio.create_task(collect_events(instance_id))
    
    # Give the collector time to start
    await asyncio.sleep(0.5)
    
    # =================================================================
    # SEND FIRST MESSAGE
    # =================================================================
    logger.info("")
    logger.info("=" * 60)
    logger.info("[TEST] Sending first message 'hi'...")
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
    logger.info("[TEST] Waiting for message processing (max 60s)...")
    logger.info("=" * 60)
    
    # Wait for the completed event via EventBus
    wait_timeout = 60
    
    while time.time() - start_time < wait_timeout:
        # Check if message was completed (check queue stats)
        stats = manager.get_queue_stats(instance_id)
        logger.debug(f"[TEST] Queue stats: pending={stats['pending_count']}, processing={stats['processing_count']}")
        
        if stats['pending_count'] == 0 and stats['processing_count'] == 0:
            # Check if we got a response
            await asyncio.sleep(1)  # Small delay to ensure events are processed
            break
        
        await asyncio.sleep(0.5)
    
    elapsed = time.time() - start_time
    logger.info(f"[TEST] Processing completed in {elapsed:.2f}s")
    
    # Wait for event collection to finish
    await collect_task
    
    # Wait for fire-and-forget title generation to complete (happens after completed event)
    # Title generation has a 30s timeout, so wait up to 35s for it
    await asyncio.sleep(35)
    
    # =================================================================
    # VERIFY RESULTS
    # =================================================================
    logger.info("")
    logger.info("=" * 60)
    logger.info("[TEST] VERIFICATION")
    logger.info("=" * 60)
    
    # 1. Check that title_updated event was broadcast
    logger.info(f"[TEST] title_updated event received: {title_updated_received}")
    assert title_updated_received, "title_updated event should be broadcast"
    
    # 2. Check that title is set in metadata
    final_instance = manager._instance_repository.get(instance_id)
    final_meta = final_instance.instance_metadata
    logger.info(f"[TEST] Final title from metadata: {final_meta['title']}")
    assert final_meta is not None
    assert final_meta["title"] is not None, "Title should be set after first message"
    assert len(final_meta["title"]) > 0, "Title should not be empty"
    
    # 3. Check that title appears in list_instances()
    instances_list, total = manager._instance_repository.list()
    logger.info(f"[TEST] Total instances: {total}")
    
    # Convert to dict format for compatibility with existing test logic
    instances = [
        {
            "instance_id": inst.instance_id,
            "title": inst.instance_metadata.get("title"),
        }
        for inst in instances_list
    ]
    
    instance = next((s for s in instances if s["instance_id"] == instance_id), None)
    assert instance is not None, "Instance should exist in list"
    
    logger.info(f"[TEST] Title from list_instances: {instance['title']}")
    assert instance["title"] is not None, "Title should appear in list_instances()"
    assert instance["title"] == final_meta["title"], "Title should match between metadata and list"
    
    # 4. Check events received
    event_types = [e.get("event_type") for e in events_received]
    logger.info(f"[TEST] Events received: {event_types}")
    
    assert "title_updated" in event_types, "title_updated event should be in events"
    assert "completed" in event_types, "completed event should be in events"
    
    logger.info("")
    logger.info("=" * 60)
    logger.info(f"[TEST] ✅ PASSED - Title generated: '{final_meta['title']}'")
    logger.info("=" * 60)
    
    # Cleanup
    manager.terminate_instance(instance_id)
    logger.info("[TEST] Instance terminated")


@pytest.mark.asyncio
async def test_instance_title_not_regenerated(
    integration_config,
    test_db_path
):
    """
    Test that title is not regenerated if it already exists.
    
    This tests the logic that skips title generation when title already exists.
    """
    from daemon.manager import InstanceManager
    # Use manager._instance_repository instead of standalone persistence functions
    
    # Unpack unique db paths
    db_path, checkpointer_path = test_db_path
    
    # Modify the persistence config for test isolation
    integration_config.persistence.db_path = str(db_path)
    integration_config.persistence.checkpointer_db_path = str(checkpointer_path)
    
    # Create manager
    logger.info("[TEST] Creating InstanceManager...")
    manager = InstanceManager(integration_config)
    await manager.initialize()
    
    # Spawn coder instance
    project_root = Path(__file__).parent.parent.parent
    coder_agent_dir = str(project_root / "agents" / "coder")
    
    instance_id = manager.spawn_instance(agent_id="coder")
    
    # Pre-set a title before sending any messages
    manager._instance_repository.update_title(instance_id, "Pre-set Title")
    logger.info(f"[TEST] Pre-set title: 'Pre-set Title'")
    
    # Verify title is set
    instance = manager._instance_repository.get(instance_id)
    assert instance.instance_metadata["title"] == "Pre-set Title"
    
    # Send first message
    result = await manager.enqueue_message(
        instance_id=instance_id,
        message="hi",
        source="test"
    )
    
    # Wait for processing
    start_time = time.time()
    wait_timeout = 60
    
    while time.time() - start_time < wait_timeout:
        stats = manager.get_queue_stats(instance_id)
        if stats['pending_count'] == 0 and stats['processing_count'] == 0:
            await asyncio.sleep(1)
            break
        await asyncio.sleep(0.5)
    
    # Verify title is still the pre-set one (not regenerated)
    final_instance = manager._instance_repository.get(instance_id)
    final_meta = final_instance.instance_metadata
    logger.info(f"[TEST] Final title: {final_meta['title']}")
    
    # The title should NOT have been overwritten by a new generated title
    # (it should remain "Pre-set Title")
    assert final_meta["title"] == "Pre-set Title", \
        f"Title should not be overwritten. Expected 'Pre-set Title', got '{final_meta['title']}'"
    
    logger.info("[TEST] ✅ PASSED - Title was not regenerated")
    
    # Cleanup
    manager.terminate_instance(instance_id)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
