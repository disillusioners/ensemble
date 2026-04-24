"""Comprehensive tests for job queue tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.tools.job_queue import create_job_tools
from daemon.tools._tool_registry import CATEGORY_MODULES


class TestJobQueueToolRegistration:
    """Tests for tool registration."""

    def test_create_job_tools_returns_16_tools(self):
        """Verify create_job_tools returns exactly 16 tools."""
        job_service = AsyncMock()
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()

        tools = create_job_tools(job_service, queue_mgmt_service, dead_letter_service)

        assert len(tools) == 16

    def test_each_tool_has_job_category(self):
        """Verify each tool has _tool_category == 'job' attribute."""
        job_service = AsyncMock()
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
            "job_id": "job-1", "status": "pending", "agent_id": "coder",
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
            "agent_id": "coder",
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
            "agent_id": "coder",
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
            "agent_id": "coder",
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
            "agent_id": "coder",
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
            "agent_id": "coder",
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
