"""Unit tests for MCP KB server (daemon/mcp/kb_server.py)."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager


def _make_message(tool_calls):
    """Build a mock message with a given list of tool calls."""
    msg = MagicMock()
    msg.tool_calls = tool_calls
    return msg


def _make_tool_call(name):
    """Build a tool call dict (as LangGraph stores them)."""
    return {"name": name, "args": {}, "id": f"call_{name}"}


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_manager():
    """Create a mock InstanceManager."""
    manager = MagicMock()
    manager._job_queue_service = MagicMock()
    
    # Create mock project repository with all required async methods
    project_repo = MagicMock()
    
    # Default project returned when searching by ID
    def get_project(project_id):
        if project_id:
            mock_project = MagicMock()
            mock_project.project_id = project_id
            mock_project.name = project_id
            mock_project.shortnames = []
            mock_project.main_directory = f"/path/to/{project_id}"
            mock_project.status = "active"
            mock_project.tags = []
            return mock_project
        return None
    
    project_repo.get = get_project
    project_repo.get_by_name = lambda name: None
    project_repo.get_by_shortname = lambda shortname: None
    project_repo.get_by_directory = lambda directory: None
    project_repo.list_projects = lambda **kwargs: []
    project_repo.search = lambda query=None, limit=20: []
    manager._project_repository = project_repo
    
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
    import daemon.mcp.kb_server as kb_server_module
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

class TestExploreReturnsResponseUnchanged:
    """Test that explore returns the explorer's response without stripping any heading.

    The "Need Update KB" heading is no longer relevant — it's a legacy
    artifact. The system now derives the flag deterministically from the
    child's checkpoint tool calls.
    """

    @pytest.mark.asyncio
    async def test_explore_returns_response_with_legacy_heading_intact(self, kb_server_setup):
        """Legacy '## Need Update KB:' heading in response is returned to caller as-is."""
        setup = kb_server_setup

        # No checkpointer → read_file_called defaults to False
        if hasattr(setup["manager"], "_checkpointer"):
            del setup["manager"]._checkpointer

        mock_response = "## Need Update KB: false\nSome result text"

        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, return_value=("Some result text", "test-child-id")):
            result = await setup["explore_tool"].fn(
                query="test query",
                project_id="test-project",
                mode="hybrid",
            )

        # Response is returned untouched (no stripping).
        assert result == "Some result text"


class TestExploreTriggersKbUpdateWhenReadFileCalled:
    """Test that explore triggers KB update when the explorer called read_file.

    The 'Need Update KB' signal is derived from the child's checkpoint: any
    ``read_file`` tool call implies the KB didn't have the answer.
    """

    @pytest.mark.asyncio
    async def test_explore_triggers_kb_update_when_read_file_called(self, kb_server_setup):
        """read_file in checkpoint → _enqueue_kb_update_job should be scheduled."""
        setup = kb_server_setup

        # Checkpointer reports a read_file call
        mock_checkpointer = MagicMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("read_file")]),
                ]
            }
        })
        setup["manager"]._checkpointer = mock_checkpointer

        mock_response = "Knowledge found about the codebase"

        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, return_value=(mock_response, "test-child-id")), \
             patch("daemon.mcp.kb_server._enqueue_kb_update_job", new_callable=AsyncMock) as mock_enqueue, \
             patch("daemon.mcp.kb_server._check_read_file_called_via_checkpoint", new_callable=AsyncMock, return_value=True):

            result = await setup["explore_tool"].fn(
                query="test query",
                project_id="test-project",
                mode="hybrid",
            )

        # Verify the KB update job was scheduled via asyncio.ensure_future
        mock_enqueue.assert_called_once()
        call_kwargs = mock_enqueue.call_args.kwargs
        assert call_kwargs["query"] == "test query"
        assert call_kwargs["project_id"] == "test-project"
        assert "Knowledge found" in call_kwargs["explorer_response"]

    @pytest.mark.asyncio
    async def test_explore_skips_kb_update_when_no_read_file(self, kb_server_setup):
        """No read_file in checkpoint → no _enqueue_kb_update_job is scheduled."""
        setup = kb_server_setup

        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, return_value=("Some result", "test-child-id")), \
             patch("daemon.mcp.kb_server._enqueue_kb_update_job", new_callable=AsyncMock) as mock_enqueue, \
             patch("daemon.mcp.kb_server._check_read_file_called_via_checkpoint", new_callable=AsyncMock, return_value=False):

            await setup["explore_tool"].fn(
                query="test query",
                project_id="test-project",
                mode="hybrid",
            )

        # No job should have been enqueued
        mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_ignores_legacy_heading_when_no_read_file(self, kb_server_setup):
        """Legacy '## Need Update KB: true' in response is ignored — checkpoint is source of truth."""
        setup = kb_server_setup

        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, return_value=("## Need Update KB: true\nSome result", "test-child-id")), \
             patch("daemon.mcp.kb_server._enqueue_kb_update_job", new_callable=AsyncMock) as mock_enqueue, \
             patch("daemon.mcp.kb_server._check_read_file_called_via_checkpoint", new_callable=AsyncMock, return_value=False):

            await setup["explore_tool"].fn(
                query="test query",
                project_id="test-project",
                mode="hybrid",
            )

        # No job enqueued — system check wins over the legacy heading.
        mock_enqueue.assert_not_called()


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
    async def test_explore_error_when_no_project_identifier_provided(self, kb_server_setup):
        """Explore should return error when no project identifier is provided."""
        setup = kb_server_setup
        
        result = await setup["explore_tool"].fn(
            query="test query",
            project_id=None,
            project_name=None,
            project_path=None,
            mode="hybrid",
        )
        
        assert "Error:" in result
        # The actual error message contains "No project identifier" or "Provide"
        assert "No project identifier" in result or "Provide" in result


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
        
        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, return_value=(None, "test-child-id")):
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

        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, return_value=("Result", "test-child-id")):
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


# =============================================================================
# Helper Functions for New Tests
# =============================================================================

def _create_mock_project(project_id, name, shortnames=None, main_directory=None, status="active", tags=None):
    """Create a mock project object."""
    project = MagicMock()
    project.project_id = project_id
    project.name = name
    project.shortnames = shortnames or []
    project.main_directory = main_directory
    project.status = status
    project.tags = tags or []
    return project


# =============================================================================
# Test Cases for _resolve_project
# =============================================================================

class TestResolveProject:
    """Test the module-level _resolve_project function."""

    @pytest.fixture
    def mock_project_repository(self):
        """Create a mock project repository with all required methods."""
        repo = MagicMock()
        repo.get = MagicMock()
        repo.get_by_name = MagicMock()
        repo.get_by_shortname = MagicMock()
        repo.get_by_directory = MagicMock()
        repo.list_projects = MagicMock(return_value=[])
        repo.search = MagicMock(return_value=[])
        return repo

    @pytest.fixture
    def setup_with_repo(self, kb_server_setup, mock_project_repository):
        """Setup with mock repository attached to manager."""
        setup = kb_server_setup
        setup["manager"]._project_repository = mock_project_repository
        return setup

    @pytest.mark.asyncio
    async def test_exact_match_by_project_id(self, setup_with_repo, mock_project_repository):
        """Exact match by project_id returns the project_id."""
        from daemon.mcp import kb_server as kb_server_module
        
        mock_project = _create_mock_project(
            project_id="exact-id-123",
            name="Test Project",
        )
        mock_project_repository.get.return_value = mock_project
        
        result, error = await kb_server_module._resolve_project(
            project_id="exact-id-123",
            project_name=None,
            project_path=None,
        )
        
        assert result == "exact-id-123"
        assert error is None
        mock_project_repository.get.assert_called_once_with("exact-id-123")

    @pytest.mark.asyncio
    async def test_exact_match_by_project_name(self, setup_with_repo, mock_project_repository):
        """Exact match by name returns project.id."""
        from daemon.mcp import kb_server as kb_server_module
        
        mock_project = _create_mock_project(
            project_id="uuid-456",
            name="My Test Project",
        )
        mock_project_repository.get_by_name.return_value = mock_project
        
        result, error = await kb_server_module._resolve_project(
            project_id=None,
            project_name="My Test Project",
            project_path=None,
        )
        
        assert result == "uuid-456"
        assert error is None
        mock_project_repository.get_by_name.assert_called_once_with("My Test Project")

    @pytest.mark.asyncio
    async def test_exact_match_by_shortname(self, setup_with_repo, mock_project_repository):
        """Exact match by shortname returns project.id (via project_name parameter)."""
        from daemon.mcp import kb_server as kb_server_module
        
        mock_project = _create_mock_project(
            project_id="short-789",
            name="Short Project",
            shortnames=["sp"],
        )
        # Important: get_by_name must return None first, then get_by_shortname finds it
        mock_project_repository.get_by_name.return_value = None
        mock_project_repository.get_by_shortname.return_value = mock_project
        
        result, error = await kb_server_module._resolve_project(
            project_id=None,
            project_name="sp",
            project_path=None,
        )
        
        assert result == "short-789"
        assert error is None
        mock_project_repository.get_by_shortname.assert_called_once_with("sp")

    @pytest.mark.asyncio
    async def test_exact_match_by_project_path(self, setup_with_repo, mock_project_repository):
        """Exact match by path returns project.project_id."""
        from daemon.mcp import kb_server as kb_server_module
        
        mock_project = _create_mock_project(
            project_id="path-abc",
            name="Path Project",
            main_directory="/Users/test/path-project",
        )
        # get_by_directory returns list[Project]
        mock_project_repository.get_by_directory.return_value = [mock_project]
        
        result, error = await kb_server_module._resolve_project(
            project_id=None,
            project_name=None,
            project_path="/Users/test/path-project",
        )
        
        assert result == "path-abc"
        assert error is None
        mock_project_repository.get_by_directory.assert_called_once_with("/Users/test/path-project")

    @pytest.mark.asyncio
    async def test_fuzzy_uuid_match(self, setup_with_repo, mock_project_repository):
        """Fuzzy UUID (85% threshold) resolves correctly."""
        from daemon.mcp import kb_server as kb_server_module
        
        # Create a project with a full UUID
        full_uuid = "550e8400-e29b-41d4-a716-446655440000"
        mock_project = _create_mock_project(
            project_id=full_uuid,
            name="UUID Project",
        )
        
        # Return None for exact match, project for fuzzy match
        mock_project_repository.get.return_value = None
        mock_project_repository.get_by_name.return_value = None
        mock_project_repository.get_by_shortname.return_value = None
        mock_project_repository.get_by_directory.return_value = None
        mock_project_repository.list_projects.return_value = [mock_project]
        
        # Provide 85% matching UUID (just change last digit)
        fuzzy_uuid = "550e8400-e29b-41d4-a716-446655440001"
        
        result, error = await kb_server_module._resolve_project(
            project_id=fuzzy_uuid,
            project_name=None,
            project_path=None,
        )
        
        assert result == full_uuid
        assert error is None

    @pytest.mark.asyncio
    async def test_fuzzy_name_match(self, setup_with_repo, mock_project_repository):
        """Fuzzy name (typo 'agents-ensamble', 80% threshold) resolves."""
        from daemon.mcp import kb_server as kb_server_module
        
        mock_project = _create_mock_project(
            project_id="agents-uuid",
            name="agents-ensemble",
        )
        
        mock_project_repository.get.return_value = None
        mock_project_repository.get_by_name.return_value = None
        mock_project_repository.get_by_shortname.return_value = None
        mock_project_repository.get_by_directory.return_value = None
        mock_project_repository.list_projects.return_value = [mock_project]
        
        # Typo: 'ensamble' instead of 'ensemble'
        result, error = await kb_server_module._resolve_project(
            project_id=None,
            project_name="agents-ensamble",
            project_path=None,
        )
        
        assert result == "agents-uuid"
        assert error is None

    @pytest.mark.asyncio
    async def test_fuzzy_path_match(self, setup_with_repo, mock_project_repository):
        """Fuzzy path (70% threshold) resolves."""
        from daemon.mcp import kb_server as kb_server_module
        
        mock_project = _create_mock_project(
            project_id="fuzzy-path-uuid",
            name="Fuzzy Path Project",
            main_directory="/Users/test/my-project",
        )
        
        mock_project_repository.get.return_value = None
        mock_project_repository.get_by_name.return_value = None
        mock_project_repository.get_by_shortname.return_value = None
        mock_project_repository.get_by_directory.return_value = None
        mock_project_repository.list_projects.return_value = [mock_project]
        
        # Similar path with small typo
        result, error = await kb_server_module._resolve_project(
            project_id=None,
            project_name=None,
            project_path="/Users/test/my-projec",  # Missing last character
        )
        
        assert result == "fuzzy-path-uuid"
        assert error is None

    @pytest.mark.asyncio
    async def test_no_match_near_candidate(self, setup_with_repo, mock_project_repository):
        """No match but near candidate returns 'Did you mean?' error."""
        from daemon.mcp import kb_server as kb_server_module
        
        # Create a project with a name that is similar but below 0.8 threshold
        # 0.8+ = auto-resolve, 0.6-0.8 = "Did you mean?" error
        mock_project = _create_mock_project(
            project_id="close-match-uuid",
            name="my-test-project",
        )
        
        mock_project_repository.get.return_value = None
        mock_project_repository.get_by_name.return_value = None
        mock_project_repository.get_by_shortname.return_value = None
        mock_project_repository.get_by_directory.return_value = None
        mock_project_repository.list_projects.return_value = [mock_project]
        
        # Search for something that's similar but not too similar (0.6-0.8 range)
        # "my-test-xyz" vs "my-test-project" should be around 0.6-0.7
        result, error = await kb_server_module._resolve_project(
            project_id=None,
            project_name="my-test-xyz",
            project_path=None,
        )
        
        assert result is None
        assert error is not None
        assert "Did you mean" in error
        assert "my-test-project" in error

    @pytest.mark.asyncio
    async def test_no_match_at_all(self, setup_with_repo, mock_project_repository):
        """No match returns error listing available projects."""
        from daemon.mcp import kb_server as kb_server_module
        
        project1 = _create_mock_project(project_id="uuid-1", name="Project One")
        project2 = _create_mock_project(project_id="uuid-2", name="Project Two")
        
        mock_project_repository.get.return_value = None
        mock_project_repository.get_by_name.return_value = None
        mock_project_repository.get_by_shortname.return_value = None
        mock_project_repository.get_by_directory.return_value = None
        mock_project_repository.list_projects.return_value = [project1, project2]
        
        result, error = await kb_server_module._resolve_project(
            project_id="nonexistent-id",
            project_name=None,
            project_path=None,
        )
        
        assert result is None
        assert error is not None
        assert "Project One" in error
        assert "Project Two" in error

    @pytest.mark.asyncio
    async def test_no_params_provided(self, setup_with_repo, mock_project_repository):
        """None of 3 params returns error listing projects."""
        from daemon.mcp import kb_server as kb_server_module
        
        project1 = _create_mock_project(project_id="uuid-1", name="Project One")
        project2 = _create_mock_project(project_id="uuid-2", name="Project Two")
        
        mock_project_repository.list_projects.return_value = [project1, project2]
        
        result, error = await kb_server_module._resolve_project(
            project_id=None,
            project_name=None,
            project_path=None,
        )
        
        assert result is None
        assert error is not None
        # The actual error message format
        assert "No project identifier" in error or "project" in error.lower()
        assert "Project One" in error

    @pytest.mark.asyncio
    async def test_manager_not_set(self, reset_kb_server_module):
        """Manager not set returns initialization error."""
        from daemon.mcp import kb_server as kb_server_module
        
        # Ensure manager is None
        assert kb_server_module._manager is None
        
        result, error = await kb_server_module._resolve_project(
            project_id="any-id",
            project_name=None,
            project_path=None,
        )
        
        assert result is None
        assert error is not None
        assert "not initialized" in error.lower()

    @pytest.mark.asyncio
    async def test_empty_project_id_returns_error(self, setup_with_repo):
        """Empty string project_id should return clear error."""
        from daemon.mcp import kb_server as kb_server_module
        
        _, error = await kb_server_module._resolve_project(project_id="")
        assert error is not None
        assert "empty" in error.lower()

    @pytest.mark.asyncio
    async def test_empty_project_name_returns_error(self, setup_with_repo):
        """Empty string project_name should return clear error."""
        from daemon.mcp import kb_server as kb_server_module
        
        _, error = await kb_server_module._resolve_project(project_name="")
        assert error is not None
        assert "empty" in error.lower()

    @pytest.mark.asyncio
    async def test_empty_project_path_returns_error(self, setup_with_repo):
        """Empty string project_path should return clear error."""
        from daemon.mcp import kb_server as kb_server_module
        
        _, error = await kb_server_module._resolve_project(project_path="")
        assert error is not None
        assert "empty" in error.lower()

    @pytest.mark.asyncio
    async def test_project_id_takes_priority_over_project_name(self, setup_with_repo, mock_project_repository):
        """When both project_id and project_name provided, project_id wins."""
        from daemon.mcp import kb_server as kb_server_module
        
        mock_project = _create_mock_project(
            project_id="id-A",
            name="Project A",
        )
        mock_project_repository.get = MagicMock(return_value=mock_project)
        
        # Provide both — project_id should be used, project_name ignored
        resolved_id, error = await kb_server_module._resolve_project(
            project_id="id-A",
            project_name="different-name"
        )
        assert resolved_id == "id-A"
        assert error is None
        # Verify repo.get was called (project_id branch), NOT get_by_name
        mock_project_repository.get.assert_called_once_with("id-A")

    @pytest.mark.asyncio
    async def test_short_name_skips_fuzzy_matching(self, setup_with_repo, mock_project_repository):
        """Very short project names (< 3 chars) should skip fuzzy matching."""
        from daemon.mcp import kb_server as kb_server_module
        
        projects = [_create_mock_project(
            project_id="id-1",
            name="ab",
            shortnames=["ab"],
        )]
        mock_project_repository.get_by_name = MagicMock(return_value=None)
        mock_project_repository.get_by_shortname = MagicMock(return_value=None)
        mock_project_repository.list_projects = MagicMock(return_value=projects)
        
        _, error = await kb_server_module._resolve_project(project_name="ac")  # length 2
        assert error is not None
        assert "not found" in error.lower()
        assert "Available projects" in error  # Falls through to listing, no fuzzy match


# =============================================================================
# Test Cases for ensemble_kb_list_projects
# =============================================================================

class TestListProjects:
    """Test ensemble_kb_list_projects tool."""

    @pytest.mark.asyncio
    async def test_returns_json_list_of_projects(self, kb_server_setup):
        """Verify JSON output with expected fields (id, name, shortnames, main_directory, status, tags)."""
        setup = kb_server_setup
        mcp = setup["mcp"]
        
        list_tool = mcp._tool_manager.get_tool("ensemble_kb_list_projects")
        
        # Setup mock repository with projects
        project1 = _create_mock_project(
            project_id="uuid-1",
            name="Project One",
            shortnames=["p1"],
            main_directory="/path/to/project1",
            status="active",
            tags=["tag1", "tag2"],
        )
        project2 = _create_mock_project(
            project_id="uuid-2",
            name="Project Two",
            shortnames=[],
            main_directory="/path/to/project2",
            status="archived",
            tags=[],
        )
        setup["manager"]._project_repository.list_projects = MagicMock(return_value=[project1, project2])
        
        result = await list_tool.fn()
        
        # Verify result is JSON string
        import json
        projects = json.loads(result)
        
        assert len(projects) == 2
        assert projects[0]["id"] == "uuid-1"
        assert projects[0]["name"] == "Project One"
        assert projects[0]["shortnames"] == ["p1"]
        assert projects[0]["main_directory"] == "/path/to/project1"
        assert projects[0]["status"] == "active"
        assert projects[0]["tags"] == ["tag1", "tag2"]
        
        assert projects[1]["id"] == "uuid-2"
        assert projects[1]["name"] == "Project Two"
        assert projects[1]["status"] == "archived"

    @pytest.mark.asyncio
    async def test_passes_limit_offset_status_params(self, kb_server_setup):
        """Verify params are passed to repository."""
        setup = kb_server_setup
        mcp = setup["mcp"]
        
        list_tool = mcp._tool_manager.get_tool("ensemble_kb_list_projects")
        
        setup["manager"]._project_repository.list_projects = MagicMock(return_value=[])
        
        await list_tool.fn(limit=10, offset=5, status="active")
        
        setup["manager"]._project_repository.list_projects.assert_called_once_with(limit=10, offset=5, status="active")

    @pytest.mark.asyncio
    async def test_returns_error_when_manager_not_set(self, reset_kb_server_module):
        """Error when manager not set."""
        from daemon.mcp import kb_server as kb_server_module
        
        mcp = kb_server_module.create_kb_mcp_server()
        list_tool = mcp._tool_manager.get_tool("ensemble_kb_list_projects")
        
        result = await list_tool.fn()
        
        assert "not initialized" in result.lower()


# =============================================================================
# Test Cases for ensemble_kb_search_projects
# =============================================================================

class TestSearchProjects:
    """Test ensemble_kb_search_projects tool."""

    @pytest.mark.asyncio
    async def test_returns_json_list_of_matching_projects(self, kb_server_setup):
        """Verify JSON output with matching projects."""
        setup = kb_server_setup
        mcp = setup["mcp"]
        
        search_tool = mcp._tool_manager.get_tool("ensemble_kb_search_projects")
        
        mock_project = _create_mock_project(
            project_id="search-uuid",
            name="Search Result Project",
            shortnames=["srp"],
            main_directory="/path/to/search",
            status="active",
            tags=["search"],
        )
        setup["manager"]._project_repository.search = MagicMock(return_value=[mock_project])
        
        result = await search_tool.fn(query="search query")
        
        import json
        projects = json.loads(result)
        
        assert len(projects) == 1
        assert projects[0]["id"] == "search-uuid"
        assert projects[0]["name"] == "Search Result Project"

    @pytest.mark.asyncio
    async def test_passes_query_and_limit_params(self, kb_server_setup):
        """Verify params passed to repository."""
        setup = kb_server_setup
        mcp = setup["mcp"]
        
        search_tool = mcp._tool_manager.get_tool("ensemble_kb_search_projects")
        
        setup["manager"]._project_repository.search = MagicMock(return_value=[])
        
        await search_tool.fn(query="test query", limit=20)
        
        setup["manager"]._project_repository.search.assert_called_once_with(query="test query", limit=20)

    @pytest.mark.asyncio
    async def test_returns_error_when_manager_not_set(self, reset_kb_server_module):
        """Error when manager not set."""
        from daemon.mcp import kb_server as kb_server_module
        
        mcp = kb_server_module.create_kb_mcp_server()
        search_tool = mcp._tool_manager.get_tool("ensemble_kb_search_projects")
        
        result = await search_tool.fn(query="test")
        
        assert "not initialized" in result.lower()


# =============================================================================
# Test Cases for Explore with Alternative Project Identifiers
# =============================================================================

class TestExploreWithAlternativeProjectIdentifiers:
    """Test explore tool with project_name and project_path parameters."""

    @pytest.mark.asyncio
    async def test_explore_with_project_name(self, kb_server_setup):
        """Explore should work with project_name param instead of project_id."""
        setup = kb_server_setup
        
        mock_project = _create_mock_project(
            project_id="resolved-uuid-123",
            name="test-project",
            shortnames=["tp"],
            main_directory="/path/to/project",
        )
        
        # Mock repository methods for name lookup
        setup["manager"]._project_repository.get_by_name = MagicMock(return_value=mock_project)
        setup["manager"]._project_repository.get_by_shortname = MagicMock(return_value=None)
        setup["manager"]._project_repository.get_by_directory = MagicMock(return_value=None)
        
        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, return_value=("Result", "test-child-id")):
            result = await setup["explore_tool"].fn(
                query="test query",
                project_name="test-project",
                mode="hybrid",
            )

        assert result == "Result"

        # Verify the repository was called with correct name
        setup["manager"]._project_repository.get_by_name.assert_called_once_with("test-project")

    @pytest.mark.asyncio
    async def test_explore_with_project_path(self, kb_server_setup):
        """Explore should work with project_path param instead of project_id."""
        setup = kb_server_setup
        
        mock_project = _create_mock_project(
            project_id="path-resolved-uuid",
            name="path-project",
            main_directory="/Users/test/my-project",
        )
        
        # Mock repository methods for path lookup (return list for get_by_directory)
        setup["manager"]._project_repository.get_by_directory = MagicMock(return_value=[mock_project])
        setup["manager"]._project_repository.get_by_name = MagicMock(return_value=None)
        setup["manager"]._project_repository.get_by_shortname = MagicMock(return_value=None)
        
        with patch("daemon.mcp.kb_server.invoke_agent_and_wait", new_callable=AsyncMock, return_value=("Path Result", "test-child-id")):
            result = await setup["explore_tool"].fn(
                query="test query",
                project_path="/Users/test/my-project",
                mode="local",
            )
        
        assert result == "Path Result"
        
        # Verify the repository was called with correct path
        setup["manager"]._project_repository.get_by_directory.assert_called_once_with("/Users/test/my-project")

    @pytest.mark.asyncio
    async def test_explore_error_when_no_project_identifier(self, kb_server_setup):
        """Explore should return error when none of project_id, project_name, project_path provided."""
        setup = kb_server_setup
        
        # Pass None for all project identifiers to trigger the "Provide either" error
        result = await setup["explore_tool"].fn(
            query="test query",
            project_id=None,
            project_name=None,
            project_path=None,
            mode="hybrid",
        )
        
        # Should get error about needing to provide a project identifier
        assert "Provide either" in result or "project" in result.lower()


# =============================================================================
# Test Cases for Experience with Alternative Project Identifiers
# =============================================================================

class TestExperienceWithAlternativeProjectIdentifiers:
    """Test experience tool with project_name and project_path parameters."""

    @pytest.mark.asyncio
    async def test_experience_with_project_name(self, kb_server_setup):
        """Experience should work with project_name param."""
        setup = kb_server_setup
        
        mock_project = _create_mock_project(
            project_id="experience-uuid-456",
            name="experience-project",
            shortnames=["ep"],
            main_directory="/path/to/experience",
        )
        
        # Mock repository methods for name lookup
        setup["manager"]._project_repository.get_by_name = MagicMock(return_value=mock_project)
        setup["manager"]._project_repository.get_by_shortname = MagicMock(return_value=None)
        setup["manager"]._project_repository.get_by_directory = MagicMock(return_value=None)
        
        with patch("daemon.mcp.kb_server._enqueue_experience_job", new_callable=AsyncMock):
            result = await setup["experience_tool"].fn(
                text="New experience to record",
                project_name="experience-project",
            )
        
        assert result == "Knowledge recording started."
        
        # Verify the repository was called with correct name
        setup["manager"]._project_repository.get_by_name.assert_called_once_with("experience-project")

    @pytest.mark.asyncio
    async def test_experience_with_project_path(self, kb_server_setup):
        """Experience should work with project_path param."""
        setup = kb_server_setup
        
        mock_project = _create_mock_project(
            project_id="exp-path-uuid",
            name="exp-path-project",
            main_directory="/Users/test/exp-path",
        )
        
        # Mock repository methods for path lookup
        setup["manager"]._project_repository.get_by_directory = MagicMock(return_value=[mock_project])
        setup["manager"]._project_repository.get_by_name = MagicMock(return_value=None)
        setup["manager"]._project_repository.get_by_shortname = MagicMock(return_value=None)
        
        with patch("daemon.mcp.kb_server._enqueue_experience_job", new_callable=AsyncMock):
            result = await setup["experience_tool"].fn(
                text="Experience with path",
                project_path="/Users/test/exp-path",
            )
        
        assert result == "Knowledge recording started."
        
        # Verify the repository was called with correct path
        setup["manager"]._project_repository.get_by_directory.assert_called_once_with("/Users/test/exp-path")

    @pytest.mark.asyncio
    async def test_experience_error_when_no_project_identifier(self, kb_server_setup):
        """Experience should return error when none of project_id, project_name, project_path provided."""
        setup = kb_server_setup
        
        # Pass None for all project identifiers to trigger the "Provide either" error
        result = await setup["experience_tool"].fn(
            text="Some knowledge",
            project_id=None,
            project_name=None,
            project_path=None,
        )
        
        # Should get error about needing to provide a project identifier
        assert "Provide either" in result or "project" in result.lower()
