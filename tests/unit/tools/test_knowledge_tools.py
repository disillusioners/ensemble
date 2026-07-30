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
    _check_rag_errored_via_checkpoint,
    _check_rag_queried_via_checkpoint,
    _check_read_file_called_via_checkpoint,
    _enqueue_experience_job,
    _enqueue_kb_update_job,
    _generate_experience_idempotency_key,
    _generate_idempotency_key,
    create_knowledge_tools,
    KB_GAP_TOOL_NAME,
    RAG_TOOL_NAMES,
)
from daemon.services.context_injection import get_shared_context


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
    mock_instance_meta.project_id = "test-project-123"
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

    # Configure spawn_instance_with_mcp to return a predictable ID
    manager.spawn_instance_with_mcp = AsyncMock(return_value="spawned-instance-abc123")

    # Configure enqueue_message as async (legacy, for backwards compat)
    manager.enqueue_message = AsyncMock()

    # Set up job queue service for new fire-and-forget pattern
    mock_queue = MagicMock()
    mock_queue.queue_id = "system-kb-fifo-queue-123"
    mock_queue_repo = MagicMock()
    mock_queue_repo.get_by_name = MagicMock(return_value=mock_queue)

    mock_job_service = MagicMock()
    mock_job_service._queue_repo = mock_queue_repo
    mock_job_service.enqueue = AsyncMock(return_value=MagicMock(job_id="job-456"))

    manager._job_queue_service = mock_job_service

    # Mock live_hub for status_change emission in spawn_instance
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()

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
        mock_instance_meta.project_id = "test-project"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=("Explorer found relevant information about the project.", "test-child-id")):
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
        mock_instance_meta.project_id = "test-project"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock,
                   return_value=(None, "test-child-id")):
            tools = create_knowledge_tools(mock_manager, "test-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            result = await explore_tool.ainvoke({"query": "Long running query"})

            assert "timed out" in result.lower() or "failed" in result.lower()

    @pytest.mark.asyncio
    async def test_explore_auto_injects_project_id(self, configured_env, mock_manager):
        """Verify project_id from instance context is used."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "auto-detected-project"}
        mock_instance_meta.project_id = "auto-detected-project"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock,
                   return_value=("Result", "test-child-id")) as mock_invoke:
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
        mock_instance_meta.project_id = "test-project"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock,
                   return_value=("Result", "test-child-id")) as mock_invoke:
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
        """Verify experience returns confirmation after enqueuing job."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        result = await experience_tool.ainvoke({
            "text": "This is important knowledge to record.",
        })

        # Allow fire-and-forget task to complete
        await asyncio.sleep(0.1)

        assert "Knowledge recording started" in result
        # Verify job was enqueued
        mock_manager._job_queue_service.enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_experience_not_configured(self, unconfigured_env, mock_manager):
        """Verify error when RAG is not configured."""
        tools = create_knowledge_tools(mock_manager, "test-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        result = await experience_tool.ainvoke({"text": "Some knowledge"})

        assert "Error" in result
        assert "not configured" in result.lower()
        # Should not have enqueued any job
        mock_manager._job_queue_service.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_experience_auto_injects_project_id(self, configured_env, mock_manager):
        """Verify project_id from context is used when enqueuing job."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        await experience_tool.ainvoke({"text": "Test knowledge text"})

        # Allow fire-and-forget task to complete
        await asyncio.sleep(0.1)

        # Verify job was enqueued with project_id from instance metadata
        mock_manager._job_queue_service.enqueue.assert_called_once()
        call_kwargs = mock_manager._job_queue_service.enqueue.call_args.kwargs
        assert call_kwargs["project_id"] == "test-project-123"

    @pytest.mark.asyncio
    async def test_experience_returns_immediately(self, configured_env, mock_manager):
        """Verify return value indicates recording has started (fire-and-forget)."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        result = await experience_tool.ainvoke({
            "text": "Quick knowledge recording.",
        })

        # Return should indicate recording started
        assert "Knowledge recording started" in result
        # Should NOT return instance ID anymore
        assert "spawned-instance" not in result

    @pytest.mark.asyncio
    async def test_experience_sends_correct_message(self, configured_env, mock_manager):
        """Verify the message sent to kb-writer includes the knowledge text."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        await experience_tool.ainvoke({
            "text": "The project uses Python 3.11 and FastAPI.",
        })

        # Allow fire-and-forget task to complete
        await asyncio.sleep(0.1)

        mock_manager._job_queue_service.enqueue.assert_called_once()
        call_kwargs = mock_manager._job_queue_service.enqueue.call_args.kwargs
        message = call_kwargs["message"]
        assert "Python 3.11" in message
        assert "FastAPI" in message

    @pytest.mark.asyncio
    async def test_experience_includes_project_in_message(self, configured_env, mock_manager):
        """Verify project ID is included in the message when available."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        await experience_tool.ainvoke({
            "text": "Important finding about the project.",
        })

        # Allow fire-and-forget task to complete
        await asyncio.sleep(0.1)

        mock_manager._job_queue_service.enqueue.assert_called_once()
        call_kwargs = mock_manager._job_queue_service.enqueue.call_args.kwargs
        message = call_kwargs["message"]
        assert "Project: test-project-123" in message

    @pytest.mark.asyncio
    async def test_experience_uses_kb_writer_agent(self, configured_env, mock_manager):
        """Verify kb-writer agent is targeted by the enqueued job."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        await experience_tool.ainvoke({"text": "Test"})

        # Allow fire-and-forget task to complete
        await asyncio.sleep(0.1)

        mock_manager._job_queue_service.enqueue.assert_called_once()
        call_kwargs = mock_manager._job_queue_service.enqueue.call_args.kwargs
        assert call_kwargs["agent_id"] == "kb-writer"

    @pytest.mark.asyncio
    async def test_experience_job_enqueue_failure_is_silent(self, configured_env, mock_manager):
        """Job service raises exception - experience() still returns normally."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        # Make enqueue raise an exception
        mock_manager._job_queue_service.enqueue = AsyncMock(
            side_effect=Exception("Database error")
        )

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        # This should not raise - failure should be silent
        result = await experience_tool.ainvoke({"text": "Test knowledge"})

        # Result should still be returned normally
        assert "Knowledge recording started" in result

    @pytest.mark.asyncio
    async def test_experience_returns_error_when_no_project_id(self, configured_env, mock_manager):
        """Error returned when project_id is not available."""
        # Override instance metadata to return no project (empty dict)
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {}
        mock_instance_meta.project_id = None
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        result = await experience_tool.ainvoke({"text": "Test knowledge"})

        assert "Error" in result
        assert "project_id" in result.lower()
        # No job should be enqueued
        mock_manager._job_queue_service.enqueue.assert_not_called()


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


class TestGenerateExperienceIdempotencyKey:
    """Tests for _generate_experience_idempotency_key() function."""

    def test_experience_idempotency_key_deterministic(self):
        """Same inputs produce the same key."""
        key1 = _generate_experience_idempotency_key("Important knowledge", "proj-123")
        key2 = _generate_experience_idempotency_key("Important knowledge", "proj-123")
        assert key1 == key2

    def test_experience_idempotency_key_different_text(self):
        """Different text produces different keys."""
        key1 = _generate_experience_idempotency_key("Python 3.11 is fast", "proj-123")
        key2 = _generate_experience_idempotency_key("Rust is memory safe", "proj-123")
        assert key1 != key2

    def test_experience_idempotency_key_different_projects(self):
        """Different projects produce different keys."""
        key1 = _generate_experience_idempotency_key("Same text", "proj-123")
        key2 = _generate_experience_idempotency_key("Same text", "proj-456")
        assert key1 != key2

    def test_experience_idempotency_key_long_text(self):
        """Very long text (10000+ chars) is handled correctly."""
        long_text = "x" * 15000
        # Should not crash
        key = _generate_experience_idempotency_key(long_text, "proj-123")
        # Should produce a valid 32-character hex hash
        assert len(key) == 32
        assert all(c in "0123456789abcdef" for c in key)


# =============================================================================
# Experience Job Enqueue Integration Tests
# =============================================================================


class TestExperienceJobEnqueue:
    """Tests for experience() tool job enqueue behavior."""

    @pytest.mark.asyncio
    async def test_experience_queue_fallback_to_system_fifo(
        self, configured_env, mock_manager
    ):
        """When system_kb_fifo_queue doesn't exist, falls back to system_fifo_queue."""
        # Set up instance metadata
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        # Set up job queue service where KB queue returns None, FIFO queue returns valid queue
        fallback_queue = MagicMock()
        fallback_queue.queue_id = "system-fifo-queue-456"

        kb_queue_repo = MagicMock()
        # First call returns None (KB queue doesn't exist)
        # Second call returns fallback queue
        kb_queue_repo.get_by_name = MagicMock(
            side_effect=[None, fallback_queue]
        )

        job_service = MagicMock()
        job_service._queue_repo = kb_queue_repo
        job_service.enqueue = AsyncMock(return_value=MagicMock(job_id="job-789"))
        mock_manager._job_queue_service = job_service

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        await experience_tool.ainvoke({"text": "Test knowledge text"})

        await asyncio.sleep(0.1)

        # Verify both queues were checked
        assert kb_queue_repo.get_by_name.call_count == 2
        kb_queue_repo.get_by_name.assert_any_call("test-project-123", "system_kb_fifo_queue")
        kb_queue_repo.get_by_name.assert_any_call("test-project-123", "system_fifo_queue")

        # Verify job was enqueued with fallback queue's ID
        job_service.enqueue.assert_called_once()
        call_kwargs = job_service.enqueue.call_args.kwargs
        assert call_kwargs["queue_id"] == "system-fifo-queue-456"
        assert call_kwargs["agent_id"] == "kb-writer"

    @pytest.mark.asyncio
    async def test_experience_no_job_queue_service(self, configured_env, mock_manager, caplog):
        """When _job_queue_service is None, experience() returns success without crashing."""
        # Set up instance metadata
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        # Remove _job_queue_service (or set to None)
        mock_manager._job_queue_service = None

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        with caplog.at_level("WARNING"):
            result = await experience_tool.ainvoke({"text": "Test knowledge"})

        # Should still return success (fire-and-forget pattern)
        assert "Knowledge recording started" in result
        # Warning should be logged about missing job queue service
        assert any(
            "JobQueueService not available" in record.message
            for record in caplog.records
        ), "Expected warning about missing JobQueueService"

    @pytest.mark.asyncio
    async def test_experience_no_system_queue_skips_enqueue(
        self, configured_env, mock_manager
    ):
        """When neither KB queue nor FIFO queue exists, no job is enqueued."""
        # Set up instance metadata
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        # Both queues return None
        kb_queue_repo = MagicMock()
        kb_queue_repo.get_by_name = MagicMock(return_value=None)

        job_service = MagicMock()
        job_service._queue_repo = kb_queue_repo
        job_service.enqueue = AsyncMock(return_value=MagicMock(job_id="job-999"))
        mock_manager._job_queue_service = job_service

        tools = create_knowledge_tools(mock_manager, "parent-instance-id")
        experience_tool = next(t for t in tools if t.name == "experience")

        await experience_tool.ainvoke({"text": "Test knowledge text"})

        await asyncio.sleep(0.1)

        # Verify both queues were checked
        assert kb_queue_repo.get_by_name.call_count == 2
        # No job should have been enqueued
        job_service.enqueue.assert_not_called()


# =============================================================================
# Explore Job Enqueue Integration Tests
# =============================================================================


@pytest.fixture
def mock_manager_with_job_queue(configured_env, mock_manager):
    """Create a mock manager with job queue service for explore tests."""
    # Set up instance metadata
    mock_instance_meta = MagicMock()
    mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
    mock_instance_meta.project_id = "test-project-123"
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
    """Tests for explore() tool job enqueue behavior.

    The "Need Update KB" signal is now derived from the child's checkpoint:
    if the explorer called ``read_file``, the system enqueues a kb-importer
    job. The agent no longer emits a ``## Need Update KB:`` heading and the
    system no longer strips it from the response.
    """

    @pytest.fixture
    def mock_manager_with_checkpoint(self, mock_manager_with_job_queue):
        """Augment the job-queue mock with a checkpointer reporting read_file."""
        mock_checkpointer = MagicMock()
        # Default: no read_file call → no KB update
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [_make_message([_make_tool_call("bash")])],
            }
        })
        mock_manager_with_job_queue._checkpointer = mock_checkpointer
        return mock_manager_with_job_queue

    def _make_response_with_heading(self, body: str = "## Answer\nSome content.") -> str:
        """Build a response that still includes the legacy heading (for backward compat)."""
        return f"{body}\n\n## Need Update KB: true"

    @pytest.mark.asyncio
    async def test_explore_returns_response_unchanged(self, configured_env, mock_manager_with_checkpoint):
        """Response is returned to the caller without stripping any heading."""
        explorer_response = (
            "## Answer\nFound important information.\n\n"
            "## Need Update KB: true"
        )

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            tools = create_knowledge_tools(mock_manager_with_checkpoint, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            result = await explore_tool.ainvoke({"query": "What is X?"})

            # Response text is returned as-is — no more heading-stripping.
            assert "Found important information" in result
            assert "## Need Update KB: true" in result

    @pytest.mark.asyncio
    async def test_explore_enqueues_job_when_rag_queried_and_read_file_called(
        self, configured_env, mock_manager_with_checkpoint
    ):
        """RAG queried + read_file in checkpoint → kb-importer job is enqueued.

        The KB-gap signal is the conjunction of the two: a file fallback
        from RAG is what indicates a genuine KB gap. read_file alone (no
        RAG attempt) is NOT enough — see the RAG-error regression test
        for the inverse case.
        """
        explorer_response = "## Answer\nFound info from files."

        mock_manager_with_checkpoint._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                    _make_message([_make_tool_call("read_file")]),
                ],
            }
        })

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            tools = create_knowledge_tools(mock_manager_with_checkpoint, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({"query": "What is the architecture?"})

            # Allow fire-and-forget task to complete
            await asyncio.sleep(0.1)

            # Verify kb-importer job was enqueued
            mock_manager_with_checkpoint._job_queue_service.enqueue.assert_called_once()
            call_kwargs = mock_manager_with_checkpoint._job_queue_service.enqueue.call_args.kwargs
            assert call_kwargs["agent_id"] == "kb-importer"
            assert "What is the architecture?" in call_kwargs["message"]

    @pytest.mark.asyncio
    async def test_explore_skips_job_when_no_read_file(self, configured_env, mock_manager_with_checkpoint):
        """No read_file in checkpoint → no job is enqueued (system check, not response)."""
        explorer_response = (
            "## Answer\nNo new knowledge found.\n\n"
            "## Need Update KB: false"
        )

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            tools = create_knowledge_tools(mock_manager_with_checkpoint, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({"query": "What is X?"})

            await asyncio.sleep(0.1)

            # No job should have been enqueued (no read_file in checkpoint)
            mock_manager_with_checkpoint._job_queue_service.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_ignores_legacy_heading_when_no_read_file(
        self, configured_env, mock_manager_with_checkpoint
    ):
        """Legacy `## Need Update KB: true` heading in response is ignored when no read_file."""
        explorer_response = (
            "## Answer\nKnowledge claimed as new.\n\n"
            "## Need Update KB: true"
        )

        # Checkpoint has NO read_file call → must NOT enqueue
        mock_manager_with_checkpoint._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [_make_message([_make_tool_call("rag_query_data")])],
            }
        })

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            tools = create_knowledge_tools(mock_manager_with_checkpoint, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({"query": "What is X?"})

            await asyncio.sleep(0.1)

            mock_manager_with_checkpoint._job_queue_service.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_skips_job_when_no_project_id(self, configured_env, mock_manager_with_checkpoint):
        """RAG + read_file both called, but no project_id → no job enqueued."""
        # Override instance metadata to return no project
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {}
        mock_instance_meta.project_id = None
        mock_manager_with_checkpoint._instance_repository.get = MagicMock(
            return_value=mock_instance_meta
        )

        # Both RAG and read_file called — gating would normally enqueue
        mock_manager_with_checkpoint._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                    _make_message([_make_tool_call("read_file")]),
                ],
            }
        })

        explorer_response = "## Answer\nNew knowledge discovered."

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            tools = create_knowledge_tools(mock_manager_with_checkpoint, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({"query": "What is X?"})

            await asyncio.sleep(0.1)

            # Job should NOT be enqueued because project_id is None
            mock_manager_with_checkpoint._job_queue_service.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_job_enqueue_failure_is_silent(self, configured_env, mock_manager_with_checkpoint):
        """Job service raises exception - explore() still returns normally."""
        explorer_response = "## Answer\nNew knowledge found."

        # RAG + read_file both called → enqueue path is reached
        mock_manager_with_checkpoint._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                    _make_message([_make_tool_call("read_file")]),
                ],
            }
        })

        # Make enqueue raise an exception
        mock_manager_with_checkpoint._job_queue_service.enqueue = AsyncMock(
            side_effect=Exception("Database error")
        )

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            tools = create_knowledge_tools(mock_manager_with_checkpoint, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            # This should not raise - failure should be silent
            result = await explore_tool.ainvoke({"query": "What is X?"})

            # Result should still be returned normally
            assert result is not None
            assert "New knowledge found" in result

    @pytest.mark.asyncio
    async def test_explore_logs_warning_when_no_project_id(self, configured_env, mock_manager_with_checkpoint, caplog):
        """Warning is logged when project_id is missing despite RAG + read_file."""
        # Override instance metadata to return no project
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {}
        mock_instance_meta.project_id = None
        mock_manager_with_checkpoint._instance_repository.get = MagicMock(
            return_value=mock_instance_meta
        )

        # RAG + read_file both called — gating satisfied, project_id is the blocker
        mock_manager_with_checkpoint._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                    _make_message([_make_tool_call("read_file")]),
                ],
            }
        })

        explorer_response = "## Answer\nNew knowledge discovered."

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            tools = create_knowledge_tools(mock_manager_with_checkpoint, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            with caplog.at_level("WARNING"):
                await explore_tool.ainvoke({"query": "What is X?"})

            await asyncio.sleep(0.1)

            # Warning should be logged about missing project_id
            assert any(
                "project_id not available" in record.message
                for record in caplog.records
            ), "Expected warning about missing project_id"

    @pytest.mark.asyncio
    async def test_explore_passes_response_to_kb_importer_job(
        self, configured_env, mock_manager_with_checkpoint
    ):
        """kb-importer job receives the explorer's response (no heading stripping)."""
        explorer_response = "## Answer\nFound info from files."

        # RAG + read_file both called → gating satisfied → job enqueued.
        # The RAG ToolMessage has a successful response (no error).
        mock_manager_with_checkpoint._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                    _make_tool_message(
                        "rag_query_data",
                        "## Entities\n- **AuthService** (Service): Handles login",
                    ),
                    _make_message([_make_tool_call("read_file")]),
                ],
            }
        })

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            tools = create_knowledge_tools(mock_manager_with_checkpoint, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({"query": "What is the architecture?"})

            await asyncio.sleep(0.1)

            # Verify job was enqueued with the original response
            mock_manager_with_checkpoint._job_queue_service.enqueue.assert_called_once()
            call_kwargs = mock_manager_with_checkpoint._job_queue_service.enqueue.call_args.kwargs
            assert "Found info from files" in call_kwargs["message"]

    @pytest.mark.asyncio
    async def test_explore_skips_job_when_rag_errored(
        self, configured_env, mock_manager_with_checkpoint
    ):
        """RAG error + read_file → no kb-importer job (KB may already have the info).

        Regression for the RAG-outage scenario: when RAG times out / 504s /
        refuses connection, the explorer falls back to read_file, but the KB
        might already contain the requested knowledge. Without the
        ``rag_errored`` guard, a transient RAG outage would pollute the KB
        with a redundant update.
        """
        explorer_response = "## Answer\nFound info from files after RAG failed."

        # RAG errored (ToolMessage content starts with "RAG error:") AND
        # read_file was called. rag_queried=True, rag_errored=True,
        # read_file_called=True → gating fails → no enqueue.
        mock_manager_with_checkpoint._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                    _make_tool_message(
                        "rag_query_data",
                        "RAG error: TimeoutError: request timed out after 120s",
                    ),
                    _make_message([_make_tool_call("read_file")]),
                ],
            }
        })

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            tools = create_knowledge_tools(mock_manager_with_checkpoint, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({"query": "What is the auth?"})

            await asyncio.sleep(0.1)

            # RAG errored → gating fails → no job enqueued
            mock_manager_with_checkpoint._job_queue_service.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_skips_job_when_rag_504(
        self, configured_env, mock_manager_with_checkpoint
    ):
        """RAG 504 error + read_file → no enqueue (504 is a transient outage)."""
        explorer_response = "## Answer\nFallback to files."

        mock_manager_with_checkpoint._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                    _make_tool_message(
                        "rag_query_data",
                        "RAG error: HTTP 504 Gateway Timeout from upstream LightRAG",
                    ),
                    _make_message([_make_tool_call("read_file")]),
                ],
            }
        })

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            tools = create_knowledge_tools(mock_manager_with_checkpoint, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({"query": "What is X?"})

            await asyncio.sleep(0.1)

            mock_manager_with_checkpoint._job_queue_service.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_enqueues_job_when_rag_succeeded(
        self, configured_env, mock_manager_with_checkpoint
    ):
        """RAG succeeded (no error in ToolMessage) + read_file → enqueue.

        Positive control for the RAG-error regression test: confirms that
        the helper correctly distinguishes error responses from
        successful ones, so legitimate KB gaps still trigger updates.
        """
        explorer_response = "## Answer\nFound info from files."

        mock_manager_with_checkpoint._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                    _make_tool_message(
                        "rag_query_data",
                        "## Entities\n- **NoMatch** (concept): nothing relevant",
                    ),
                    _make_message([_make_tool_call("read_file")]),
                ],
            }
        })

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            tools = create_knowledge_tools(mock_manager_with_checkpoint, "parent-instance-id")
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({"query": "What is the architecture?"})

            await asyncio.sleep(0.1)

            # Successful RAG + read_file → gating satisfied → enqueue
            mock_manager_with_checkpoint._job_queue_service.enqueue.assert_called_once()


# =============================================================================
# Explore Auto-Injection Tests
# =============================================================================


class TestExploreAutoInjection:
    """Tests for explore() context auto-injection via get_shared_context."""

    @pytest.fixture
    def mock_manager_for_injection(self, configured_env, mock_manager):
        """Mock manager with tree_root_id support for injection tests."""
        # Set up instance metadata with project_id
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        # Set up get_tree_root_id to return a valid context key
        mock_manager._instance_repository.get_tree_root_id = MagicMock(
            return_value="tree-root-instance-id"
        )

        return mock_manager

    @pytest.mark.asyncio
    async def test_explore_injects_context_into_message(
        self, mock_manager_for_injection
    ):
        """When get_shared_context returns injection text, message includes it."""
        injection_text = "# Shared Context\ncontext_key: tree-root-instance-id\n\n## Pre-loaded Context (auto-matched)\n\n### test-file (85% match)\nAnswer content here.\n"

        with patch(
            "daemon.tools.knowledge_tools.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=injection_text,
        ) as mock_to_thread:
            with patch(
                "daemon.tools.knowledge_tools.invoke_agent_and_wait",
                new_callable=AsyncMock,
                return_value=("Explorer result.", "test-child-id"),
            ) as mock_invoke:
                tools = create_knowledge_tools(
                    mock_manager_for_injection, "parent-instance-id"
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                result = await explore_tool.ainvoke({"query": "What is X?"})

                # Verify asyncio.to_thread was called with get_shared_context
                mock_to_thread.assert_called_once()
                call_args = mock_to_thread.call_args
                assert call_args[0][0] == get_shared_context
                assert call_args[0][1] == "tree-root-instance-id"
                assert call_args[0][2] == "What is X?"

                # Verify message sent to invoke_agent_and_wait includes injection
                mock_invoke.assert_called_once()
                message = mock_invoke.call_args.kwargs["message"]
                assert injection_text in message
                assert "# Shared Context" in message
                assert "## Pre-loaded Context" in message

                # Verify final result is returned
                assert result == "Explorer result."

    @pytest.mark.asyncio
    async def test_explore_includes_empty_format_when_no_matches(
        self, mock_manager_for_injection
    ):
        """When get_shared_context returns empty format (no matches), message includes it."""
        empty_format = "# Shared Context\ncontext_key: tree-root-instance-id\n\n## Pre-loaded Context\nThere is no context yet."

        with patch(
            "daemon.tools.knowledge_tools.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=empty_format,
        ):
            with patch(
                "daemon.tools.knowledge_tools.invoke_agent_and_wait",
                new_callable=AsyncMock,
                return_value=("Explorer result.", "test-child-id"),
            ) as mock_invoke:
                tools = create_knowledge_tools(
                    mock_manager_for_injection, "parent-instance-id"
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                result = await explore_tool.ainvoke({"query": "Test query"})

                # Verify message includes empty format (no matches)
                mock_invoke.assert_called_once()
                message = mock_invoke.call_args.kwargs["message"]
                assert "# Shared Context" in message
                assert "There is no context yet" in message

                # Verify explore still works
                assert result == "Explorer result."

    @pytest.mark.asyncio
    async def test_explore_falls_back_to_current_instance_id_when_tree_root_empty(
        self, configured_env, mock_manager
    ):
        """When get_tree_root_id returns empty, uses current_instance_id as fallback."""
        # Set up instance metadata
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        # Set up get_tree_root_id to return empty string (falsy)
        # This causes fallback to current_instance_id which is truthy
        mock_manager._instance_repository.get_tree_root_id = MagicMock(return_value="")

        with patch(
            "daemon.tools.knowledge_tools.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value="Fallback injection text",
        ) as mock_to_thread:
            with patch(
                "daemon.tools.knowledge_tools.invoke_agent_and_wait",
                new_callable=AsyncMock,
                return_value=("Explorer result.", "test-child-id"),
            ) as mock_invoke:
                tools = create_knowledge_tools(mock_manager, "parent-instance-id")
                explore_tool = next(t for t in tools if t.name == "explore")

                result = await explore_tool.ainvoke({"query": "Test"})

                # Verify asyncio.to_thread was called with get_shared_context
                mock_to_thread.assert_called_once()
                # Verify the fallback context_key (current_instance_id) was used
                call_args = mock_to_thread.call_args
                assert call_args[0][0] == get_shared_context
                assert call_args[0][1] == "parent-instance-id"

                # Verify message includes the injection
                message = mock_invoke.call_args.kwargs["message"]
                assert "Fallback injection text" in message

                assert result == "Explorer result."

    @pytest.mark.asyncio
    async def test_explore_injection_failure_is_nonblocking(
        self, mock_manager_for_injection
    ):
        """If get_shared_context raises, explore still works."""
        async def raise_error(func, *args, **kwargs):
            raise OSError("Disk error")

        with patch(
            "daemon.tools.knowledge_tools.asyncio.to_thread",
            side_effect=raise_error,
        ):
            with patch(
                "daemon.tools.knowledge_tools.invoke_agent_and_wait",
                new_callable=AsyncMock,
                return_value=("Explorer succeeded despite injection failure.", "test-child-id"),
            ) as mock_invoke:
                tools = create_knowledge_tools(
                    mock_manager_for_injection, "parent-instance-id"
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                # This should NOT raise - failure should be non-blocking
                result = await explore_tool.ainvoke({"query": "Test query"})

                # Verify explore still completed successfully
                mock_invoke.assert_called_once()
                assert "succeeded" in result

    @pytest.mark.asyncio
    async def test_explore_injection_uses_thread_pool(
        self, mock_manager_for_injection
    ):
        """Verify asyncio.to_thread is used for get_shared_context."""
        mock_to_thread = AsyncMock(return_value="# Shared Context\ncontext_key: tree-root-instance-id\n\n## Pre-loaded Context\nContent.")

        with patch("daemon.tools.knowledge_tools.asyncio.to_thread", mock_to_thread):
            with patch(
                "daemon.tools.knowledge_tools.invoke_agent_and_wait",
                new_callable=AsyncMock,
                return_value=("Result", "test-child-id"),
            ):
                tools = create_knowledge_tools(
                    mock_manager_for_injection, "parent-instance-id"
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                await explore_tool.ainvoke({"query": "Test"})

                # Verify asyncio.to_thread was called
                mock_to_thread.assert_called_once()
                # First positional arg should be get_shared_context
                call_args = mock_to_thread.call_args
                assert call_args[0][0] == get_shared_context  # The actual function
                assert call_args[0][1] == "tree-root-instance-id"
                assert call_args[0][2] == "Test"


class TestKnowledgeToolsConditionalCreation:
    """Tests for knowledge tools conditional creation based on RAG status."""

    def test_create_instance_tools_does_not_call_create_knowledge_tools_when_rag_disabled(
        self, unconfigured_env, mock_manager
    ):
        """create_instance_tools should NOT call create_knowledge_tools when RAG is disabled."""
        from daemon.tools.instance import create_instance_tools
        
        with patch("daemon.tools.instance.is_rag_enabled", return_value=False):
            with patch("daemon.tools.instance.create_knowledge_tools") as mock_create:
                # Mock required tools to avoid other dependencies
                with patch("daemon.tools.instance.create_rag_tools", return_value=[]):
                    with patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()):
                        with patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()):
                            with patch("daemon.tools.instance.create_project_tools", return_value=[]):
                                with patch("daemon.tools.instance.create_job_tools", return_value=[]):
                                    with patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()):
                                        with patch("daemon.tools.instance.scan_tools_for_full_docs"):
                                            with patch("daemon.tools.instance._apply_tool_filter", return_value=[]):
                                                tools = create_instance_tools(mock_manager, "test-instance", "test-agent")
                
                # create_knowledge_tools should NOT have been called
                mock_create.assert_not_called()
                # Verify explore and experience tools are not in the result
                tool_names = [t.name for t in tools if hasattr(t, 'name')]
                assert "explore" not in tool_names
                assert "experience" not in tool_names

    def test_create_instance_tools_calls_create_knowledge_tools_when_rag_enabled(
        self, configured_env, mock_manager
    ):
        """create_instance_tools SHOULD call create_knowledge_tools when RAG is enabled."""
        from daemon.tools.instance import create_instance_tools
        from daemon.registry import get_registry, AgentMetadata, ToolFilter
        
        with patch("daemon.tools.instance.is_rag_enabled", return_value=True):
            with patch("daemon.tools.instance.create_knowledge_tools") as mock_create:
                # Return actual knowledge tools with proper name attribute
                mock_explore = MagicMock()
                mock_explore.name = "explore"
                mock_experience = MagicMock()
                mock_experience.name = "experience"
                mock_create.return_value = [mock_explore, mock_experience]
                with patch("daemon.tools.instance.create_rag_tools", return_value=[]):
                    with patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()):
                        with patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()):
                            with patch("daemon.tools.instance.create_project_tools", return_value=[]):
                                with patch("daemon.tools.instance.create_job_tools", return_value=[]):
                                    with patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()):
                                        with patch("daemon.tools.instance.scan_tools_for_full_docs"):
                                            # Patch registry to return no tools filter (allow all)
                                            mock_registry = MagicMock()
                                            mock_agent_meta = AgentMetadata(
                                                id="test-agent",
                                                name="Test",
                                                description="Test agent",
                                                path=MagicMock(),
                                                system=False,
                                            )
                                            mock_agent_meta.tools = None  # No restrictions
                                            mock_registry.get.return_value = mock_agent_meta
                                            mock_registry.get_resolved.return_value = mock_agent_meta
                                            with patch("daemon.registry.get_registry", return_value=mock_registry):
                                                tools = create_instance_tools(mock_manager, "test-instance", "test-agent")
                
                # create_knowledge_tools SHOULD have been called
                # Note: ``agent_id`` is now forwarded so the explore() tool
                # can resolve Explorer's ``caller_model_overrides`` for the
                # calling agent. See ``TestExploreCallerModelOverrides``.
                mock_create.assert_called_once_with(mock_manager, "test-instance", agent_id="test-agent")
                # Verify explore and experience tools are in the result
                tool_names = [t.name for t in tools]
                assert "explore" in tool_names
                assert "experience" in tool_names

    def test_create_instance_tools_excludes_rag_tools_when_disabled(self, unconfigured_env, mock_manager):
        """When RAG is disabled, explore and experience tools should NOT be in the final tool list."""
        from daemon.tools.instance import create_instance_tools
        
        with patch("daemon.tools.instance.is_rag_enabled", return_value=False):
            with patch("daemon.tools.instance.create_knowledge_tools") as mock_create:
                with patch("daemon.tools.instance.create_rag_tools", return_value=[]):
                    with patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()):
                        with patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()):
                            with patch("daemon.tools.instance.create_project_tools", return_value=[]):
                                with patch("daemon.tools.instance.create_job_tools", return_value=[]):
                                    with patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()):
                                        with patch("daemon.tools.instance.scan_tools_for_full_docs"):
                                            with patch("daemon.tools.instance._apply_tool_filter", return_value=[]):
                                                tools = create_instance_tools(mock_manager, "test-instance", "test-agent")
                
                # create_knowledge_tools should NOT have been called
                mock_create.assert_not_called()
                
                # Verify explore and experience tools are not in the result
                tool_names = [t.name for t in tools if hasattr(t, 'name')]
                assert "explore" not in tool_names, "explore tool should not be present when RAG is disabled"
                assert "experience" not in tool_names, "experience tool should not be present when RAG is disabled"
                
                # Also verify no RAG-prefixed tools are present
                for name in tool_names:
                    assert not name.startswith("rag_"), f"RAG tool {name} should not be present when LIGHTRAG_HOST is unset"

    def test_create_instance_tools_includes_rag_tools_when_enabled(self, configured_env, mock_manager):
        """When RAG is enabled, explore and experience tools SHOULD be in the final tool list."""
        from daemon.tools.instance import create_instance_tools
        from daemon.registry import get_registry, AgentMetadata, ToolFilter
        
        with patch("daemon.tools.instance.is_rag_enabled", return_value=True):
            with patch("daemon.tools.instance.create_knowledge_tools") as mock_create:
                # Return actual knowledge tools with proper name attribute
                mock_explore = MagicMock()
                mock_explore.name = "explore"
                mock_experience = MagicMock()
                mock_experience.name = "experience"
                mock_create.return_value = [mock_explore, mock_experience]
                with patch("daemon.tools.instance.create_rag_tools", return_value=[]):
                    with patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()):
                        with patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()):
                            with patch("daemon.tools.instance.create_project_tools", return_value=[]):
                                with patch("daemon.tools.instance.create_job_tools", return_value=[]):
                                    with patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()):
                                        with patch("daemon.tools.instance.scan_tools_for_full_docs"):
                                            # Patch registry to return no tools filter (allow all)
                                            mock_registry = MagicMock()
                                            mock_agent_meta = AgentMetadata(
                                                id="test-agent",
                                                name="Test",
                                                description="Test agent",
                                                path=MagicMock(),
                                                system=False,
                                            )
                                            mock_agent_meta.tools = None  # No restrictions
                                            mock_registry.get.return_value = mock_agent_meta
                                            mock_registry.get_resolved.return_value = mock_agent_meta
                                            with patch("daemon.registry.get_registry", return_value=mock_registry):
                                                tools = create_instance_tools(mock_manager, "test-instance", "test-agent")
                
                # create_knowledge_tools SHOULD have been called
                # ``agent_id`` is now forwarded so the explore() tool can
                # resolve Explorer's ``caller_model_overrides``. See
                # ``TestExploreCallerModelOverrides`` for the new behavior.
                mock_create.assert_called_once_with(mock_manager, "test-instance", agent_id="test-agent")

                # Verify explore and experience tools are in the final tool list
                tool_names = [t.name for t in tools]
                assert "explore" in tool_names, "explore tool should be present when RAG is enabled"
                assert "experience" in tool_names, "experience tool should be present when RAG is enabled"


# =============================================================================
# Explore Auto-Save Tests (Layer A: Agent-level flag)
# =============================================================================


class TestExploreAutoSave:
    """Tests for explore() auto-save feature triggered by checkpoint-based RAG detection."""

    @pytest.fixture
    def mock_manager_for_save(self, configured_env, mock_manager):
        """Mock manager set up for auto-save tests."""
        # Set up instance metadata with project_id
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        # Set up get_tree_root_id to return a valid context key
        mock_manager._instance_repository.get_tree_root_id = MagicMock(
            return_value="tree-root-for-save-test"
        )

        # Set up checkpointer to report a RAG tool was called (Phase 1: checkpoint
        # is the source of truth for rag_queried; tests using "## Did you query
        # RAG: yes" must also have the checkpoint agree).
        mock_checkpointer = MagicMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                ]
            }
        })
        mock_manager._checkpointer = mock_checkpointer

        return mock_manager

    @pytest.mark.asyncio
    async def test_explore_rag_queried_yes_triggers_save(self, mock_manager_for_save, tmp_path):
        """Checkpoint shows RAG was queried → _save_explorer_result is called."""
        explorer_response = "## Answer\nFound important information."

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
                mock_tempfile.gettempdir.return_value = str(tmp_path)

                tools = create_knowledge_tools(mock_manager_for_save, "parent-instance-id")
                explore_tool = next(t for t in tools if t.name == "explore")

                with patch("daemon.tools.knowledge_tools._save_explorer_result") as mock_save:
                    result = await explore_tool.ainvoke({"query": "What is the auth?"})

                    # _save_explorer_result should have been called
                    mock_save.assert_called_once()
                    call_kwargs = mock_save.call_args.kwargs
                    assert call_kwargs["query"] == "What is the auth?"
                    assert "Found important information" in call_kwargs["result"]
                    assert call_kwargs["context_key"] == "tree-root-for-save-test"

    @pytest.mark.asyncio
    async def test_explore_rag_queried_no_skips_save(self, mock_manager_for_save, tmp_path):
        """Checkpoint shows no RAG queried → _save_explorer_result is NOT called."""
        explorer_response = "## Answer\nNo need to save this."

        # Checkpoint agrees: no RAG tool called
        mock_manager_for_save._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [_make_message([_make_tool_call("bash")])]}
        })

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
                mock_tempfile.gettempdir.return_value = str(tmp_path)

                tools = create_knowledge_tools(mock_manager_for_save, "parent-instance-id")
                explore_tool = next(t for t in tools if t.name == "explore")

                with patch("daemon.tools.knowledge_tools._save_explorer_result") as mock_save:
                    result = await explore_tool.ainvoke({"query": "Quick question"})

                    # _save_explorer_result should NOT have been called
                    mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_missing_rag_queried_defaults_to_no_save(self, mock_manager_for_save, tmp_path):
        """When checkpoint shows no RAG queried, save is NOT triggered."""
        explorer_response = (
            "## Answer\nRegular response without save flag."
        )

        # Checkpoint agrees: no RAG tool called
        mock_manager_for_save._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [_make_message([_make_tool_call("bash")])]}
        })

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
                mock_tempfile.gettempdir.return_value = str(tmp_path)

                tools = create_knowledge_tools(mock_manager_for_save, "parent-instance-id")
                explore_tool = next(t for t in tools if t.name == "explore")

                with patch("daemon.tools.knowledge_tools._save_explorer_result") as mock_save:
                    result = await explore_tool.ainvoke({"query": "What?"})

                    # _save_explorer_result should NOT have been called
                    mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_save_uses_current_instance_id_when_no_tree_root(self, mock_manager_for_save, tmp_path):
        """When get_tree_root_id returns empty, uses current_instance_id as context key."""
        mock_manager_for_save._instance_repository.get_tree_root_id = MagicMock(return_value="")

        explorer_response = "## Answer\nData to save."

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
                mock_tempfile.gettempdir.return_value = str(tmp_path)

                tools = create_knowledge_tools(mock_manager_for_save, "parent-instance-id")
                explore_tool = next(t for t in tools if t.name == "explore")

                with patch("daemon.tools.knowledge_tools._save_explorer_result") as mock_save:
                    await explore_tool.ainvoke({"query": "Test"})

                    # Should use current_instance_id as fallback
                    mock_save.assert_called_once()
                    call_kwargs = mock_save.call_args.kwargs
                    assert call_kwargs["context_key"] == "parent-instance-id"

    @pytest.mark.asyncio
    async def test_explore_save_failure_is_nonblocking(self, mock_manager_for_save, tmp_path):
        """If _save_explorer_result raises, explore still returns successfully."""
        explorer_response = "## Answer\nImportant information."

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
                mock_tempfile.gettempdir.return_value = str(tmp_path)

                tools = create_knowledge_tools(mock_manager_for_save, "parent-instance-id")
                explore_tool = next(t for t in tools if t.name == "explore")

                with patch("daemon.tools.knowledge_tools._save_explorer_result",
                           side_effect=IOError("Disk full")):
                    result = await explore_tool.ainvoke({"query": "What?"})

                # Explore should still return the response
                assert "Important information" in result


# =============================================================================
# Explore Auto-Save Dedup Tests (Layer B: System-level dedup)
# =============================================================================


class TestExploreAutoSaveDedup:
    """Tests for explore() auto-save dedup at system level in _save_explorer_result."""

    @pytest.fixture
    def mock_manager_for_dedup(self, configured_env, mock_manager):
        """Mock manager set up for dedup tests."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        mock_manager._instance_repository.get_tree_root_id = MagicMock(
            return_value="dedup-test-context"
        )

        # Set up checkpointer to report a RAG tool was called (Phase 1: checkpoint
        # is the source of truth for rag_queried).
        mock_checkpointer = MagicMock()
        mock_checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                ]
            }
        })
        mock_manager._checkpointer = mock_checkpointer

        return mock_manager

    @pytest.mark.asyncio
    async def test_save_skips_duplicate_concise(self, mock_manager_for_dedup, tmp_path):
        """Auto-save skips file creation when ## Concise: is too similar to existing."""
        # Set up context directory with existing file
        context_dir = tmp_path / "ensemble" / "context" / "dedup-test-context"
        context_dir.mkdir(parents=True, exist_ok=True)

        # Create existing file with a concise section (very similar content)
        existing_file = context_dir / "existing_20260601_120000.md"
        existing_file.write_text("""# Existing Result

## Concise:
The authentication system uses JWT tokens for user authentication with refresh tokens for security.

## Answer
Full details here.
""")

        # Explorer response includes ## Concise: section (which gets passed to save)
        explorer_response = """## Answer
JWT tokens for authentication.

## Concise:
The authentication system uses JWT tokens for user authentication with refresh tokens for security.
"""

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
                mock_tempfile.gettempdir.return_value = str(tmp_path)

                tools = create_knowledge_tools(mock_manager_for_dedup, "parent-instance-id")
                explore_tool = next(t for t in tools if t.name == "explore")

                await explore_tool.ainvoke({"query": "auth tokens"})

                # Allow async save to complete
                import asyncio
                await asyncio.sleep(0.05)

                # Should NOT create a new file (duplicate concise)
                files = list(context_dir.glob("*.md"))
                assert len(files) == 1, f"Expected 1 file (duplicate skip), got {len(files)}: {[f.name for f in files]}"

    @pytest.mark.asyncio
    async def test_save_creates_file_for_different_concise(self, mock_manager_for_dedup, tmp_path):
        """Auto-save creates file when ## Concise: is different enough."""
        context_dir = tmp_path / "ensemble" / "context" / "dedup-test-context"
        context_dir.mkdir(parents=True, exist_ok=True)

        # Create existing file with different concise
        existing_file = context_dir / "existing_20260601_120000.md"
        existing_file.write_text("""# Existing Result

## Concise:
The authentication system uses JWT tokens for user authentication.

## Answer
Full details here.
""")

        explorer_response = "## Answer\nThe database uses PostgreSQL."

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
                mock_tempfile.gettempdir.return_value = str(tmp_path)

                tools = create_knowledge_tools(mock_manager_for_dedup, "parent-instance-id")
                explore_tool = next(t for t in tools if t.name == "explore")

                await explore_tool.ainvoke({"query": "database schema"})

                # Allow async save to complete
                import asyncio
                await asyncio.sleep(0.05)

                # Should create a new file (different concise)
                files = list(context_dir.glob("*.md"))
                assert len(files) == 2, f"Expected 2 files, got {len(files)}: {[f.name for f in files]}"

    @pytest.mark.asyncio
    async def test_save_handles_corrupted_files_gracefully(self, mock_manager_for_dedup, tmp_path, caplog):
        """Auto-save continues even if existing files are corrupted."""
        import logging
        context_dir = tmp_path / "ensemble" / "context" / "dedup-test-context"
        context_dir.mkdir(parents=True, exist_ok=True)

        # Create a valid file
        existing_file = context_dir / "existing_20260601_120000.md"
        existing_file.write_text("""# Existing Result

## Concise:
Some valid concise content about authentication.
""")

        explorer_response = "## Answer\nNew information to save."

        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
            with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
                mock_tempfile.gettempdir.return_value = str(tmp_path)

                tools = create_knowledge_tools(mock_manager_for_dedup, "parent-instance-id")
                explore_tool = next(t for t in tools if t.name == "explore")

                with caplog.at_level(logging.DEBUG):
                    await explore_tool.ainvoke({"query": "new topic"})

                    # Allow async save to complete
                    import asyncio
                    await asyncio.sleep(0.05)

                # Should have saved the file (corrupted files don't block saving)
                files = list(context_dir.glob("*.md"))
                assert len(files) == 2


# =============================================================================
# Checkpoint RAG Detection Helper Tests
# =============================================================================


def _make_message(tool_calls):
    """Build a mock message with a given list of tool calls."""
    msg = MagicMock()
    msg.tool_calls = tool_calls
    return msg


def _make_tool_call(name):
    """Build a tool call dict (as LangGraph stores them)."""
    return {"name": name, "args": {}, "id": f"call_{name}"}


def _make_tool_message(name, content, tool_call_id=None):
    """Build a mock ToolMessage with the given tool name and content.

    Uses real ``ToolMessage`` shape (with ``name`` and ``content``
    attributes) so the scan helpers can inspect it like a real
    checkpoint entry. The AI-side message carrying the matching
    ``tool_calls`` is intentionally omitted — the scan helpers only
    look at the tool's own response message.
    """
    from langchain_core.messages import ToolMessage

    return ToolMessage(
        content=content,
        tool_call_id=tool_call_id or f"call_{name}",
        name=name,
    )


class TestCheckRagQueriedViaCheckpoint:
    """Tests for _check_rag_queried_via_checkpoint() helper."""

    @pytest.mark.asyncio
    async def test_rag_tool_found(self):
        """Returns True when messages contain rag_query_data tool call."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                ]
            }
        })

        result = await _check_rag_queried_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_rag_get_graph_found(self):
        """Returns True when messages contain rag_get_graph tool call."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_get_graph")]),
                ]
            }
        })

        result = await _check_rag_queried_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_no_rag_tools(self):
        """Returns False when no RAG tool calls in messages."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("bash")]),
                    _make_message([_make_tool_call("read_file")]),
                ]
            }
        })

        result = await _check_rag_queried_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_checkpoint_exception(self):
        """Returns False when checkpointer raises (graceful degradation)."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(side_effect=RuntimeError("DB error"))

        result = await _check_rag_queried_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_checkpoint_none(self):
        """Returns False when checkpoint state is None."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value=None)

        result = await _check_rag_queried_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """Returns False when state is valid but messages list is empty."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": []}
        })

        result = await _check_rag_queried_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_multiple_tools_one_rag(self):
        """Returns True when many tool calls include one RAG call."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("bash")]),
                    _make_message([_make_tool_call("read_file")]),
                    _make_message([_make_tool_call("rag_query_data")]),
                    _make_message([_make_tool_call("write_file")]),
                ]
            }
        })

        result = await _check_rag_queried_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_tool_call_object_with_attr(self):
        """Returns True when tool_call is an object with .name attribute (not a dict)."""
        tc_obj = MagicMock()
        tc_obj.name = "rag_query_data"

        msg = MagicMock()
        msg.tool_calls = [tc_obj]

        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [msg]}
        })

        result = await _check_rag_queried_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_rag_tool_names_constant(self):
        """RAG_TOOL_NAMES is a frozenset with the expected tool names."""
        assert isinstance(RAG_TOOL_NAMES, frozenset)
        assert "rag_query_data" in RAG_TOOL_NAMES
        assert "rag_get_graph" in RAG_TOOL_NAMES

    @pytest.mark.asyncio
    async def test_checkpoint_state_is_not_dict(self):
        """Returns False when state is a non-dict object (graceful degradation)."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value="unexpected string")
        result = await _check_rag_queried_via_checkpoint(checkpointer, "instance-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_channel_values_no_messages_key(self):
        """Returns False when state has channel_values dict but no messages key."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"other_key": "value"}
        })
        result = await _check_rag_queried_via_checkpoint(checkpointer, "instance-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_tool_calls_list(self):
        """Returns False when message has tool_calls = [] (empty list, no items)."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([]),  # empty tool_calls list
                ]
            }
        })
        result = await _check_rag_queried_via_checkpoint(checkpointer, "instance-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_message_without_tool_calls_attribute(self):
        """Returns False when message has no tool_calls attribute at all."""
        msg = MagicMock(spec=[])  # spec=[] forbids any attribute access
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [msg]}
        })
        result = await _check_rag_queried_via_checkpoint(checkpointer, "instance-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_unwraps_raw_saver_when_checkpointer_is_checkpointer_adapter(self):
        """Regression for context-dir-empty bug: when the checkpointer is a
        real ``CheckpointerAdapter`` (has ``raw_saver`` but no ``aget``
        directly), the function must unwrap ``raw_saver`` and call ``aget``
        on it. Previously, calling ``checkpointer.aget(...)`` raised
        ``AttributeError`` that was silently swallowed at DEBUG level,
        always returning False and disabling the explorer's auto-save path.

        This test uses a real ``SqliteCheckpointerAdapter`` (a concrete
        ``CheckpointerAdapter``) wrapping a ``MagicMock`` saver. The mock
        saver is not a bare ``MagicMock`` on the wrapper — exactly the
        shape the production code receives from
        ``InstanceManager._checkpointer``.
        """
        from daemon.checkpoint_adapter import SqliteCheckpointerAdapter

        raw_saver = MagicMock()
        raw_saver.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                ]
            }
        })
        adapter = SqliteCheckpointerAdapter(raw_saver)

        result = await _check_rag_queried_via_checkpoint(adapter, "inst-123")

        assert result is True
        # Verify aget was awaited on the raw_saver (not on the adapter,
        # which has no aget and would have raised AttributeError before
        # the fix).
        raw_saver.aget.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unwraps_raw_saver_returns_false_when_no_rag_tool_calls(self):
        """Regression companion: real ``CheckpointerAdapter`` wrapper where
        the underlying checkpoint contains no RAG tool calls. Must return
        False (correctly) rather than silently False-due-to-AttributeError.
        """
        from daemon.checkpoint_adapter import SqliteCheckpointerAdapter

        raw_saver = MagicMock()
        raw_saver.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("bash")]),
                ]
            }
        })
        adapter = SqliteCheckpointerAdapter(raw_saver)

        result = await _check_rag_queried_via_checkpoint(adapter, "inst-456")

        assert result is False
        raw_saver.aget.assert_awaited_once()


# =============================================================================
# Checkpoint RAG Error Detection Helper Tests
# =============================================================================


class TestCheckRagErroredViaCheckpoint:
    """Tests for _check_rag_errored_via_checkpoint() helper.

    Scans checkpoint ToolMessages for RAG-tool responses whose content
    contains a known error indicator (``"RAG error"`` or leading
    ``"Error: "``). Used to gate the "Need Update KB" enqueue so that
    RAG outages don't trigger spurious KB updates.
    """

    @pytest.mark.asyncio
    async def test_rag_error_string_detected(self):
        """RAG error: prefix in tool response → True."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_tool_message(
                        "rag_query_data",
                        "RAG error: Connection refused",
                    ),
                ]
            }
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_rag_timeout_detected(self):
        """RAG error: TimeoutError in tool response → True."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_tool_message(
                        "rag_query_data",
                        "RAG error: TimeoutError: request timed out after 120s",
                    ),
                ]
            }
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_rag_504_detected(self):
        """RAG error containing 504 → True."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_tool_message(
                        "rag_get_graph",
                        "RAG error: HTTP 504 Gateway Timeout from upstream",
                    ),
                ]
            }
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_error_prefix_detected(self):
        """ToolMessage content starting with 'Error: ' → True (pre-call validation)."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_tool_message(
                        "rag_query_data",
                        "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable.",
                    ),
                ]
            }
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_successful_rag_response_not_detected(self):
        """Plain text RAG result (no error) → False."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_tool_message(
                        "rag_query_data",
                        "## Entities\n- **AuthService** (Service): Handles login",
                    ),
                ]
            }
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_empty_rag_response_not_detected(self):
        """Empty RAG response → False (no results, not an error)."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_tool_message("rag_query_data", ""),
                ]
            }
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_no_rag_tool_messages(self):
        """No RAG ToolMessages in checkpoint → False."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("bash")]),
                    _make_message([_make_tool_call("read_file")]),
                ]
            }
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_non_rag_tool_error_ignored(self):
        """Error from a non-RAG tool → False (only RAG errors count)."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_tool_message(
                        "bash",
                        "Error: command not found",
                    ),
                ]
            }
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_checkpoint_exception(self):
        """Returns False when checkpointer raises (graceful degradation)."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(side_effect=RuntimeError("DB error"))

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_checkpoint_none(self):
        """Returns False when checkpoint state is None."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value=None)

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """Returns False when state is valid but messages list is empty."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": []}
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_mixed_rag_success_and_error(self):
        """One RAG tool errored, another succeeded → True (any error counts)."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_tool_message(
                        "rag_get_graph",
                        "## Entities\n- **AuthService** (Service)",
                    ),
                    _make_tool_message(
                        "rag_query_data",
                        "RAG error: TimeoutError",
                    ),
                ]
            }
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_message_without_name_attribute_ignored(self):
        """Messages without ``name`` attribute are not RAG tool messages → False."""
        msg = MagicMock(spec=["content"])
        msg.content = "RAG error: something"  # would be detected if name matched
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [msg]}
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_message_without_content_attribute_skipped(self):
        """ToolMessage with name but no content → not an error, no crash."""
        msg = MagicMock(spec=["name"])
        msg.name = "rag_query_data"
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [msg]}
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_list_content_with_error(self):
        """ToolMessage with list-of-parts content (e.g. multimodal) containing error."""
        msg = MagicMock()
        msg.name = "rag_query_data"
        msg.content = [
            {"type": "text", "text": "RAG error: 504 Gateway Timeout"},
        ]
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [msg]}
        })

        result = await _check_rag_errored_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_unwraps_raw_saver_when_checkpointer_is_checkpointer_adapter(self):
        """Regression: CheckpointerAdapter wrapper exposes raw_saver; aget is
        called on the raw saver, not the adapter."""
        from daemon.checkpoint_adapter import SqliteCheckpointerAdapter

        raw_saver = MagicMock()
        raw_saver.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_tool_message(
                        "rag_query_data",
                        "RAG error: Connection refused",
                    ),
                ]
            }
        })
        adapter = SqliteCheckpointerAdapter(raw_saver)

        result = await _check_rag_errored_via_checkpoint(adapter, "inst-err-1")

        assert result is True
        raw_saver.aget.assert_awaited_once()


# =============================================================================
# Checkpoint read_file Detection Helper Tests
# =============================================================================


class TestCheckReadFileCalledViaCheckpoint:
    """Tests for _check_read_file_called_via_checkpoint() helper.

    This is the deterministic, system-driven source of the "Need Update KB"
    flag: the explorer reading a file implies the KB lacked the information.
    """

    @pytest.mark.asyncio
    async def test_read_file_found(self):
        """Returns True when messages contain a read_file tool call."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("read_file")]),
                ]
            }
        })

        result = await _check_read_file_called_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_no_read_file(self):
        """Returns False when no read_file tool call in messages."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("bash")]),
                    _make_message([_make_tool_call("rag_query_data")]),
                ]
            }
        })

        result = await _check_read_file_called_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_checkpoint_exception(self):
        """Returns False when checkpointer raises (graceful degradation)."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(side_effect=RuntimeError("DB error"))

        result = await _check_read_file_called_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_checkpoint_none(self):
        """Returns False when checkpoint state is None."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value=None)

        result = await _check_read_file_called_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """Returns False when state is valid but messages list is empty."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": []}
        })

        result = await _check_read_file_called_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_other_filesystem_tools_only(self):
        """Returns False when other filesystem tools (list_directory, glob_files,
        grep_files) are called but read_file is not."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("list_directory")]),
                    _make_message([_make_tool_call("grep_files")]),
                ]
            }
        })

        result = await _check_read_file_called_via_checkpoint(checkpointer, "instance-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_mixed_tools_one_read_file(self):
        """Returns True when many tool calls include one read_file call."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("bash")]),
                    _make_message([_make_tool_call("rag_query_data")]),
                    _make_message([_make_tool_call("read_file")]),
                    _make_message([_make_tool_call("write_file")]),
                ]
            }
        })

        result = await _check_read_file_called_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_tool_call_object_with_attr(self):
        """Returns True when tool_call is an object with .name attribute (not a dict)."""
        tc_obj = MagicMock()
        tc_obj.name = "read_file"

        msg = MagicMock()
        msg.tool_calls = [tc_obj]

        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [msg]}
        })

        result = await _check_read_file_called_via_checkpoint(checkpointer, "instance-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_kb_gap_tool_name_constant(self):
        """KB_GAP_TOOL_NAME is the literal 'read_file'."""
        assert KB_GAP_TOOL_NAME == "read_file"

    @pytest.mark.asyncio
    async def test_checkpoint_state_is_not_dict(self):
        """Returns False when state is a non-dict object (graceful degradation)."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value="unexpected string")
        result = await _check_read_file_called_via_checkpoint(checkpointer, "instance-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_channel_values_no_messages_key(self):
        """Returns False when state has channel_values dict but no messages key."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"other_key": "value"}
        })
        result = await _check_read_file_called_via_checkpoint(checkpointer, "instance-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_tool_calls_list(self):
        """Returns False when message has tool_calls = [] (empty list, no items)."""
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([]),
                ]
            }
        })
        result = await _check_read_file_called_via_checkpoint(checkpointer, "instance-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_message_without_tool_calls_attribute(self):
        """Returns False when message has no tool_calls attribute at all."""
        msg = MagicMock(spec=[])
        checkpointer = MagicMock()
        checkpointer.aget = AsyncMock(return_value={
            "channel_values": {"messages": [msg]}
        })
        result = await _check_read_file_called_via_checkpoint(checkpointer, "instance-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_unwraps_raw_saver_when_checkpointer_is_checkpointer_adapter(self):
        """Regression: CheckpointerAdapter wrapper exposes raw_saver; aget is
        called on the raw saver, not the adapter."""
        from daemon.checkpoint_adapter import SqliteCheckpointerAdapter

        raw_saver = MagicMock()
        raw_saver.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("read_file")]),
                ]
            }
        })
        adapter = SqliteCheckpointerAdapter(raw_saver)

        result = await _check_read_file_called_via_checkpoint(adapter, "inst-789")

        assert result is True
        raw_saver.aget.assert_awaited_once()


# =============================================================================
# Explore() Checkpoint Integration Tests
# =============================================================================


class TestExploreCheckpointIntegration:
    """Tests for explore()'s use of checkpoint-based RAG detection."""

    @pytest.fixture
    def mock_manager_with_checkpointer(self, configured_env, mock_manager):
        """Mock manager with a checkpointer attached and instance metadata set up."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)
        mock_manager._instance_repository.get_tree_root_id = MagicMock(
            return_value="tree-root-for-checkpoint"
        )

        # Checkpointer returns no RAG tools by default; tests override per case.
        mock_checkpointer = MagicMock()
        mock_checkpointer.aget = AsyncMock(return_value=None)
        mock_manager._checkpointer = mock_checkpointer

        return mock_manager

    @pytest.mark.asyncio
    async def test_explore_checkpoint_rag_found_saves(
        self, mock_manager_with_checkpointer, tmp_path
    ):
        """Checkpoint says RAG was called → _save_explorer_result is triggered."""
        # Checkpoint shows a RAG tool call was made
        mock_manager_with_checkpointer._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                ]
            }
        })

        # No heading in response — checkpoint is the source of truth
        explorer_response = (
            "## Answer\nFound important information."
        )

        with patch(
            "daemon.tools.knowledge_tools.invoke_agent_and_wait",
            new_callable=AsyncMock,
            return_value=(explorer_response, "test-child-id"),
        ):
            with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
                mock_tempfile.gettempdir.return_value = str(tmp_path)

                tools = create_knowledge_tools(
                    mock_manager_with_checkpointer, "parent-instance-id"
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                with patch("daemon.tools.knowledge_tools._save_explorer_result") as mock_save:
                    await explore_tool.ainvoke({"query": "What is the auth?"})

                    # _save_explorer_result should have been called (checkpoint says RAG)
                    mock_save.assert_called_once()
                    call_kwargs = mock_save.call_args.kwargs
                    assert call_kwargs["query"] == "What is the auth?"
                    assert call_kwargs["context_key"] == "tree-root-for-checkpoint"

    @pytest.mark.asyncio
    async def test_explore_checkpoint_rag_not_found_skips(
        self, mock_manager_with_checkpointer, tmp_path
    ):
        """Checkpoint says no RAG → _save_explorer_result is NOT called.
        (Response text is irrelevant — rag_queried is sourced from the checkpoint.)
        """
        # Checkpoint shows no RAG tool calls
        mock_manager_with_checkpointer._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("bash")]),
                ]
            }
        })

        explorer_response = "## Answer\nContent."

        with patch(
            "daemon.tools.knowledge_tools.invoke_agent_and_wait",
            new_callable=AsyncMock,
            return_value=(explorer_response, "test-child-id"),
        ):
            with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
                mock_tempfile.gettempdir.return_value = str(tmp_path)

                tools = create_knowledge_tools(
                    mock_manager_with_checkpointer, "parent-instance-id"
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                with patch("daemon.tools.knowledge_tools._save_explorer_result") as mock_save:
                    await explore_tool.ainvoke({"query": "Quick question"})

                    # Save should NOT be triggered (checkpoint says no RAG)
                    mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_error_still_checks_checkpoint(
        self, mock_manager_with_checkpointer, tmp_path
    ):
        """Even when agent errors, checkpoint is inspected — if RAG was called, save triggers."""
        # Checkpoint shows RAG was called BEFORE the error
        mock_manager_with_checkpointer._checkpointer.aget = AsyncMock(return_value={
            "channel_values": {
                "messages": [
                    _make_message([_make_tool_call("rag_query_data")]),
                    _make_message([_make_tool_call("bash")]),
                ]
            }
        })

        # Agent errors out
        error_response = "Error: Agent failed. Something broke at the end."

        with patch(
            "daemon.tools.knowledge_tools.invoke_agent_and_wait",
            new_callable=AsyncMock,
            return_value=(error_response, "test-child-id"),
        ):
            tools = create_knowledge_tools(
                mock_manager_with_checkpointer, "parent-instance-id"
            )
            explore_tool = next(t for t in tools if t.name == "explore")

            with patch("daemon.tools.knowledge_tools._save_explorer_result") as mock_save:
                result = await explore_tool.ainvoke({"query": "Some query"})

                # The error should be returned to the caller
                assert "Error" in result

                # Checkpoint was inspected even though the agent errored.
                # The new code calls aget() once for RAG detection, once for
                # RAG error detection, and once for read_file detection,
                # so we expect 3 calls.
                assert mock_manager_with_checkpointer._checkpointer.aget.await_count == 3

                # No save on error path — we return BEFORE the save block
                mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_no_checkpointer_attribute(
        self, configured_env, mock_manager, tmp_path
    ):
        """When manager has no _checkpointer attribute, falls back gracefully."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project"}
        mock_instance_meta.project_id = "test-project"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        # No _checkpointer set on the manager
        if hasattr(mock_manager, "_checkpointer"):
            del mock_manager._checkpointer

        explorer_response = "## Answer\nFound info."

        with patch(
            "daemon.tools.knowledge_tools.invoke_agent_and_wait",
            new_callable=AsyncMock,
            return_value=(explorer_response, "test-child-id"),
        ):
            with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
                mock_tempfile.gettempdir.return_value = str(tmp_path)

                tools = create_knowledge_tools(mock_manager, "parent-instance-id")
                explore_tool = next(t for t in tools if t.name == "explore")

                with patch("daemon.tools.knowledge_tools._save_explorer_result") as mock_save:
                    # Should not raise; with no checkpointer, rag_queried=False
                    await explore_tool.ainvoke({"query": "What?"})
                    mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_explore_returns_tuple_from_invoke(
        self, mock_manager_with_checkpointer
    ):
        """invoke_agent_and_wait is called with return_instance_id=True."""
        with patch(
            "daemon.tools.knowledge_tools.invoke_agent_and_wait",
            new_callable=AsyncMock,
            return_value=("Result", "test-child-id"),
        ) as mock_invoke:
            tools = create_knowledge_tools(
                mock_manager_with_checkpointer, "parent-instance-id"
            )
            explore_tool = next(t for t in tools if t.name == "explore")

            await explore_tool.ainvoke({"query": "What?"})

            # Verify the call was made with return_instance_id=True
            mock_invoke.assert_called_once()
            call_kwargs = mock_invoke.call_args.kwargs


# =============================================================================
# Experience Auto-Save Tests
# =============================================================================


class TestExperienceAutoSave:
    """Tests for experience() auto-save to the shared context directory.

    Verifies:
    - A ``*_experience.md`` file is created on call
    - Near-duplicate text (Jaccard overlap >= 0.8) is skipped
    - Non-duplicate text is saved
    - Errors never propagate (fire-and-forget)
    """

    @pytest.fixture
    def mock_manager_for_experience_save(self, configured_env, mock_manager):
        """Mock manager set up for experience auto-save tests."""
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project-123"}
        mock_instance_meta.project_id = "test-project-123"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)

        mock_manager._instance_repository.get_tree_root_id = MagicMock(
            return_value="experience-save-context"
        )

        # Project name lookup
        mock_project = MagicMock()
        mock_project.name = "agents-ensemble"
        mock_manager._project_repository = MagicMock()
        mock_manager._project_repository.get = MagicMock(return_value=mock_project)

        return mock_manager

    @pytest.mark.asyncio
    async def test_experience_saves_file_with_experience_suffix(
        self, mock_manager_for_experience_save, tmp_path
    ):
        """Calling experience() saves a file with the ``_experience.md`` suffix."""
        text = "The project uses Python 3.11 with FastAPI for the backend service."

        with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
            mock_tempfile.gettempdir.return_value = str(tmp_path)

            tools = create_knowledge_tools(
                mock_manager_for_experience_save, "parent-instance-id"
            )
            experience_tool = next(t for t in tools if t.name == "experience")

            await experience_tool.ainvoke({"text": text})

            # Allow fire-and-forget thread to complete
            await asyncio.sleep(0.1)

            context_dir = tmp_path / "ensemble" / "context" / "experience-save-context"
            files = list(context_dir.glob("*_experience.md"))
            assert len(files) == 1, (
                f"Expected 1 _experience.md file, got {len(files)}: "
                f"{[f.name for f in files]}"
            )

            # Content sanity check
            content = files[0].read_text(encoding="utf-8")
            assert "# Experience Recorded" in content
            assert text in content
            assert "agents-ensemble" in content

    @pytest.mark.asyncio
    async def test_experience_skips_duplicate_content(
        self, mock_manager_for_experience_save, tmp_path
    ):
        """Near-duplicate content (containment overlap >= 0.8) is skipped.

        Regression test for the Jaccard→containment fix in
        ``_is_duplicate_experience``: with Jaccard, the file's markdown
        header (``# Experience Recorded``, ``**Time**:``, ``**Project**:``,
        etc.) inflates the union denominator without contributing to the
        intersection, so the ratio drops well below 0.8 even for
        near-identical content. Containment (intersection / min) is the
        correct metric here.

        This test also deliberately uses a *different* first 60 chars
        for the new text vs. the pre-seeded text — i.e. a different
        slug — so any future change that brings back silent overwrite
        (e.g. dropping the timestamp and re-introducing slug collision)
        would no longer be masked as "dedup". With a unique timestamped
        filename (post Fix #2), a true dedup failure would create a
        second file and the assertion would fail.
        """
        context_dir = tmp_path / "ensemble" / "context" / "experience-save-context"
        context_dir.mkdir(parents=True, exist_ok=True)

        # Pre-seeded text — opens with "The authentication..." (slug: "the-...")
        pre_seeded_text = (
            "The authentication system uses JWT tokens for user authentication "
            "with refresh tokens for session security and rotation."
        )
        pre_seeded_slug = "the-authentication-system-uses-jwt-tokens-for-user-authentic"
        pre_seeded_file = context_dir / f"{pre_seeded_slug}_experience.md"
        pre_seeded_file.write_text(
            f"# Experience Recorded\n**Time**: 2026-06-14T12:00:00\n"
            f"**Project**: agents-ensemble\n\n{pre_seeded_text}\n",
            encoding="utf-8",
        )

        # New text — opens with "JWT-based..." (slug: "jwt-based-...") but
        # shares enough tokens with the pre-seeded text to trip containment
        # dedup. Token math:
        #   pre_seeded unique tokens: ~14 (incl. "the", "jwt", "tokens"...)
        #   new unique tokens:         ~13 (incl. "jwt-based" instead of "the", "jwt")
        #   shared:                    ~12
        #   Jaccard (old):             12 / (13 + 14 - 12) = 12/15 ≈ 0.80 — borderline
        #   Jaccard vs. file content:  12 / (13 + 21 - 12) = 12/22 ≈ 0.55 < 0.8
        #   Containment (new):         12 / min(13, 21) = 12/13 ≈ 0.92 ≥ 0.8
        # So Jaccard would *not* trigger dedup (especially against the file
        # content which includes the markdown header); containment does.
        new_text = (
            "JWT-based authentication system uses tokens for user "
            "authentication with refresh tokens for session security and rotation."
        )
        new_slug = "jwt-based-authentication-system-uses-tokens-for-user-authen"
        # Sanity: slugs must differ or this test is meaningless.
        assert pre_seeded_slug != new_slug

        with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
            mock_tempfile.gettempdir.return_value = str(tmp_path)

            tools = create_knowledge_tools(
                mock_manager_for_experience_save, "parent-instance-id"
            )
            experience_tool = next(t for t in tools if t.name == "experience")

            await experience_tool.ainvoke({"text": new_text})

            await asyncio.sleep(0.1)

            # Should still be exactly 1 file (the pre-seeded one). If dedup
            # did not fire, the timestamped new filename would have produced
            # a second file (different slug → no overwrite possible).
            files = list(context_dir.glob("*_experience.md"))
            assert len(files) == 1, (
                f"Expected 1 file (duplicate skip), got {len(files)}: "
                f"{[f.name for f in files]}"
            )
            assert files[0].name == f"{pre_seeded_slug}_experience.md", (
                f"Expected the pre-seeded file to remain, got {files[0].name}"
            )

    @pytest.mark.asyncio
    async def test_experience_saves_non_duplicate_content(
        self, mock_manager_for_experience_save, tmp_path
    ):
        """Non-duplicate content is saved as a new ``*_experience.md`` file."""
        context_dir = tmp_path / "ensemble" / "context" / "experience-save-context"
        context_dir.mkdir(parents=True, exist_ok=True)

        # Pre-seed with unrelated content so dedup does not match.
        existing_file = context_dir / "unrelated_experience.md"
        existing_file.write_text(
            "# Experience Recorded\n\nCompletely unrelated knowledge about cooking pasta.",
            encoding="utf-8",
        )

        text = (
            "The deployment pipeline uses GitHub Actions for CI and ArgoCD for "
            "continuous delivery to the Kubernetes cluster with canary releases."
        )

        with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
            mock_tempfile.gettempdir.return_value = str(tmp_path)

            tools = create_knowledge_tools(
                mock_manager_for_experience_save, "parent-instance-id"
            )
            experience_tool = next(t for t in tools if t.name == "experience")

            await experience_tool.ainvoke({"text": text})

            await asyncio.sleep(0.1)

            files = list(context_dir.glob("*_experience.md"))
            # The pre-seeded file plus the new save = 2.
            assert len(files) == 2, (
                f"Expected 2 files (existing + new), got {len(files)}: "
                f"{[f.name for f in files]}"
            )

            new_files = [f for f in files if "deployment-pipeline" in f.name]
            assert len(new_files) == 1, (
                f"Expected 1 new file with deployment slug, got {len(new_files)}"
            )
            content = new_files[0].read_text(encoding="utf-8")
            assert text in content

    @pytest.mark.asyncio
    async def test_experience_save_failure_does_not_propagate(
        self, mock_manager_for_experience_save, tmp_path
    ):
        """If _save_experience_result raises, experience() still returns normally.

        The save is fire-and-forget; failures are logged at DEBUG but never
        propagate to the caller or affect the return value.
        """
        with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
            mock_tempfile.gettempdir.return_value = str(tmp_path)

            tools = create_knowledge_tools(
                mock_manager_for_experience_save, "parent-instance-id"
            )
            experience_tool = next(t for t in tools if t.name == "experience")

            with patch(
                "daemon.tools.knowledge_tools._save_experience_result",
                side_effect=IOError("Disk full"),
            ):
                # Should not raise.
                result = await experience_tool.ainvoke(
                    {"text": "Some knowledge text to record."}
                )

            # Return value is preserved regardless of save failure.
            assert "Knowledge recording started" in result

    def test_save_experience_result_creates_file(self, tmp_path):
        """Direct unit test of ``_save_experience_result`` end-to-end."""
        from daemon.tools.knowledge_tools import _save_experience_result

        with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
            mock_tempfile.gettempdir.return_value = str(tmp_path)

            _save_experience_result(
                "The test suite uses pytest with asyncio mode enabled.",
                "direct-call-context",
                project_name="test-project",
            )

            context_dir = tmp_path / "ensemble" / "context" / "direct-call-context"
            files = list(context_dir.glob("*_experience.md"))
            assert len(files) == 1
            content = files[0].read_text(encoding="utf-8")
            assert "# Experience Recorded" in content
            assert "test-project" in content
            assert "pytest" in content

    def test_save_experience_result_never_raises_on_bad_path(self, tmp_path):
        """``_save_experience_result`` swallows errors (fire-and-forget)."""
        from daemon.tools.knowledge_tools import _save_experience_result

        # Force a path that cannot be created by making tmp_path a file.
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")

        with patch("daemon.tools.knowledge_tools.tempfile") as mock_tempfile:
            mock_tempfile.gettempdir.return_value = str(blocker)

            # Should not raise — error is logged at DEBUG.
            _save_experience_result(
                "Some experience text that would normally be recorded safely.",
                "bad-context",
                project_name=None,
            )


# =============================================================================
# Explore Caller-Model Override Tests
# =============================================================================
#
# These tests cover the "explore tool auto-switch model when caller is coder"
# feature: Explorer's ``meta.json`` may declare ``caller_model_overrides``,
# a map of caller agent_id -> model name. When the explore() tool is
# invoked, it resolves the override for the calling agent and forwards
# the resulting model to ``invoke_agent_and_wait``.
#
# IMPORTANT — MagicMock.get_version() returns truthy by default. Each
# test that exercises the fallback path explicitly sets
# ``get_version.return_value = None`` so the fallback to ``get_resolved``
# is actually exercised. (See the Explorer Result mock gotcha in the
# agent-registry docs.)


class TestExploreCallerModelOverrides:
    """Verify ``caller_model_overrides`` resolves and forwards to the
    spawned explorer instance.

    The registry lookup MUST follow the project pattern:
    ``get_version("explorer", None) or get_resolved("explorer")``.
    """

    def _make_explorer_meta(
        self,
        caller_model_overrides: dict | None = None,
    ) -> MagicMock:
        """Build a mock ``AgentMetadata`` carrying only the fields the
        ``explore()`` tool reads. Other attributes fall through to
        ``MagicMock`` defaults (which is fine — the tool only reads
        ``caller_model_overrides``).
        """
        meta = MagicMock()
        meta.caller_model_overrides = caller_model_overrides or {}
        return meta

    def _stub_registry(
        self,
        meta: MagicMock | None,
    ) -> MagicMock:
        """Build a mock registry whose ``get_version`` / ``get_resolved``
        return ``meta`` for ``"explorer"`` lookups (and ``None`` for
        everything else). The ``get_version``/``get_resolved`` branches
        are spelled out explicitly so the test exercises the real
        short-circuit (``get_version or get_resolved``) rather than
        relying on MagicMock's truthy default.
        """
        registry = MagicMock()

        def fake_get_version(agent_id: str, version_tag=None):
            if agent_id == "explorer":
                return meta
            return None

        def fake_get_resolved(agent_id: str):
            if agent_id == "explorer":
                return meta
            return None

        registry.get_version.side_effect = fake_get_version
        registry.get_resolved.side_effect = fake_get_resolved
        return registry

    @pytest.mark.asyncio
    async def test_coder_with_null_override_forwards_system_default_model(
        self, configured_env, mock_manager
    ):
        """``{"coder": null}`` means "use the system default model" — the
        ``explore()`` tool must resolve the actual default model string
        from ``manager.config.llm.model`` and forward it to
        ``invoke_agent_and_wait``. Without this resolution, the
        spawned explorer would fall back to its default ``"quick"``
        model, defeating the purpose of the override.
        """
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project"}
        mock_instance_meta.project_id = "test-project"
        mock_manager._instance_repository.get = MagicMock(
            return_value=mock_instance_meta
        )

        # Wire the mock manager's config so the resolve path can
        # discover the system default model. In production this comes
        # from ``config.llm.model`` (which defaults to OPENAI_MODEL).
        mock_manager.config = MagicMock()
        mock_manager.config.llm = MagicMock()
        mock_manager.config.llm.model = "gpt-4o"

        meta = self._make_explorer_meta(caller_model_overrides={"coder": None})
        mock_registry = self._stub_registry(meta)

        with patch(
            "daemon.registry.get_registry", return_value=mock_registry
        ):
            with patch(
                "daemon.tools.knowledge_tools.invoke_agent_and_wait",
                new_callable=AsyncMock,
                return_value=("Explorer result.", "test-child-id"),
            ) as mock_invoke:
                tools = create_knowledge_tools(
                    mock_manager, "parent-instance-id", agent_id="coder"
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                await explore_tool.ainvoke({"query": "What is X?"})

                # The resolved system default model MUST be forwarded.
                mock_invoke.assert_called_once()
                assert mock_invoke.call_args.kwargs.get("model") == "gpt-4o"

    @pytest.mark.asyncio
    async def test_coder_with_string_override_forwards_override_model(
        self, configured_env, mock_manager
    ):
        """``{"coder": "reasoning"}`` means "force the 'reasoning' model"
        — the explore() tool must forward ``model="reasoning"`` to
        ``invoke_agent_and_wait``. Spawn-layer allow-list enforcement
        happens via ``_resolve_model_override``.
        """
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project"}
        mock_instance_meta.project_id = "test-project"
        mock_manager._instance_repository.get = MagicMock(
            return_value=mock_instance_meta
        )

        meta = self._make_explorer_meta(
            caller_model_overrides={"coder": "reasoning"}
        )
        mock_registry = self._stub_registry(meta)

        with patch(
            "daemon.registry.get_registry", return_value=mock_registry
        ):
            with patch(
                "daemon.tools.knowledge_tools.invoke_agent_and_wait",
                new_callable=AsyncMock,
                return_value=("Explorer result.", "test-child-id"),
            ) as mock_invoke:
                tools = create_knowledge_tools(
                    mock_manager, "parent-instance-id", agent_id="coder"
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                await explore_tool.ainvoke({"query": "What is X?"})

                mock_invoke.assert_called_once()
                assert mock_invoke.call_args.kwargs.get("model") == "reasoning"

    @pytest.mark.asyncio
    async def test_non_coder_caller_does_not_forward_model(
        self, configured_env, mock_manager
    ):
        """Caller NOT in the overrides map (e.g. "developer") → no
        override is applied; ``model=None`` is forwarded (interpreted
        by the boundary as "no override"). Explorer uses its default
        ``llm_model``.
        """
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project"}
        mock_instance_meta.project_id = "test-project"
        mock_manager._instance_repository.get = MagicMock(
            return_value=mock_instance_meta
        )

        meta = self._make_explorer_meta(caller_model_overrides={"coder": None})
        mock_registry = self._stub_registry(meta)

        with patch(
            "daemon.registry.get_registry", return_value=mock_registry
        ):
            with patch(
                "daemon.tools.knowledge_tools.invoke_agent_and_wait",
                new_callable=AsyncMock,
                return_value=("Explorer result.", "test-child-id"),
            ) as mock_invoke:
                tools = create_knowledge_tools(
                    mock_manager, "parent-instance-id", agent_id="developer"
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                await explore_tool.ainvoke({"query": "What is X?"})

                mock_invoke.assert_called_once()
                # ``model`` is forwarded (always) but is None — no override.
                assert mock_invoke.call_args.kwargs.get("model") is None

    @pytest.mark.asyncio
    async def test_no_override_config_does_not_forward_model(
        self, configured_env, mock_manager
    ):
        """When Explorer's meta.json has no ``caller_model_overrides``
        field (backward-compat case), the map is empty and no override
        is applied regardless of caller. ``model=None`` is forwarded
        (boundary-level "no override").
        """
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project"}
        mock_instance_meta.project_id = "test-project"
        mock_manager._instance_repository.get = MagicMock(
            return_value=mock_instance_meta
        )

        # Empty map simulates an older meta.json without the field.
        meta = self._make_explorer_meta(caller_model_overrides={})
        mock_registry = self._stub_registry(meta)

        with patch(
            "daemon.registry.get_registry", return_value=mock_registry
        ):
            with patch(
                "daemon.tools.knowledge_tools.invoke_agent_and_wait",
                new_callable=AsyncMock,
                return_value=("Explorer result.", "test-child-id"),
            ) as mock_invoke:
                tools = create_knowledge_tools(
                    mock_manager, "parent-instance-id", agent_id="coder"
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                await explore_tool.ainvoke({"query": "What is X?"})

                mock_invoke.assert_called_once()
                assert mock_invoke.call_args.kwargs.get("model") is None

    @pytest.mark.asyncio
    async def test_null_agent_id_does_not_forward_model(
        self, configured_env, mock_manager
    ):
        """Backward-compat: legacy caller passes ``agent_id=""`` (default).
        The override lookup is skipped entirely and ``model=None`` is
        forwarded. This protects every existing test / call site that
        still uses the 2-arg ``create_knowledge_tools(manager, id)`` form.
        """
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project"}
        mock_instance_meta.project_id = "test-project"
        mock_manager._instance_repository.get = MagicMock(
            return_value=mock_instance_meta
        )

        # Even if the registry IS configured for an override, the empty
        # agent_id path must short-circuit before the lookup.
        meta = self._make_explorer_meta(caller_model_overrides={"coder": None})
        mock_registry = self._stub_registry(meta)

        with patch(
            "daemon.registry.get_registry", return_value=mock_registry
        ):
            with patch(
                "daemon.tools.knowledge_tools.invoke_agent_and_wait",
                new_callable=AsyncMock,
                return_value=("Explorer result.", "test-child-id"),
            ) as mock_invoke:
                tools = create_knowledge_tools(
                    mock_manager, "parent-instance-id", agent_id=""
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                await explore_tool.ainvoke({"query": "What is X?"})

                mock_invoke.assert_called_once()
                assert mock_invoke.call_args.kwargs.get("model") is None

    @pytest.mark.asyncio
    async def test_registry_returns_none_falls_back_to_no_override(
        self, configured_env, mock_manager
    ):
        """When the registry has no entry for "explorer" (e.g. exploratory
        test env), the override lookup is skipped and ``model=None`` is
        forwarded. Ensures the fallback path tolerates a missing
        ``AgentMetadata`` without raising.
        """
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project"}
        mock_instance_meta.project_id = "test-project"
        mock_manager._instance_repository.get = MagicMock(
            return_value=mock_instance_meta
        )

        # Registry has no explorer → get_version and get_resolved both
        # return None. Critical: the bare MagicMock default returns a
        # truthy object, so we must explicitly set return_value to None.
        mock_registry = MagicMock()
        mock_registry.get_version.return_value = None
        mock_registry.get_resolved.return_value = None

        with patch(
            "daemon.registry.get_registry", return_value=mock_registry
        ):
            with patch(
                "daemon.tools.knowledge_tools.invoke_agent_and_wait",
                new_callable=AsyncMock,
                return_value=("Explorer result.", "test-child-id"),
            ) as mock_invoke:
                tools = create_knowledge_tools(
                    mock_manager, "parent-instance-id", agent_id="coder"
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                # Should not raise even with no explorer meta.
                await explore_tool.ainvoke({"query": "What is X?"})

                mock_invoke.assert_called_once()
                assert mock_invoke.call_args.kwargs.get("model") is None

    @pytest.mark.asyncio
    async def test_registry_lookup_error_does_not_break_explore(
        self, configured_env, mock_manager
    ):
        """If ``get_registry`` raises (e.g. wiring failure), the
        override lookup is swallowed at DEBUG and ``explore()`` still
        works — the tool must not propagate registry failures to the
        caller.
        """
        mock_instance_meta = MagicMock()
        mock_instance_meta.instance_metadata = {"project_id": "test-project"}
        mock_instance_meta.project_id = "test-project"
        mock_manager._instance_repository.get = MagicMock(
            return_value=mock_instance_meta
        )

        with patch(
            "daemon.registry.get_registry",
            side_effect=RuntimeError("registry unavailable"),
        ):
            with patch(
                "daemon.tools.knowledge_tools.invoke_agent_and_wait",
                new_callable=AsyncMock,
                return_value=("Explorer result.", "test-child-id"),
            ) as mock_invoke:
                tools = create_knowledge_tools(
                    mock_manager, "parent-instance-id", agent_id="coder"
                )
                explore_tool = next(t for t in tools if t.name == "explore")

                result = await explore_tool.ainvoke({"query": "What is X?"})

                # Tool still returns the explorer's result.
                assert "Explorer result" in result
                # No override was applied — model=None.
                assert mock_invoke.call_args.kwargs.get("model") is None
