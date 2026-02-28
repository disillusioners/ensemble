"""Tests for daemon/manager.py"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

from daemon.manager import SessionManager, parse_think_tags
from daemon.config import Config, LLMConfig, LimitsConfig, PersistenceConfig, DaemonConfig, AgentsConfig


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
        daemon=DaemonConfig(host="0.0.0.0", port=8080),
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
    # Mock invoke to return a response with messages
    mock_message = Mock()
    mock_message.content = "Test response"
    mock_message.type = 'ai'
    mock_message.tool_calls = []  # Empty tool calls to avoid iteration error
    graph.invoke.return_value = {"messages": [mock_message]}
    return graph


@pytest.fixture
def mock_prompt_cache():
    """Create a mock prompt cache."""
    return Mock()


class TestSessionManagerInit:
    """Tests for SessionManager initialization."""

    def test_session_manager_init(self, mock_config, mock_checkpointer, mock_prompt_cache):
        """Test manager initialization."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = SessionManager(mock_config)
            
            assert manager.config == mock_config
            assert manager.conn is not None
            assert manager.checkpointer == mock_checkpointer
            assert manager.sessions == {}


class TestSpawnSession:
    """Tests for spawn_session method."""

    def test_spawn_session_generates_id(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test that session_id is auto-generated."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]), \
             patch('daemon.manager.save_session_metadata'), \
             patch('daemon.manager.get_session_metadata', return_value=None):
            
            manager = SessionManager(mock_config)
            session_id = manager.spawn_session(agent_dir="/path/to/agent")
            
            # Should have generated a UUID
            assert session_id is not None
            assert len(session_id) == 36  # UUID format

    def test_spawn_session_uses_provided_id(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test that provided session_id is used."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]), \
             patch('daemon.manager.save_session_metadata'), \
             patch('daemon.manager.get_session_metadata', return_value=None):
            
            manager = SessionManager(mock_config)
            session_id = manager.spawn_session(agent_dir="/path/to/agent", session_id="custom-session-id")
            
            assert session_id == "custom-session-id"

    def test_spawn_session_max_sessions_limit(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test that max_sessions limit is enforced."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            # Set max_sessions to 2 for this test
            mock_config.limits.max_sessions = 2
            
            manager = SessionManager(mock_config)
            
            # Create 2 sessions (reaching the limit)
            with patch('daemon.manager.build_session_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_session_tools', return_value=[]), \
                 patch('daemon.manager.save_session_metadata'), \
                 patch('daemon.manager.get_session_metadata', return_value=None):
                
                manager.spawn_session(agent_dir="/path/to/agent", session_id="session-1")
                manager.spawn_session(agent_dir="/path/to/agent", session_id="session-2")
                
                # Third session should raise ValueError
                with pytest.raises(ValueError, match="Max sessions limit reached"):
                    manager.spawn_session(agent_dir="/path/to/agent", session_id="session-3")

    def test_spawn_session_max_children_limit(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test that max_children_per_session limit is enforced."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            # Set max_children_per_session to 2 for this test
            mock_config.limits.max_children_per_session = 2
            
            manager = SessionManager(mock_config)
            
            # Parent session with 2 children should reach the limit
            with patch('daemon.manager.build_session_graph', return_value=mock_graph), \
                 patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
                 patch('daemon.manager.create_session_tools', return_value=[]), \
                 patch('daemon.manager.save_session_metadata'), \
                 patch('daemon.manager.get_session_metadata', return_value={"children": ["child1", "child2"]}):
                
                # Third child should raise ValueError
                with pytest.raises(ValueError, match="Max children per session limit reached"):
                    manager.spawn_session(agent_dir="/path/to/agent", parent_id="parent-session")

    def test_spawn_session_creates_graph(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test that graph is created and stored."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph) as mock_build, \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]), \
             patch('daemon.manager.save_session_metadata'), \
             patch('daemon.manager.get_session_metadata', return_value=None):
            
            manager = SessionManager(mock_config)
            session_id = manager.spawn_session(agent_dir="/path/to/agent", session_id="test-session")
            
            # Verify graph was built and stored
            mock_build.assert_called_once()
            assert session_id in manager.sessions
            assert manager.sessions[session_id][0] == mock_graph
            assert manager.sessions[session_id][1] == "/path/to/agent"


class TestSendMessage:
    """Tests for send_message method."""

    def test_send_message_success(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test sending message to session."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]), \
             patch('daemon.manager.save_session_metadata'), \
             patch('daemon.manager.get_session_metadata', return_value=None):
            
            manager = SessionManager(mock_config)
            session_id = manager.spawn_session(agent_dir="/path/to/agent", session_id="test-session")
            
            # Send a message
            response = manager.send_message(session_id, "Hello!")
            
            # Verify graph.invoke was called
            mock_graph.invoke.assert_called_once()
            assert response.content == "Test response"

    def test_send_message_session_not_found(self, mock_config, mock_checkpointer, mock_prompt_cache):
        """Test error when session doesn't exist."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = SessionManager(mock_config)
            
            with pytest.raises(KeyError, match="Session not found"):
                manager.send_message("non-existent-session", "Hello!")


class TestTerminateSession:
    """Tests for terminate_session method."""

    def test_terminate_session_success(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test terminating session."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]), \
             patch('daemon.manager.save_session_metadata'), \
             patch('daemon.manager.get_session_metadata', return_value=None), \
             patch('daemon.manager.update_session_status') as mock_update:
            
            manager = SessionManager(mock_config)
            session_id = manager.spawn_session(agent_dir="/path/to/agent", session_id="test-session")
            
            result = manager.terminate_session(session_id)
            
            assert result is True
            assert session_id not in manager.sessions
            mock_update.assert_called_once()

    def test_terminate_session_not_found(self, mock_config, mock_checkpointer, mock_prompt_cache):
        """Test terminating non-existent session."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = SessionManager(mock_config)
            
            result = manager.terminate_session("non-existent-session")
            
            assert result is False


class TestGetSession:
    """Tests for get_session method."""

    def test_get_session_success(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test getting session graph."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]), \
             patch('daemon.manager.save_session_metadata'), \
             patch('daemon.manager.get_session_metadata', return_value=None):
            
            manager = SessionManager(mock_config)
            session_id = manager.spawn_session(agent_dir="/path/to/agent", session_id="test-session")
            
            graph = manager.get_session(session_id)
            
            assert graph == mock_graph

    def test_get_session_not_found(self, mock_config, mock_checkpointer, mock_prompt_cache):
        """Test error when session doesn't exist."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache):
            
            manager = SessionManager(mock_config)
            
            with pytest.raises(KeyError, match="Session not found"):
                manager.get_session("non-existent-session")


class TestListSessions:
    """Tests for list_sessions method."""

    def test_list_sessions(self, mock_config, mock_checkpointer, mock_prompt_cache):
        """Test listing sessions."""
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.list_all_sessions', return_value=[
                 {"session_id": "session-1", "agent_dir": "/path/1", "status": "running"},
                 {"session_id": "session-2", "agent_dir": "/path/2", "status": "idle"}
             ]):
            
            manager = SessionManager(mock_config)
            sessions = manager.list_sessions()
            
            assert len(sessions) == 2
            assert sessions[0]["session_id"] == "session-1"
            assert sessions[1]["session_id"] == "session-2"


class TestThinkTagParsing:
    """Tests for <think/> tag parsing in message responses."""

    def test_think_tag_extracted_from_content(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test that <think/> tags are extracted and removed from content."""
        # Use spec=[] to prevent Mock from auto-creating attributes like 'thinking'
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = "<think>this is my thinking</think>The actual response"
        mock_message.type = 'ai'
        mock_message.tool_calls = []
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]), \
             patch('daemon.manager.save_session_metadata'), \
             patch('daemon.manager.get_session_metadata', return_value=None):
            
            manager = SessionManager(mock_config)
            session_id = manager.spawn_session(agent_dir="/path/to/agent", session_id="test-session")
            
            result = manager.send_message(session_id, "Hello!")
            
            # Thinking should be extracted
            assert result.thinking_extracted == "this is my thinking"
            # Content should have tags removed
            assert result.content == "The actual response"
            # No metadata thinking in this case
            assert result.thinking is None

    def test_multiple_think_tags_extracted(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test that multiple <think/> tags are combined."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = "<think>First thought</think>Some text<think>Second thought</think>More text"
        mock_message.type = 'ai'
        mock_message.tool_calls = []
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]), \
             patch('daemon.manager.save_session_metadata'), \
             patch('daemon.manager.get_session_metadata', return_value=None):
            
            manager = SessionManager(mock_config)
            session_id = manager.spawn_session(agent_dir="/path/to/agent", session_id="test-session")
            
            result = manager.send_message(session_id, "Hello!")
            
            # Both thoughts should be combined with newline
            assert result.thinking_extracted == "First thought\nSecond thought"
            # Content should have all tags removed
            assert result.content == "Some textMore text"

    def test_think_tag_with_attributes(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test that <think/> tags with attributes are parsed."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = '<think budget="123" duration="456">My reasoning here</think>Response'
        mock_message.type = 'ai'
        mock_message.tool_calls = []
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]), \
             patch('daemon.manager.save_session_metadata'), \
             patch('daemon.manager.get_session_metadata', return_value=None):
            
            manager = SessionManager(mock_config)
            session_id = manager.spawn_session(agent_dir="/path/to/agent", session_id="test-session")
            
            result = manager.send_message(session_id, "Hello!")
            
            assert result.thinking_extracted == "My reasoning here"
            assert result.content == "Response"

    def test_thinking_metadata_priority_over_extracted(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test that metadata thinking is kept separate from extracted thinking."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls', 'additional_kwargs'])
        mock_message.content = "<think>Extracted thinking</think>Response"
        mock_message.type = 'ai'
        mock_message.tool_calls = []
        # Simulate metadata thinking (from provider)
        mock_message.additional_kwargs = {"reasoning_content": "Metadata thinking"}
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]), \
             patch('daemon.manager.save_session_metadata'), \
             patch('daemon.manager.get_session_metadata', return_value=None):
            
            manager = SessionManager(mock_config)
            session_id = manager.spawn_session(agent_dir="/path/to/agent", session_id="test-session")
            
            result = manager.send_message(session_id, "Hello!")
            
            # Both should be populated separately
            assert result.thinking == "Metadata thinking"
            assert result.thinking_extracted == "Extracted thinking"
            assert result.content == "Response"

    def test_no_think_tag_returns_none_extracted(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test that response without think tags has None for thinking_extracted."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = "Just a regular response"
        mock_message.type = 'ai'
        mock_message.tool_calls = []
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]), \
             patch('daemon.manager.save_session_metadata'), \
             patch('daemon.manager.get_session_metadata', return_value=None):
            
            manager = SessionManager(mock_config)
            session_id = manager.spawn_session(agent_dir="/path/to/agent", session_id="test-session")
            
            result = manager.send_message(session_id, "Hello!")
            
            assert result.thinking_extracted is None
            assert result.thinking is None
            assert result.content == "Just a regular response"

    def test_case_insensitive_think_tags(self, mock_config, mock_checkpointer, mock_prompt_cache, mock_graph):
        """Test that <THINK> and <Think> tags are also parsed."""
        mock_message = Mock(spec=['content', 'type', 'tool_calls'])
        mock_message.content = "<THINK>Upper case thinking</THINK>Response"
        mock_message.type = 'ai'
        mock_message.tool_calls = []
        mock_graph.invoke.return_value = {"messages": [mock_message]}
        
        with patch('daemon.manager.get_checkpointer', return_value=mock_checkpointer), \
             patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
             patch('daemon.manager.build_session_graph', return_value=mock_graph), \
             patch('daemon.manager.load_and_cache_prompt', return_value=("system prompt", 100)), \
             patch('daemon.manager.create_session_tools', return_value=[]), \
             patch('daemon.manager.save_session_metadata'), \
             patch('daemon.manager.get_session_metadata', return_value=None):
            
            manager = SessionManager(mock_config)
            session_id = manager.spawn_session(agent_dir="/path/to/agent", session_id="test-session")
            
            result = manager.send_message(session_id, "Hello!")
            
            assert result.thinking_extracted == "Upper case thinking"
            assert result.content == "Response"
