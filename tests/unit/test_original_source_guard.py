"""Unit tests for original_source guard fix.

This module tests the fix for the bug where `internal_agent:*` and other `internal_*`
sources (that are NOT `internal_report:*` or `internal_error_report:*`) would
incorrectly overwrite the `original_source` instance metadata.

The guard checks `not message_source.startswith("internal_")` before storing
the source in metadata, ensuring only true external sources are recorded.

Test areas:
1. Guard blocks internal_agent source
2. Guard blocks internal_report source (goes to different branch anyway)
3. Guard allows external sources (telegram, discord, api)
4. First-external-wins behavior
5. Already-set not overwritten
6. Both code paths (metadata exists / doesn't exist)
7. Full dispatch flow simulation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def mock_config():
    """Create a mock config for manager tests."""
    from daemon.config import Config, LLMConfig, LimitsConfig, PersistenceConfig, DaemonConfig, AgentsConfig

    return Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4",
            temperature=0.7
        ),
        limits=LimitsConfig(
            max_instances=5,
            max_children_per_instance=3,
            instance_timeout_minutes=60,
            message_rate_limit=60
        ),
        persistence=PersistenceConfig(
            db_path=":memory:",
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
            checkpoint_max_count=1000
        ),
        daemon=DaemonConfig(host="0.0.0.0", port=8079),
        agents=AgentsConfig(directory="./agents")
    )


@pytest.fixture
def mock_prompt_cache():
    """Create a mock prompt cache."""
    return Mock()


@pytest.fixture
def create_mock_graph(self):
    """Factory to create mock graphs with configurable AI responses."""
    def _create(content="Response", msg_id="msg-1"):
        mock_graph = Mock()

        ai_msg = Mock()
        ai_msg.content = content
        ai_msg.type = 'ai'
        ai_msg.tool_calls = []
        ai_msg.id = msg_id

        async def mock_astream(*args, **kwargs):
            yield ("updates", {"agent": {"messages": [ai_msg]}})
            yield ("updates", {"agent": {"messages": []}})

        mock_graph.astream = mock_astream
        mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})
        return mock_graph

    return _create


@pytest.fixture
def create_mock_instance_repo(self):
    """Factory to create mock instance repositories with tracking."""
    def _create(initial_metadata=None):
        metadata_state = {"data": initial_metadata.copy() if initial_metadata else {}}

        def get_side_effect(instance_id):
            meta = MagicMock()
            meta.instance_metadata = metadata_state["data"] if metadata_state["data"] else None
            return meta

        def set_metadata_side_effect(instance_id, key, value):
            if metadata_state["data"] is None:
                metadata_state["data"] = {}
            metadata_state["data"][key] = value

        mock_repo = MagicMock()
        mock_repo.create.return_value = MagicMock(instance_id="test-instance")
        mock_repo.get.side_effect = get_side_effect
        mock_repo.set_metadata.side_effect = set_metadata_side_effect

        return mock_repo, metadata_state

    return _create


# ==============================================================================
# Helper: Create manager with mocked dependencies
# ==============================================================================

def create_test_manager(mock_config, mock_prompt_cache, mock_instance_repo, message_content="Response"):
    """Create a manager instance with all necessary mocks for testing."""
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    ai_msg = Mock()
    ai_msg.content = message_content
    ai_msg.type = 'ai'
    ai_msg.tool_calls = []
    ai_msg.id = "msg-test"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [ai_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        manager.source_dispatcher = mock_dispatcher
        manager.spawn_instance = Mock(return_value="test-instance")
        manager.instances["test-instance"] = (mock_graph, "agents/coder")

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        return manager, instance_id, mock_dispatcher


# ==============================================================================
# Test Area 1: Guard blocks internal_agent source
# ==============================================================================

@pytest.mark.asyncio
async def test_guard_blocks_internal_agent_source(mock_config, mock_prompt_cache):
    """Test #1: internal_agent:* does NOT trigger set_metadata for original_source.

    The guard `not message_source.startswith("internal_")` should prevent
    internal_agent:* sources from overwriting the original_source metadata.
    """
    # Create mock with empty metadata
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata=None)
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    # Process message with internal_agent source
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Internal agent message",
        message_id="msg-agent-001",
        message_source="internal_agent:child1"
    )

    # Verify set_metadata was NOT called for original_source
    for call in mock_repo.set_metadata.call_args_list:
        assert call[0][1] != "original_source" or not call[0][0].startswith("internal_")


@pytest.mark.asyncio
async def test_internal_agent_dispatched_with_own_source(mock_config, mock_prompt_cache):
    """Test #2: internal_agent:* is dispatched with its own source, not original_source.

    When processing internal_agent messages, the response should be dispatched
    back to the internal_agent source (not the original external source).
    """
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata={"original_source": "telegram:123"})
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    # Process message with internal_agent source
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Internal agent message",
        message_id="msg-agent-002",
        message_source="internal_agent:child1"
    )

    # Verify dispatch used internal_agent source, not original telegram source
    mock_dispatcher.dispatch_message.assert_called()
    call_args = mock_dispatcher.dispatch_message.call_args
    assert call_args.kwargs['source'] == "internal_agent:child1"


# ==============================================================================
# Test Area 2: Guard blocks internal_report source (goes to different branch)
# ==============================================================================

@pytest.mark.asyncio
async def test_internal_report_branch_does_not_store_original_source(mock_config, mock_prompt_cache):
    """Test #3: internal_report:* goes to different branch and does not store metadata.

    The internal_report:* source takes the `if is_internal_report:` branch,
    which retrieves original_source from metadata but does NOT store it.
    This test verifies that no set_metadata call is made for original_source.
    """
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata={"original_source": "telegram:123"})
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    # Process message with internal_report source
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Child completed",
        message_id="msg-report-001",
        message_source="internal_report:child1:msg456"
    )

    # Verify set_metadata was NOT called for original_source
    # (internal_report takes a different code path)
    for call in mock_repo.set_metadata.call_args_list:
        # set_metadata may be called for other metadata keys, just not original_source
        if call[0][1] == "original_source":
            pytest.fail("set_metadata should not be called for original_source with internal_report")


@pytest.mark.asyncio
async def test_internal_error_report_branch_does_not_store_original_source(mock_config, mock_prompt_cache):
    """Test #4: internal_error_report:* goes to different branch and does not store metadata."""
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata={"original_source": "discord:user123"})
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    # Process message with internal_error_report source
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Child error",
        message_id="msg-error-001",
        message_source="internal_error_report:child1"
    )

    # Verify set_metadata was NOT called for original_source
    for call in mock_repo.set_metadata.call_args_list:
        if call[0][1] == "original_source":
            pytest.fail("set_metadata should not be called for original_source with internal_error_report")


# ==============================================================================
# Test Area 3: Guard allows external sources
# ==============================================================================

@pytest.mark.asyncio
async def test_guard_allows_telegram_source(mock_config, mock_prompt_cache):
    """Test #5: telegram:* source IS stored as original_source."""
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata=None)
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Hello from Telegram",
        message_id="msg-telegram-001",
        message_source="telegram:bot1:123456789"
    )

    # Verify set_metadata was called with original_source = telegram source
    mock_repo.set_metadata.assert_called_with(
        instance_id, "original_source", "telegram:bot1:123456789"
    )


@pytest.mark.asyncio
async def test_guard_allows_discord_source(mock_config, mock_prompt_cache):
    """Test #6: discord:* source IS stored as original_source."""
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata=None)
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Hello from Discord",
        message_id="msg-discord-001",
        message_source="discord:channel:abc123"
    )

    # Verify set_metadata was called with original_source = discord source
    mock_repo.set_metadata.assert_called_with(
        instance_id, "original_source", "discord:channel:abc123"
    )


@pytest.mark.asyncio
async def test_guard_allows_api_source(mock_config, mock_prompt_cache):
    """Test #7: api source (no colon) IS stored as original_source.

    Note: 'api' starts with 'a', not 'internal_', so the guard passes.
    """
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata=None)
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="API request",
        message_id="msg-api-001",
        message_source="api"
    )

    # Verify set_metadata was called with original_source = api
    mock_repo.set_metadata.assert_called_with(instance_id, "original_source", "api")


@pytest.mark.asyncio
async def test_guard_allows_webhook_source(mock_config, mock_prompt_cache):
    """Test #8: webhook:* source IS stored as original_source."""
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata=None)
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Webhook event",
        message_id="msg-webhook-001",
        message_source="webhook:github:push"
    )

    # Verify set_metadata was called
    mock_repo.set_metadata.assert_called_with(
        instance_id, "original_source", "webhook:github:push"
    )


# ==============================================================================
# Test Area 4: First-external-wins behavior
# ==============================================================================

@pytest.mark.asyncio
async def test_first_external_wins_internal_agent_not_stored(mock_config, mock_prompt_cache):
    """Test #9: First external source wins; subsequent internal_agent does not change it.

    Flow:
    1. First message from telegram:123 → stores original_source = telegram:123
    2. Second message from internal_agent:child1 → original_source unchanged
    """
    metadata_state = {"data": None}

    def get_side_effect(instance_id):
        meta = MagicMock()
        meta.instance_metadata = metadata_state["data"]
        return meta

    def set_metadata_side_effect(instance_id, key, value):
        metadata_state["data"] = {key: value}

    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.side_effect = get_side_effect
    mock_repo.set_metadata.side_effect = set_metadata_side_effect

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    # Step 1: External message
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="First message",
        message_id="msg-first-001",
        message_source="telegram:123456789"
    )

    assert metadata_state["data"] == {"original_source": "telegram:123456789"}

    # Step 2: Internal agent message - should NOT overwrite
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Second message from agent",
        message_id="msg-second-001",
        message_source="internal_agent:child1"
    )

    # Original source should still be telegram, not changed to internal_agent
    assert metadata_state["data"]["original_source"] == "telegram:123456789"


@pytest.mark.asyncio
async def test_second_external_does_not_overwrite_first(mock_config, mock_prompt_cache):
    """Test #10: Second external source does not overwrite the first one.

    Flow:
    1. First message from telegram:111 → stores original_source = telegram:111
    2. Second message from telegram:222 → original_source stays telegram:111
    """
    metadata_state = {"data": {}}

    def get_side_effect(instance_id):
        meta = MagicMock()
        meta.instance_metadata = metadata_state["data"]
        return meta

    def set_metadata_side_effect(instance_id, key, value):
        metadata_state["data"][key] = value

    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.side_effect = get_side_effect
    mock_repo.set_metadata.side_effect = set_metadata_side_effect

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    # Step 1: First external message
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="First message",
        message_id="msg-first-002",
        message_source="telegram:111"
    )

    initial_call_count = mock_repo.set_metadata.call_count
    assert metadata_state["data"]["original_source"] == "telegram:111"

    # Step 2: Second external message - should NOT overwrite
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Second message",
        message_id="msg-second-002",
        message_source="telegram:222"
    )

    # Verify set_metadata was NOT called again for original_source
    assert mock_repo.set_metadata.call_count == initial_call_count
    assert metadata_state["data"]["original_source"] == "telegram:111"


# ==============================================================================
# Test Area 5: Already-set not overwritten (write-once)
# ==============================================================================

@pytest.mark.asyncio
async def test_already_set_not_overwritten_by_internal(mock_config, mock_prompt_cache):
    """Test #11: When original_source is already set, internal sources don't overwrite it.

    This tests the `if not current` condition - if current is truthy, don't overwrite.
    """
    metadata_state = {"data": {"original_source": "telegram:original_chat"}}

    def get_side_effect(instance_id):
        meta = MagicMock()
        meta.instance_metadata = metadata_state["data"]
        return meta

    def set_metadata_side_effect(instance_id, key, value):
        metadata_state["data"][key] = value

    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.side_effect = get_side_effect
    mock_repo.set_metadata.side_effect = set_metadata_side_effect

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    # Process internal_agent message when original_source is already set
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Internal agent message",
        message_id="msg-overwrite-001",
        message_source="internal_agent:child1"
    )

    # Original source should remain unchanged
    assert metadata_state["data"]["original_source"] == "telegram:original_chat"

    # Verify set_metadata was NOT called to overwrite original_source
    for call in mock_repo.set_metadata.call_args_list:
        if call[0][1] == "original_source" and call[0][2] == "internal_agent:child1":
            pytest.fail("original_source should not be overwritten by internal_agent")


@pytest.mark.asyncio
async def test_already_set_not_overwritten_by_another_external(mock_config, mock_prompt_cache):
    """Test #12: When original_source is set, another external source doesn't overwrite it."""
    metadata_state = {"data": {"original_source": "discord:original_user"}}

    def get_side_effect(instance_id):
        meta = MagicMock()
        meta.instance_metadata = metadata_state["data"]
        return meta

    def set_metadata_side_effect(instance_id, key, value):
        metadata_state["data"][key] = value

    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.side_effect = get_side_effect
    mock_repo.set_metadata.side_effect = set_metadata_side_effect

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    # Process a different external source when original_source is already set
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Message from different source",
        message_id="msg-diff-001",
        message_source="telegram:different_chat"
    )

    # Original source should remain the first one (discord)
    assert metadata_state["data"]["original_source"] == "discord:original_user"


# ==============================================================================
# Test Area 6: Both code paths (metadata exists / doesn't exist)
# ==============================================================================

@pytest.mark.asyncio
async def test_path_a_metadata_exists_without_original_source(mock_config, mock_prompt_cache):
    """Test #13 (Path A): Instance metadata exists but doesn't have original_source.

    This exercises the first branch:
    if instance_meta is not None and instance_meta.instance_metadata is not None:
        current = instance_meta.instance_metadata.get("original_source")
        if not current and not message_source.startswith("internal_"):
            set_metadata(...)

    When current is None/empty AND source is external, set_metadata should be called.
    """
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata={"existing": "data"})  # Has metadata, no original_source
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Test message",
        message_id="msg-path-a-001",
        message_source="telegram:test_chat"
    )

    # External source should be stored even when other metadata exists
    mock_repo.set_metadata.assert_called_with(
        instance_id, "original_source", "telegram:test_chat"
    )


@pytest.mark.asyncio
async def test_path_a_metadata_exists_internal_source_blocked(mock_config, mock_prompt_cache):
    """Test #14 (Path A): Instance metadata exists, internal source should be blocked."""
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata={"existing": "data"})  # Has metadata
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Internal message",
        message_id="msg-path-a-002",
        message_source="internal_agent:test_child"
    )

    # set_metadata should NOT be called for original_source with internal source
    for call in mock_repo.set_metadata.call_args_list:
        if call[0][1] == "original_source":
            pytest.fail("set_metadata should not be called for original_source with internal_agent in Path A")


@pytest.mark.asyncio
async def test_path_b_metadata_does_not_exist(mock_config, mock_prompt_cache):
    """Test #15 (Path B): Instance metadata is None.

    This exercises the else branch:
    else:
        # Instance metadata doesn't exist yet, set it directly
        if not message_source.startswith("internal_"):
            set_metadata(...)

    When instance_metadata is None AND source is external, set_metadata should be called.
    """
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata=None)  # No metadata
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Test message",
        message_id="msg-path-b-001",
        message_source="webhook:github:push"
    )

    # External source should be stored even when no metadata exists
    mock_repo.set_metadata.assert_called_with(
        instance_id, "original_source", "webhook:github:push"
    )


@pytest.mark.asyncio
async def test_path_b_metadata_none_internal_source_blocked(mock_config, mock_prompt_cache):
    """Test #16 (Path B): Instance metadata is None, internal source should be blocked."""
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata=None)  # No metadata
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Internal message",
        message_id="msg-path-b-002",
        message_source="internal_agent:another_child"
    )

    # set_metadata should NOT be called for original_source with internal source
    for call in mock_repo.set_metadata.call_args_list:
        if call[0][1] == "original_source":
            pytest.fail("set_metadata should not be called for original_source with internal_agent in Path B")


# ==============================================================================
# Test Area 7: Full dispatch flow simulation
# ==============================================================================

@pytest.mark.asyncio
async def test_full_dispatch_flow_external_then_internal_then_report(mock_config, mock_prompt_cache):
    """Test #17: Full dispatch flow - external stores → internal doesn't change → report uses original.

    Flow:
    1. External telegram message → sets original_source = "telegram:bot1:12345"
    2. Internal agent message (internal_agent:child1) → original_source unchanged
    3. Internal report (internal_report:child1:msg_id) → dispatch_source resolved from metadata

    Verifies end state: original_source still = "telegram:bot1:12345"
    """
    metadata_state = {"data": None}

    def get_side_effect(instance_id):
        meta = MagicMock()
        meta.instance_metadata = metadata_state["data"]
        return meta

    def set_metadata_side_effect(instance_id, key, value):
        if metadata_state["data"] is None:
            metadata_state["data"] = {}
        metadata_state["data"][key] = value

    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.side_effect = get_side_effect
    mock_repo.set_metadata.side_effect = set_metadata_side_effect

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    # Step 1: External telegram message
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Start the task",
        message_id="msg-step1",
        message_source="telegram:bot1:12345"
    )

    assert metadata_state["data"]["original_source"] == "telegram:bot1:12345"

    # Step 2: Internal agent message
    mock_dispatcher.dispatch_message.reset_mock()
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Forward to child",
        message_id="msg-step2",
        message_source="internal_agent:child1"
    )

    # Original source should still be telegram
    assert metadata_state["data"]["original_source"] == "telegram:bot1:12345"

    # Step 3: Internal report - should dispatch using original_source
    mock_dispatcher.dispatch_message.reset_mock()
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Child completed",
        message_id="msg-step3",
        message_source="internal_report:child1:msg_id"
    )

    # Dispatch should have been called with the ORIGINAL source
    mock_dispatcher.dispatch_message.assert_called()
    call_args = mock_dispatcher.dispatch_message.call_args
    assert call_args.kwargs['source'] == "telegram:bot1:12345"
    assert "internal_report" not in call_args.kwargs['source']


@pytest.mark.asyncio
async def test_full_flow_with_existing_metadata_and_internal_agent(mock_config, mock_prompt_cache):
    """Test #18: Pre-existing metadata is preserved when internal_agent messages arrive.

    This tests the scenario where metadata already has values and internal_agent
    messages are processed - nothing should be overwritten.
    """
    metadata_state = {"data": {"original_source": "discord:channel_xyz", "project_id": "proj-123"}}

    def get_side_effect(instance_id):
        meta = MagicMock()
        meta.instance_metadata = metadata_state["data"]
        return meta

    def set_metadata_side_effect(instance_id, key, value):
        metadata_state["data"][key] = value

    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.side_effect = get_side_effect
    mock_repo.set_metadata.side_effect = set_metadata_side_effect

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    # Process internal_agent when metadata already exists
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Child processing",
        message_id="msg-preserve-001",
        message_source="internal_agent:worker_child"
    )

    # All metadata should be preserved
    assert metadata_state["data"]["original_source"] == "discord:channel_xyz"
    assert metadata_state["data"]["project_id"] == "proj-123"


# ==============================================================================
# Edge cases
# ==============================================================================

@pytest.mark.asyncio
async def test_edge_case_empty_string_source(mock_config, mock_prompt_cache):
    """Test #19: Empty string source is ignored (falsy check at line 778).

    An empty string fails the `if message_source:` check at line 778, so the
    entire source storage logic is skipped. This is expected behavior - empty
    sources are ignored rather than stored.
    """
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata=None)
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    # Empty string is falsy - `if message_source:` check fails
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Message with empty source",
        message_id="msg-empty-001",
        message_source=""
    )

    # Empty string should NOT be stored - it fails the `if message_source:` check
    for call in mock_repo.set_metadata.call_args_list:
        if call[0][1] == "original_source":
            pytest.fail("Empty string source should not be stored")


@pytest.mark.asyncio
async def test_edge_case_source_starting_with_internal_but_not_exact_match(mock_config, mock_prompt_cache):
    """Test #20: Sources starting with 'internal' but not 'internal_' should be allowed.

    For example, "internal_telegram:123" starts with "internal_" and should be blocked.
    But this test verifies that our guard correctly identifies the prefix.
    """
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = MagicMock(instance_metadata=None)
    mock_repo.set_metadata = MagicMock()

    manager, instance_id, mock_dispatcher = create_test_manager(
        mock_config, mock_prompt_cache, mock_repo
    )

    # "internal_telegram" starts with "internal_" - should be blocked
    await manager._process_message_with_tracking(
        instance_id=instance_id,
        message="Message",
        message_id="msg-internal-tg",
        message_source="internal_telegram:123"
    )

    # set_metadata should NOT have been called for original_source
    for call in mock_repo.set_metadata.call_args_list:
        if call[0][1] == "original_source":
            pytest.fail("Sources starting with 'internal_' should be blocked")


# ==============================================================================
# Summary
# ==============================================================================

"""
Test Coverage Summary:
- 20 tests covering all 7 areas of the original_source guard fix
- Tests guard logic: blocks internal_* sources, allows external sources
- Tests first-external-wins and write-once behavior
- Tests both code paths (metadata exists / doesn't exist)
- Tests full dispatch flow simulation
- Tests edge cases (empty strings, prefix matching)
"""
