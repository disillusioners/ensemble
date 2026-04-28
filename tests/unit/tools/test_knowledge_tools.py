"""Tests for knowledge management tools (daemon.tools.knowledge_tools).

Tests the explore and experience tools created by create_knowledge_tools()
factory function, including RAG configuration checks, agent invocation,
and fire-and-forget patterns.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.tools.knowledge_tools import (
    _enqueue_experiencer_job,
    _generate_idempotency_key,
    _parse_should_update_kb,
    _SHOULD_UPDATE_KB_PATTERN,
    create_knowledge_tools,
)


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


# =============================================================================
# Parse Should Update KB Tests
# =============================================================================


class TestParseShouldUpdateKb:
    """Tests for _parse_should_update_kb() flag parsing function."""

    def test_parse_should_update_kb_true(self):
        """Heading with Need Update KB: true returns True."""
        response = "Some response\n## Need Update KB: true\nMore text"
        assert _parse_should_update_kb(response) is True

    def test_parse_should_update_kb_false(self):
        """Heading with Need Update KB: false returns False."""
        response = "Some response\n## Need Update KB: false\nMore text"
        assert _parse_should_update_kb(response) is False

    def test_parse_should_update_kb_missing(self):
        """No heading in response returns False (default)."""
        response = "## Answer\nSome text\n## Confidence: HIGH"
        assert _parse_should_update_kb(response) is False

    def test_parse_should_update_kb_case_insensitive(self):
        """Flag parsing is case-insensitive."""
        assert _parse_should_update_kb("## Need Update KB: TRUE") is True
        assert _parse_should_update_kb("## Need Update KB: True") is True
        assert _parse_should_update_kb("## Need Update KB: TRUE") is True
        assert _parse_should_update_kb("## NEED UPDATE KB: TRUE") is True

    def test_parse_should_update_kb_malformed(self):
        """Malformed flag values return False."""
        response = "## Need Update KB: maybe"
        assert _parse_should_update_kb(response) is False

    def test_parse_should_update_kb_with_extra_whitespace(self):
        """Heading with extra whitespace/newlines still parses correctly."""
        response = "## Need Update KB: true  \nMore text"
        assert _parse_should_update_kb(response) is True

    def test_parse_should_update_kb_bold_true(self):
        """Bold formatting **true** parses correctly as True."""
        response = "## Need Update KB: **true**\nMore text"
        assert _parse_should_update_kb(response) is True

    def test_parse_should_update_kb_bold_false(self):
        """Bold formatting **false** parses correctly as False."""
        response = "## Need Update KB: **false**\nMore text"
        assert _parse_should_update_kb(response) is False

    def test_parse_should_update_kb_italic_true(self):
        """Italic formatting *true* parses correctly as True."""
        response = "## Need Update KB: *true*\nMore text"
        assert _parse_should_update_kb(response) is True

    def test_parse_should_update_kb_italic_false(self):
        """Italic formatting *false* parses correctly as False."""
        response = "## Need Update KB: *false*\nMore text"
        assert _parse_should_update_kb(response) is False

    def test_parse_should_update_kb_heading_stripped_from_response(self):
        """Heading is properly stripped from response text."""
        response = "Some response\n## Need Update KB: true\nMore text"
        stripped = _SHOULD_UPDATE_KB_PATTERN.sub("", response).strip()
        # Heading including newlines is removed
        assert "Need Update KB" not in stripped
        assert "Some response" in stripped
        assert "More text" in stripped

    def test_parse_should_update_kb_bold_heading_stripped(self):
        """Bold heading is stripped including bold markers."""
        response = "Some response\n## Need Update KB: **true**\nMore text"
        stripped = _SHOULD_UPDATE_KB_PATTERN.sub("", response).strip()
        assert "Need Update KB" not in stripped
        assert "**true**" not in stripped
        assert "Some response" in stripped
        assert "More text" in stripped

    def test_parse_should_update_kb_response_without_heading_unchanged(self):
        """Response without heading is returned unchanged."""
        response = "Some response without Need Update KB"
        stripped = _SHOULD_UPDATE_KB_PATTERN.sub("", response).strip()
        assert stripped == response

    def test_parse_should_update_kb_old_meta_format_returns_false(self):
        """Old META block format returns False (no longer supported)."""
        response = "Some response\n<META>\nshould_update_kb: true\n</META>\nMore text"
        assert _parse_should_update_kb(response) is False


# =============================================================================
# Idempotency Key Tests
# =============================================================================


class TestGenerateIdempotencyKey:
    """Tests for _generate_idempotency_key() function."""

    def test_idempotency_key_deterministic(self):
        """Same inputs produce the same key."""
        key1 = _generate_idempotency_key("What is the project?", "proj-123")
        key2 = _generate_idempotency_key("What is the project?", "proj-123")
        assert key1 == key2

    def test_idempotency_key_different_queries(self):
        """Different queries produce different keys."""
        key1 = _generate_idempotency_key("What is the project?", "proj-123")
        key2 = _generate_idempotency_key("What is the architecture?", "proj-123")
        assert key1 != key2

    def test_idempotency_key_different_projects(self):
        """Different projects produce different keys."""
        key1 = _generate_idempotency_key("What is the project?", "proj-123")
        key2 = _generate_idempotency_key("What is the project?", "proj-456")
        assert key1 != key2


# =============================================================================
# Explore Job Enqueue Integration Tests
# =============================================================================


@pytest.fixture
def mock_manager_with_job_queue(configured_env, mock_manager):
    """Create a mock manager with job queue service for explore tests."""
    # Set up instance metadata
    mock_instance_meta = MagicMock()
    mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
    mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

    # Set up job queue service
    mock_queue = MagicMock()
    mock_queue.queue_id = "system-parallel-queue-123"
    mock_queue_repo = MagicMock()
    mock_queue_repo.get_by_name = MagicMock(return_value=mock_queue)

    mock_job_service = MagicMock()
    mock_job_service._queue_repo = mock_queue_repo
    mock_job_service.enqueue = AsyncMock(return_value=MagicMock(job_id="job-123"))

    mock_manager._job_queue_service = mock_job_service

    return mock_manager


class TestExploreJobEnqueue:
    """Tests for explore() tool job enqueue behavior."""

    @pytest.mark.asyncio
    async def test_explore_strips_heading_from_response(self, configured_env, mock_manager_with_job_queue):
        """Response with Need Update KB heading is stripped before returning to caller."""
        explorer_response = (
            "## Answer\nFound important information.\n\n"
            "## Need Update KB: true"
        )

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=explorer_response):
            tools = create_knowledge_tools(mock_manager_with_job_queue, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            result = await explore_tool.ainvoke({"query": "What is X?"})

            # Heading should be stripped from result
            assert "Need Update KB" not in result
            assert "Found important information" in result

    @pytest.mark.asyncio
    async def test_explore_enqueues_job_when_flag_true(self, configured_env, mock_manager_with_job_queue):
        """Need Update KB: true + project_id causes job to be enqueued."""
        explorer_response = (
            "## Answer\nFound info from files.\n\n"
            "## Need Update KB: true"
        )

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=explorer_response):
            tools = create_knowledge_tools(mock_manager_with_job_queue, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({"query": "What is the architecture?"})

            # Allow fire-and-forget task to complete
            await asyncio.sleep(0.1)

            # Verify job was enqueued
            mock_manager_with_job_queue._job_queue_service.enqueue.assert_called_once()
            call_kwargs = mock_manager_with_job_queue._job_queue_service.enqueue.call_args.kwargs
            assert call_kwargs["agent_id"] == "experiencer"
            assert "What is the architecture?" in call_kwargs["message"]

    @pytest.mark.asyncio
    async def test_explore_skips_job_when_flag_false(self, configured_env, mock_manager_with_job_queue):
        """Need Update KB: false means no job is enqueued."""
        explorer_response = (
            "## Answer\nNo new knowledge found.\n\n"
            "## Need Update KB: false"
        )

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=explorer_response):
            tools = create_knowledge_tools(mock_manager_with_job_queue, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({"query": "What is X?"})

            await asyncio.sleep(0.1)

            # No job should have been enqueued
            mock_manager_with_job_queue._job_queue_service.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_skips_job_when_no_project_id(self, configured_env, mock_manager_with_job_queue):
        """Flag true but no project_id means no job is enqueued."""
        # Override instance metadata to return no project (empty dict)
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {}
        mock_manager_with_job_queue._instance_repository.get = MagicMock(
            return_value=mock_instance_meta
        )

        explorer_response = (
            "## Answer\nNew knowledge discovered.\n\n"
            "## Need Update KB: true"
        )

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=explorer_response):
            tools = create_knowledge_tools(mock_manager_with_job_queue, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({"query": "What is X?"})

            await asyncio.sleep(0.1)

            # Job should NOT be enqueued because project_id is None
            mock_manager_with_job_queue._job_queue_service.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_job_enqueue_failure_is_silent(self, configured_env, mock_manager_with_job_queue):
        """Job service raises exception - explore() still returns normally."""
        explorer_response = (
            "## Answer\nNew knowledge found.\n\n"
            "## Need Update KB: true"
        )

        # Make enqueue raise an exception
        mock_manager_with_job_queue._job_queue_service.enqueue = AsyncMock(
            side_effect=Exception("Database error")
        )

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=explorer_response):
            tools = create_knowledge_tools(mock_manager_with_job_queue, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            # This should not raise - failure should be silent
            result = await explore_tool.ainvoke({"query": "What is X?"})

            # Result should still be returned normally
            assert result is not None
            assert "New knowledge found" in result
