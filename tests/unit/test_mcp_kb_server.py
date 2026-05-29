"""Unit tests for MCP KB server (daemon/mcp/kb_server.py)."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_manager():
    """Create a mock InstanceManager."""
    manager = MagicMock()
    manager._job_queue_service = MagicMock()
    return manager


@pytest.fixture
def reset_kb_server_module():
    """Reset the kb_server module state before and after each test."""
    import daemon.mcp.kb_server as kb_server_module
    
    # Save original state
    original_manager = kb_server_module._manager
    original_mcp_server = kb_server_module._mcp_server
    original_http_app = kb_server_module._http_app
    
    # Reset to initial state
    kb_server_module._manager = None
    kb_server_module._mcp_server = None
    kb_server_module._http_app = None
    
    yield kb_server_module
    
    # Restore original state
    kb_server_module._manager = original_manager
    kb_server_module._mcp_server = original_mcp_server
    kb_server_module._http_app = original_http_app


@pytest.fixture
def kb_server_setup(reset_kb_server_module, mock_manager):
    """Create KB MCP server with mock manager and return tool functions."""
    from daemon.mcp import kb_server as kb_server_module
    from daemon.rag import config as rag_config_module
    
    # Set the manager
    kb_server_module.set_kb_mcp_manager(mock_manager)
    
    # Enable RAG for testing (set LIGHTRAG_HOST env var and _rag_enabled flag)
    original_host = os.environ.get("LIGHTRAG_HOST")
    os.environ["LIGHTRAG_HOST"] = "http://localhost:8000"
    original_rag_enabled = rag_config_module._rag_enabled
    rag_config_module._rag_enabled = True
    
    # Create the server (tools will use the patched _rag_enabled via is_rag_enabled())
    mcp = kb_server_module.create_kb_mcp_server()
    
    # Get tool functions from the tool manager
    explore_tool = mcp._tool_manager.get_tool("ensemble_kb_explore")
    experience_tool = mcp._tool_manager.get_tool("ensemble_kb_experience")
    
    yield {
        "module": kb_server_module,
        "mcp": mcp,
        "manager": mock_manager,
        "explore_tool": explore_tool,
        "experience_tool": experience_tool,
    }
    
    # Restore original RAG state
    rag_config_module._rag_enabled = original_rag_enabled
    if original_host is None:
        del os.environ["LIGHTRAG_HOST"]
    else:
        os.environ["LIGHTRAG_HOST"] = original_host


# =============================================================================
# Test Cases
# =============================================================================

class TestExploreReturnsResultWithKbHeadingStripped:
    """Test that explore strips the KB heading from results."""

    @pytest.mark.asyncio
    async def test_explore_returns_result_with_kb_heading_stripped(self, kb_server_setup):
        """Heading '## Need Update KB: false' should be stripped from result."""
        setup = kb_server_setup
        
        # Mock invoke_agent_and_wait to return response with KB heading
        mock_response = "## Need Update KB: false\nSome result text"
        
        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, return_value=mock_response):
            result = await setup["explore_tool"].fn(
                query="test query",
                project_id="test-project",
                mode="hybrid",
            )
        
        # Verify heading is stripped
        assert result == "Some result text"


class TestExploreTriggersKbUpdateWhenFlagTrue:
    """Test that explore triggers KB update when flag is true."""

    @pytest.mark.asyncio
    async def test_explore_triggers_kb_update_when_flag_true(self, kb_server_setup):
        """When explorer returns '## Need Update KB: true', _enqueue_kb_update_job should be scheduled."""
        setup = kb_server_setup
        
        mock_response = "## Need Update KB: true\nKnowledge found about the codebase"
        
        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, return_value=mock_response), \
             patch("daemon.mcp.kb_server._enqueue_kb_update_job", new_callable=AsyncMock) as mock_enqueue:
            
            result = await setup["explore_tool"].fn(
                query="test query",
                project_id="test-project",
                mode="hybrid",
            )
        
        # Verify the KB update job was scheduled via asyncio.ensure_future
        # Note: asyncio.ensure_future schedules but doesn't await, so we check .called
        mock_enqueue.assert_called_once()
        call_kwargs = mock_enqueue.call_args.kwargs
        assert call_kwargs["query"] == "test query"
        assert call_kwargs["project_id"] == "test-project"
        assert "Knowledge found" in call_kwargs["explorer_response"]


class TestExperienceEnqueuesViaEnqueueExperienceJob:
    """Test that experience tool uses _enqueue_experience_job (not invoke_agent_and_wait)."""

    @pytest.mark.asyncio
    async def test_experience_enqueues_via_enqueue_experience_job(self, kb_server_setup):
        """Experience tool should call _enqueue_experience_job, not invoke_agent_and_wait."""
        setup = kb_server_setup
        
        with patch("daemon.mcp.kb_server._enqueue_experience_job", new_callable=AsyncMock) as mock_enqueue, \
             patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock) as mock_invoke:
            
            result = await setup["experience_tool"].fn(
                text="New knowledge to record",
                project_id="test-project",
            )
        
        # Verify _enqueue_experience_job was called (via asyncio.ensure_future)
        mock_enqueue.assert_called_once()
        call_kwargs = mock_enqueue.call_args.kwargs
        assert call_kwargs["text"] == "New knowledge to record"
        assert call_kwargs["project_id"] == "test-project"
        
        # Verify invoke_agent_and_wait was NOT called
        mock_invoke.assert_not_called()
        
        # Verify success message
        assert result == "Knowledge recording started."


class TestExploreErrorWhenProjectIdMissing:
    """Test explore validation for project_id."""

    @pytest.mark.asyncio
    async def test_explore_error_when_project_id_missing(self, kb_server_setup):
        """Explore should return error when project_id is empty."""
        setup = kb_server_setup
        
        result = await setup["explore_tool"].fn(
            query="test query",
            project_id="",
            mode="hybrid",
        )
        
        assert "project_id is required" in result
        assert "Error:" in result


class TestExploreErrorWhenModeInvalid:
    """Test explore validation for mode parameter."""

    @pytest.mark.asyncio
    async def test_explore_error_when_mode_invalid(self, kb_server_setup):
        """Explore should return error for invalid mode."""
        setup = kb_server_setup
        
        result = await setup["explore_tool"].fn(
            query="test query",
            project_id="test-project",
            mode="invalid",
        )
        
        assert "Error:" in result
        assert "invalid" in result.lower()
        assert "local" in result  # Should mention valid modes


class TestErrorWhenRagDisabled:
    """Test that tools return error when RAG is disabled."""

    @pytest.mark.asyncio
    async def test_error_when_rag_disabled(self, kb_server_setup):
        """Both tools should return error when is_rag_enabled returns False."""
        setup = kb_server_setup
        
        with patch("daemon.mcp.kb_server.is_rag_enabled", return_value=False):
            # Test explore
            explore_result = await setup["explore_tool"].fn(
                query="test query",
                project_id="test-project",
                mode="hybrid",
            )
            
            # Test experience
            experience_result = await setup["experience_tool"].fn(
                text="Some knowledge",
                project_id="test-project",
            )
        
        # Both should return RAG disabled error
        assert "RAG" in explore_result or "not enabled" in explore_result.lower()
        assert "RAG" in experience_result or "not enabled" in experience_result.lower()


class TestErrorWhenManagerNotInitialized:
    """Test that tools return error when manager is not set."""

    @pytest.mark.asyncio
    async def test_error_when_manager_not_initialized(self, reset_kb_server_module):
        """Both tools should return error when _manager is None."""
        from daemon.mcp import kb_server as kb_server_module
        
        # Create server WITHOUT setting manager
        mcp = kb_server_module.create_kb_mcp_server()
        explore_tool = mcp._tool_manager.get_tool("ensemble_kb_explore")
        experience_tool = mcp._tool_manager.get_tool("ensemble_kb_experience")
        
        # Test explore without manager
        explore_result = await explore_tool.fn(
            query="test query",
            project_id="test-project",
            mode="hybrid",
        )
        
        # Test experience without manager
        experience_result = await experience_tool.fn(
            text="Some knowledge",
            project_id="test-project",
        )
        
        # Both should return not initialized error
        assert "not initialized" in explore_result.lower()
        assert "not initialized" in experience_result.lower()


class TestExploreNoneResultReturnsTimeoutMessage:
    """Test explore behavior when agent returns None."""

    @pytest.mark.asyncio
    async def test_explore_none_result_returns_timeout_message(self, kb_server_setup):
        """When invoke_agent_and_wait returns None, should return timeout message."""
        setup = kb_server_setup
        
        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, return_value=None):
            result = await setup["explore_tool"].fn(
                query="test query",
                project_id="test-project",
                mode="hybrid",
            )
        
        assert "timed out" in result.lower() or "failed" in result.lower()


class TestExploreConstructsMessageCorrectly:
    """Test that explore constructs the correct message format."""

    @pytest.mark.asyncio
    async def test_explore_constructs_message_correctly(self, kb_server_setup):
        """Message should match format: f'Query (mode={mode}): {query}\\nProject: {project_id}'."""
        setup = kb_server_setup
        
        captured_message = None
        
        async def capture_invoke(manager, agent_id, message, **kwargs):
            nonlocal captured_message
            captured_message = message
            return "Some result"
        
        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", side_effect=capture_invoke):
            await setup["explore_tool"].fn(
                query="my test query",
                project_id="my-project-123",
                mode="local",
            )
        
        # Verify message format
        expected = "Query (mode=local): my test query\nProject: my-project-123"
        assert captured_message == expected


class TestSessionManagerAndHelpersAfterCreate:
    """Test that session manager and helper functions work after create_kb_mcp_server()."""

    def test_session_manager_and_helpers_after_create(self, reset_kb_server_module, mock_manager):
        """get_kb_mcp_http_app(), get_kb_mcp_sse_app(), get_kb_mcp_session_manager() should work after create."""
        from daemon.mcp import kb_server as kb_server_module
        
        # Set manager and create server
        kb_server_module.set_kb_mcp_manager(mock_manager)
        kb_server_module.create_kb_mcp_server()
        
        # Verify all helpers return non-None values
        http_app = kb_server_module.get_kb_mcp_http_app()
        assert http_app is not None
        
        sse_app = kb_server_module.get_kb_mcp_sse_app()
        assert sse_app is not None
        
        session_manager = kb_server_module.get_kb_mcp_session_manager()
        assert session_manager is not None


class TestHelpersErrorBeforeCreate:
    """Test that helper functions raise RuntimeError before create_kb_mcp_server()."""

    def test_helpers_error_before_create(self, reset_kb_server_module):
        """All helper functions should raise RuntimeError before create_kb_mcp_server() is called."""
        from daemon.mcp import kb_server as kb_server_module
        
        # Ensure server is not created
        assert kb_server_module._mcp_server is None
        assert kb_server_module._http_app is None
        
        # get_kb_mcp_http_app should raise
        with pytest.raises(RuntimeError, match="not created"):
            kb_server_module.get_kb_mcp_http_app()
        
        # get_kb_mcp_sse_app should raise
        with pytest.raises(RuntimeError, match="not created"):
            kb_server_module.get_kb_mcp_sse_app()
        
        # get_kb_mcp_session_manager should raise
        with pytest.raises(RuntimeError, match="not created"):
            kb_server_module.get_kb_mcp_session_manager()


class TestExploreWithDifferentModes:
    """Test explore tool with different valid modes."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["local", "global", "hybrid", "naive"])
    async def test_explore_accepts_valid_modes(self, kb_server_setup, mode):
        """All valid modes should be accepted without error."""
        setup = kb_server_setup
        
        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, return_value="Result"):
            result = await setup["explore_tool"].fn(
                query="test query",
                project_id="test-project",
                mode=mode,
            )
        
        # Should succeed (not contain error message)
        assert "Error:" not in result
        assert result == "Result"


class TestExperienceDoesNotStripKbHeading:
    """Test that experience tool does not process KB heading (it doesn't call explorer)."""

    @pytest.mark.asyncio
    async def test_experience_does_not_call_explorer(self, kb_server_setup):
        """Experience tool should NOT call invoke_agent_and_wait at all."""
        setup = kb_server_setup
        
        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock) as mock_invoke, \
             patch("daemon.mcp.kb_server._enqueue_experience_job", new_callable=AsyncMock):
            
            result = await setup["experience_tool"].fn(
                text="Knowledge to record",
                project_id="test-project",
            )
        
        # Should NOT have called explorer
        mock_invoke.assert_not_called()


class TestExploreHandlesExceptionGracefully:
    """Test that explore handles exceptions gracefully."""

    @pytest.mark.asyncio
    async def test_explore_handles_exception_from_invoke_agent(self, kb_server_setup):
        """Exception from invoke_agent_and_wait should be caught and return error message."""
        setup = kb_server_setup
        
        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, side_effect=RuntimeError("Agent failed")):
            result = await setup["explore_tool"].fn(
                query="test query",
                project_id="test-project",
                mode="hybrid",
            )
        
        # Should return sanitized error message (not expose internal details)
        assert "Error:" in result
        assert "internal error" in result.lower()
