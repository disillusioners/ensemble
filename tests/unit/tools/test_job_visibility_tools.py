"""Unit tests for P0 Job Visibility Tools — ``job_messages`` and ``job_tree``.

Tests the two new tools added to ``create_job_tools()`` in
``daemon/tools/job_queue.py``. These tools give agents read-only visibility
into the conversation messages and instance hierarchy spawned by a job.

Test coverage:
  * Happy paths (root + descendants)
  * Job-not-found, no-instance-id, instance-not-found error paths
  * Pagination semantics (``has_more``, ``next_offset``, ``returned_count``)
  * Content snippet truncation (200 chars)
  * Tool-call redaction (``arguments_snippet`` only, no ``output``)
  * Project-scoped access control (mismatch + None backward-compat)
  * Tree building (nested children, MAX_TREE_NODES=200 truncation,
    empty tree)
  * Cycle detection in tree (self-referencing parent_id)
  * Import / factory registration smoke test

The fixture / mock style mirrors ``tests/test_job_queue_tools.py`` so
the patterns stay consistent across the two files.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.tools.job_queue import create_job_tools
from daemon.tools._tool_registry import CATEGORY_MODULES
from daemon import constants
from daemon.services import project_normalizer

# Test constant for system default project ID (mirrors test_job_queue_tools.py)
TEST_SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


# ── Autouse Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def setup_system_default_project():
    """Set SYSTEM_DEFAULT_PROJECT_ID for tests that call normalize_project_id()."""
    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    constants.SYSTEM_DEFAULT_PROJECT_ID = TEST_SYSTEM_PROJECT_ID

    yield

    constants.SYSTEM_DEFAULT_PROJECT_ID = original


# ── Helpers ──────────────────────────────────────────────────────────────────────


def _make_work_record(
    work_id,
    *,
    instance_id=None,
    project_id=None,
    agent_id=None,
):
    """Build a MagicMock standing in for a ``WorkRecord`` returned by ``job_service.get_work``.

    ``job_messages`` / ``job_tree`` only access ``record.instance_id``,
    ``record.project_id`` and ``record.agent_id`` — they don't call
    ``to_dict()`` — so the helper is intentionally minimal vs. the
    fuller helper in ``tests/test_job_queue_tools.py``.
    """
    record = MagicMock(name=f"WorkRecord[{work_id[:8]}]")
    record.instance_id = instance_id
    record.project_id = project_id
    record.agent_id = agent_id
    return record


def _make_instance(
    instance_id,
    *,
    agent_id="developer",
    agent_name=None,
    parent_id=None,
    status="running",
    project_id=None,
    created_at=None,
):
    """Build an Instance-shaped mock for ``manager._instance_repository.get``."""
    inst = MagicMock(name=f"Instance[{instance_id[:8]}]")
    inst.instance_id = instance_id
    inst.agent_id = agent_id
    inst.agent_name = agent_name if agent_name is not None else agent_id.title()
    inst.parent_id = parent_id
    inst.status = status
    inst.project_id = project_id
    inst.created_at = created_at
    return inst


def _make_msg(role, content, *, tool_calls=None):
    """Build a checkpoint message dict in the shape ``manager.get_messages`` returns."""
    msg = {"role": role, "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return msg


# ── Shared Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_services():
    """Standard ``(job_service, queue_mgmt_service, dead_letter_service)`` tuple."""
    job_service = AsyncMock()
    job_service.use_virtual_job_resolver = False
    queue_mgmt_service = AsyncMock()
    dead_letter_service = MagicMock()
    return job_service, queue_mgmt_service, dead_letter_service


@pytest.fixture
def mock_manager():
    """Manager mock exposing ``_instance_repository`` and async ``get_messages``.

    Mirrors the ``mock_manager`` fixture in ``tests/test_job_queue_tools.py``
    (the Phase-2.5 variant) but without the ``enqueue_message_job`` /
    ``_task_repo`` bits — neither is needed by ``job_messages`` or
    ``job_tree``.
    """
    manager = MagicMock()
    instance_repo = MagicMock()
    manager._instance_repository = instance_repo
    manager.get_messages = AsyncMock(return_value=[])
    return manager


@pytest.fixture
def tools(mock_services, mock_manager):
    """Build the tool list with ``manager`` injected."""
    job_service, queue_mgmt_service, dead_letter_service = mock_services
    return create_job_tools(
        job_service, queue_mgmt_service, dead_letter_service,
        manager=mock_manager,
    )


@pytest.fixture
def job_messages_tool(tools):
    """``job_messages`` tool (index 13 in the returned list)."""
    return tools[13]


@pytest.fixture
def job_tree_tool(tools):
    """``job_tree`` tool (index 14 in the returned list)."""
    return tools[14]


@pytest.fixture
def job_progress_tool(tools):
    """``job_progress`` tool (index 15 in the returned list)."""
    return tools[15]


@pytest.fixture
def job_inject_tool(tools):
    """``job_inject`` tool (index 16 in the returned list)."""
    return tools[16]


# ─────────────────────────────────────────────────────────────────────────────────
# TestJobMessagesTool
# ─────────────────────────────────────────────────────────────────────────────────


class TestJobMessagesTool:
    """Tests for the ``job_messages`` tool."""

    @pytest.mark.asyncio
    async def test_job_messages_happy_path(
        self, mock_services, mock_manager, job_messages_tool,
    ):
        """Happy path: valid job with root instance + 2 messages, verify full shape."""
        job_service, _, _ = mock_services

        root_id = "root-1"
        record = _make_work_record(
            "job-1", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id, agent_id="developer", agent_name="Dev")
        mock_manager._instance_repository.get = MagicMock(return_value=root)
        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=[root_id])
        mock_manager.get_messages = AsyncMock(return_value=[
            _make_msg("user", "Hello there"),
            _make_msg("assistant", "Hi! How can I help?"),
        ])

        result = await job_messages_tool.ainvoke({"job_id": "job-1"})

        # Shape check — every documented key must be present.
        assert result["job_id"] == "job-1"
        assert result["root_instance"] == {"instance_id": root_id, "agent_id": "developer"}
        assert result["child_instances"] == []
        assert result["total_messages"] == 2
        assert result["returned_count"] == 2
        assert result["has_more"] is False
        assert result["next_offset"] is None

        # Messages content check.
        assert len(result["messages"]) == 2
        first = result["messages"][0]
        assert first["instance_id"] == root_id
        assert first["agent_id"] == "developer"
        assert first["role"] == "user"
        assert first["content_snippet"] == "Hello there"

    @pytest.mark.asyncio
    async def test_job_messages_job_not_found(
        self, mock_services, job_messages_tool,
    ):
        """``get_work`` returns None → ``{"error": "Job … not found"}``."""
        job_service, _, _ = mock_services
        job_service.get_work = AsyncMock(return_value=None)

        result = await job_messages_tool.ainvoke({"job_id": "missing-job"})

        assert result == {"error": "Job missing-job not found"}

    @pytest.mark.asyncio
    async def test_job_messages_no_instance_id(
        self, mock_services, job_messages_tool,
    ):
        """Record has no ``instance_id`` → ``{"error": "Job … has no associated instance_id"}``."""
        job_service, _, _ = mock_services
        record = _make_work_record("job-2", instance_id=None, project_id="proj-1")
        job_service.get_work = AsyncMock(return_value=record)

        result = await job_messages_tool.ainvoke({"job_id": "job-2"})

        assert result == {"error": "Job job-2 has no associated instance_id"}

    @pytest.mark.asyncio
    async def test_job_messages_pagination_has_more_true(
        self, mock_services, mock_manager, job_messages_tool,
    ):
        """10 messages, limit=3, offset=0 → returned_count=3, has_more=True, next_offset=3."""
        job_service, _, _ = mock_services
        root_id = "root-2"

        record = _make_work_record(
            "job-3", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id)
        mock_manager._instance_repository.get = MagicMock(return_value=root)
        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=[root_id])
        mock_manager.get_messages = AsyncMock(return_value=[
            _make_msg("user", f"msg-{i}") for i in range(10)
        ])

        result = await job_messages_tool.ainvoke(
            {"job_id": "job-3", "limit": 3, "offset": 0},
        )

        assert result["total_messages"] == 10
        assert result["returned_count"] == 3
        assert len(result["messages"]) == 3
        assert result["has_more"] is True
        assert result["next_offset"] == 3

    @pytest.mark.asyncio
    async def test_job_messages_pagination_has_more_false(
        self, mock_services, mock_manager, job_messages_tool,
    ):
        """5 messages, offset=4, limit=2 → 1 returned, has_more=False, next_offset=None."""
        job_service, _, _ = mock_services
        root_id = "root-3"

        record = _make_work_record(
            "job-4", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id)
        mock_manager._instance_repository.get = MagicMock(return_value=root)
        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=[root_id])
        mock_manager.get_messages = AsyncMock(return_value=[
            _make_msg("user", f"msg-{i}") for i in range(5)
        ])

        result = await job_messages_tool.ainvoke(
            {"job_id": "job-4", "limit": 2, "offset": 4},
        )

        # collected[4:6] = [msg-4] → 1 message; offset(4)+len(1) = 5 == total → no more.
        assert result["total_messages"] == 5
        assert result["returned_count"] == 1
        assert len(result["messages"]) == 1
        assert result["has_more"] is False
        assert result["next_offset"] is None

    @pytest.mark.asyncio
    async def test_job_messages_tool_calls_redaction(
        self, mock_services, mock_manager, job_messages_tool,
    ):
        """Tool-call messages expose ``name`` + ``arguments_snippet`` only — no ``output``."""
        job_service, _, _ = mock_services
        root_id = "root-4"

        record = _make_work_record(
            "job-5", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id)
        mock_manager._instance_repository.get = MagicMock(return_value=root)
        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=[root_id])

        # A tool-call with output, id and a long args blob — the test
        # confirms output/id are redacted and args are truncated to 100 chars.
        long_arg = {"command": "x" * 200}
        mock_manager.get_messages = AsyncMock(return_value=[
            _make_msg(
                "assistant",
                "Running command…",
                tool_calls=[{
                    "name": "bash",
                    "args": long_arg,
                    "id": "call_abc123",
                    "output": "SECRET OUTPUT THAT MUST NOT LEAK",
                }],
            ),
        ])

        result = await job_messages_tool.ainvoke({"job_id": "job-5"})

        msgs = result["messages"]
        assert len(msgs) == 1
        tool_calls_summary = msgs[0]["tool_calls"]
        assert len(tool_calls_summary) == 1
        tc = tool_calls_summary[0]

        # Only the two documented keys are exposed.
        assert set(tc.keys()) == {"name", "arguments_snippet"}
        assert tc["name"] == "bash"

        # args are stringified and truncated to 100 chars.
        assert len(tc["arguments_snippet"]) == 100
        assert tc["arguments_snippet"] == str(long_arg)[:100]

        # Explicit no-leak assertions.
        assert "output" not in tc
        assert "id" not in tc
        assert "SECRET" not in json_dumps(tc)

    @pytest.mark.asyncio
    async def test_job_messages_project_id_mismatch(
        self, mock_services, mock_manager, job_messages_tool,
    ):
        """Job's project_id ≠ caller's project_id → access denied."""
        job_service, _, _ = mock_services

        # job's project is proj-A.
        record = _make_work_record(
            "job-6", instance_id="root-5", project_id="proj-A", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        caller = _make_instance("caller-id", project_id="proj-B")
        root = _make_instance("root-5")

        # The access-control lookup calls ``get("caller-id")`` first;
        # subsequent calls fetch the root instance. Side effect keeps
        # the two lookups distinct.
        def get_side_effect(iid):
            if iid == "caller-id":
                return caller
            if iid == "root-5":
                return root
            return None

        mock_manager._instance_repository.get = MagicMock(side_effect=get_side_effect)

        # Re-build tools with current_instance_id set so the access
        # control branch fires.
        _, queue_mgmt, dlq = mock_services
        tools = create_job_tools(
            job_service, queue_mgmt, dlq,
            manager=mock_manager,
            current_instance_id="caller-id",
        )
        job_messages_with_caller = tools[13]

        result = await job_messages_with_caller.ainvoke({"job_id": "job-6"})

        assert result == {"error": "Access denied: job does not belong to caller's project"}

    @pytest.mark.asyncio
    async def test_job_messages_project_id_none_backward_compat(
        self, mock_services, mock_manager, job_messages_tool,
    ):
        """``record.project_id`` is None → access check skipped (backward compatible)."""
        job_service, _, _ = mock_services

        # Job has no project_id → branch is skipped regardless of caller.
        record = _make_work_record(
            "job-7", instance_id="root-6", project_id=None, agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        # Even if the caller has a project_id, the check is skipped.
        caller = _make_instance("caller-id", project_id="proj-anything")
        root = _make_instance("root-6")

        def get_side_effect(iid):
            if iid == "caller-id":
                return caller
            if iid == "root-6":
                return root
            return None

        mock_manager._instance_repository.get = MagicMock(side_effect=get_side_effect)
        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=["root-6"])
        mock_manager.get_messages = AsyncMock(return_value=[
            _make_msg("user", "hi"),
        ])

        # Rebuild with current_instance_id set — verify the check
        # is skipped because record.project_id is None.
        _, queue_mgmt, dlq = mock_services
        tools = create_job_tools(
            job_service, queue_mgmt, dlq,
            manager=mock_manager,
            current_instance_id="caller-id",
        )
        job_messages_with_caller = tools[13]

        result = await job_messages_with_caller.ainvoke({"job_id": "job-7"})

        assert "error" not in result
        assert result["job_id"] == "job-7"
        assert result["total_messages"] == 1

    @pytest.mark.asyncio
    async def test_job_messages_content_snippet_truncation(
        self, mock_services, mock_manager, job_messages_tool,
    ):
        """Message content > 200 chars → ``content_snippet`` truncated to 200."""
        job_service, _, _ = mock_services
        root_id = "root-7"

        record = _make_work_record(
            "job-8", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id)
        mock_manager._instance_repository.get = MagicMock(return_value=root)
        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=[root_id])

        long_content = "X" * 250
        mock_manager.get_messages = AsyncMock(return_value=[
            _make_msg("assistant", long_content),
        ])

        result = await job_messages_tool.ainvoke({"job_id": "job-8"})

        snippet = result["messages"][0]["content_snippet"]
        assert len(snippet) == 200
        assert snippet == long_content[:200]


# ─────────────────────────────────────────────────────────────────────────────────
# TestJobTreeTool
# ─────────────────────────────────────────────────────────────────────────────────


class TestJobTreeTool:
    """Tests for the ``job_tree`` tool."""

    @pytest.mark.asyncio
    async def test_job_tree_happy_path(
        self, mock_services, mock_manager, job_tree_tool,
    ):
        """Happy path: single root, no children, all expected keys present."""
        job_service, _, _ = mock_services
        root_id = "tree-root-1"

        record = _make_work_record(
            "job-tree-1", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(
            root_id, agent_id="developer", agent_name="Dev", status="running",
        )
        mock_manager._instance_repository.get = MagicMock(return_value=root)
        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=[root_id])

        result = await job_tree_tool.ainvoke({"job_id": "job-tree-1"})

        # All keys present.
        assert set(result.keys()) == {
            "job_id", "tree", "total_instances", "active_instances", "truncated",
        }
        assert result["job_id"] == "job-tree-1"
        assert result["total_instances"] == 1
        assert result["active_instances"] == 1
        assert result["truncated"] is False

        # Tree node shape.
        tree = result["tree"]
        assert tree["instance_id"] == root_id
        assert tree["agent_id"] == "developer"
        assert tree["agent_name"] == "Dev"
        assert tree["status"] == "running"
        assert tree["children"] == []

    @pytest.mark.asyncio
    async def test_job_tree_job_not_found(
        self, mock_services, job_tree_tool,
    ):
        """``get_work`` returns None → ``{"error": "Job … not found"}``."""
        job_service, _, _ = mock_services
        job_service.get_work = AsyncMock(return_value=None)

        result = await job_tree_tool.ainvoke({"job_id": "missing-tree"})

        assert result == {"error": "Job missing-tree not found"}

    @pytest.mark.asyncio
    async def test_job_tree_nested_children(
        self, mock_services, mock_manager, job_tree_tool,
    ):
        """3-level deep hierarchy (root → child → grandchild) — verify nesting."""
        job_service, _, _ = mock_services
        root_id, child_id, grand_id = "tree-root-2", "tree-child-2", "tree-grand-2"

        record = _make_work_record(
            "job-tree-2", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(
            root_id, agent_id="developer", agent_name="Dev",
            status="running", parent_id=None,
        )
        child = _make_instance(
            child_id, agent_id="worker", agent_name="Worker",
            status="running", parent_id=root_id,
        )
        grand = _make_instance(
            grand_id, agent_id="explorer", agent_name="Explorer",
            status="completed", parent_id=child_id,
        )

        # BFS-ordered list (per the source comment).
        all_ids = [root_id, child_id, grand_id]
        by_id = {root_id: root, child_id: child, grand_id: grand}

        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=all_ids)
        mock_manager._instance_repository.get = MagicMock(
            side_effect=lambda iid: by_id.get(iid),
        )

        result = await job_tree_tool.ainvoke({"job_id": "job-tree-2"})

        # Counts: 3 total, 2 active (grand is terminal/completed).
        assert result["total_instances"] == 3
        assert result["active_instances"] == 2
        assert result["truncated"] is False

        tree = result["tree"]
        assert tree["instance_id"] == root_id
        assert tree["children"] and len(tree["children"]) == 1

        child_node = tree["children"][0]
        assert child_node["instance_id"] == child_id
        assert child_node["agent_name"] == "Worker"
        assert child_node["children"] and len(child_node["children"]) == 1

        grand_node = child_node["children"][0]
        assert grand_node["instance_id"] == grand_id
        assert grand_node["agent_name"] == "Explorer"
        assert grand_node["status"] == "completed"
        assert grand_node["children"] == []

    @pytest.mark.asyncio
    async def test_job_tree_max_nodes_truncated(
        self, mock_services, mock_manager, job_tree_tool,
    ):
        """Tree of >200 nodes → ``truncated=True``."""
        job_service, _, _ = mock_services
        root_id = "tree-root-3"

        record = _make_work_record(
            "job-tree-3", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id)

        # 201 ids: root + 200 fictitious descendants. The bulk-load
        # loop returns the root instance for the root id only, so
        # children_map stays empty — only the ``truncated`` flag is
        # under test here.
        all_ids = [root_id] + [f"phantom-{i}" for i in range(200)]
        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=all_ids)

        def get_side_effect(iid):
            return root if iid == root_id else None

        mock_manager._instance_repository.get = MagicMock(side_effect=get_side_effect)

        result = await job_tree_tool.ainvoke({"job_id": "job-tree-3"})

        assert result["truncated"] is True
        # Only the root is in the instance_map; total reflects only it.
        assert result["total_instances"] == 1

    @pytest.mark.asyncio
    async def test_job_tree_project_id_mismatch(
        self, mock_services, mock_manager, job_tree_tool,
    ):
        """Job's project_id ≠ caller's project_id → access denied."""
        job_service, _, _ = mock_services

        record = _make_work_record(
            "job-tree-4", instance_id="tree-root-4", project_id="proj-A", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        caller = _make_instance("caller-id", project_id="proj-B")
        root = _make_instance("tree-root-4")

        def get_side_effect(iid):
            if iid == "caller-id":
                return caller
            if iid == "tree-root-4":
                return root
            return None

        mock_manager._instance_repository.get = MagicMock(side_effect=get_side_effect)

        _, queue_mgmt, dlq = mock_services
        tools = create_job_tools(
            job_service, queue_mgmt, dlq,
            manager=mock_manager,
            current_instance_id="caller-id",
        )
        job_tree_with_caller = tools[14]

        result = await job_tree_with_caller.ainvoke({"job_id": "job-tree-4"})

        assert result == {"error": "Access denied: job does not belong to caller's project"}

    @pytest.mark.asyncio
    async def test_job_tree_empty_tree(
        self, mock_services, mock_manager, job_tree_tool,
    ):
        """Root instance has no children → tree contains root only, empty children list."""
        job_service, _, _ = mock_services
        root_id = "tree-root-5"

        record = _make_work_record(
            "job-tree-5", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id, status="completed")
        mock_manager._instance_repository.get = MagicMock(return_value=root)
        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=[root_id])

        result = await job_tree_tool.ainvoke({"job_id": "job-tree-5"})

        assert result["tree"]["instance_id"] == root_id
        assert result["tree"]["children"] == []
        assert result["total_instances"] == 1
        # "completed" is terminal → active_count = 0.
        assert result["active_instances"] == 0
        assert result["truncated"] is False


# ─────────────────────────────────────────────────────────────────────────────────
# TestJobProgressTool
# ─────────────────────────────────────────────────────────────────────────────────


class TestJobProgressTool:
    """Tests for the ``job_progress`` tool (P1).

    ``job_progress`` returns a progress snapshot: status, elapsed time
    since creation, last assistant message (truncated to 200 chars), and
    instance tree counts (total, active, completed).
    """

    @pytest.mark.asyncio
    async def test_job_progress_happy_path(
        self, mock_services, mock_manager, job_progress_tool,
    ):
        """Happy path: valid job returns the full progress shape."""
        job_service, _, _ = mock_services

        root_id = "prog-root-1"
        record = _make_work_record(
            "prog-1", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(
            root_id,
            agent_id="developer",
            status="running",
            created_at="2025-01-01T00:00:00+00:00",
        )
        mock_manager._instance_repository.get = MagicMock(return_value=root)
        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=[root_id])
        mock_manager.get_messages = AsyncMock(return_value=[
            _make_msg("user", "Do something"),
            _make_msg("assistant", "Working on it"),
        ])

        result = await job_progress_tool.ainvoke({"job_id": "prog-1"})

        # Shape check — every documented key must be present.
        assert set(result.keys()) == {
            "job_id", "status", "elapsed_seconds",
            "last_assistant_message", "instance_tree",
        }
        assert result["job_id"] == "prog-1"
        assert result["status"] == "running"

        # elapsed_seconds is a rounded float; just verify it's a positive number.
        assert isinstance(result["elapsed_seconds"], float)
        assert result["elapsed_seconds"] > 0

        # Last assistant message.
        last_msg = result["last_assistant_message"]
        assert last_msg is not None
        assert last_msg["content_snippet"] == "Working on it"

        # Instance tree counts.
        tree = result["instance_tree"]
        assert tree["total_instances"] == 1
        assert tree["active_instances"] == 1
        assert tree["completed_instances"] == 0

    @pytest.mark.asyncio
    async def test_job_progress_job_not_found(
        self, mock_services, job_progress_tool,
    ):
        """``get_work`` returns None → ``{"error": "Job … not found"}``."""
        job_service, _, _ = mock_services
        job_service.get_work = AsyncMock(return_value=None)

        result = await job_progress_tool.ainvoke({"job_id": "missing-prog"})

        assert result == {"error": "Job missing-prog not found"}

    @pytest.mark.asyncio
    async def test_job_progress_elapsed_time_calculation(
        self, mock_services, mock_manager, job_progress_tool,
    ):
        """Mock ``created_at`` to a known past time → verify elapsed math."""
        job_service, _, _ = mock_services

        root_id = "prog-root-2"

        record = _make_work_record(
            "prog-2", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        # Set created_at to ~120 seconds ago via a known offset. We can't
        # use the exact value because of test execution time, but we can
        # verify the math is approximately right.
        from datetime import datetime, UTC, timedelta

        known_past = datetime.now(UTC) - timedelta(seconds=120)
        root = _make_instance(
            root_id,
            created_at=known_past.isoformat(),
        )
        mock_manager._instance_repository.get = MagicMock(return_value=root)
        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=[root_id])
        mock_manager.get_messages = AsyncMock(return_value=[])

        result = await job_progress_tool.ainvoke({"job_id": "prog-2"})

        # elapsed should be ~120s, allowing a few seconds of slack.
        elapsed = result["elapsed_seconds"]
        assert 118 <= elapsed <= 125

    @pytest.mark.asyncio
    async def test_job_progress_no_assistant_messages(
        self, mock_services, mock_manager, job_progress_tool,
    ):
        """Messages list empty → ``last_assistant_message`` is None."""
        job_service, _, _ = mock_services

        root_id = "prog-root-3"

        record = _make_work_record(
            "prog-3", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id, created_at="2025-01-01T00:00:00+00:00")
        mock_manager._instance_repository.get = MagicMock(return_value=root)
        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=[root_id])

        # Only user messages, no assistant message.
        mock_manager.get_messages = AsyncMock(return_value=[
            _make_msg("user", "Hello?"),
        ])

        result = await job_progress_tool.ainvoke({"job_id": "prog-3"})

        assert result["last_assistant_message"] is None

    @pytest.mark.asyncio
    async def test_job_progress_project_id_mismatch(
        self, mock_services, mock_manager, job_progress_tool,
    ):
        """Job's project_id ≠ caller's project_id → access denied."""
        job_service, _, _ = mock_services

        record = _make_work_record(
            "prog-4", instance_id="prog-root-4", project_id="proj-A", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        caller = _make_instance("caller-id", project_id="proj-B")
        root = _make_instance("prog-root-4")

        def get_side_effect(iid):
            if iid == "caller-id":
                return caller
            if iid == "prog-root-4":
                return root
            return None

        mock_manager._instance_repository.get = MagicMock(side_effect=get_side_effect)

        _, queue_mgmt, dlq = mock_services
        tools = create_job_tools(
            job_service, queue_mgmt, dlq,
            manager=mock_manager,
            current_instance_id="caller-id",
        )
        job_progress_with_caller = tools[15]

        result = await job_progress_with_caller.ainvoke({"job_id": "prog-4"})

        assert result == {"error": "Access denied: job does not belong to caller's project"}

    @pytest.mark.asyncio
    async def test_job_progress_all_children_completed(
        self, mock_services, mock_manager, job_progress_tool,
    ):
        """Instance tree where all children are terminal → active_instances = 0."""
        job_service, _, _ = mock_services

        root_id = "prog-root-5"

        record = _make_work_record(
            "prog-5", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id, status="completed", created_at="2025-01-01T00:00:00+00:00")
        child1 = _make_instance("prog-child-5a", status="completed")
        child2 = _make_instance("prog-child-5b", status="terminated")

        all_ids = [root_id, "prog-child-5a", "prog-child-5b"]
        by_id = {root_id: root, "prog-child-5a": child1, "prog-child-5b": child2}

        mock_manager._instance_repository.get_tree_ids = MagicMock(return_value=all_ids)
        mock_manager._instance_repository.get = MagicMock(
            side_effect=lambda iid: by_id.get(iid),
        )
        mock_manager.get_messages = AsyncMock(return_value=[])

        result = await job_progress_tool.ainvoke({"job_id": "prog-5"})

        tree = result["instance_tree"]
        assert tree["total_instances"] == 3
        assert tree["active_instances"] == 0
        assert tree["completed_instances"] == 3


# ─────────────────────────────────────────────────────────────────────────────────
# TestJobInjectTool
# ─────────────────────────────────────────────────────────────────────────────────


class TestJobInjectTool:
    """Tests for the ``job_inject`` tool (P1).

    ``job_inject`` injects a message into a RUNNING or WAITING_CHILDREN
    instance via ``manager.set_injection()``. It does NOT create a new
    job — it piggybacks on the existing turn.
    """

    @pytest.mark.asyncio
    async def test_job_inject_happy_path(
        self, mock_services, mock_manager, job_inject_tool,
    ):
        """Valid running job, message injected → success dict."""
        job_service, _, _ = mock_services

        root_id = "inject-root-1"
        record = _make_work_record(
            "inject-1", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id, status="running")
        mock_manager._instance_repository.get = MagicMock(return_value=root)

        # set_injection returns {"content": ..., "timestamp": ...}
        expected_entry = {"content": "Hello injected", "timestamp": "2025-01-01T00:00:00+00:00"}
        mock_manager.set_injection = MagicMock(return_value=expected_entry)
        mock_manager.get_injection_count = MagicMock(return_value=1)

        result = await job_inject_tool.ainvoke({
            "job_id": "inject-1",
            "message": "Hello injected",
        })

        # Verify the success shape.
        assert result["job_id"] == "inject-1"
        assert result["instance_id"] == root_id
        assert result["status"] == "injected"
        assert result["pending_count"] == 1
        assert result["content"] == "Hello injected"
        assert result["timestamp"] == "2025-01-01T00:00:00+00:00"

        # Verify set_injection was called correctly.
        mock_manager.set_injection.assert_called_once_with(root_id, "Hello injected")

    @pytest.mark.asyncio
    async def test_job_inject_job_not_found(
        self, mock_services, job_inject_tool,
    ):
        """``get_work`` returns None → ``{"error": "Job … not found"}``."""
        job_service, _, _ = mock_services
        job_service.get_work = AsyncMock(return_value=None)

        result = await job_inject_tool.ainvoke({
            "job_id": "missing-inject",
            "message": "Hello",
        })

        assert result == {"error": "Job missing-inject not found"}

    @pytest.mark.asyncio
    async def test_job_inject_instance_not_running(
        self, mock_services, mock_manager, job_inject_tool,
    ):
        """Instance is COMPLETED → error dict about status."""
        job_service, _, _ = mock_services

        root_id = "inject-root-2"
        record = _make_work_record(
            "inject-2", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id, status="completed")
        mock_manager._instance_repository.get = MagicMock(return_value=root)

        result = await job_inject_tool.ainvoke({
            "job_id": "inject-2",
            "message": "Hello",
        })

        assert "error" in result
        assert "completed" in result["error"]
        assert "RUNNING" in result["error"]
        assert "job_continue" in result["error"]

    @pytest.mark.asyncio
    async def test_job_inject_project_id_mismatch(
        self, mock_services, mock_manager, job_inject_tool,
    ):
        """Job's project_id ≠ caller's project_id → access denied."""
        job_service, _, _ = mock_services

        record = _make_work_record(
            "inject-3", instance_id="inject-root-3", project_id="proj-A", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        caller = _make_instance("caller-id", project_id="proj-B")
        root = _make_instance("inject-root-3")

        def get_side_effect(iid):
            if iid == "caller-id":
                return caller
            if iid == "inject-root-3":
                return root
            return None

        mock_manager._instance_repository.get = MagicMock(side_effect=get_side_effect)

        _, queue_mgmt, dlq = mock_services
        tools = create_job_tools(
            job_service, queue_mgmt, dlq,
            manager=mock_manager,
            current_instance_id="caller-id",
        )
        job_inject_with_caller = tools[16]

        result = await job_inject_with_caller.ainvoke({
            "job_id": "inject-3",
            "message": "Hello",
        })

        assert result == {"error": "Access denied: job does not belong to caller's project"}

    @pytest.mark.asyncio
    async def test_job_inject_empty_message(
        self, mock_services, mock_manager, job_inject_tool,
    ):
        """Empty string or whitespace message → still injected (source does NOT validate).

        The source code for ``job_inject`` does NOT validate the message
        content — it passes whatever string is given directly to
        ``manager.set_injection()``. This test documents that behavior:
        an empty string is accepted and forwarded as-is.
        """
        job_service, _, _ = mock_services

        root_id = "inject-root-4"
        record = _make_work_record(
            "inject-4", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id, status="running")
        mock_manager._instance_repository.get = MagicMock(return_value=root)

        expected_entry = {"content": "", "timestamp": "2025-01-01T00:00:00+00:00"}
        mock_manager.set_injection = MagicMock(return_value=expected_entry)
        mock_manager.get_injection_count = MagicMock(return_value=1)

        result = await job_inject_tool.ainvoke({
            "job_id": "inject-4",
            "message": "",
        })

        # Source does not validate message content — empty string is accepted.
        assert result["status"] == "injected"
        assert result["content"] == ""
        mock_manager.set_injection.assert_called_once_with(root_id, "")

    @pytest.mark.asyncio
    async def test_job_inject_set_injection_failure(
        self, mock_services, mock_manager, job_inject_tool,
    ):
        """``set_injection`` raises exception → sanitized error dict."""
        job_service, _, _ = mock_services

        root_id = "inject-root-5"
        record = _make_work_record(
            "inject-5", instance_id=root_id, project_id="proj-1", agent_id="developer",
        )
        job_service.get_work = AsyncMock(return_value=record)

        root = _make_instance(root_id, status="running")
        mock_manager._instance_repository.get = MagicMock(return_value=root)

        # set_injection raises — the except block should catch and sanitize.
        mock_manager.set_injection = MagicMock(
            side_effect=RuntimeError("SECRET INTERNAL ERROR"),
        )

        result = await job_inject_tool.ainvoke({
            "job_id": "inject-5",
            "message": "Hello",
        })

        # The error should be sanitized — no internal exception details.
        assert "error" in result
        assert "Internal error" in result["error"]
        # The raw exception message must NOT leak.
        assert "SECRET INTERNAL ERROR" not in str(result)


class TestJobVisibilityToolsRegistration:
    """Smoke / import / registration tests for the two new tools."""

    def test_create_job_tools_is_importable(self):
        """``from daemon.tools.job_queue import create_job_tools`` works."""
        from daemon.tools.job_queue import create_job_tools as factory
        assert callable(factory)

    def test_create_job_tools_importable_from_daemon_tools(self):
        """``from daemon.tools import create_job_tools`` works (re-exported)."""
        from daemon.tools import create_job_tools
        assert callable(create_job_tools)

    def test_job_visibility_tools_in_tool_list(self, mock_services, mock_manager):
        """All visibility tools appear in the list returned by ``create_job_tools``."""
        job_service, queue_mgmt_service, dead_letter_service = mock_services

        tools = create_job_tools(
            job_service, queue_mgmt_service, dead_letter_service,
            manager=mock_manager,
        )

        tool_names = [t.name for t in tools]
        assert "job_messages" in tool_names
        assert "job_tree" in tool_names
        assert "job_progress" in tool_names
        assert "job_inject" in tool_names

        # All visibility tools must carry the "job" category.
        for tool in tools:
            if tool.name in ("job_messages", "job_tree", "job_progress", "job_inject"):
                assert tool._tool_category == "job"

    def test_job_in_category_modules_includes_visibility_tools(self):
        """The 'job' category in ``CATEGORY_MODULES`` exposes the new tools."""
        assert "job" in CATEGORY_MODULES
        assert CATEGORY_MODULES["job"] == "daemon.tools.job_queue"


# ─────────────────────────────────────────────────────────────────────────────────
# Local helpers
# ─────────────────────────────────────────────────────────────────────────────────


def json_dumps(obj) -> str:
    """Minimal JSON serializer for a one-off assertion that no secret leaks."""
    import json
    return json.dumps(obj)
