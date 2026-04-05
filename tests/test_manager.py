"""Tests for daemon/manager.py"""

import pytest
import asyncio
import time
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from datetime import datetime

from daemon.manager import InstanceManager, parse_think_tags
from daemon.config import Config, LLMConfig, LimitsConfig, PersistenceConfig, DaemonConfig, AgentsConfig
from daemon.events import Event
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
            assert manager._engine is not None
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
            instance_id = manager.spawn_instance(agent_id="coder")
            
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
            instance_id = manager.spawn_instance(agent_id="coder", instance_id="550e8400-e29b-41d4-a716-446655440000")
            
            assert instance_id == "550e8400-e29b-41d4-a716-446655440000"

    def test_spawn_instance_max_instances_limit(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that max_instances limit is enforced."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            # Set max_instances to 2 for this test
            mock_config.limits.max_instances = 2
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            
            # Create 2 instances (reaching the limit)
            with patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_instance_tools', return_value=[]):
                
                manager.spawn_instance(agent_id="coder", instance_id="instance-1")
                manager.spawn_instance(agent_id="coder", instance_id="instance-2")
                
                # Third instance should raise ValueError
                with pytest.raises(ValueError, match="Max instances limit reached"):
                    manager.spawn_instance(agent_id="coder", instance_id="instance-3")

    def test_spawn_instance_max_children_limit(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that max_children_per_instance limit is enforced."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            # Set max_children_per_instance to 2 for this test
            mock_config.limits.max_children_per_instance = 2
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            
            # Parent instance with 2 children should reach the limit
            mock_parent_instance = MagicMock()
            mock_parent_instance.children = ["child1", "child2"]
            mock_instance_repository.get.return_value = mock_parent_instance
            
            with patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_instance_tools', return_value=[]):
                
                # Third child should raise ValueError
                with pytest.raises(ValueError, match="Max children per instance limit reached"):
                    manager.spawn_instance(agent_id="coder", parent_id="parent-instance")

    def test_spawn_instance_creates_graph(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test that graph is created and stored."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph) as mock_build, \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")
            
            # Verify graph was built and stored
            mock_build.assert_called_once()
            assert instance_id in manager.instances
            assert manager.instances[instance_id][0] == mock_graph
            # The second element is the resolved agent directory path (not a static string)
            assert manager.instances[instance_id][1].endswith("agents/coder")


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
            instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")
            
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

    def test_terminate_instance_success(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test terminating instance."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id = manager.spawn_instance(agent_id="coder", instance_id="550e8400-e29b-41d4-a716-446655440001")
            
            result = manager.terminate_instance(instance_id)
            
            assert result is True
            assert instance_id not in manager.instances
            mock_instance_repository.update_status.assert_called_once_with(instance_id, "terminated")

    def test_terminate_instance_not_found(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository):
        """Test terminating non-existent instance."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            
            result = manager.terminate_instance("non-existent-instance")
            
            assert result is False


class TestGetInstance:
    """Tests for get_instance method."""

    def test_get_instance_success(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_instance_repository):
        """Test getting instance graph."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_instance_tools', return_value=[]):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")
            
            graph = manager.get_instance(instance_id)
            
            assert graph == mock_graph

    def test_get_instance_not_found(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository):
        """Test error when instance doesn't exist."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = InstanceManager(mock_config)
            
            with pytest.raises(KeyError, match="Instance not found"):
                manager.get_instance("non-existent-instance")


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
            instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")
            
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
            instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")
            
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
            instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")
            
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
            instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")
            
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
            instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")
            
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
            instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")
            
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
        """Test that title is generated and broadcast correctly."""
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.graph.ThinkingChatOpenAI', return_value=mock_llm):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.broadcaster = AsyncMock()
            
            # Mock the instance repository to return a instance with no title
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance
            
            # Call the method
            await manager._generate_and_broadcast_title("test-instance", "Hello, how are you?")
            
            # Verify broadcast was called with correct event
            manager.broadcaster.broadcast.assert_called_once()
            call_args = manager.broadcaster.broadcast.call_args
            event = call_args[0][0]
            assert isinstance(event, Event)
            assert event.type == "title_updated"
            assert event.instance_id == "test-instance"
            assert event.data == {"title": "Test Instance Title"}
            
            # Verify update_title was called
            mock_instance_repository.update_title.assert_called_once_with("test-instance", "Test Instance Title")

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_already_exists(self, mock_config, mock_instance_repository):
        """Test that broadcast is NOT called when title already exists."""
        with patch('daemon.manager.PromptCache', return_value=Mock()):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.broadcaster = AsyncMock()
            
            # Mock the instance repository to return a instance with existing title
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {"title": "Existing Title"}
            mock_instance_repository.get.return_value = mock_instance
            
            # Call the method - should return early since title exists
            await manager._generate_and_broadcast_title("test-instance", "Hello!")
            
            # Broadcast should NOT be called because title already exists
            manager.broadcaster.broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_llm_failure(self, mock_config, mock_instance_repository):
        """Test that handles LLM failure gracefully without broadcasting."""
        mock_llm_instance = Mock()
        mock_llm_instance.invoke.side_effect = Exception("LLM Error")
        
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.graph.ThinkingChatOpenAI', return_value=mock_llm_instance), \
             patch('daemon.manager.logger') as mock_logger:
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.broadcaster = AsyncMock()
            
            # Mock the instance repository
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance
            
            # Call the method - should not raise (exception is caught)
            await manager._generate_and_broadcast_title("test-instance", "Hello!")
            
            # Broadcast should NOT be called on failure
            manager.broadcaster.broadcast.assert_not_called()
            
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
             patch('daemon.graph.ThinkingChatOpenAI', return_value=mock_llm):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.broadcaster = AsyncMock()
            
            # Mock the instance repository
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance
            
            # Call the method
            await manager._generate_and_broadcast_title("test-instance", "Hello!")
            
            # Verify broadcast was called with truncated title
            manager.broadcaster.broadcast.assert_called_once()
            call_args = manager.broadcaster.broadcast.call_args
            event = call_args[0][0]
            assert len(event.data["title"]) <= 100
            assert event.data["title"].endswith("...")

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_empty_message(self, mock_config, mock_instance_repository):
        """Test that empty message returns early without broadcasting."""
        with patch('daemon.manager.PromptCache', return_value=Mock()):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.broadcaster = AsyncMock()
            
            # Call with empty message
            await manager._generate_and_broadcast_title("test-instance", "")
            
            # Broadcast should NOT be called
            manager.broadcaster.broadcast.assert_not_called()
            
            # Call with whitespace only
            await manager._generate_and_broadcast_title("test-instance", "   ")
            
            # Broadcast should NOT be called
            manager.broadcaster.broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_list_content(self, mock_config, mock_instance_repository):
        """Test that list content from LLM is handled correctly."""
        mock_llm = Mock()
        mock_response = Mock()
        # Return content as a list (some LLM providers return this format)
        mock_response.content = [{"type": "text", "text": "List Response Title"}]
        mock_llm.invoke.return_value = mock_response
        
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.graph.ThinkingChatOpenAI', return_value=mock_llm):
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.broadcaster = AsyncMock()
            
            # Mock the instance repository
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance
            
            # Call the method
            await manager._generate_and_broadcast_title("test-instance", "Hello!")
            
            # Verify title was extracted from list and broadcast
            manager.broadcaster.broadcast.assert_called_once()
            call_args = manager.broadcaster.broadcast.call_args
            event = call_args[0][0]
            assert "List Response Title" in event.data["title"]

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_error_caught(self, mock_config, mock_instance_repository):
        """Test that errors are caught and logged without crashing."""
        mock_llm_instance = Mock()
        mock_llm_instance.invoke.side_effect = Exception("LLM Error")
        
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.graph.ThinkingChatOpenAI', return_value=mock_llm_instance), \
             patch('daemon.manager.logger') as mock_logger:
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.broadcaster = AsyncMock()
            
            # Mock the instance repository
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance
            
            # Call the method - should not raise (exception is caught)
            await manager._generate_and_broadcast_title("test-instance", "Hello!")
            
            # Verify broadcast was NOT called due to error
            manager.broadcaster.broadcast.assert_not_called()
            
            # Should log warning
            mock_logger.warning.assert_called()
            assert "Failed to generate title" in str(mock_logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_instance_title_broadcasts_correct_event(self, mock_config, mock_instance_repository):
        """Test that the broadcast event has exactly the expected structure."""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = "Exact Title"
        mock_llm.invoke.return_value = mock_response
        
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.graph.ThinkingChatOpenAI', return_value=mock_llm):
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.broadcaster = AsyncMock()
            
            # Mock the instance repository
            mock_instance = MagicMock()
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance
            
            # Call the method
            await manager._generate_and_broadcast_title("instance-123", "User message content")
            
            # Capture the Event object passed to broadcast
            manager.broadcaster.broadcast.assert_called_once()
            event = manager.broadcaster.broadcast.call_args[0][0]
            
            # Assert event structure
            assert event.type == "title_updated"
            assert event.instance_id == "instance-123"
            assert event.message_id == ""
            assert event.data == {"title": "Exact Title"}


class TestTitleGenerationFireAndForget:
    """Tests for asyncio.create_task behavior in _process_queue."""

    @pytest.mark.asyncio
    async def test_title_generation_does_not_block_completed_event(self, mock_config, mock_instance_repository):
        """Test that completed event is broadcast BEFORE title generation finishes (fire-and-forget)."""
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.manager.get_instance_messages', new_callable=AsyncMock, return_value=[]) as mock_get_messages:
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.broadcaster = AsyncMock()
            manager.circuit_breaker = Mock()
            manager.circuit_breaker.can_execute = Mock(return_value=True)
            manager.circuit_breaker.record_success = Mock()
            manager._request_registry = Mock()
            manager._request_registry.register = Mock(return_value=Mock(token=None))
            # Set _checkpointer directly since checkpointer is a property without setter
            manager._checkpointer = Mock()
            # Mock watchdog to avoid database errors
            manager.watchdog = Mock()
            
            # Track event order with timestamps
            event_timestamps = {}
            
            # Mock _generate_and_broadcast_title to be slow
            async def slow_title_gen(*args, **kwargs):
                await asyncio.sleep(0.5)
                event_timestamps["title_started"] = time.monotonic()
            
            manager._generate_and_broadcast_title = slow_title_gen
            
            # Create a mock broadcast that tracks events with timing
            async def tracking_broadcast(event):
                if event.type == "completed":
                    event_timestamps["completed_broadcast"] = time.monotonic()
                # Call the original AsyncMock's behavior
                await AsyncMock()(event)
            
            manager.broadcaster.broadcast = AsyncMock(side_effect=tracking_broadcast)
            
            # Create a mock message result
            mock_result = Mock()
            mock_result.content = "Response"
            mock_result.thinking = None
            mock_result.thinking_extracted = None
            mock_result.tool_calls = []
            manager._process_message_with_tracking = AsyncMock(return_value=mock_result)
            
            # Mock dequeue to return one message then None
            queued_msg = QueuedMessage(
                message_id="msg-123",
                instance_id="test-instance",
                content="Hello!",
                source="test",
                retry_count=0
            )
            manager._queue_repository = Mock()
            manager._queue_repository.dequeue_by_instance = Mock(side_effect=[queued_msg, None])
            manager._queue_repository.get_status = Mock(return_value="processing")
            manager._queue_repository.complete = Mock()
            manager._queue_repository.is_empty = Mock(return_value=True)
            
            # Mock instance metadata
            mock_instance = Mock()
            mock_instance.parent_id = None
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance
            
            # Record start time
            start_time = time.monotonic()
            
            # Run _process_queue - should return quickly due to fire-and-forget
            await manager._process_queue("test-instance")
            
            # Verify completed was broadcast
            assert "completed_broadcast" in event_timestamps, "Completed event should be broadcasted"
            
            # Verify title generation has NOT started yet (because it's fire-and-forget)
            # Since we're not waiting for the background task, title should not have started
            # within this short time window
            assert "title_started" not in event_timestamps, "Title generation should not block - it runs in background"
            
            # Verify _process_queue returned quickly (< 0.3s while title takes 0.5s)
            elapsed = time.monotonic() - start_time
            assert elapsed < 0.3, f"_process_queue took {elapsed:.2f}s, should return quickly"

    @pytest.mark.asyncio
    async def test_title_generation_not_triggered_for_non_first_message(self, mock_config, mock_instance_repository):
        """Test that title generation is NOT triggered when is_first_message is False."""
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.manager.get_instance_messages', new_callable=AsyncMock, return_value=["existing-message"]) as mock_get_messages:
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.broadcaster = AsyncMock()
            manager.circuit_breaker = Mock()
            manager.circuit_breaker.can_execute = Mock(return_value=True)
            manager.circuit_breaker.record_success = Mock()
            manager._request_registry = Mock()
            manager._request_registry.register = Mock(return_value=Mock(token=None))
            # Set _checkpointer directly since checkpointer is a property without setter
            manager._checkpointer = Mock()
            # Mock watchdog to avoid database errors
            manager.watchdog = Mock()
            
            # Track if title generation was called
            title_gen_called = False
            
            async def track_title_gen(*args, **kwargs):
                nonlocal title_gen_called
                title_gen_called = True
            
            manager._generate_and_broadcast_title = track_title_gen
            
            # Mock _process_message_with_tracking
            mock_result = Mock()
            mock_result.content = "Response"
            mock_result.thinking = None
            mock_result.thinking_extracted = None
            mock_result.tool_calls = []
            manager._process_message_with_tracking = AsyncMock(return_value=mock_result)
            
            # Mock dequeue to return one message then None
            queued_msg = QueuedMessage(
                message_id="msg-123",
                instance_id="test-instance",
                content="Hello!",
                source="test",
                retry_count=0
            )
            manager._queue_repository = Mock()
            manager._queue_repository.dequeue_by_instance = Mock(side_effect=[queued_msg, None])
            manager._queue_repository.get_status = Mock(return_value="processing")
            manager._queue_repository.complete = Mock()
            manager._queue_repository.is_empty = Mock(return_value=True)
            
            # Mock instance metadata
            mock_instance = Mock()
            mock_instance.parent_id = None
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance
            
            # Run _process_queue
            await manager._process_queue("test-instance")
            
            # Verify title generation was NOT called
            assert not title_gen_called, "Title generation should NOT be called for non-first messages"
            
            # Verify NO title_updated event was broadcast
            title_updated_calls = [c for c in manager.broadcaster.broadcast.call_args_list 
                                  if c[0][0].type == "title_updated"]
            assert len(title_updated_calls) == 0, "No title_updated event should be broadcasted"

    @pytest.mark.asyncio
    async def test_fire_and_forget_isolation(self, mock_config, mock_instance_repository):
        """Test that _process_queue returns quickly even when title generation is slow."""
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.manager.get_instance_messages', new_callable=AsyncMock, return_value=[]) as mock_get_messages:
            
            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.broadcaster = AsyncMock()
            manager.circuit_breaker = Mock()
            manager.circuit_breaker.can_execute = Mock(return_value=True)
            manager.circuit_breaker.record_success = Mock()
            manager._request_registry = Mock()
            manager._request_registry.register = Mock(return_value=Mock(token=None))
            # Set _checkpointer directly since checkpointer is a property without setter
            manager._checkpointer = Mock()
            # Mock watchdog to avoid database errors
            manager.watchdog = Mock()
            
            # Mock _generate_and_broadcast_title to be slow (2 seconds)
            async def slow_title_gen(*args, **kwargs):
                await asyncio.sleep(2.0)
            
            manager._generate_and_broadcast_title = slow_title_gen
            
            # Mock _process_message_with_tracking
            mock_result = Mock()
            mock_result.content = "Response"
            mock_result.thinking = None
            mock_result.thinking_extracted = None
            mock_result.tool_calls = []
            manager._process_message_with_tracking = AsyncMock(return_value=mock_result)
            
            # Mock dequeue to return one message then None
            queued_msg = QueuedMessage(
                message_id="msg-123",
                instance_id="test-instance",
                content="Hello!",
                source="test",
                retry_count=0
            )
            manager._queue_repository = Mock()
            manager._queue_repository.dequeue_by_instance = Mock(side_effect=[queued_msg, None])
            manager._queue_repository.get_status = Mock(return_value="processing")
            manager._queue_repository.complete = Mock()
            manager._queue_repository.is_empty = Mock(return_value=True)
            
            # Mock instance metadata
            mock_instance = Mock()
            mock_instance.parent_id = None
            mock_instance.instance_metadata = {}
            mock_instance_repository.get.return_value = mock_instance
            
            # Measure how long _process_queue takes
            start_time = time.monotonic()
            await manager._process_queue("test-instance")
            elapsed = time.monotonic() - start_time
            
            # _process_queue should return quickly (< 0.5s) even with slow title generation
            assert elapsed < 0.5, f"_process_queue took {elapsed:.2f}s, should return quickly with fire-and-forget title generation"
