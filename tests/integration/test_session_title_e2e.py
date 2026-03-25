"""
End-to-end test for session title generation feature.

This test:
1. Creates a session using real config
2. Sends "hi" as the first message
3. Waits for processing to complete
4. Verifies that:
   - The session has a title set (not None)
   - The title is stored in metadata
   - A `title_updated` SSE event was broadcast
   - The title appears in `list_sessions()` response

Run with:
    pytest tests/integration/test_session_title_e2e.py -v -s --run-integration
"""

import os
import pytest
import asyncio
import logging
import time
from pathlib import Path

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
def test_db_path():
    """Return a test database path and cleanup after."""
    project_root = Path(__file__).parent.parent.parent
    db_path = project_root / "test_e2e_session_title.db"
    db_path.unlink(missing_ok=True)
    yield db_path
    # Cleanup
    if db_path.exists():
        db_path.unlink()


@pytest.mark.asyncio
async def test_session_title_generation_e2e(
    integration_config,
    test_db_path
):
    """
    Test that session title is generated after first message.
    
    Verifies:
    1. Session has title set after first message
    2. Title is stored in metadata
    3. title_updated SSE event is broadcast
    4. Title appears in list_sessions()
    """
    from daemon.manager import SessionManager
    from daemon.persistence import get_session_metadata, list_all_sessions
    
    # Modify the persistence config for test isolation
    integration_config.persistence.db_path = str(test_db_path)
    
    # Create manager
    logger.info("[TEST] Creating SessionManager...")
    manager = SessionManager(integration_config)
    
    # Ensure main loop is set for event broadcasting
    manager.broadcaster.set_main_loop(asyncio.get_running_loop())
    
    # Spawn coder session
    project_root = Path(__file__).parent.parent.parent
    coder_agent_dir = str(project_root / "agents" / "coder")
    logger.info(f"[TEST] Creating session with agent: {coder_agent_dir}")
    
    session_id = manager.spawn_session(agent_id="coder")
    logger.info(f"[TEST] Session created: {session_id}")
    
    # Verify initial state - no title
    initial_meta = get_session_metadata(manager.conn, session_id)
    assert initial_meta is not None
    assert initial_meta["title"] is None, "Title should be None before first message"
    logger.info(f"[TEST] Initial title: {initial_meta['title']}")
    
    # Track events
    events_received = []
    title_updated_received = False
    
    async def collect_events(session_id: str):
        """Collect events from the broadcaster."""
        nonlocal title_updated_received
        queue = await manager.broadcaster.get_queue(session_id)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=60)
                events_received.append(event)
                logger.info(f"[SSE] Event: {event.type}, session_id: {event.session_id}")
                
                if event.type == "title_updated":
                    title_updated_received = True
                    logger.info(f"[SSE] Title updated event received: {event.data}")
                
                if event.type == "completed":
                    # Wait a bit more to ensure all events are collected
                    await asyncio.sleep(1)
                    break
            except asyncio.TimeoutError:
                logger.warning("[SSE] Timeout waiting for events")
                break
    
    # Start collecting events
    collect_task = asyncio.create_task(collect_events(session_id))
    
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
    logger.info("[TEST] Waiting for message processing (max 60s)...")
    logger.info("=" * 60)
    
    # Wait for the completed event via broadcaster
    wait_timeout = 60
    
    while time.time() - start_time < wait_timeout:
        # Check if message was completed (check queue stats)
        stats = manager.get_queue_stats(session_id)
        logger.debug(f"[TEST] Queue stats: pending={stats.pending_count}, processing={stats.processing_count}")
        
        if stats.pending_count == 0 and stats.processing_count == 0:
            # Check if we got a response
            await asyncio.sleep(1)  # Small delay to ensure events are processed
            break
        
        await asyncio.sleep(0.5)
    
    elapsed = time.time() - start_time
    logger.info(f"[TEST] Processing completed in {elapsed:.2f}s")
    
    # Wait for event collection to finish
    await collect_task
    
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
    final_meta = get_session_metadata(manager.conn, session_id)
    logger.info(f"[TEST] Final title from metadata: {final_meta['title']}")
    assert final_meta is not None
    assert final_meta["title"] is not None, "Title should be set after first message"
    assert len(final_meta["title"]) > 0, "Title should not be empty"
    
    # 3. Check that title appears in list_sessions()
    sessions = list_all_sessions(manager.conn)
    logger.info(f"[TEST] Total sessions: {len(sessions)}")
    
    session = next((s for s in sessions if s["session_id"] == session_id), None)
    assert session is not None, "Session should exist in list"
    
    logger.info(f"[TEST] Title from list_sessions: {session['title']}")
    assert session["title"] is not None, "Title should appear in list_sessions()"
    assert session["title"] == final_meta["title"], "Title should match between metadata and list"
    
    # 4. Check events received
    event_types = [e.type for e in events_received]
    logger.info(f"[TEST] Events received: {event_types}")
    
    assert "title_updated" in event_types, "title_updated event should be in events"
    assert "completed" in event_types, "completed event should be in events"
    
    logger.info("")
    logger.info("=" * 60)
    logger.info(f"[TEST] ✅ PASSED - Title generated: '{final_meta['title']}'")
    logger.info("=" * 60)
    
    # Cleanup
    manager.terminate_session(session_id)
    logger.info("[TEST] Session terminated")


@pytest.mark.asyncio
async def test_session_title_not_regenerated(
    integration_config,
    test_db_path
):
    """
    Test that title is not regenerated if it already exists.
    
    This tests the logic that skips title generation when title already exists.
    """
    from daemon.manager import SessionManager
    from daemon.persistence import get_session_metadata, update_session_title
    
    # Modify the persistence config for test isolation
    integration_config.persistence.db_path = str(test_db_path)
    
    # Create manager
    logger.info("[TEST] Creating SessionManager...")
    manager = SessionManager(integration_config)
    manager.broadcaster.set_main_loop(asyncio.get_running_loop())
    
    # Spawn coder session
    project_root = Path(__file__).parent.parent.parent
    coder_agent_dir = str(project_root / "agents" / "coder")
    
    session_id = manager.spawn_session(agent_id="coder")
    
    # Pre-set a title before sending any messages
    update_session_title(manager.conn, session_id, "Pre-set Title")
    logger.info(f"[TEST] Pre-set title: 'Pre-set Title'")
    
    # Verify title is set
    meta = get_session_metadata(manager.conn, session_id)
    assert meta["title"] == "Pre-set Title"
    
    # Send first message
    result = await manager.enqueue_message(
        session_id=session_id,
        message="hi",
        source="test"
    )
    
    # Wait for processing
    start_time = time.time()
    wait_timeout = 60
    
    while time.time() - start_time < wait_timeout:
        stats = manager.get_queue_stats(session_id)
        if stats.pending_count == 0 and stats.processing_count == 0:
            await asyncio.sleep(1)
            break
        await asyncio.sleep(0.5)
    
    # Verify title is still the pre-set one (not regenerated)
    final_meta = get_session_metadata(manager.conn, session_id)
    logger.info(f"[TEST] Final title: {final_meta['title']}")
    
    # The title should NOT have been overwritten by a new generated title
    # (it should remain "Pre-set Title")
    assert final_meta["title"] == "Pre-set Title", \
        f"Title should not be overwritten. Expected 'Pre-set Title', got '{final_meta['title']}'"
    
    logger.info("[TEST] ✅ PASSED - Title was not regenerated")
    
    # Cleanup
    manager.terminate_session(session_id)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
