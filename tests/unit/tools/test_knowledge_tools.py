"""Tests for knowledge management tools (daemon.tools.knowledge_tools).

Tests the explore and experience tools created by create_knowledge_tools()
factory function, including RAG configuration checks, agent invocation,
and fire-and-forget patterns.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.tools.knowledge_tools import create_knowledge_tools


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def configured_env():
    """Set up environment variables for configured RAG client."""
    os.environ["LIGHTRAG_HOST"] = "http://localhost:8724"
    os.environ["LIGHTRAG_API_KEY"] = "test-api-key"
    os.environ["LIGHTRAG_WORKSPACE"] = "test-workspace"
    os.environ["LIGHTRAG_TIMEOUT"] = "60"

    yield {
        "host": "http://localhost:8724",
        "api_key": "test-api-key",
        "workspace": "test-workspace",
        "timeout": 60.0,
    }

    # Cleanup
    for key in ["LIGHTRAG_HOST", "LIGHTRAG_API_KEY", "LIGHTRAG_WORKSPACE", "LIGHTRAG_TIMEOUT"]:
        os.environ.pop(key, None)


@pytest.fixture
def unconfigured_env():
    """Ensure no RAG environment variables are set."""
    for key in ["LIGHTRAG_HOST", "LIGHTRAG_API_KEY", "LIGHTRAG_WORKSPACE", "LIGHTRAG_TIMEOUT"]:
        os.environ.pop(key, None)
    yield


@pytest.fixture
def mock_manager():
    """Create a mock InstanceManager with configured return values."""
    manager = MagicMock()

    # Configure _instance_repository.get to return mock instance with metadata
    # (NOT get_instance which returns CompiledStateGraph)
    mock_instance_meta = MagicMock()
    mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

    # Configure spawn_instance to return a predictable ID
    manager.spawn_instance = MagicMock(return_value="spawned-instance-abc123")

    # Configure enqueue_message as async
    manager.enqueue_message = AsyncMock()

    return manager


@pytest.fixture
def knowledge_tools(configured_env, mock_manager):
    """Create knowledge tools with RAG enabled."""
    tools = create_knowledge_tools(mock_manager, "parent-instance-id")
    return tools


# =============================================================================
# Factory Tests
# =============================================================================


class TestKnowledgeToolsFactory:
    """Tests for the create_knowledge_tools factory function."""

    def test_knowledge_tools_factory_returns_2_tools(self, configured_env, mock_manager):
        """Factory returns exactly 2 tools."""
        tools = create_knowledge_tools(mock_manager, "test-instance-id")
        assert len(tools) == 2

    def test_knowledge_tools_have_correct_category(self, knowledge_tools):
        """Both tools have _tool_category == 'knowledge'."""
        for tool in knowledge_tools:
            assert hasattr(tool, "_tool_category")
            assert tool._tool_category == "knowledge"


# =============================================================================
# Explore Tool Tests
# =============================================================================


class TestExploreTool:
    """Tests for the explore tool."""

    @pytest.mark.asyncio
    async def test_explore_success(self, configured_env, mock_manager):
        """Verify explore returns result from explorer agent."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project"}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock,
                   return_value="Explorer found relevant information about the project."):
            tools = create_knowledge_tools(mock_manager, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            result = await explore_tool.ainvoke({
                "query": "What is the project structure?",
            })

            assert "Explorer found" in result

    @pytest.mark.asyncio
    async def test_explore_not_configured(self, unconfigured_env, mock_manager):
        """Verify error when RAG is not configured."""
        tools = create_knowledge_tools(mock_manager, "test-instance-id")
        explore_tool = next(t for t in tools if t.name == "explore")

        result = await explore_tool.ainvoke({"query": "Test query"})

        assert "Error" in result
        assert "not configured" in result.lower()

    @pytest.mark.asyncio
    async def test_explore_timeout_returns_error(self, configured_env, mock_manager):
        """Verify graceful error when agent times out."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project"}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock,
                   return_value=None):
            tools = create_knowledge_tools(mock_manager, "test-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            result = await explore_tool.ainvoke({"query": "Long running query"})

            assert "timed out" in result.lower() or "failed" in result.lower()

    @pytest.mark.asyncio
    async def test_explore_auto_injects_project_id(self, configured_env, mock_manager):
        """Verify project_id from instance context is used."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "auto-detected-project"}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock,
                   return_value="Result") as mock_invoke:
            tools = create_knowledge_tools(mock_manager, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")
            await explore_tool.ainvoke({"query": "Test"})

            # Verify project_id was extracted from instance
            mock_manager._instance_repository.get.assert_called_with("parent-instance-id")

    @pytest.mark.asyncio
    async def test_explore_passes_mode_in_message(self, configured_env, mock_manager):
        """Verify mode parameter is included in the message sent to explorer."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project"}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock,
                   return_value="Result") as mock_invoke:
            tools = create_knowledge_tools(mock_manager, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({
                "query": "Test query",
                "mode": "local",
            })

            # Verify the call was made
            mock_invoke.assert_called_once()
            call_kwargs = mock_invoke.call_args.kwargs
            message = call_kwargs["message"]
            assert "mode=local" in message


# =============================================================================
# Experience Tool Tests
# =============================================================================


class TestExperienceTool:
    """Tests for the experience tool (fire-and-forget knowledge recording)."""

    @pytest.mark.asyncio
    async def test_experience_success(self, configured_env, mock_manager):
        """Verify experience returns confirmation after spawning."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        result = await experience_tool.ainvoke({
            "text": "This is important knowledge to record.",
        })

        assert "Knowledge recording started" in result
        mock_manager.spawn_instance.assert_called_once()
        mock_manager.enqueue_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_experience_not_configured(self, unconfigured_env, mock_manager):
        """Verify error when RAG is not configured."""
        tools = create_knowledge_tools(mock_manager, "test-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        result = await experience_tool.ainvoke({"text": "Some knowledge"})

        assert "Error" in result
        assert "not configured" in result.lower()
        # Should not have spawned any instance
        mock_manager.spawn_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_experience_auto_injects_project_id(self, configured_env, mock_manager):
        """Verify project_id from context is used when spawning."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        await experience_tool.ainvoke({"text": "Test knowledge text"})

        # Verify spawn_instance was called with project_id from instance metadata
        mock_manager.spawn_instance.assert_called_once()
        call_kwargs = mock_manager.spawn_instance.call_args.kwargs
        assert call_kwargs["project_id"] == "test-project-123"

    @pytest.mark.asyncio
    async def test_experience_returns_immediately(self, configured_env, mock_manager):
        """Verify return value includes instance ID prefix (fire-and-forget)."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        result = await experience_tool.ainvoke({
            "text": "Quick knowledge recording.",
        })

        # Return should include truncated instance ID
        assert "spawned-instance" in result or "..." in result
        # The full ID is not in the result (truncated)
        assert "spawned-instance-abc123" not in result

    @pytest.mark.asyncio
    async def test_experience_sends_correct_message(self, configured_env, mock_manager):
        """Verify the message sent to experiencer includes the knowledge text."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        await experience_tool.ainvoke({
            "text": "The project uses Python 3.11 and FastAPI.",
        })

        mock_manager.enqueue_message.assert_called_once()
        call_kwargs = mock_manager.enqueue_message.call_args.kwargs
        message = call_kwargs["message"]
        assert "Python 3.11" in message
        assert "FastAPI" in message

    @pytest.mark.asyncio
    async def test_experience_includes_project_in_message(self, configured_env, mock_manager):
        """Verify project ID is included in the message when available."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        await experience_tool.ainvoke({
            "text": "Important finding about the project.",
        })

        mock_manager.enqueue_message.assert_called_once()
        call_kwargs = mock_manager.enqueue_message.call_args.kwargs
        message = call_kwargs["message"]
        assert "Project: test-project-123" in message

    @pytest.mark.asyncio
    async def test_experience_uses_experiencer_agent(self, configured_env, mock_manager):
        """Verify experiencer agent is spawned."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        await experience_tool.ainvoke({"text": "Test"})

        mock_manager.spawn_instance.assert_called_once()
        call_kwargs = mock_manager.spawn_instance.call_args.kwargs
        assert call_kwargs["agent_id"] == "experiencer"
