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
        """Happy: returns record.to_dict()."""
        job_service, _, _ = mock_services
        job_get = tools[1]

        # Phase 7: production calls ``job_service.get_work`` (resolver-
        # aware lookup) — NOT the legacy ``get_job``. The record's
        # ``to_dict()`` is the canonical serializer that drives the tool
        # return value, so we stub it to return the expected dict
        # directly. ``MagicMock.to_dict.return_value = {...}`` makes
        # ``record.to_dict()`` equal that dict (rather than a fresh
        # MagicMock) so the equality assertion below passes.
        record = MagicMock()
        expected_dict = {"job_id": "job-1", "status": "pending"}
        record.to_dict.return_value = expected_dict
        job_service.get_work = AsyncMock(return_value=record)

        result = await job_get.ainvoke({"job_id": "job-1"})

        assert result == expected_dict

    @pytest.mark.asyncio
    async def test_job_get_not_found(self, mock_services, tools):
        """Error: service returns None → {'error': 'Job not found'}."""
        job_service, _, _ = mock_services
        job_get = tools[1]

        # Phase 7: production calls ``get_work``; None means the
        # resolver could not resolve the work_id (either no JobItem,
        # no Task, or a phantom id that no longer exists).
        job_service.get_work = AsyncMock(return_value=None)

        result = await job_get.ainvoke({"job_id": "job-1"})

        assert result == {"error": "Job not found"}

    @pytest.mark.asyncio
    async def test_job_get_exception(self, mock_services, tools):
        """Error: Exception → {'error': 'Failed to get job: ...'}."""
        job_service, _, _ = mock_services
        job_get = tools[1]

        # Phase 7: production calls ``get_work``. A side-effect on
        # the AsyncMock surfaces as the awaited call's exception,
        # which the tool catches into the error dict.
        job_service.get_work = AsyncMock(side_effect=RuntimeError("Database error"))

        result = await job_get.ainvoke({"job_id": "job-1"})

        assert result == {"error": "Failed to get job: Database error"}


class TestJobListTool:
    """Tests for job_list tool."""

    @pytest.fixture
    def mock_services(self):
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = False
        # Phase 7: ``job_list`` consults ``job_service._work_resolver``
        # via ``getattr(..., None)``. On a bare ``AsyncMock()`` that
        # ``getattr`` auto-creates an AsyncMock child (the ``default``
        # argument is only used if the attribute would otherwise raise
        # AttributeError — Mock's ``__getattr__`` swallows it and returns
        # a fresh child). Setting ``_work_resolver = None`` here routes
        # the tool through the legacy ``list_jobs`` fallback that these
        # tests stub. Without this, the resolver path takes over and
        # tries ``work_resolver.list_work`` (an AsyncMock) — which
        # returns a MagicMock and the assertions don't match.
        job_service._work_resolver = None
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

        # Phase 7: production resolves the work_id via ``get_work``
        # FIRST to choose between the cooperative task-cancel and the
        # instant JobItem-atomic-cancel branches. Returning a record
        # with ``kind="job"`` routes the call into the legacy
        # ``cancel_job`` path that this test is exercising. Without
        # this, the bare AsyncMock's auto-attribute ``record.kind``
        # is a MagicMock (truthy and ``!= "job"``), so production
        # takes the task-cancel branch and returns the cooperative
        # "Task repository not available" error instead.
        record = _make_work_record(
            "job-1", kind="job", status="processing",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        job_service.cancel_job = AsyncMock(return_value=True)

        result = await job_cancel.ainvoke({"job_id": "job-1"})

        assert result == "Job job-1 cancelled successfully."

    @pytest.mark.asyncio
    async def test_job_cancel_failure(self, mock_services, tools):
        """Error: cancel_job returns False → 'ERROR: Could not cancel job {job_id}. Job may not be in a cancellable state.'"""
        job_service, _, _ = mock_services
        job_cancel = tools[3]

        # See ``test_job_cancel_success`` for why ``get_work`` is
        # stubbed with a ``kind="job"`` record (production takes the
        # ``cancel_job`` branch only for JobItem work_ids).
        record = _make_work_record(
            "job-1", kind="job", status="processing",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        job_service.cancel_job = AsyncMock(return_value=False)

        result = await job_cancel.ainvoke({"job_id": "job-1"})

        assert result == "ERROR: Could not cancel job job-1. Job may not be in a cancellable state."

    @pytest.mark.asyncio
    async def test_job_cancel_exception(self, mock_services, tools):
        """Error: Exception → 'ERROR: Failed to cancel job {job_id}: ...'"""
        job_service, _, _ = mock_services
        job_cancel = tools[3]

        # See ``test_job_cancel_success`` for why ``get_work`` is
        # stubbed with a ``kind="job"`` record.
        record = _make_work_record(
            "job-1", kind="job", status="processing",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        job_service.cancel_job = AsyncMock(side_effect=RuntimeError("Service unavailable"))

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

        # Phase 7 P-D guard: production calls ``get_work`` first to
        # classify the work (JobItem vs Task). A ``kind="job"`` record
        # routes into the legacy ``retry_job`` path; a ``kind != "job"``
        # record returns the precise "no retry path for task-type
        # work" error instead. See TestJobCancelTool for the same
        # rationale.
        record = _make_work_record(
            "job-1", kind="job", status="failed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        mock_job_item = MagicMock()
        job_service.retry_job = AsyncMock(return_value=mock_job_item)

        result = await job_retry.ainvoke({"job_id": "job-1"})

        assert result == "Job job-1 retry initiated successfully."
        # Ensure new job_id is NOT mentioned in the success message
        assert "new" not in result.lower()

    @pytest.mark.asyncio
    async def test_job_retry_failure(self, mock_services, tools):
        """Error: retry_job returns None → 'ERROR: Could not retry job {job_id}. Job may not be in a retryable state.'"""
        job_service, _, _ = mock_services
        job_retry = tools[4]

        # See ``test_job_retry_success`` for the kind="job" rationale.
        record = _make_work_record(
            "job-1", kind="job", status="failed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        job_service.retry_job = AsyncMock(return_value=None)

        result = await job_retry.ainvoke({"job_id": "job-1"})

        assert result == "ERROR: Could not retry job job-1. Job may not be in a retryable state."

    @pytest.mark.asyncio
    async def test_job_retry_exception(self, mock_services, tools):
        """Error: Exception → 'ERROR: Failed to retry job {job_id}: ...'"""
        job_service, _, _ = mock_services
        job_retry = tools[4]

        # See ``test_job_retry_success`` for the kind="job" rationale.
        record = _make_work_record(
            "job-1", kind="job", status="failed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        job_service.retry_job = AsyncMock(side_effect=RuntimeError("Service error"))

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

        # Phase 7 P-D guard: production calls ``get_work`` first;
        # ``kind="job"`` routes into the legacy ``soft_delete_job``
        # path. See TestJobCancelTool for the same pattern.
        record = _make_work_record(
            "job-1", kind="job", status="completed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        mock_job_item = MagicMock()
        job_service.soft_delete_job = AsyncMock(return_value=mock_job_item)

        result = await job_delete.ainvoke({"job_id": "job-1"})

        assert result == "Job job-1 deleted successfully."

    @pytest.mark.asyncio
    async def test_job_delete_failure(self, mock_services, tools):
        """Error: returns None → 'ERROR: Could not delete job {job_id}. Job may not exist.'"""
        job_service, _, _ = mock_services
        job_delete = tools[5]

        # See ``test_job_delete_success`` for the kind="job" rationale.
        record = _make_work_record(
            "job-1", kind="job", status="completed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        job_service.soft_delete_job = AsyncMock(return_value=None)

        result = await job_delete.ainvoke({"job_id": "job-1"})

        assert result == "ERROR: Could not delete job job-1. Job may not exist."

    @pytest.mark.asyncio
    async def test_job_delete_exception(self, mock_services, tools):
        """Error: Exception → 'ERROR: Failed to delete job {job_id}: ...'"""
        job_service, _, _ = mock_services
        job_delete = tools[5]

        # See ``test_job_delete_success`` for the kind="job" rationale.
        record = _make_work_record(
            "job-1", kind="job", status="completed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        job_service.soft_delete_job = AsyncMock(side_effect=RuntimeError("Database error"))

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

        # Phase 7 P-D guard: production calls ``get_work`` first;
        # ``kind="job"`` routes into the legacy ``restore_job`` path.
        record = _make_work_record(
            "job-1", kind="job", status="completed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        mock_job_item = MagicMock()
        job_service.restore_job = AsyncMock(return_value=mock_job_item)

        result = await job_restore.ainvoke({"job_id": "job-1"})

        assert result == "Job job-1 restored successfully."

    @pytest.mark.asyncio
    async def test_job_restore_failure(self, mock_services, tools):
        """Error: returns None → 'ERROR: Could not restore job {job_id}. Job may not exist or may not be deleted.'"""
        job_service, _, _ = mock_services
        job_restore = tools[6]

        # See ``test_job_restore_success`` for the kind="job" rationale.
        record = _make_work_record(
            "job-1", kind="job", status="completed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        job_service.restore_job = AsyncMock(return_value=None)

        result = await job_restore.ainvoke({"job_id": "job-1"})

        assert result == "ERROR: Could not restore job job-1. Job may not exist or may not be deleted."

    @pytest.mark.asyncio
    async def test_job_restore_exception(self, mock_services, tools):
        """Error: Exception → 'ERROR: Failed to restore job {job_id}: ...'"""
        job_service, _, _ = mock_services
        job_restore = tools[6]

        # See ``test_job_restore_success`` for the kind="job" rationale.
        record = _make_work_record(
            "job-1", kind="job", status="completed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        job_service.restore_job = AsyncMock(side_effect=RuntimeError("Service error"))

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
        """Build a manager mock with _instance_repository and ``enqueue_message_job``.

        Phase 5 (2026-06-27, the message-Job cutover) renamed the
        manager-side hook from ``enqueue_message`` to
        ``enqueue_message_job`` so the public facade can return a
        ``job_id`` alongside the message id (Phase 5's POC declared the
        message-Job item to be a first-class work primitive). The
        ``job_continue`` tool calls ``manager.enqueue_message_job`` —
        NOT ``manager.enqueue_message``. The Phase-2.5 fixture still
        mocked the legacy ``enqueue_message`` name, which leaves
        ``enqueue_message_job`` auto-mocked (returns MagicMock) and
        every continue test fails with a ``KeyError`` on the missing
        ``message_id``/``new_job_id`` keys.
        """
        manager = MagicMock()
        instance_repo = MagicMock()
        manager._instance_repository = instance_repo
        # Phase 5 cutover: ``enqueue_message_job`` is the renamed
        # replacement for ``enqueue_message`` on the public manager
        # facade. ``job_continue`` calls
        # ``manager.enqueue_message_job(instance_id=..., message=...,
        # source=...)`` and expects an ``AsyncMessageResult`` back.
        manager.enqueue_message_job = AsyncMock()
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
        # Phase 7 P-B: the resolver is now ALWAYS the lookup path.
        # ``get_work`` returns a ``kind="job"`` record (the
        # ``status`` and ``instance_id`` flow through the tool's
        # terminal-state check and instance_id extraction). ``get_job``
        # is still called on the ``kind="job"`` branch — it's a cheap
        # follow-up that loads the soft-delete column
        # (``JobItem.deleted_at``) which WorkRecord doesn't carry.
        record = _make_work_record(
            "old-job-1", kind="job", status="completed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        old_job = self._make_old_job(instance_id="inst-1")
        job_service.get_job = AsyncMock(return_value=old_job)
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
        mock_manager._instance_repository.get = MagicMock(return_value=instance)
        # Phase 5 cutover: ``manager.enqueue_message_job`` (renamed
        # from ``enqueue_message``) returns an ``AsyncMessageResult``
        # that carries the new ``job_id`` for the message mirror. The
        # tool uses ``result.message_id``, ``result.job_id``,
        # ``result.status`` — those attributes must be present on
        # the returned object.
        from daemon.manager import AsyncMessageResult
        mock_manager.enqueue_message_job = AsyncMock(return_value=AsyncMessageResult(
            message_id="msg-1",
            instance_id="inst-1",
            status="queued",
            job_id="new-job-1",
        ))
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
        mock_manager.enqueue_message_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_job_continue_job_not_found(self, mock_services, tools):
        """Error: job_service.get_work returns None → {'error': 'Job {id} not found'}."""
        job_service, _, _ = mock_services
        job_continue = tools[12]

        # Phase 7 P-B: the resolver is ALWAYS the lookup. None means
        # the work_id is unknown (no JobItem, no Task). The follow-up
        # ``get_job`` is irrelevant on the not-found branch — the
        # tool returns before reaching it.
        job_service.get_work = AsyncMock(return_value=None)

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

        # Phase 7 P-B: the terminal check is on the WorkRecord's
        # status (``record.status``), not on the legacy
        # ``JobItem.status``. ``processing`` is not in
        # ``TERMINAL_STATES`` so the tool returns the precise
        # "not in a terminal state" error. Because ``record.kind``
        # is ``"job"``, production enters the ``get_job`` follow-up
        # for the soft-delete guard FIRST — so we stub ``get_job``
        # to return an undeleted JobItem (``deleted_at=None``) to
        # let the test reach the terminal check rather than
        # aborting on the soft-delete path.
        record = _make_work_record(
            "old-job-1", kind="job", status="processing",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        job_service.get_job = AsyncMock(return_value=self._make_old_job(
            status="processing",
            deleted_at=None,
        ))

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

        # Phase 7 P-B: resolver returns a kind="job" record, then
        # production makes the follow-up ``get_job`` call purely to
        # check ``JobItem.deleted_at``. A non-None ``deleted_at`` makes
        # the tool return the precise "has been deleted" error
        # without invoking the manager.
        record = _make_work_record(
            "old-job-1", kind="job", status="completed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        job_service.get_job = AsyncMock(return_value=self._make_old_job(
            status="completed",
            deleted_at=MagicMock(),  # any non-None value
        ))

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

        # Phase 7 P-B: resolver first, then follow-up ``get_job`` for
        # the soft-delete column. The terminal-state and deleted_at
        # checks both pass; the error comes from the instance-status
        # guard inside the manager path.
        record = _make_work_record(
            "old-job-1", kind="job", status="completed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        job_service.get_job = AsyncMock(return_value=self._make_old_job())
        instance = self._make_instance(status="terminated")
        mock_manager._instance_repository.get = MagicMock(return_value=instance)

        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue",
        })

        assert result == {"error": "Instance is terminated — spawn a new instance instead"}
        mock_manager.enqueue_message_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_job_continue_instance_paused(self, mock_services, mock_manager, tools):
        """Error: instance status is 'paused' → 'Instance is paused — unpause it first'."""
        job_service, _, _ = mock_services
        job_continue = tools[12]

        # See ``test_job_continue_instance_terminated`` for the
        # general setup. The pre-check on ``InstanceStatus.PAUSED``
        # short-circuits before the in-flight Task check.
        record = _make_work_record(
            "old-job-1", kind="job", status="completed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        job_service.get_job = AsyncMock(return_value=self._make_old_job())
        instance = self._make_instance(status="paused")
        mock_manager._instance_repository.get = MagicMock(return_value=instance)

        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue",
        })

        assert result == {"error": "Instance is paused — unpause it first"}
        mock_manager.enqueue_message_job.assert_not_awaited()

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

        # Phase 7 P-B: resolver returns a kind="job" terminal record.
        # Because the record is ``kind="job"``, production enters
        # the soft-delete follow-up first and would short-circuit
        # with "has been deleted" against an auto-mocked
        # ``get_job`` (whose ``deleted_at`` defaults to a MagicMock).
        # Stub ``get_job`` to return an undeleted JobItem so the
        # test reaches the manager-is-None guard.
        record = _make_work_record(
            "old-job-1", kind="job", status="completed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        job_service.get_job = AsyncMock(return_value=self._make_old_job(
            deleted_at=None,
        ))

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

        # Phase 7 P-B: resolver returns a kind="job" terminal record,
        # the follow-up ``get_job`` returns an undeleted JobItem, the
        # instance is running. The only thing standing between us
        # and ``enqueue_message_job`` is the Phase 2.5 in-flight Task
        # gate — which the test flips to True below.
        record = _make_work_record(
            "old-job-1", kind="job", status="completed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        job_service.get_job = AsyncMock(return_value=self._make_old_job())
        instance = self._make_instance(status="running")
        mock_manager._instance_repository.get = MagicMock(return_value=instance)
        # Phase 2.5 gate: a Task is already driving this instance.
        mock_manager._task_repo.has_inflight_task = MagicMock(return_value=True)

        result = await job_continue.ainvoke({
            "old_job_id": "old-job-1",
            "message": "Continue",
        })

        assert "error" in result
        assert "has a task still in flight" in result["error"]
        assert "inst-1" in result["error"]
        # Critical: enqueue should NOT have been called.
        # Phase 5 rename: production calls ``enqueue_message_job``.
        mock_manager.enqueue_message_job.assert_not_awaited()


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
        Task (``kind="report"``). Verifies the resolver path resolves
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
            work_id, kind="report", status="running",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
            error=None,
        )
        job_service.get_work = AsyncMock(return_value=record)

        result = await job_get.ainvoke({"job_id": work_id})

        assert result == record.to_dict()
        assert result["kind"] == "report"
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
            "task-bbbb2222", kind="report", status="running",
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
        """``job_cancel`` with flag ON on a Task (``kind="report"``)
        goes through the **cooperative** ``task_repo.request_cancel``
        path — NOT ``cancel_job``. Returns the cooperative-cancel
        message that documents the asynchronous semantics.

        THIS IS THE HIGH 2 REGRESSION GUARD: pre-fix the tool used
        ``record.kind != "task"`` but the resolver emits
        ``kind="report"`` (Tasks are reports post-Phase 4), so every
        Task cancel was routed into ``cancel_job`` (instant atomic
        cancel that does nothing for worker-pool rows). The
        corrected check is ``record.kind != "job"``.
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
            work_id, kind="report", status="running",
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


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 (P-A / P-B / P-D) tests for the virtual-job tool completeness
# hardening. See ``docs/plans/virtual-job-tool-completeness.md`` §6.
#
# P-A — ``job_list`` resolver path passes ``root_only=True`` (tool layer).
# P-B — ``job_continue`` resolves both task and job work_ids (resolver-
#       aware lookup).
# P-D — ``job_retry`` / ``job_delete`` / ``job_restore`` return a
#       precise "not applicable for task-type work" message for task
#       work_ids under the resolver flag.
#
# All tests run with ``use_virtual_job_resolver=True`` so the
# resolver-aware branches are exercised. The kill-switch
# (``use_virtual_job_resolver=False``) tests live in the per-tool
# classes above.
# ─────────────────────────────────────────────────────────────────────────────


class TestJobListRootScoping:
    """P-A tool-layer: ``job_list`` with the resolver flag ON passes
    ``root_only=True`` to ``work_resolver.list_work`` so the jober's
    management view is root-scoped (child-instance work filtered out).

    Unit-level proof of the tool-layer change: we mock
    ``work_resolver.list_work`` and verify the ``root_only=True`` kwarg
    is passed. The actual filtering lives inside ``list_work``
    (Phase 5 P-A, ``daemon/services/work_resolver.py``) — tested
    separately by the integration suite.
    """

    @pytest.mark.asyncio
    async def test_job_list_resolver_excludes_children(self):
        """``job_list`` resolver path calls ``list_work`` with
        ``root_only=True``. The MagicMock accepts the kwarg; the
        assertion is on the contract (the resolver receives the
        filter so it can drop child-instance rows).
        """
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = True
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        work_resolver = MagicMock(name="WorkResolverService")
        job_service._work_resolver = work_resolver

        # Return only root-scoped rows — the resolver would have
        # filtered them out before returning. This mirrors the
        # production behavior so the tool output is realistic.
        rec_root = _make_work_record(
            "job-aaaa1111", kind="job", status="pending",
            instance_id="root-inst-1", project_id="proj-1", agent_id="developer",
        )
        work_resolver.list_work = MagicMock(return_value=[rec_root])

        tools = create_job_tools(job_service, queue_mgmt_service, dead_letter_service)
        job_list = tools[2]

        result = await job_list.ainvoke({})

        # The tool must have called list_work with root_only=True
        work_resolver.list_work.assert_called_once()
        call_kwargs = work_resolver.list_work.call_args.kwargs
        assert call_kwargs.get("root_only") is True, (
            "job_list must pass root_only=True to work_resolver.list_work "
            "so the jober's management view excludes child-instance work"
        )
        # And the result reflects the (already filtered) records
        assert result["count"] == 1
        assert result["jobs"][0]["work_id"] == "job-aaaa1111"
        # Legacy list_jobs must NOT have been called
        job_service.list_jobs.assert_not_called()


class TestJobRetryDeleteRestoreTaskKindMessage:
    """P-D: ``job_retry`` / ``job_delete`` / ``job_restore`` return a
    precise "not applicable for task-type work" message when the
    resolver resolves a task work_id (``kind != "job"``).

    Before P-D, these tools called ``job_service.retry_job`` /
    ``soft_delete_job`` / ``restore_job`` directly with the work_id
    — JobItem-only lookups — so a task work_id produced the generic
    "may not exist / not be retryable" message. P-D adds a
    resolve-then-classify guard so the caller knows the work is a
    task and that the operation isn't applicable by design.
    """

    @staticmethod
    def _build_resolver_service(task_work_id="task-aaaa1111", kind="report",
                                  instance_id="inst-1"):
        """Build a job_service mock where the resolver returns a
        task-kind WorkRecord and the legacy retry/delete/restore path
        would have raised a confusing generic error.
        """
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = True
        # Resolver returns a task-kind record (P-D guard should fire)
        record = _make_work_record(
            task_work_id, kind=kind, status="completed",
            instance_id=instance_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        # Legacy path should NOT be called when P-D guard fires
        job_service.retry_job = AsyncMock(return_value=None)
        job_service.soft_delete_job = AsyncMock(return_value=None)
        job_service.restore_job = AsyncMock(return_value=None)
        return job_service

    @pytest.mark.asyncio
    async def test_job_retry_task_kind_message(self):
        """``job_retry`` against a task ``work_id`` returns the
        precise "no retry path" message and does NOT call the
        JobItem-only ``retry_job`` path.
        """
        job_service = self._build_resolver_service(
            task_work_id="task-abcdef12-3456", kind="report"
        )
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(job_service, queue_mgmt_service, dead_letter_service)
        job_retry = tools[4]

        result = await job_retry.ainvoke({"job_id": "task-abcdef12-3456"})

        # P-D message format (matches other "ERROR: ..." prefixes in the file)
        assert result.startswith("ERROR:")
        assert "task-abc" in result  # 8-char prefix slice ([:8])
        assert "task-type work" in result
        assert "report" in result  # the kind name (post-Phase 4 Tasks are reports)
        assert "retry path" in result
        # The legacy JobItem path must NOT have been called
        job_service.retry_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_job_delete_task_kind_message(self):
        """``job_delete`` against a task ``work_id`` returns the
        precise "no delete path" message and does NOT call the
        JobItem-only ``soft_delete_job`` path.
        """
        job_service = self._build_resolver_service(
            task_work_id="task-abcdef12-3456", kind="report"
        )
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(job_service, queue_mgmt_service, dead_letter_service)
        job_delete = tools[5]

        result = await job_delete.ainvoke({"job_id": "task-abcdef12-3456"})

        assert result.startswith("ERROR:")
        assert "task-abc" in result
        assert "task-type work" in result
        assert "report" in result
        assert "delete path" in result
        job_service.soft_delete_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_job_restore_task_kind_message(self):
        """``job_restore`` against a task ``work_id`` returns the
        precise "no restore path" message and does NOT call the
        JobItem-only ``restore_job`` path.
        """
        job_service = self._build_resolver_service(
            task_work_id="task-abcdef12-3456", kind="report"
        )
        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(job_service, queue_mgmt_service, dead_letter_service)
        job_restore = tools[6]

        result = await job_restore.ainvoke({"job_id": "task-abcdef12-3456"})

        assert result.startswith("ERROR:")
        assert "task-abc" in result
        assert "task-type work" in result
        assert "report" in result  # the kind name (post-Phase 4 Tasks are reports)
        assert "restore path" in result
        job_service.restore_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_job_retry_job_kind_falls_through(self):
        """P-D regression: when the resolver returns a job-kind
        record, the tool falls through to the legacy ``retry_job``
        path. Verifies the P-D guard only fires for ``kind != "job"``.
        """
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = True
        record = _make_work_record(
            "job-aaaa1111", kind="job", status="failed",
            instance_id="inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        mock_job_item = MagicMock()
        job_service.retry_job = AsyncMock(return_value=mock_job_item)

        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(job_service, queue_mgmt_service, dead_letter_service)
        job_retry = tools[4]

        result = await job_retry.ainvoke({"job_id": "job-aaaa1111"})

        # Falls through to retry_job, returns success
        assert result == "Job job-aaaa1111 retry initiated successfully."
        job_service.retry_job.assert_awaited_once_with("job-aaaa1111")

    @pytest.mark.asyncio
    async def test_job_retry_resolver_returns_none_falls_through(self):
        """P-D regression: when ``get_work`` returns ``None``
        (legacy id, flag off, or genuinely a job), the tool falls
        through to the legacy ``retry_job`` path — the precise error
        is only for confirmed task-kind work.
        """
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = True
        job_service.get_work = AsyncMock(return_value=None)  # not found by resolver
        # Legacy path returns None too → generic error
        job_service.retry_job = AsyncMock(return_value=None)

        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(job_service, queue_mgmt_service, dead_letter_service)
        job_retry = tools[4]

        result = await job_retry.ainvoke({"job_id": "missing-job-1"})

        # Legacy verbatim behavior — generic "may not be in a retryable state"
        assert result == "ERROR: Could not retry job missing-job-1. Job may not be in a retryable state."
        job_service.retry_job.assert_awaited_once_with("missing-job-1")


class TestJobContinueResolverAware:
    """P-B: ``job_continue`` resolves task work_ids through the
    resolver so the typical jober round-trip
    (``job_continue`` → returns ``new_job_id`` (task work_id) →
    ``job_continue`` again) works without "Job not found" errors.

    The pre-P-B implementation called ``job_service.get_job`` which is
    JobItem-only — task work_ids (the typical handle the jober holds
    for continued-instance work) returned "Job not found". The
    rewrite resolves via ``job_service.get_work`` (kind-agnostic) and
    routes the rest of the validation by ``record.kind``.
    """

    @staticmethod
    def _build_continue_services(*, instance_id="root-inst-1"):
        """Build a (job_service, manager) pair ready for the happy
        path under ``use_virtual_job_resolver=True``.
        """
        job_service = AsyncMock()
        job_service.use_virtual_job_resolver = True
        manager = MagicMock()
        instance_repo = MagicMock()
        manager._instance_repository = instance_repo
        # Phase 5 cutover: the manager-side hook is named
        # ``enqueue_message_job`` (NOT ``enqueue_message``) so the
        # public facade can return a ``job_id`` alongside the message
        # id. See ``mock_manager`` fixture in TestJobContinueTool for
        # the same fix.
        manager.enqueue_message_job = AsyncMock()
        task_repo = MagicMock()
        task_repo.has_inflight_task = MagicMock(return_value=False)
        manager._task_repo = task_repo
        return job_service, manager

    @pytest.mark.asyncio
    async def test_job_continue_from_task_work_id(self):
        """The D14 test #9 gap: ``job_continue`` accepts a task
        ``work_id`` (the handle ``job_continue`` itself returned as
        ``new_job_id`` on the prior call) and resolves the instance
        through the WorkRecord. Asserts the resolver path is used
        (NOT the legacy ``get_job`` lookup) and that the returned
        ``instance_id`` matches the WorkRecord's instance_id
        (reviewer finding — must equal the original root instance).
        """
        from daemon.manager import AsyncMessageResult

        job_service, manager = self._build_continue_services(instance_id="root-inst-1")
        task_work_id = "task-abcdef12-3456"
        record = _make_work_record(
            task_work_id, kind="report", status="completed",
            instance_id="root-inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        # No follow-up get_job call needed — task-kind skips the
        # soft-delete guard (no deleted_at on tasks).
        job_service.get_job = AsyncMock(return_value=None)

        # Instance healthy
        instance_meta = MagicMock()
        instance_meta.status = "running"
        manager._instance_repository.get.return_value = instance_meta
        manager.enqueue_message_job.return_value = AsyncMessageResult(
            message_id="msg-2",
            instance_id="root-inst-1",
            status="queued",
            job_id="task-zzzz9999",  # the new turn's work_id (task-shaped)
        )

        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            manager=manager,
        )
        job_continue = tools[12]

        result = await job_continue.ainvoke({
            "old_job_id": task_work_id,
            "message": "Continue working on this",
        })

        # Resolver path was used
        job_service.get_work.assert_awaited_once_with(task_work_id)
        # CRITICAL: returned instance_id matches the WorkRecord's
        # instance_id (the original root, not a phantom or the child)
        assert result["instance_id"] == "root-inst-1"
        assert result["old_job_id"] == task_work_id
        assert result["new_job_id"] == "task-zzzz9999"
        assert result["status"] == "queued"
        manager.enqueue_message_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_job_continue_from_job_work_id(self):
        """Regression: ``job_continue`` against a legacy JobItem
        ``work_id`` (``kind="job"``) still works. The P-B rewrite
        must not break the legacy path — it adds a branch where
        ``get_job`` is called for the soft-delete check, but the
        end-to-end behavior is preserved.
        """
        from daemon.manager import AsyncMessageResult

        job_service, manager = self._build_continue_services(instance_id="root-inst-1")
        job_work_id = "job-abcdef12-3456"
        record = _make_work_record(
            job_work_id, kind="job", status="completed",
            instance_id="root-inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        # JobItem exists with no deleted_at (legacy path proceeds)
        old_job = MagicMock()
        old_job.deleted_at = None
        job_service.get_job = AsyncMock(return_value=old_job)

        instance_meta = MagicMock()
        instance_meta.status = "running"
        manager._instance_repository.get.return_value = instance_meta
        manager.enqueue_message_job.return_value = AsyncMessageResult(
            message_id="msg-2",
            instance_id="root-inst-1",
            status="queued",
            job_id="task-zzzz9999",
        )

        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            manager=manager,
        )
        job_continue = tools[12]

        result = await job_continue.ainvoke({
            "old_job_id": job_work_id,
            "message": "Continue",
        })

        # Resolver then legacy get_job for deleted_at check
        job_service.get_work.assert_awaited_once_with(job_work_id)
        job_service.get_job.assert_awaited_once_with(job_work_id)
        assert result["instance_id"] == "root-inst-1"
        manager.enqueue_message_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_job_continue_soft_deleted_job_rejected(self):
        """``kind="job"`` + ``deleted_at`` set → rejected with the
        "has been deleted and cannot be continued" message. The P-B
        rewrite must check ``deleted_at`` on the JobItem branch even
        though ``WorkRecord`` doesn't carry the column.
        """
        job_service, manager = self._build_continue_services()
        job_work_id = "job-abcdef12-3456"
        record = _make_work_record(
            job_work_id, kind="job", status="completed",
            instance_id="root-inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        # JobItem exists with deleted_at SET (soft-deleted)
        old_job = MagicMock()
        old_job.deleted_at = MagicMock()  # any non-None value
        job_service.get_job = AsyncMock(return_value=old_job)

        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            manager=manager,
        )
        job_continue = tools[12]

        result = await job_continue.ainvoke({
            "old_job_id": job_work_id,
            "message": "Continue",
        })

        assert result == {"error": f"Job {job_work_id} has been deleted and cannot be continued"}
        # CRITICAL: enqueue must NOT have been called
        manager.enqueue_message_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_job_continue_task_kind_skips_deleted_check(self):
        """``kind="report"`` (Task) → NO ``get_job`` call, NO
        deleted_at check (tasks are not soft-deletable). The P-B
        rewrite SKIPS the ``get_job`` follow-up entirely on the
        task branch so the lookup cost stays constant.
        """
        from daemon.manager import AsyncMessageResult

        job_service, manager = self._build_continue_services(instance_id="root-inst-1")
        task_work_id = "task-abcdef12-3456"
        record = _make_work_record(
            task_work_id, kind="report", status="completed",
            instance_id="root-inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        job_service.get_job = AsyncMock(return_value=None)  # would be confusing if called

        instance_meta = MagicMock()
        instance_meta.status = "running"
        manager._instance_repository.get.return_value = instance_meta
        manager.enqueue_message_job.return_value = AsyncMessageResult(
            message_id="msg-2",
            instance_id="root-inst-1",
            status="queued",
            job_id="task-zzzz9999",
        )

        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            manager=manager,
        )
        job_continue = tools[12]

        result = await job_continue.ainvoke({
            "old_job_id": task_work_id,
            "message": "Continue",
        })

        # Resolver called
        job_service.get_work.assert_awaited_once_with(task_work_id)
        # Legacy get_job must NOT have been called for the task branch
        job_service.get_job.assert_not_called()
        # And the enqueue happened normally
        assert result["instance_id"] == "root-inst-1"
        manager.enqueue_message_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_job_continue_get_work_race(self):
        """Reviewer W3 race guard: ``get_work`` returns a job-kind
        record, but the follow-up ``get_job`` returns ``None`` (row
        was deleted between the two calls). The tool must reject as
        "deleted" rather than falling through to ``enqueue_message``
        against a phantom work_id.
        """
        job_service, manager = self._build_continue_services()
        job_work_id = "job-abcdef12-3456"
        record = _make_work_record(
            job_work_id, kind="job", status="completed",
            instance_id="root-inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)
        # Race: get_job returns None (row deleted between calls)
        job_service.get_job = AsyncMock(return_value=None)

        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            manager=manager,
        )
        job_continue = tools[12]

        result = await job_continue.ainvoke({
            "old_job_id": job_work_id,
            "message": "Continue",
        })

        # Reject as deleted — message uses the same "has been deleted" wording
        # so callers can't distinguish the race from a real soft-delete.
        assert result == {"error": f"Job {job_work_id} has been deleted and cannot be continued"}
        # CRITICAL: enqueue must NOT have been called (no phantom work_id flow)
        manager.enqueue_message_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_job_continue_resolver_not_found(self):
        """``get_work`` returns ``None`` (neither task nor job) →
        existing "Job not found" message (no behavior change for
        the truly-missing case).
        """
        job_service, manager = self._build_continue_services()
        job_service.get_work = AsyncMock(return_value=None)

        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            manager=manager,
        )
        job_continue = tools[12]

        result = await job_continue.ainvoke({
            "old_job_id": "missing-work-id",
            "message": "Continue",
        })

        assert result == {"error": "Job missing-work-id not found"}
        # enqueue must NOT have been called
        manager.enqueue_message_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_job_continue_task_kind_not_terminal(self):
        """``kind="report"`` + non-terminal status → rejected. The P-B
        rewrite uses ``work_status.is_terminal`` (canonical) so a
        Task's "running" (canonical "processing") is correctly
        classified as non-terminal.
        """
        job_service, manager = self._build_continue_services()
        task_work_id = "task-abcdef12-3456"
        record = _make_work_record(
            task_work_id, kind="report", status="processing",  # canonical non-terminal
            instance_id="root-inst-1", project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        queue_mgmt_service = AsyncMock()
        dead_letter_service = MagicMock()
        tools = create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            manager=manager,
        )
        job_continue = tools[12]

        result = await job_continue.ainvoke({
            "old_job_id": task_work_id,
            "message": "Continue",
        })

        assert "error" in result
        assert "not in a terminal state" in result["error"]
        assert "processing" in result["error"]
        # No follow-up get_job, no enqueue
        job_service.get_job.assert_not_called()
        manager.enqueue_message_job.assert_not_awaited()
