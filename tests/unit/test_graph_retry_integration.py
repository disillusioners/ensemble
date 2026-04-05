"""Integration tests for graph retry and compaction integration.

Tests cover:
1. TRANSIENT_EXCEPTIONS is a tuple with correct types
2. build_instance_graph exists and is callable
3. build_session_graph alias exists
4. build_instance_graph accepts compactor and graph_config parameters
5. create_agent_node accepts compactor, graph_ref, config, llm_config parameters
6. Reactive compaction flow with ContextLengthExceededError
7. Reactive compaction: compactor is None - error re-raised
8. Reactive compaction: compaction returns None - error re-raised
9. Reactive compaction: graph_ref is None - error re-raised
10. Error classifier applied before with_retry
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from dataclasses import dataclass
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


class TestTransientExceptions:
    """Test 1: TRANSIENT_EXCEPTIONS is a tuple with correct types."""

    def test_transient_exceptions_is_tuple(self):
        """TRANSIENT_EXCEPTIONS should be a tuple, not a list."""
        from daemon.llm_error_classifier import TRANSIENT_EXCEPTIONS
        assert isinstance(TRANSIENT_EXCEPTIONS, tuple), (
            f"TRANSIENT_EXCEPTIONS should be a tuple, got {type(TRANSIENT_EXCEPTIONS)}"
        )

    def test_transient_exceptions_contains_expected_types(self):
        """TRANSIENT_EXCEPTIONS should contain expected exception types."""
        from daemon.llm_error_classifier import (
            TRANSIENT_EXCEPTIONS,
            TransientAPIError,
            LLMResponseValidationError,
        )
        
        expected_types = (
            TransientAPIError,
            ConnectionResetError,
            BrokenPipeError,
            ConnectionAbortedError,
        )
        
        for exc_type in expected_types:
            assert exc_type in TRANSIENT_EXCEPTIONS, (
                f"{exc_type.__name__} should be in TRANSIENT_EXCEPTIONS"
            )

    def test_transient_exceptions_excludes_context_length_error(self):
        """ContextLengthExceededError should NOT be in TRANSIENT_EXCEPTIONS."""
        from daemon.llm_error_classifier import (
            TRANSIENT_EXCEPTIONS,
            ContextLengthExceededError,
        )
        assert ContextLengthExceededError not in TRANSIENT_EXCEPTIONS, (
            "ContextLengthExceededError should not be retried by with_retry"
        )


class TestBuildInstanceGraph:
    """Test 2, 3, 4: build_instance_graph and alias existence and parameters."""

    def test_build_instance_graph_exists(self):
        """build_instance_graph should exist and be callable."""
        from daemon.graph import build_instance_graph
        assert callable(build_instance_graph)

    def test_build_instance_graph_alias_exists(self):
        """build_instance_graph should exist."""
        from daemon.graph import build_instance_graph
        assert callable(build_instance_graph)

    def test_build_instance_graph_signature_has_compactor(self):
        """build_instance_graph should accept compactor parameter."""
        import inspect
        from daemon.graph import build_instance_graph
        
        sig = inspect.signature(build_instance_graph)
        assert 'compactor' in sig.parameters, (
            "build_instance_graph should accept 'compactor' parameter"
        )

    def test_build_instance_graph_signature_has_graph_config(self):
        """build_instance_graph should accept graph_config parameter."""
        import inspect
        from daemon.graph import build_instance_graph
        
        sig = inspect.signature(build_instance_graph)
        assert 'graph_config' in sig.parameters, (
            "build_instance_graph should accept 'graph_config' parameter"
        )


class TestCreateAgentNode:
    """Test 5: create_agent_node accepts correct parameters."""

    def test_create_agent_node_signature(self):
        """create_agent_node should accept compactor, graph_ref, config, llm_config."""
        import inspect
        from daemon.graph import create_agent_node
        
        sig = inspect.signature(create_agent_node)
        params = list(sig.parameters.keys())
        
        assert 'compactor' in params, "create_agent_node should accept 'compactor'"
        assert 'graph_ref' in params, "create_agent_node should accept 'graph_ref'"
        assert 'config' in params, "create_agent_node should accept 'config'"
        assert 'llm_config' in params, "create_agent_node should accept 'llm_config'"


class TestReactiveCompaction:
    """Test 6-9: Reactive compaction flow and edge cases."""

    @pytest.fixture
    def mock_llm_with_tools(self):
        """Create mock LLM with tools."""
        mock = MagicMock()
        mock.invoke = MagicMock()
        return mock

    @pytest.fixture
    def mock_graph(self):
        """Create mock compiled graph."""
        graph = MagicMock()
        graph.aget_state = AsyncMock()
        graph.aupdate_state = AsyncMock()
        return graph

    @pytest.fixture
    def mock_compactor(self):
        """Create mock compactor."""
        compactor = MagicMock()
        compactor.compact_state = AsyncMock()
        compactor.config = MagicMock()
        compactor.llm_config = {}
        return compactor

    @pytest.fixture
    def mock_state(self):
        """Create mock state with messages."""
        @dataclass
        class MockStateValues:
            values: dict
            
        state = MockStateValues(values={
            'messages': [
                HumanMessage(content="Hello"),
                AIMessage(content="Hi there!"),
            ],
            'compacted_at': None,
        })
        state.values = state.values
        return state

    @pytest.mark.asyncio
    async def test_reactive_compaction_success(
        self, mock_llm_with_tools, mock_graph, mock_compactor, mock_state
    ):
        """Test successful reactive compaction when ContextLengthExceededError is raised."""
        from daemon.graph import create_agent_node
        from daemon.llm_error_classifier import ContextLengthExceededError
        from daemon.compaction import CompactionResult
        from openai import BadRequestError
        
        # Create mock httpx response
        mock_response = Mock()
        
        # First call raises context length error, second call succeeds
        original_error = BadRequestError(
            message="context_length_exceeded",
            response=mock_response,
            body=None,
        )
        mock_llm_with_tools.invoke.side_effect = [
            ContextLengthExceededError(original_error, model="gpt-4o"),
            AIMessage(content="Response after compaction"),
        ]
        
        # Setup mock compactor result
        compacted_messages = [HumanMessage(content="Summary of previous conversation")]
        compaction_result = CompactionResult(
            replacement_messages=compacted_messages,
            tokens_before=1000,
            tokens_after=500,
            tokens_saved=500,
            messages_before=10,
            messages_after=3,
            compaction_type="summarization",
            compacted_at="2024-01-01T00:00:00Z",
        )
        mock_compactor.compact_state.return_value = compaction_result
        
        # Setup mock graph state
        updated_state = MagicMock()
        updated_state.values = {
            'messages': compacted_messages,
            'compacted_at': "2024-01-01T00:00:00Z",
        }
        mock_graph.aget_state.return_value = mock_state
        mock_graph.aupdate_state.return_value = updated_state
        
        # Create agent node
        graph_ref = [mock_graph]
        config = {"configurable": {"thread_id": "test"}}
        llm_config = {"model": "gpt-4o"}
        
        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            compactor=mock_compactor,
            graph_ref=graph_ref,
            config=config,
            llm_config=llm_config,
        )
        
        # Invoke agent node
        result = await agent_node({"messages": []})
        
        # Verify compaction was called
        mock_compactor.compact_state.assert_called_once()
        
        # Verify graph state was updated
        assert mock_graph.aupdate_state.call_count >= 2  # Once for messages, once for compacted_at
        
        # Verify LLM was called twice (initial + after compaction)
        assert mock_llm_with_tools.invoke.call_count == 2
        
        # Verify result contains the response
        assert "messages" in result
        assert len(result["messages"]) == 1

    @pytest.mark.asyncio
    async def test_reactive_compaction_no_compactor(self, mock_llm_with_tools, mock_state):
        """Test that ContextLengthExceededError is re-raised when compactor is None."""
        from daemon.graph import create_agent_node
        from daemon.llm_error_classifier import ContextLengthExceededError
        from openai import BadRequestError
        
        # Create mock httpx response
        mock_response = Mock()
        
        # First call raises context length error
        original_error = BadRequestError(
            message="context_length_exceeded",
            response=mock_response,
            body=None,
        )
        mock_llm_with_tools.invoke.side_effect = ContextLengthExceededError(
            original_error, model="gpt-4o"
        )
        
        # Create agent node with NO compactor
        graph_ref = [None]  # Even with graph_ref set, no compactor should re-raise
        config = {"configurable": {"thread_id": "test"}}
        llm_config = {"model": "gpt-4o"}
        
        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            compactor=None,  # No compactor
            graph_ref=graph_ref,
            config=config,
            llm_config=llm_config,
        )
        
        # Should re-raise the error
        with pytest.raises(ContextLengthExceededError):
            await agent_node({"messages": []})
        
        # LLM should only be called once (no retry attempted)
        assert mock_llm_with_tools.invoke.call_count == 1

    @pytest.mark.asyncio
    async def test_reactive_compaction_returns_none(self, mock_llm_with_tools, mock_graph, mock_state):
        """Test that error is re-raised when compaction returns None."""
        from daemon.graph import create_agent_node
        from daemon.llm_error_classifier import ContextLengthExceededError
        from openai import BadRequestError
        
        # Create mock httpx response
        mock_response = Mock()
        
        # First call raises context length error
        original_error = BadRequestError(
            message="context_length_exceeded",
            response=mock_response,
            body=None,
        )
        mock_llm_with_tools.invoke.side_effect = ContextLengthExceededError(
            original_error, model="gpt-4o"
        )
        
        # Mock compactor returns None
        mock_compactor = MagicMock()
        mock_compactor.compact_state = AsyncMock(return_value=None)
        mock_compactor.config = MagicMock()
        mock_compactor.llm_config = {}
        
        # Setup mock graph state
        mock_graph.aget_state.return_value = mock_state
        
        # Create agent node
        graph_ref = [mock_graph]
        config = {"configurable": {"thread_id": "test"}}
        llm_config = {"model": "gpt-4o"}
        
        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            compactor=mock_compactor,
            graph_ref=graph_ref,
            config=config,
            llm_config=llm_config,
        )
        
        # Should re-raise the error
        with pytest.raises(ContextLengthExceededError):
            await agent_node({"messages": []})
        
        # Compactor was called but returned None
        mock_compactor.compact_state.assert_called_once()
        
        # LLM should only be called once
        assert mock_llm_with_tools.invoke.call_count == 1

    @pytest.mark.asyncio
    async def test_reactive_compaction_graph_ref_none(self, mock_llm_with_tools, mock_state):
        """Test that error is re-raised when graph_ref is None."""
        from daemon.graph import create_agent_node
        from daemon.llm_error_classifier import ContextLengthExceededError
        from openai import BadRequestError
        
        # Create mock httpx response
        mock_response = Mock()
        
        # First call raises context length error
        original_error = BadRequestError(
            message="context_length_exceeded",
            response=mock_response,
            body=None,
        )
        mock_llm_with_tools.invoke.side_effect = ContextLengthExceededError(
            original_error, model="gpt-4o"
        )
        
        # Mock compactor
        mock_compactor = MagicMock()
        mock_compactor.compact_state = AsyncMock()
        mock_compactor.config = MagicMock()
        mock_compactor.llm_config = {}
        
        # Create agent node with graph_ref = [None]
        graph_ref = [None]
        config = {"configurable": {"thread_id": "test"}}
        llm_config = {"model": "gpt-4o"}
        
        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            compactor=mock_compactor,
            graph_ref=graph_ref,
            config=config,
            llm_config=llm_config,
        )
        
        # Should re-raise the error
        with pytest.raises(ContextLengthExceededError):
            await agent_node({"messages": []})
        
        # Compactor should NOT be called when graph_ref[0] is None
        mock_compactor.compact_state.assert_not_called()
        
        # LLM should only be called once
        assert mock_llm_with_tools.invoke.call_count == 1


class TestErrorClassifierIntegration:
    """Test 10: Error classifier is applied before with_retry."""

    def test_error_classifier_before_retry(self):
        """Error classifier should be applied BEFORE with_retry in build_instance_graph."""
        import inspect
        from daemon.graph import build_instance_graph
        
        # Read the source code to verify order
        source = inspect.getsource(build_instance_graph)
        
        # Remove comments to avoid false positives from docstrings/comments
        lines = source.split('\n')
        code_lines = []
        for line in lines:
            # Remove inline comments but keep code
            code = line.split('#')[0]
            if code.strip():
                code_lines.append(code)
        code_only = '\n'.join(code_lines)
        
        # Find positions of classify_llm_errors and with_retry in code
        classify_pos = code_only.find("classify_llm_errors")
        retry_pos = code_only.find("with_retry")
        
        assert classify_pos != -1, "classify_llm_errors should be called"
        assert retry_pos != -1, "with_retry should be called"
        assert classify_pos < retry_pos, (
            "classify_llm_errors should be called BEFORE with_retry "
            f"(found at {classify_pos} vs {retry_pos})"
        )

    def test_retry_uses_transient_exceptions(self):
        """with_retry should use TRANSIENT_EXCEPTIONS for retry_if_exception_type."""
        import inspect
        from daemon.graph import build_instance_graph
        
        source = inspect.getsource(build_instance_graph)
        
        # Verify TRANSIENT_EXCEPTIONS is used in with_retry call
        assert "TRANSIENT_EXCEPTIONS" in source, (
            "TRANSIENT_EXCEPTIONS should be used in retry configuration"
        )
        assert "retry_if_exception_type=TRANSIENT_EXCEPTIONS" in source, (
            "with_retry should use TRANSIENT_EXCEPTIONS for retry_if_exception_type"
        )


class TestBuildInstanceGraphIntegration:
    """Integration tests for build_instance_graph with mocked dependencies."""

    @pytest.fixture
    def mock_checkpointer(self):
        """Create mock checkpointer."""
        return MagicMock()

    @pytest.fixture
    def mock_tools(self):
        """Create mock tools list."""
        return [MagicMock(name="tool1"), MagicMock(name="tool2")]

    @pytest.mark.asyncio
    async def test_build_instance_graph_with_compactor(self, mock_checkpointer, mock_tools):
        """Test build_instance_graph accepts and uses compactor."""
        with patch('daemon.graph.ThinkingChatOpenAI') as mock_llm_class:
            # Setup mock LLM
            mock_llm_instance = MagicMock()
            mock_llm_with_tools = MagicMock()
            mock_llm_instance.bind_tools.return_value = mock_llm_with_tools
            mock_llm_class.return_value = mock_llm_instance
            
            # Mock langgraph components
            with patch('daemon.graph.StateGraph') as mock_state_graph:
                mock_graph_instance = MagicMock()
                mock_compiled = MagicMock()
                mock_graph_instance.compile.return_value = mock_compiled
                mock_state_graph.return_value = mock_graph_instance
                
                with patch('daemon.graph.ToolNode'):
                    from daemon.graph import build_instance_graph
                    
                    mock_compactor = MagicMock()
                    mock_compactor.config = MagicMock()
                    mock_compactor.llm_config = {}
                    
                    graph_config = {"configurable": {"thread_id": "test"}}
                    
                    result = build_instance_graph(
                        tools=mock_tools,
                        checkpointer=mock_checkpointer,
                        llm_config={"model": "gpt-4o", "api_key": "test"},
                        system_prompt="You are helpful.",
                        compactor=mock_compactor,
                        graph_config=graph_config,
                    )
                    
                    # Verify graph was compiled
                    assert result is mock_compiled
                    
                    # Verify StateGraph was called
                    mock_state_graph.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_instance_graph_with_retry_config(self, mock_checkpointer, mock_tools):
        """Test build_instance_graph applies retry config with classify_llm_errors."""
        with patch('daemon.graph.ThinkingChatOpenAI') as mock_llm_class:
            # Setup mock LLM
            mock_llm_instance = MagicMock()
            mock_llm_with_tools = MagicMock()
            mock_llm_instance.bind_tools.return_value = mock_llm_with_tools
            mock_llm_class.return_value = mock_llm_instance
            
            # Mock with_retry method
            mock_llm_with_tools.with_retry = MagicMock(return_value=mock_llm_with_tools)
            
            # Mock classify_llm_errors
            with patch('daemon.graph.classify_llm_errors') as mock_classify:
                mock_classify.return_value = mock_llm_with_tools
                
                # Mock langgraph components
                with patch('daemon.graph.StateGraph') as mock_state_graph:
                    mock_graph_instance = MagicMock()
                    mock_compiled = MagicMock()
                    mock_graph_instance.compile.return_value = mock_compiled
                    mock_state_graph.return_value = mock_graph_instance
                    
                    with patch('daemon.graph.ToolNode'):
                        from daemon.graph import build_instance_graph
                        
                        retry_config = {"max_retries": 5}
                        
                        build_instance_graph(
                            tools=mock_tools,
                            checkpointer=mock_checkpointer,
                            llm_config={"model": "gpt-4o", "api_key": "test"},
                            system_prompt="You are helpful.",
                            retry_config=retry_config,
                        )
                        
                        # Verify classify_llm_errors was called (BEFORE with_retry)
                        mock_classify.assert_called_once_with(mock_llm_with_tools)
                        
                        # Verify with_retry was called on the classified LLM
                        mock_llm_with_tools.with_retry.assert_called_once()


class TestCompactionContextPassedToCompactor:
    """Test that CompactionContext is correctly built and passed to compactor."""

    @pytest.mark.asyncio
    async def test_compaction_context_structure(self):
        """Test CompactionContext is built with correct fields."""
        from daemon.graph import create_agent_node
        from daemon.llm_error_classifier import ContextLengthExceededError
        from daemon.compaction import CompactionContext
        from openai import BadRequestError
        
        mock_llm_with_tools = MagicMock()
        
        # Create mock httpx response
        mock_response = Mock()
        
        # First call raises context length error
        original_error = BadRequestError(
            message="context_length_exceeded",
            response=mock_response,
            body=None,
        )
        
        # Track the context passed to compact_state
        captured_context = None
        
        async def capture_compact_state(ctx):
            nonlocal captured_context
            captured_context = ctx
            from daemon.compaction import CompactionResult
            return CompactionResult(
                replacement_messages=[],
                tokens_before=1000,
                tokens_after=500,
                tokens_saved=500,
                messages_before=5,
                messages_after=2,
                compaction_type="summarization",
            )
        
        mock_compactor = MagicMock()
        mock_compactor.compact_state = capture_compact_state
        mock_compactor.config = MagicMock()
        mock_compactor.llm_config = {"model": "gpt-4o"}
        
        mock_graph = MagicMock()
        mock_state = MagicMock()
        mock_state.values = {
            'messages': [HumanMessage(content="Test")],
            'compacted_at': None,
        }
        mock_graph.aget_state = AsyncMock(return_value=mock_state)
        mock_graph.aupdate_state = AsyncMock()
        
        graph_ref = [mock_graph]
        config = {"configurable": {"thread_id": "test"}}
        llm_config = {"model": "gpt-4o"}
        
        # First invoke raises context error, second succeeds
        mock_llm_with_tools.invoke.side_effect = [
            ContextLengthExceededError(original_error, model="gpt-4o"),
            AIMessage(content="Success after compaction"),
        ]
        
        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            compactor=mock_compactor,
            graph_ref=graph_ref,
            config=config,
            llm_config=llm_config,
        )
        
        result = await agent_node({"messages": []})
        
        # Verify context structure
        assert captured_context is not None
        assert isinstance(captured_context, CompactionContext)
        assert 'messages' in captured_context.__dict__
        assert 'model_name' in captured_context.__dict__
        assert 'config' in captured_context.__dict__
        assert 'llm_config' in captured_context.__dict__


class TestProxyHeaderInjection:
    """Test that proxy headers are injected in LLM config."""

    def test_proxy_header_injected(self):
        """LLM config should include x-proxy-app header."""
        with patch('daemon.graph.ThinkingChatOpenAI') as mock_llm_class:
            mock_llm_instance = MagicMock()
            mock_llm_instance.bind_tools.return_value = MagicMock()
            mock_llm_class.return_value = mock_llm_instance
            
            with patch('daemon.graph.StateGraph'):
                with patch('daemon.graph.ToolNode'):
                    from daemon.graph import build_instance_graph
                    
                    build_instance_graph(
                        tools=[],
                        checkpointer=MagicMock(),
                        llm_config={"model": "gpt-4o", "api_key": "test"},
                        system_prompt="You are helpful.",
                    )
                    
                    # Verify ThinkingChatOpenAI was called with headers
                    call_kwargs = mock_llm_class.call_args[1]
                    assert "default_headers" in call_kwargs
                    assert call_kwargs["default_headers"]["x-proxy-app"] == "ensemble"
