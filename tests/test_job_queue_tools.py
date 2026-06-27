"""Comprehensive tests for job queue tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.tools.job_queue import create_job_tools
from daemon.tools._tool_registry import CATEGORY_MODULES
from daemon import constants
from daemon.services import project_normalizer

# Test constant for system default project ID
TEST_SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


# ── Autouse Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def setup_system_default_project():
    """Set SYSTEM_DEFAULT_PROJECT_ID for tests that call normalize_project_id()."""
    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    constants.SYSTEM_DEFAULT_PROJECT_ID = TEST_SYSTEM_PROJECT_ID

    yield

    constants.SYSTEM_DEFAULT_PROJECT_ID = original


class TestJobQueueToolRegistration:
    """Tests for tool registration."""

    def test_create_job_tools_returns_17_tools(self):
        """Verify create_job_tools returns exactly 17 tools (16 original + job_continue)."""
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()

        tools = create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

        assert len(tools) == 17

    def test_each_tool_has_job_category(self):
        """Verify each tool has _tool_category == 'job' attribute."""
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()

        tools = create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

        for tool in tools:
            assert hasattr(tool, "_tool_category")
            assert tool._tool_category == "job"

    def test_job_in_category_modules(self):
        """Verify 'job' is in CATEGORY_MODULES."""
        assert "job" in CATEGORY_MODULES
        assert CATEGORY_MODULES["job"] == "daemon.tools.job_queue"

    def test_create_job_tools_importable_from_daemon_tools(self):
        """Verify create_job_tools is importable from daemon.tools."""
        from daemon.tools import create_job_tools
        assert callable(create_job_tools)


class TestJobCreateTool:
    """Tests for job_create tool."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def tools(self, mock_services):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(job_service, queue_mgmt_service, dead_letter_service, agent_id="test-agent")

    @pytest.fixture
    def job_create(self, tools):
        return tools[0]  # job_create is first

    @pytest.mark.asyncio
    async def test_job_create_happy_path(self, mock_services, tools):
        """Happy: job_service.enqueue() returns JobItem, verify returned dict."""
        job_service, _, _ = mock_services
        job_create = tools[0]

        mock_job_item = MagicMock()
        expected_dict = {
            "job_id": "job-1", "status": "pending", "agent_id": "developer",
            "message": "test", "source": "api", "project_id": None, "queue_id": None,
            "priority": 5, "created_at": None, "started_at": None, "completed_at": None,
            "instance_id": None, "error_message": None, "result_summary": None,
            "metadata": None, "cancelled_at": None, "deleted_at": None,
            "retry_count": 0, "max_retries": 3, "idempotency_key": None,
            "failed_at": None, "next_retry_at": None
        }
        mock_job_item.to_dict.return_value = expected_dict
        job_service.enqueue.return_value = mock_job_item

        result = await job_create.ainvoke({
            "agent_id": "developer",
            "message": "test",
        })

        assert result == expected_dict
        job_service.enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_job_create_value_error(self, mock_services, tools):
        """Error: ValueError → {'error': str(e)}."""
        job_service, _, _ = mock_services
        job_create = tools[0]

        job_service.enqueue.side_effect = ValueError("Invalid job parameters")

        result = await job_create.ainvoke({
            "agent_id": "developer",
            "message": "test",
        })

        assert result == {"error": "Invalid job parameters"}

    @pytest.mark.asyncio
    async def test_job_create_generic_exception(self, mock_services, tools):
        """Error: Generic Exception → {'error': 'Failed to create job: ...'}."""
        job_service, _, _ = mock_services
        job_create = tools[0]

        job_service.enqueue.side_effect = RuntimeError("Database connection failed")

        result = await job_create.ainvoke({
            "agent_id": "developer",
            "message": "test",
        })

        assert result == {"error": "Failed to create job: Database connection failed"}

    @pytest.mark.asyncio
    async def test_job_create_agent_source_override(self, mock_services):
        """Edge case: When agent_id='test-agent', calling with default source='api' passes 'agent:test-agent'."""
        job_service, _, _ = mock_services
        tools = create_job_tools(job_service, mock_services[1], mock_services[2], agent_id="test-agent")
        job_create = tools[0]

        mock_job_item = MagicMock()
        mock_job_item.to_dict.return_value = {"job_id": "job-1"}
        job_service.enqueue.return_value = mock_job_item

        await job_create.ainvoke({
            "agent_id": "developer",
            "message": "test",
        })

        # Verify source was overridden to 'agent:test-agent'
        call_kwargs = job_service.enqueue.call_args.kwargs
        assert call_kwargs["source"] == "agent:test-agent"

    @pytest.mark.asyncio
    async def test_job_create_explicit_source_not_overridden(self, mock_services):
        """Edge case: When source is explicitly set to 'manual', it should NOT be overridden."""
        job_service, _, _ = mock_services
        tools = create_job_tools(job_service, mock_services[1], mock_services[2], agent_id="test-agent")
        job_create = tools[0]

        mock_job_item = MagicMock()
        mock_job_item.to_dict.return_value = {"job_id": "job-1"}
        job_service.enqueue.return_value = mock_job_item

        await job_create.ainvoke({
            "agent_id": "developer",
            "message": "test",
            "source": "manual",
        })

        # Verify source was NOT overridden (stays as 'manual')
        call_kwargs = job_service.enqueue.call_args.kwargs
        assert call_kwargs["source"] == "manual"


class TestJobGetTool:
    """Tests for job_get tool."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def tools(self, mock_services):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

    @pytest.fixture
    def job_get(self, tools):
        return tools[1]  # job_get is second

    @pytest.mark.asyncio
    async def test_job_get_happy_path(self, mock_services, tools):
        """Happy: returns job_item.to_dict()."""
        job_service, _, _ = mock_services
        job_get = tools[1]

        mock_job_item = MagicMock()
        expected_dict = {"job_id": "job-1", "status": "pending"}
        mock_job_item.to_dict.return_value = expected_dict
        job_service.get_job.return_value = mock_job_item

        result = await job_get.ainvoke({"job_id": "job-1"})

        assert result == expected_dict

    @pytest.mark.asyncio
    async def test_job_get_not_found(self, mock_services, tools):
        """Error: service returns None → {'error': 'Job not found'}."""
        job_service, _, _ = mock_services
        job_get = tools[1]

        job_service.get_job.return_value = None

        result = await job_get.ainvoke({"job_id": "job-1"})

        assert result == {"error": "Job not found"}

    @pytest.mark.asyncio
    async def test_job_get_exception(self, mock_services, tools):
        """Error: Exception → {'error': 'Failed to get job: ...'}."""
        job_service, _, _ = mock_services
        job_get = tools[1]

        job_service.get_job.side_effect = RuntimeError("Database error")

        result = await job_get.ainvoke({"job_id": "job-1"})

        assert result == {"error": "Failed to get job: Database error"}


class TestJobListTool:
    """Tests for job_list tool."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def tools(self, mock_services):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

    @pytest.fixture
    def job_list(self, tools):
        return tools[2]  # job_list is third

    @pytest.mark.asyncio
    async def test_job_list_happy_path(self, mock_services, tools):
        """Happy: returns {'jobs': [...], 'count': N}."""
        job_service, _, _ = mock_services
        job_list = tools[2]

        mock_job_1 = MagicMock()
        mock_job_1.to_dict.return_value = {"job_id": "job-1"}
        mock_job_2 = MagicMock()
        mock_job_2.to_dict.return_value = {"job_id": "job-2"}
        job_service.list_jobs.return_value = [mock_job_1, mock_job_2]

        result = await job_list.ainvoke({})

        assert result == {"jobs": [{"job_id": "job-1"}, {"job_id": "job-2"}], "count": 2}

    @pytest.mark.asyncio
    async def test_job_list_exception(self, mock_services, tools):
        """Error: Exception → {'error': 'Failed to list jobs: ...'}."""
        job_service, _, _ = mock_services
        job_list = tools[2]

        job_service.list_jobs.side_effect = RuntimeError("Query failed")

        result = await job_list.ainvoke({})

        assert result == {"error": "Failed to list jobs: Query failed"}


class TestJobCancelTool:
    """Tests for job_cancel tool."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def tools(self, mock_services):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

    @pytest.fixture
    def job_cancel(self, tools):
        return tools[3]  # job_cancel is fourth

    @pytest.mark.asyncio
    async def test_job_cancel_success(self, mock_services, tools):
        """Happy: cancel_job returns True → 'Job {job_id} cancelled successfully.'"""
        job_service, _, _ = mock_services
        job_cancel = tools[3]

        job_service.cancel_job.return_value = True

        result = await job_cancel.ainvoke({"job_id": "job-1"})

        assert result == "Job job-1 cancelled successfully."

    @pytest.mark.asyncio
    async def test_job_cancel_failure(self, mock_services, tools):
        """Error: cancel_job returns False → 'ERROR: Could not cancel job {job_id}. Job may not be in a cancellable state.'"""
        job_service, _, _ = mock_services
        job_cancel = tools[3]

        job_service.cancel_job.return_value = False

        result = await job_cancel.ainvoke({"job_id": "job-1"})

        assert result == "ERROR: Could not cancel job job-1. Job may not be in a cancellable state."

    @pytest.mark.asyncio
    async def test_job_cancel_exception(self, mock_services, tools):
        """Error: Exception → 'ERROR: Failed to cancel job {job_id}: ...'"""
        job_service, _, _ = mock_services
        job_cancel = tools[3]

        job_service.cancel_job.side_effect = RuntimeError("Service unavailable")

        result = await job_cancel.ainvoke({"job_id": "job-1"})

        assert result == "ERROR: Failed to cancel job job-1: Service unavailable"


class TestJobRetryTool:
    """Tests for job_retry tool."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def tools(self, mock_services):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

    @pytest.fixture
    def job_retry(self, tools):
        return tools[4]  # job_retry is fifth

    @pytest.mark.asyncio
    async def test_job_retry_success(self, mock_services, tools):
        """Happy: retry_job returns JobItem → 'Job {job_id} retry initiated successfully.' (NO new job_id mentioned!)"""
        job_service, _, _ = mock_services
        job_retry = tools[4]

        mock_job_item = MagicMock()
        job_service.retry_job.return_value = mock_job_item

        result = await job_retry.ainvoke({"job_id": "job-1"})

        assert result == "Job job-1 retry initiated successfully."
        # Ensure new job_id is NOT mentioned in the success message
        assert "new" not in result.lower()

    @pytest.mark.asyncio
    async def test_job_retry_failure(self, mock_services, tools):
        """Error: retry_job returns None → 'ERROR: Could not retry job {job_id}. Job may not be in a retryable state.'"""
        job_service, _, _ = mock_services
        job_retry = tools[4]

        job_service.retry_job.return_value = None

        result = await job_retry.ainvoke({"job_id": "job-1"})

        assert result == "ERROR: Could not retry job job-1. Job may not be in a retryable state."

    @pytest.mark.asyncio
    async def test_job_retry_exception(self, mock_services, tools):
        """Error: Exception → 'ERROR: Failed to retry job {job_id}: ...'"""
        job_service, _, _ = mock_services
        job_retry = tools[4]

        job_service.retry_job.side_effect = RuntimeError("Service error")

        result = await job_retry.ainvoke({"job_id": "job-1"})

        assert result == "ERROR: Failed to retry job job-1: Service error"


class TestJobDeleteTool:
    """Tests for job_delete tool."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def tools(self, mock_services):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

    @pytest.fixture
    def job_delete(self, tools):
        return tools[5]  # job_delete is sixth

    @pytest.mark.asyncio
    async def test_job_delete_success(self, mock_services, tools):
        """Happy: soft_delete_job returns JobItem → 'Job {job_id} deleted successfully.'"""
        job_service, _, _ = mock_services
        job_delete = tools[5]

        mock_job_item = MagicMock()
        job_service.soft_delete_job.return_value = mock_job_item

        result = await job_delete.ainvoke({"job_id": "job-1"})

        assert result == "Job job-1 deleted successfully."

    @pytest.mark.asyncio
    async def test_job_delete_failure(self, mock_services, tools):
        """Error: returns None → 'ERROR: Could not delete job {job_id}. Job may not exist.'"""
        job_service, _, _ = mock_services
        job_delete = tools[5]

        job_service.soft_delete_job.return_value = None

        result = await job_delete.ainvoke({"job_id": "job-1"})

        assert result == "ERROR: Could not delete job job-1. Job may not exist."

    @pytest.mark.asyncio
    async def test_job_delete_exception(self, mock_services, tools):
        """Error: Exception → 'ERROR: Failed to delete job {job_id}: ...'"""
        job_service, _, _ = mock_services
        job_delete = tools[5]

        job_service.soft_delete_job.side_effect = RuntimeError("Database error")

        result = await job_delete.ainvoke({"job_id": "job-1"})

        assert result == "ERROR: Failed to delete job job-1: Database error"


class TestJobRestoreTool:
    """Tests for job_restore tool."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def tools(self, mock_services):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

    @pytest.fixture
    def job_restore(self, tools):
        return tools[6]  # job_restore is seventh

    @pytest.mark.asyncio
    async def test_job_restore_success(self, mock_services, tools):
        """Happy: restore_job returns JobItem → 'Job {job_id} restored successfully.'"""
        job_service, _, _ = mock_services
        job_restore = tools[6]

        mock_job_item = MagicMock()
        job_service.restore_job.return_value = mock_job_item

        result = await job_restore.ainvoke({"job_id": "job-1"})

        assert result == "Job job-1 restored successfully."

    @pytest.mark.asyncio
    async def test_job_restore_failure(self, mock_services, tools):
        """Error: returns None → 'ERROR: Could not restore job {job_id}. Job may not exist or may not be deleted.'"""
        job_service, _, _ = mock_services
        job_restore = tools[6]

        job_service.restore_job.return_value = None

        result = await job_restore.ainvoke({"job_id": "job-1"})

        assert result == "ERROR: Could not restore job job-1. Job may not exist or may not be deleted."

    @pytest.mark.asyncio
    async def test_job_restore_exception(self, mock_services, tools):
        """Error: Exception → 'ERROR: Failed to restore job {job_id}: ...'"""
        job_service, _, _ = mock_services
        job_restore = tools[6]

        job_service.restore_job.side_effect = RuntimeError("Service error")

        result = await job_restore.ainvoke({"job_id": "job-1"})

        assert result == "ERROR: Failed to restore job job-1: Service error"


class TestQueueListTool:
    """Tests for queue_list tool."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def tools(self, mock_services):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

    @pytest.fixture
    def queue_list(self, tools):
        return tools[7]  # queue_list is eighth

    @pytest.mark.asyncio
    async def test_queue_list_happy_path(self, mock_services, tools):
        """Happy: returns {'queues': [...], 'count': N}."""
        _, queue_mgmt_service, _ = mock_services
        queue_list = tools[7]

        mock_queue_1 = MagicMock()
        mock_queue_1.queue_id = "queue-1"
        mock_queue_2 = MagicMock()
        mock_queue_2.queue_id = "queue-2"
        queue_mgmt_service.list_queues.return_value = [mock_queue_1, mock_queue_2]

        result = await queue_list.ainvoke({"project_id": "proj-1"})

        assert result == {"queues": [mock_queue_1, mock_queue_2], "count": 2}

    @pytest.mark.asyncio
    async def test_queue_list_exception(self, mock_services, tools):
        """Error: Exception → {'error': 'Failed to list queues: ...'}."""
        _, queue_mgmt_service, _ = mock_services
        queue_list = tools[7]

        queue_mgmt_service.list_queues.side_effect = RuntimeError("Database error")

        result = await queue_list.ainvoke({"project_id": "proj-1"})

        assert result == {"error": "Failed to list queues: Database error"}


class TestQueueCreateTool:
    """Tests for queue_create tool."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def tools(self, mock_services):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

    @pytest.fixture
    def queue_create(self, tools):
        return tools[8]  # queue_create is ninth

    @pytest.mark.asyncio
    async def test_queue_create_happy_path(self, mock_services, tools):
        """Happy: create_queue returns queue → 'Queue '{queue_name}' created successfully. Queue ID: {queue.queue_id}'"""
        _, queue_mgmt_service, _ = mock_services
        queue_create = tools[8]

        mock_queue = MagicMock()
        mock_queue.queue_id = "queue-123"
        queue_mgmt_service.create_queue.return_value = mock_queue

        result = await queue_create.ainvoke({
            "project_id": "proj-1",
            "queue_name": "test-queue",
        })

        assert result == "Queue 'test-queue' created successfully. Queue ID: queue-123"

    @pytest.mark.asyncio
    async def test_queue_create_value_error(self, mock_services, tools):
        """Error: ValueError → 'ERROR: {str(e)}'"""
        _, queue_mgmt_service, _ = mock_services
        queue_create = tools[8]

        queue_mgmt_service.create_queue.side_effect = ValueError("Queue name already exists")

        result = await queue_create.ainvoke({
            "project_id": "proj-1",
            "queue_name": "test-queue",
        })

        assert result == "ERROR: Queue name already exists"

    @pytest.mark.asyncio
    async def test_queue_create_exception(self, mock_services, tools):
        """Error: Exception → 'ERROR: Failed to create queue: ...'}"""
        _, queue_mgmt_service, _ = mock_services
        queue_create = tools[8]

        queue_mgmt_service.create_queue.side_effect = RuntimeError("Database error")

        result = await queue_create.ainvoke({
            "project_id": "proj-1",
            "queue_name": "test-queue",
        })

        assert result == "ERROR: Failed to create queue: Database error"


class TestQueueUpdateTool:
    """Tests for queue_update tool."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def tools(self, mock_services):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

    @pytest.fixture
    def queue_update(self, tools):
        return tools[9]  # queue_update is tenth

    @pytest.mark.asyncio
    async def test_queue_update_happy_path(self, mock_services, tools):
        """Happy: all params provided, get_queue returns queue, update_queue returns queue → 'Queue {queue_id} updated successfully.'"""
        _, queue_mgmt_service, _ = mock_services
        queue_update = tools[9]

        mock_queue = MagicMock()
        queue_mgmt_service.get_queue.return_value = mock_queue
        queue_mgmt_service.update_queue.return_value = mock_queue

        result = await queue_update.ainvoke({
            "queue_id": "queue-1",
            "project_id": "proj-1",
            "queue_name": "new-name",
            "concurrency_limit": 5,
            "is_paused": True,
        })

        assert result == "Queue queue-1 updated successfully."

    @pytest.mark.asyncio
    async def test_queue_update_no_updates(self, mock_services, tools):
        """Edge case: all optional params (queue_name, concurrency_limit, is_paused) are None → 'ERROR: No updates provided.'"""
        _, queue_mgmt_service, _ = mock_services
        queue_update = tools[9]

        result = await queue_update.ainvoke({
            "queue_id": "queue-1",
            "project_id": "proj-1",
        })

        assert result == "ERROR: No updates provided."
        # Verify get_queue was NOT called
        queue_mgmt_service.get_queue.assert_not_called()

    @pytest.mark.asyncio
    async def test_queue_update_queue_not_found(self, mock_services, tools):
        """Edge case: get_queue returns None → 'ERROR: Queue {queue_id} not found in project {project_id}.'"""
        _, queue_mgmt_service, _ = mock_services
        queue_update = tools[9]

        queue_mgmt_service.get_queue.return_value = None

        result = await queue_update.ainvoke({
            "queue_id": "queue-1",
            "project_id": "proj-1",
            "queue_name": "new-name",
        })

        assert result == "ERROR: Queue queue-1 not found in project proj-1."

    @pytest.mark.asyncio
    async def test_queue_update_update_returns_none(self, mock_services, tools):
        """Error: update_queue returns None → 'ERROR: Queue {queue_id} not found.'"""
        _, queue_mgmt_service, _ = mock_services
        queue_update = tools[9]

        mock_queue = MagicMock()
        queue_mgmt_service.get_queue.return_value = mock_queue
        queue_mgmt_service.update_queue.return_value = None

        result = await queue_update.ainvoke({
            "queue_id": "queue-1",
            "project_id": "proj-1",
            "is_paused": True,
        })

        assert result == "ERROR: Queue queue-1 not found."

    @pytest.mark.asyncio
    async def test_queue_update_value_error(self, mock_services, tools):
        """Error: ValueError → 'ERROR: {str(e)}'"""
        _, queue_mgmt_service, _ = mock_services
        queue_update = tools[9]

        # Service raises ValueError for business logic validation (not Pydantic)
        queue_mgmt_service.update_queue.side_effect = ValueError("Queue name already taken")

        mock_queue = MagicMock()
        queue_mgmt_service.get_queue.return_value = mock_queue

        result = await queue_update.ainvoke({
            "queue_id": "queue-1",
            "project_id": "proj-1",
            "queue_name": "duplicate-name",  # Valid Pydantic value, triggers service ValueError
        })

        assert result == "ERROR: Queue name already taken"

    @pytest.mark.asyncio
    async def test_queue_update_exception(self, mock_services, tools):
        """Error: Exception → 'ERROR: Failed to update queue: ...'}"""
        _, queue_mgmt_service, _ = mock_services
        queue_update = tools[9]

        queue_mgmt_service.update_queue.side_effect = RuntimeError("Database error")

        mock_queue = MagicMock()
        queue_mgmt_service.get_queue.return_value = mock_queue

        result = await queue_update.ainvoke({
            "queue_id": "queue-1",
            "project_id": "proj-1",
            "is_paused": False,
        })

        assert result == "ERROR: Failed to update queue: Database error"


class TestDlqListTool:
    """Tests for dlq_list tool (SYNC)."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def tools(self, mock_services):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

    @pytest.fixture
    def dlq_list(self, tools):
        return tools[10]  # dlq_list is eleventh

    def test_dlq_list_happy_path(self, mock_services, tools):
        """Happy: returns {'items': [...], 'count': N, 'total': M}."""
        _, _, dead_letter_service = mock_services
        dlq_list = tools[10]

        mock_dlq_item_1 = MagicMock()
        mock_dlq_item_1.to_dict.return_value = {"dlq_id": "dlq-1", "job_id": "job-1"}
        mock_dlq_item_2 = MagicMock()
        mock_dlq_item_2.to_dict.return_value = {"dlq_id": "dlq-2", "job_id": "job-2"}
        dead_letter_service.list_dlq.return_value = ([mock_dlq_item_1, mock_dlq_item_2], 5)

        # Use .invoke() for sync tool
        result = dlq_list.invoke({
            "project_id": "proj-1",
        })

        assert result == {
            "items": [{"dlq_id": "dlq-1", "job_id": "job-1"}, {"dlq_id": "dlq-2", "job_id": "job-2"}],
            "count": 2,
            "total": 5,
        }

    def test_dlq_list_exception(self, mock_services, tools):
        """Error: Exception → {'error': 'Failed to list DLQ items: ...'}."""
        _, _, dead_letter_service = mock_services
        dlq_list = tools[10]

        dead_letter_service.list_dlq.side_effect = RuntimeError("Service error")

        # Use .invoke() for sync tool
        result = dlq_list.invoke({
            "project_id": "proj-1",
        })

        assert result == {"error": "Failed to list DLQ items: Service error"}


class TestDlqReplayTool:
    """Tests for dlq_replay tool (SYNC)."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def tools(self, mock_services):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

    @pytest.fixture
    def dlq_replay(self, tools):
        return tools[11]  # dlq_replay is twelfth

    def test_dlq_replay_happy_path(self, mock_services, tools):
        """Happy: replay_from_dlq returns JobItem → 'DLQ entry {dlq_id} replayed successfully. Job {job_item.job_id} is now pending.'"""
        _, _, dead_letter_service = mock_services
        dlq_replay = tools[11]

        mock_job_item = MagicMock()
        mock_job_item.job_id = "job-1"
        dead_letter_service.replay_from_dlq.return_value = mock_job_item

        # Use .invoke() for sync tool
        result = dlq_replay.invoke({
            "dlq_id": "dlq-1",
        })

        assert result == "DLQ entry dlq-1 replayed successfully. Job job-1 is now pending."

    def test_dlq_replay_failure(self, mock_services, tools):
        """Error: returns None → 'ERROR: Could not replay DLQ entry {dlq_id}.'"""
        _, _, dead_letter_service = mock_services
        dlq_replay = tools[11]

        dead_letter_service.replay_from_dlq.return_value = None

        # Use .invoke() for sync tool
        result = dlq_replay.invoke({
            "dlq_id": "dlq-1",
        })

        assert result == "ERROR: Could not replay DLQ entry dlq-1."

    def test_dlq_replay_exception(self, mock_services, tools):
        """Error: Exception → 'ERROR: Failed to replay DLQ entry {dlq_id}: ...'}"""
        _, _, dead_letter_service = mock_services
        dlq_replay = tools[11]

        dead_letter_service.replay_from_dlq.side_effect = RuntimeError("Service unavailable")

        # Use .invoke() for sync tool
        result = dlq_replay.invoke({
            "dlq_id": "dlq-1",
        })

        assert result == "ERROR: Failed to replay DLQ entry dlq-1: Service unavailable"


class TestJobContinueTool:
    """Tests for job_continue tool (tool index 12)."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        return job_service, queue_mgmt_service, dead_letter_service

    @pytest.fixture
    def mock_manager(self):
        """Build a manager mock with _instance_repository and enqueue_message."""
        manager = MagicMock()
        instance_repo = MagicMock()
        manager._instance_repository = instance_repo
        manager.enqueue_message = AsyncMock()
        # Phase 2.5 (Task 2.5.8): ``job_continue`` no longer consults
        # ``JobRepository.find_processing_message_jobs_by_instance`` —
        # that method is removed (no MESSAGE ``JobItem`` rows post-D13).
        # The DB-level concurrency gate now lives on
        # ``TaskRepository.has_inflight_task(instance_id)``, returning
        # True when ANY PENDING or RUNNING ``task`` row exists for the
        # instance. Default to False so happy-path tests pass; tests
        # exercising the gate override the return value explicitly.
        task_repo = MagicMock()
        task_repo.has_inflight_task = MagicMock(return_value=False)
        manager._task_repo = task_repo
        return manager

    @pytest.fixture
    def tools(self, mock_services, mock_manager):
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        return create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            manager=mock_manager,
        )

    @pytest.fixture
    def job_continue(self, tools):
        return tools[12]  # job_continue is at index 12 (13th tool, after 12 + job_continue)

    def _make_old_job(self, status="completed", instance_id="inst-1", deleted_at=None):
        """Build a MagicMock standing in for a JobItem returned by job_service.get_job."""
        old_job = MagicMock()
        old_job.status = status
        old_job.instance_id = instance_id
        old_job.deleted_at = deleted_at
        return old_job

    def _make_instance(self, status="running"):
        instance = MagicMock()
        instance.status = status
        return instance

    def _mock_happy_path(self, job_service, mock_manager, *, instance_status="running"):
        """Configure all mocks for the happy path; return the new_job_id used."""
        old_job = self._make_old_job(instance_id="inst-1")
        job_service.get_job.return_value = old_job
        # No in-flight Task rows for this instance (Phase 2.5 gate).
        # The legacy ``find_processing_message_jobs_by_instance`` mock
        # on ``job_service._repository`` is gone — the gate moved to
        # ``TaskRepository.has_inflight_task`` (Task 2.5.8). The default
        # fixture value (False) already satisfies the happy path; this
        # line is explicit so the intent is visible next to the legacy
        # mock that used to live here.
        mock_manager._task_repo.has_inflight_task = MagicMock(return_value=False)
        # Instance is healthy
        instance = self._make_instance(status=instance_status)
        mock_manager._instance_repository.get.return_value = instance
        # enqueue returns an AsyncMessageResult-like object
        from daemon.manager import AsyncMessageResult
        mock_manager.enqueue_message.return_value = AsyncMessageResult(
            message_id="msg-1",
            instance_id="inst-1",
            status="queued",
            job_id="new-job-1",
        )
        return "new-job-1"

    @pytest.mark.asyncio
    async def test_job_continue_happy_path(self, mock_services, mock_manager, tools):
        """Happy: valid completed job, active instance, no zombie jobs → returns new_job_id."""
        job_service, _, _ = mock_services
        job_continue = tools[12]
        new_job_id = self._mock_happy_path(job_service, mock_manager)

        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue working on this",
        })

        assert result == {
            "old_job_id": "old-job-1",
            "instance_id": "inst-1",
            "message_id": "msg-1",
            "new_job_id": new_job_id,
            "status": "queued",
        }
        mock_manager.enqueue_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_job_continue_job_not_found(self, mock_services, tools):
        """Error: job_service.get_job returns None → {'error': 'Job {id} not found'}."""
        job_service, _, _ = mock_services
        job_continue = tools[12]

        job_service.get_job.return_value = None

        result = await job_continue.ainvoke({
            "old_job_id": "missing-job",
            "message": "Continue",
        })

        assert result == {"error": "Job missing-job not found"}

    @pytest.mark.asyncio
    async def test_job_continue_job_not_terminal(self, mock_services, tools):
        """Error: job status is 'processing' (not in TERMINAL_STATES) → error."""
        job_service, _, _ = mock_services
        job_continue = tools[12]

        job_service.get_job.return_value = self._make_old_job(status="processing")

        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue",
        })

        assert "error" in result
        assert "not in a terminal state" in result["error"]
        assert "processing" in result["error"]

    @pytest.mark.asyncio
    async def test_job_continue_job_soft_deleted(self, mock_services, tools):
        """Error: deleted_at is not None → 'has been deleted and cannot be continued'."""
        job_service, _, _ = mock_services
        job_continue = tools[12]

        job_service.get_job.return_value = self._make_old_job(
            status="completed",
            deleted_at=MagicMock(),  # any non-None value
        )

        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue",
        })

        assert result == {"error": "Job old-job-1 has been deleted and cannot be continued"}

    @pytest.mark.asyncio
    async def test_job_continue_instance_terminated(self, mock_services, mock_manager, tools):
        """Error: instance status is 'terminated' → 'Instance is terminated — spawn a new instance instead'."""
        job_service, _, _ = mock_services
        job_continue = tools[12]

        job_service.get_job.return_value = self._make_old_job()
        instance = self._make_instance(status="terminated")
        mock_manager._instance_repository.get.return_value = instance

        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue",
        })

        assert result == {"error": "Instance is terminated — spawn a new instance instead"}
        mock_manager.enqueue_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_job_continue_instance_paused(self, mock_services, mock_manager, tools):
        """Error: instance status is 'paused' → 'Instance is paused — unpause it first'."""
        job_service, _, _ = mock_services
        job_continue = tools[12]

        job_service.get_job.return_value = self._make_old_job()
        instance = self._make_instance(status="paused")
        mock_manager._instance_repository.get.return_value = instance

        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue",
        })

        assert result == {"error": "Instance is paused — unpause it first"}
        mock_manager.enqueue_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_job_continue_manager_is_none(self, mock_services, tools):
        """Error: manager not provided → 'Instance manager not available'."""
        # Build tools without manager
        job_service, queue_mgmt_service, dead_letter_service = mock_services
        tools_no_mgr = create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            # manager omitted → defaults to None
        )
        job_continue = tools_no_mgr[12]

        job_service.get_job.return_value = self._make_old_job()

        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue",
        })

        assert result == {
            "error": "Instance manager not available — job_continue requires manager access"
        }

    @pytest.mark.asyncio
    async def test_job_continue_zombie_processing_job(self, mock_services, mock_manager, tools):
        """Error: a PENDING/RUNNING Task already exists for the instance → 'has a task still in flight'.

        Phase 2.5 (Task 2.5.8): the legacy ``find_processing_message_jobs_by_instance``
        gate was replaced with ``TaskRepository.has_inflight_task`` —
        when the instance has a PENDING or RUNNING ``task`` row, the
        tool returns ``{"error": "Instance ... has a task still in
        flight — wait for it to complete first"}`` and skips the
        enqueue. PAUSED tasks are intentionally NOT counted as
        in-flight (paused is a quiescent state).
        """
        job_service, _, _ = mock_services
        job_continue = tools[12]

        job_service.get_job.return_value = self._make_old_job()
        instance = self._make_instance(status="running")
        mock_manager._instance_repository.get.return_value = instance
        # Phase 2.5 gate: a Task is already driving this instance.
        mock_manager._task_repo.has_inflight_task = MagicMock(return_value=True)

        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue",
        })

        assert "error" in result
        assert "has a task still in flight" in result["error"]
        assert "inst-1" in result["error"]
        # Critical: enqueue should NOT have been called
        mock_manager.enqueue_message.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 (Batch 4a) — resolver-routed tool tests
#
# The flag ``job_service.use_virtual_job_resolver`` switches
# ``job_get`` / ``job_list`` / ``job_cancel`` / ``watch_job`` / ``watch_jobs``
# onto the ``WorkResolverService``-driven path that unifies Task rows
# (worker-pool side) and JobItem rows (dispatch-queue side) under the
# virtual-job surface.
#
# HIGH 2 was a wrong-kind check in ``job_cancel`` that was unreachable
# under the previous all-``use_virtual_job_resolver=False`` test pack.
# These tests exercise the resolver branch for every routed tool so a
# regression of HIGH 2 (or any sibling routing bug) fails loudly.
# ─────────────────────────────────────────────────────────────────────────────


def _make_work_record(work_id, kind, status, *, instance_id=None, project_id=None,
                       agent_id=None, result_summary=None, error=None):
    """Build a WorkRecord-shaped mock with a real ``to_dict()``.

    The tool code calls ``record.to_dict()`` to serialise — using a real
    ``to_dict()`` (rather than a MagicMock return_value) keeps the test
    assertions meaningful and matches the contract documented in
    ``daemon.services.work_resolver.WorkRecord.to_dict``.
    """
    record = MagicMock(name=f"WorkRecord[{work_id[:8]}]")
    record.work_id = work_id
    record.kind = kind
    record.status = status
    record.instance_id = instance_id
    record.project_id = project_id
    record.agent_id = agent_id
    record.result_summary = result_summary
    record.error = error
    record.created_at = None
    record.to_dict = lambda: {
        "work_id": record.work_id,
        "kind": record.kind,
        "status": record.status,
        "instance_id": record.instance_id,
        "project_id": record.project_id,
        "agent_id": record.agent_id,
        "result_summary": record.result_summary,
        "error": record.error,
        "created_at": None,
    }
    return record


class TestResolverRoutedTools:
    """Tests for the ``use_virtual_job_resolver=True`` branch in
    ``job_get`` / ``job_list`` / ``job_cancel`` / ``watch_job``.

    These tests run with the resolver flag **ON**, exercising the
    ``WorkResolverService``-driven paths. The complementary
    ``use_virtual_job_resolver=False`` tests live in the per-tool
    classes above; together they cover the full kill-switch surface.
    """

    # ── job_get — resolver path ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_job_get_resolver_path_job(self):
        """``job_get`` with flag ON returns the WorkRecord dict for a
        JobItem (``kind="job"``). ``job_service.get_work`` is the
        resolver-aware lookup; ``to_dict()`` is the canonical
        serialiser both routers and MCP tools share.
        """
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = True
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(job_service, queue_mgmt_service, dead_letter_service)
        job_get = tools[1]

        work_id = "job-abcdef12-3456"
        record = _make_work_record(
            work_id, kind="job", status="pending",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        result = await job_get.ainvoke({"job_id": work_id})

        assert result == record.to_dict()
        assert result["kind"] == "job"
        job_service.get_work.assert_awaited_once_with(work_id)
        # Legacy path must NOT have been called
        job_service.get_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_job_get_resolver_path_task(self):
        """``job_get`` with flag ON returns the WorkRecord dict for a
        Task (``kind="turn"``). Verifies the resolver path resolves
        worker-pool rows, not just JobItems.
        """
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = True
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(job_service, queue_mgmt_service, dead_letter_service)
        job_get = tools[1]

        work_id = "task-abcdef12-3456"
        record = _make_work_record(
            work_id, kind="turn", status="running",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
            error=None,
        )
        job_service.get_work = AsyncMock(return_value=record)

        result = await job_get.ainvoke({"job_id": work_id})

        assert result == record.to_dict()
        assert result["kind"] == "turn"
        assert result["status"] == "running"
        job_service.get_work.assert_awaited_once_with(work_id)
        job_service.get_job.assert_not_called()

    # ── job_list — resolver path ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_job_list_resolver_path(self):
        """``job_list`` with flag ON calls ``work_resolver.list_work``
        and returns the union of jobs + tasks as ``{"jobs": [...],
        "count": N}``.

        ``list_work`` is invoked synchronously via
        ``asyncio.to_thread`` — so the resolver's ``list_work`` must be
        a sync callable. Use a plain ``MagicMock`` (NOT ``AsyncMock``)
        and assert via ``assert_called_once_with`` rather than
        ``assert_awaited_*``.
        """
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = True
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        # The resolver is fetched via ``getattr(job_service,
        # "_work_resolver", None)`` so plain attribute assignment works.
        work_resolver = MagicMock(name="WorkResolverService")
        job_service._work_resolver = work_resolver

        tools = create_job_tools(job_service, queue_mgmt_service, dead_letter_service)
        job_list = tools[2]

        rec_job = _make_work_record(
            "job-aaaa1111", kind="job", status="pending",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        rec_task = _make_work_record(
            "task-bbbb2222", kind="turn", status="running",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        work_resolver.list_work = MagicMock(return_value=[rec_job, rec_task])

        result = await job_list.ainvoke({})

        assert result["count"] == 2
        assert result["jobs"] == [rec_job.to_dict(), rec_task.to_dict()]
        work_resolver.list_work.assert_called_once()
        # Legacy list_jobs must NOT have been called
        job_service.list_jobs.assert_not_called()

    # ── job_cancel — resolver path (HIGH 2 regression guard) ──────────

    @pytest.mark.asyncio
    async def test_job_cancel_resolver_path_task(self):
        """``job_cancel`` with flag ON on a Task (``kind="turn"``)
        goes through the **cooperative** ``task_repo.request_cancel``
        path — NOT ``cancel_job``. Returns the cooperative-cancel
        message that documents the asynchronous semantics.

        THIS IS THE HIGH 2 REGRESSION GUARD: pre-fix the tool used
        ``record.kind != "task"`` but the resolver emits
        ``kind="turn"`` / ``kind="report"``, so every Task cancel was
        routed into ``cancel_job`` (instant atomic cancel that does
        nothing for worker-pool rows). The corrected check is
        ``record.kind != "job"``.
        """
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = True
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        manager = MagicMock(name="Manager")
        task_repo = MagicMock(name="TaskRepo")
        manager._task_repo = task_repo
        tools = create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            manager=manager,
        )
        job_cancel = tools[3]

        work_id = "task-abcdef12-3456"
        record = _make_work_record(
            work_id, kind="turn", status="running",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        # ``get_by_work_id`` and ``request_cancel`` are both invoked
        # synchronously via ``asyncio.to_thread``, so plain MagicMocks
        # (NOT AsyncMock) — see ``daemon/tools/job_queue.py:512-522``.
        task_row = MagicMock(name="TaskRow")
        task_row.id = 42
        task_repo.get_by_work_id = MagicMock(return_value=task_row)
        task_repo.request_cancel = MagicMock(return_value=True)

        result = await job_cancel.ainvoke({"job_id": work_id})

        assert "Cancel requested" in result
        assert "cooperative" in result
        # CRITICAL: instant atomic cancel path must NOT have been taken
        job_service.cancel_job.assert_not_called()
        task_repo.get_by_work_id.assert_called_once_with(work_id)
        task_repo.request_cancel.assert_called_once_with(42)

    @pytest.mark.asyncio
    async def test_job_cancel_resolver_path_job(self):
        """``job_cancel`` with flag ON on a JobItem (``kind="job"``)
        goes through the instant atomic ``cancel_job`` path and
        returns the success message.
        """
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = True
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        # ``manager`` is passed so the cooperative-path guard succeeds;
        # it must NOT be exercised on the JobItem branch.
        manager = MagicMock(name="Manager")
        manager._task_repo = MagicMock(name="TaskRepo")
        tools = create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            manager=manager,
        )
        job_cancel = tools[3]

        work_id = "job-abcdef12-3456"
        record = _make_work_record(
            work_id, kind="job", status="pending",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        job_service.cancel_job = AsyncMock(return_value=True)

        result = await job_cancel.ainvoke({"job_id": work_id})

        assert result == f"Job {work_id} cancelled successfully."
        job_service.cancel_job.assert_awaited_once_with(work_id)
        # Cooperative task path must NOT have been taken
        manager._task_repo.get_by_work_id.assert_not_called()
        manager._task_repo.request_cancel.assert_not_called()

    # ── watch_job — resolver path ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_watch_job_resolver_path(self):
        """``watch_job`` with flag ON registers a watch via
        ``service.get_work`` + ``watcher_repo.add_watch`` on a
        non-terminal WorkRecord. The watch should be registered
        exactly once for the supplied work_id.
        """
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = True
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        watcher_repo = MagicMock(name="JobWatcherRepository")
        watcher_repo.count_watches_for_instance = MagicMock(return_value=0)
        watcher_repo.add_watch = MagicMock(return_value=MagicMock(name="JobWatcher"))
        tools = create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            current_instance_id="watcher-inst-1",
            watcher_repo=watcher_repo,
        )
        watch_job = tools[13]

        work_id = "job-abcdef12-3456"
        record = _make_work_record(
            work_id, kind="job", status="processing",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        result = await watch_job.ainvoke({"job_id": work_id})

        assert "Watch registered" in result
        # Resolver path was used (not the legacy get_job path)
        job_service.get_work.assert_awaited_once_with(work_id)
        job_service.get_job.assert_not_called()
        # Watch was registered against the supplied work_id
        watcher_repo.add_watch.assert_called_once()
        call_args = watcher_repo.add_watch.call_args
        assert call_args.args[0] == work_id
        assert call_args.args[1] == "watcher-inst-1"
        # notify_watchers should NOT fire on a non-terminal record
        job_service.notify_watchers.assert_not_called()
