"""Tests for daemon/manager.py"""

import pytest
import asyncio
import time
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from datetime import datetime

from daemon.manager import SessionManager, parse_think_tags
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
            max_sessions=5,
            max_children_per_session=3,
            session_timeout_minutes=60,
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
def mock_session_repository():
    """Create a mock session repository."""
    mock_repo = MagicMock()
    # Default return values for common methods
    mock_repo.create.return_value = MagicMock(session_id="test-session")
    mock_repo.get.return_value = None
    mock_repo.list.return_value = ([], 0)
    return mock_repo


@pytest.fixture
def mock_prompt_cache():
    """Create a mock prompt cache."""
    return Mock()


class TestSessionManagerInit:
    """Tests for SessionManager initialization."""

    def test_session_manager_init(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_session_repository):
        """Test manager initialization."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = SessionManager(mock_config)
            # Mock the session repository to avoid database connection
            manager._session_repository = mock_session_repository
            
            assert manager.config == mock_config
            assert manager._engine is not None
            assert manager.sessions == {}


class TestSpawnSession:
    """Tests for spawn_session method."""

    def test_spawn_session_generates_id(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
        """Test that session_id is auto-generated."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            session_id = manager.spawn_session(agent_id="coder")
            
            # Should have generated a UUID
            assert session_id is not None
            assert len(session_id) == 36  # UUID format

    def test_spawn_session_uses_provided_id(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
        """Test that provided session_id is used."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            session_id = manager.spawn_session(agent_id="coder", session_id="550e8400-e29b-41d4-a716-446655440000")
            
            assert session_id == "550e8400-e29b-41d4-a716-446655440000"

    def test_spawn_session_max_sessions_limit(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
        """Test that max_sessions limit is enforced."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            # Set max_sessions to 2 for this test
            mock_config.limits.max_sessions = 2
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            
            # Create 2 sessions (reaching the limit)
            with patch('daemon.manager.build_session_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_session_tools', return_value=[]):
                
                manager.spawn_session(agent_id="coder", session_id="session-1")
                manager.spawn_session(agent_id="coder", session_id="session-2")
                
                # Third session should raise ValueError
                with pytest.raises(ValueError, match="Max sessions limit reached"):
                    manager.spawn_session(agent_id="coder", session_id="session-3")

    def test_spawn_session_max_children_limit(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
        """Test that max_children_per_session limit is enforced."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            # Set max_children_per_session to 2 for this test
            mock_config.limits.max_children_per_session = 2
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            
            # Parent session with 2 children should reach the limit
            mock_parent_session = MagicMock()
            mock_parent_session.children = ["child1", "child2"]
            mock_session_repository.get.return_value = mock_parent_session
            
            with patch('daemon.manager.build_session_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_session_tools', return_value=[]):
                
                # Third child should raise ValueError
                with pytest.raises(ValueError, match="Max children per session limit reached"):
                    manager.spawn_session(agent_id="coder", parent_id="parent-session")

    def test_spawn_session_creates_graph(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
        """Test that graph is created and stored."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph) as mock_build, \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            session_id = manager.spawn_session(agent_id="coder", session_id="test-session")
            
            # Verify graph was built and stored
            mock_build.assert_called_once()
            assert session_id in manager.sessions
            assert manager.sessions[session_id][0] == mock_graph
            # The second element is the resolved agent directory path (not a static string)
            assert manager.sessions[session_id][1].endswith("agents/coder")


class TestSendMessage:
    """Tests for send_message method."""

    @pytest.mark.asyncio
    async def test_send_message_success(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
        """Test sending message to session."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            session_id = manager.spawn_session(agent_id="coder", session_id="test-session")
            
            # Send a message
            response = await manager.send_message(session_id, "Hello!")
            
            # Verify the response content
            assert response.content == "Test response"

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_send_message_session_not_found(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_session_repository):
        """Test error when session doesn't exist."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            
            with pytest.raises(KeyError, match="Session not found"):
                await manager.send_message("non-existent-session", "Hello!")


class TestTerminateSession:
    """Tests for terminate_session method."""

    def test_terminate_session_success(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
        """Test terminating session."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            session_id = manager.spawn_session(agent_id="coder", session_id="550e8400-e29b-41d4-a716-446655440001")
            
            result = manager.terminate_session(session_id)
            
            assert result is True
            assert session_id not in manager.sessions
            mock_session_repository.update_status.assert_called_once_with(session_id, "terminated")

    def test_terminate_session_not_found(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_session_repository):
        """Test terminating non-existent session."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            
            result = manager.terminate_session("non-existent-session")
            
            assert result is False


class TestGetSession:
    """Tests for get_session method."""

    def test_get_session_success(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
        """Test getting session graph."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            session_id = manager.spawn_session(agent_id="coder", session_id="test-session")
            
            graph = manager.get_session(session_id)
            
            assert graph == mock_graph

    def test_get_session_not_found(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_session_repository):
        """Test error when session doesn't exist."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = SessionManager(mock_config)
            
            with pytest.raises(KeyError, match="Session not found"):
                manager.get_session("non-existent-session")


class TestListSessions:
    """Tests for list_sessions method."""

    def test_list_sessions(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_session_repository):
        """Test listing sessions."""
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            
            # Mock the list method to return sessions
            mock_session1 = MagicMock()
            mock_session1.session_id = "session-1"
            mock_session1.agent_dir = "/path/1"
            mock_session1.status = "running"
            mock_session1.session_metadata = {}
            mock_session1.to_dict.return_value = {"session_id": "session-1", "agent_dir": "/path/1", "status": "running"}
            
            mock_session2 = MagicMock()
            mock_session2.session_id = "session-2"
            mock_session2.agent_dir = "/path/2"
            mock_session2.status = "idle"
            mock_session2.session_metadata = {}
            mock_session2.to_dict.return_value = {"session_id": "session-2", "agent_dir": "/path/2", "status": "idle"}
            
            mock_session_repository.list.return_value = ([mock_session1, mock_session2], 2)
            
            sessions, total = manager.list_sessions()
            
            assert len(sessions) == 2
            assert sessions[0]["session_id"] == "session-1"
            assert sessions[1]["session_id"] == "session-2"
            assert total == 2


class TestThinkTagParsing:
    """Tests for <think/> tag parsing in message responses."""

    @pytest.mark.asyncio
    async def test_think_tag_extracted_from_content(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
        """Test that <think/> tags are extracted and removed from content."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = '<think>this is my thinking</think>The actual response'
        mock_message.type = 'ai'
        mock_message.tool_calls = []
        
        # Update the mock_graph to return our message
        async def mock_ainvoke(*args, **kwargs):
            return {"messages": [mock_message]}
        mock_graph.ainvoke = mock_ainvoke
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            session_id = manager.spawn_session(agent_id="coder", session_id="test-session")
            
            result = await manager.send_message(session_id, "Hello!")
            
            # Thinking should be extracted
            assert result.thinking_extracted == "this is my thinking"
            # Content should have tags removed
            assert result.content == "The actual response"
            # No metadata thinking in this case
            assert result.thinking is None

    @pytest.mark.asyncio
    async def test_multiple_think_tags_extracted(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
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
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            session_id = manager.spawn_session(agent_id="coder", session_id="test-session")
            
            result = await manager.send_message(session_id, "Hello!")
            
            # Both thoughts should be combined with newline
            assert result.thinking_extracted == "First thought\nSecond thought"
            # Content should have all tags removed
            assert result.content == "Some textMore text"

    @pytest.mark.asyncio
    async def test_think_tag_with_attributes(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
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
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            session_id = manager.spawn_session(agent_id="coder", session_id="test-session")
            
            result = await manager.send_message(session_id, "Hello!")
            
            assert result.thinking_extracted == "My reasoning here"
            assert result.content == "Another thought"

    @pytest.mark.asyncio
    async def test_thinking_metadata_priority_over_extracted(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
        """Test that metadata thinking takes priority over extracted thinking."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = '<think>Extracted thinking</think>'

        mock_message.type = 'ai'
        mock_message.tool_calls = []
        # Simulate metadata thinking (from provider)
        mock_message.additional_kwargs = {"reasoning_content": "Metadata thinking"}
        
        async def mock_ainvoke(*args, **kwargs):
            return {"messages": [mock_message]}
        mock_graph.ainvoke = mock_ainvoke
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            session_id = manager.spawn_session(agent_id="coder", session_id="test-session")
            
            result = await manager.send_message(session_id, "Hello!")
            
            # Both should be populated separately
            assert result.thinking == "Metadata thinking"
            assert result.thinking_extracted == "Extracted thinking"
            assert result.content == ""

    @pytest.mark.asyncio
    async def test_no_think_tag_returns_none_extracted(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
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
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            session_id = manager.spawn_session(agent_id="coder", session_id="test-session")
            
            result = await manager.send_message(session_id, "Hello!")
            
            assert result.thinking_extracted is None
            assert result.thinking is None
            assert result.content == "Just a regular response"

    @pytest.mark.asyncio
    async def test_case_insensitive_think_tags(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph, mock_session_repository):
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
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            session_id = manager.spawn_session(agent_id="coder", session_id="test-session")
            
            result = await manager.send_message(session_id, "Hello!")
            
            assert result.thinking_extracted == "Upper case thinking"
            assert result.content == "Response"
class TestGenerateSessionTitle:
    """Tests for _generate_session_title method."""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM that returns a title."""
        mock = Mock()
        mock_response = Mock()
        mock_response.content = "Test Session Title"
        mock.invoke.return_value = mock_response
        return mock

    @pytest.mark.asyncio
    async def test_generate_session_title_success(self, mock_config, mock_llm, mock_session_repository):
        """Test that title is generated and stored."""
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.graph.ThinkingChatOpenAI', return_value=mock_llm):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            
            # Mock the session repository to return a session with no title
            mock_session = MagicMock()
            mock_session.session_metadata = {}
            mock_session_repository.get.return_value = mock_session
            
            # Call the method
            title = await manager._generate_session_title("test-session", "Hello, how are you?")
            
            # Verify title was generated
            assert title is not None
            assert title == "Test Session Title"
            
            # Verify update_title was called
            mock_session_repository.update_title.assert_called_once()
            
    @pytest.mark.asyncio
    async def test_generate_session_title_already_exists(self, mock_config, mock_session_repository):
        """Test that returns None when title already exists."""
        with patch('daemon.manager.PromptCache', return_value=Mock()):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            
            # Mock the session repository to return a session with existing title
            mock_session = MagicMock()
            mock_session.session_metadata = {"title": "Existing Title"}
            mock_session_repository.get.return_value = mock_session
            
            # Call the method - should return None since title exists
            title = await manager._generate_session_title("test-session", "Hello!")
            
            # Should return None because title already exists
            assert title is None

    @pytest.mark.asyncio
    async def test_generate_session_title_llm_failure(self, mock_config, mock_session_repository):
        """Test that handles LLM failure gracefully."""
        # We need to mock at the instantiation level too, since the try/except 
        # doesn't cover the LLM instantiation (line 1126 in manager.py)
        mock_llm_instance = Mock()
        mock_llm_instance.invoke.side_effect = Exception("LLM Error")
        
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.graph.ThinkingChatOpenAI', return_value=mock_llm_instance), \
             patch('daemon.manager.logger') as mock_logger:
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            
            # Mock the session repository
            mock_session = MagicMock()
            mock_session.session_metadata = {}
            mock_session_repository.get.return_value = mock_session
            
            # Call the method - should not raise (exception is caught in try/except)
            title = await manager._generate_session_title("test-session", "Hello!")
            
            # Should return None on failure
            assert title is None
            
            # Should not call update_title
            mock_session_repository.update_title.assert_not_called()
            
            # Should log warning
            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_generate_session_title_truncates_long_titles(self, mock_config, mock_session_repository):
        """Test that long titles are truncated to 100 chars."""
        long_title = "A" * 200  # 200 character title
        
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = long_title
        mock_llm.invoke.return_value = mock_response
        
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.graph.ThinkingChatOpenAI', return_value=mock_llm):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            
            # Mock the session repository
            mock_session = MagicMock()
            mock_session.session_metadata = {}
            mock_session_repository.get.return_value = mock_session
            
            # Call the method
            title = await manager._generate_session_title("test-session", "Hello!")
            
            # Verify title was truncated to 100 chars (actually 97 + "...")
            assert title is not None
            assert len(title) <= 100
            assert title.endswith("...")

    @pytest.mark.asyncio
    async def test_generate_session_title_empty_message(self, mock_config, mock_session_repository):
        """Test that empty message returns None."""
        with patch('daemon.manager.PromptCache', return_value=Mock()):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            
            # Call with empty message
            title = await manager._generate_session_title("test-session", "")
            assert title is None
            
            # Call with whitespace only
            title = await manager._generate_session_title("test-session", "   ")
            assert title is None

    @pytest.mark.asyncio
    async def test_generate_session_title_list_content(self, mock_config, mock_session_repository):
        """Test that list content from LLM is handled correctly."""
        mock_llm = Mock()
        mock_response = Mock()
        # Return content as a list (some LLM providers return this format)
        mock_response.content = [{"type": "text", "text": "List Response Title"}]
        mock_llm.invoke.return_value = mock_response
        
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.graph.ThinkingChatOpenAI', return_value=mock_llm):
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            
            # Mock the session repository
            mock_session = MagicMock()
            mock_session.session_metadata = {}
            mock_session_repository.get.return_value = mock_session
            
            # Call the method
            title = await manager._generate_session_title("test-session", "Hello!")
            
            # Verify title was extracted from list
            assert title is not None
            assert "List Response Title" in title


class TestGenerateAndBroadcastTitle:
    """Tests for _generate_and_broadcast_title helper method."""

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_success(self, mock_config, mock_session_repository):
        """Test that title is generated and broadcast correctly."""
        with patch('daemon.manager.PromptCache', return_value=Mock()):
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            manager.broadcaster = AsyncMock()
            
            # Mock _generate_session_title to return a title
            manager._generate_session_title = AsyncMock(return_value="Test Title")
            
            # Call the method
            await manager._generate_and_broadcast_title("test-session", "Hello, how are you?")
            
            # Verify broadcast was called with correct event
            manager.broadcaster.broadcast.assert_called_once()
            call_args = manager.broadcaster.broadcast.call_args
            event = call_args[0][0]
            assert isinstance(event, Event)
            assert event.type == "title_updated"
            assert event.session_id == "test-session"
            assert event.message_id == ""
            assert event.data == {"title": "Test Title"}

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_no_title_returned(self, mock_config, mock_session_repository):
        """Test that broadcast is NOT called when no title is generated."""
        with patch('daemon.manager.PromptCache', return_value=Mock()):
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            manager.broadcaster = AsyncMock()
            
            # Mock _generate_session_title to return None
            manager._generate_session_title = AsyncMock(return_value=None)
            
            # Call the method
            await manager._generate_and_broadcast_title("test-session", "Hello!")
            
            # Verify broadcast was NOT called
            manager.broadcaster.broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_error_caught(self, mock_config, mock_session_repository):
        """Test that errors are caught and logged without crashing."""
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.manager.logger') as mock_logger:
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            manager.broadcaster = AsyncMock()
            
            # Mock _generate_session_title to raise an exception
            manager._generate_session_title = AsyncMock(side_effect=Exception("LLM Error"))
            
            # Call the method - should not raise
            await manager._generate_and_broadcast_title("test-session", "Hello!")
            
            # Verify broadcast was NOT called due to error
            manager.broadcaster.broadcast.assert_not_called()
            
            # Verify warning was logged
            mock_logger.warning.assert_called()
            assert "Failed to generate title" in str(mock_logger.warning.call_args)

    @pytest.mark.asyncio
    async def test_generate_and_broadcast_title_broadcasts_correct_event(self, mock_config, mock_session_repository):
        """Test that the broadcast event has exactly the expected structure."""
        with patch('daemon.manager.PromptCache', return_value=Mock()):
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
            manager.broadcaster = AsyncMock()
            
            # Mock _generate_session_title to return a specific title
            manager._generate_session_title = AsyncMock(return_value="Exact Title")
            
            # Call the method
            await manager._generate_and_broadcast_title("session-123", "User message content")
            
            # Capture the Event object passed to broadcast
            manager.broadcaster.broadcast.assert_called_once()
            event = manager.broadcaster.broadcast.call_args[0][0]
            
            # Assert event structure
            assert event.type == "title_updated"
            assert event.session_id == "session-123"
            assert event.message_id == ""
            assert event.data == {"title": "Exact Title"}


class TestTitleGenerationFireAndForget:
    """Tests for asyncio.create_task behavior in _process_queue."""

    @pytest.mark.asyncio
    async def test_title_generation_does_not_block_completed_event(self, mock_config, mock_session_repository):
        """Test that completed event is broadcast BEFORE title generation finishes (fire-and-forget)."""
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.manager.get_session_messages', new_callable=AsyncMock, return_value=[]) as mock_get_messages:
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
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
                session_id="test-session",
                content="Hello!",
                source="test",
                retry_count=0
            )
            manager._queue_repository = Mock()
            manager._queue_repository.dequeue_by_session = Mock(side_effect=[queued_msg, None])
            manager._queue_repository.get_status = Mock(return_value="processing")
            manager._queue_repository.complete = Mock()
            manager._queue_repository.is_empty = Mock(return_value=True)
            
            # Mock session metadata
            mock_session = Mock()
            mock_session.parent_id = None
            mock_session.session_metadata = {}
            mock_session_repository.get.return_value = mock_session
            
            # Record start time
            start_time = time.monotonic()
            
            # Run _process_queue - should return quickly due to fire-and-forget
            await manager._process_queue("test-session")
            
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
    async def test_title_generation_not_triggered_for_non_first_message(self, mock_config, mock_session_repository):
        """Test that title generation is NOT triggered when is_first_message is False."""
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.manager.get_session_messages', new_callable=AsyncMock, return_value=["existing-message"]) as mock_get_messages:
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
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
                session_id="test-session",
                content="Hello again!",
                source="test",
                retry_count=0
            )
            manager._queue_repository = Mock()
            manager._queue_repository.dequeue_by_session = Mock(side_effect=[queued_msg, None])
            manager._queue_repository.get_status = Mock(return_value="processing")
            manager._queue_repository.complete = Mock()
            manager._queue_repository.is_empty = Mock(return_value=True)
            
            # Mock session metadata
            mock_session = Mock()
            mock_session.parent_id = None
            mock_session.session_metadata = {}
            mock_session_repository.get.return_value = mock_session
            
            # Run _process_queue
            await manager._process_queue("test-session")
            
            # Verify title generation was NOT called
            assert not title_gen_called, "Title generation should NOT be called for non-first messages"
            
            # Verify NO title_updated event was broadcast
            title_updated_calls = [c for c in manager.broadcaster.broadcast.call_args_list 
                                  if c[0][0].type == "title_updated"]
            assert len(title_updated_calls) == 0, "No title_updated event should be broadcasted"

    @pytest.mark.asyncio
    async def test_fire_and_forget_isolation(self, mock_config, mock_session_repository):
        """Test that _process_queue returns quickly even when title generation is slow."""
        with patch('daemon.manager.PromptCache', return_value=Mock()), \
             patch('daemon.manager.get_session_messages', new_callable=AsyncMock, return_value=[]) as mock_get_messages:
            
            manager = SessionManager(mock_config)
            manager._session_repository = mock_session_repository
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
                session_id="test-session",
                content="Hello!",
                source="test",
                retry_count=0
            )
            manager._queue_repository = Mock()
            manager._queue_repository.dequeue_by_session = Mock(side_effect=[queued_msg, None])
            manager._queue_repository.get_status = Mock(return_value="processing")
            manager._queue_repository.complete = Mock()
            manager._queue_repository.is_empty = Mock(return_value=True)
            
            # Mock session metadata
            mock_session = Mock()
            mock_session.parent_id = None
            mock_session.session_metadata = {}
            mock_session_repository.get.return_value = mock_session
            
            # Measure how long _process_queue takes
            start_time = time.monotonic()
            await manager._process_queue("test-session")
            elapsed = time.monotonic() - start_time
            
            # _process_queue should return quickly (< 0.5s) even with slow title generation
            assert elapsed < 0.5, f"_process_queue took {elapsed:.2f}s, should return quickly with fire-and-forget title generation"
