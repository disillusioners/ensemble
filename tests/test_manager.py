"""Tests for daemon/manager.py"""

import pytest
import asyncio
import time
from contextlib import contextmanager
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from datetime import datetime

from daemon.manager import InstanceManager, parse_think_tags
from daemon.config import Config, LLMConfig, LimitsConfig, PersistenceConfig, DaemonConfig, AgentsConfig
from daemon.queue import QueuedMessage


class TestParseThinkTags:
    """Tests for parse_think_tags utility function."""

    def test_basic_extraction(self):
        """Test basic think tag extraction."""
        content = "\x3cthink\x3ethis is my thinking\x3c/think\x3eThe actual response"
        cleaned, thinking = parse_think_tags(content)
        assert thinking == "this is my thinking"
        assert cleaned == "The actual response"

    def test_multiple_tags(self):
        """Test multiple think tags are combined."""
        content = "\x3cthink\x3eFirst thought\x3c/think\x3eSome text\x3cthink\x3eSecond thought\x3c/think\x3eMore text"
        cleaned, thinking = parse_think_tags(content)
        assert thinking == "First thought\nSecond thought"
        assert cleaned == "Some textMore text"

    def test_tags_with_attributes(self):
        """Test think tags with attributes."""
        content = '\x3cthink budget="123" duration="456"\x3eMy reasoning\x3c/think\x3eResponse'
        cleaned, thinking = parse_think_tags(content)
        assert thinking == "My reasoning"
        assert cleaned == "Response"

    def test_no_tags(self):
        """Test content without think tags."""
        content = "Just a regular response"
        cleaned, thinking = parse_think_tags(content)
        assert thinking is None
        assert cleaned == "Just a regular response"

    def test_case_insensitive(self):
        """Test case insensitive parsing."""
        content = "\x3cTHINK\x3eUpper case\x3c/THINK\x3eResponse"
        cleaned, thinking = parse_think_tags(content)
        assert thinking == "Upper case"
        assert cleaned == "Response"

    def test_multiline_thinking(self):
        """Test multiline thinking content."""
        content = "\x3cthink\x3eLine 1\nLine 2\nLine 3\x3c/think\x3eResponse"
        cleaned, thinking = parse_think_tags(content)
        assert thinking == "Line 1\nLine 2\nLine 3"
        assert cleaned == "Response"


@pytest.fixture
def mock_config():
    """Create a mock config."""
    return Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4",
            temperature=0.7
        ),
        limits=LimitsConfig(
            max_children_per_instance=3,
            instance_timeout_minutes=60,
            message_rate_limit=60
        ),
        persistence=PersistenceConfig(
            db_path=":memory:",
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
            max_instance_history=300
        ),
        daemon=DaemonConfig(host="0.0.0.0", port=8079),
        agents=AgentsConfig(directory="./agents")
    )


@pytest.fixture
def mock_checkpointer():
    """Create a mock checkpointer."""
    return Mock()


@pytest.fixture
def mock_graph():
    """Create a mock graph."""
    graph = Mock()
    # Store the message to be returned - tests can modify this
    mock_message = Mock()
    mock_message.content = "Test response"
    mock_message.type = 'ai'
    mock_message.tool_calls = []  # Empty tool calls to avoid iteration error
    graph.invoke.return_value = {"messages": [mock_message]}
    # Make ainvoke return the same messages as invoke
    async def mock_ainvoke(*args, **kwargs):
        return {"messages": [mock_message]}
    graph.ainvoke = mock_ainvoke
    # Allow tests to access the mock message for modification
    graph._mock_message = mock_message
    return graph


@pytest.fixture
def mock_instance_repository():
    """Create a mock instance repository."""
    mock_repo = MagicMock()
    # Default return values for common methods
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = None
    mock_repo.list.return_value = ([], 0)
    return mock_repo


@pytest.fixture
def mock_prompt_cache():
    """Create a mock prompt cache."""
    return Mock()


class TestInstanceManagerInit:
    """Tests for InstanceManager initialization."""

    def test_instance_manager_init(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository):
        """Test manager initialization."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = InstanceManager(mock_config)
            # Mock the instance repository to avoid database connection
            manager._instance_repository = mock_instance_repository
            
            assert manager.config == mock_config
            assert manager.engine is not None
            assert manager.instances == {}


class TestSpawnInstance:
    """Tests for spawn_instance method."""

    def test_spawn_instance_generates_id(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that instance_id is auto-generated."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id, _ = manager.spawn_instance(agent_id="developer")
            
            # Should have generated a UUID
            assert instance_id is not None
            assert len(instance_id) == 36  # UUID format

    def test_spawn_instance_uses_provided_id(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that provided instance_id is used."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="550e8400-e29b-41d4-a716-446655440000")
            
            assert instance_id == "550e8400-e29b-41d4-a716-446655440000"

    def test_spawn_instance_max_children_limit(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that max_children_per_instance limit is enforced via DB query."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            # Set max_children_per_instance to 2 for this test
            mock_config.limits.max_children_per_instance = 2
            
            # Mock count_children to return 2 (at limit)
            mock_instance_repository.count_children.return_value = 2
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            
            with patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_instance_tools', return_value=[]):
                
                # Third child should raise ValueError (limit is 2, already at limit)
                with pytest.raises(ValueError, match="Max children limit reached"):
                    manager.spawn_instance(agent_id="developer", parent_id="parent-instance")

    def test_spawn_instance_creates_graph(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that graph is created and stored."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph) as mock_build, \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            # Verify graph was built and stored
            mock_build.assert_called_once()
            assert instance_id in manager.instances
            assert manager.instances[instance_id][0] == mock_graph
            # The second element is the resolved agent directory path (not a static string)
            assert manager.instances[instance_id][1].endswith("agents/developer")


class TestSendMessage:
    """Tests for send_message method."""

    @pytest.mark.asyncio
    async def test_send_message_success(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test sending message to instance."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            # Send a message
            response = await manager.send_message(instance_id, "Hello!")
            
            # Verify the response content
            assert response.content == "Test response"

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_send_message_instance_not_found(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository):
        """Test error when instance doesn't exist."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            
            with pytest.raises(KeyError, match="Instance not found"):
                await manager.send_message("non-existent-instance", "Hello!")


class TestTerminateInstance:
    """Tests for terminate_instance method."""

    @pytest.mark.asyncio
    async def test_terminate_instance_success(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test terminating instance.

        H10 refactor: terminate_instance now writes through a real
        ``WriteGuardSession`` against ``manager.engine`` /
        ``manager.write_guard`` (single atomic cascade transaction)
        instead of calling ``instance_repository.update()``. The mock-based
        test therefore stubs the engine and write_guard with MagicMock and
        patches the sync DB helpers (``_spawn_instance_db_sync``,
        ``_terminate_instance_db_sync``) to return fake results, so the
        service-level orchestration can be exercised without touching a
        real DB. The session never actually executes against the MagicMock
        engine, so the pre-DB side-effect assertions remain valid.
        """
        from daemon.services.instance_lifecycle import _SpawnResult, _TerminateResult

        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            # H10 fix: stub the engine + write_guard so the WriteGuardSession
            # gate accepts the write. Both are read-only properties on
            # InstanceManager — set the underlying private attributes instead.
            manager._engine = MagicMock()
            manager._write_guard = MagicMock()

            # Bypass the sync DB helpers entirely so the test exercises the
            # service-level orchestration (in-memory cleanup, outbox wiring,
            # child cascade, return value) without needing a real SQLite DB.
            # Pre-fix, the same test asserted on ``instance_repository.update``
            # — but the H10 refactor moved the write into a raw
            # ``WriteGuardSession`` transaction that no longer routes through
            # the repository mock layer.
            now_iso = "2026-01-01T00:00:00+00:00"
            manager._lifecycle_service._spawn_instance_db_sync = MagicMock(
                return_value=_SpawnResult(
                    created=True,
                    parent_id=None,
                    agent_id="developer",
                    project_id=None,
                    created_at=now_iso,
                    inherited_source=False,
                )
            )
            manager._lifecycle_service._terminate_instance_db_sync = MagicMock(
                return_value=_TerminateResult(
                    skip=False,
                    parent_id=None,
                    agent_id="developer",
                    message_jobs_cancelled=0,
                    all_jobs_cancelled=0,
                    message_queue_removed=0,
                    tasks_removed=0,
                )
            )

            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="550e8400-e29b-41d4-a716-446655440001")

            result = await manager.terminate_instance(instance_id)

            assert result is True
            assert instance_id not in manager.instances
            # H10 fix: status + waiting_for are now written atomically by
            # ``_terminate_instance_db_sync`` inside a single WriteGuardSession
            # transaction against ``manager.engine`` — they are no longer
            # routed through ``instance_repository.update``. The in-memory
            # cleanup (``manager.instances`` removal + return True) is the
            # observable behaviour we still assert on.

    @pytest.mark.asyncio
    async def test_terminate_instance_not_found(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository):
        """Test terminating non-existent instance."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            
            result = await manager.terminate_instance("non-existent-instance")
            
            assert result is False


class TestGetInstance:
    """Tests for get_instance method."""

    @pytest.mark.asyncio
    async def test_get_instance_success(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test getting instance graph."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            graph = await manager.get_instance(instance_id)
            
            assert graph == mock_graph

    @pytest.mark.asyncio  
    async def test_get_instance_not_found(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository):
        """Test error when instance doesn't exist."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = InstanceManager(mock_config)
            
            with pytest.raises(KeyError, match="Instance not found"):
                await manager.get_instance("non-existent-instance")


class TestListInstances:
    """Tests for list_instances method."""

    def test_list_instances(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository):
        """Test listing instances."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            
            # Mock the list method to return instances
            mock_instance1 = MagicMock()
            mock_instance1.instance_id = "instance-1"
            mock_instance1.agent_dir = "/path/1"
            mock_instance1.status = "running"
            mock_instance1.instance_metadata = {}
            mock_instance1.to_dict.return_value = {"instance_id": "instance-1", "agent_dir": "/path/1", "status": "running"}
            
            mock_instance2 = MagicMock()
            mock_instance2.instance_id = "instance-2"
            mock_instance2.agent_dir = "/path/2"
            mock_instance2.status = "idle"
            mock_instance2.instance_metadata = {}
            mock_instance2.to_dict.return_value = {"instance_id": "instance-2", "agent_dir": "/path/2", "status": "idle"}
            
            mock_instance_repository.list.return_value = ([mock_instance1, mock_instance2], 2)
            
            instances, total = manager.list_instances()
            
            assert len(instances) == 2
            assert instances[0]["instance_id"] == "instance-1"
            assert instances[1]["instance_id"] == "instance-2"
            assert total == 2


class TestThinkTagParsing:
    """Tests for think tag parsing in send_message."""

    @pytest.mark.asyncio
    async def test_basic_think_tag_extraction(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that basic think tags are extracted from response."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = "\x3cthink\x3ethis is my thinking\x3c/think\x3eThe actual response"
        mock_message.type = 'ai'
        mock_message.tool_calls = []
        
        # Update the mock_graph to return our message
        async def mock_ainvoke(*args, **kwargs):
            return {"messages": [mock_message]}
        mock_graph.ainvoke = mock_ainvoke
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            result = await manager.send_message(instance_id, "Hello!")
            
            # Thinking should be extracted
            assert result.thinking_extracted == "this is my thinking"
            # Content should have tags removed
            assert result.content == "The actual response"
            # No metadata thinking in this case
            assert result.thinking is None

    @pytest.mark.asyncio
    async def test_multiple_think_tags_extracted(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that multiple <think/> tags are combined."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = '<think>First thought</think>Some text<think>Second thought</think>More text'
        mock_message.type = 'ai'
        mock_message.tool_calls = []
        
        async def mock_ainvoke(*args, **kwargs):
            return {"messages": [mock_message]}
        mock_graph.ainvoke = mock_ainvoke
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            result = await manager.send_message(instance_id, "Hello!")
            
            # Both thoughts should be combined with newline
            assert result.thinking_extracted == "First thought\nSecond thought"
            # Content should have all tags removed
            assert result.content == "Some textMore text"

    @pytest.mark.asyncio
    async def test_think_tag_with_attributes(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that <think/> tags with attributes are parsed."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = '<think budget="123" duration="456">My reasoning here</think>Another thought'

        mock_message.type = 'ai'
        mock_message.tool_calls = []
        
        async def mock_ainvoke(*args, **kwargs):
            return {"messages": [mock_message]}
        mock_graph.ainvoke = mock_ainvoke
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            result = await manager.send_message(instance_id, "Hello!")
            
            assert result.thinking_extracted == "My reasoning here"
            assert result.content == "Another thought"

    @pytest.mark.asyncio
    async def test_thinking_metadata_priority_over_extracted(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that metadata thinking takes priority over extracted thinking."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = '<think>Extracted thinking\n</think>\n'
        mock_message.type = 'ai'
        mock_message.tool_calls = []
        # Simulate metadata thinking (from provider)
        mock_message.additional_kwargs = {"reasoning_content": "Metadata thinking"}
        
        async def mock_ainvoke(*args, **kwargs):
            return {"messages": [mock_message]}
        mock_graph.ainvoke = mock_ainvoke
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            result = await manager.send_message(instance_id, "Hello!")
            
            # Both should be populated separately
            assert result.thinking == "Metadata thinking"
            assert result.thinking_extracted == "Extracted thinking"
            assert result.content == ""

    @pytest.mark.asyncio
    async def test_no_think_tag_returns_none_extracted(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that response without think tags has None for thinking_extracted."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = "Just a regular response"
        mock_message.type = 'ai'
        mock_message.tool_calls = []
        
        async def mock_ainvoke(*args, **kwargs):
            return {"messages": [mock_message]}
        mock_graph.ainvoke = mock_ainvoke
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            result = await manager.send_message(instance_id, "Hello!")
            
            assert result.thinking_extracted is None
            assert result.thinking is None
            assert result.content == "Just a regular response"

    @pytest.mark.asyncio
    async def test_case_insensitive_think_tags(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that <THINK> and <Think> tags are also parsed."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = "<THINK>Upper case thinking</THINK>Response"
        mock_message.type = 'ai'
        mock_message.tool_calls = []
        
        async def mock_ainvoke(*args, **kwargs):
            return {"messages": [mock_message]}
        mock_graph.ainvoke = mock_ainvoke
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            result = await manager.send_message(instance_id, "Hello!")
            
            assert result.thinking_extracted == "Upper case thinking"
            assert result.content == "Response"
class TestGenerateAndBroadcastTitle:
    """Tests for _generate_and_broadcast_title method."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM that returns a title."""
        mock = Mock()
        mock_response = Mock()
        mock_response.content = "Test Instance Title"
        mock.invoke.return_value = mock_response
        return mock

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_success(self, mock_config, mock_llm, mock_instance_repository):
        """Test that title is generated and update_title is called correctly."""
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.services.title_generation.ThinkingChatOpenAI', return_value=mock_llm):

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            # Mock the instance repository to return a instance with no title
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance

            # Call the method
            await manager._generate_and_broadcast_title("test-instance", "Hello, how are you?")

            # Verify update_title was called with correct title
            mock_instance_repository.update_title.assert_called_once_with("test-instance", "Test Instance Title")

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_already_exists(self, mock_config, mock_instance_repository):
        """Test that update_title is NOT called when title already exists."""
        with patch('daemon.manager.PromptCache', return_value=Mock()):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            
            # Mock the instance repository to return a instance with existing title
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {"title": "Existing Title"}
            mock_instance_repository.get.return_value = mock_instance
            
            # Call the method - should return early since title exists
            await manager._generate_and_broadcast_title("test-instance", "Hello!")
            
            # update_title should NOT be called because title already exists
            mock_instance_repository.update_title.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_llm_failure(self, mock_config, mock_instance_repository):
        """Test that handles LLM failure gracefully without calling update_title."""
        mock_llm_instance = Mock()
        mock_llm_instance.invoke.side_effect = Exception("LLM Error")
        
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.services.title_generation.ThinkingChatOpenAI', return_value=mock_llm_instance), \
             patch('daemon.manager.logger') as mock_logger:

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            # Mock the instance repository
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance

            # Call the method - should not raise (exception is caught)
            await manager._generate_and_broadcast_title("test-instance", "Hello!")
            
            # update_title should NOT be called on failure
            mock_instance_repository.update_title.assert_not_called()
            
            # Should log warning
            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_truncates_long_titles(self, mock_config, mock_instance_repository):
        """Test that long titles are truncated to 100 chars."""
        long_title = "A" * 200  # 200 character title
        
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = long_title
        mock_llm.invoke.return_value = mock_response
        
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.services.title_generation.ThinkingChatOpenAI', return_value=mock_llm):

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            # Mock the instance repository
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance

            # Call the method
            await manager._generate_and_broadcast_title("test-instance", "Hello!")

            # Verify update_title was called with truncated title
            mock_instance_repository.update_title.assert_called_once()
            call_args = mock_instance_repository.update_title.call_args
            title = call_args[0][1]
            assert len(title) <= 100
            assert title.endswith("...")

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_empty_message(self, mock_config, mock_instance_repository):
        """Test that empty message returns early without calling update_title."""
        with patch('daemon.manager.PromptCache', return_value=Mock()):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            
            # Call with empty message
            await manager._generate_and_broadcast_title("test-instance", "")
            
            # update_title should NOT be called
            mock_instance_repository.update_title.assert_not_called()
            
            # Call with whitespace only
            await manager._generate_and_broadcast_title("test-instance", "   ")
            
            # update_title should NOT be called
            mock_instance_repository.update_title.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_list_content(self, mock_config, mock_instance_repository):
        """Test that list content from LLM is handled correctly."""
        mock_llm = Mock()
        mock_response = Mock()
        # Return content as a list (some LLM providers return this format)
        mock_response.content = [{"type": "text", "text": "List Response Title"}]
        mock_llm.invoke.return_value = mock_response
        
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.services.title_generation.ThinkingChatOpenAI', return_value=mock_llm):

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository

            # Mock the instance repository
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance

            # Call the method
            await manager._generate_and_broadcast_title("test-instance", "Hello!")

            # Verify title was extracted from list and update_title was called
            mock_instance_repository.update_title.assert_called_once()
            call_args = mock_instance_repository.update_title.call_args
            title = call_args[0][1]
            assert "List Response Title" in title

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_error_caught(self, mock_config, mock_instance_repository):
        """Test that errors are caught and logged without crashing."""
        mock_llm_instance = Mock()
        mock_llm_instance.invoke.side_effect = Exception("LLM Error")
        
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.services.title_generation.ThinkingChatOpenAI', return_value=mock_llm_instance), \
             patch('daemon.manager.logger') as mock_logger:
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            
            # Mock the instance repository
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance
            
            # Call the method - should not raise (exception is caught)
            await manager._generate_and_broadcast_title("test-instance", "Hello!")
            
            # Verify update_title was NOT called due to error
            mock_instance_repository.update_title.assert_not_called()
            
            # Should log warning
            mock_logger.warning.assert_called()
            assert "Failed to generate title" in str(mock_logger.warning.call_args)


class TestProgressiveMessageDelivery:
    """Tests for progressive message delivery via source_dispatcher."""

    @pytest.fixture
    def mock_source_dispatcher(self):
        """Create a mock source dispatcher."""
        dispatcher = AsyncMock()
        dispatcher.dispatch_message = AsyncMock(return_value=None)
        return dispatcher

    @pytest.fixture
    def streaming_graph_with_agent_message(self):
        """Create a mock graph that yields streaming events with agent messages."""
        graph = Mock()
        
        # Create an AI message with text content
        ai_message = Mock()
        ai_message.content = "Streaming response"
        ai_message.type = 'ai'
        ai_message.tool_calls = []
        ai_message.id = "msg-1"
        
        # Create a streaming event with agent node data
        stream_event = ("updates", {"agent": {"messages": [ai_message]}})
        
        # Generator that yields the stream event
        async def mock_astream(*args, **kwargs):
            yield stream_event
            yield ("updates", {"agent": {"messages": []}})  # Empty update to end
        graph.astream = mock_astream
        graph.invoke = Mock(return_value={"messages": [ai_message]})
        
        return graph

    @pytest.fixture
    def streaming_graph_with_tool_calls_only(self):
        """Create a mock graph that yields events with tool_calls but empty content."""
        graph = Mock()
        
        # Create a tool message with empty content (no text to send)
        tool_message = Mock()
        tool_message.content = ""  # Empty content - should be skipped
        tool_message.type = 'ai'  # But it's type 'ai' (from tool call)
        tool_message.tool_calls = [{"name": "some_tool", "id": "call_123"}]
        tool_message.id = "msg-1"
        
        stream_event = ("updates", {"agent": {"messages": [tool_message]}})
        
        async def mock_astream(*args, **kwargs):
            yield stream_event
            yield ("updates", {"agent": {"messages": []}})
        graph.astream = mock_astream
        graph.invoke = Mock(return_value={"messages": [tool_message]})
        
        return graph

    @pytest.mark.asyncio
    async def test_manager_calls_dispatch_message_for_agent_text_messages(
        self, mock_config, mock_checkpointer, mock_prompt_cache, 
        streaming_graph_with_agent_message, mock_instance_repository, mock_source_dispatcher
    ):
        """Manager should call dispatch_message for each agent text message during streaming."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=streaming_graph_with_agent_message), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.source_dispatcher = mock_source_dispatcher
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)
            
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            # Call _process_message_with_tracking directly with message_source
            response = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source="telegram:12345"
            )
            
            # Verify dispatch_message was called for the agent text message
            mock_source_dispatcher.dispatch_message.assert_called()
            
            # Check the call arguments
            calls = mock_source_dispatcher.dispatch_message.call_args_list
            # Should have called at least once with the message content
            assert len(calls) >= 1

    @pytest.mark.asyncio
    async def test_manager_skips_dispatch_for_tool_calls_only_messages(
        self, mock_config, mock_checkpointer, mock_prompt_cache,
        streaming_graph_with_tool_calls_only, mock_instance_repository, mock_source_dispatcher
    ):
        """Manager should NOT call dispatch_message for tool_calls-only messages with empty content."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=streaming_graph_with_tool_calls_only), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.source_dispatcher = mock_source_dispatcher
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)
            
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            # Call _process_message_with_tracking directly with message_source
            response = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source="telegram:12345"
            )
            
            # Verify dispatch_message was NOT called for empty content
            # The message has type='ai' but content is empty/whitespace
            mock_source_dispatcher.dispatch_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_manager_handles_dispatch_errors_gracefully(
        self, mock_config, mock_checkpointer, mock_prompt_cache,
        streaming_graph_with_agent_message, mock_instance_repository
    ):
        """Manager should handle dispatch errors gracefully without breaking execution."""
        # Create a mock dispatcher that raises an exception
        mock_dispatcher = AsyncMock()
        mock_dispatcher.dispatch_message = AsyncMock(side_effect=Exception("Dispatch failed"))
        
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=streaming_graph_with_agent_message), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]), \
             patch('daemon.services.instance_messaging.logger') as mock_logger:
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.source_dispatcher = mock_dispatcher
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)

            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")

            # This should NOT raise - errors should be caught and logged
            response = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source="telegram:12345"
            )
            
            # Verify the error was logged
            mock_logger.warning.assert_called()
            warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("Progressive dispatch failed" in c for c in warning_calls)
            
            # Response should still be returned (execution continued)
            assert response is not None

    @pytest.mark.asyncio
    async def test_manager_does_not_dispatch_without_source_dispatcher(
        self, mock_config, mock_checkpointer, mock_prompt_cache,
        streaming_graph_with_agent_message, mock_instance_repository
    ):
        """Manager should not try to dispatch when source_dispatcher is None."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=streaming_graph_with_agent_message), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)
            # source_dispatcher defaults to None in this test

            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")

            # Should not raise even without dispatcher
            response = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source="telegram:12345"
            )
            
            # Response should still be returned
            assert response is not None

    @pytest.mark.asyncio
    async def test_manager_does_not_dispatch_without_message_source(
        self, mock_config, mock_checkpointer, mock_prompt_cache,
        streaming_graph_with_agent_message, mock_instance_repository, mock_source_dispatcher
    ):
        """Manager should not dispatch when message_source is None/empty."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=streaming_graph_with_agent_message), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.source_dispatcher = mock_source_dispatcher
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)
            
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            # Call _process_message_with_tracking WITHOUT a message_source
            response = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source=None  # No source specified
            )
            
            # Verify dispatch_message was NOT called (no source specified)
            mock_source_dispatcher.dispatch_message.assert_not_called()


class TestToolResultStreaming:
    """Tests for real-time tool_result SSE emission from the streaming loop."""

    @pytest.fixture
    def mock_live_hub(self):
        """Create a mock live hub whose stream_* methods are AsyncMocks."""
        hub = MagicMock()
        hub.stream_message = AsyncMock()
        hub.stream_tool_result = AsyncMock()
        hub.stream_status_change = AsyncMock()
        hub.stream_error = AsyncMock()
        return hub

    @pytest.fixture
    def streaming_graph_with_tool_message(self):
        """Graph yields: agent (AIMessage w/ tool_calls) then tools (ToolMessage)."""
        from langchain_core.messages import AIMessage, ToolMessage

        ai_msg = AIMessage(
            content="",
            id="ai-1",
            tool_calls=[{"id": "call_abc", "name": "echo", "args": {"x": 1}}],
        )
        tool_msg = ToolMessage(
            content="the tool output",
            tool_call_id="call_abc",
            id="tm-1",
        )

        graph = Mock()

        async def mock_astream(*args, **kwargs):
            yield ("updates", {"agent": {"messages": [ai_msg]}})
            yield ("updates", {"tools": {"messages": [tool_msg]}})
            yield ("updates", {"agent": {"messages": []}})

        graph.astream = mock_astream
        graph.invoke = Mock(return_value={"messages": [ai_msg, tool_msg]})
        return graph

    @pytest.fixture
    def streaming_graph_with_two_tool_messages(self):
        """Graph yields: tools (two ToolMessages) — two tool_result events expected."""
        from langchain_core.messages import ToolMessage

        tm1 = ToolMessage(content="first", tool_call_id="call_a", id="tm-1")
        tm2 = ToolMessage(content="second", tool_call_id="call_b", id="tm-2")

        graph = Mock()

        async def mock_astream(*args, **kwargs):
            yield ("updates", {"tools": {"messages": [tm1, tm2]}})
            yield ("updates", {"agent": {"messages": []}})

        graph.astream = mock_astream
        graph.invoke = Mock(return_value={"messages": [tm1, tm2]})
        return graph

    @pytest.mark.asyncio
    async def test_streaming_loop_emits_tool_result_for_tool_message(
        self, mock_config, mock_checkpointer, mock_prompt_cache,
        streaming_graph_with_tool_message, mock_instance_repository, mock_live_hub
    ):
        """A ToolMessage in the `tools` node update must emit a tool_result event."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=streaming_graph_with_tool_message), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager._live_hub = mock_live_hub
            manager.source_dispatcher = None
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)

            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")

            await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source=None,
            )

        assert mock_live_hub.stream_tool_result.await_count >= 1
        call = mock_live_hub.stream_tool_result.call_args
        assert call.kwargs["instance_id"] == instance_id
        assert call.kwargs["tool_call_id"] == "call_abc"
        assert call.kwargs["content"] == "the tool output"
        assert call.kwargs["message_id"] == "tm-1"

    @pytest.mark.asyncio
    async def test_streaming_loop_dedupes_tool_result_across_updates(
        self, mock_config, mock_checkpointer, mock_prompt_cache,
        streaming_graph_with_tool_message, mock_instance_repository, mock_live_hub
    ):
        """The same ToolMessage appearing in multiple updates iterations must emit only once."""
        # Yield the tools node update twice with the same ToolMessage id to
        # simulate LangGraph re-delivering cumulative state.
        from langchain_core.messages import ToolMessage

        tool_msg = ToolMessage(content="the tool output", tool_call_id="call_abc", id="tm-dup")

        graph = Mock()

        async def mock_astream(*args, **kwargs):
            yield ("updates", {"tools": {"messages": [tool_msg]}})
            yield ("updates", {"tools": {"messages": [tool_msg]}})

        graph.astream = mock_astream
        graph.invoke = Mock(return_value={"messages": [tool_msg]})

        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager._live_hub = mock_live_hub
            manager.source_dispatcher = None
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)

            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")

            await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source=None,
            )

        assert mock_live_hub.stream_tool_result.await_count == 1

    @pytest.mark.asyncio
    async def test_streaming_loop_emits_tool_result_for_each_tool(
        self, mock_config, mock_checkpointer, mock_prompt_cache,
        streaming_graph_with_two_tool_messages, mock_instance_repository, mock_live_hub
    ):
        """Each ToolMessage in a single tools node update must emit its own tool_result event."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=streaming_graph_with_two_tool_messages), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager._live_hub = mock_live_hub
            manager.source_dispatcher = None
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)

            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")

            await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source=None,
            )

        assert mock_live_hub.stream_tool_result.await_count == 2
        call_kwarg_pairs = [c.kwargs for c in mock_live_hub.stream_tool_result.call_args_list]
        assert {c["tool_call_id"] for c in call_kwarg_pairs} == {"call_a", "call_b"}
        assert {c["content"] for c in call_kwarg_pairs} == {"first", "second"}


class TestListContentHandling:
    """Tests for Fix W2: List content handling (content as list of blocks)."""

    @pytest.fixture
    def mock_source_dispatcher(self):
        """Create a mock source dispatcher."""
        dispatcher = AsyncMock()
        dispatcher.dispatch_message = AsyncMock(return_value=None)
        return dispatcher

    @pytest.fixture
    def streaming_graph_with_list_content(self):
        """Create a mock graph that yields events with list content blocks."""
        graph = Mock()
        
        # Create an AI message with list content (like [{"type": "text", "text": "..."}])
        ai_message = Mock()
        ai_message.content = [{"type": "text", "text": "List content response"}]
        ai_message.type = 'ai'
        ai_message.tool_calls = []
        ai_message.id = "msg-list-1"
        
        stream_event = ("updates", {"agent": {"messages": [ai_message]}})
        
        async def mock_astream(*args, **kwargs):
            yield stream_event
            yield ("updates", {"agent": {"messages": []}})
        graph.astream = mock_astream
        graph.invoke = Mock(return_value={"messages": [ai_message]})
        
        return graph

    @pytest.fixture
    def streaming_graph_with_list_content_no_text(self):
        """Create a mock graph that yields events with list content but no text blocks."""
        graph = Mock()
        
        # Create an AI message with list content but no text blocks
        ai_message = Mock()
        ai_message.content = [{"type": "image", "url": "http://example.com/image.png"}]
        ai_message.type = 'ai'
        ai_message.tool_calls = []
        ai_message.id = "msg-list-no-text-1"
        
        stream_event = ("updates", {"agent": {"messages": [ai_message]}})
        
        async def mock_astream(*args, **kwargs):
            yield stream_event
            yield ("updates", {"agent": {"messages": []}})
        graph.astream = mock_astream
        graph.invoke = Mock(return_value={"messages": [ai_message]})
        
        return graph

    @pytest.fixture
    def streaming_graph_with_mixed_list_content(self):
        """Create a mock graph with list content containing multiple text blocks."""
        graph = Mock()
        
        # Create an AI message with multiple text blocks in list
        ai_message = Mock()
        ai_message.content = [
            {"type": "text", "text": "Part one "},
            {"type": "text", "text": "Part two "},
            {"type": "text", "text": "Part three"}
        ]
        ai_message.type = 'ai'
        ai_message.tool_calls = []
        ai_message.id = "msg-mixed-1"
        
        stream_event = ("updates", {"agent": {"messages": [ai_message]}})
        
        async def mock_astream(*args, **kwargs):
            yield stream_event
            yield ("updates", {"agent": {"messages": []}})
        graph.astream = mock_astream
        graph.invoke = Mock(return_value={"messages": [ai_message]})
        
        return graph

    @pytest.mark.asyncio
    async def test_manager_extracts_text_from_list_content(
        self, mock_config, mock_checkpointer, mock_prompt_cache,
        streaming_graph_with_list_content, mock_instance_repository, mock_source_dispatcher
    ):
        """Test that manager correctly extracts text from list content and dispatches it.
        
        When message.content is a list like [{"type": "text", "text": "hello"}],
        the manager should extract the text and dispatch it.
        """
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=streaming_graph_with_list_content), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]), \
             patch('daemon.manager.parse_think_tags', return_value=("test content", None)):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.source_dispatcher = mock_source_dispatcher
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)
            
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            response = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source="telegram:12345"
            )
            
            # Verify dispatch_message was called with extracted text
            mock_source_dispatcher.dispatch_message.assert_called()
            call_args = mock_source_dispatcher.dispatch_message.call_args
            assert call_args.kwargs['content'] == "List content response"

    @pytest.mark.asyncio
    async def test_manager_skips_dispatch_when_list_has_no_text(
        self, mock_config, mock_checkpointer, mock_prompt_cache,
        streaming_graph_with_list_content_no_text, mock_instance_repository, mock_source_dispatcher
    ):
        """Test that manager does NOT dispatch when list content has no text blocks.
        
        When message.content is a list with no text blocks (e.g., only images),
        the manager should skip dispatching since there's no text to send.
        """
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=streaming_graph_with_list_content_no_text), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]), \
             patch('daemon.manager.parse_think_tags', return_value=("test content", None)):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.source_dispatcher = mock_source_dispatcher
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)
            
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            response = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source="telegram:12345"
            )
            
            # Verify dispatch_message was NOT called (no text content)
            mock_source_dispatcher.dispatch_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_manager_joins_multiple_text_blocks(
        self, mock_config, mock_checkpointer, mock_prompt_cache,
        streaming_graph_with_mixed_list_content, mock_instance_repository, mock_source_dispatcher
    ):
        """Test that manager joins multiple text blocks with spaces."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=streaming_graph_with_mixed_list_content), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]), \
             patch('daemon.manager.parse_think_tags', return_value=("test content", None)):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.source_dispatcher = mock_source_dispatcher
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)
            
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            response = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source="telegram:12345"
            )
            
            # Verify dispatch_message was called with joined text
            mock_source_dispatcher.dispatch_message.assert_called()
            call_args = mock_source_dispatcher.dispatch_message.call_args
            # Content ends with spaces so join produces double spaces
            assert call_args.kwargs['content'] == "Part one  Part two  Part three"


class TestStreamingDeduplicationByMessageId:
    """Tests for Fix W3: Streaming deduplication by message ID."""

    @pytest.fixture
    def mock_source_dispatcher(self):
        """Create a mock source dispatcher."""
        dispatcher = AsyncMock()
        dispatcher.dispatch_message = AsyncMock(return_value=None)
        return dispatcher

    @pytest.fixture
    def streaming_graph_with_duplicate_ids(self):
        """Create a mock graph that yields events with duplicate message IDs."""
        graph = Mock()
        
        # First message with ID
        msg1 = Mock()
        msg1.content = "First message"
        msg1.type = 'ai'
        msg1.tool_calls = []
        msg1.id = "msg-duplicate"
        
        # Second message with SAME ID (duplicate)
        msg2 = Mock()
        msg2.content = "Duplicate message"
        msg2.type = 'ai'
        msg2.tool_calls = []
        msg2.id = "msg-duplicate"  # Same ID!
        
        stream_event1 = ("updates", {"agent": {"messages": [msg1]}})
        stream_event2 = ("updates", {"agent": {"messages": [msg2]}})
        
        async def mock_astream(*args, **kwargs):
            yield stream_event1
            yield stream_event2
            yield ("updates", {"agent": {"messages": []}})
        graph.astream = mock_astream
        graph.invoke = Mock(return_value={"messages": [msg1, msg2]})
        
        return graph

    @pytest.fixture
    def streaming_graph_with_unique_ids(self):
        """Create a mock graph that yields events with unique message IDs."""
        graph = Mock()
        
        msg1 = Mock()
        msg1.content = "First unique message"
        msg1.type = 'ai'
        msg1.tool_calls = []
        msg1.id = "msg-unique-1"
        
        msg2 = Mock()
        msg2.content = "Second unique message"
        msg2.type = 'ai'
        msg2.tool_calls = []
        msg2.id = "msg-unique-2"
        
        stream_event1 = ("updates", {"agent": {"messages": [msg1]}})
        stream_event2 = ("updates", {"agent": {"messages": [msg2]}})
        
        async def mock_astream(*args, **kwargs):
            yield stream_event1
            yield stream_event2
            yield ("updates", {"agent": {"messages": []}})
        graph.astream = mock_astream
        graph.invoke = Mock(return_value={"messages": [msg1, msg2]})
        
        return graph

    @pytest.mark.asyncio
    async def test_manager_skips_duplicate_message_ids(
        self, mock_config, mock_checkpointer, mock_prompt_cache,
        streaming_graph_with_duplicate_ids, mock_instance_repository, mock_source_dispatcher
    ):
        """Test that manager skips dispatching messages with duplicate IDs.
        
        When the same message ID appears multiple times in the streaming session,
        the manager should only dispatch the first one and skip subsequent duplicates.
        """
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=streaming_graph_with_duplicate_ids), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.source_dispatcher = mock_source_dispatcher
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)
            
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            response = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source="telegram:12345"
            )
            
            # Verify dispatch_message was called exactly ONCE for the duplicate ID
            mock_source_dispatcher.dispatch_message.assert_called_once()
            
            # The content should be from the FIRST message only
            call_args = mock_source_dispatcher.dispatch_message.call_args
            assert call_args.kwargs['content'] == "First message"

    @pytest.mark.asyncio
    async def test_manager_dispatches_unique_message_ids(
        self, mock_config, mock_checkpointer, mock_prompt_cache,
        streaming_graph_with_unique_ids, mock_instance_repository, mock_source_dispatcher
    ):
        """Test that manager dispatches messages with different IDs normally.
        
        When messages have unique IDs, the manager should dispatch each one.
        """
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=streaming_graph_with_unique_ids), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.source_dispatcher = mock_source_dispatcher
            # H10-era mocks: stub project_repository.match_by_keywords so the
            # project-injection path doesn't hit the in-memory SQLite engine.
            manager._project_repository = MagicMock()
            manager._project_repository.match_by_keywords = MagicMock(return_value=None)
            
            instance_id, _ = manager.spawn_instance(agent_id="developer", instance_id="test-instance")
            
            response = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello!",
                message_id="test-msg-001",
                message_source="telegram:12345"
            )
            
            # Verify dispatch_message was called TWICE (once for each unique ID)
            assert mock_source_dispatcher.dispatch_message.call_count == 2
            
            # Check both messages were dispatched
            call_args_list = mock_source_dispatcher.dispatch_message.call_args_list
            contents = [call.kwargs['content'] for call in call_args_list]
            assert "First unique message" in contents
            assert "Second unique message" in contents


class TestTitleGenerationTrigger:
    """Tests for title generation trigger behavior in enqueue_message methods.

    Verifies that _maybe_trigger_title_generation is called correctly based on:
    - Message type (AGENT or HUMAN)
    - Instance state transition (IDLE -> RUNNING)
    """

    @pytest.fixture
    def mock_manager(self, mock_config, mock_instance_repository):
        """Create a mock manager with required attributes."""
        manager = MagicMock()
        manager.config = mock_config
        manager._instance_repository = mock_instance_repository
        manager._queue_repository = MagicMock()
        manager._project_repository = MagicMock()
        manager._engine = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._worker_pool = None
        manager._graph_tasks = {}
        manager.prompt_cache = Mock()
        manager._llm_semaphore = asyncio.Semaphore(1)
        manager._compactor = None
        manager._checkpointer = None
        manager._generate_and_broadcast_title = AsyncMock()
        # Mock job queue service
        manager._job_queue_service = MagicMock()
        manager._job_queue_service.enqueue = AsyncMock(return_value=MagicMock(job_id="test-job"))
        return manager

    @pytest.fixture
    def mock_cancellation_service(self):
        """Create a mock cancellation service."""
        service = MagicMock()
        service.is_shutting_down = False
        return service

    @pytest.mark.asyncio
    async def test_agent_message_triggers_title_on_idle_to_running(
        self, mock_manager, mock_cancellation_service, mock_instance_repository
    ):
        """Test that AGENT message triggers title generation when instance transitions IDLE->RUNNING.

        This is the bug fix case: previously only HUMAN messages triggered title generation,
        but AGENT messages (e.g., parent-to-child messages) should also trigger it.
        """
        from daemon.services.instance_messaging import InstanceMessagingService
        from daemon.repositories.instance.models import InstanceStatus

        # Patch Session to return our mock with IDLE instance
        with patch('daemon.services.instance_messaging.Session') as mock_session_cls, \
             patch('daemon.services.instance_messaging.MainLoopBridge') as mock_bridge, \
             patch('daemon.services.instance_messaging.Instance') as mock_instance_model, \
             patch('daemon.services.instance_messaging.MessageQueue') as mock_message_queue, \
             patch('daemon.services.instance_messaging.Task') as mock_task, \
             patch('daemon.services.instance_messaging.Event') as mock_event:

            # Create a fresh mock instance for each call
            def get_instance_side_effect(instance_id, instance_cls):
                mock_instance = MagicMock()
                mock_instance.status = InstanceStatus.IDLE.value  # Start as IDLE
                mock_instance.agent_id = "test-agent"
                mock_instance.instance_metadata = {}
                mock_instance.version = 1
                mock_instance.paused_at = None
                # The code will transition this to RUNNING
                return mock_instance

            mock_session = MagicMock()
            mock_session.get.side_effect = get_instance_side_effect

            @contextmanager
            def mock_session_ctx():
                yield mock_session

            mock_session_cls.return_value = mock_session_ctx()

            # Configure MainLoopBridge to capture the call
            mock_bridge.run_async_no_wait = Mock()

            # Create the messaging service
            service = InstanceMessagingService(mock_manager, mock_cancellation_service)

            # Call enqueue_message with AGENT source (triggers MessageType.AGENT)
            await service.enqueue_message(
                instance_id="test-instance-id",
                message="Agent message content",
                source="internal_agent:parent-123",
            )

            # Verify _maybe_trigger_title_generation was triggered (via MainLoopBridge)
            # This proves AGENT messages now trigger title generation (the bug fix)
            mock_bridge.run_async_no_wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_human_message_still_triggers_title_on_idle_to_running(
        self, mock_manager, mock_cancellation_service, mock_instance_repository
    ):
        """Test that HUMAN message still triggers title generation (regression test)."""
        from daemon.services.instance_messaging import InstanceMessagingService
        from daemon.repositories.instance.models import InstanceStatus

        with patch('daemon.services.instance_messaging.Session') as mock_session_cls, \
             patch('daemon.services.instance_messaging.MainLoopBridge') as mock_bridge, \
             patch('daemon.services.instance_messaging.Instance') as mock_instance_model, \
             patch('daemon.services.instance_messaging.MessageQueue') as mock_message_queue, \
             patch('daemon.services.instance_messaging.Task') as mock_task, \
             patch('daemon.services.instance_messaging.Event') as mock_event:

            def get_instance_side_effect(instance_id, instance_cls):
                mock_instance = MagicMock()
                mock_instance.status = InstanceStatus.IDLE.value
                mock_instance.agent_id = "test-agent"
                mock_instance.instance_metadata = {}
                mock_instance.version = 1
                mock_instance.paused_at = None
                return mock_instance

            mock_session = MagicMock()
            mock_session.get.side_effect = get_instance_side_effect

            @contextmanager
            def mock_session_ctx():
                yield mock_session

            mock_session_cls.return_value = mock_session_ctx()

            mock_bridge.run_async_no_wait = Mock()

            service = InstanceMessagingService(mock_manager, mock_cancellation_service)

            # Call enqueue_message with default source (triggers MessageType.HUMAN)
            await service.enqueue_message(
                instance_id="test-instance-id",
                message="Human message content",
                source="user",
            )

            # Verify title generation was triggered
            mock_bridge.run_async_no_wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_title_generation_skipped_when_already_running(
        self, mock_manager, mock_cancellation_service, mock_instance_repository
    ):
        """Test that title generation is NOT triggered when instance is already RUNNING."""
        from daemon.services.instance_messaging import InstanceMessagingService
        from daemon.repositories.instance.models import InstanceStatus

        with patch('daemon.services.instance_messaging.Session') as mock_session_cls, \
             patch('daemon.services.instance_messaging.MainLoopBridge') as mock_bridge, \
             patch('daemon.services.instance_messaging.Instance') as mock_instance_model, \
             patch('daemon.services.instance_messaging.MessageQueue') as mock_message_queue, \
             patch('daemon.services.instance_messaging.Task') as mock_task, \
             patch('daemon.services.instance_messaging.Event') as mock_event:

            def get_instance_side_effect(instance_id, instance_cls):
                mock_instance = MagicMock()
                mock_instance.status = InstanceStatus.RUNNING.value  # Already RUNNING
                mock_instance.agent_id = "test-agent"
                mock_instance.instance_metadata = {}
                mock_instance.version = 1
                mock_instance.paused_at = None
                return mock_instance

            mock_session = MagicMock()
            mock_session.get.side_effect = get_instance_side_effect

            @contextmanager
            def mock_session_ctx():
                yield mock_session

            mock_session_cls.return_value = mock_session_ctx()

            mock_bridge.run_async_no_wait = Mock()

            service = InstanceMessagingService(mock_manager, mock_cancellation_service)

            # Call enqueue_message with AGENT source
            await service.enqueue_message(
                instance_id="test-instance-id",
                message="Any message content",
                source="internal_agent:parent-123",
            )

            # Verify title generation was NOT triggered
            mock_bridge.run_async_no_wait.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_message_triggers_title_via_jq_on_idle_to_running(
        self, mock_manager, mock_cancellation_service, mock_instance_repository
    ):
        """Test that AGENT message triggers title generation via enqueue_message.

        This tests the JobQueue path which has the same title generation logic.
        """
        from daemon.services.instance_messaging import InstanceMessagingService
        from daemon.repositories.instance.models import InstanceStatus

        # Configure mock instance repository to return proper metadata for via_jq path
        mock_instance_meta = MagicMock()
        mock_instance_meta.agent_id = "test-agent"
        mock_instance_meta.project_id = "test-project"
        mock_instance_repository.get.return_value = mock_instance_meta

        with patch('daemon.services.instance_messaging.Session') as mock_session_cls, \
             patch('daemon.services.instance_messaging.MainLoopBridge') as mock_bridge, \
             patch('daemon.services.instance_messaging.Instance') as mock_instance_model, \
             patch('daemon.services.instance_messaging.MessageQueue') as mock_message_queue, \
             patch('daemon.services.instance_messaging.Event') as mock_event:

            def get_instance_side_effect(instance_id, instance_cls):
                mock_instance = MagicMock()
                mock_instance.status = InstanceStatus.IDLE.value
                mock_instance.agent_id = "test-agent"
                mock_instance.instance_metadata = {}
                mock_instance.version = 1
                mock_instance.paused_at = None
                return mock_instance

            mock_session = MagicMock()
            mock_session.get.side_effect = get_instance_side_effect

            @contextmanager
            def mock_session_ctx():
                yield mock_session

            mock_session_cls.return_value = mock_session_ctx()

            mock_bridge.run_async_no_wait = Mock()

            service = InstanceMessagingService(mock_manager, mock_cancellation_service)

            # Call enqueue_message with AGENT source
            await service.enqueue_message(
                instance_id="test-instance-id",
                message="Agent message",
                source="internal_agent:parent-123",
                priority=0,
            )

            # Verify title generation was triggered
            # This proves AGENT messages now trigger title generation (the bug fix)
            mock_bridge.run_async_no_wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_title_generation_skipped_via_jq_when_already_running(
        self, mock_manager, mock_cancellation_service, mock_instance_repository
    ):
        """Test that title generation is NOT triggered via enqueue_message when already RUNNING."""
        from daemon.services.instance_messaging import InstanceMessagingService
        from daemon.repositories.instance.models import InstanceStatus

        # Configure mock instance repository to return proper metadata for via_jq path
        mock_instance_meta = MagicMock()
        mock_instance_meta.agent_id = "test-agent"
        mock_instance_meta.project_id = "test-project"
        mock_instance_repository.get.return_value = mock_instance_meta

        with patch('daemon.services.instance_messaging.Session') as mock_session_cls, \
             patch('daemon.services.instance_messaging.MainLoopBridge') as mock_bridge, \
             patch('daemon.services.instance_messaging.Instance') as mock_instance_model, \
             patch('daemon.services.instance_messaging.MessageQueue') as mock_message_queue, \
             patch('daemon.services.instance_messaging.Event') as mock_event:

            def get_instance_side_effect(instance_id, instance_cls):
                mock_instance = MagicMock()
                mock_instance.status = InstanceStatus.RUNNING.value  # Already RUNNING
                mock_instance.agent_id = "test-agent"
                mock_instance.instance_metadata = {}
                mock_instance.version = 1
                mock_instance.paused_at = None
                return mock_instance

            mock_session = MagicMock()
            mock_session.get.side_effect = get_instance_side_effect

            @contextmanager
            def mock_session_ctx():
                yield mock_session

            mock_session_cls.return_value = mock_session_ctx()

            mock_bridge.run_async_no_wait = Mock()

            service = InstanceMessagingService(mock_manager, mock_cancellation_service)

            # Call enqueue_message with default source (triggers HUMAN)
            await service.enqueue_message(
                instance_id="test-instance-id",
                message="Human message",
                source="user",
                priority=0,
            )

            # Verify title generation was NOT triggered
            mock_bridge.run_async_no_wait.assert_not_called()
